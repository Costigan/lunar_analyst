from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Protocol

import numpy as np

from backend.jobs.raster_transform import ExecutionHints
from backend.jobs.raster_transform import GridSpec
from backend.jobs.raster_transform import Raster
from backend.jobs.raster_transform import RasterTransformError
from backend.jobs.raster_transform import align_inputs_to_target
from backend.jobs.raster_transform import aspect_raster
from backend.jobs.raster_transform import compute_value_range
from backend.jobs.raster_transform import hillshade_raster
from backend.jobs.raster_transform import load_target_grid_from_dem
from backend.jobs.raster_transform import raster_file
from backend.jobs.raster_transform import raster_let
from backend.jobs.raster_transform import scenario_dem
from backend.jobs.raster_transform import slope_raster
from backend.jobs.raster_transform import write_output_raster as _write_output_raster
from backend.jobs.map_algebra import TargetGrid
from backend.notebook.runtime import get_context
from backend.notebook.runtime import infer_local_scenario_identity_and_root
from backend.notebook.runtime import is_running_under_job_runner
from backend.notebook.runtime import is_cancelled
from backend.notebook.runtime import register_output_if_available
from backend.notebook.runtime import replace_output_file
from backend.notebook.runtime import report_progress
from backend.notebook.runtime import resolve_primary_dem_path
from backend.notebook.runtime import safe_scenario_relative_path
from backend.worker.gdal_runtime import configure_gdal_runtime
from backend.worker.lightmap_streaming import LightmapStreamRequestPy
from backend.worker.lightmap_streaming import LightmapStreamRequestV2Py
from backend.worker.lightmap_streaming import LightmapStreamingClient
from backend.worker.lightmap_streaming import StreamTileMetaV2Py
from backend.worker.lightmap_streaming import TemporalSignalSpecPy
from backend.worker.lightmap_streaming import stream_tiles
from backend.worker.lightmap_streaming import stream_tiles_v2
from backend.worker.native_bootstrap import bootstrap_pythonnet
from backend.worker.native_bootstrap import import_moonlib

__all__ = [
    "LightmapRunConfig",
    "LightmapTileTransform",
    "ChunkedTemporalReducer",
    "bool_param",
    "bootstrap_native_and_register_gdal",
    "create_moonlib_bridge",
    "directory_file_stats",
    "label_regions",
    "region_sizes",
    "filter_regions_by_size",
    "find_borders",
    "compute_mask_connectivity_metrics",
    "get_context",
    "is_running_under_job_runner",
    "is_cancelled",
    "ExecutionHints",
    "GridSpec",
    "Raster",
    "aspect_raster",
    "hillshade_raster",
    "raster_file",
    "raster_let",
    "register_output_if_available",
    "replace_output_file",
    "report_progress",
    "run_lightmap_streaming_raster_job",
    "run_lightmap_signal_streaming_raster_job",
    "run_lightmap_native_reduction_raster_job",
    "resolve_dem_path_from_params",
    "resolve_scenario_identity_and_root",
    "resolve_primary_dem_path",
    "resolve_scenario_relative_dir",
    "safe_scenario_relative_path",
    "scenario_dem",
    "slope_raster",
    "write_output_raster",
    "to_dotnet_string_list",
    "write_json",
]


LightmapTileTransform = Callable[[np.ndarray], np.ndarray]


class ChunkedTemporalReducer(Protocol):
    def init_tile_state(self, tile_meta: StreamTileMetaV2Py) -> Any: ...
    def update(self, state: Any, tile_chunk: np.ndarray, tile_meta: StreamTileMetaV2Py) -> Any: ...
    def finalize(self, state: Any, tile_meta: StreamTileMetaV2Py) -> np.ndarray: ...


@dataclass(frozen=True)
class LightmapRunConfig:
    time_start_utc: str
    time_stop_utc: str
    time_step_hours: float
    default_horizons_relative_dir: str = "lighting/horizons"
    default_output_relative_path: str = "lighting/lightmap_streaming_time_mean.tif"
    output_kind: str = "raster"
    output_subkind: str = "lightmap_streaming_time_mean"
    output_render_mode: str | None = "raster"
    output_dtype: str = "float32"
    output_nodata: float = -9999.0
    patch_width: int = 128
    patch_height: int = 128
    max_read_parallelism: int = 4
    max_compute_parallelism: int = 24
    ready_queue_capacity: int = 64
    default_observer_elevation_meters: float = 0.0
    default_use_spice_sun_vectors: bool = True
    buffer_count: int = 6
    poll_timeout_ms: int = 250
    stream_progress_tile_interval: int = 20


def bool_param(params: dict[str, Any], key: str, default: bool) -> bool:
    raw = params.get(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(raw)


def resolve_dem_path_from_params(
    *,
    scenario_root: Path,
    scenario_id: str,
    params: dict[str, Any],
    param_name: str = "dem_relative_path",
    default_relative_path: str = "dem.tif",
) -> Path:
    dem_rel_raw = str(params.get(param_name, "")).strip()
    if dem_rel_raw:
        dem_rel = safe_scenario_relative_path(dem_rel_raw, default=default_relative_path)
        dem_path = (scenario_root / dem_rel).resolve()
        if not dem_path.exists():
            raise FileNotFoundError(f"DEM file does not exist: {dem_path}")
        return dem_path
    return resolve_primary_dem_path(
        scenario_root_dir=scenario_root,
        scenario_id=scenario_id,
    )


def resolve_scenario_relative_dir(
    *,
    scenario_root: Path,
    raw: str,
    default: str,
    create: bool = True,
) -> tuple[str, Path]:
    relative = safe_scenario_relative_path(str(raw).strip(), default=default)
    resolved = (scenario_root / relative).resolve()
    if create:
        resolved.mkdir(parents=True, exist_ok=True)
    return relative, resolved


def resolve_scenario_identity_and_root(
    *,
    default_scenario_id: str = "test_scenario",
    default_scenario_parent_dir: str | Path = "/e/lunar_analyst_scenarios",
    scenario_id_env: str = "LUNAR_NOTEBOOK_SCENARIO_ID",
    scenario_root_env: str = "LUNAR_NOTEBOOK_SCENARIO_ROOT",
) -> tuple[str, Path]:
    if is_running_under_job_runner():
        ctx = get_context()
        return str(ctx.scenario_id), Path(ctx.scenario_root_dir).resolve()

    scenario_id_raw = os.getenv(scenario_id_env, "").strip()
    scenario_id = scenario_id_raw or str(default_scenario_id)
    scenario_root_raw = os.getenv(scenario_root_env, "").strip()
    if scenario_root_raw:
        scenario_root = Path(scenario_root_raw).expanduser().resolve()
        return scenario_id, scenario_root

    inferred = infer_local_scenario_identity_and_root()
    if inferred is not None:
        return inferred

    scenario_root = (
        Path(default_scenario_parent_dir).expanduser() / scenario_id
    ).resolve()
    return scenario_id, scenario_root


def write_output_raster(
    *,
    output_path: Path,
    target_grid: TargetGrid,
    array: np.ndarray,
    nodata_value: float | None = None,
    valid_mask: np.ndarray | None = None,
    overwrite: bool,
) -> int:
    resolved = Path(output_path).expanduser()
    if not resolved.is_absolute():
        _scenario_id, scenario_root = resolve_scenario_identity_and_root()
        relative = safe_scenario_relative_path(resolved.as_posix(), default=resolved.name or "output.tif")
        resolved = (scenario_root / relative).resolve()
    else:
        resolved = resolved.resolve()
    return _write_output_raster(
        output_path=resolved,
        target_grid=target_grid,
        array=array,
        nodata_value=nodata_value,
        valid_mask=valid_mask,
        overwrite=overwrite,
    )


def bootstrap_native_and_register_gdal(
    *,
    force: bool = True,
    verify_bridge_smoke: bool = False,
) -> int:
    bootstrap_pythonnet(force=force, verify_bridge_smoke=verify_bridge_smoke)
    from OSGeo.GDAL import Gdal

    Gdal.AllRegister()
    driver_count = int(Gdal.GetDriverCount())
    if driver_count <= 0:
        raise RuntimeError(
            "GDAL registration failed in CLR runtime (driver_count=0) before native execution."
        )
    return driver_count


def create_moonlib_bridge(
    *,
    force_bootstrap: bool = True,
    verify_bridge_smoke: bool = False,
) -> Any:
    bootstrap_native_and_register_gdal(
        force=force_bootstrap,
        verify_bridge_smoke=verify_bridge_smoke,
    )
    moonlib = import_moonlib(
        force_bootstrap=False,
        verify_bridge_smoke=verify_bridge_smoke,
    )
    return moonlib.MoonlibBridge()


def to_dotnet_string_list(values: Iterable[Any]) -> Any:
    from System import String
    from System.Collections.Generic import List as DotNetList

    output = DotNetList[String]()
    for item in values:
        output.Add(String(str(item)))
    return output


def directory_file_stats(path: Path) -> tuple[int, int]:
    file_count = 0
    size_bytes = 0
    for child in Path(path).rglob("*"):
        if child.is_file():
            file_count += 1
            size_bytes += child.stat().st_size
    return file_count, size_bytes


def write_json(
    path: Path,
    payload: dict[str, Any],
    *,
    indent: int = 2,
    sort_keys: bool = True,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=indent, sort_keys=sort_keys) + "\n",
        encoding="utf-8",
    )


def label_regions(
    mask: Any,
    *,
    cleanup_mode: str = "none",
    cleanup_iterations: int = 1,
) -> np.ndarray:
    arr = np.asarray(mask)
    if arr.ndim != 2:
        raise ValueError(f"label_regions() expects a 2D input mask; received ndim={int(arr.ndim)}.")
    from scipy import ndimage  # type: ignore

    mode_key = str(cleanup_mode or "none").strip().lower() or "none"
    if mode_key not in {"none", "erosion", "opening"}:
        raise ValueError("label_regions() cleanup_mode must be one of: 'none', 'erosion', 'opening'.")
    iterations = int(cleanup_iterations or 0)
    if iterations < 0:
        raise ValueError("label_regions() cleanup_iterations must be an integer >= 0.")
    mask_bool = np.asarray(arr, dtype=bool)
    if mode_key in {"erosion", "opening"} and iterations > 0:
        structure_bool = np.ones((3, 3), dtype=bool)
        if mode_key == "erosion":
            mask_bool = ndimage.binary_erosion(mask_bool, structure=structure_bool, iterations=iterations)
        else:
            mask_bool = ndimage.binary_opening(mask_bool, structure=structure_bool, iterations=iterations)

    structure = np.ones((3, 3), dtype=np.uint8)
    labels, _ = ndimage.label(mask_bool, structure=structure)
    return np.asarray(labels, dtype=np.int32)


def find_borders(mask: Any) -> np.ndarray:
    arr = np.asarray(mask)
    if arr.ndim != 2:
        raise ValueError(f"find_borders() expects a 2D input mask; received ndim={int(arr.ndim)}.")
    from scipy import ndimage  # type: ignore

    mask_bool = np.asarray(arr, dtype=bool)
    structure = np.ones((3, 3), dtype=bool)
    eroded = ndimage.binary_erosion(mask_bool, structure=structure, border_value=0)
    return np.logical_and(mask_bool, np.logical_not(eroded))


def region_sizes(
    mask: Any,
    *,
    cleanup_mode: str = "none",
    cleanup_iterations: int = 1,
) -> np.ndarray:
    labels = label_regions(
        mask,
        cleanup_mode=cleanup_mode,
        cleanup_iterations=cleanup_iterations,
    )
    counts = np.bincount(labels.ravel())
    sized = counts[labels]
    sized[labels == 0] = 0
    return np.asarray(sized, dtype=np.int32)


def filter_regions_by_size(
    mask: Any,
    threshold: float,
    comparator: str,
    *,
    cleanup_mode: str = "none",
    cleanup_iterations: int = 1,
) -> np.ndarray:
    arr = np.asarray(mask)
    if arr.ndim != 2:
        raise ValueError(f"filter_regions_by_size() expects a 2D input mask; received ndim={int(arr.ndim)}.")
    threshold_value = float(threshold)
    if threshold_value < 0.0:
        raise ValueError("filter_regions_by_size() threshold must be >= 0.")
    comp = str(comparator).strip()
    if comp not in {">=", "<="}:
        raise ValueError("filter_regions_by_size() comparator must be one of: '>=', '<='.")
    from scipy import ndimage  # type: ignore

    mask_original = np.asarray(arr, dtype=bool)
    labels_original, _ = ndimage.label(mask_original, structure=np.ones((3, 3), dtype=np.uint8))
    if labels_original.size == 0:
        return np.zeros_like(mask_original, dtype=bool)
    labels_seed = label_regions(
        mask_original,
        cleanup_mode=cleanup_mode,
        cleanup_iterations=cleanup_iterations,
    )
    seed_counts = np.bincount(labels_seed.ravel())
    keep_seed_ids = seed_counts >= threshold_value if comp == ">=" else seed_counts <= threshold_value
    if keep_seed_ids.size > 0:
        keep_seed_ids[0] = False
    keep_seed_pixels = keep_seed_ids[labels_seed]
    kept_original_ids = np.unique(labels_original[keep_seed_pixels])
    if kept_original_ids.size == 0:
        return np.zeros_like(mask_original, dtype=bool)
    keep_original_ids = np.zeros(int(labels_original.max()) + 1, dtype=bool)
    keep_original_ids[kept_original_ids] = True
    keep_original_ids[0] = False
    return keep_original_ids[labels_original]


def compute_mask_connectivity_metrics(
    mask: Any,
    *,
    cleanup_mode: str = "none",
    cleanup_iterations: int = 1,
) -> tuple[int, int, float]:
    arr = np.asarray(mask)
    if arr.ndim != 2:
        raise ValueError(
            f"compute_mask_connectivity_metrics() expects a 2D input mask; received ndim={int(arr.ndim)}."
        )
    total = int(np.count_nonzero(arr))
    if total <= 0:
        return 0, 0, 0.0

    from scipy import ndimage  # type: ignore

    mode_key = str(cleanup_mode or "none").strip().lower() or "none"
    iterations = max(0, int(cleanup_iterations or 0))

    mask_bool = np.asarray(arr, dtype=bool)
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


def _safe_report_progress(*, percent: float, message: str, stage: str) -> None:
    if not is_running_under_job_runner():
        return
    report_progress(percent=percent, message=message, stage=stage)


def _safe_is_cancelled() -> bool:
    if not is_running_under_job_runner():
        return False
    return is_cancelled()


def _resolve_runtime_context() -> tuple[str, Path, dict[str, Any]]:
    if is_running_under_job_runner():
        ctx = get_context()
        scenario_id = str(ctx.scenario_id)
        scenario_root = Path(ctx.scenario_root_dir).resolve()
        params = ctx.params if isinstance(ctx.params, dict) else {}
        return scenario_id, scenario_root, params

    scenario_id, scenario_root = resolve_scenario_identity_and_root()
    return scenario_id, scenario_root, {}


def _resolve_surrounding_dem_paths(params: dict[str, Any]) -> list[Path]:
    raw = params.get("surrounding_dem_paths", [])
    if not isinstance(raw, list):
        raise ValueError("params.surrounding_dem_paths must be a list of paths.")
    return [
        Path(str(item)).expanduser().resolve()
        for item in raw
        if str(item).strip()
    ]


def _resolve_raster_dtype(*, gdal_module: Any, dtype_name: str) -> tuple[np.dtype[Any], int]:
    name = str(dtype_name).strip().lower()
    mapping: dict[str, tuple[np.dtype[Any], int]] = {
        "uint8": (np.dtype(np.uint8), int(gdal_module.GDT_Byte)),
        "int16": (np.dtype(np.int16), int(gdal_module.GDT_Int16)),
        "uint16": (np.dtype(np.uint16), int(gdal_module.GDT_UInt16)),
        "int32": (np.dtype(np.int32), int(gdal_module.GDT_Int32)),
        "uint32": (np.dtype(np.uint32), int(gdal_module.GDT_UInt32)),
        "float32": (np.dtype(np.float32), int(gdal_module.GDT_Float32)),
        "float64": (np.dtype(np.float64), int(gdal_module.GDT_Float64)),
    }
    resolved = mapping.get(name)
    if resolved is None:
        raise ValueError(
            f"Unsupported output_dtype={dtype_name!r}. "
            f"Supported: {', '.join(sorted(mapping.keys()))}."
        )
    return resolved


def run_lightmap_streaming_raster_job(
    *,
    config: LightmapRunConfig,
    tile_transform: LightmapTileTransform,
) -> dict[str, Any]:
    scenario_id, scenario_root, params = _resolve_runtime_context()
    if not scenario_root.exists() or not scenario_root.is_dir():
        raise FileNotFoundError(f"Scenario root does not exist: {scenario_root}")

    _safe_report_progress(percent=5.0, message="Initializing native bridge", stage="init")
    client = LightmapStreamingClient(force_bootstrap=True, verify_bridge_smoke=False)

    _safe_report_progress(percent=10.0, message="Initializing GDAL runtime", stage="init")
    configure_gdal_runtime()
    from osgeo import gdal

    gdal.UseExceptions()
    gdal_np_dtype, gdal_dtype = _resolve_raster_dtype(
        gdal_module=gdal,
        dtype_name=str(params.get("output_dtype", config.output_dtype)),
    )

    dem_path = resolve_dem_path_from_params(
        scenario_root=scenario_root,
        scenario_id=scenario_id,
        params=params,
    )
    try:
        dem_rel = dem_path.relative_to(scenario_root).as_posix()
    except ValueError:
        dem_rel = str(dem_path)

    horizons_rel, horizons_dir = resolve_scenario_relative_dir(
        scenario_root=scenario_root,
        raw=str(
            params.get("horizons_relative_dir", config.default_horizons_relative_dir)
        ).strip(),
        default=config.default_horizons_relative_dir,
        create=False,
    )
    if not horizons_dir.exists() or not horizons_dir.is_dir():
        raise FileNotFoundError(f"Horizons directory does not exist: {horizons_dir}")

    output_rel = safe_scenario_relative_path(
        str(
            params.get("output_relative_path", config.default_output_relative_path)
        ).strip(),
        default=config.default_output_relative_path,
    )
    output_path = (scenario_root / output_rel).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    replace_output_file(output_path)

    surrounding_dem_paths = _resolve_surrounding_dem_paths(params)
    observer_elevation_meters = float(
        params.get(
            "observer_elevation_meters",
            config.default_observer_elevation_meters,
        )
    )
    time_start_utc = str(params.get("time_start_utc", config.time_start_utc)).strip()
    time_stop_utc = str(params.get("time_stop_utc", config.time_stop_utc)).strip()
    time_step_hours = float(params.get("time_step_hours", config.time_step_hours))
    use_spice_sun_vectors = bool_param(
        params,
        "use_spice_sun_vectors",
        config.default_use_spice_sun_vectors,
    )
    buffer_count = max(1, int(params.get("buffer_count", config.buffer_count)))
    poll_timeout_ms = max(1, int(params.get("poll_timeout_ms", config.poll_timeout_ms)))
    patch_width = max(1, int(params.get("patch_width", config.patch_width)))
    patch_height = max(1, int(params.get("patch_height", config.patch_height)))
    max_read_parallelism = max(
        1, int(params.get("max_read_parallelism", config.max_read_parallelism))
    )
    max_compute_parallelism = max(
        1,
        int(params.get("max_compute_parallelism", config.max_compute_parallelism)),
    )
    ready_queue_capacity = max(
        1, int(params.get("ready_queue_capacity", config.ready_queue_capacity))
    )
    nodata_value = float(params.get("nodata_value", config.output_nodata))

    request = LightmapStreamRequestPy(
        scenario_root_dir=scenario_root,
        dem_path=dem_path,
        surrounding_dem_paths=surrounding_dem_paths,
        horizon_dir=horizons_dir,
        start_utc=time_start_utc,
        stop_utc=time_stop_utc,
        time_step_hours=time_step_hours,
        observer_elevation_meters=observer_elevation_meters,
        patch_width=patch_width,
        patch_height=patch_height,
        max_read_parallelism=max_read_parallelism,
        max_compute_parallelism=max_compute_parallelism,
        ready_queue_capacity=ready_queue_capacity,
        use_spice_sun_vectors=use_spice_sun_vectors,
    )

    dem_ds = gdal.Open(str(dem_path), gdal.GA_ReadOnly)
    if dem_ds is None:
        raise RuntimeError(f"Failed to open DEM: {dem_path}")

    width = int(dem_ds.RasterXSize)
    height = int(dem_ds.RasterYSize)
    projection = dem_ds.GetProjection() or ""
    geotransform = dem_ds.GetGeoTransform(can_return_null=True)
    if geotransform is None:
        raise RuntimeError("Primary DEM has no geotransform.")

    driver = gdal.GetDriverByName("GTiff")
    if driver is None:
        raise RuntimeError("GDAL GTiff driver not available.")

    out_ds = driver.Create(
        str(output_path),
        width,
        height,
        1,
        gdal_dtype,
        options=[
            "TILED=YES",
            "BLOCKXSIZE=128",
            "BLOCKYSIZE=128",
            "COMPRESS=LZW",
            "BIGTIFF=IF_SAFER",
        ],
    )
    if out_ds is None:
        raise RuntimeError(f"Failed to create output GeoTIFF: {output_path}")

    out_ds.SetProjection(projection)
    out_ds.SetGeoTransform(geotransform)
    out_band = out_ds.GetRasterBand(1)
    if out_band is None:
        raise RuntimeError("Failed to access output raster band.")
    out_band.SetNoDataValue(nodata_value)
    out_band.Fill(nodata_value)

    tiles_written = 0
    global_min: float | None = None
    global_max: float | None = None

    try:
        _safe_report_progress(percent=15.0, message="Streaming lightmap tiles", stage="stream")
        for tile_meta, tile_3d in stream_tiles(
            client,
            request,
            buffer_count=buffer_count,
            poll_timeout_ms=poll_timeout_ms,
        ):
            if _safe_is_cancelled():
                raise RuntimeError("Job cancelled while streaming lightmap tiles.")

            tile_2d = tile_transform(tile_3d)
            if not isinstance(tile_2d, np.ndarray):
                raise TypeError("tile_transform must return a numpy.ndarray.")
            if tile_2d.ndim != 2:
                raise ValueError("tile_transform output must have shape [height, width].")
            if tile_2d.shape[0] < int(tile_meta.height) or tile_2d.shape[1] < int(tile_meta.width):
                raise ValueError(
                    "tile_transform output shape is smaller than streamed tile window."
                )
            if tile_2d.dtype != gdal_np_dtype:
                tile_2d = tile_2d.astype(gdal_np_dtype, copy=False)
            if not tile_2d.flags["C_CONTIGUOUS"]:
                tile_2d = np.ascontiguousarray(tile_2d)

            xoff = int(tile_meta.patch_col)
            yoff = int(tile_meta.patch_row)
            tw = int(tile_meta.width)
            th = int(tile_meta.height)

            write_w = min(tw, width - xoff)
            write_h = min(th, height - yoff)
            if write_w <= 0 or write_h <= 0:
                continue

            window = tile_2d[:write_h, :write_w]
            out_band.WriteArray(window, xoff=xoff, yoff=yoff)
            tiles_written += 1

            local_min = float(window.min())
            local_max = float(window.max())
            global_min = local_min if global_min is None else min(global_min, local_min)
            global_max = local_max if global_max is None else max(global_max, local_max)

            if tiles_written % max(1, int(config.stream_progress_tile_interval)) == 0:
                _safe_report_progress(
                    percent=min(90.0, 15.0 + (tiles_written / 20.0)),
                    message=f"Wrote {tiles_written} tile(s)",
                    stage="stream",
                )
    finally:
        out_band.FlushCache()
        out_ds.FlushCache()
        out_band = None
        out_ds = None
        dem_ds = None

    register_output_if_available(
        relative_path=output_rel,
        kind=config.output_kind,
        subkind=config.output_subkind,
        render_mode=config.output_render_mode,
        metadata={
            "source_dem": dem_rel,
            "horizons_relative_dir": horizons_rel,
            "time_start_utc": time_start_utc,
            "time_stop_utc": time_stop_utc,
            "time_step_hours": time_step_hours,
            "tiles_written": tiles_written,
            "value_min": global_min,
            "value_max": global_max,
            "output_dtype": str(gdal_np_dtype),
            "observer_elevation_meters": observer_elevation_meters,
            "use_spice_sun_vectors": use_spice_sun_vectors,
        },
    )

    _safe_report_progress(
        percent=95.0,
        message="Lightmap streaming complete",
        stage="finalize",
    )

    return {
        "scenario_id": scenario_id,
        "scenario_root": str(scenario_root),
        "dem_relative_path": dem_rel,
        "horizons_relative_dir": horizons_rel,
        "output_relative_path": output_rel,
        "time_start_utc": time_start_utc,
        "time_stop_utc": time_stop_utc,
        "time_step_hours": time_step_hours,
        "tiles_written": tiles_written,
        "value_min": global_min,
        "value_max": global_max,
    }


def _prepare_lightmap_raster_job_v2(
    *,
    config: LightmapRunConfig,
) -> tuple[dict[str, Any], Any, Any]:
    scenario_id, scenario_root, params = _resolve_runtime_context()
    if not scenario_root.exists() or not scenario_root.is_dir():
        raise FileNotFoundError(f"Scenario root does not exist: {scenario_root}")

    _safe_report_progress(percent=5.0, message="Initializing native bridge", stage="init")
    client = LightmapStreamingClient(force_bootstrap=True, verify_bridge_smoke=False)

    _safe_report_progress(percent=10.0, message="Initializing GDAL runtime", stage="init")
    configure_gdal_runtime()
    from osgeo import gdal

    gdal.UseExceptions()
    gdal_np_dtype, gdal_dtype = _resolve_raster_dtype(
        gdal_module=gdal,
        dtype_name=str(params.get("output_dtype", config.output_dtype)),
    )

    dem_path = resolve_dem_path_from_params(
        scenario_root=scenario_root,
        scenario_id=scenario_id,
        params=params,
    )
    try:
        dem_rel = dem_path.relative_to(scenario_root).as_posix()
    except ValueError:
        dem_rel = str(dem_path)

    horizons_rel, horizons_dir = resolve_scenario_relative_dir(
        scenario_root=scenario_root,
        raw=str(
            params.get("horizons_relative_dir", config.default_horizons_relative_dir)
        ).strip(),
        default=config.default_horizons_relative_dir,
        create=False,
    )
    if not horizons_dir.exists() or not horizons_dir.is_dir():
        raise FileNotFoundError(f"Horizons directory does not exist: {horizons_dir}")

    output_rel = safe_scenario_relative_path(
        str(
            params.get("output_relative_path", config.default_output_relative_path)
        ).strip(),
        default=config.default_output_relative_path,
    )
    output_path = (scenario_root / output_rel).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    replace_output_file(output_path)

    surrounding_dem_paths = _resolve_surrounding_dem_paths(params)
    observer_elevation_meters = float(
        params.get(
            "observer_elevation_meters",
            config.default_observer_elevation_meters,
        )
    )
    time_start_utc = str(params.get("time_start_utc", config.time_start_utc)).strip()
    time_stop_utc = str(params.get("time_stop_utc", config.time_stop_utc)).strip()
    time_step_hours = float(params.get("time_step_hours", config.time_step_hours))
    use_spice_sun_vectors = bool_param(
        params,
        "use_spice_sun_vectors",
        config.default_use_spice_sun_vectors,
    )
    use_spice_earth_vectors = bool_param(params, "use_spice_earth_vectors", True)
    buffer_count = max(1, int(params.get("buffer_count", config.buffer_count)))
    poll_timeout_ms = max(1, int(params.get("poll_timeout_ms", config.poll_timeout_ms)))
    patch_width = max(1, int(params.get("patch_width", config.patch_width)))
    patch_height = max(1, int(params.get("patch_height", config.patch_height)))
    max_read_parallelism = max(
        1, int(params.get("max_read_parallelism", config.max_read_parallelism))
    )
    max_compute_parallelism = max(
        1,
        int(params.get("max_compute_parallelism", config.max_compute_parallelism)),
    )
    ready_queue_capacity = max(
        1, int(params.get("ready_queue_capacity", config.ready_queue_capacity))
    )
    nodata_value = float(params.get("nodata_value", config.output_nodata))
    chunk_time_count = max(1, int(params.get("chunk_time_count", 256)))

    dem_ds = gdal.Open(str(dem_path), gdal.GA_ReadOnly)
    if dem_ds is None:
        raise RuntimeError(f"Failed to open DEM: {dem_path}")

    width = int(dem_ds.RasterXSize)
    height = int(dem_ds.RasterYSize)
    projection = dem_ds.GetProjection() or ""
    geotransform = dem_ds.GetGeoTransform(can_return_null=True)
    if geotransform is None:
        raise RuntimeError("Primary DEM has no geotransform.")

    return (
        {
            "scenario_id": scenario_id,
            "scenario_root": scenario_root,
            "params": params,
            "dem_path": dem_path,
            "dem_rel": dem_rel,
            "horizons_rel": horizons_rel,
            "horizons_dir": horizons_dir,
            "output_rel": output_rel,
            "output_path": output_path,
            "surrounding_dem_paths": surrounding_dem_paths,
            "observer_elevation_meters": observer_elevation_meters,
            "time_start_utc": time_start_utc,
            "time_stop_utc": time_stop_utc,
            "time_step_hours": time_step_hours,
            "use_spice_sun_vectors": use_spice_sun_vectors,
            "use_spice_earth_vectors": use_spice_earth_vectors,
            "buffer_count": buffer_count,
            "poll_timeout_ms": poll_timeout_ms,
            "patch_width": patch_width,
            "patch_height": patch_height,
            "max_read_parallelism": max_read_parallelism,
            "max_compute_parallelism": max_compute_parallelism,
            "ready_queue_capacity": ready_queue_capacity,
            "nodata_value": nodata_value,
            "chunk_time_count": chunk_time_count,
            "width": width,
            "height": height,
            "projection": projection,
            "geotransform": geotransform,
            "gdal_np_dtype": gdal_np_dtype,
            "gdal_dtype": gdal_dtype,
        },
        client,
        gdal,
    )


def _create_output_raster_dataset(
    *,
    gdal: Any,
    output_path: Path,
    width: int,
    height: int,
    band_count: int,
    gdal_dtype: int,
    projection: str,
    geotransform: Any,
    nodata_value: float,
) -> tuple[Any, list[Any]]:
    driver = gdal.GetDriverByName("GTiff")
    if driver is None:
        raise RuntimeError("GDAL GTiff driver not available.")

    out_ds = driver.Create(
        str(output_path),
        width,
        height,
        band_count,
        gdal_dtype,
        options=[
            "TILED=YES",
            "BLOCKXSIZE=128",
            "BLOCKYSIZE=128",
            "COMPRESS=LZW",
            "BIGTIFF=IF_SAFER",
        ],
    )
    if out_ds is None:
        raise RuntimeError(f"Failed to create output GeoTIFF: {output_path}")
    out_ds.SetProjection(projection)
    out_ds.SetGeoTransform(geotransform)

    bands: list[Any] = []
    for band_index in range(1, band_count + 1):
        band = out_ds.GetRasterBand(band_index)
        if band is None:
            raise RuntimeError(f"Failed to access output raster band {band_index}.")
        band.SetNoDataValue(nodata_value)
        band.Fill(nodata_value)
        bands.append(band)
    return out_ds, bands


def run_lightmap_signal_streaming_raster_job(
    *,
    config: LightmapRunConfig,
    signals: list[TemporalSignalSpecPy],
    reducer: ChunkedTemporalReducer,
) -> dict[str, Any]:
    ctx, client, gdal = _prepare_lightmap_raster_job_v2(config=config)

    request = LightmapStreamRequestV2Py(
        scenario_root_dir=ctx["scenario_root"],
        dem_path=ctx["dem_path"],
        surrounding_dem_paths=ctx["surrounding_dem_paths"],
        horizon_dir=ctx["horizons_dir"],
        start_utc=ctx["time_start_utc"],
        stop_utc=ctx["time_stop_utc"],
        time_step_hours=ctx["time_step_hours"],
        observer_elevation_meters=ctx["observer_elevation_meters"],
        patch_width=ctx["patch_width"],
        patch_height=ctx["patch_height"],
        max_read_parallelism=ctx["max_read_parallelism"],
        max_compute_parallelism=ctx["max_compute_parallelism"],
        ready_queue_capacity=ctx["ready_queue_capacity"],
        use_spice_sun_vectors=ctx["use_spice_sun_vectors"],
        mode="signal_stream",
        signals=signals,
        chunk_time_count=ctx["chunk_time_count"],
        reducers=None,
        use_spice_earth_vectors=ctx["use_spice_earth_vectors"],
    )

    out_ds, out_bands = _create_output_raster_dataset(
        gdal=gdal,
        output_path=ctx["output_path"],
        width=ctx["width"],
        height=ctx["height"],
        band_count=1,
        gdal_dtype=ctx["gdal_dtype"],
        projection=ctx["projection"],
        geotransform=ctx["geotransform"],
        nodata_value=ctx["nodata_value"],
    )
    out_band = out_bands[0]

    tiles_written = 0
    global_min: float | None = None
    global_max: float | None = None
    total_time_count = request.time_count()
    tile_states: dict[tuple[int, int], dict[str, Any]] = {}

    try:
        _safe_report_progress(percent=15.0, message="Streaming v2 signal chunks", stage="stream")
        for tile_meta, tile_chunk in stream_tiles_v2(
            client,
            request,
            buffer_count=ctx["buffer_count"],
            poll_timeout_ms=ctx["poll_timeout_ms"],
        ):
            if _safe_is_cancelled():
                raise RuntimeError("Job cancelled while streaming signal chunks.")

            if tile_meta.rank != 4:
                raise ValueError(f"Signal stream helper expects rank=4 tiles, got rank={tile_meta.rank}.")

            tile_key = (int(tile_meta.patch_row), int(tile_meta.patch_col))
            entry = tile_states.get(tile_key)
            if entry is None:
                entry = {
                    "state": reducer.init_tile_state(tile_meta),
                    "next_time_offset": 0,
                    "seen_time_count": 0,
                    "last_meta": tile_meta,
                }
                tile_states[tile_key] = entry

            if int(tile_meta.time_offset) != int(entry["next_time_offset"]):
                raise ValueError(
                    f"Unexpected chunk order for tile {tile_key}: "
                    f"expected time_offset={entry['next_time_offset']}, got {tile_meta.time_offset}."
                )

            entry["state"] = reducer.update(entry["state"], tile_chunk, tile_meta)
            entry["next_time_offset"] = int(tile_meta.time_offset) + int(tile_meta.time_count)
            entry["seen_time_count"] = int(entry["seen_time_count"]) + int(tile_meta.time_count)
            entry["last_meta"] = tile_meta

            if int(entry["seen_time_count"]) < total_time_count:
                continue
            if int(entry["seen_time_count"]) > total_time_count:
                raise ValueError(f"Received too many samples for tile {tile_key}.")

            tile_2d = reducer.finalize(entry["state"], tile_meta)
            if not isinstance(tile_2d, np.ndarray):
                raise TypeError("ChunkedTemporalReducer.finalize must return numpy.ndarray.")
            if tile_2d.ndim != 2:
                raise ValueError("ChunkedTemporalReducer.finalize output must have shape [height, width].")
            if tile_2d.shape[0] < int(tile_meta.height) or tile_2d.shape[1] < int(tile_meta.width):
                raise ValueError("Reducer finalize output shape is smaller than streamed tile window.")
            if tile_2d.dtype != ctx["gdal_np_dtype"]:
                tile_2d = tile_2d.astype(ctx["gdal_np_dtype"], copy=False)
            if not tile_2d.flags["C_CONTIGUOUS"]:
                tile_2d = np.ascontiguousarray(tile_2d)

            xoff = int(tile_meta.patch_col)
            yoff = int(tile_meta.patch_row)
            tw = int(tile_meta.width)
            th = int(tile_meta.height)
            write_w = min(tw, int(ctx["width"]) - xoff)
            write_h = min(th, int(ctx["height"]) - yoff)
            if write_w > 0 and write_h > 0:
                window = tile_2d[:write_h, :write_w]
                out_band.WriteArray(window, xoff=xoff, yoff=yoff)
                tiles_written += 1
                local_min = float(window.min())
                local_max = float(window.max())
                global_min = local_min if global_min is None else min(global_min, local_min)
                global_max = local_max if global_max is None else max(global_max, local_max)

                if tiles_written % max(1, int(config.stream_progress_tile_interval)) == 0:
                    _safe_report_progress(
                        percent=min(90.0, 15.0 + (tiles_written / 20.0)),
                        message=f"Wrote {tiles_written} tile(s)",
                        stage="stream",
                    )

            del tile_states[tile_key]

        if tile_states:
            pending = sorted(tile_states.keys())
            raise RuntimeError(f"Signal stream ended before finalizing all tiles: {pending[:5]}")
    finally:
        try:
            out_band.FlushCache()
        finally:
            out_ds.FlushCache()
            out_band = None
            out_ds = None

    register_output_if_available(
        relative_path=ctx["output_rel"],
        kind=config.output_kind,
        subkind=config.output_subkind,
        render_mode=config.output_render_mode,
        metadata={
            "source_dem": ctx["dem_rel"],
            "horizons_relative_dir": ctx["horizons_rel"],
            "time_start_utc": ctx["time_start_utc"],
            "time_stop_utc": ctx["time_stop_utc"],
            "time_step_hours": ctx["time_step_hours"],
            "tiles_written": tiles_written,
            "value_min": global_min,
            "value_max": global_max,
            "output_dtype": str(ctx["gdal_np_dtype"]),
            "observer_elevation_meters": ctx["observer_elevation_meters"],
            "use_spice_sun_vectors": ctx["use_spice_sun_vectors"],
            "use_spice_earth_vectors": ctx["use_spice_earth_vectors"],
            "stream_mode": "signal_stream",
            "signals": [s.signal for s in signals],
        },
    )

    _safe_report_progress(percent=95.0, message="V2 signal-stream complete", stage="finalize")
    return {
        "scenario_id": ctx["scenario_id"],
        "scenario_root": str(ctx["scenario_root"]),
        "dem_relative_path": ctx["dem_rel"],
        "horizons_relative_dir": ctx["horizons_rel"],
        "output_relative_path": ctx["output_rel"],
        "time_start_utc": ctx["time_start_utc"],
        "time_stop_utc": ctx["time_stop_utc"],
        "time_step_hours": ctx["time_step_hours"],
        "tiles_written": tiles_written,
        "value_min": global_min,
        "value_max": global_max,
    }


def run_lightmap_native_reduction_raster_job(
    *,
    config: LightmapRunConfig,
    reducers: list[dict[str, Any]],
) -> dict[str, Any]:
    if not reducers:
        raise ValueError("reducers must be non-empty.")

    ctx, client, gdal = _prepare_lightmap_raster_job_v2(config=config)

    request = LightmapStreamRequestV2Py(
        scenario_root_dir=ctx["scenario_root"],
        dem_path=ctx["dem_path"],
        surrounding_dem_paths=ctx["surrounding_dem_paths"],
        horizon_dir=ctx["horizons_dir"],
        start_utc=ctx["time_start_utc"],
        stop_utc=ctx["time_stop_utc"],
        time_step_hours=ctx["time_step_hours"],
        observer_elevation_meters=ctx["observer_elevation_meters"],
        patch_width=ctx["patch_width"],
        patch_height=ctx["patch_height"],
        max_read_parallelism=ctx["max_read_parallelism"],
        max_compute_parallelism=ctx["max_compute_parallelism"],
        ready_queue_capacity=ctx["ready_queue_capacity"],
        use_spice_sun_vectors=ctx["use_spice_sun_vectors"],
        mode="native_reduce",
        signals=None,
        chunk_time_count=ctx["chunk_time_count"],
        reducers=reducers,
        use_spice_earth_vectors=ctx["use_spice_earth_vectors"],
    )

    out_ds, out_bands = _create_output_raster_dataset(
        gdal=gdal,
        output_path=ctx["output_path"],
        width=ctx["width"],
        height=ctx["height"],
        band_count=len(reducers),
        gdal_dtype=ctx["gdal_dtype"],
        projection=ctx["projection"],
        geotransform=ctx["geotransform"],
        nodata_value=ctx["nodata_value"],
    )

    tiles_written = 0
    band_mins: list[float | None] = [None] * len(reducers)
    band_maxs: list[float | None] = [None] * len(reducers)

    try:
        _safe_report_progress(percent=15.0, message="Streaming native-reduced tiles", stage="stream")
        for tile_meta, tile_reduced in stream_tiles_v2(
            client,
            request,
            buffer_count=ctx["buffer_count"],
            poll_timeout_ms=ctx["poll_timeout_ms"],
        ):
            if _safe_is_cancelled():
                raise RuntimeError("Job cancelled while streaming native-reduced tiles.")
            if tile_meta.rank != 3:
                raise ValueError(f"NativeReduce helper expects rank=3 tiles, got rank={tile_meta.rank}.")
            if tile_reduced.ndim != 3:
                raise ValueError("NativeReduce helper expected ndarray shape [channel, height, width].")

            xoff = int(tile_meta.patch_col)
            yoff = int(tile_meta.patch_row)
            tw = int(tile_meta.width)
            th = int(tile_meta.height)
            write_w = min(tw, int(ctx["width"]) - xoff)
            write_h = min(th, int(ctx["height"]) - yoff)
            if write_w <= 0 or write_h <= 0:
                continue

            for band_index, out_band in enumerate(out_bands):
                channel = tile_reduced[band_index, :write_h, :write_w]
                if channel.dtype != ctx["gdal_np_dtype"]:
                    channel = channel.astype(ctx["gdal_np_dtype"], copy=False)
                if not channel.flags["C_CONTIGUOUS"]:
                    channel = np.ascontiguousarray(channel)
                out_band.WriteArray(channel, xoff=xoff, yoff=yoff)

                local_min = float(channel.min())
                local_max = float(channel.max())
                band_mins[band_index] = (
                    local_min if band_mins[band_index] is None else min(band_mins[band_index], local_min)
                )
                band_maxs[band_index] = (
                    local_max if band_maxs[band_index] is None else max(band_maxs[band_index], local_max)
                )

            tiles_written += 1
            if tiles_written % max(1, int(config.stream_progress_tile_interval)) == 0:
                _safe_report_progress(
                    percent=min(90.0, 15.0 + (tiles_written / 20.0)),
                    message=f"Wrote {tiles_written} tile(s)",
                    stage="stream",
                )
    finally:
        for band in out_bands:
            try:
                band.FlushCache()
            except Exception:
                pass
        out_ds.FlushCache()
        out_bands = []
        out_ds = None

    register_output_if_available(
        relative_path=ctx["output_rel"],
        kind=config.output_kind,
        subkind=config.output_subkind,
        render_mode=config.output_render_mode,
        metadata={
            "source_dem": ctx["dem_rel"],
            "horizons_relative_dir": ctx["horizons_rel"],
            "time_start_utc": ctx["time_start_utc"],
            "time_stop_utc": ctx["time_stop_utc"],
            "time_step_hours": ctx["time_step_hours"],
            "tiles_written": tiles_written,
            "band_mins": band_mins,
            "band_maxs": band_maxs,
            "output_dtype": str(ctx["gdal_np_dtype"]),
            "observer_elevation_meters": ctx["observer_elevation_meters"],
            "use_spice_sun_vectors": ctx["use_spice_sun_vectors"],
            "use_spice_earth_vectors": ctx["use_spice_earth_vectors"],
            "stream_mode": "native_reduce",
            "reducers": reducers,
        },
    )

    _safe_report_progress(percent=95.0, message="V2 native reduction complete", stage="finalize")
    return {
        "scenario_id": ctx["scenario_id"],
        "scenario_root": str(ctx["scenario_root"]),
        "dem_relative_path": ctx["dem_rel"],
        "horizons_relative_dir": ctx["horizons_rel"],
        "output_relative_path": ctx["output_rel"],
        "time_start_utc": ctx["time_start_utc"],
        "time_stop_utc": ctx["time_stop_utc"],
        "time_step_hours": ctx["time_step_hours"],
        "tiles_written": tiles_written,
        "band_mins": band_mins,
        "band_maxs": band_maxs,
        "reducer_count": len(reducers),
    }
