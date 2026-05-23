from __future__ import annotations

import logging
import json
import hashlib
import math
import sqlite3
import tempfile
import time
import threading
from pathlib import Path
from typing import Any, Literal

import numpy as np
import rasterio
from pydantic import BaseModel, ConfigDict, Field
from rasterio.transform import rowcol, xy

from backend.api.errors import ApiError
from backend.core.config import load_app_config, resolve_config_path
from backend.contracts.decorators import contract
from backend.contracts.models import JobMode, ToolConfirmationMode, ToolVisibility
from backend.jobs.map_algebra import (
    MapAlgebraError,
    align_inputs_to_target,
    compute_ast_hash,
    evaluate_expression,
    evaluate_expression_for_variables,
    expression_digest,
    finalize_output_array,
    load_target_grid_from_dem,
    parse_validate_expression,
    write_output_raster,
)
from backend.jobs import raster_transform
from backend.jobs.executors.horizons import execute_generate_horizons
from backend.jobs.executors.notebook import execute_run_notebook_definition
from backend.jobs.executors.rag import execute_assistant_rag_ingest
from backend.jobs.runtime_context import (
    publish_generated_raster_layer,
    emit_job_progress,
    execute_notebook_job,
    is_job_cancel_requested,
    RegisteredRasterOutput,
    register_generated_raster,
    resolve_scenario_paths,
)
from backend.services.assistant.rag_index import create_default_rag_index
from backend.services.artifact_catalog import register_artifact_output
from backend.services.cog import convert_geotiff_to_cog
from backend.services.colormap_support import (
    contour_rgba,
    resolve_colormap_registry,
    resolve_default_colormap_for_name,
    sample_colormap_rgba,
    tone_map_rgb,
)
from backend.worker.gdal_runtime import configure_gdal_runtime
from backend.worker.lightmap_streaming import LightmapStreamRequestV2Py
from backend.worker.lightmap_streaming import LightmapStreamingClient
from backend.worker.lightmap_streaming import TemporalSignalSpecPy
from backend.worker.lightmap_streaming import stream_tiles_v2
from backend.worker.native_bootstrap import import_moonlib

logger = logging.getLogger(__name__)

TEMPORAL_SIGNAL_ALIASES: dict[str, str] = {
    "lighting_raster": "sun_fraction_u8",
    "earth_above_horizon": "earth_center_margin_deg_f32",
    "sun_above_horizon": "sun_center_margin_deg_f32",
}

RASTER_TRANSFORM_TEMPORAL_SOURCE_ALIASES: dict[str, str] = {
    "sun_fraction": "sun_fraction_u8",
    "sun_over_horizon_deg": "sun_center_margin_deg_f32",
    "earth_over_horizon_deg": "earth_center_margin_deg_f32",
}


def _raise_if_cancelled() -> None:
    if not is_job_cancel_requested():
        return
    raise MapAlgebraError(
        code="map_algebra_canceled",
        message="Raster calculation canceled.",
        status_code=409,
    )


def _raise_if_transform_cancelled() -> None:
    if not is_job_cancel_requested():
        return
    raise raster_transform.RasterTransformError(
        code="raster_transform_canceled",
        message="Raster transform canceled.",
        status_code=409,
    )


def _lightmap_status_payload(status: Any | None) -> dict[str, Any]:
    if status is None:
        return {}
    return {
        "native_job_id": getattr(status, "job_id", None),
        "native_state": getattr(status, "state", None),
        "native_progress01": getattr(status, "progress01", None),
        "tiles_produced": getattr(status, "tiles_produced", None),
        "tiles_consumed": getattr(status, "tiles_consumed", None),
        "ready_queue_depth": getattr(status, "ready_queue_depth", None),
        "free_buffer_count": getattr(status, "free_buffer_count", None),
        "native_message": getattr(status, "message", None),
    }


def _emit_lightmap_tile_progress(
    *,
    stage: str,
    message: str,
    tiles_written: int,
    total_tiles: int | None,
    tile_meta: Any | None = None,
    status: Any | None = None,
) -> None:
    payload: dict[str, Any] = {
        "event_kind": "native_status",
        "stage": stage,
        "message": message,
        "processed": int(tiles_written),
    }
    if total_tiles is not None and total_tiles > 0:
        payload["total"] = int(total_tiles)
        payload["percent"] = round(
            min(100.0, max(0.0, (float(tiles_written) / float(total_tiles)) * 100.0)),
            1,
        )
    if tile_meta is not None:
        payload.update(
            {
                "tile_id": getattr(tile_meta, "tile_id", None),
                "patch_row": getattr(tile_meta, "patch_row", None),
                "patch_col": getattr(tile_meta, "patch_col", None),
                "tile_width": getattr(tile_meta, "width", None),
                "tile_height": getattr(tile_meta, "height", None),
            }
        )
    payload.update(
        {key: value for key, value in _lightmap_status_payload(status).items() if value is not None}
    )
    emit_job_progress(payload)


def _to_dotnet_string_list_or_python_list(values: list[str]) -> Any:
    try:
        from System import String  # type: ignore
        from System.Collections.Generic import List as DotNetList  # type: ignore

        dotnet_values = DotNetList[String]()
        for value in values:
            dotnet_values.Add(String(str(value)))
        return dotnet_values
    except ModuleNotFoundError:
        return [str(value) for value in values]


class GenerateHorizonsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    scenario_root_dir: str
    dem_path: str
    horizons_dir: str
    overwrite_horizons: bool
    compress_horizons: bool
    artifact_db_path: str | None = None


class PingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    message: str


class GenerateHillshadeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    dem_path: str
    hillshade_path: str
    size_bytes: int
    artifact_db_path: str | None = None


class AssistantRagIngestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    relative_root: str
    db_path: str
    scanned: int
    added: int
    updated: int
    skipped: int
    deleted: int


class GenerateHorizonProfileResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    dem_path: str
    observer_x: float
    observer_y: float
    observer_height_m: float
    azimuth_step_deg: float
    output_format: str
    artifact_db_path: str | None = None


class GenerateLightmapTimeseriesResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    dem_path: str
    horizons_dir: str
    output_dir: str
    time_start_utc: str
    time_stop_utc: str
    step_seconds: int
    artifact_db_path: str | None = None


class GenerateLosViewshedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    dem_path: str
    observer_count: int
    observer_input_mode: Literal["single", "list", "mask"]
    backend_mode_requested: Literal["gdal", "cuda", "auto"]
    backend_mode_selected: Literal["gdal", "cuda"]
    backend_fallback_applied: bool = False
    backend_fallback_reason: str | None = None
    merge_mode: Literal["any_visible", "visibility_count"]
    target_height_m: float
    max_range_m: float
    output_path: str
    output_relative_path: str
    file_id: str
    product_id: str
    output_dtype: str
    output_nodata: float | None = None
    route_metrics: dict[str, Any] = Field(default_factory=dict)
    high_fidelity_mode: bool = False
    parabolic_error_m: float | None = None
    parameter_hash: str | None = None
    progress_events: list[dict[str, Any]] = Field(default_factory=list)
    artifact_db_path: str | None = None


class GenerateLightmapReductionRasterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    scenario_root_dir: str
    dem_path: str
    horizons_dir: str
    output_path: str
    time_start_utc: str
    time_stop_utc: str
    time_step_hours: float
    reducer_kind: str
    tiles_written: int
    value_min: float | None = None
    value_max: float | None = None
    artifact_db_path: str | None = None


class GeneratePermanentShadowRasterResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    scenario_root_dir: str
    dem_path: str
    horizons_dir: str
    output_path: str
    size_bytes: int
    artifact_db_path: str | None = None


class GenerateDemDerivativeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    dem_path: str
    output_path: str
    derivative_kind: str
    artifact_db_path: str | None = None


class NotebookOutputRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str
    kind: str
    subkind: str
    render_mode: str | None = None
    product_id: str | None = None
    file_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NotebookJobResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    notebook_job_id: str
    notebook_path: str
    notebook_hash: str
    outputs: list[NotebookOutputRecord]
    result: dict[str, Any]
    progress_events: list[dict[str, Any]]


class RasterInputReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str | None = None
    product_id: str | None = None
    signal: str | None = None
    kind: str | None = None
    temporal_source: str | None = None
    times: str | None = None
    station_name: str | None = None
    start_utc: str | None = None
    stop_utc: str | None = None
    step_hours: float | None = None


class RasterCalculateInputReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str | None = None
    product_id: str | None = None
    signal: Literal["lighting_raster", "earth_above_horizon", "sun_above_horizon"] | None = None


class RasterCalculatePublishLayerOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    title: str | None = None
    visible: bool = True
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    z_index: int | None = None
    style: dict[str, Any] = Field(default_factory=dict)
    on_existing: Literal["update", "error", "new"] = "update"
    transparent_background: bool = False


class RasterTransformInputReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str | None = None
    product_id: str | None = None
    kind: Literal["times"] | None = None
    temporal_source: Literal[
        "sun_fraction",
        "sun_over_horizon_deg",
        "earth_over_horizon_deg",
        "station_over_horizon_deg",
    ] | None = None
    times: str | None = None
    station_name: str | None = None
    start_utc: str | None = None
    stop_utc: str | None = None
    step_hours: float | None = None
    signal: Literal["lighting_raster", "earth_above_horizon", "sun_above_horizon"] | None = None


class RasterCalculateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    expression: str
    inputs: dict[str, RasterCalculateInputReference] = Field(min_length=1)
    output_relative_path: str | None = None
    overwrite_mode: Literal["ask", "never", "always"] = "ask"
    resampling: Literal["nearest", "bilinear", "cubic"] = "bilinear"
    time_start_utc: str | None = None
    time_stop_utc: str | None = None
    time_step_hours: float | None = None
    horizons_relative_dir: str | None = None
    observer_elevation_meters: float = 0.0
    patch_width: int = Field(default=128, ge=1)
    patch_height: int = Field(default=128, ge=1)
    chunk_time_count: int = Field(default=256, ge=1)
    buffer_count: int = Field(default=6, ge=1)
    poll_timeout_ms: int = Field(default=250, ge=1)
    use_spice_sun_vectors: bool = True
    use_spice_earth_vectors: bool = True
    publish_layer: RasterCalculatePublishLayerOptions | None = None
    mode: JobMode = JobMode.QUEUED


class RasterTransformRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    script: str
    inputs: dict[str, RasterTransformInputReference] = Field(min_length=1)
    output_relative_path: str | None = None
    overwrite_mode: Literal["ask", "never", "always"] = "ask"
    overwrite: bool | None = None
    mode: JobMode = JobMode.QUEUED
    resampling: Literal["nearest", "bilinear", "cubic"] = "bilinear"
    spatial_partitioning: Literal["auto", "allowed", "forbidden"] = "auto"
    time_partitioning: Literal["auto", "allowed", "forbidden"] = "auto"
    spatial_halo_pixels: int = Field(default=0, ge=0)
    time_start_utc: str | None = None
    time_stop_utc: str | None = None
    time_step_hours: float | None = None
    horizons_relative_dir: str | None = None
    observer_elevation_meters: float = 0.0
    patch_width: int = Field(default=128, ge=1)
    patch_height: int = Field(default=128, ge=1)
    chunk_time_count: int = Field(default=256, ge=1)
    buffer_count: int = Field(default=6, ge=1)
    poll_timeout_ms: int = Field(default=250, ge=1)
    use_spice_sun_vectors: bool = True
    use_spice_earth_vectors: bool = True


class RasterCalculateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    output_relative_path: str
    output_path: str
    product_id: str
    file_id: str
    output_dtype: str
    output_nodata: float | None = None
    target_crs: str
    target_width: int
    target_height: int
    expression: str
    expression_ast_hash: str
    used_variables: list[str]
    used_functions: list[str]
    used_operators: list[str]
    reprojected_inputs: list[str]
    temporal_inputs: list[str] = Field(default_factory=list)
    time_start_utc: str | None = None
    time_stop_utc: str | None = None
    time_step_hours: float | None = None
    published_layer_id: str | None = None
    published_layer_title: str | None = None
    published_layer_visible: bool | None = None
    artifact_db_path: str | None = None
    progress_events: list[dict[str, Any]] = Field(default_factory=list)


class RasterTransformResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    output_relative_path: str
    output_path: str
    product_id: str
    file_id: str
    output_dtype: str
    output_nodata: float | None = None
    target_crs: str
    target_width: int
    target_height: int
    script: str
    script_hash: str
    used_variables: list[str]
    used_functions: list[str]
    used_operators: list[str]
    reprojected_inputs: list[str]
    temporal_inputs: list[str] = Field(default_factory=list)
    time_start_utc: str | None = None
    time_stop_utc: str | None = None
    time_step_hours: float | None = None
    planner_summary: dict[str, Any] = Field(default_factory=dict)
    artifact_db_path: str | None = None
    progress_events: list[dict[str, Any]] = Field(default_factory=list)


class ExportColormapRgbaGeoTiffResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    source_relative_path: str
    output_relative_path: str
    output_path: str
    product_id: str
    file_id: str
    colormap_id: str | None = None
    style_mode: str = "colormap"
    output_dtype: str = "uint8"
    output_band_count: int = 4
    artifact_db_path: str | None = None


class ViewshedObserverPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    observer_height_m: float | None = None


class ViewshedObserverMaskReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str | None = None
    product_id: str | None = None
    threshold: float = 0.0


class GenerateLosViewshedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    dem_path: str | None = None
    scenario_root_dir: str | None = None
    observer_x: float | None = None
    observer_y: float | None = None
    observer_height_m: float = 2.0
    observer_list: list[ViewshedObserverPoint] | None = None
    observer_mask: ViewshedObserverMaskReference | None = None
    target_height_m: float = 0.0
    max_range_m: float = 0.0
    output_relative_path: str | None = None
    overwrite_mode: Literal["ask", "never", "always"] = "ask"
    merge_mode: Literal["any_visible", "visibility_count"] = "any_visible"
    backend_mode: Literal["gdal", "cuda", "auto"] = "auto"
    force_parabolic: bool = False
    allow_force_parabolic_override: bool = False
    parabolic_error_tolerance_m: float | None = None
    mode: JobMode = JobMode.QUEUED


class AnalyzeObserverMaskConnectivityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    observer_mask: ViewshedObserverMaskReference
    dem_path: str | None = None
    scenario_root_dir: str | None = None
    require_match_dem_grid: bool = True
    cleanup_mode: Literal["none", "erosion", "opening"] = "none"
    cleanup_iterations: int = Field(default=1, ge=0)
    mode: JobMode = JobMode.QUEUED


class AnalyzeObserverMaskConnectivityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    dem_path: str
    mask_path: str
    observer_count: int
    observer_density: float
    component_count: int
    largest_component_size: int
    adjacency_ratio: float
    cleanup_mode: Literal["none", "erosion", "opening"]
    cleanup_iterations: int
    require_match_dem_grid: bool = True
    dem_height: int
    dem_width: int
    progress_events: list[dict[str, Any]] = Field(default_factory=list)


class ToolImplementations:
    """Typed tool implementation signatures define valid job executions."""
    DRAFT_HANDLER_NAMES = frozenset(
        {
            "generate_horizon_profile",
            "generate_lightmap_timeseries",
        }
    )

    @staticmethod
    @contract(
        name="ToolImplementations.generate_horizons",
        response_type=GenerateHorizonsResult,
        description="Compute horizons for a DEM product.",
        tool_visibility=ToolVisibility.PUBLIC,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "terrain", "horizons", "worker-only"),
    )
    def generate_horizons(
        scenario_id: str,
        scenario_root_dir: str,
        dem_path: str,
        horizons_dir: str,
        surrounding_dem_paths: list[str] | None = None,
        observer_elevation_meters: float = 0.0,
        overwrite_horizons: bool = False,
        compress_horizons: bool = True,
        mode: JobMode = JobMode.QUEUED,
    ) -> GenerateHorizonsResult:
        payload = execute_generate_horizons(
            scenario_id=scenario_id,
            scenario_root_dir=scenario_root_dir,
            dem_path=dem_path,
            horizons_dir=horizons_dir,
            surrounding_dem_paths=surrounding_dem_paths,
            observer_elevation_meters=observer_elevation_meters,
            overwrite_horizons=overwrite_horizons,
            compress_horizons=compress_horizons,
            moonlib_importer=import_moonlib,
            artifact_registrar=register_artifact_output,
            emit_progress=emit_job_progress,
            is_cancel_requested=is_job_cancel_requested,
        )
        return GenerateHorizonsResult.model_validate(payload)

    @staticmethod
    @contract(
        name="ToolImplementations.add_one",
        response_type=float,
        description="Return input value plus one.",
        tool_visibility=ToolVisibility.SYSTEM,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "system", "test"),
    )
    def add_one(value: float) -> float:
        return value + 1.0

    @staticmethod
    @contract(
        name="ToolImplementations.multiply",
        response_type=float,
        description="Return a * b for basic multi-argument contract testing.",
        tool_visibility=ToolVisibility.SYSTEM,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "system", "test"),
    )
    def multiply(a: float, b: float) -> float:
        return a * b

    @staticmethod
    @contract(
        name="ToolImplementations.ping",
        response_type=PingResult,
        description="Return a simple typed health-style payload.",
        tool_visibility=ToolVisibility.SYSTEM,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "system", "health"),
    )
    def ping(message: str = "ok") -> PingResult:
        return PingResult(ok=True, message=message)

    @staticmethod
    @contract(
        name="ToolImplementations.echo_upper",
        response_type=str,
        description="Return input text uppercased.",
        tool_visibility=ToolVisibility.SYSTEM,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "system", "test"),
    )
    def echo_upper(text: str) -> str:
        return text.upper()

    @staticmethod
    @contract(
        name="ToolImplementations.assistant_rag_ingest",
        response_type=AssistantRagIngestResult,
        description="Ingest git-managed RAG corpus files into the global assistant retrieval index.",
        tool_visibility=ToolVisibility.PUBLIC,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "assistant", "rag", "index"),
    )
    def assistant_rag_ingest(
        scenario_id: str = "global",
        relative_root: str = "",
        rebuild: bool = False,
        extensions: list[str] | None = None,
        respect_directives: bool = True,
        mode: JobMode = JobMode.QUEUED,
    ) -> AssistantRagIngestResult:
        del mode
        payload = execute_assistant_rag_ingest(
            scenario_id=scenario_id,
            relative_root=relative_root,
            rebuild=rebuild,
            extensions=extensions,
            respect_directives=respect_directives,
            create_index=create_default_rag_index,
            emit_progress=emit_job_progress,
            is_cancel_requested=is_job_cancel_requested,
            cancellation_error_factory=lambda: ApiError(
                status_code=409,
                code="assistant_rag_ingest_cancelled",
                message="RAG ingest canceled.",
            ),
        )
        return AssistantRagIngestResult.model_validate(payload)

    @staticmethod
    @contract(
        name="ToolImplementations.run_notebook_definition",
        response_type=NotebookJobResult,
        description="Run a discovered notebook job definition headlessly in a subprocess.",
        tool_visibility=ToolVisibility.SYSTEM,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "system", "notebook"),
    )
    def run_notebook_definition(
        scenario_id: str,
        notebook_job_id: str,
        params: dict[str, Any] | None = None,
        runtime_mode: Literal["osgeo", "moonlib"] = "osgeo",
        mode: JobMode = JobMode.QUEUED,
    ) -> NotebookJobResult:
        raw = execute_run_notebook_definition(
            scenario_id=scenario_id,
            notebook_job_id=notebook_job_id,
            params=params,
            runtime_mode=runtime_mode,
            notebook_executor=execute_notebook_job,
        )
        return NotebookJobResult(
            scenario_id=scenario_id,
            notebook_job_id=notebook_job_id,
            notebook_path=str(raw["notebook_path"]),
            notebook_hash=str(raw["notebook_hash"]),
            outputs=[
                NotebookOutputRecord.model_validate(item)
                for item in raw.get("outputs", [])
            ],
            result=raw.get("result", {}),
            progress_events=[
                item
                for item in raw.get("progress_events", [])
                if isinstance(item, dict)
            ],
        )

    @staticmethod
    def _load_viewshed_runtime_config() -> dict[str, Any]:
        app_cfg = load_app_config()
        backend_cfg = app_cfg.get("backend", {}) if isinstance(app_cfg, dict) else {}
        viewshed_cfg = backend_cfg.get("viewshed", {}) if isinstance(backend_cfg, dict) else {}
        if not isinstance(viewshed_cfg, dict):
            return {}
        return dict(viewshed_cfg)

    @staticmethod
    def _parabolic_error_m(max_range_m: float) -> float:
        # Difference between parabolic sag and spherical sag approximation.
        radius_m = 1_737_400.0
        distance_m = max(0.0, float(max_range_m))
        return (distance_m**4) / (8.0 * (radius_m**3)) if distance_m > 0.0 else 0.0

    @staticmethod
    def _estimate_pixel_size_meters(transform: Any) -> float:
        a = float(getattr(transform, "a", 0.0))
        e = float(getattr(transform, "e", 0.0))
        px_x = abs(a)
        px_y = abs(e)
        if px_x > 0.0 and px_y > 0.0:
            return (px_x + px_y) / 2.0
        return max(px_x, px_y, 1.0)

    @staticmethod
    def _compute_mask_connectivity_metrics(
        mask: np.ndarray,
        *,
        cleanup_mode: str = "none",
        cleanup_iterations: int = 1,
    ) -> tuple[int, int, float]:
        total = int(mask.sum())
        if total <= 0:
            return 0, 0, 0.0
        from scipy import ndimage  # type: ignore

        mode_key = str(cleanup_mode or "none").strip().lower() or "none"
        iterations = int(cleanup_iterations or 0)
        if iterations < 0:
            iterations = 0
        mask_bool = mask.astype(bool, copy=False)
        if mode_key in {"erosion", "opening"} and iterations > 0:
            structure = np.ones((3, 3), dtype=bool)
            if mode_key == "erosion":
                mask_bool = ndimage.binary_erosion(mask_bool, structure=structure, iterations=iterations)
            else:
                mask_bool = ndimage.binary_opening(mask_bool, structure=structure, iterations=iterations)

        total_after = int(np.count_nonzero(mask_bool))
        if total_after <= 0:
            return 0, 0, 0.0
        mask_u8 = mask_bool.astype(np.uint8, copy=False)

        # 8-neighbor support count per cell (excluding self).
        kernel = np.ones((3, 3), dtype=np.uint8)
        neighbor_count = ndimage.convolve(mask_u8, kernel, mode="constant", cval=0) - mask_u8
        adjacency_count = int(np.count_nonzero(mask_bool & (neighbor_count > 0)))

        structure = np.ones((3, 3), dtype=np.uint8)
        labeled, component_count = ndimage.label(mask_bool, structure=structure)
        if int(component_count) <= 0:
            return 0, 0, 0.0
        counts = np.bincount(labeled.ravel())
        largest_component = int(counts[1:].max()) if counts.size > 1 else 0
        adjacency_ratio = float(adjacency_count) / float(total_after)
        return int(component_count), largest_component, adjacency_ratio

    @staticmethod
    def _cuda_available() -> tuple[bool, str | None]:
        try:
            from numba import cuda as numba_cuda  # type: ignore
        except Exception as exc:
            return False, f"numba_cuda_import_failed: {exc}"
        try:
            if not bool(numba_cuda.is_available()):
                return False, "numba_cuda_not_available"
        except Exception as exc:
            return False, f"numba_cuda_probe_failed: {exc}"
        return True, None

    @staticmethod
    def _select_viewshed_backend(
        *,
        backend_mode: str,
        observer_count: int,
        observer_density: float,
        adjacency_ratio: float,
        largest_component_size: int,
        cfg: dict[str, Any],
    ) -> tuple[str, str]:
        requested = str(backend_mode or "auto").strip().lower() or "auto"
        if requested in {"gdal", "cuda"}:
            return requested, f"forced_{requested}"

        auto_min_observers = int(cfg.get("auto_cuda_min_observers", 256) or 256)
        auto_min_density = float(cfg.get("auto_cuda_min_density", 0.01) or 0.01)
        auto_min_adjacency_ratio = float(cfg.get("auto_cuda_min_adjacency_ratio", 0.20) or 0.20)
        auto_min_largest_component = int(cfg.get("auto_cuda_min_largest_component", 128) or 128)

        if observer_count >= auto_min_observers:
            return "cuda", f"observer_count>={auto_min_observers}"
        if observer_density >= auto_min_density and observer_count >= max(32, auto_min_observers // 2):
            return "cuda", f"observer_density>={auto_min_density}"
        if (
            adjacency_ratio >= auto_min_adjacency_ratio
            and largest_component_size >= auto_min_largest_component
            and observer_count >= max(32, auto_min_observers // 4)
        ):
            return "cuda", (
                f"adjacency_ratio>={auto_min_adjacency_ratio},"
                f"largest_component>={auto_min_largest_component}"
            )
        return "gdal", "auto_default_gdal"

    @staticmethod
    def _viewshed_progress(
        progress_events: list[dict[str, Any]],
        *,
        percent: float,
        stage: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "percent": float(percent),
            "stage": str(stage),
            "message": str(message),
        }
        if extra:
            payload.update(dict(extra))
        progress_events.append(payload)
        emit_job_progress(payload)

    @staticmethod
    def _default_viewshed_output_relative_path(
        *,
        scenario_id: str,
        observer_count: int,
        merge_mode: str,
        max_range_m: float,
    ) -> str:
        digest = hashlib.sha256(
            json.dumps(
                {
                    "scenario_id": scenario_id,
                    "observer_count": observer_count,
                    "merge_mode": merge_mode,
                    "max_range_m": float(max_range_m),
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:12]
        return f"analysis/viewshed/viewshed.{merge_mode}.{digest}.tif"

    @staticmethod
    def _resolve_viewshed_mask_path(
        *,
        scenario_id: str,
        scenario_root: Path,
        observer_mask: ViewshedObserverMaskReference,
    ) -> Path:
        rel_raw = str(observer_mask.relative_path or "").strip()
        product_id = str(observer_mask.product_id or "").strip()
        if rel_raw and product_id:
            raise ApiError(
                status_code=422,
                code="viewshed_invalid_argument",
                message="observer_mask cannot specify both relative_path and product_id.",
            )
        if rel_raw:
            rel = ToolImplementations._normalize_relative_path(rel_raw)
            path = (scenario_root / rel).resolve()
            if scenario_root != path and scenario_root not in path.parents:
                raise ApiError(
                    status_code=422,
                    code="viewshed_output_path_invalid",
                    message="observer_mask path escapes scenario root.",
                    details={"relative_path": rel},
                )
            return path
        if product_id:
            try:
                return ToolImplementations._resolve_product_input_path(
                    scenario_root=scenario_root,
                    scenario_id=scenario_id,
                    product_id=product_id,
                )
            except MapAlgebraError as exc:
                raise ApiError(
                    status_code=int(exc.status_code),
                    code="viewshed_input_not_found",
                    message=exc.message,
                    details=dict(exc.details),
                ) from exc
        raise ApiError(
            status_code=422,
            code="viewshed_invalid_argument",
            message="observer_mask requires relative_path or product_id.",
        )

    @staticmethod
    def _normalize_relative_path(relative_path: str) -> str:
        rel = str(relative_path or "").strip().replace("\\", "/")
        while "//" in rel:
            rel = rel.replace("//", "/")
        rel = rel.lstrip("/").rstrip("/")
        if not rel:
            raise MapAlgebraError(
                code="map_algebra_output_path_invalid",
                message="relative_path is required.",
            )
        parts = [part for part in rel.split("/") if part and part != "."]
        if any(part == ".." for part in parts):
            raise MapAlgebraError(
                code="map_algebra_output_path_invalid",
                message="relative_path cannot contain path traversal.",
                details={"relative_path": relative_path},
            )
        return "/".join(parts)

    @staticmethod
    def _resolve_product_input_path(
        *,
        scenario_root: Path,
        scenario_id: str,
        product_id: str,
    ) -> Path:
        db_path = (scenario_root / "scenario.db").resolve()
        if not db_path.exists() or not db_path.is_file():
            raise MapAlgebraError(
                code="map_algebra_input_not_found",
                message="Scenario database not found while resolving product input.",
                details={"scenario_id": scenario_id, "product_id": product_id},
                status_code=404,
            )
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT pf.relative_path
                FROM product_files pf
                JOIN products p ON p.product_id = pf.product_id
                WHERE pf.scenario_id = ? AND pf.product_id = ?
                ORDER BY pf.created_at_utc DESC
                LIMIT 1
                """,
                (scenario_id, product_id),
            ).fetchone()
        if row is None:
            raise MapAlgebraError(
                code="map_algebra_input_not_found",
                message=f"No raster file registered for product_id={product_id}.",
                details={"scenario_id": scenario_id, "product_id": product_id},
                status_code=404,
            )
        rel = ToolImplementations._normalize_relative_path(str(row[0]))
        resolved = (scenario_root / rel).resolve()
        if scenario_root != resolved and scenario_root not in resolved.parents:
            raise MapAlgebraError(
                code="map_algebra_output_path_invalid",
                message="Resolved input path escapes scenario root.",
                details={"product_id": product_id, "relative_path": rel},
            )
        return resolved

    @staticmethod
    def _resolve_temporal_signal_name(signal_name: str) -> str:
        alias = str(signal_name or "").strip().lower()
        if alias not in TEMPORAL_SIGNAL_ALIASES:
            raise MapAlgebraError(
                code="map_algebra_temporal_signal_unsupported",
                message=f"Unsupported temporal signal: {signal_name!r}",
                details={"allowed": sorted(TEMPORAL_SIGNAL_ALIASES.keys())},
            )
        return TEMPORAL_SIGNAL_ALIASES[alias]

    @staticmethod
    def _resolve_raster_input_paths(
        *,
        scenario_id: str,
        scenario_root: Path,
        raw_inputs: dict[str, Any],
    ) -> tuple[dict[str, Path], dict[str, dict[str, Any]], dict[str, str]]:
        if not isinstance(raw_inputs, dict) or not raw_inputs:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="inputs must be a non-empty object.",
            )
        resolved_paths: dict[str, Path] = {}
        input_refs: dict[str, dict[str, Any]] = {}
        temporal_signals: dict[str, str] = {}
        for name, raw_ref in sorted(raw_inputs.items(), key=lambda item: item[0]):
            variable_name = str(name or "").strip()
            if not variable_name:
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message="Input variable names must be non-empty.",
                )
            if not isinstance(raw_ref, dict):
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message=f"Input '{variable_name}' must be an object.",
                )
            ref = RasterInputReference.model_validate(raw_ref)
            rel_raw = str(ref.relative_path or "").strip()
            product_id = str(ref.product_id or "").strip()
            signal_name = str(ref.signal or "").strip()
            if signal_name:
                if rel_raw or product_id:
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message=(
                            f"Input '{variable_name}' cannot combine signal with "
                            "relative_path/product_id."
                        ),
                        details={"input_name": variable_name},
                    )
                native_signal = ToolImplementations._resolve_temporal_signal_name(signal_name)
                temporal_signals[variable_name] = native_signal
                input_refs[variable_name] = {
                    "signal": signal_name.strip().lower(),
                    "native_signal": native_signal,
                }
                continue
            if not rel_raw and not product_id:
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message=(
                        f"Input '{variable_name}' must include relative_path, product_id, "
                        "or signal."
                    ),
                    details={"input_name": variable_name},
                )
            if rel_raw:
                rel = ToolImplementations._normalize_relative_path(rel_raw)
                absolute = (scenario_root / rel).resolve()
                if scenario_root != absolute and scenario_root not in absolute.parents:
                    raise MapAlgebraError(
                        code="map_algebra_output_path_invalid",
                        message=f"Input path escapes scenario root: {variable_name}",
                        details={"input_name": variable_name, "relative_path": rel},
                    )
                resolved_paths[variable_name] = absolute
                input_refs[variable_name] = {"relative_path": rel}
                continue
            absolute = ToolImplementations._resolve_product_input_path(
                scenario_root=scenario_root,
                scenario_id=scenario_id,
                product_id=product_id,
            )
            relative = absolute.relative_to(scenario_root).as_posix()
            resolved_paths[variable_name] = absolute
            input_refs[variable_name] = {"product_id": product_id, "relative_path": relative}
        return resolved_paths, input_refs, temporal_signals

    @staticmethod
    def _resolve_raster_transform_temporal_source_name(source_name: str) -> str:
        alias = str(source_name or "").strip().lower()
        if alias == "station_over_horizon_deg":
            raise raster_transform.RasterTransformError(
                code="raster_transform_temporal_signal_unsupported",
                message="station_over_horizon_deg is not implemented in the worker runtime.",
                details={"temporal_source": alias},
            )
        if alias not in RASTER_TRANSFORM_TEMPORAL_SOURCE_ALIASES:
            raise raster_transform.RasterTransformError(
                code="raster_transform_temporal_signal_unsupported",
                message=f"Unsupported raster transform temporal source: {source_name!r}",
                details={"allowed": sorted(RASTER_TRANSFORM_TEMPORAL_SOURCE_ALIASES.keys()) + ["station_over_horizon_deg"]},
            )
        return RASTER_TRANSFORM_TEMPORAL_SOURCE_ALIASES[alias]

    @staticmethod
    def _resolve_raster_transform_input_bindings(
        *,
        scenario_id: str,
        scenario_root: Path,
        raw_inputs: dict[str, Any],
    ) -> tuple[dict[str, Path], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any] | None]:
        if not isinstance(raw_inputs, dict) or not raw_inputs:
            raise raster_transform.RasterTransformError(
                code="raster_transform_invalid_argument",
                message="inputs must be a non-empty object.",
            )
        resolved_paths: dict[str, Path] = {}
        input_refs: dict[str, dict[str, Any]] = {}
        temporal_bindings: dict[str, dict[str, Any]] = {}
        time_binding: dict[str, Any] | None = None
        for name, raw_ref in sorted(raw_inputs.items(), key=lambda item: item[0]):
            variable_name = str(name or "").strip()
            if not variable_name:
                raise raster_transform.RasterTransformError(
                    code="raster_transform_invalid_argument",
                    message="Input variable names must be non-empty.",
                )
            if variable_name in raster_transform.SAFE_FACADE_NAMES:
                raise raster_transform.RasterTransformError(
                    code="raster_transform_invalid_argument",
                    message=f"Input binding name is reserved: {variable_name}",
                    details={
                        "input_name": variable_name,
                        "hint": "Reserved runtime facades (for example `np`) cannot be used as input names.",
                    },
                )
            if not isinstance(raw_ref, dict):
                raise raster_transform.RasterTransformError(
                    code="raster_transform_invalid_argument",
                    message=f"Input '{variable_name}' must be an object.",
                )
            ref = RasterInputReference.model_validate(raw_ref)
            kind = str(ref.kind or "").strip().lower()
            rel_raw = str(ref.relative_path or "").strip()
            product_id = str(ref.product_id or "").strip()
            signal_name = str(ref.signal or "").strip()
            temporal_source = str(ref.temporal_source or "").strip().lower()
            times_ref = str(ref.times or "").strip()
            station_name = str(ref.station_name or "").strip()
            start_utc = str(ref.start_utc or "").strip()
            stop_utc = str(ref.stop_utc or "").strip()
            step_hours = ref.step_hours

            is_times_binding = kind == "times" or any((start_utc, stop_utc, step_hours is not None))
            if is_times_binding:
                if variable_name != "times":
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_invalid_argument",
                        message="The reserved temporal domain binding must be named 'times'.",
                        details={
                            "input_name": variable_name,
                            "hint": "Use a single reserved binding named `times` with start_utc/stop_utc/step_hours.",
                        },
                    )
                if any((rel_raw, product_id, signal_name, temporal_source, station_name, times_ref)):
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_invalid_argument",
                        message="The reserved 'times' binding cannot be combined with raster or temporal source fields.",
                        details={"input_name": variable_name},
                    )
                if not start_utc or not stop_utc or step_hours is None:
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_temporal_time_range_required",
                        message="The reserved 'times' binding requires start_utc, stop_utc, and step_hours.",
                    )
                if float(step_hours) <= 0.0:
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_invalid_argument",
                        message="times.step_hours must be > 0.",
                    )
                time_binding = {
                    "binding_name": variable_name,
                    "start_utc": start_utc,
                    "stop_utc": stop_utc,
                    "step_hours": float(step_hours),
                }
                input_refs[variable_name] = {"kind": "times", **time_binding}
                continue

            if temporal_source:
                if any((rel_raw, product_id, signal_name)):
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_invalid_argument",
                        message=(
                            f"Input '{variable_name}' cannot combine temporal_source with "
                            "relative_path/product_id/signal."
                        ),
                        details={"input_name": variable_name},
                    )
                native_signal = ToolImplementations._resolve_raster_transform_temporal_source_name(temporal_source)
                temporal_bindings[variable_name] = {
                    "temporal_source": temporal_source,
                    "native_signal": native_signal,
                    "times": times_ref or "times",
                    "station_name": station_name or None,
                }
                input_refs[variable_name] = {
                    "temporal_source": temporal_source,
                    "native_signal": native_signal,
                    "times": times_ref or "times",
                }
                if station_name:
                    input_refs[variable_name]["station_name"] = station_name
                continue

            if signal_name:
                if rel_raw or product_id:
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_invalid_argument",
                        message=(
                            f"Input '{variable_name}' cannot combine signal with "
                            "relative_path/product_id."
                        ),
                        details={"input_name": variable_name},
                    )
                native_signal = ToolImplementations._resolve_temporal_signal_name(signal_name)
                temporal_bindings[variable_name] = {
                    "temporal_source": "legacy_signal",
                    "native_signal": native_signal,
                    "legacy_signal": signal_name.strip().lower(),
                    "times": "times",
                }
                input_refs[variable_name] = {
                    "signal": signal_name.strip().lower(),
                    "native_signal": native_signal,
                }
                continue

            if not rel_raw and not product_id:
                raise raster_transform.RasterTransformError(
                    code="raster_transform_invalid_argument",
                    message=(
                        f"Input '{variable_name}' must include relative_path, product_id, "
                        "signal, or temporal_source."
                    ),
                    details={"input_name": variable_name},
                )
            if rel_raw:
                rel = ToolImplementations._normalize_relative_path(rel_raw)
                absolute = (scenario_root / rel).resolve()
                if scenario_root != absolute and scenario_root not in absolute.parents:
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_output_path_invalid",
                        message=f"Input path escapes scenario root: {variable_name}",
                        details={"input_name": variable_name, "relative_path": rel},
                    )
                resolved_paths[variable_name] = absolute
                input_refs[variable_name] = {"relative_path": rel}
                continue
            absolute = ToolImplementations._resolve_product_input_path(
                scenario_root=scenario_root,
                scenario_id=scenario_id,
                product_id=product_id,
            )
            relative = absolute.relative_to(scenario_root).as_posix()
            resolved_paths[variable_name] = absolute
            input_refs[variable_name] = {"product_id": product_id, "relative_path": relative}

        for variable_name, spec in temporal_bindings.items():
            times_name = str(spec.get("times", "")).strip() or "times"
            if times_name != "times":
                raise raster_transform.RasterTransformError(
                    code="raster_transform_invalid_argument",
                    message="Temporal raster bindings must reference the reserved 'times' binding in v1.",
                    details={
                        "input_name": variable_name,
                        "times": times_name,
                        "hint": "Set the temporal input binding field `times` to `times`.",
                    },
                )
        return resolved_paths, input_refs, temporal_bindings, time_binding

    @staticmethod
    def _resolve_raster_transform_time_domain(
        *,
        time_binding: dict[str, Any] | None,
        time_start_utc: str | None,
        time_stop_utc: str | None,
        time_step_hours: float | None,
        has_temporal_inputs: bool,
    ) -> tuple[str, str, float | None]:
        binding_start = ""
        binding_stop = ""
        binding_step: float | None = None
        if time_binding is not None:
            binding_start = str(time_binding.get("start_utc", "")).strip()
            binding_stop = str(time_binding.get("stop_utc", "")).strip()
            raw_step = time_binding.get("step_hours")
            binding_step = None if raw_step is None else float(raw_step)
        legacy_start = str(time_start_utc or "").strip()
        legacy_stop = str(time_stop_utc or "").strip()
        legacy_step = None if time_step_hours is None else float(time_step_hours)
        if time_binding is not None and any((legacy_start, legacy_stop, legacy_step is not None)):
            mismatch = (
                legacy_start != binding_start
                or legacy_stop != binding_stop
                or legacy_step != binding_step
            )
            if mismatch:
                raise raster_transform.RasterTransformError(
                    code="raster_transform_invalid_argument",
                    message=(
                        "Top-level time_* request fields must match the reserved 'times' binding "
                        "when both are supplied."
                    ),
                    details={
                        "hint": (
                            "Use one canonical temporal domain source. Prefer the reserved `times` binding and "
                            "omit top-level time_start_utc/time_stop_utc/time_step_hours."
                        ),
                    },
                )
        if time_binding is not None:
            return binding_start, binding_stop, binding_step
        if has_temporal_inputs:
            return legacy_start, legacy_stop, legacy_step
        return "", "", legacy_step

    @staticmethod
    def _raster_transform_prefilter_failure_payload(
        *,
        stage: str,
        error: raster_transform.RasterTransformError,
        planner_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "eligible": False,
            "failure_stage": str(stage),
            "error": {
                "code": str(error.code),
                "message": str(error.message),
                "details": dict(error.details),
            },
            "planner_summary": planner_summary,
        }

    @staticmethod
    def _raster_transform_prefilter_validate(*, arguments: dict[str, Any]) -> dict[str, Any]:
        """
        Internal eval pre-filter validator for raster.transform requests.
        This is intentionally helper-only and must not be registered as a public tool.
        """
        stage = "binding_validate"
        plan: raster_transform.RasterTransformPlan | None = None
        parsed: raster_transform.ParsedScript | None = None

        try:
            try:
                request = RasterTransformRequest.model_validate(arguments)
            except Exception as exc:
                raise raster_transform.RasterTransformError(
                    code="raster_transform_invalid_argument",
                    message="Invalid raster.transform arguments.",
                    details={
                        "error": str(exc),
                        "hint": "Provide a valid raster.transform request with scenario_id, script, and inputs.",
                    },
                ) from exc

            resolved = resolve_scenario_paths(request.scenario_id)
            scenario_root = Path(resolved.scenario_root_dir).expanduser().resolve()
            dem_path = Path(resolved.dem_path).expanduser().resolve()
            if not scenario_root.exists() or not scenario_root.is_dir():
                raise raster_transform.RasterTransformError(
                    code="raster_transform_input_not_found",
                    message=f"Scenario root directory not found: {scenario_root}",
                    status_code=404,
                )
            if not dem_path.exists() or not dem_path.is_file():
                raise raster_transform.RasterTransformError(
                    code="raster_transform_input_not_found",
                    message=f"Scenario DEM not found: {dem_path}",
                    details={"scenario_id": request.scenario_id},
                    status_code=404,
                )

            resolved_inputs, _input_refs, temporal_bindings, time_binding = (
                ToolImplementations._resolve_raster_transform_input_bindings(
                    scenario_id=request.scenario_id,
                    scenario_root=scenario_root,
                    raw_inputs={
                        str(name): (
                            ref.model_dump(mode="python")
                            if isinstance(ref, RasterTransformInputReference)
                            else dict(ref or {})
                        )
                        for name, ref in request.inputs.items()
                    },
                )
            )

            stage = "parse_validate"
            parsed = raster_transform.parse_validate_script(
                request.script,
                allowed_variables=set(resolved_inputs.keys()) | set(temporal_bindings.keys()),
            )

            stage = "build_plan"
            target_grid = raster_transform.load_target_grid_from_dem(dem_path)
            temporal_input_names = sorted(temporal_bindings.keys())
            static_input_names = sorted(resolved_inputs.keys())
            start_utc, stop_utc, step_hours = ToolImplementations._resolve_raster_transform_time_domain(
                time_binding=time_binding,
                time_start_utc=request.time_start_utc,
                time_stop_utc=request.time_stop_utc,
                time_step_hours=request.time_step_hours,
                has_temporal_inputs=bool(temporal_input_names),
            )

            if temporal_input_names:
                if not start_utc or not stop_utc or step_hours is None:
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_temporal_time_range_required",
                        message=(
                            "Temporal inputs require a reserved 'times' binding or matching "
                            "time_start_utc/time_stop_utc/time_step_hours request fields."
                        ),
                    )
                if float(step_hours) <= 0.0:
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_invalid_argument",
                        message="time_step_hours must be > 0.",
                    )
                try:
                    horizons_dir = ToolImplementations._resolve_temporal_horizons_dir(
                        scenario_root=scenario_root,
                        horizons_relative_dir=request.horizons_relative_dir,
                    )
                except MapAlgebraError as exc:
                    raise raster_transform._translate_map_error(exc) from exc
                if not horizons_dir.exists() or not horizons_dir.is_dir():
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_temporal_horizons_not_found",
                        message=f"Horizons directory not found: {horizons_dir}",
                        status_code=404,
                    )
                native_signals: list[str] = []
                for variable_name in temporal_input_names:
                    native = str(temporal_bindings[variable_name]["native_signal"])
                    if native not in native_signals:
                        native_signals.append(native)
                temporal_request = ToolImplementations._build_raster_transform_temporal_request(
                    scenario_root=scenario_root,
                    dem_path=dem_path,
                    horizons_dir=horizons_dir,
                    time_start_utc=start_utc,
                    time_stop_utc=stop_utc,
                    time_step_hours=step_hours,
                    observer_elevation_meters=request.observer_elevation_meters,
                    patch_width=request.patch_width,
                    patch_height=request.patch_height,
                    chunk_time_count=request.chunk_time_count,
                    use_spice_sun_vectors=request.use_spice_sun_vectors,
                    use_spice_earth_vectors=request.use_spice_earth_vectors,
                    native_signals=native_signals,
                )
                total_time_count = int(temporal_request.time_count())
            else:
                total_time_count = 1

            plan = raster_transform.build_plan(
                parsed=parsed,
                target_grid=target_grid,
                static_input_names=static_input_names,
                temporal_input_names=temporal_input_names,
                spatial_partitioning=request.spatial_partitioning,
                time_partitioning=request.time_partitioning,
                spatial_halo_pixels=request.spatial_halo_pixels,
                patch_width=request.patch_width,
                patch_height=request.patch_height,
                time_count=total_time_count,
            )

            stage = "estimate_resources"
            raster_transform.enforce_plan_limits(plan)
            return {
                "eligible": True,
                "failure_stage": None,
                "error": None,
                "planner_summary": plan.model_dump(),
                "script_hash": raster_transform.compute_script_hash(parsed.normalized_script),
                "used_variables": sorted(parsed.used_variables),
                "used_functions": sorted(parsed.used_functions),
                "used_operators": sorted(parsed.used_operators),
                "temporal_inputs": temporal_input_names,
                "static_inputs": static_input_names,
            }
        except raster_transform.RasterTransformError as error:
            return ToolImplementations._raster_transform_prefilter_failure_payload(
                stage=stage,
                error=error,
                planner_summary=None if plan is None else plan.model_dump(),
            )
        except Exception as exc:  # pragma: no cover - defensive internal error path
            internal_error = raster_transform.RasterTransformError(
                code="raster_transform_internal_error",
                message="Internal pre-filter validation failed.",
                details={"error": str(exc)},
                status_code=500,
            )
            return ToolImplementations._raster_transform_prefilter_failure_payload(
                stage=stage,
                error=internal_error,
                planner_summary=None if plan is None else plan.model_dump(),
            )

    @staticmethod
    def _emit_map_algebra_progress(
        progress_events: list[dict[str, Any]],
        *,
        percent: float,
        message: str,
        stage: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "percent": float(percent),
            "message": str(message),
            "stage": str(stage),
        }
        if extra:
            payload.update(dict(extra))
        progress_events.append(payload)
        emit_job_progress(payload)

    @staticmethod
    def _default_map_algebra_output_relative_path(
        *,
        expression: str,
        input_paths: dict[str, Path],
    ) -> str:
        digest = expression_digest(expression=expression, inputs=input_paths)
        return f"analysis/map_algebra/raster_calc_{digest}.tif"

    @staticmethod
    def _default_raster_transform_output_relative_path(
        *,
        script: str,
        input_paths: dict[str, Path],
    ) -> str:
        digest = expression_digest(expression=script, inputs=input_paths)
        return f"analysis/raster_transform/raster_transform_{digest}.tif"

    @staticmethod
    def _default_colormap_rgba_output_relative_path(source_relative_path: str) -> str:
        source = Path(source_relative_path.replace("\\", "/"))
        stem = source.stem
        if stem.lower().endswith(".rgba"):
            file_name = f"{stem}.tif"
        else:
            file_name = f"{stem}.rgba.tif"
        return str((source.parent / file_name).as_posix()).lstrip("/")

    @staticmethod
    def _api_error_from_map_algebra(error: MapAlgebraError) -> ApiError:
        return ApiError(
            status_code=int(error.status_code),
            code=error.code,
            message=error.message,
            details=dict(error.details),
        )

    @staticmethod
    def _api_error_from_raster_transform(error: raster_transform.RasterTransformError) -> ApiError:
        return ApiError(
            status_code=int(error.status_code),
            code=error.code,
            message=error.message,
            details=dict(error.details),
        )

    @staticmethod
    def _resolve_temporal_horizons_dir(
        *,
        scenario_root: Path,
        horizons_relative_dir: str | None,
    ) -> Path:
        raw = str(horizons_relative_dir or "").strip()
        if not raw:
            return (scenario_root / "lighting" / "horizons").resolve()
        if Path(raw).is_absolute():
            candidate = Path(raw).expanduser().resolve()
        else:
            rel = ToolImplementations._normalize_relative_path(raw)
            candidate = (scenario_root / rel).resolve()
        if scenario_root != candidate and scenario_root not in candidate.parents:
            raise MapAlgebraError(
                code="map_algebra_output_path_invalid",
                message="Temporal horizons path escapes scenario root.",
                details={"horizons_relative_dir": horizons_relative_dir},
            )
        return candidate

    @staticmethod
    def _build_raster_transform_temporal_request(
        *,
        scenario_root: Path,
        dem_path: Path,
        horizons_dir: Path,
        time_start_utc: str,
        time_stop_utc: str,
        time_step_hours: float,
        observer_elevation_meters: float,
        patch_width: int,
        patch_height: int,
        chunk_time_count: int,
        use_spice_sun_vectors: bool,
        use_spice_earth_vectors: bool,
        native_signals: list[str],
    ) -> LightmapStreamRequestV2Py:
        signal_specs = [TemporalSignalSpecPy(signal=name) for name in native_signals]
        return LightmapStreamRequestV2Py(
            scenario_root_dir=scenario_root,
            dem_path=dem_path,
            surrounding_dem_paths=[],
            horizon_dir=horizons_dir,
            start_utc=time_start_utc,
            stop_utc=time_stop_utc,
            time_step_hours=float(time_step_hours),
            observer_elevation_meters=float(observer_elevation_meters),
            patch_width=max(1, int(patch_width)),
            patch_height=max(1, int(patch_height)),
            max_read_parallelism=4,
            max_compute_parallelism=24,
            ready_queue_capacity=64,
            use_spice_sun_vectors=bool(use_spice_sun_vectors),
            mode="signal_stream",
            signals=signal_specs,
            chunk_time_count=max(1, int(chunk_time_count)),
            reducers=None,
            use_spice_earth_vectors=bool(use_spice_earth_vectors),
        )

    @staticmethod
    def _load_raster_transform_full_extent_temporal_inputs(
        *,
        client: LightmapStreamingClient,
        request: LightmapStreamRequestV2Py,
        temporal_input_names: list[str],
        temporal_signals: dict[str, str],
        target_grid: Any,
        available_patch_keys: set[tuple[int, int]],
        missing_patch_keys: set[tuple[int, int]],
        buffer_count: int,
        poll_timeout_ms: int,
        progress_events: list[dict[str, Any]],
    ) -> tuple[dict[str, np.ndarray], np.ndarray, int]:
        total_time_count = int(request.time_count())
        variables: dict[str, np.ndarray] = {}
        for variable_name in temporal_input_names:
            variables[variable_name] = np.zeros(
                (
                    total_time_count,
                    int(target_grid.height),
                    int(target_grid.width),
                ),
                dtype=np.float32,
            )
        patch_invalid_mask = np.zeros(
            (int(target_grid.height), int(target_grid.width)),
            dtype=bool,
        )
        for patch_key in missing_patch_keys:
            raster_transform.mark_patch_invalid(
                patch_invalid_mask,
                patch_key=patch_key,
                patch_width=int(request.patch_width),
                patch_height=int(request.patch_height),
                width=int(target_grid.width),
                height=int(target_grid.height),
            )
        native_index = {
            signal: idx
            for idx, signal in enumerate(
                [spec.signal for spec in (request.signals or [])]
            )
        }
        tile_count = 0
        seen_patch_keys: set[tuple[int, int]] = set()
        for tile_meta, tile_chunk in stream_tiles_v2(
            client,
            request,
            buffer_count=max(1, int(buffer_count)),
            poll_timeout_ms=max(1, int(poll_timeout_ms)),
        ):
            _raise_if_transform_cancelled()
            if int(tile_meta.rank) != 4 or int(getattr(tile_chunk, "ndim", 0)) != 4:
                raise raster_transform.RasterTransformError(
                    code="raster_transform_temporal_stream_failed",
                    message="Temporal signal stream returned an unexpected tensor rank.",
                    details={
                        "rank": int(tile_meta.rank),
                        "ndim": int(getattr(tile_chunk, "ndim", 0)),
                    },
                )
            time_offset = int(tile_meta.time_offset)
            chunk_time_count_local = int(tile_meta.time_count)
            yoff = int(tile_meta.patch_row)
            xoff = int(tile_meta.patch_col)
            tile_height = int(tile_meta.height)
            tile_width = int(tile_meta.width)
            seen_patch_keys.add((yoff, xoff))
            for variable_name in temporal_input_names:
                native_signal = temporal_signals[variable_name]
                chan = int(native_index[native_signal])
                chunk_view = np.asarray(
                    tile_chunk[:chunk_time_count_local, chan, :tile_height, :tile_width],
                    dtype=np.float32,
                )
                variables[variable_name][
                    time_offset : time_offset + chunk_time_count_local,
                    yoff : yoff + tile_height,
                    xoff : xoff + tile_width,
                ] = chunk_view
            tile_count += 1
            if tile_count % 20 == 0:
                ToolImplementations._emit_map_algebra_progress(
                    progress_events,
                    percent=min(82.0, 45.0 + (tile_count / 5.0)),
                    message=f"Loaded {tile_count} temporal tile chunk(s).",
                    stage="execute_transform",
                )
        missing_available_tiles = sorted(available_patch_keys - seen_patch_keys)
        if missing_available_tiles:
            raise raster_transform.RasterTransformError(
                code="raster_transform_temporal_stream_failed",
                message="Temporal stream terminated before all available horizon patches were returned.",
                details={"missing_patch_count": len(missing_available_tiles)},
            )
        return variables, patch_invalid_mask, len(missing_patch_keys)

    @staticmethod
    def _execute_raster_transform_tiled_temporal(
        *,
        client: LightmapStreamingClient,
        request: LightmapStreamRequestV2Py,
        parsed: raster_transform.ParsedScript,
        aligned: dict[str, Any],
        static_input_names: list[str],
        temporal_input_names: list[str],
        temporal_signals: dict[str, str],
        target_grid: Any,
        all_raster_input_names: set[str],
        available_patch_keys: set[tuple[int, int]],
        missing_patch_keys: set[tuple[int, int]],
        output_path: Path,
        overwrite: bool,
        progress_events: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> tuple[str, float | None, np.ndarray | None, int, float | None, float | None, int]:
        if output_path.exists() and not overwrite:
            raise raster_transform.RasterTransformError(
                code="raster_transform_output_exists",
                message=f"Output file already exists: {output_path}",
                details={"output_path": str(output_path)},
                status_code=409,
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        native_signals = [spec.signal for spec in (request.signals or [])]
        channel_index = {name: idx for idx, name in enumerate(native_signals)}
        tile_states: dict[tuple[int, int], dict[str, Any]] = {}
        output_dataset = None
        output_dtype = ""
        output_nodata = None
        tiles_written = 0
        value_min: float | None = None
        value_max: float | None = None
        total_time_count = int(request.time_count())
        seen_patch_keys: set[tuple[int, int]] = set()
        try:
            for tile_meta, tile_chunk in stream_tiles_v2(
                client,
                request,
                buffer_count=metadata.get("buffer_count", 6),
                poll_timeout_ms=metadata.get("poll_timeout_ms", 250),
            ):
                _raise_if_transform_cancelled()
                if int(tile_meta.rank) != 4 or int(getattr(tile_chunk, "ndim", 0)) != 4:
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_temporal_stream_failed",
                        message="Temporal signal stream returned an unexpected tensor rank.",
                        details={
                            "rank": int(tile_meta.rank),
                            "ndim": int(getattr(tile_chunk, "ndim", 0)),
                        },
                    )
                tile_key = (int(tile_meta.patch_row), int(tile_meta.patch_col))
                seen_patch_keys.add(tile_key)
                state = tile_states.get(tile_key)
                if state is None:
                    state = {
                        "chunks": {name: [] for name in temporal_input_names},
                        "next_time_offset": 0,
                        "seen_time_count": 0,
                    }
                    tile_states[tile_key] = state
                expected_time_offset = int(state["next_time_offset"])
                if int(tile_meta.time_offset) != expected_time_offset:
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_temporal_stream_failed",
                        message="Temporal tile chunks arrived out of order.",
                        details={
                            "tile_key": [tile_key[0], tile_key[1]],
                            "expected_time_offset": expected_time_offset,
                            "time_offset": int(tile_meta.time_offset),
                        },
                    )
                tile_height = int(tile_meta.height)
                tile_width = int(tile_meta.width)
                chunk_time_count_local = int(tile_meta.time_count)
                for variable_name in temporal_input_names:
                    native_signal = temporal_signals[variable_name]
                    chan = int(channel_index[native_signal])
                    chunk_view = np.asarray(
                        tile_chunk[:chunk_time_count_local, chan, :tile_height, :tile_width],
                        dtype=np.float32,
                    )
                    state["chunks"][variable_name].append(chunk_view)
                state["next_time_offset"] = expected_time_offset + chunk_time_count_local
                state["seen_time_count"] = int(state["seen_time_count"]) + chunk_time_count_local
                if int(state["seen_time_count"]) < total_time_count:
                    continue
                yoff, xoff = tile_key
                variables: dict[str, Any] = {}
                nodata_masks: dict[str, np.ndarray] = {}
                for variable_name in static_input_names:
                    record = aligned[variable_name]
                    y1 = yoff + tile_height
                    x1 = xoff + tile_width
                    variables[variable_name] = np.asarray(record.data[yoff:y1, xoff:x1], dtype=np.float32)
                    nodata_masks[variable_name] = np.asarray(
                        record.nodata_mask[yoff:y1, xoff:x1],
                        dtype=bool,
                    )
                for variable_name in temporal_input_names:
                    temporal_cube = np.concatenate(state["chunks"][variable_name], axis=0)
                    variables[variable_name] = temporal_cube
                tile_result = raster_transform.execute_script(
                    parsed=parsed,
                    variables=variables,
                    target_shape=(tile_height, tile_width),
                    transform=target_grid.transform,
                    metadata={"time_step_hours": metadata.get("time_step_hours")},
                )
                if tile_result.ndim == 3:
                    if int(tile_result.shape[0]) != 1:
                        raise raster_transform.RasterTransformError(
                            code="raster_transform_temporal_reduce_required",
                            message="Temporal transform result must be reduced before writing a raster.",
                            details={
                                "tile_key": [tile_key[0], tile_key[1]],
                                "time_count": int(tile_result.shape[0]),
                            },
                        )
                    tile_result = np.asarray(tile_result[0], dtype=np.float32)
                semantic = raster_transform.infer_result_semantic(tile_result)
                tile_array, tile_dtype, tile_nodata, tile_valid_mask = raster_transform.finalize_output_array(
                    result=np.asarray(tile_result),
                    semantic=semantic,
                    used_variables=parsed.used_variables,
                    participating_variables=all_raster_input_names,
                    nodata_masks=nodata_masks,
                )
                if output_dataset is None:
                    output_dtype = str(tile_dtype)
                    output_nodata = tile_nodata
                    output_dataset = rasterio.open(
                        output_path,
                        "w",
                        driver="GTiff",
                        width=int(target_grid.width),
                        height=int(target_grid.height),
                        count=1,
                        dtype=output_dtype,
                        crs=target_grid.crs,
                        transform=target_grid.transform,
                        nodata=output_nodata,
                        tiled=True,
                        blockxsize=128,
                        blockysize=128,
                        compress="lzw",
                        bigtiff="IF_SAFER",
                    )
                    if output_nodata is not None:
                        output_dataset.write(
                            np.full(
                                (int(target_grid.height), int(target_grid.width)),
                                output_nodata,
                                dtype=np.dtype(output_dtype),
                            ),
                            1,
                        )
                write_width = min(tile_width, int(target_grid.width) - xoff)
                write_height = min(tile_height, int(target_grid.height) - yoff)
                if write_width <= 0 or write_height <= 0:
                    del tile_states[tile_key]
                    continue
                tile_window = np.asarray(tile_array[:write_height, :write_width], dtype=np.dtype(output_dtype))
                window = ((int(yoff), int(yoff) + int(write_height)), (int(xoff), int(xoff) + int(write_width)))
                output_dataset.write(tile_window, 1, window=window)
                if tile_valid_mask is not None:
                    output_dataset.write_mask(
                        np.where(tile_valid_mask[:write_height, :write_width], 255, 0).astype(np.uint8),
                        window=window,
                    )
                local_min, local_max = raster_transform.compute_value_range(
                    tile_window,
                    nodata_value=output_nodata,
                    valid_mask=None if tile_valid_mask is None else tile_valid_mask[:write_height, :write_width],
                )
                if local_min is not None and local_max is not None:
                    value_min = local_min if value_min is None else min(value_min, local_min)
                    value_max = local_max if value_max is None else max(value_max, local_max)
                tiles_written += 1
                if tiles_written % 20 == 0:
                    ToolImplementations._emit_map_algebra_progress(
                        progress_events,
                        percent=min(84.0, 45.0 + (tiles_written / 5.0)),
                        message=f"Processed {tiles_written} temporal tile(s).",
                        stage="execute_transform",
                    )
                del tile_states[tile_key]
            if tile_states:
                raise raster_transform.RasterTransformError(
                    code="raster_transform_temporal_stream_failed",
                    message="Temporal stream terminated before all tiles were finalized.",
                    details={"pending_tile_count": len(tile_states)},
                )
            missing_available_tiles = sorted(available_patch_keys - seen_patch_keys)
            if missing_available_tiles:
                raise raster_transform.RasterTransformError(
                    code="raster_transform_temporal_stream_failed",
                    message="Temporal stream terminated before all available horizon patches were returned.",
                    details={"missing_patch_count": len(missing_available_tiles)},
                )
            if output_dataset is None:
                output_dtype = "float32"
                output_nodata = float(-9999.0)
                output_dataset = rasterio.open(
                    output_path,
                    "w",
                    driver="GTiff",
                    width=int(target_grid.width),
                    height=int(target_grid.height),
                    count=1,
                    dtype=output_dtype,
                    crs=target_grid.crs,
                    transform=target_grid.transform,
                    nodata=output_nodata,
                    tiled=True,
                    blockxsize=128,
                    blockysize=128,
                    compress="lzw",
                    bigtiff="IF_SAFER",
                )
                output_dataset.write(
                    np.full(
                        (int(target_grid.height), int(target_grid.width)),
                        output_nodata,
                        dtype=np.float32,
                    ),
                    1,
                )
            for patch_key in sorted(missing_patch_keys):
                yoff, xoff = patch_key
                write_width = min(int(request.patch_width), int(target_grid.width) - int(xoff))
                write_height = min(int(request.patch_height), int(target_grid.height) - int(yoff))
                if write_width <= 0 or write_height <= 0:
                    continue
                fill_value = np.float32(output_nodata if output_nodata is not None else 0.0)
                tile_window = np.full((write_height, write_width), fill_value, dtype=np.dtype(output_dtype))
                window = ((int(yoff), int(yoff) + int(write_height)), (int(xoff), int(xoff) + int(write_width)))
                output_dataset.write(tile_window, 1, window=window)
                output_dataset.write_mask(np.zeros((write_height, write_width), dtype=np.uint8), window=window)
        finally:
            if output_dataset is not None:
                try:
                    output_dataset.FlushCache()
                except Exception:
                    pass
                try:
                    output_dataset.close()
                except Exception:
                    pass
            output_dataset = None
        size_bytes = int(output_path.stat().st_size) if output_path.exists() else 0
        return output_dtype or "float32", output_nodata, None, size_bytes, value_min, value_max, len(missing_patch_keys)

    @staticmethod
    def _resolve_overwrite_mode(
        *,
        overwrite_mode: str | None,
    ) -> tuple[str, bool]:
        mode_key = str(overwrite_mode or "ask").strip().lower() or "ask"
        if mode_key not in {"ask", "never", "always"}:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="overwrite_mode must be one of: ask, never, always.",
                details={"overwrite_mode": overwrite_mode},
            )
        return mode_key, mode_key == "always"

    @staticmethod
    def _resolve_raster_transform_overwrite_mode(
        *,
        overwrite_mode: str | None,
        overwrite: bool | None,
    ) -> tuple[str, bool]:
        raw_mode = str(overwrite_mode).strip().lower() if overwrite_mode is not None else ""
        # Compatibility path: treat legacy boolean overwrite as authoritative when mode is omitted
        # or left at default ask.
        if overwrite is not None and raw_mode in {"", "ask"}:
            mode_key = "always" if bool(overwrite) else "never"
        elif overwrite_mode is not None:
            mode_key = raw_mode or "ask"
        elif overwrite is None:
            mode_key = "ask"
        else:
            mode_key = "always" if bool(overwrite) else "never"
        if mode_key not in {"ask", "never", "always"}:
            raise raster_transform.RasterTransformError(
                code="raster_transform_invalid_argument",
                message="overwrite_mode must be one of: ask, never, always.",
                details={"overwrite_mode": overwrite_mode},
            )
        return mode_key, mode_key == "always"

    @staticmethod
    def _raise_output_exists_for_overwrite_mode(
        *,
        output_path: Path,
        overwrite_mode: str,
    ) -> None:
        if overwrite_mode == "ask":
            raise MapAlgebraError(
                code="map_algebra_overwrite_confirmation_required",
                message=f"Output file already exists and overwrite requires confirmation: {output_path}",
                details={
                    "output_path": str(output_path),
                    "overwrite_mode": "ask",
                    "resolution": "confirm_overwrite_or_choose_new_output_path",
                },
                status_code=409,
            )
        raise MapAlgebraError(
            code="map_algebra_output_exists",
            message=f"Output file already exists: {output_path}",
            details={"output_path": str(output_path), "overwrite_mode": overwrite_mode},
            status_code=409,
        )

    @staticmethod
    def _raise_raster_transform_output_exists_for_overwrite_mode(
        *,
        output_path: Path,
        overwrite_mode: str,
    ) -> None:
        if overwrite_mode == "ask":
            raise raster_transform.RasterTransformError(
                code="map_algebra_overwrite_confirmation_required",
                message=f"Output file already exists and overwrite requires confirmation: {output_path}",
                details={
                    "output_path": str(output_path),
                    "overwrite_mode": "ask",
                    "resolution": "confirm_overwrite_or_choose_new_output_path",
                },
                status_code=409,
            )
        raise raster_transform.RasterTransformError(
            code="raster_transform_output_exists",
            message=f"Output file already exists: {output_path}",
            details={"output_path": str(output_path), "overwrite_mode": overwrite_mode},
            status_code=409,
        )

    @staticmethod
    @contract(
        name="ToolImplementations.raster_calculate",
        request_type=RasterCalculateRequest,
        response_type=RasterCalculateResult,
        description="Evaluate a restricted map algebra DSL expression and persist a derived raster.",
        tool_name="raster.calculate",
        tool_title="raster calculate",
        tool_visibility=ToolVisibility.PUBLIC,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "raster", "analysis"),
    )
    def raster_calculate(
        scenario_id: str,
        expression: str,
        inputs: dict[str, Any],
        output_relative_path: str | None = None,
        overwrite_mode: str = "ask",
        resampling: str = "bilinear",
        time_start_utc: str | None = None,
        time_stop_utc: str | None = None,
        time_step_hours: float | None = None,
        horizons_relative_dir: str | None = None,
        observer_elevation_meters: float = 0.0,
        patch_width: int = 128,
        patch_height: int = 128,
        chunk_time_count: int = 256,
        buffer_count: int = 6,
        poll_timeout_ms: int = 250,
        use_spice_sun_vectors: bool = True,
        use_spice_earth_vectors: bool = True,
        publish_layer: dict[str, Any] | None = None,
        mode: JobMode = JobMode.QUEUED,
    ) -> RasterCalculateResult:
        _ = mode
        progress_events: list[dict[str, Any]] = []
        start_ts = time.monotonic()
        try:
            logger.info(
                "raster_calculate start scenario_id=%s output_relative_path=%s overwrite_mode=%s",
                scenario_id,
                str(output_relative_path or "").strip() or "<auto>",
                str(overwrite_mode or "").strip() or "ask",
            )
            resolved = resolve_scenario_paths(scenario_id)
            scenario_root = Path(resolved.scenario_root_dir).expanduser().resolve()
            dem_path = Path(resolved.dem_path).expanduser().resolve()
            if not scenario_root.exists() or not scenario_root.is_dir():
                raise MapAlgebraError(
                    code="map_algebra_input_not_found",
                    message=f"Scenario root directory not found: {scenario_root}",
                    status_code=404,
                )
            if not dem_path.exists() or not dem_path.is_file():
                raise MapAlgebraError(
                    code="map_algebra_input_not_found",
                    message=f"Scenario DEM not found: {dem_path}",
                    details={"scenario_id": scenario_id},
                    status_code=404,
                )

            configure_gdal_runtime()
            ToolImplementations._emit_map_algebra_progress(
                progress_events,
                percent=5.0,
                message="Validating map algebra expression.",
                stage="parse_validate",
            )
            resolved_inputs, input_refs, temporal_signals = ToolImplementations._resolve_raster_input_paths(
                scenario_id=scenario_id,
                scenario_root=scenario_root,
                raw_inputs=inputs,
            )
            parsed = parse_validate_expression(
                expression,
                allowed_variables=set(resolved_inputs.keys()) | set(temporal_signals.keys()),
            )
            expression_hash = compute_ast_hash(parsed.normalized_expression)
            logger.info(
                "raster_calculate parsed scenario_id=%s expression_hash=%s used_variables=%s",
                scenario_id,
                expression_hash[:12],
                sorted(parsed.used_variables),
            )
            used_static_inputs = sorted(
                name for name in parsed.used_variables if name in resolved_inputs
            )
            used_temporal_inputs = sorted(
                name for name in parsed.used_variables if name in temporal_signals
            )
            temporal_client: LightmapStreamingClient | None = None
            if used_temporal_inputs:
                # Bootstrap native runtime before importing rasterio/GDAL code paths.
                temporal_client = LightmapStreamingClient(
                    force_bootstrap=True,
                    verify_bridge_smoke=False,
                )

            _raise_if_cancelled()
            ToolImplementations._emit_map_algebra_progress(
                progress_events,
                percent=12.0,
                message="Loading scenario DEM grid.",
                stage="resolve_inputs",
            )
            logger.info("raster_calculate loading target grid scenario_id=%s dem_path=%s", scenario_id, dem_path)
            target_grid = load_target_grid_from_dem(dem_path)
            logger.info(
                "raster_calculate loaded target grid scenario_id=%s width=%s height=%s crs=%s",
                scenario_id,
                int(target_grid.width),
                int(target_grid.height),
                str(target_grid.crs),
            )
            aligned: dict[str, Any] = {}
            if used_static_inputs:
                logger.info(
                    "raster_calculate aligning inputs scenario_id=%s input_count=%s inputs=%s",
                    scenario_id,
                    len(used_static_inputs),
                    used_static_inputs,
                )
                aligned = align_inputs_to_target(
                    input_paths={name: resolved_inputs[name] for name in used_static_inputs},
                    target_grid=target_grid,
                    resampling_name=resampling,
                    on_progress=lambda payload: ToolImplementations._emit_map_algebra_progress(
                        progress_events,
                        percent=float(payload.get("percent", 20.0)),
                        message=str(payload.get("message", "Aligning inputs.")),
                        stage=str(payload.get("stage", "reproject_align")),
                        extra={k: v for k, v in payload.items() if k not in {"percent", "message", "stage"}},
                    ),
                    is_cancel_requested=is_job_cancel_requested,
                )
                logger.info(
                    "raster_calculate aligned inputs scenario_id=%s aligned_count=%s",
                    scenario_id,
                    len(aligned),
                )

            if output_relative_path is None or not str(output_relative_path).strip():
                digest_inputs: dict[str, Path] = {}
                for name in sorted(parsed.used_variables):
                    ref = input_refs.get(name, {})
                    token = str(
                        ref.get("relative_path")
                        or ref.get("product_id")
                        or ref.get("signal")
                        or "input"
                    )
                    safe = (
                        token.replace("/", "_")
                        .replace("\\", "_")
                        .replace(":", "_")
                        .replace(" ", "_")
                    )
                    digest_inputs[name] = Path(safe)
                if used_temporal_inputs:
                    time_token = (
                        f"{str(time_start_utc or '').strip()}_"
                        f"{str(time_stop_utc or '').strip()}_"
                        f"{time_step_hours if time_step_hours is not None else ''}"
                    )
                    safe_time = (
                        time_token.replace("/", "_")
                        .replace("\\", "_")
                        .replace(":", "_")
                        .replace(" ", "_")
                    )
                    digest_inputs["__time__"] = Path(safe_time)
                output_relative_path = ToolImplementations._default_map_algebra_output_relative_path(
                    expression=parsed.normalized_expression,
                    input_paths=digest_inputs,
                )
            rel_output = ToolImplementations._normalize_relative_path(str(output_relative_path))
            output_path = (scenario_root / rel_output).resolve()
            if scenario_root != output_path and scenario_root not in output_path.parents:
                raise MapAlgebraError(
                    code="map_algebra_output_path_invalid",
                    message="Output path escapes scenario root.",
                    details={"output_relative_path": rel_output},
                )

            output_dtype: str
            output_nodata: float | None
            size_bytes: int
            value_min: float | None = None
            value_max: float | None = None
            temporal_metadata: dict[str, Any] | None = None
            publish_options = RasterCalculatePublishLayerOptions.model_validate(publish_layer or {})
            published_layer_id: str | None = None
            published_layer_title: str | None = None
            published_layer_visible: bool | None = None

            def _coerce_published_mask_nodata(
                *,
                dtype_name: str | None,
                nodata_value: float | None,
            ) -> float | None:
                if not bool(publish_options.enabled):
                    return nodata_value
                if not bool(publish_options.transparent_background):
                    return nodata_value
                if str(parsed.semantic) != "mask":
                    return nodata_value
                if dtype_name is not None and str(dtype_name) != "uint8":
                    return nodata_value
                return float(0.0) if nodata_value is None else nodata_value

            overwrite_mode_key, overwrite_existing = ToolImplementations._resolve_overwrite_mode(
                overwrite_mode=overwrite_mode,
            )
            if output_path.exists() and not overwrite_existing:
                ToolImplementations._raise_output_exists_for_overwrite_mode(
                    output_path=output_path,
                    overwrite_mode=overwrite_mode_key,
                )

            if not used_temporal_inputs:
                _raise_if_cancelled()
                ToolImplementations._emit_map_algebra_progress(
                    progress_events,
                    percent=45.0,
                    message="Evaluating map algebra expression.",
                    stage="evaluate_tiles",
                )
                result = evaluate_expression(
                    parsed=parsed,
                    aligned_inputs=aligned,
                    target_grid=target_grid,
                )
                logger.info(
                    "raster_calculate evaluated scenario_id=%s expression_hash=%s result_ndim=%s",
                    scenario_id,
                    expression_hash[:12],
                    int(getattr(result, "ndim", 0)),
                )
                if result.ndim == 3:
                    if int(result.shape[0]) != 1:
                        raise MapAlgebraError(
                            code="map_algebra_temporal_reduce_required",
                            message=(
                                "Expression result is temporal [time, height, width]. "
                                "Use min/max/avg/std to reduce across time."
                            ),
                            details={"time_count": int(result.shape[0])},
                        )
                    result = np.asarray(result[0], dtype=np.float32)
                output_array, output_dtype, output_nodata = finalize_output_array(
                    result=result,
                    semantic=parsed.semantic,
                    used_variables=parsed.used_variables,
                    aligned_inputs=aligned,
                )
                output_nodata = _coerce_published_mask_nodata(
                    dtype_name=output_dtype,
                    nodata_value=output_nodata,
                )

                _raise_if_cancelled()
                ToolImplementations._emit_map_algebra_progress(
                    progress_events,
                    percent=70.0,
                    message="Writing output raster.",
                    stage="write_output",
                    extra={"output_relative_path": rel_output},
                )
                size_bytes = write_output_raster(
                    output_path=output_path,
                    target_grid=target_grid,
                    array=output_array,
                    nodata_value=output_nodata,
                    overwrite=overwrite_existing,
                )
                logger.info(
                    "raster_calculate wrote output scenario_id=%s output_relative_path=%s size_bytes=%s",
                    scenario_id,
                    rel_output,
                    int(size_bytes),
                )
                valid = (
                    output_array
                    if output_nodata is None
                    else output_array[np.asarray(output_array) != np.asarray(output_nodata)]
                )
                if isinstance(valid, np.ndarray) and valid.size > 0:
                    value_min = float(np.min(valid))
                    value_max = float(np.max(valid))
            else:
                start_utc = str(time_start_utc or "").strip()
                stop_utc = str(time_stop_utc or "").strip()
                if not start_utc or not stop_utc or time_step_hours is None:
                    raise MapAlgebraError(
                        code="map_algebra_temporal_time_range_required",
                        message=(
                            "Temporal inputs require time_start_utc, time_stop_utc, and "
                            "time_step_hours."
                        ),
                    )
                step_hours = float(time_step_hours)
                if step_hours <= 0.0:
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message="time_step_hours must be > 0.",
                    )
                horizons_dir = ToolImplementations._resolve_temporal_horizons_dir(
                    scenario_root=scenario_root,
                    horizons_relative_dir=horizons_relative_dir,
                )
                if not horizons_dir.exists() or not horizons_dir.is_dir():
                    raise MapAlgebraError(
                        code="map_algebra_temporal_horizons_not_found",
                        message=f"Horizons directory not found: {horizons_dir}",
                        status_code=404,
                    )
                output_path.parent.mkdir(parents=True, exist_ok=True)

                native_signals: list[str] = []
                for variable_name in used_temporal_inputs:
                    native = temporal_signals[variable_name]
                    if native not in native_signals:
                        native_signals.append(native)
                signal_specs = [TemporalSignalSpecPy(signal=name) for name in native_signals]

                request = LightmapStreamRequestV2Py(
                    scenario_root_dir=scenario_root,
                    dem_path=dem_path,
                    surrounding_dem_paths=[],
                    horizon_dir=horizons_dir,
                    start_utc=start_utc,
                    stop_utc=stop_utc,
                    time_step_hours=step_hours,
                    observer_elevation_meters=float(observer_elevation_meters),
                    patch_width=max(1, int(patch_width)),
                    patch_height=max(1, int(patch_height)),
                    max_read_parallelism=4,
                    max_compute_parallelism=24,
                    ready_queue_capacity=64,
                    use_spice_sun_vectors=bool(use_spice_sun_vectors),
                    mode="signal_stream",
                    signals=signal_specs,
                    chunk_time_count=max(1, int(chunk_time_count)),
                    reducers=None,
                    use_spice_earth_vectors=bool(use_spice_earth_vectors),
                )
                total_time_count = int(request.time_count())
                estimated_tile_bytes = (
                    total_time_count
                    * int(request.patch_height)
                    * int(request.patch_width)
                    * max(1, len(used_temporal_inputs))
                    * 4
                )
                if estimated_tile_bytes > 768 * 1024 * 1024:
                    raise MapAlgebraError(
                        code="map_algebra_temporal_axis_too_large",
                        message="Temporal request exceeds per-tile working-set limit.",
                        details={
                            "estimated_tile_bytes": int(estimated_tile_bytes),
                            "time_count": total_time_count,
                            "patch_width": int(request.patch_width),
                            "patch_height": int(request.patch_height),
                        },
                    )

                _raise_if_cancelled()
                ToolImplementations._emit_map_algebra_progress(
                    progress_events,
                    percent=45.0,
                    message="Streaming temporal signal chunks.",
                    stage="evaluate_tiles",
                )

                channel_index = {name: idx for idx, name in enumerate(native_signals)}
                tile_states: dict[tuple[int, int], dict[str, Any]] = {}
                output_dataset = None
                output_band = None
                output_dtype = ""
                output_nodata = None
                tiles_written = 0
                client = temporal_client or LightmapStreamingClient(
                    force_bootstrap=True,
                    verify_bridge_smoke=False,
                )
                np_dtype_map: dict[str, np.dtype[Any]] = {
                    "uint8": np.dtype(np.uint8),
                    "float32": np.dtype(np.float32),
                }
                try:
                    for tile_meta, tile_chunk in stream_tiles_v2(
                        client,
                        request,
                        buffer_count=max(1, int(buffer_count)),
                        poll_timeout_ms=max(1, int(poll_timeout_ms)),
                    ):
                        _raise_if_cancelled()
                        if int(tile_meta.rank) != 4 or int(getattr(tile_chunk, "ndim", 0)) != 4:
                            raise MapAlgebraError(
                                code="map_algebra_temporal_stream_failed",
                                message=(
                                    "Temporal signal stream returned an unexpected tensor rank."
                                ),
                                details={
                                    "rank": int(tile_meta.rank),
                                    "ndim": int(getattr(tile_chunk, "ndim", 0)),
                                },
                            )

                        tile_key = (int(tile_meta.patch_row), int(tile_meta.patch_col))
                        state = tile_states.get(tile_key)
                        if state is None:
                            state = {
                                "chunks": {name: [] for name in used_temporal_inputs},
                                "next_time_offset": 0,
                                "seen_time_count": 0,
                            }
                            tile_states[tile_key] = state
                        expected_time_offset = int(state["next_time_offset"])
                        if int(tile_meta.time_offset) != expected_time_offset:
                            raise MapAlgebraError(
                                code="map_algebra_temporal_stream_failed",
                                message="Temporal tile chunks arrived out of order.",
                                details={
                                    "tile_key": [tile_key[0], tile_key[1]],
                                    "expected_time_offset": expected_time_offset,
                                    "time_offset": int(tile_meta.time_offset),
                                },
                            )

                        tile_height = int(tile_meta.height)
                        tile_width = int(tile_meta.width)
                        chunk_time_count_local = int(tile_meta.time_count)
                        for variable_name in used_temporal_inputs:
                            native_signal = temporal_signals[variable_name]
                            chan = int(channel_index[native_signal])
                            chunk_view = np.asarray(
                                tile_chunk[:chunk_time_count_local, chan, :tile_height, :tile_width],
                                dtype=np.float32,
                            )
                            state["chunks"][variable_name].append(chunk_view)
                        state["next_time_offset"] = expected_time_offset + chunk_time_count_local
                        state["seen_time_count"] = int(state["seen_time_count"]) + chunk_time_count_local

                        if int(state["seen_time_count"]) < total_time_count:
                            continue
                        if int(state["seen_time_count"]) > total_time_count:
                            raise MapAlgebraError(
                                code="map_algebra_temporal_stream_failed",
                                message="Temporal stream produced too many samples for a tile.",
                                details={
                                    "tile_key": [tile_key[0], tile_key[1]],
                                    "seen_time_count": int(state["seen_time_count"]),
                                    "expected_time_count": total_time_count,
                                },
                            )

                        yoff, xoff = tile_key
                        variables: dict[str, Any] = {}
                        nodata_masks: dict[str, np.ndarray] = {}
                        for variable_name in used_static_inputs:
                            record = aligned[variable_name]
                            y1 = yoff + tile_height
                            x1 = xoff + tile_width
                            variables[variable_name] = np.asarray(
                                record.data[yoff:y1, xoff:x1],
                                dtype=np.float32,
                            )
                            nodata_masks[variable_name] = np.asarray(
                                record.nodata_mask[yoff:y1, xoff:x1],
                                dtype=bool,
                            )
                        for variable_name in used_temporal_inputs:
                            chunks = state["chunks"][variable_name]
                            temporal_cube = np.concatenate(chunks, axis=0)
                            if int(temporal_cube.shape[0]) != total_time_count:
                                raise MapAlgebraError(
                                    code="map_algebra_temporal_stream_failed",
                                    message="Temporal cube assembly failed.",
                                    details={
                                        "tile_key": [tile_key[0], tile_key[1]],
                                        "time_count": int(temporal_cube.shape[0]),
                                        "expected_time_count": total_time_count,
                                    },
                                )
                            variables[variable_name] = temporal_cube

                        tile_result = evaluate_expression_for_variables(
                            parsed=parsed,
                            variables=variables,
                            target_shape=(tile_height, tile_width),
                            transform=target_grid.transform,
                        )
                        if tile_result.ndim == 3:
                            if int(tile_result.shape[0]) != 1:
                                raise MapAlgebraError(
                                    code="map_algebra_temporal_reduce_required",
                                    message=(
                                        "Temporal expression result must be reduced with "
                                        "min/max/avg/std before writing a raster."
                                    ),
                                    details={
                                        "tile_key": [tile_key[0], tile_key[1]],
                                        "time_count": int(tile_result.shape[0]),
                                    },
                                )
                            tile_result = np.asarray(tile_result[0], dtype=np.float32)

                        tile_array, tile_dtype, tile_nodata = finalize_output_array(
                            result=np.asarray(tile_result),
                            semantic=parsed.semantic,
                            used_variables=parsed.used_variables,
                            nodata_masks=nodata_masks,
                        )

                        if output_dataset is None:
                            output_dtype = str(tile_dtype)
                            output_nodata = tile_nodata
                            output_nodata = _coerce_published_mask_nodata(
                                dtype_name=output_dtype,
                                nodata_value=output_nodata,
                            )
                            if output_dtype not in np_dtype_map:
                                raise MapAlgebraError(
                                    code="map_algebra_internal_error",
                                    message=f"Unsupported output dtype: {output_dtype}",
                                )
                            output_dataset = rasterio.open(
                                output_path,
                                "w",
                                driver="GTiff",
                                width=int(target_grid.width),
                                height=int(target_grid.height),
                                count=1,
                                dtype=output_dtype,
                                crs=target_grid.crs,
                                transform=target_grid.transform,
                                nodata=output_nodata,
                                tiled=True,
                                blockxsize=128,
                                blockysize=128,
                                compress="lzw",
                                bigtiff="IF_SAFER",
                            )
                            if output_nodata is not None:
                                output_dataset.write(
                                    np.full(
                                        (int(target_grid.height), int(target_grid.width)),
                                        output_nodata,
                                        dtype=np.dtype(output_dtype),
                                    ),
                                    1,
                                )

                        write_width = min(tile_width, int(target_grid.width) - xoff)
                        write_height = min(tile_height, int(target_grid.height) - yoff)
                        if write_width <= 0 or write_height <= 0:
                            del tile_states[tile_key]
                            continue
                        tile_window = np.asarray(
                            tile_array[:write_height, :write_width],
                            dtype=np_dtype_map[output_dtype],
                        )
                        if output_dataset is None:
                            raise MapAlgebraError(
                                code="map_algebra_internal_error",
                                message="Output raster dataset not initialized.",
                            )
                        window = (
                            (int(yoff), int(yoff) + int(write_height)),
                            (int(xoff), int(xoff) + int(write_width)),
                        )
                        output_dataset.write(tile_window, 1, window=window)

                        if output_nodata is None:
                            valid_values = tile_window
                        else:
                            valid_values = tile_window[tile_window != output_nodata]
                        if isinstance(valid_values, np.ndarray) and valid_values.size > 0:
                            local_min = float(np.min(valid_values))
                            local_max = float(np.max(valid_values))
                            value_min = local_min if value_min is None else min(value_min, local_min)
                            value_max = local_max if value_max is None else max(value_max, local_max)

                        tiles_written += 1
                        if tiles_written % 20 == 0:
                            ToolImplementations._emit_map_algebra_progress(
                                progress_events,
                                percent=min(84.0, 45.0 + (tiles_written / 5.0)),
                                message=f"Processed {tiles_written} temporal tile(s).",
                                stage="evaluate_tiles",
                            )
                        del tile_states[tile_key]
                    if tile_states:
                        raise MapAlgebraError(
                            code="map_algebra_temporal_stream_failed",
                            message="Temporal stream terminated before all tiles were finalized.",
                            details={"pending_tile_count": len(tile_states)},
                        )
                except MapAlgebraError:
                    raise
                except Exception as exc:
                    raise MapAlgebraError(
                        code="map_algebra_temporal_stream_failed",
                        message="Temporal signal streaming failed.",
                        details={"error": str(exc)},
                    ) from exc
                finally:
                    if output_dataset is not None:
                        try:
                            output_dataset.close()
                        except Exception:
                            pass
                    output_band = None
                    output_dataset = None

                if not output_dtype:
                    semantic_key = parsed.semantic if parsed.semantic in {"mask", "byte"} else "continuous"
                    output_dtype = "uint8" if semantic_key in {"mask", "byte"} else "float32"
                    output_nodata = None if output_dtype == "uint8" else float(-9999.0)
                    output_nodata = _coerce_published_mask_nodata(
                        dtype_name=output_dtype,
                        nodata_value=output_nodata,
                    )
                    empty_value = 0 if output_nodata is None else float(output_nodata)
                    empty_array = np.full(
                        (int(target_grid.height), int(target_grid.width)),
                        empty_value,
                        dtype=np.uint8 if output_dtype == "uint8" else np.float32,
                    )
                    size_bytes = write_output_raster(
                        output_path=output_path,
                        target_grid=target_grid,
                        array=empty_array,
                        nodata_value=output_nodata,
                        overwrite=overwrite_existing,
                    )
                else:
                    size_bytes = int(output_path.stat().st_size) if output_path.exists() else 0

                temporal_metadata = {
                    "time_start_utc": start_utc,
                    "time_stop_utc": stop_utc,
                    "time_step_hours": step_hours,
                    "horizons_relative_dir": horizons_dir.relative_to(scenario_root).as_posix(),
                    "signals": {
                        name: {
                            "alias": str(input_refs[name].get("signal", "")),
                            "native_signal": temporal_signals[name],
                        }
                        for name in used_temporal_inputs
                    },
                    "chunk_time_count": int(request.chunk_time_count),
                    "patch_width": int(request.patch_width),
                    "patch_height": int(request.patch_height),
                    "total_time_count": total_time_count,
                    "use_spice_sun_vectors": bool(use_spice_sun_vectors),
                    "use_spice_earth_vectors": bool(use_spice_earth_vectors),
                }

            lineage = {
                "source": "map_algebra",
                "expression": parsed.normalized_expression,
                "expression_ast_hash": expression_hash,
                "inputs": input_refs,
                "target_grid": {
                    "dem_path": dem_path.name,
                    "crs": str(target_grid.crs),
                    "width": int(target_grid.width),
                    "height": int(target_grid.height),
                    "transform": [float(v) for v in target_grid.transform],
                },
                "resampling": str(resampling),
                "used_variables": sorted(parsed.used_variables),
                "used_functions": sorted(parsed.used_functions),
                "used_operators": sorted(parsed.used_operators),
                "reprojected_inputs": sorted(
                    [name for name, record in aligned.items() if bool(record.reprojected)]
                ),
                "output_dtype": output_dtype,
                "output_nodata": output_nodata,
            }
            if temporal_metadata is not None:
                lineage["temporal"] = temporal_metadata
            if bool(publish_options.enabled):
                lineage["publish_layer"] = publish_options.model_dump(mode="json")

            _raise_if_cancelled()
            ToolImplementations._emit_map_algebra_progress(
                progress_events,
                percent=86.0,
                message="Registering raster artifact.",
                stage="register_artifact",
            )
            registered = register_generated_raster(
                scenario_id=scenario_id,
                relative_path=rel_output,
                lineage=lineage,
            )
            logger.info(
                "raster_calculate registered scenario_id=%s product_id=%s file_id=%s relative_path=%s",
                scenario_id,
                registered.product_id,
                registered.file_id,
                registered.relative_path,
            )
            register_artifact_output(
                scenario_root_dir=scenario_root,
                scenario_id=scenario_id,
                job_type="raster_calculate",
                artifact_kind="raster",
                artifact_path=output_path,
                size_bytes=size_bytes,
                metadata={
                    "product_id": registered.product_id,
                    "file_id": registered.file_id,
                    "expression_ast_hash": expression_hash,
                    "output_dtype": output_dtype,
                    "output_nodata": output_nodata,
                    "value_min": value_min,
                    "value_max": value_max,
                },
            )
            if bool(publish_options.enabled):
                ToolImplementations._emit_map_algebra_progress(
                    progress_events,
                    percent=92.0,
                    message="Publishing raster layer state.",
                    stage="register_artifact",
                )
                requested_title = str(publish_options.title or "").strip()
                default_title = Path(rel_output).stem
                try:
                    published = publish_generated_raster_layer(
                        scenario_id=scenario_id,
                        product_id=registered.product_id,
                        file_id=registered.file_id,
                        title=requested_title or default_title,
                        visible=bool(publish_options.visible),
                        opacity=float(publish_options.opacity),
                        z_index=None if publish_options.z_index is None else int(publish_options.z_index),
                        style=dict(publish_options.style or {}),
                        on_existing=str(publish_options.on_existing),
                    )
                except Exception as exc:
                    raise MapAlgebraError(
                        code="map_algebra_layer_publish_failed",
                        message="Raster output was created, but publishing layer state failed.",
                        details={
                            "error": str(exc),
                            "file_id": registered.file_id,
                            "product_id": registered.product_id,
                        },
                    ) from exc
                published_layer_id = str(published.layer_id)
                published_layer_title = str(published.title)
                published_layer_visible = bool(published.visible)

            ToolImplementations._emit_map_algebra_progress(
                progress_events,
                percent=100.0,
                message="Map algebra raster calculation completed.",
                stage="register_artifact",
            )
            return RasterCalculateResult(
                scenario_id=scenario_id,
                output_relative_path=registered.relative_path,
                output_path=str(output_path),
                product_id=registered.product_id,
                file_id=registered.file_id,
                output_dtype=output_dtype,
                output_nodata=output_nodata,
                target_crs=str(target_grid.crs),
                target_width=int(target_grid.width),
                target_height=int(target_grid.height),
                expression=parsed.normalized_expression,
                expression_ast_hash=expression_hash,
                used_variables=sorted(parsed.used_variables),
                used_functions=sorted(parsed.used_functions),
                used_operators=sorted(parsed.used_operators),
                reprojected_inputs=sorted(
                    [name for name, record in aligned.items() if bool(record.reprojected)]
                ),
                temporal_inputs=used_temporal_inputs,
                time_start_utc=None if temporal_metadata is None else str(temporal_metadata["time_start_utc"]),
                time_stop_utc=None if temporal_metadata is None else str(temporal_metadata["time_stop_utc"]),
                time_step_hours=None
                if temporal_metadata is None
                else float(temporal_metadata["time_step_hours"]),
                published_layer_id=published_layer_id,
                published_layer_title=published_layer_title,
                published_layer_visible=published_layer_visible,
                artifact_db_path=str((scenario_root / "scenario.db").resolve()),
                progress_events=progress_events,
            )
        except MapAlgebraError as error:
            logger.warning(
                "raster_calculate failed scenario_id=%s code=%s message=%s",
                scenario_id,
                error.code,
                error.message,
            )
            raise ToolImplementations._api_error_from_map_algebra(error) from error
        finally:
            elapsed_ms = max(0, int((time.monotonic() - start_ts) * 1000))
            logger.info("raster_calculate finished scenario_id=%s elapsed_ms=%s", scenario_id, elapsed_ms)

    @staticmethod
    @contract(
        name="ToolImplementations.raster_transform",
        request_type=RasterTransformRequest,
        response_type=RasterTransformResult,
        description="Evaluate a restricted raster transform script and persist a derived raster.",
        tool_name="raster.transform",
        tool_title="raster transform",
        tool_visibility=ToolVisibility.PUBLIC,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "raster", "analysis"),
    )
    def raster_transform(
        scenario_id: str,
        script: str,
        inputs: dict[str, Any],
        output_relative_path: str | None = None,
        overwrite_mode: str = "ask",
        overwrite: bool | None = None,
        mode: JobMode = JobMode.QUEUED,
        resampling: str = "bilinear",
        spatial_partitioning: str = "auto",
        time_partitioning: str = "auto",
        spatial_halo_pixels: int = 0,
        time_start_utc: str | None = None,
        time_stop_utc: str | None = None,
        time_step_hours: float | None = None,
        horizons_relative_dir: str | None = None,
        observer_elevation_meters: float = 0.0,
        patch_width: int = 128,
        patch_height: int = 128,
        chunk_time_count: int = 256,
        buffer_count: int = 6,
        poll_timeout_ms: int = 250,
        use_spice_sun_vectors: bool = True,
        use_spice_earth_vectors: bool = True,
    ) -> RasterTransformResult:
        _ = mode
        progress_events: list[dict[str, Any]] = []
        try:
            resolved = resolve_scenario_paths(scenario_id)
            scenario_root = Path(resolved.scenario_root_dir).expanduser().resolve()
            dem_path = Path(resolved.dem_path).expanduser().resolve()
            if not scenario_root.exists() or not scenario_root.is_dir():
                raise raster_transform.RasterTransformError(
                    code="raster_transform_input_not_found",
                    message=f"Scenario root directory not found: {scenario_root}",
                    status_code=404,
                )
            if not dem_path.exists() or not dem_path.is_file():
                raise raster_transform.RasterTransformError(
                    code="raster_transform_input_not_found",
                    message=f"Scenario DEM not found: {dem_path}",
                    details={"scenario_id": scenario_id},
                    status_code=404,
                )

            configure_gdal_runtime()
            ToolImplementations._emit_map_algebra_progress(
                progress_events,
                percent=5.0,
                message="Validating raster transform script.",
                stage="parse_validate",
            )
            resolved_inputs, input_refs, temporal_bindings, time_binding = (
                ToolImplementations._resolve_raster_transform_input_bindings(
                    scenario_id=scenario_id,
                    scenario_root=scenario_root,
                    raw_inputs=inputs,
                )
            )
            parsed = raster_transform.parse_validate_script(
                script,
                allowed_variables=set(resolved_inputs.keys()) | set(temporal_bindings.keys()),
            )
            script_hash = raster_transform.compute_script_hash(parsed.normalized_script)
            used_static_inputs = sorted(name for name in parsed.used_variables if name in resolved_inputs)
            used_temporal_inputs = sorted(name for name in parsed.used_variables if name in temporal_bindings)
            static_input_names = sorted(resolved_inputs.keys())
            temporal_input_names = sorted(temporal_bindings.keys())
            all_raster_input_names = set(static_input_names) | set(temporal_input_names)

            _raise_if_transform_cancelled()
            ToolImplementations._emit_map_algebra_progress(
                progress_events,
                percent=10.0,
                message="Building raster transform plan.",
                stage="build_plan",
            )
            target_grid = raster_transform.load_target_grid_from_dem(dem_path)

            start_utc, stop_utc, step_hours = ToolImplementations._resolve_raster_transform_time_domain(
                time_binding=time_binding,
                time_start_utc=time_start_utc,
                time_stop_utc=time_stop_utc,
                time_step_hours=time_step_hours,
                has_temporal_inputs=bool(temporal_input_names),
            )
            temporal_signals: dict[str, str] = {}
            available_patch_keys: set[tuple[int, int]] = set()
            missing_patch_keys: set[tuple[int, int]] = set()
            if temporal_input_names:
                if not start_utc or not stop_utc or step_hours is None:
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_temporal_time_range_required",
                        message=(
                            "Temporal inputs require a reserved 'times' binding or matching "
                            "time_start_utc/time_stop_utc/time_step_hours request fields."
                        ),
                    )
                if step_hours <= 0.0:
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_invalid_argument",
                        message="time_step_hours must be > 0.",
                    )
                try:
                    horizons_dir = ToolImplementations._resolve_temporal_horizons_dir(
                        scenario_root=scenario_root,
                        horizons_relative_dir=horizons_relative_dir,
                    )
                except MapAlgebraError as exc:
                    raise raster_transform._translate_map_error(exc) from exc
                if not horizons_dir.exists() or not horizons_dir.is_dir():
                    raise raster_transform.RasterTransformError(
                        code="raster_transform_temporal_horizons_not_found",
                        message=f"Horizons directory not found: {horizons_dir}",
                        status_code=404,
                    )
                expected_patch_keys = raster_transform.expected_patch_keys(
                    width=int(target_grid.width),
                    height=int(target_grid.height),
                    patch_width=int(patch_width),
                    patch_height=int(patch_height),
                )
                available_patch_keys = raster_transform.available_horizon_patch_keys(
                    horizons_dir=horizons_dir,
                    observer_elevation_meters=observer_elevation_meters,
                )
                missing_patch_keys = expected_patch_keys - available_patch_keys
                native_signals: list[str] = []
                for variable_name in temporal_input_names:
                    native = str(temporal_bindings[variable_name]["native_signal"])
                    temporal_signals[variable_name] = native
                    if native not in native_signals:
                        native_signals.append(native)
                request = ToolImplementations._build_raster_transform_temporal_request(
                    scenario_root=scenario_root,
                    dem_path=dem_path,
                    horizons_dir=horizons_dir,
                    time_start_utc=start_utc,
                    time_stop_utc=stop_utc,
                    time_step_hours=step_hours,
                    observer_elevation_meters=observer_elevation_meters,
                    patch_width=patch_width,
                    patch_height=patch_height,
                    chunk_time_count=chunk_time_count,
                    use_spice_sun_vectors=use_spice_sun_vectors,
                    use_spice_earth_vectors=use_spice_earth_vectors,
                    native_signals=native_signals,
                )
                total_time_count = int(request.time_count())
            else:
                horizons_dir = None
                request = None
                total_time_count = 1

            plan = raster_transform.build_plan(
                parsed=parsed,
                target_grid=target_grid,
                static_input_names=static_input_names,
                temporal_input_names=temporal_input_names,
                spatial_partitioning=spatial_partitioning,
                time_partitioning=time_partitioning,
                spatial_halo_pixels=spatial_halo_pixels,
                patch_width=patch_width,
                patch_height=patch_height,
                time_count=total_time_count,
            )
            ToolImplementations._emit_map_algebra_progress(
                progress_events,
                percent=15.0,
                message="Estimating working set.",
                stage="estimate_resources",
                extra=plan.model_dump(),
            )
            raster_transform.enforce_plan_limits(plan)

            _raise_if_transform_cancelled()
            ToolImplementations._emit_map_algebra_progress(
                progress_events,
                percent=20.0,
                message="Resolving and aligning raster inputs.",
                stage="resolve_inputs",
            )
            aligned: dict[str, Any] = {}
            if static_input_names:
                aligned = raster_transform.align_inputs_to_target(
                    input_paths={name: resolved_inputs[name] for name in static_input_names},
                    target_grid=target_grid,
                    resampling_name=resampling,
                    on_progress=lambda payload: ToolImplementations._emit_map_algebra_progress(
                        progress_events,
                        percent=float(payload.get("percent", 25.0)),
                        message=str(payload.get("message", "Aligning inputs.")),
                        stage=str(payload.get("stage", "reproject_align")),
                        extra={k: v for k, v in payload.items() if k not in {"percent", "message", "stage"}},
                    ),
                    is_cancel_requested=is_job_cancel_requested,
                )

            if output_relative_path is None or not str(output_relative_path).strip():
                digest_inputs: dict[str, Path] = {}
                for name in sorted(all_raster_input_names):
                    ref = input_refs.get(name, {})
                    token = str(
                        ref.get("relative_path")
                        or ref.get("product_id")
                        or ref.get("temporal_source")
                        or ref.get("signal")
                        or "input"
                    )
                    safe = token.replace("/", "_").replace("\\", "_").replace(":", "_").replace(" ", "_")
                    digest_inputs[name] = Path(safe)
                if temporal_input_names:
                    time_token = f"{start_utc}_{stop_utc}_{step_hours if step_hours is not None else ''}"
                    safe_time = time_token.replace("/", "_").replace("\\", "_").replace(":", "_").replace(" ", "_")
                    digest_inputs["__time__"] = Path(safe_time)
                output_relative_path = ToolImplementations._default_raster_transform_output_relative_path(
                    script=parsed.normalized_script,
                    input_paths=digest_inputs,
                )
            rel_output = ToolImplementations._normalize_relative_path(str(output_relative_path))
            output_path = (scenario_root / rel_output).resolve()
            if scenario_root != output_path and scenario_root not in output_path.parents:
                raise raster_transform.RasterTransformError(
                    code="raster_transform_output_path_invalid",
                    message="Output path escapes scenario root.",
                    details={"output_relative_path": rel_output},
                )
            overwrite_mode_key, overwrite_existing = ToolImplementations._resolve_raster_transform_overwrite_mode(
                overwrite_mode=overwrite_mode,
                overwrite=overwrite,
            )
            if output_path.exists() and not overwrite_existing:
                ToolImplementations._raise_raster_transform_output_exists_for_overwrite_mode(
                    output_path=output_path,
                    overwrite_mode=overwrite_mode_key,
                )

            output_dtype = ""
            output_nodata: float | None = None
            size_bytes = 0
            value_min: float | None = None
            value_max: float | None = None
            temporal_metadata: dict[str, Any] | None = None
            output_valid_mask: np.ndarray | None = None

            if not temporal_input_names:
                _raise_if_transform_cancelled()
                ToolImplementations._emit_map_algebra_progress(
                    progress_events,
                    percent=45.0,
                    message="Executing raster transform.",
                    stage="execute_transform",
                )
                variables = {
                    name: np.asarray(aligned[name].data, dtype=np.float32)
                    for name in static_input_names
                }
                result = raster_transform.execute_script(
                    parsed=parsed,
                    variables=variables,
                    target_shape=(int(target_grid.height), int(target_grid.width)),
                    transform=target_grid.transform,
                    metadata={"time_step_hours": step_hours} if step_hours is not None else {},
                )
                if result.ndim == 3:
                    if int(result.shape[0]) != 1:
                        raise raster_transform.RasterTransformError(
                            code="raster_transform_temporal_reduce_required",
                            message="Temporal transform result must be reduced before writing a raster.",
                            details={"time_count": int(result.shape[0])},
                        )
                    result = np.asarray(result[0], dtype=np.float32)
                semantic = raster_transform.infer_result_semantic(result)
                output_array, output_dtype, output_nodata, output_valid_mask = raster_transform.finalize_output_array(
                    result=result,
                    semantic=semantic,
                    used_variables=parsed.used_variables,
                    participating_variables=all_raster_input_names,
                    aligned_inputs=aligned,
                )
                _raise_if_transform_cancelled()
                ToolImplementations._emit_map_algebra_progress(
                    progress_events,
                    percent=72.0,
                    message="Writing output raster.",
                    stage="write_output",
                    extra={"output_relative_path": rel_output},
                )
                size_bytes = raster_transform.write_output_raster(
                    output_path=output_path,
                    target_grid=target_grid,
                    array=output_array,
                    nodata_value=output_nodata,
                    valid_mask=output_valid_mask,
                    overwrite=overwrite_existing,
                )
                value_min, value_max = raster_transform.compute_value_range(
                    output_array,
                    nodata_value=output_nodata,
                    valid_mask=output_valid_mask,
                )
            else:
                temporal_client = LightmapStreamingClient(
                    force_bootstrap=True,
                    verify_bridge_smoke=False,
                )
                assert request is not None
                ToolImplementations._emit_map_algebra_progress(
                    progress_events,
                    percent=45.0,
                    message="Executing temporal raster transform.",
                    stage="execute_transform",
                    extra={"execution_strategy": plan.execution_strategy},
                )
                if plan.execution_strategy == "full_extent_temporal":
                    temporal_variables, temporal_invalid_mask, missing_patch_count = (
                        ToolImplementations._load_raster_transform_full_extent_temporal_inputs(
                        client=temporal_client,
                        request=request,
                        temporal_input_names=temporal_input_names,
                        temporal_signals=temporal_signals,
                        target_grid=target_grid,
                        available_patch_keys=available_patch_keys,
                        missing_patch_keys=missing_patch_keys,
                        buffer_count=max(1, int(buffer_count)),
                        poll_timeout_ms=max(1, int(poll_timeout_ms)),
                        progress_events=progress_events,
                    ))
                    variables = {
                        name: np.asarray(aligned[name].data, dtype=np.float32)
                        for name in static_input_names
                    }
                    variables.update(temporal_variables)
                    result = raster_transform.execute_script(
                        parsed=parsed,
                        variables=variables,
                        target_shape=(int(target_grid.height), int(target_grid.width)),
                        transform=target_grid.transform,
                        metadata={"time_step_hours": step_hours},
                    )
                    if result.ndim == 3:
                        if int(result.shape[0]) != 1:
                            raise raster_transform.RasterTransformError(
                                code="raster_transform_temporal_reduce_required",
                                message="Temporal transform result must be reduced before writing a raster.",
                                details={"time_count": int(result.shape[0])},
                            )
                        result = np.asarray(result[0], dtype=np.float32)
                    semantic = raster_transform.infer_result_semantic(result)
                    temporal_nodata_masks = {
                        name: np.array(temporal_invalid_mask, copy=True)
                        for name in temporal_input_names
                    }
                    output_array, output_dtype, output_nodata, output_valid_mask = raster_transform.finalize_output_array(
                        result=result,
                        semantic=semantic,
                        used_variables=parsed.used_variables,
                        participating_variables=all_raster_input_names,
                        aligned_inputs=aligned,
                        nodata_masks=temporal_nodata_masks,
                    )
                    ToolImplementations._emit_map_algebra_progress(
                        progress_events,
                        percent=72.0,
                        message="Writing output raster.",
                        stage="write_output",
                        extra={"output_relative_path": rel_output},
                    )
                    size_bytes = raster_transform.write_output_raster(
                        output_path=output_path,
                        target_grid=target_grid,
                        array=output_array,
                        nodata_value=output_nodata,
                        valid_mask=output_valid_mask,
                        overwrite=overwrite_existing,
                    )
                    value_min, value_max = raster_transform.compute_value_range(
                        output_array,
                        nodata_value=output_nodata,
                        valid_mask=output_valid_mask,
                    )
                else:
                    (
                        output_dtype,
                        output_nodata,
                        output_valid_mask,
                        size_bytes,
                        value_min,
                        value_max,
                        missing_patch_count,
                    ) = (
                        ToolImplementations._execute_raster_transform_tiled_temporal(
                            client=temporal_client,
                            request=request,
                            parsed=parsed,
                            aligned=aligned,
                            static_input_names=static_input_names,
                            temporal_input_names=temporal_input_names,
                            temporal_signals=temporal_signals,
                            target_grid=target_grid,
                            all_raster_input_names=all_raster_input_names,
                            available_patch_keys=available_patch_keys,
                            missing_patch_keys=missing_patch_keys,
                            output_path=output_path,
                            overwrite=overwrite_existing,
                            progress_events=progress_events,
                            metadata={
                                "time_step_hours": step_hours,
                                "buffer_count": max(1, int(buffer_count)),
                                "poll_timeout_ms": max(1, int(poll_timeout_ms)),
                            },
                        )
                    )
                temporal_metadata = {
                    "time_start_utc": start_utc,
                    "time_stop_utc": stop_utc,
                    "time_step_hours": step_hours,
                    "horizons_relative_dir": horizons_dir.relative_to(scenario_root).as_posix() if horizons_dir is not None else None,
                    "signals": {
                        name: {
                            "alias": str(input_refs[name].get("signal", "")),
                            "temporal_source": str(input_refs[name].get("temporal_source", "")),
                            "native_signal": temporal_signals[name],
                            "times": str(input_refs[name].get("times", "times")),
                        }
                        for name in temporal_input_names
                    },
                    "chunk_time_count": int(chunk_time_count),
                    "patch_width": int(patch_width),
                    "patch_height": int(patch_height),
                    "total_time_count": int(total_time_count),
                    "use_spice_sun_vectors": bool(use_spice_sun_vectors),
                    "use_spice_earth_vectors": bool(use_spice_earth_vectors),
                    "execution_strategy": plan.execution_strategy,
                    "missing_horizon_patch_count": int(missing_patch_count),
                }

            lineage = {
                "source": "raster_transform",
                "script": parsed.normalized_script,
                "script_hash": script_hash,
                "inputs": input_refs,
                "input_binding_mode": "times_binding" if time_binding is not None else "legacy_time_fields",
                "target_grid": {
                    "dem_path": dem_path.name,
                    "crs": str(target_grid.crs),
                    "width": int(target_grid.width),
                    "height": int(target_grid.height),
                    "transform": [float(v) for v in target_grid.transform],
                },
                "resampling": str(resampling),
                "used_variables": sorted(parsed.used_variables),
                "used_functions": sorted(parsed.used_functions),
                "used_operators": sorted(parsed.used_operators),
                "reprojected_inputs": sorted(
                    [name for name, record in aligned.items() if bool(record.reprojected)]
                ),
                "planner_summary": plan.model_dump(),
                "output_dtype": output_dtype,
                "output_nodata": output_nodata,
                "bound_static_inputs": static_input_names,
                "bound_temporal_inputs": temporal_input_names,
            }
            if temporal_metadata is not None:
                lineage["temporal"] = temporal_metadata

            _raise_if_transform_cancelled()
            ToolImplementations._emit_map_algebra_progress(
                progress_events,
                percent=88.0,
                message="Registering raster transform artifact.",
                stage="register_artifact",
            )
            registered = register_generated_raster(
                scenario_id=scenario_id,
                relative_path=rel_output,
                lineage=lineage,
            )
            register_artifact_output(
                scenario_root_dir=scenario_root,
                scenario_id=scenario_id,
                job_type="raster_transform",
                artifact_kind="raster",
                artifact_path=output_path,
                size_bytes=size_bytes,
                metadata={
                    "product_id": registered.product_id,
                    "file_id": registered.file_id,
                    "script_hash": script_hash,
                    "output_dtype": output_dtype,
                    "output_nodata": output_nodata,
                    "value_min": value_min,
                    "value_max": value_max,
                },
            )
            ToolImplementations._emit_map_algebra_progress(
                progress_events,
                percent=100.0,
                message="Raster transform completed.",
                stage="register_artifact",
            )
            return RasterTransformResult(
                scenario_id=scenario_id,
                output_relative_path=registered.relative_path,
                output_path=str(output_path),
                product_id=registered.product_id,
                file_id=registered.file_id,
                output_dtype=output_dtype,
                output_nodata=output_nodata,
                target_crs=str(target_grid.crs),
                target_width=int(target_grid.width),
                target_height=int(target_grid.height),
                script=parsed.normalized_script,
                script_hash=script_hash,
                used_variables=sorted(parsed.used_variables),
                used_functions=sorted(parsed.used_functions),
                used_operators=sorted(parsed.used_operators),
                reprojected_inputs=sorted(
                    [name for name, record in aligned.items() if bool(record.reprojected)]
                ),
                temporal_inputs=temporal_input_names,
                time_start_utc=start_utc or None,
                time_stop_utc=stop_utc or None,
                time_step_hours=step_hours,
                planner_summary=plan.model_dump(),
                artifact_db_path=str((scenario_root / "scenario.db").resolve()),
                progress_events=progress_events,
            )
        except raster_transform.RasterTransformError as error:
            raise ToolImplementations._api_error_from_raster_transform(error) from error

    @staticmethod
    @contract(
        name="ToolImplementations.export_colormap_rgba_geotiff",
        response_type=ExportColormapRgbaGeoTiffResult,
        description="Apply a raster style colormap/contour definition and export a COG-compatible RGBA GeoTIFF.",
        tool_visibility=ToolVisibility.PUBLIC,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "raster", "colormap", "export"),
    )
    def export_colormap_rgba_geotiff(
        scenario_id: str,
        source_relative_path: str,
        style: dict[str, Any] | None = None,
        output_relative_path: str | None = None,
        overwrite_mode: Literal["ask", "never", "always"] = "ask",
        mode: JobMode = JobMode.QUEUED,
    ) -> ExportColormapRgbaGeoTiffResult:
        _ = mode
        paths = resolve_scenario_paths(scenario_id)
        scenario_root = Path(paths.scenario_root_dir).expanduser().resolve()
        rel_source = ToolImplementations._normalize_relative_path(source_relative_path)
        source_path = (scenario_root / rel_source).resolve()
        if scenario_root != source_path and scenario_root not in source_path.parents:
            raise ApiError(
                status_code=422,
                code="export_colormap_source_path_invalid",
                message="source_relative_path escapes scenario root.",
                details={"source_relative_path": rel_source},
            )
        if not source_path.exists() or not source_path.is_file():
            raise ApiError(
                status_code=404,
                code="export_colormap_source_not_found",
                message="Source raster file was not found.",
                details={"source_relative_path": rel_source},
            )
        if source_path.suffix.lower() not in {".tif", ".tiff"}:
            raise ApiError(
                status_code=422,
                code="export_colormap_source_not_geotiff",
                message="Source file must be a GeoTIFF.",
                details={"source_relative_path": rel_source},
            )

        if output_relative_path is None or not str(output_relative_path).strip():
            output_relative_path = ToolImplementations._default_colormap_rgba_output_relative_path(rel_source)
        rel_output = ToolImplementations._normalize_relative_path(str(output_relative_path))
        output_path = (scenario_root / rel_output).resolve()
        if scenario_root != output_path and scenario_root not in output_path.parents:
            raise ApiError(
                status_code=422,
                code="export_colormap_output_path_invalid",
                message="output_relative_path escapes scenario root.",
                details={"output_relative_path": rel_output},
            )
        mode_key, overwrite_existing = ToolImplementations._resolve_overwrite_mode(overwrite_mode=overwrite_mode)
        if output_path.exists() and not overwrite_existing:
            try:
                ToolImplementations._raise_output_exists_for_overwrite_mode(
                    output_path=output_path,
                    overwrite_mode=mode_key,
                )
            except MapAlgebraError as error:
                raise ApiError(
                    status_code=int(error.status_code),
                    code=error.code.replace("map_algebra", "export_colormap"),
                    message=error.message,
                    details=dict(error.details),
                ) from error
        output_path.parent.mkdir(parents=True, exist_ok=True)

        app_cfg = load_app_config()
        backend_cfg = app_cfg.get("backend", {}) if isinstance(app_cfg, dict) else {}
        map_cfg = backend_cfg.get("lunar_analyst", {}) if isinstance(backend_cfg, dict) else {}
        if not isinstance(map_cfg, dict):
            map_cfg = {}
        registry = resolve_colormap_registry(
            repo_root=Path(__file__).resolve().parents[2],
            config_path=resolve_config_path(),
            map_cfg=map_cfg,
            scenario_root=scenario_root,
        )
        cmap_by_id = {
            str(item.get("id", "")).strip(): item
            for item in list(registry.get("colormaps", []))
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }

        style_payload = dict(style or {})
        style_mode = str(style_payload.get("style_mode", "colormap")).strip().lower() or "colormap"
        selected_colormap = str(style_payload.get("colormap", "")).strip()
        matched_rule: str | None = None
        if style_mode != "contour":
            if not selected_colormap:
                selected_colormap, matched_rule = resolve_default_colormap_for_name(
                    file_name=source_path.name,
                    colormaps=list(registry.get("colormaps", [])),
                    rules=list(registry.get("rules", [])),
                    fallback_default=str(registry.get("default", "gray")),
                )
            if selected_colormap not in cmap_by_id:
                selected_colormap = str(registry.get("default", "gray"))
            if selected_colormap not in cmap_by_id:
                raise ApiError(
                    status_code=422,
                    code="export_colormap_colormap_not_found",
                    message=f"Unknown colormap id: {selected_colormap}",
                )
            colormap = cmap_by_id[selected_colormap]
        else:
            colormap = None

        output_tmp = output_path.with_name(f".{output_path.stem}.tmp.tif")
        if output_tmp.exists():
            output_tmp.unlink()
        try:
            with rasterio.open(source_path) as src:
                band1 = src.read(1, masked=False).astype(np.float32)
                nodata = src.nodata
                valid_mask = np.isfinite(band1)
                if nodata is not None and np.isfinite(float(nodata)):
                    valid_mask &= band1 != float(nodata)

                raw_value_min = style_payload.get("valueMin")
                raw_value_max = style_payload.get("valueMax")
                try:
                    value_min = float(raw_value_min) if raw_value_min is not None else np.nan
                except (TypeError, ValueError):
                    value_min = np.nan
                try:
                    value_max = float(raw_value_max) if raw_value_max is not None else np.nan
                except (TypeError, ValueError):
                    value_max = np.nan
                if not np.isfinite(value_min) or not np.isfinite(value_max) or value_max <= value_min:
                    if np.any(valid_mask):
                        values = band1[valid_mask]
                        value_min = float(np.nanmin(values))
                        value_max = float(np.nanmax(values))
                        if not np.isfinite(value_min) or not np.isfinite(value_max) or value_max <= value_min:
                            value_min = 0.0
                            value_max = 1.0
                    else:
                        value_min = 0.0
                        value_max = 1.0

                if style_mode == "contour":
                    contour_cfg = style_payload.get("contour") if isinstance(style_payload.get("contour"), dict) else {}
                    interval = float((contour_cfg or {}).get("interval", 1.0))
                    offset = float((contour_cfg or {}).get("offset", 0.0))
                    line_color_raw = (contour_cfg or {}).get("line_color", [255, 255, 255, 1.0])
                    if not isinstance(line_color_raw, list) or len(line_color_raw) < 4:
                        line_color_raw = [255, 255, 255, 1.0]
                    line_color = [
                        max(0.0, min(255.0, float(line_color_raw[0]))),
                        max(0.0, min(255.0, float(line_color_raw[1]))),
                        max(0.0, min(255.0, float(line_color_raw[2]))),
                        max(0.0, min(1.0, float(line_color_raw[3]))),
                    ]
                    line_width_value = float((contour_cfg or {}).get("line_width_value", interval * 0.02))
                    rgba = contour_rgba(
                        raw_values=band1,
                        interval=interval,
                        offset=offset,
                        line_color=line_color,
                        line_width_value=line_width_value,
                    )
                else:
                    rgba = sample_colormap_rgba(
                        colormap=colormap or {},
                        raw_values=band1,
                        value_min=value_min,
                        value_max=value_max,
                    )
                    rgb01 = rgba[..., :3] / 255.0
                    brightness = float(style_payload.get("brightness", 0.0))
                    contrast = float(style_payload.get("contrast", 1.0))
                    rgba[..., :3] = tone_map_rgb(rgb01, brightness, contrast) * 255.0
                rgba[~valid_mask, 3] = 0.0

                profile = src.profile.copy()
                profile.update(
                    {
                        "driver": "GTiff",
                        "count": 4,
                        "dtype": "uint8",
                        "nodata": None,
                        "tiled": True,
                        "compress": "deflate",
                        "predictor": 2,
                        "blockxsize": 256,
                        "blockysize": 256,
                        "bigtiff": "IF_SAFER",
                    }
                )
                with rasterio.open(output_tmp, "w", **profile) as out:
                    out.write(np.clip(rgba[..., 0], 0, 255).astype(np.uint8), 1)
                    out.write(np.clip(rgba[..., 1], 0, 255).astype(np.uint8), 2)
                    out.write(np.clip(rgba[..., 2], 0, 255).astype(np.uint8), 3)
                    out.write(np.clip(rgba[..., 3] * 255.0, 0, 255).astype(np.uint8), 4)
                    out.set_band_description(1, "red")
                    out.set_band_description(2, "green")
                    out.set_band_description(3, "blue")
                    out.set_band_description(4, "alpha")

            convert_geotiff_to_cog(output_tmp, output_path)
        finally:
            if output_tmp.exists():
                try:
                    output_tmp.unlink()
                except OSError:
                    pass

        lineage = {
            "source": "export_colormap_rgba_geotiff",
            "source_relative_path": rel_source,
            "style_mode": style_mode,
            "colormap_id": selected_colormap if style_mode != "contour" else None,
            "style": style_payload,
            "matched_default_rule": matched_rule,
            "output_dtype": "uint8",
            "output_bands": 4,
        }
        registered = register_generated_raster(
            scenario_id=scenario_id,
            relative_path=rel_output,
            lineage=lineage,
        )
        size_bytes = int(output_path.stat().st_size) if output_path.exists() else 0
        register_artifact_output(
            scenario_root_dir=scenario_root,
            scenario_id=scenario_id,
            job_type="export_colormap_rgba_geotiff",
            artifact_kind="raster",
            artifact_path=output_path,
            size_bytes=size_bytes,
            metadata={
                "product_id": registered.product_id,
                "file_id": registered.file_id,
                "source_relative_path": rel_source,
                "style_mode": style_mode,
                "colormap_id": selected_colormap if style_mode != "contour" else None,
            },
        )
        return ExportColormapRgbaGeoTiffResult(
            scenario_id=scenario_id,
            source_relative_path=rel_source,
            output_relative_path=registered.relative_path,
            output_path=str(output_path),
            product_id=registered.product_id,
            file_id=registered.file_id,
            colormap_id=selected_colormap if style_mode != "contour" else None,
            style_mode=style_mode,
            output_dtype="uint8",
            output_band_count=4,
            artifact_db_path=str((scenario_root / "scenario.db").resolve()),
        )

    @staticmethod
    def _resolve_lightmap_reduction_paths(
        *,
        scenario_id: str,
        scenario_root_dir: str | None,
        dem_path: str,
        horizons_dir: str,
        output_path: str,
    ) -> tuple[Path, Path, Path, Path]:
        if scenario_root_dir:
            scenario_root = Path(scenario_root_dir).expanduser().resolve()
        else:
            paths = resolve_scenario_paths(scenario_id)
            scenario_root = Path(paths.scenario_root_dir).expanduser().resolve()
        dem = Path(dem_path).expanduser().resolve()
        horizons = Path(horizons_dir).expanduser().resolve()
        output = Path(output_path).expanduser().resolve()

        if not scenario_root.exists() or not scenario_root.is_dir():
            raise FileNotFoundError(f"Scenario root directory does not exist: {scenario_root}")
        if not dem.exists() or not dem.is_file():
            raise FileNotFoundError(f"DEM file does not exist or is not a file: {dem}")
        if not horizons.exists() or not horizons.is_dir():
            raise FileNotFoundError(f"Horizons directory does not exist: {horizons}")
        if output.exists() and output.is_dir():
            raise IsADirectoryError(f"Output path must be a file path, not a directory: {output}")

        output.parent.mkdir(parents=True, exist_ok=True)
        return scenario_root, dem, horizons, output

    @staticmethod
    def _run_native_psr_mapops(
        *,
        scenario_root: Path,
        dem: Path,
        horizons: Path,
        output: Path,
    ) -> None:
        if is_job_cancel_requested():
            raise RuntimeError("PSR raster generation canceled.")

        moonlib = import_moonlib()
        bridge = moonlib.MoonlibBridge()
        surrounding_dems = _to_dotnet_string_list_or_python_list([])

        def _emit_psr_progress(progress: Any) -> None:
            def _get(name: str, default: Any = None) -> Any:
                return getattr(progress, name, default)

            payload: dict[str, Any] = {
                "message": str(_get("Message", "PSR native execution progress.")),
            }
            percent = _get("Percent")
            if percent is not None:
                try:
                    payload["percent"] = float(percent)
                except (TypeError, ValueError):
                    pass
            stage = _get("Stage")
            if stage is not None:
                payload["stage"] = str(stage)
            emit_job_progress(payload)

        def _is_cancel_requested() -> bool:
            return bool(is_job_cancel_requested())

        try:
            bridge.GeneratePermanentShadowMap(
                str(scenario_root),
                str(dem),
                surrounding_dems,
                str(horizons),
                str(output),
                _emit_psr_progress,
                _is_cancel_requested,
            )
        except TypeError:
            bridge.GeneratePermanentShadowMap(
                str(scenario_root),
                str(dem),
                surrounding_dems,
                str(horizons),
                str(output),
            )

        if is_job_cancel_requested():
            raise RuntimeError("PSR raster generation canceled.")

    @staticmethod
    def _run_native_reduce_lightmap_raster(
        *,
        scenario_id: str,
        scenario_root_dir: str | None,
        dem_path: str,
        horizons_dir: str,
        output_path: str,
        time_start_utc: str,
        time_stop_utc: str,
        time_step_hours: float,
        reducers: list[dict[str, Any]],
        reducer_kind: str,
        observer_elevation_meters: float = 0.0,
        patch_width: int = 128,
        patch_height: int = 128,
        max_read_parallelism: int = 4,
        max_compute_parallelism: int = 24,
        ready_queue_capacity: int = 64,
        buffer_count: int = 6,
        poll_timeout_ms: int = 250,
        use_spice_sun_vectors: bool = True,
        use_spice_earth_vectors: bool = True,
        output_dtype: str = "float32",
        output_band_index: int = 1,
        nodata_value: float = -9999.0,
        status_poll_interval_seconds: float = 1.0,
    ) -> GenerateLightmapReductionRasterResult:
        if not reducers:
            raise ValueError("reducers must be non-empty.")
        if float(time_step_hours) <= 0:
            raise ValueError("time_step_hours must be > 0.")

        scenario_root, dem, horizons, output = ToolImplementations._resolve_lightmap_reduction_paths(
            scenario_id=scenario_id,
            scenario_root_dir=scenario_root_dir,
            dem_path=dem_path,
            horizons_dir=horizons_dir,
            output_path=output_path,
        )

        # Bootstrap native runtime before importing Python GDAL to avoid strict
        # DLL resolver conflicts (for example proj_9.dll loaded from a different root).
        client = LightmapStreamingClient(force_bootstrap=True, verify_bridge_smoke=False)

        configure_gdal_runtime()
        from osgeo import gdal

        gdal.UseExceptions()
        dtype_name = str(output_dtype).strip().lower()
        gdal_dtype_map = {
            "uint8": int(gdal.GDT_Byte),
            "int16": int(gdal.GDT_Int16),
            "uint16": int(gdal.GDT_UInt16),
            "int32": int(gdal.GDT_Int32),
            "uint32": int(gdal.GDT_UInt32),
            "float32": int(gdal.GDT_Float32),
            "float64": int(gdal.GDT_Float64),
        }
        np_dtype_map: dict[str, np.dtype[Any]] = {
            "uint8": np.dtype(np.uint8),
            "int16": np.dtype(np.int16),
            "uint16": np.dtype(np.uint16),
            "int32": np.dtype(np.int32),
            "uint32": np.dtype(np.uint32),
            "float32": np.dtype(np.float32),
            "float64": np.dtype(np.float64),
        }
        if dtype_name not in gdal_dtype_map:
            raise ValueError(f"Unsupported output_dtype={output_dtype!r}")

        dem_ds = gdal.Open(str(dem), gdal.GA_ReadOnly)
        if dem_ds is None:
            raise RuntimeError(f"Failed to open DEM: {dem}")
        width = int(dem_ds.RasterXSize)
        height = int(dem_ds.RasterYSize)
        total_tiles = max(
            1,
            int(math.ceil(width / max(1, int(patch_width))))
            * int(math.ceil(height / max(1, int(patch_height)))),
        )
        projection = dem_ds.GetProjection() or ""
        geotransform = dem_ds.GetGeoTransform(can_return_null=True)
        if geotransform is None:
            raise RuntimeError("Primary DEM has no geotransform.")

        driver = gdal.GetDriverByName("GTiff")
        if driver is None:
            raise RuntimeError("GDAL GTiff driver not available.")
        band_count = max(1, len(reducers))
        out_ds = driver.Create(
            str(output),
            width,
            height,
            band_count,
            gdal_dtype_map[dtype_name],
            options=[
                "TILED=YES",
                "BLOCKXSIZE=128",
                "BLOCKYSIZE=128",
                "COMPRESS=LZW",
                "BIGTIFF=IF_SAFER",
            ],
        )
        if out_ds is None:
            raise RuntimeError(f"Failed to create output GeoTIFF: {output}")
        out_ds.SetProjection(projection)
        out_ds.SetGeoTransform(geotransform)

        out_bands: list[Any] = []
        for idx in range(1, band_count + 1):
            band = out_ds.GetRasterBand(idx)
            if band is None:
                raise RuntimeError(f"Failed to access output raster band {idx}.")
            band.SetNoDataValue(float(nodata_value))
            band.Fill(float(nodata_value))
            out_bands.append(band)

        request = LightmapStreamRequestV2Py(
            scenario_root_dir=scenario_root,
            dem_path=dem,
            surrounding_dem_paths=[],
            horizon_dir=horizons,
            start_utc=str(time_start_utc),
            stop_utc=str(time_stop_utc),
            time_step_hours=float(time_step_hours),
            observer_elevation_meters=float(observer_elevation_meters),
            patch_width=int(patch_width),
            patch_height=int(patch_height),
            max_read_parallelism=int(max_read_parallelism),
            max_compute_parallelism=int(max_compute_parallelism),
            ready_queue_capacity=int(ready_queue_capacity),
            use_spice_sun_vectors=bool(use_spice_sun_vectors),
            mode="native_reduce",
            reducers=reducers,
            use_spice_earth_vectors=bool(use_spice_earth_vectors),
        )

        selected_band_index = int(output_band_index)
        if selected_band_index < 1 or selected_band_index > band_count:
            raise ValueError("output_band_index must be within reducer band count.")
        np_dtype = np_dtype_map[dtype_name]
        tiles_written = 0
        value_min: float | None = None
        value_max: float | None = None
        last_status_poll = 0.0

        _emit_lightmap_tile_progress(
            stage="native_reduce_stream_start",
            message="Starting native lightmap reduction stream.",
            tiles_written=0,
            total_tiles=total_tiles,
        )

        try:
            for tile_meta, tile_reduced in stream_tiles_v2(
                client,
                request,
                buffer_count=max(1, int(buffer_count)),
                poll_timeout_ms=max(1, int(poll_timeout_ms)),
            ):
                if is_job_cancel_requested():
                    try:
                        native_job_id = str(getattr(tile_meta, "job_id", "") or "")
                        if native_job_id:
                            client.cancel(native_job_id)
                    except Exception:
                        pass
                    raise RuntimeError("Lightmap native reduction canceled.")
                if tile_meta.rank != 3:
                    raise ValueError(f"NativeReduce expected rank=3 tiles, got rank={tile_meta.rank}.")
                if tile_reduced.ndim != 3:
                    raise ValueError("NativeReduce expected ndarray shape [channel, height, width].")

                xoff = int(tile_meta.patch_col)
                yoff = int(tile_meta.patch_row)
                tw = int(tile_meta.width)
                th = int(tile_meta.height)
                write_w = min(tw, width - xoff)
                write_h = min(th, height - yoff)
                if write_w <= 0 or write_h <= 0:
                    continue

                for band_i, band in enumerate(out_bands):
                    arr = tile_reduced[band_i, :write_h, :write_w]
                    if arr.dtype != np_dtype:
                        arr = arr.astype(np_dtype, copy=False)
                    if not arr.flags["C_CONTIGUOUS"]:
                        arr = np.ascontiguousarray(arr)
                    band.WriteArray(arr, xoff=xoff, yoff=yoff)

                    if band_i + 1 == selected_band_index:
                        local_min = float(arr.min())
                        local_max = float(arr.max())
                        value_min = local_min if value_min is None else min(value_min, local_min)
                        value_max = local_max if value_max is None else max(value_max, local_max)

                tiles_written += 1
                status = None
                now_monotonic = time.monotonic()
                if (
                    float(status_poll_interval_seconds) > 0
                    and now_monotonic - last_status_poll >= float(status_poll_interval_seconds)
                ):
                    last_status_poll = now_monotonic
                    native_job_id = str(getattr(tile_meta, "job_id", "") or "")
                    if native_job_id:
                        try:
                            status = client.get_status(native_job_id)
                        except Exception:
                            status = None
                _emit_lightmap_tile_progress(
                    stage="native_reduce_tiles",
                    message=f"Processed {tiles_written}/{total_tiles} native lightmap tile(s).",
                    tiles_written=tiles_written,
                    total_tiles=total_tiles,
                    tile_meta=tile_meta,
                    status=status,
                )
        finally:
            for band in out_bands:
                try:
                    band.FlushCache()
                except Exception:
                    pass
            try:
                out_ds.FlushCache()
            except Exception:
                pass
            dem_ds = None
            out_ds = None

        _emit_lightmap_tile_progress(
            stage="native_reduce_complete",
            message="Native lightmap reduction completed.",
            tiles_written=tiles_written,
            total_tiles=total_tiles,
        )

        size_bytes = int(output.stat().st_size) if output.exists() else 0
        register_artifact_output(
            scenario_root_dir=scenario_root,
            scenario_id=scenario_id,
            job_type=reducer_kind,
            artifact_kind="raster",
            artifact_path=output,
            size_bytes=size_bytes,
            metadata={
                "dem_path": str(dem),
                "horizons_dir": str(horizons),
                "time_start_utc": str(time_start_utc),
                "time_stop_utc": str(time_stop_utc),
                "time_step_hours": float(time_step_hours),
                "reducers": reducers,
                "output_band_index": selected_band_index,
                "output_dtype": dtype_name,
                "tiles_written": tiles_written,
                "value_min": value_min,
                "value_max": value_max,
            },
        )

        return GenerateLightmapReductionRasterResult(
            scenario_id=scenario_id,
            scenario_root_dir=str(scenario_root),
            dem_path=str(dem),
            horizons_dir=str(horizons),
            output_path=str(output),
            time_start_utc=str(time_start_utc),
            time_stop_utc=str(time_stop_utc),
            time_step_hours=float(time_step_hours),
            reducer_kind=reducer_kind,
            tiles_written=tiles_written,
            value_min=value_min,
            value_max=value_max,
            artifact_db_path=str((scenario_root / "scenario.db").resolve()),
        )

    @staticmethod
    @contract(
        name="ToolImplementations.generate_average_sun_fraction_raster",
        response_type=GenerateLightmapReductionRasterResult,
        description="Generate a raster of average sun fraction using native temporal reduction.",
        tool_visibility=ToolVisibility.PUBLIC,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "lighting", "reduction", "worker-only"),
    )
    def generate_average_sun_fraction_raster(
        scenario_id: str,
        dem_path: str,
        horizons_dir: str,
        output_path: str,
        time_start_utc: str,
        time_stop_utc: str,
        time_step_hours: float,
        scenario_root_dir: str | None = None,
        observer_elevation_meters: float = 0.0,
        output_normalized_01: bool = True,
        use_spice_sun_vectors: bool = True,
        mode: JobMode = JobMode.QUEUED,
    ) -> GenerateLightmapReductionRasterResult:
        _ = mode
        return ToolImplementations._run_native_reduce_lightmap_raster(
            scenario_id=scenario_id,
            scenario_root_dir=scenario_root_dir,
            dem_path=dem_path,
            horizons_dir=horizons_dir,
            output_path=output_path,
            time_start_utc=time_start_utc,
            time_stop_utc=time_stop_utc,
            time_step_hours=time_step_hours,
            reducers=[
                {
                    "kind": "average_sun_fraction",
                    "output_normalized_01": bool(output_normalized_01),
                    "output_type": "float32",
                }
            ],
            reducer_kind="generate_average_sun_fraction_raster",
            observer_elevation_meters=observer_elevation_meters,
            use_spice_sun_vectors=use_spice_sun_vectors,
            output_band_index=1,
        )

    @staticmethod
    @contract(
        name="ToolImplementations.generate_earth_above_terrain_duration_raster",
        response_type=GenerateLightmapReductionRasterResult,
        description="Generate cumulative duration raster where Earth is above terrain threshold.",
        tool_visibility=ToolVisibility.PUBLIC,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "lighting", "reduction", "worker-only"),
    )
    def generate_earth_above_terrain_duration_raster(
        scenario_id: str,
        dem_path: str,
        horizons_dir: str,
        output_path: str,
        time_start_utc: str,
        time_stop_utc: str,
        time_step_hours: float,
        threshold_deg: float = 0.0,
        threshold_reference: str = "center_margin",
        duration_unit: str = "hours",
        scenario_root_dir: str | None = None,
        observer_elevation_meters: float = 0.0,
        use_spice_sun_vectors: bool = True,
        use_spice_earth_vectors: bool = True,
        mode: JobMode = JobMode.QUEUED,
    ) -> GenerateLightmapReductionRasterResult:
        _ = mode
        return ToolImplementations._run_native_reduce_lightmap_raster(
            scenario_id=scenario_id,
            scenario_root_dir=scenario_root_dir,
            dem_path=dem_path,
            horizons_dir=horizons_dir,
            output_path=output_path,
            time_start_utc=time_start_utc,
            time_stop_utc=time_stop_utc,
            time_step_hours=time_step_hours,
            reducers=[
                {
                    "kind": "cumulative_duration_where",
                    "unit": str(duration_unit),
                    "margin_predicate": {
                        "signal": "earth_center_margin_deg_f32",
                        "reference": str(threshold_reference),
                        "threshold_value": float(threshold_deg),
                        "greater_than_or_equal": True,
                    },
                    "output_type": "float32",
                }
            ],
            reducer_kind="generate_earth_above_terrain_duration_raster",
            observer_elevation_meters=observer_elevation_meters,
            use_spice_sun_vectors=use_spice_sun_vectors,
            use_spice_earth_vectors=use_spice_earth_vectors,
            output_band_index=1,
        )

    @staticmethod
    @contract(
        name="ToolImplementations.generate_combined_sun_earth_max_contiguous_duration_raster",
        response_type=GenerateLightmapReductionRasterResult,
        description="Generate max contiguous duration raster for combined Sun fraction and Earth-above-terrain predicates.",
        tool_visibility=ToolVisibility.PUBLIC,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "lighting", "reduction", "worker-only"),
    )
    def generate_combined_sun_earth_max_contiguous_duration_raster(
        scenario_id: str,
        dem_path: str,
        horizons_dir: str,
        output_path: str,
        time_start_utc: str,
        time_stop_utc: str,
        time_step_hours: float,
        min_sun_fraction_u8: int = 1,
        earth_threshold_deg: float = 0.0,
        earth_threshold_reference: str = "center_margin",
        duration_unit: str = "hours",
        scenario_root_dir: str | None = None,
        observer_elevation_meters: float = 0.0,
        use_spice_sun_vectors: bool = True,
        use_spice_earth_vectors: bool = True,
        mode: JobMode = JobMode.QUEUED,
    ) -> GenerateLightmapReductionRasterResult:
        _ = mode
        return ToolImplementations._run_native_reduce_lightmap_raster(
            scenario_id=scenario_id,
            scenario_root_dir=scenario_root_dir,
            dem_path=dem_path,
            horizons_dir=horizons_dir,
            output_path=output_path,
            time_start_utc=time_start_utc,
            time_stop_utc=time_stop_utc,
            time_step_hours=time_step_hours,
            reducers=[
                {
                    "kind": "combined_sun_earth_contiguous_duration",
                    "unit": str(duration_unit),
                    "sun_predicate": {
                        "min_sun_fraction_u8": int(min_sun_fraction_u8),
                        "greater_than_or_equal": True,
                    },
                    "earth_margin_predicate": {
                        "signal": "earth_center_margin_deg_f32",
                        "reference": str(earth_threshold_reference),
                        "threshold_value": float(earth_threshold_deg),
                        "greater_than_or_equal": True,
                    },
                    "output_type": "float32",
                }
            ],
            reducer_kind="generate_combined_sun_earth_max_contiguous_duration_raster",
            observer_elevation_meters=observer_elevation_meters,
            use_spice_sun_vectors=use_spice_sun_vectors,
            use_spice_earth_vectors=use_spice_earth_vectors,
            output_band_index=1,
        )

    @staticmethod
    @contract(
        name="ToolImplementations.generate_psr_raster",
        response_type=GeneratePermanentShadowRasterResult,
        description="Generate a permanent-shadow (PSR-style) raster using the native moonlib mapops pipeline.",
        tool_visibility=ToolVisibility.PUBLIC,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "lighting", "psr", "worker-only"),
    )
    def generate_psr_raster(
        scenario_id: str,
        dem_path: str | None = None,
        horizons_dir: str | None = None,
        output_path: str | None = None,
        scenario_root_dir: str | None = None,
        observer_elevation_meters: float = 0.0,
        mode: JobMode = JobMode.QUEUED,
    ) -> GeneratePermanentShadowRasterResult:
        _ = mode
        _ = observer_elevation_meters  # Current native PSR path uses precomputed horizons and native defaults.

        emit_job_progress(
            {
                "percent": 5.0,
                "stage": "psr_setup",
                "message": "Resolving PSR input paths.",
            }
        )
        resolved_paths = None
        try:
            resolved_paths = resolve_scenario_paths(scenario_id)
        except Exception:
            resolved_paths = None

        if scenario_root_dir:
            scenario_root_default = Path(scenario_root_dir).expanduser().resolve()
        else:
            if resolved_paths is None:
                raise RuntimeError("Scenario path resolver is not configured.")
            scenario_root_default = Path(resolved_paths.scenario_root_dir).expanduser().resolve()

        dem_path_raw = str(dem_path or "").strip()
        if dem_path_raw:
            dem_path = dem_path_raw
        else:
            dem_candidates: list[Path] = []
            if resolved_paths is not None:
                dem_candidates.append(Path(resolved_paths.dem_path).expanduser().resolve())
            dem_candidates.extend(
                [
                    (scenario_root_default / "primary_dem.tif").resolve(),
                    (scenario_root_default / "dem.tif").resolve(),
                ]
            )
            deduped: list[Path] = []
            seen: set[str] = set()
            for candidate in dem_candidates:
                key = str(candidate).lower()
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(candidate)
            existing = next((candidate for candidate in deduped if candidate.exists() and candidate.is_file()), None)
            dem_path = str((existing or deduped[0]).resolve())

        horizons_dir = str(horizons_dir or "").strip() or str((scenario_root_default / "lighting" / "horizons").resolve())
        output_path = str(output_path or "").strip() or str(
            (scenario_root_default / "lighting" / "psr.tif").resolve()
        )

        scenario_root, dem, horizons, output = ToolImplementations._resolve_lightmap_reduction_paths(
            scenario_id=scenario_id,
            scenario_root_dir=scenario_root_dir,
            dem_path=dem_path,
            horizons_dir=horizons_dir,
            output_path=output_path,
        )

        emit_job_progress(
            {
                "percent": 10.0,
                "stage": "psr_native_execution",
                "message": "Starting native PSR map operation.",
            }
        )
        ToolImplementations._run_native_psr_mapops(
            scenario_root=scenario_root,
            dem=dem,
            horizons=horizons,
            output=output,
        )

        emit_job_progress(
            {
                "percent": 92.0,
                "stage": "psr_validate_output",
                "message": "Validating PSR raster output.",
            }
        )
        if not output.exists() or not output.is_file():
            raise RuntimeError(f"PSR output raster was not created: {output}")
        size_bytes = int(output.stat().st_size) if output.exists() else 0
        emit_job_progress(
            {
                "percent": 96.0,
                "stage": "psr_register_artifact",
                "message": "Registering PSR raster artifact.",
            }
        )
        register_artifact_output(
            scenario_root_dir=scenario_root,
            scenario_id=scenario_id,
            job_type="generate_psr_raster",
            artifact_kind="raster",
            artifact_path=output,
            size_bytes=size_bytes,
            metadata={
                "dem_path": str(dem),
                "horizons_dir": str(horizons),
                "observer_elevation_meters": float(observer_elevation_meters),
            },
        )

        emit_job_progress(
            {
                "percent": 100.0,
                "stage": "psr_complete",
                "message": "PSR raster generation complete.",
            }
        )
        return GeneratePermanentShadowRasterResult(
            scenario_id=scenario_id,
            scenario_root_dir=str(scenario_root),
            dem_path=str(dem),
            horizons_dir=str(horizons),
            output_path=str(output),
            size_bytes=size_bytes,
            artifact_db_path=str((scenario_root / "scenario.db").resolve()),
        )

    @staticmethod
    def _resolve_viewshed_observers(
        *,
        scenario_id: str,
        scenario_root: Path,
        dem_shape: tuple[int, int],
        dem_transform: Any,
        observer_x: float | None,
        observer_y: float | None,
        observer_height_m: float,
        observer_list: list[ViewshedObserverPoint] | None,
        observer_mask: ViewshedObserverMaskReference | None,
        routing_cleanup_mode: str = "none",
        routing_cleanup_iterations: int = 1,
    ) -> tuple[str, list[tuple[float, float, float]], dict[str, Any]]:
        has_single = observer_x is not None or observer_y is not None
        has_list = bool(observer_list)
        has_mask = observer_mask is not None
        provided = int(has_single) + int(has_list) + int(has_mask)
        if provided != 1:
            raise ApiError(
                status_code=422,
                code="viewshed_invalid_argument",
                message="Provide exactly one observer mode: single, observer_list, or observer_mask.",
            )
        if has_single and (observer_x is None or observer_y is None):
            raise ApiError(
                status_code=422,
                code="viewshed_invalid_argument",
                message="observer_x and observer_y are both required for single-observer mode.",
            )

        rows_count, cols_count = dem_shape
        observer_points: list[tuple[float, float, float]] = []
        pixel_rows: list[int] = []
        pixel_cols: list[int] = []
        input_mode: Literal["single", "list", "mask"] = "single"

        if has_single:
            input_mode = "single"
            obs_x = float(observer_x)
            obs_y = float(observer_y)
            rr, cc = rowcol(dem_transform, obs_x, obs_y)
            row_i = int(rr)
            col_i = int(cc)
            if row_i < 0 or col_i < 0 or row_i >= rows_count or col_i >= cols_count:
                raise ApiError(
                    status_code=422,
                    code="viewshed_observer_out_of_bounds",
                    message="Single observer lies outside DEM extent.",
                    details={"observer_x": obs_x, "observer_y": obs_y},
                )
            observer_points.append((obs_x, obs_y, float(observer_height_m)))
            pixel_rows.append(row_i)
            pixel_cols.append(col_i)
        elif has_list:
            input_mode = "list"
            assert observer_list is not None
            for item in observer_list:
                obs_x = float(item.x)
                obs_y = float(item.y)
                rr, cc = rowcol(dem_transform, obs_x, obs_y)
                row_i = int(rr)
                col_i = int(cc)
                if row_i < 0 or col_i < 0 or row_i >= rows_count or col_i >= cols_count:
                    raise ApiError(
                        status_code=422,
                        code="viewshed_observer_out_of_bounds",
                        message="observer_list contains points outside DEM extent.",
                        details={"observer_x": obs_x, "observer_y": obs_y},
                    )
                obs_h = float(observer_height_m if item.observer_height_m is None else item.observer_height_m)
                observer_points.append((obs_x, obs_y, obs_h))
                pixel_rows.append(row_i)
                pixel_cols.append(col_i)
        else:
            input_mode = "mask"
            assert observer_mask is not None
            mask_path = ToolImplementations._resolve_viewshed_mask_path(
                scenario_id=scenario_id,
                scenario_root=scenario_root,
                observer_mask=observer_mask,
            )
            if not mask_path.exists():
                raise ApiError(
                    status_code=404,
                    code="viewshed_input_not_found",
                    message=f"observer_mask raster not found: {mask_path}",
                )
            with rasterio.open(mask_path) as mask_ds:
                if mask_ds.width != cols_count or mask_ds.height != rows_count:
                    raise ApiError(
                        status_code=422,
                        code="viewshed_mask_grid_mismatch",
                        message="observer_mask raster dimensions must match DEM dimensions.",
                        details={
                            "dem_shape": [rows_count, cols_count],
                            "mask_shape": [mask_ds.height, mask_ds.width],
                        },
                    )
                if mask_ds.transform != dem_transform:
                    raise ApiError(
                        status_code=422,
                        code="viewshed_mask_grid_mismatch",
                        message="observer_mask transform must match DEM transform.",
                    )
                values = mask_ds.read(1)
                threshold = float(observer_mask.threshold)
                mask_bool = values > threshold
                if mask_ds.nodata is not None:
                    mask_bool = np.logical_and(mask_bool, values != mask_ds.nodata)
                rr, cc = np.where(mask_bool)
                if rr.size == 0:
                    raise ApiError(
                        status_code=422,
                        code="viewshed_no_observers",
                        message="observer_mask produced zero observer pixels after thresholding.",
                    )
                xs, ys = xy(dem_transform, rr, cc, offset="center")
                for idx in range(int(rr.size)):
                    observer_points.append((float(xs[idx]), float(ys[idx]), float(observer_height_m)))
                    pixel_rows.append(int(rr[idx]))
                    pixel_cols.append(int(cc[idx]))

        observer_count = len(observer_points)
        density = float(observer_count) / float(rows_count * cols_count) if rows_count > 0 and cols_count > 0 else 0.0
        occupancy = np.zeros((rows_count, cols_count), dtype=bool)
        occupancy[np.asarray(pixel_rows, dtype=np.int32), np.asarray(pixel_cols, dtype=np.int32)] = True
        cleanup_mode_applied = "none"
        cleanup_iterations_applied = 0
        if input_mode == "mask":
            cleanup_mode_applied = str(routing_cleanup_mode or "none").strip().lower() or "none"
            cleanup_iterations_applied = int(routing_cleanup_iterations or 0)
        component_count, largest_component, adjacency_ratio = ToolImplementations._compute_mask_connectivity_metrics(
            occupancy,
            cleanup_mode=cleanup_mode_applied,
            cleanup_iterations=cleanup_iterations_applied,
        )
        metrics = {
            "observer_count": observer_count,
            "observer_density": density,
            "adjacency_ratio": adjacency_ratio,
            "component_count": component_count,
            "largest_component_size": largest_component,
            "dem_height": rows_count,
            "dem_width": cols_count,
            "routing_cleanup_mode": cleanup_mode_applied,
            "routing_cleanup_iterations": cleanup_iterations_applied,
        }
        return input_mode, observer_points, metrics

    @staticmethod
    def _run_gdal_viewshed_single(
        *,
        dem_path: Path,
        observer_x: float,
        observer_y: float,
        observer_height_m: float,
        target_height_m: float,
        max_range_m: float,
    ) -> np.ndarray:
        configure_gdal_runtime()
        try:
            from osgeo import gdal  # type: ignore
        except Exception as exc:
            raise ApiError(
                status_code=500,
                code="viewshed_gdal_import_failed",
                message="Failed to import osgeo.gdal for viewshed execution.",
                details={"error": str(exc)},
            ) from exc

        dem_ds = gdal.Open(str(dem_path), gdal.GA_ReadOnly)
        if dem_ds is None:
            raise ApiError(
                status_code=500,
                code="viewshed_gdal_open_failed",
                message=f"Unable to open DEM with GDAL: {dem_path}",
            )
        band = dem_ds.GetRasterBand(1)
        if band is None:
            raise ApiError(
                status_code=500,
                code="viewshed_gdal_open_failed",
                message="DEM band 1 could not be opened for viewshed.",
            )

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="viewshed_single_", suffix=".tif", delete=False) as tmp:
                temp_path = Path(tmp.name).resolve()

            def _cancel_callback(_complete: float, _msg: str, _data: object | None) -> int:
                return 0 if is_job_cancel_requested() else 1

            ds = gdal.ViewshedGenerate(
                band,
                "GTiff",
                str(temp_path),
                ["COMPRESS=LZW", "TILED=YES"],
                float(observer_x),
                float(observer_y),
                float(observer_height_m),
                float(target_height_m),
                1.0,
                0.0,
                0.0,
                255.0,
                1.0,
                gdal.GVM_Edge,
                float(max_range_m),
                _cancel_callback,
                None,
                gdal.GVOT_NORMAL,
                [],
            )
            if ds is None:
                if is_job_cancel_requested():
                    raise ApiError(
                        status_code=409,
                        code="viewshed_canceled",
                        message="Viewshed run canceled.",
                    )
                raise ApiError(
                    status_code=500,
                    code="viewshed_gdal_failed",
                    message="GDAL viewshed generation returned no dataset.",
                )
            ds.FlushCache()
            ds = None
            with rasterio.open(temp_path) as out_ds:
                return out_ds.read(1)
        finally:
            dem_ds = None
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    @staticmethod
    def _run_viewshed_gdal(
        *,
        dem_path: Path,
        observer_points: list[tuple[float, float, float]],
        merge_mode: str,
        target_height_m: float,
        max_range_m: float,
        progress_events: list[dict[str, Any]],
    ) -> tuple[np.ndarray, str, float | None]:
        merged: np.ndarray | None = None
        total = max(1, len(observer_points))
        started = time.monotonic()
        last_emit = started
        emit_interval_seconds = 10.0
        for index, (obs_x, obs_y, obs_h) in enumerate(observer_points):
            if is_job_cancel_requested():
                raise ApiError(status_code=409, code="viewshed_canceled", message="Viewshed run canceled.")
            single = ToolImplementations._run_gdal_viewshed_single(
                dem_path=dem_path,
                observer_x=obs_x,
                observer_y=obs_y,
                observer_height_m=obs_h,
                target_height_m=target_height_m,
                max_range_m=max_range_m,
            )
            visible_mask = single == 1
            if merged is None:
                if merge_mode == "any_visible":
                    merged = np.zeros(single.shape, dtype=np.uint8)
                else:
                    merged = np.zeros(single.shape, dtype=np.uint16)
            if merge_mode == "any_visible":
                merged[visible_mask] = 1
            else:
                merged = merged + visible_mask.astype(np.uint16)
            processed = index + 1
            now = time.monotonic()
            should_emit = (
                processed == 1
                or processed == total
                or ((now - last_emit) >= emit_interval_seconds)
            )
            if should_emit:
                last_emit = now
                elapsed_s = max(0.001, now - started)
                obs_per_sec = float(processed) / elapsed_s
                remaining = max(0, total - processed)
                eta_seconds = float(remaining) / max(obs_per_sec, 1e-6)
                percent = 20.0 + (70.0 * float(processed) / float(total))
                ToolImplementations._viewshed_progress(
                    progress_events,
                    percent=percent,
                    stage="gdal_merge",
                    message=(
                        f"Processed observers {processed}/{total} "
                        f"(rate={obs_per_sec:.2f}/s, eta={eta_seconds/60.0:.1f} min)."
                    ),
                    extra={
                        "observers_processed": int(processed),
                        "observers_total": int(total),
                        "observers_rate_per_sec": float(obs_per_sec),
                        "eta_seconds": float(eta_seconds),
                    },
                )

        if merged is None:
            raise ApiError(
                status_code=422,
                code="viewshed_no_observers",
                message="No observer points were resolved.",
            )
        if merge_mode == "any_visible":
            return merged.astype(np.uint8, copy=False), "uint8", 255.0
        merged_u16 = np.clip(merged, 0, np.iinfo(np.uint16).max).astype(np.uint16, copy=False)
        return merged_u16, "uint16", None

    @staticmethod
    def _run_viewshed_cuda(
        *,
        dem_path: Path,
        observer_points: list[tuple[float, float, float]],
        merge_mode: str,
        target_height_m: float,
        max_range_m: float,
        apply_parabolic: bool,
        observer_batch_size: int,
        direction_count: int,
        step_size_pixels: float,
        progress_events: list[dict[str, Any]],
    ) -> tuple[np.ndarray, str, float | None]:
        configure_gdal_runtime()
        try:
            from numba import cuda as numba_cuda  # type: ignore
        except Exception as exc:
            raise ApiError(
                status_code=500,
                code="viewshed_cuda_unavailable",
                message="CUDA path requested but numba.cuda is unavailable.",
                details={"error": str(exc)},
            ) from exc
        if not bool(numba_cuda.is_available()):
            raise ApiError(
                status_code=500,
                code="viewshed_cuda_unavailable",
                message="CUDA path requested but no CUDA device is available.",
            )

        with rasterio.open(dem_path) as dem_ds:
            dem = dem_ds.read(1).astype(np.float32, copy=False)
            transform = dem_ds.transform
        pixel_size_m = float(ToolImplementations._estimate_pixel_size_meters(transform))
        if pixel_size_m <= 0.0:
            raise ApiError(
                status_code=422,
                code="viewshed_invalid_argument",
                message="DEM pixel size must be > 0 for CUDA viewshed.",
            )
        obs_rc = np.zeros((len(observer_points), 3), dtype=np.float32)
        for idx, (obs_x, obs_y, obs_h) in enumerate(observer_points):
            rr, cc = rowcol(transform, obs_x, obs_y)
            obs_rc[idx, 0] = float(rr)
            obs_rc[idx, 1] = float(cc)
            obs_rc[idx, 2] = float(obs_h)

        if merge_mode != "any_visible":
            raise ApiError(
                status_code=422,
                code="viewshed_cuda_merge_mode_unsupported",
                message="CUDA viewshed currently supports merge_mode='any_visible' only.",
            )

        dir_count = max(8, int(direction_count))
        step_px = max(0.1, float(step_size_pixels))

        theta = (2.0 * math.pi * np.arange(dir_count, dtype=np.float32)) / float(dir_count)
        dirs = np.stack((np.cos(theta), np.sin(theta)), axis=1).astype(np.float32, copy=False)
        d_dirs = numba_cuda.to_device(dirs)

        out_u8 = np.zeros(dem.shape, dtype=np.uint8)
        d_dem = numba_cuda.to_device(dem)
        d_out = numba_cuda.to_device(out_u8)
        d_probe = numba_cuda.to_device(np.zeros((1,), dtype=np.uint8))

        threads = 128
        curvature_flag = 1 if apply_parabolic else 0
        rows = int(dem.shape[0])
        cols = int(dem.shape[1])
        diag_px = math.sqrt(float(rows * rows + cols * cols))
        max_range_px = float(max_range_m) / pixel_size_m if float(max_range_m) > 0.0 else diag_px
        max_steps = max(1, int(math.ceil(max_range_px / step_px)))

        def _build_raycast_kernel_snapshot_2026_03_25(cuda_mod: Any) -> Any:
            """Preserved reference of the original supercover ray-casting kernel (not used at runtime)."""

            @cuda_mod.jit
            def _raycast_kernel_snapshot(
                dem_arr: Any,
                observers: Any,
                directions: Any,
                out_arr: Any,
                pixel_size: float,
                max_range: float,
                target_h: float,
                step_pixels: float,
                max_steps_local: int,
                curvature_mode: int,
            ) -> None:
                rows = dem_arr.shape[0]
                cols = dem_arr.shape[1]
                ray_index = cuda_mod.grid(1)
                obs_count_local = observers.shape[0]
                dir_count_local = directions.shape[0]
                total_rays_local = obs_count_local * dir_count_local
                if ray_index >= total_rays_local:
                    return
                radius = 1737400.0
                obs_idx = ray_index // dir_count_local
                dir_idx = ray_index - (obs_idx * dir_count_local)

                orow_f = observers[obs_idx, 0]
                ocol_f = observers[obs_idx, 1]
                oheight = observers[obs_idx, 2]
                orow = int(orow_f)
                ocol = int(ocol_f)
                if orow < 0 or orow >= rows or ocol < 0 or ocol >= cols:
                    return
                obs_base = dem_arr[orow, ocol] + oheight
                dx_dir = directions[dir_idx, 0]
                dy_dir = directions[dir_idx, 1]
                ox = ocol_f + 0.5
                oy = orow_f + 0.5
                prev_x = ox
                prev_y = oy
                max_slope = -1.0e30
                last_rr = -1
                last_cc = -1

                for step_idx in range(1, max_steps_local + 1):
                    current_x = ox + dx_dir * (float(step_idx) * step_pixels)
                    current_y = oy + dy_dir * (float(step_idx) * step_pixels)

                    seg_dx = current_x - prev_x
                    seg_dy = current_y - prev_y
                    seg_steps = int(math.ceil(max(abs(seg_dx), abs(seg_dy))))
                    if seg_steps < 1:
                        seg_steps = 1

                    stop_ray = False
                    for seg_i in range(1, seg_steps + 1):
                        t = float(seg_i) / float(seg_steps)
                        sx = prev_x + seg_dx * t
                        sy = prev_y + seg_dy * t
                        rr = int(math.floor(sy))
                        cc = int(math.floor(sx))
                        if rr < 0 or rr >= rows or cc < 0 or cc >= cols:
                            stop_ray = True
                            break
                        if rr == last_rr and cc == last_cc:
                            continue
                        last_rr = rr
                        last_cc = cc
                        dcol = (float(cc) + 0.5) - ox
                        drow = (float(rr) + 0.5) - oy
                        dist = math.sqrt(dcol * dcol + drow * drow) * pixel_size
                        if dist <= 0.0:
                            continue
                        if max_range > 0.0 and dist > max_range:
                            stop_ray = True
                            break
                        drop = (dist * dist) / (2.0 * radius) if curvature_mode == 1 else 0.0
                        sample = dem_arr[rr, cc]
                        slope_occ = (sample - obs_base - drop) / dist
                        slope_tgt = (sample + target_h - obs_base - drop) / dist
                        if slope_tgt + 1.0e-8 >= max_slope:
                            out_arr[rr, cc] = 1
                        if slope_occ > max_slope:
                            max_slope = slope_occ
                    prev_x = current_x
                    prev_y = current_y
                    if stop_ray:
                        break

            return _raycast_kernel_snapshot

        @numba_cuda.jit
        def _raycast_kernel(
            dem_arr: Any,
            observers: Any,
            directions: Any,
            ray_obs_indices: Any,
            ray_dir_indices: Any,
            out_arr: Any,
            max_slope_state: Any,
            last_rr_state: Any,
            last_cc_state: Any,
            active_state: Any,
            pixel_size: float,
            max_range: float,
            target_h: float,
            step_pixels: float,
            step_start: int,
            step_end: int,
            curvature_mode: int,
        ) -> None:
            rows = dem_arr.shape[0]
            cols = dem_arr.shape[1]
            ray_index = numba_cuda.grid(1)
            total_rays_local = ray_obs_indices.shape[0]
            if ray_index >= total_rays_local:
                return
            radius = 1737400.0
            obs_idx = int(ray_obs_indices[ray_index])
            dir_idx = int(ray_dir_indices[ray_index])
            if active_state[ray_index] == 0:
                return

            orow_f = observers[obs_idx, 0]
            ocol_f = observers[obs_idx, 1]
            oheight = observers[obs_idx, 2]
            orow = int(orow_f)
            ocol = int(ocol_f)
            if orow < 0 or orow >= rows or ocol < 0 or ocol >= cols:
                active_state[ray_index] = 0
                return
            obs_base = dem_arr[orow, ocol] + oheight
            dx_dir = directions[dir_idx, 0]
            dy_dir = directions[dir_idx, 1]
            ox = ocol_f + 0.5
            oy = orow_f + 0.5
            max_slope = max_slope_state[ray_index]
            last_rr = int(last_rr_state[ray_index])
            last_cc = int(last_cc_state[ray_index])

            for step_idx in range(step_start, step_end + 1):
                current_x = ox + dx_dir * (float(step_idx) * step_pixels)
                current_y = oy + dy_dir * (float(step_idx) * step_pixels)
                stop_ray = False
                if (not math.isfinite(current_x)) or (not math.isfinite(current_y)):
                    stop_ray = True
                elif current_y < 0.0 or current_y >= float(rows) or current_x < 0.0 or current_x >= float(cols):
                    stop_ray = True
                else:
                    rr = int(current_y)
                    cc = int(current_x)
                    if rr == last_rr and cc == last_cc:
                        if stop_ray:
                            break
                        continue
                    last_rr = rr
                    last_cc = cc
                    dist = (float(step_idx) * step_pixels) * pixel_size
                    if dist > 0.0:
                        if max_range > 0.0 and dist > max_range:
                            stop_ray = True
                        else:
                            drop = (dist * dist) / (2.0 * radius) if curvature_mode == 1 else 0.0
                            sample = dem_arr[rr, cc]
                            slope_occ = (sample - obs_base - drop) / dist
                            slope_tgt = (sample + target_h - obs_base - drop) / dist
                            if slope_tgt + 1.0e-8 >= max_slope:
                                # Idempotent write for any-visible merge mode; avoids global atomics.
                                out_arr[rr, cc] = 1
                            if slope_occ > max_slope:
                                max_slope = slope_occ
                if stop_ray:
                    active_state[ray_index] = 0
                    break
            max_slope_state[ray_index] = max_slope
            last_rr_state[ray_index] = last_rr
            last_cc_state[ray_index] = last_cc

        @numba_cuda.jit
        def _probe_kernel(arr: Any) -> None:
            i = numba_cuda.grid(1)
            if i < arr.size:
                arr[i] = arr[i]

        ToolImplementations._viewshed_progress(
            progress_events,
            percent=30.0,
            stage="cuda_prepare",
            message="Preparing CUDA viewshed kernel launches.",
            extra={
                "observers": int(len(observer_points)),
                "observer_batch_size": int(max(1, observer_batch_size)),
                "direction_count": int(dir_count),
                "step_size_pixels": float(step_px),
            },
        )
        total_observers = int(len(observer_points))
        batch_size = max(1, int(observer_batch_size))
        total_batches = max(1, math.ceil(float(total_observers) / float(batch_size)))
        total_rays = int(total_observers * dir_count)
        started = time.monotonic()
        last_emit = started
        emit_interval_seconds = 10.0
        state_lock = threading.Lock()
        state: dict[str, float] = {
            "processed_observers": 0.0,
            "processed_rays": 0.0,
            "batch_index": 0.0,
            "last_emit_at": started,
        }
        stop_heartbeat = threading.Event()

        def _emit_cuda_progress(prefix: str, *, force: bool = False) -> None:
            with state_lock:
                processed_observers = int(state["processed_observers"])
                processed_rays = int(state["processed_rays"])
                batch_index = int(state["batch_index"])
                prev_emit_at = float(state["last_emit_at"])
                now = time.monotonic()
                if not force and (now - prev_emit_at) < emit_interval_seconds:
                    return
                state["last_emit_at"] = now
            elapsed_s = max(0.001, time.monotonic() - started)
            rays_per_sec = float(processed_rays) / elapsed_s if processed_rays > 0 else 0.0
            remaining = max(0, total_rays - processed_rays)
            eta_seconds = float(remaining) / max(rays_per_sec, 1e-6) if processed_rays > 0 else float("inf")
            percent = 30.0 + (60.0 * float(processed_rays) / float(max(1, total_rays)))
            eta_text = f"{eta_seconds/60.0:.1f} min" if math.isfinite(eta_seconds) else "unknown"
            ToolImplementations._viewshed_progress(
                progress_events,
                percent=percent,
                stage="cuda_rays",
                message=(
                    f"{prefix} rays {processed_rays}/{total_rays} "
                    f"(observers {processed_observers}/{total_observers}, "
                    f"batch {batch_index}/{total_batches}, rate={rays_per_sec:.2f} rays/s, eta={eta_text})."
                ),
                extra={
                    "observers_processed": int(processed_observers),
                    "observers_total": int(total_observers),
                    "rays_processed": int(processed_rays),
                    "rays_total": int(total_rays),
                    "observer_batch_index": int(batch_index),
                    "observer_batch_total": int(total_batches),
                    "observer_batch_size": int(batch_size),
                    "rays_rate_per_sec": float(rays_per_sec),
                    "eta_seconds": float(eta_seconds) if math.isfinite(eta_seconds) else None,
                },
            )

        def _heartbeat() -> None:
            while not stop_heartbeat.wait(timeout=10.0):
                _emit_cuda_progress("CUDA progress:", force=True)

        heartbeat_thread = threading.Thread(
            target=_heartbeat,
            name="viewshed-cuda-progress-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            for batch_idx, start in enumerate(range(0, total_observers, batch_size), start=1):
                end = min(total_observers, start + batch_size)
                obs_batch = obs_rc[start:end, :]
                if not np.isfinite(obs_batch).all():
                    raise ApiError(
                        status_code=422,
                        code="viewshed_invalid_argument",
                        message="Observer batch contains non-finite coordinates/heights for CUDA viewshed.",
                        details={
                            "batch_index": int(batch_idx),
                            "batch_observer_start": int(start),
                            "batch_observer_end_exclusive": int(end),
                        },
                    )
                d_obs = numba_cuda.to_device(obs_rc[start:end, :])
                obs_batch_count = int(end - start)
                rays_in_batch = int(obs_batch_count * dir_count)
                blocks = int(math.ceil(float(rays_in_batch) / float(threads)))
                ray_obs_indices = np.repeat(np.arange(obs_batch_count, dtype=np.int32), dir_count)
                ray_dir_indices = np.tile(np.arange(dir_count, dtype=np.int32), obs_batch_count)
                d_ray_obs_indices = numba_cuda.to_device(ray_obs_indices)
                d_ray_dir_indices = numba_cuda.to_device(ray_dir_indices)
                d_max_slope = numba_cuda.to_device(np.full((rays_in_batch,), -1.0e30, dtype=np.float32))
                d_last_rr = numba_cuda.to_device(np.full((rays_in_batch,), -1, dtype=np.int32))
                d_last_cc = numba_cuda.to_device(np.full((rays_in_batch,), -1, dtype=np.int32))
                d_active = numba_cuda.to_device(np.ones((rays_in_batch,), dtype=np.uint8))
                try:
                    _probe_kernel[1, 1](d_probe)
                    numba_cuda.synchronize()
                except Exception as exc:
                    elapsed_s = max(0.001, time.monotonic() - started)
                    diagnostics = {
                        "cuda_error": str(exc),
                        "phase": "cuda_probe_sync",
                        "elapsed_seconds": float(elapsed_s),
                        "batch_index": int(batch_idx),
                        "batch_total": int(total_batches),
                        "batch_observer_start": int(start),
                        "batch_observer_end_exclusive": int(end),
                        "batch_observer_count": int(obs_batch_count),
                        "batch_rays": int(rays_in_batch),
                        "threads_per_block": int(threads),
                        "grid_blocks": int(blocks),
                        "direction_count": int(dir_count),
                        "step_size_pixels": float(step_px),
                        "max_steps": int(max_steps),
                        "pixel_size_m": float(pixel_size_m),
                        "max_range_m": float(max_range_m),
                        "target_height_m": float(target_height_m),
                        "batch_observers_preview": obs_batch[: min(2, obs_batch.shape[0]), :].tolist(),
                        "batch_row_min": float(np.min(obs_batch[:, 0])) if obs_batch.size else None,
                        "batch_row_max": float(np.max(obs_batch[:, 0])) if obs_batch.size else None,
                        "batch_col_min": float(np.min(obs_batch[:, 1])) if obs_batch.size else None,
                        "batch_col_max": float(np.max(obs_batch[:, 1])) if obs_batch.size else None,
                    }
                    logger.exception("viewshed cuda probe failed diagnostics=%s", diagnostics)
                    raise RuntimeError(json.dumps(diagnostics, sort_keys=True)) from exc
                try:
                    step_chunk_size = 512
                    for step_start in range(1, int(max_steps) + 1, int(step_chunk_size)):
                        step_end = min(int(max_steps), int(step_start + step_chunk_size - 1))
                        _raycast_kernel[blocks, threads](
                            d_dem,
                            d_obs,
                            d_dirs,
                            d_ray_obs_indices,
                            d_ray_dir_indices,
                            d_out,
                            d_max_slope,
                            d_last_rr,
                            d_last_cc,
                            d_active,
                            float(pixel_size_m),
                            float(max_range_m),
                            float(target_height_m),
                            float(step_px),
                            int(step_start),
                            int(step_end),
                            int(curvature_flag),
                        )
                        numba_cuda.synchronize()
                except Exception as exc:
                    elapsed_s = max(0.001, time.monotonic() - started)
                    processed_observers = int(end)
                    processed_rays = int(end * dir_count)
                    diagnostics = {
                        "cuda_error": str(exc),
                        "phase": "cuda_raycast_sync",
                        "elapsed_seconds": float(elapsed_s),
                        "batch_index": int(batch_idx),
                        "batch_total": int(total_batches),
                        "batch_observer_start": int(start),
                        "batch_observer_end_exclusive": int(end),
                        "batch_observer_count": int(obs_batch_count),
                        "batch_rays": int(rays_in_batch),
                        "processed_observers": int(processed_observers),
                        "total_observers": int(total_observers),
                        "processed_rays": int(processed_rays),
                        "total_rays": int(total_rays),
                        "threads_per_block": int(threads),
                        "grid_blocks": int(blocks),
                        "direction_count": int(dir_count),
                        "step_size_pixels": float(step_px),
                        "max_steps": int(max_steps),
                        "pixel_size_m": float(pixel_size_m),
                        "max_range_m": float(max_range_m),
                        "target_height_m": float(target_height_m),
                        "batch_observers_preview": obs_batch[: min(2, obs_batch.shape[0]), :].tolist(),
                        "batch_row_min": float(np.min(obs_batch[:, 0])) if obs_batch.size else None,
                        "batch_row_max": float(np.max(obs_batch[:, 0])) if obs_batch.size else None,
                        "batch_col_min": float(np.min(obs_batch[:, 1])) if obs_batch.size else None,
                        "batch_col_max": float(np.max(obs_batch[:, 1])) if obs_batch.size else None,
                    }
                    logger.exception("viewshed cuda batch failed diagnostics=%s", diagnostics)
                    raise RuntimeError(json.dumps(diagnostics, sort_keys=True)) from exc
                with state_lock:
                    state["processed_observers"] = float(end)
                    state["processed_rays"] = float(end * dir_count)
                    state["batch_index"] = float(batch_idx)
                now = time.monotonic()
                should_emit = (
                    int(end) == total_observers
                    or batch_idx == 1
                    or (now - last_emit) >= emit_interval_seconds
                )
                if should_emit:
                    last_emit = now
                    _emit_cuda_progress("CUDA observers processed:", force=True)
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=1.0)
            _emit_cuda_progress("CUDA observers processed:", force=True)
        out = d_out.copy_to_host()
        return (out > 0).astype(np.uint8, copy=False), "uint8", 255.0

    @staticmethod
    @contract(
        name="ToolImplementations.generate_los_viewshed",
        request_type=GenerateLosViewshedRequest,
        response_type=GenerateLosViewshedResult,
        description="Generate line-of-sight viewshed rasters from single/list/mask observers using GDAL or CUDA backends.",
        tool_name="terrain.viewshed",
        tool_title="terrain viewshed",
        tool_visibility=ToolVisibility.PUBLIC,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "terrain", "viewshed"),
    )
    def generate_los_viewshed(
        scenario_id: str,
        dem_path: str | None = None,
        scenario_root_dir: str | None = None,
        observer_x: float | None = None,
        observer_y: float | None = None,
        observer_height_m: float = 2.0,
        observer_list: list[ViewshedObserverPoint] | None = None,
        observer_mask: ViewshedObserverMaskReference | None = None,
        target_height_m: float = 0.0,
        max_range_m: float = 0.0,
        output_relative_path: str | None = None,
        overwrite_mode: str = "ask",
        merge_mode: str = "any_visible",
        backend_mode: str = "auto",
        force_parabolic: bool = False,
        allow_force_parabolic_override: bool = False,
        parabolic_error_tolerance_m: float | None = None,
        mode: JobMode = JobMode.QUEUED,
    ) -> GenerateLosViewshedResult:
        _ = mode
        progress_events: list[dict[str, Any]] = []
        raw_mode = str(backend_mode or "auto").strip().lower() or "auto"
        if raw_mode not in {"gdal", "cuda", "auto"}:
            raise ApiError(
                status_code=422,
                code="viewshed_invalid_argument",
                message="backend_mode must be one of: gdal, cuda, auto.",
            )
        merge_key = str(merge_mode or "any_visible").strip().lower() or "any_visible"
        if merge_key not in {"any_visible", "visibility_count"}:
            raise ApiError(
                status_code=422,
                code="viewshed_invalid_argument",
                message="merge_mode must be one of: any_visible, visibility_count.",
            )
        if max_range_m < 0.0:
            raise ApiError(
                status_code=422,
                code="viewshed_invalid_argument",
                message="max_range_m must be >= 0.",
            )
        if observer_mask is not None and not isinstance(observer_mask, ViewshedObserverMaskReference):
            observer_mask = ViewshedObserverMaskReference.model_validate(observer_mask)
        if observer_list is not None:
            normalized_list: list[ViewshedObserverPoint] = []
            for item in observer_list:
                if isinstance(item, ViewshedObserverPoint):
                    normalized_list.append(item)
                else:
                    normalized_list.append(ViewshedObserverPoint.model_validate(item))
            observer_list = normalized_list

        resolved_paths = None
        try:
            resolved_paths = resolve_scenario_paths(scenario_id)
        except RuntimeError:
            resolved_paths = None
        if resolved_paths is None and (not scenario_root_dir or not dem_path):
            raise RuntimeError("Scenario path resolver is not configured.")
        scenario_root = Path(
            scenario_root_dir or (resolved_paths.scenario_root_dir if resolved_paths is not None else "")
        ).expanduser().resolve()
        dem = Path(dem_path or (resolved_paths.dem_path if resolved_paths is not None else "")).expanduser().resolve()
        if not scenario_root.exists() or not scenario_root.is_dir():
            raise ApiError(
                status_code=404,
                code="viewshed_input_not_found",
                message=f"Scenario root directory not found: {scenario_root}",
            )
        if not dem.exists() or not dem.is_file():
            raise ApiError(
                status_code=404,
                code="viewshed_input_not_found",
                message=f"Scenario DEM not found: {dem}",
            )

        ToolImplementations._viewshed_progress(
            progress_events,
            percent=2.0,
            stage="resolve_inputs",
            message="Resolving observer inputs and DEM metadata.",
        )
        with rasterio.open(dem) as dem_ds:
            dem_shape = (int(dem_ds.height), int(dem_ds.width))
            dem_transform = dem_ds.transform

        cfg = ToolImplementations._load_viewshed_runtime_config()
        routing_cleanup_mode = str(cfg.get("routing_mask_cleanup", "none") or "none").strip().lower() or "none"
        if routing_cleanup_mode not in {"none", "erosion", "opening"}:
            routing_cleanup_mode = "none"
        routing_cleanup_iterations = int(cfg.get("routing_cleanup_iterations", 1) or 0)
        if routing_cleanup_iterations < 0:
            routing_cleanup_iterations = 0

        input_mode, observer_points, route_metrics = ToolImplementations._resolve_viewshed_observers(
            scenario_id=scenario_id,
            scenario_root=scenario_root,
            dem_shape=dem_shape,
            dem_transform=dem_transform,
            observer_x=observer_x,
            observer_y=observer_y,
            observer_height_m=observer_height_m,
            observer_list=observer_list,
            observer_mask=observer_mask,
            routing_cleanup_mode=routing_cleanup_mode,
            routing_cleanup_iterations=routing_cleanup_iterations,
        )

        if output_relative_path and str(output_relative_path).strip():
            rel = ToolImplementations._normalize_relative_path(str(output_relative_path))
        else:
            rel = ToolImplementations._default_viewshed_output_relative_path(
                scenario_id=scenario_id,
                observer_count=len(observer_points),
                merge_mode=merge_key,
                max_range_m=max_range_m,
            )
        output_path = (scenario_root / rel).resolve()
        if scenario_root != output_path and scenario_root not in output_path.parents:
            raise ApiError(
                status_code=422,
                code="viewshed_output_path_invalid",
                message="Output path escapes scenario root.",
                details={"output_relative_path": rel},
            )

        overwrite_key = str(overwrite_mode or "ask").strip().lower() or "ask"
        if overwrite_key not in {"ask", "never", "always"}:
            raise ApiError(
                status_code=422,
                code="viewshed_invalid_argument",
                message="overwrite_mode must be one of: ask, never, always.",
            )
        if output_path.exists() and overwrite_key != "always":
            code = "viewshed_overwrite_confirmation_required" if overwrite_key == "ask" else "viewshed_output_exists"
            raise ApiError(
                status_code=409,
                code=code,
                message=f"Output file already exists: {output_path}",
                details={"output_path": str(output_path), "overwrite_mode": overwrite_key},
            )

        tolerance_m = (
            float(parabolic_error_tolerance_m)
            if parabolic_error_tolerance_m is not None
            else float(cfg.get("parabolic_error_tolerance_m", 0.5) or 0.5)
        )
        pixel_size = ToolImplementations._estimate_pixel_size_meters(dem_transform)
        approx_distance = float(max_range_m)
        if approx_distance <= 0.0:
            approx_distance = math.sqrt(float(dem_shape[0] ** 2 + dem_shape[1] ** 2)) * float(pixel_size)
        parabolic_error_m = ToolImplementations._parabolic_error_m(approx_distance)
        high_fidelity_required = parabolic_error_m > tolerance_m
        if force_parabolic and high_fidelity_required and not bool(allow_force_parabolic_override):
            raise ApiError(
                status_code=422,
                code="viewshed_precision_override_required",
                message=(
                    "Parabolic mode exceeds configured error tolerance. "
                    "Set allow_force_parabolic_override=true to proceed anyway."
                ),
                details={
                    "parabolic_error_m": parabolic_error_m,
                    "parabolic_error_tolerance_m": tolerance_m,
                },
            )

        selected_backend, route_reason = ToolImplementations._select_viewshed_backend(
            backend_mode=raw_mode,
            observer_count=int(route_metrics["observer_count"]),
            observer_density=float(route_metrics["observer_density"]),
            adjacency_ratio=float(route_metrics["adjacency_ratio"]),
            largest_component_size=int(route_metrics["largest_component_size"]),
            cfg=cfg,
        )
        fallback_applied = False
        fallback_reason: str | None = None
        if selected_backend == "cuda":
            cuda_ok, cuda_reason = ToolImplementations._cuda_available()
            if not cuda_ok:
                if raw_mode == "cuda":
                    raise ApiError(
                        status_code=500,
                        code="viewshed_cuda_unavailable",
                        message="CUDA backend forced but unavailable.",
                        details={"reason": cuda_reason},
                    )
                fallback_applied = True
                fallback_reason = str(cuda_reason or "cuda_unavailable")
                selected_backend = "gdal"

        ToolImplementations._viewshed_progress(
            progress_events,
            percent=10.0,
            stage="routing",
            message=f"Selected {selected_backend} backend.",
            extra={
                "backend_mode_requested": raw_mode,
                "backend_mode_selected": selected_backend,
                "route_reason": route_reason,
                "backend_fallback_applied": fallback_applied,
                "backend_fallback_reason": fallback_reason,
                "route_metrics": dict(route_metrics),
            },
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if selected_backend == "cuda":
            cuda_batch_size = int(cfg.get("cuda_progress_observer_batch_size", 64) or 64)
            if cuda_batch_size < 1:
                cuda_batch_size = 64
            cuda_direction_count = int(cfg.get("cuda_ray_direction_count", 720) or 720)
            if cuda_direction_count < 8:
                cuda_direction_count = 8
            cuda_step_size_pixels = float(cfg.get("cuda_ray_step_size_pixels", 0.5) or 0.5)
            if cuda_step_size_pixels <= 0.0:
                cuda_step_size_pixels = 0.5
            try:
                out_arr, out_dtype, out_nodata = ToolImplementations._run_viewshed_cuda(
                    dem_path=dem,
                    observer_points=observer_points,
                    merge_mode=merge_key,
                    target_height_m=float(target_height_m),
                    max_range_m=float(max_range_m),
                    apply_parabolic=bool(force_parabolic or not high_fidelity_required),
                    observer_batch_size=cuda_batch_size,
                    direction_count=cuda_direction_count,
                    step_size_pixels=cuda_step_size_pixels,
                    progress_events=progress_events,
                )
            except Exception as exc:
                cuda_error = str(exc)
                auto_min_observers = int(cfg.get("auto_cuda_min_observers", 256) or 256)
                high_observer_job = int(route_metrics.get("observer_count", 0)) >= int(auto_min_observers)
                if raw_mode == "cuda":
                    raise ApiError(
                        status_code=500,
                        code="viewshed_cuda_runtime_failed",
                        message=(
                            "CUDA viewshed runtime failed. "
                            "On Windows display GPUs this can be caused by watchdog timeout (TDR) "
                            "resetting the CUDA context for long-running kernels."
                        ),
                        details={"error": cuda_error},
                    ) from exc
                if high_observer_job:
                    raise ApiError(
                        status_code=500,
                        code="viewshed_cuda_runtime_failed",
                        message=(
                            "CUDA viewshed runtime failed for a high-observer job; "
                            "GDAL fallback is disabled for this workload."
                        ),
                        details={
                            "error": cuda_error,
                            "observer_count": int(route_metrics.get("observer_count", 0)),
                            "auto_cuda_min_observers": int(auto_min_observers),
                            "selected_backend": "cuda",
                            "merge_mode": merge_key,
                            "observer_batch_size": int(cuda_batch_size),
                            "direction_count": int(cuda_direction_count),
                            "step_size_pixels": float(cuda_step_size_pixels),
                        },
                    ) from exc
                fallback_applied = True
                fallback_reason = f"cuda_runtime_failed: {cuda_error}"
                selected_backend = "gdal"
                ToolImplementations._viewshed_progress(
                    progress_events,
                    percent=22.0,
                    stage="routing",
                    message="CUDA runtime failed; falling back to GDAL backend.",
                    extra={
                        "backend_mode_requested": raw_mode,
                        "backend_mode_selected": selected_backend,
                        "backend_fallback_applied": True,
                        "backend_fallback_reason": fallback_reason,
                    },
                )
                out_arr, out_dtype, out_nodata = ToolImplementations._run_viewshed_gdal(
                    dem_path=dem,
                    observer_points=observer_points,
                    merge_mode=merge_key,
                    target_height_m=float(target_height_m),
                    max_range_m=float(max_range_m),
                    progress_events=progress_events,
                )
        else:
            out_arr, out_dtype, out_nodata = ToolImplementations._run_viewshed_gdal(
                dem_path=dem,
                observer_points=observer_points,
                merge_mode=merge_key,
                target_height_m=float(target_height_m),
                max_range_m=float(max_range_m),
                progress_events=progress_events,
            )

        with rasterio.open(dem) as dem_ds:
            profile = dem_ds.profile.copy()
            profile.update(
                {
                    "driver": "GTiff",
                    "count": 1,
                    "dtype": out_dtype,
                    "compress": "LZW",
                    "tiled": False,
                    "nodata": out_nodata,
                }
            )
            with rasterio.open(output_path, "w", **profile) as out_ds:
                out_ds.write(out_arr, 1)

        parameter_hash = hashlib.sha256(
            json.dumps(
                {
                    "scenario_id": scenario_id,
                    "dem_path": str(dem),
                    "observer_mode": input_mode,
                    "observer_count": int(route_metrics["observer_count"]),
                    "observer_height_m": float(observer_height_m),
                    "target_height_m": float(target_height_m),
                    "max_range_m": float(max_range_m),
                    "backend_mode_requested": raw_mode,
                    "backend_mode_selected": selected_backend,
                    "merge_mode": merge_key,
                    "force_parabolic": bool(force_parabolic),
                "allow_force_parabolic_override": bool(allow_force_parabolic_override),
                "routing_cleanup_mode": routing_cleanup_mode,
                "routing_cleanup_iterations": routing_cleanup_iterations,
            },
            sort_keys=True,
        ).encode("utf-8")
        ).hexdigest()

        try:
            registered = register_generated_raster(
                scenario_id=scenario_id,
                relative_path=rel,
                lineage={
                    "job": "generate_los_viewshed",
                    "dem_path": str(dem),
                    "observer_input_mode": input_mode,
                    "observer_count": int(route_metrics["observer_count"]),
                    "observer_height_m": float(observer_height_m),
                    "target_height_m": float(target_height_m),
                    "max_range_m": float(max_range_m),
                    "backend_mode_requested": raw_mode,
                    "backend_mode_selected": selected_backend,
                    "backend_fallback_applied": fallback_applied,
                    "backend_fallback_reason": fallback_reason,
                    "merge_mode": merge_key,
                    "route_metrics": dict(route_metrics),
                    "route_reason": route_reason,
                    "high_fidelity_required": bool(high_fidelity_required),
                    "parabolic_error_m": float(parabolic_error_m),
                    "parabolic_error_tolerance_m": float(tolerance_m),
                    "routing_cleanup_mode": routing_cleanup_mode,
                    "routing_cleanup_iterations": routing_cleanup_iterations,
                    "parameter_hash": parameter_hash,
                },
            )
        except RuntimeError as exc:
            if "Generated raster registrar is not configured." not in str(exc):
                raise
            logger.warning(
                "viewshed artifact registrar unavailable; returning unregistered output metadata scenario_id=%s rel=%s",
                scenario_id,
                rel,
            )
            registered = RegisteredRasterOutput(
                product_id=f"unregistered_prd_{parameter_hash[:12]}",
                file_id=f"unregistered_fil_{parameter_hash[:12]}",
                relative_path=rel,
            )

        ToolImplementations._viewshed_progress(
            progress_events,
            percent=100.0,
            stage="register_artifact",
            message="Viewshed generation completed.",
            extra={"file_id": registered.file_id, "product_id": registered.product_id},
        )

        return GenerateLosViewshedResult(
            scenario_id=scenario_id,
            dem_path=str(dem),
            observer_count=int(route_metrics["observer_count"]),
            observer_input_mode=input_mode,
            backend_mode_requested=raw_mode,
            backend_mode_selected=selected_backend,  # type: ignore[arg-type]
            backend_fallback_applied=bool(fallback_applied),
            backend_fallback_reason=fallback_reason,
            merge_mode=merge_key,  # type: ignore[arg-type]
            target_height_m=float(target_height_m),
            max_range_m=float(max_range_m),
            output_path=str(output_path),
            output_relative_path=registered.relative_path,
            file_id=registered.file_id,
            product_id=registered.product_id,
            output_dtype=out_dtype,
            output_nodata=out_nodata,
            route_metrics=dict(route_metrics),
            high_fidelity_mode=bool(high_fidelity_required and not force_parabolic),
            parabolic_error_m=float(parabolic_error_m),
            parameter_hash=parameter_hash,
            progress_events=progress_events,
            artifact_db_path=str((scenario_root / "scenario.db").resolve()),
        )

    @staticmethod
    @contract(
        name="ToolImplementations.analyze_observer_mask_connectivity",
        request_type=AnalyzeObserverMaskConnectivityRequest,
        response_type=AnalyzeObserverMaskConnectivityResult,
        description="Analyze observer-mask connectivity metrics used by viewshed backend routing.",
        tool_name="terrain.mask_connectivity_metrics",
        tool_title="terrain mask connectivity metrics",
        tool_visibility=ToolVisibility.PUBLIC,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "terrain", "viewshed", "routing"),
    )
    def analyze_observer_mask_connectivity(
        scenario_id: str,
        observer_mask: ViewshedObserverMaskReference,
        dem_path: str | None = None,
        scenario_root_dir: str | None = None,
        require_match_dem_grid: bool = True,
        cleanup_mode: str = "none",
        cleanup_iterations: int = 1,
        mode: JobMode = JobMode.QUEUED,
    ) -> AnalyzeObserverMaskConnectivityResult:
        _ = mode
        progress_events: list[dict[str, Any]] = []
        if not isinstance(observer_mask, ViewshedObserverMaskReference):
            observer_mask = ViewshedObserverMaskReference.model_validate(observer_mask)

        resolved = resolve_scenario_paths(scenario_id)
        scenario_root = Path(scenario_root_dir or resolved.scenario_root_dir).expanduser().resolve()
        dem = Path(dem_path or resolved.dem_path).expanduser().resolve()
        if not scenario_root.exists() or not scenario_root.is_dir():
            raise ApiError(
                status_code=404,
                code="viewshed_input_not_found",
                message=f"Scenario root directory not found: {scenario_root}",
            )
        if not dem.exists() or not dem.is_file():
            raise ApiError(
                status_code=404,
                code="viewshed_input_not_found",
                message=f"Scenario DEM not found: {dem}",
            )

        cleanup_key = str(cleanup_mode or "none").strip().lower() or "none"
        if cleanup_key not in {"none", "erosion", "opening"}:
            raise ApiError(
                status_code=422,
                code="viewshed_invalid_argument",
                message="cleanup_mode must be one of: none, erosion, opening.",
            )
        iterations = int(cleanup_iterations or 0)
        if iterations < 0:
            raise ApiError(
                status_code=422,
                code="viewshed_invalid_argument",
                message="cleanup_iterations must be >= 0.",
            )

        ToolImplementations._viewshed_progress(
            progress_events,
            percent=5.0,
            stage="resolve_inputs",
            message="Resolving DEM and observer mask.",
        )
        with rasterio.open(dem) as dem_ds:
            dem_shape = (int(dem_ds.height), int(dem_ds.width))
            dem_transform = dem_ds.transform

        mask_path = ToolImplementations._resolve_viewshed_mask_path(
            scenario_id=scenario_id,
            scenario_root=scenario_root,
            observer_mask=observer_mask,
        )
        if not mask_path.exists() or not mask_path.is_file():
            raise ApiError(
                status_code=404,
                code="viewshed_input_not_found",
                message=f"observer_mask raster not found: {mask_path}",
            )

        with rasterio.open(mask_path) as mask_ds:
            if require_match_dem_grid:
                if mask_ds.width != dem_shape[1] or mask_ds.height != dem_shape[0]:
                    raise ApiError(
                        status_code=422,
                        code="viewshed_mask_grid_mismatch",
                        message="observer_mask raster dimensions must match DEM dimensions.",
                        details={
                            "dem_shape": [dem_shape[0], dem_shape[1]],
                            "mask_shape": [mask_ds.height, mask_ds.width],
                        },
                    )
                if mask_ds.transform != dem_transform:
                    raise ApiError(
                        status_code=422,
                        code="viewshed_mask_grid_mismatch",
                        message="observer_mask transform must match DEM transform.",
                    )
            values = mask_ds.read(1)
            threshold = float(observer_mask.threshold)
            occupancy = values > threshold
            if mask_ds.nodata is not None:
                occupancy = np.logical_and(occupancy, values != mask_ds.nodata)

        observer_count = int(np.count_nonzero(occupancy))
        if observer_count <= 0:
            component_count = 0
            largest_component = 0
            adjacency_ratio = 0.0
        else:
            ToolImplementations._viewshed_progress(
                progress_events,
                percent=60.0,
                stage="compute_metrics",
                message="Computing connectivity metrics.",
            )
            component_count, largest_component, adjacency_ratio = ToolImplementations._compute_mask_connectivity_metrics(
                occupancy,
                cleanup_mode=cleanup_key,
                cleanup_iterations=iterations,
            )

        density = float(observer_count) / float(dem_shape[0] * dem_shape[1]) if dem_shape[0] > 0 and dem_shape[1] > 0 else 0.0
        ToolImplementations._viewshed_progress(
            progress_events,
            percent=100.0,
            stage="done",
            message="Observer-mask connectivity metrics computed.",
        )
        return AnalyzeObserverMaskConnectivityResult(
            scenario_id=scenario_id,
            dem_path=str(dem),
            mask_path=str(mask_path),
            observer_count=observer_count,
            observer_density=density,
            component_count=int(component_count),
            largest_component_size=int(largest_component),
            adjacency_ratio=float(adjacency_ratio),
            cleanup_mode=cleanup_key,  # type: ignore[arg-type]
            cleanup_iterations=int(iterations),
            require_match_dem_grid=bool(require_match_dem_grid),
            dem_height=int(dem_shape[0]),
            dem_width=int(dem_shape[1]),
            progress_events=progress_events,
        )

    # Phase 4.5.1c draft contracts: signatures are intentionally draft-only.
    # Implementations must be filled after signature ratification.
    @staticmethod
    @contract(
        name="ToolImplementations.generate_horizon_profile",
        response_type=GenerateHorizonProfileResult,
        description="Draft contract: generate a horizon profile for one observer location.",
        tool_visibility=ToolVisibility.DRAFT,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "terrain", "draft"),
    )
    def generate_horizon_profile(
        scenario_id: str,
        dem_path: str,
        output_path: str,
        observer_x: float,
        observer_y: float,
        observer_height_m: float = 2.0,
        azimuth_step_deg: float = 0.25,
        output_format: str = "json",
        scenario_root_dir: str | None = None,
        mode: JobMode = JobMode.QUEUED,
    ) -> GenerateHorizonProfileResult:
        raise NotImplementedError(
            "Draft contract only. Edit signature as needed, then implement after ratification."
        )

    @staticmethod
    @contract(
        name="ToolImplementations.generate_lightmap_timeseries",
        response_type=GenerateLightmapTimeseriesResult,
        description="Draft contract: generate time-series lightmap rasters from DEM + horizons.",
        tool_visibility=ToolVisibility.DRAFT,
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        confirmation_action_type="launch_job",
        tool_tags=("tool", "lighting", "draft", "worker-only"),
    )
    def generate_lightmap_timeseries(
        scenario_id: str,
        dem_path: str,
        horizons_dir: str,
        output_dir: str,
        time_start_utc: str,
        time_stop_utc: str,
        step_seconds: int,
        scenario_root_dir: str | None = None,
        mode: JobMode = JobMode.QUEUED,
    ) -> GenerateLightmapTimeseriesResult:
        raise NotImplementedError(
            "Draft contract only. Edit signature as needed, then implement after ratification."
        )

# Backward-compatibility alias for transitional imports.
JobHandlers = ToolImplementations
