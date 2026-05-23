from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response

from backend.api.dependencies import ServiceContainer, get_services
from backend.core.config import ESRI_103878_PROJ4
from backend.core.config import ESRI_103878_WKT
from backend.core.config import load_app_config as core_load_app_config
from backend.core.config import repo_root as core_repo_root
from backend.core.config import resolve_config_path as core_resolve_config_path
from backend.core.config import resolve_config_relative_path as core_resolve_config_relative_path
from backend.contracts.models import (
    CreateLayerStateRequest,
    CreateScenarioRequest,
    ImportGeoTiffRequest,
    Job,
    Producer,
    RenderMode,
    UpdateLayerStateRequest,
)
from backend.services.colormap_support import (
    builtin_colormaps,
    normalize_colormap,
    read_colormap_file,
    resolve_colormap_registry,
    resolve_default_colormap_for_name,
)
from backend.services.raster_delivery import ensure_map_display_raster
from backend.services.vector_delivery import load_map_display_geojson
from backend.worker.gdal_runtime import configure_gdal_runtime, import_rasterio


DEFAULT_MOON_TREK_CAPABILITIES_URL = (
    "https://trek.nasa.gov/tiles/Moon/SP/LRO_WAC_Mosaic_SPole60_100mp/1.0.0/WMTSCapabilities.xml"
)
DEFAULT_MOON_TREK_LAYER = "LRO_WAC_Mosaic_SPole60_100mp"
DEFAULT_MOON_TREK_MATRIX_SET = "default028mm"


router = APIRouter(prefix="/api/v1/lunar-analyst", tags=["lunar-analyst"])


class ApplyDefaultColormapResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str
    scenario_id: str
    source_file_id: str
    selected_colormap: str
    matched_rule: str | None = None
    style: dict[str, Any] = Field(default_factory=dict)


class ExportRgbaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_relative_path: str | None = None
    overwrite_mode: str = "ask"


class ExportRgbaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_id: str
    scenario_id: str
    source_file_id: str
    job: Job


def _aux_xml_path_for_raster(path: Path) -> Path:
    return path.with_name(f"{path.name}.aux.xml")


def _read_aux_band1_stats(path: Path) -> tuple[float | None, float | None]:
    aux_path = _aux_xml_path_for_raster(path)
    if not aux_path.exists() or not aux_path.is_file():
        return None, None
    try:
        root = ET.fromstring(aux_path.read_text(encoding="utf-8"))
    except Exception:
        return None, None

    min_val: float | None = None
    max_val: float | None = None
    for band in root.findall(".//PAMRasterBand"):
        if str(band.attrib.get("band", "")).strip() != "1":
            continue
        for mdi in band.findall(".//MDI"):
            key = str(mdi.attrib.get("key", "")).strip()
            txt = (mdi.text or "").strip()
            try:
                if key == "STATISTICS_MINIMUM":
                    min_val = float(txt)
                elif key == "STATISTICS_MAXIMUM":
                    max_val = float(txt)
            except Exception:
                continue
    return min_val, max_val


def _write_aux_band1_stats(path: Path, *, min_val: float, max_val: float) -> None:
    aux_path = _aux_xml_path_for_raster(path)
    root: ET.Element
    try:
        if aux_path.exists() and aux_path.is_file():
            root = ET.fromstring(aux_path.read_text(encoding="utf-8"))
        else:
            root = ET.Element("PAMDataset")
    except Exception:
        root = ET.Element("PAMDataset")

    band = None
    for candidate in root.findall("PAMRasterBand"):
        if str(candidate.attrib.get("band", "")).strip() == "1":
            band = candidate
            break
    if band is None:
        band = ET.SubElement(root, "PAMRasterBand", {"band": "1"})

    metadata = band.find("Metadata")
    if metadata is None:
        metadata = ET.SubElement(band, "Metadata")

    def _set_mdi(key: str, value: str) -> None:
        mdi = None
        for candidate in metadata.findall("MDI"):
            if str(candidate.attrib.get("key", "")).strip() == key:
                mdi = candidate
                break
        if mdi is None:
            mdi = ET.SubElement(metadata, "MDI", {"key": key})
        mdi.text = value

    _set_mdi("STATISTICS_MINIMUM", f"{float(min_val):.17g}")
    _set_mdi("STATISTICS_MAXIMUM", f"{float(max_val):.17g}")

    tree = ET.ElementTree(root)
    tree.write(aux_path, encoding="utf-8", xml_declaration=True)


def _display_alpha_band(ds: Any) -> int | None:
    raw = str(ds.tags().get("LUNAR_DISPLAY_ALPHA_BAND", "")).strip()
    if not raw:
        return None
    try:
        alpha_band = int(raw)
    except ValueError:
        return None
    if 1 <= alpha_band <= int(ds.count):
        return alpha_band
    return None


def _repo_root() -> Path:
    return core_repo_root()


def _resolve_config_path() -> Path:
    return core_resolve_config_path()


def _resolve_config_relative_path(raw_path: str, config_path: Path) -> Path:
    return core_resolve_config_relative_path(raw_path, config_path=config_path)


def _load_toml_config() -> dict[str, Any]:
    return core_load_app_config()


def _load_ui_config() -> dict[str, Any]:
    data = _load_toml_config()
    backend = data.get("backend", {})
    if not isinstance(backend, dict):
        return {}
    ui_cfg = backend.get("lunar_analyst", {})
    if isinstance(ui_cfg, dict):
        return ui_cfg
    legacy_cfg = backend.get("map_milestone", {})
    if isinstance(legacy_cfg, dict):
        return legacy_cfg
    return {}


def _resolve_hillshade_path() -> Path:
    map_cfg = _load_ui_config()
    raw_path = map_cfg.get("hillshade_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "lunar_analyst_not_configured",
                "message": "Lunar Analyst hillshade path is not configured.",
                "details": {"config_key": "backend.lunar_analyst.hillshade_path"},
            },
        )
    config_path = _resolve_config_path()
    resolved = _resolve_config_relative_path(raw_path, config_path)
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "hillshade_not_found",
                "message": "Configured hillshade file was not found.",
                "details": {"hillshade_path": str(resolved)},
            },
        )
    return resolved


def _resolve_scenario_root_dir(map_cfg: dict[str, Any], hillshade_path: Path) -> Path:
    raw = map_cfg.get("scenario_root_dir")
    if isinstance(raw, str) and raw.strip():
        config_path = _resolve_config_path()
        return _resolve_config_relative_path(raw, config_path)
    return hillshade_path.parent


def _resolve_map_hillshade_path() -> Path:
    map_cfg = _load_ui_config()
    source_path = _resolve_hillshade_path()
    scenario_root_dir = _resolve_scenario_root_dir(map_cfg, source_path)
    product_id = str(map_cfg.get("hillshade_product_id", "lunar_analyst_hillshade"))
    kind = str(map_cfg.get("hillshade_kind", "lighting"))
    scenario_id = str(map_cfg.get("hillshade_scenario_id", "lunar_analyst"))
    source_file_id = str(map_cfg.get("hillshade_source_file_id", source_path.name))
    try:
        return ensure_map_display_raster(
            source_path=source_path,
            scenario_root_dir=scenario_root_dir,
            scenario_id=scenario_id,
            kind=kind,
            product_id=product_id,
            source_file_id=source_file_id,
            target_crs_wkt=ESRI_103878_WKT,
            target_crs_label="ESRI:103878",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "hillshade_warp_failed",
                "message": "Failed to produce map-display hillshade derivative.",
                "details": {"source_path": str(source_path), "error": str(exc)},
            },
        ) from exc


def _resolve_colormap_scenario_root(
    map_cfg: dict[str, Any],
    scenario_id: str | None,
    services: ServiceContainer | None,
) -> Path | None:
    if scenario_id and services is not None:
        try:
            return services.scenario_service.resolve_scenario_root(scenario_id)
        except Exception:
            return None
    if isinstance(map_cfg.get("scenario_root_dir"), str) and map_cfg.get("scenario_root_dir", "").strip():
        return _resolve_config_relative_path(str(map_cfg["scenario_root_dir"]), _resolve_config_path())
    try:
        return _resolve_hillshade_path().parent
    except HTTPException:
        return None


@router.get("/config")
def get_lunar_analyst_config() -> dict[str, Any]:
    map_cfg = _load_ui_config()
    hillshade_path = _resolve_hillshade_path()
    hillshade_opacity = map_cfg.get("hillshade_opacity", 0.7)
    if not isinstance(hillshade_opacity, (int, float)):
        hillshade_opacity = 0.7

    return {
        "projection": {
            "code": "ESRI:103878",
            "proj4": ESRI_103878_PROJ4,
            "extent": [-3040000, -3040000, 3040000, 3040000],
        },
        "moon_trek": {
            "capabilities_url": str(
                map_cfg.get("moon_trek_capabilities_url", DEFAULT_MOON_TREK_CAPABILITIES_URL)
            ),
            "layer": str(map_cfg.get("moon_trek_layer", DEFAULT_MOON_TREK_LAYER)),
            "tile_matrix_set": str(
                map_cfg.get("moon_trek_tile_matrix_set", DEFAULT_MOON_TREK_MATRIX_SET)
            ),
            "style": str(map_cfg.get("moon_trek_style", "default")),
        },
        "hillshade": {
            "url": "/api/v1/lunar-analyst/hillshade",
            "native_url": "/api/v1/lunar-analyst/hillshade/native",
            "opacity": max(0.0, min(1.0, float(hillshade_opacity))),
            "path": str(hillshade_path),
        },
        "view": {
            "center": [0, 0],
            "zoom": 2,
            "extra_zoom_levels": int(map_cfg.get("extra_zoom_levels", 14)),
        },
    }


@router.post("/bootstrap")
def bootstrap_lunar_analyst(
    scenario_id: str | None = None,
    services: ServiceContainer = Depends(get_services),
) -> dict[str, Any]:
    requested_scenario_id = str(scenario_id or "").strip()
    if requested_scenario_id:
        try:
            scenario = services.scenario_service.get_scenario(requested_scenario_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "scenario_not_found",
                    "message": "Scenario not found for requested URL selection.",
                    "details": {"scenario_id": requested_scenario_id},
                },
            ) from exc
        return {"scenario_id": scenario.scenario_id}

    map_cfg = _load_ui_config()
    hillshade_path = _resolve_hillshade_path()
    scenario_root = str(map_cfg.get("scenario_root_name", "lunar_analyst"))
    scenario_name = str(map_cfg.get("scenario_name", "Lunar Analyst"))
    scenario_owner = str(map_cfg.get("scenario_owner", "lunar"))
    layer_title = str(map_cfg.get("layer_title", "Hillshade Overlay"))

    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root=scenario_root,
            name=scenario_name,
            owner=scenario_owner,
        )
    )
    scenario_id = scenario.scenario_id

    source_path_s = str(hillshade_path.resolve())
    existing_product_id: str | None = None
    for product in services.product_service.list_products(scenario_id):
        source = product.lineage.get("import_source_path")
        if isinstance(source, str) and source == source_path_s:
            existing_product_id = product.product_id
            break

    if existing_product_id is None:
        product = services.scenario_service.import_geotiff(
            scenario_id,
            ImportGeoTiffRequest(
                source_path=source_path_s,
                kind=str(map_cfg.get("hillshade_kind", "lighting")),
                subkind=str(map_cfg.get("hillshade_subkind", "hillshade")),
                producer=Producer.IMPORT,
                bypass_cog=bool(map_cfg.get("bootstrap_bypass_cog", False)),
                lineage={"lunar_analyst_bootstrap": True},
            ),
        )
        product_id = product.product_id
    else:
        product_id = existing_product_id

    files = services.product_service.list_product_files(product_id)
    if not files:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "bootstrap_missing_product_file",
                "message": "Bootstrapped product has no registered files.",
                "details": {"product_id": product_id},
            },
        )
    source_file_id = files[-1].file_id
    layers = services.layer_service.list_layers(scenario_id)
    existing_layer = next((l for l in layers if l.source_file_id == source_file_id), None)

    if existing_layer is None:
        registry = resolve_colormap_registry(
            repo_root=_repo_root(),
            config_path=_resolve_config_path(),
            map_cfg=map_cfg,
            scenario_root=services.scenario_service.resolve_scenario_root(scenario_id),
        )
        selected_colormap, _matched_rule = resolve_default_colormap_for_name(
            file_name=Path(str(files[-1].relative_path)).name,
            colormaps=list(registry.get("colormaps", [])),
            rules=list(registry.get("rules", [])),
            fallback_default=str(registry.get("default", "gray")),
        )
        layer = services.layer_service.create_layer(
            CreateLayerStateRequest(
                scenario_id=scenario_id,
                product_id=product_id,
                title=layer_title,
                visible=True,
                opacity=float(map_cfg.get("hillshade_opacity", 0.7)),
                z_index=10,
                render_mode=RenderMode.RASTER,
                source_file_id=source_file_id,
                style={
                    "brightness": 0.0,
                    "contrast": 1.0,
                    "colormap": selected_colormap,
                },
            )
        )
    else:
        layer = existing_layer
        existing_style = dict(layer.style or {})
        if (
            existing_style.get("nodataCutoff") == 0.0
            and layer.render_mode == RenderMode.RASTER
        ):
            existing_style.pop("nodataCutoff", None)
            layer = services.layer_service.update_layer(
                layer.layer_id,
                UpdateLayerStateRequest(style=existing_style),
            )

    return {
        "scenario_id": scenario_id,
        "product_id": product_id,
        "source_file_id": source_file_id,
        "layer_id": layer.layer_id,
    }


@router.get("/colormaps")
def get_lunar_analyst_colormaps(
    scenario_id: str | None = None,
    services: ServiceContainer = Depends(get_services),
) -> dict[str, Any]:
    map_cfg = _load_ui_config()
    config_path = _resolve_config_path()
    scenario_root = _resolve_colormap_scenario_root(map_cfg, scenario_id, services)
    return resolve_colormap_registry(
        repo_root=_repo_root(),
        config_path=config_path,
        map_cfg=map_cfg,
        scenario_root=scenario_root,
    )


@router.post("/layers/{layer_id}/apply-default-colormap", response_model=ApplyDefaultColormapResponse)
def apply_default_colormap(
    layer_id: str,
    services: ServiceContainer = Depends(get_services),
) -> ApplyDefaultColormapResponse:
    layer = services.stores.layers.get(layer_id)
    if layer is None:
        raise HTTPException(status_code=404, detail={"code": "layer_not_found", "layer_id": layer_id})
    if layer.render_mode != RenderMode.RASTER:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "layer_not_raster",
                "message": "Apply default colormap is only valid for raster layers.",
                "layer_id": layer_id,
            },
        )
    file_record = services.product_service.get_file_record(str(layer.source_file_id))
    map_cfg = _load_ui_config()
    scenario_root = services.scenario_service.resolve_scenario_root(layer.scenario_id)
    registry = resolve_colormap_registry(
        repo_root=_repo_root(),
        config_path=_resolve_config_path(),
        map_cfg=map_cfg,
        scenario_root=scenario_root,
    )
    selected_colormap, matched_rule = resolve_default_colormap_for_name(
        file_name=Path(str(file_record.relative_path)).name,
        colormaps=list(registry.get("colormaps", [])),
        rules=list(registry.get("rules", [])),
        fallback_default=str(registry.get("default", "gray")),
    )
    style = dict(layer.style or {})
    style["colormap"] = selected_colormap
    updated = services.layer_service.update_layer(layer_id, UpdateLayerStateRequest(style=style))
    return ApplyDefaultColormapResponse(
        layer_id=updated.layer_id,
        scenario_id=updated.scenario_id,
        source_file_id=updated.source_file_id,
        selected_colormap=selected_colormap,
        matched_rule=matched_rule,
        style=dict(updated.style or {}),
    )


@router.post("/layers/{layer_id}/export-rgba", response_model=ExportRgbaResponse)
def export_layer_rgba(
    layer_id: str,
    request: ExportRgbaRequest,
    services: ServiceContainer = Depends(get_services),
) -> ExportRgbaResponse:
    layer = services.stores.layers.get(layer_id)
    if layer is None:
        raise HTTPException(status_code=404, detail={"code": "layer_not_found", "layer_id": layer_id})
    if layer.render_mode != RenderMode.RASTER:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "layer_not_raster",
                "message": "Export as RGBA GeoTIFF is only valid for raster layers.",
                "layer_id": layer_id,
            },
        )
    file_record = services.product_service.get_file_record(str(layer.source_file_id))
    params: dict[str, Any] = {
        "scenario_id": layer.scenario_id,
        "source_relative_path": str(file_record.relative_path),
        "style": dict(layer.style or {}),
        "overwrite_mode": str(request.overwrite_mode or "ask"),
    }
    if isinstance(request.output_relative_path, str) and request.output_relative_path.strip():
        params["output_relative_path"] = request.output_relative_path
    job = services.job_service.run_typed_job("ToolImplementations.export_colormap_rgba_geotiff", params)
    return ExportRgbaResponse(
        layer_id=layer.layer_id,
        scenario_id=layer.scenario_id,
        source_file_id=layer.source_file_id,
        job=job,
    )


@router.get("/hillshade")
def get_lunar_analyst_hillshade() -> FileResponse:
    path = _resolve_map_hillshade_path()
    return FileResponse(
        path,
        media_type="image/tiff",
        filename=path.name,
    )


@router.get("/hillshade/native")
def get_lunar_analyst_hillshade_native() -> FileResponse:
    path = _resolve_hillshade_path()
    return FileResponse(
        path,
        media_type="image/tiff",
        filename=path.name,
    )


@router.get("/files/{file_id}/raster")
def get_lunar_analyst_raster_file(
    file_id: str,
    services: ServiceContainer = Depends(get_services),
) -> FileResponse:
    source_path, record = services.product_service.resolve_file_path(file_id)
    suffix = source_path.suffix.lower()
    if suffix not in (".tif", ".tiff"):
        return FileResponse(
            source_path,
            media_type=record.media_type,
            filename=source_path.name,
        )

    product = services.product_service.get_product(record.product_id)
    display_path = ensure_map_display_raster(
        source_path=source_path,
        scenario_root_dir=record.scenario_root,
        scenario_id=record.scenario_id,
        kind=product.kind,
        product_id=record.product_id,
        source_file_id=file_id,
        target_crs_wkt=ESRI_103878_WKT,
        target_crs_label="ESRI:103878",
    )
    return FileResponse(
        display_path,
        media_type="image/tiff",
        filename=display_path.name,
    )


@router.get("/files/{file_id}/vector")
def get_lunar_analyst_vector_file(
    file_id: str,
    services: ServiceContainer = Depends(get_services),
) -> Response:
    configure_gdal_runtime()
    source_path, record = services.product_service.resolve_file_path(file_id)
    suffix = source_path.suffix.lower()
    if suffix not in (".geojson", ".json"):
        return FileResponse(
            source_path,
            media_type=record.media_type,
            filename=source_path.name,
        )

    try:
        payload = load_map_display_geojson(
            source_path=source_path,
            target_crs=ESRI_103878_WKT,
            target_crs_name="urn:ogc:def:crs:ESRI::103878",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_vector_geojson",
                "message": "Vector map delivery requires valid GeoJSON and CRS metadata.",
                "details": {"file_id": file_id, "path": str(source_path), "error": str(exc)},
            },
        ) from exc
    return JSONResponse(content=payload, media_type="application/geo+json")


@router.get("/files/{file_id}/raster-stats")
def get_lunar_analyst_raster_stats(
    file_id: str,
    services: ServiceContainer = Depends(get_services),
) -> dict[str, Any]:
    source_path, record = services.product_service.resolve_file_path(file_id)
    suffix = source_path.suffix.lower()
    if suffix not in (".tif", ".tiff"):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "not_raster_file",
                "message": "Requested file is not a GeoTIFF raster.",
                "details": {"file_id": file_id, "path": str(source_path)},
            },
        )

    product = services.product_service.get_product(record.product_id)
    display_path = ensure_map_display_raster(
        source_path=source_path,
        scenario_root_dir=record.scenario_root,
        scenario_id=record.scenario_id,
        kind=product.kind,
        product_id=record.product_id,
        source_file_id=file_id,
        target_crs_wkt=ESRI_103878_WKT,
        target_crs_label="ESRI:103878",
    )

    rasterio = import_rasterio()
    with rasterio.open(display_path) as ds:
        if ds.count < 1:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "raster_missing_bands",
                    "message": "Raster has no bands.",
                    "details": {"file_id": file_id, "path": str(display_path)},
                },
            )
        band = 1
        nodata = ds.nodata
        alpha_band = _display_alpha_band(ds)
        stats_min: float | None = None
        stats_max: float | None = None
        tags = ds.tags(band)
        try:
            if "STATISTICS_MINIMUM" in tags and "STATISTICS_MAXIMUM" in tags:
                stats_min = float(tags["STATISTICS_MINIMUM"])
                stats_max = float(tags["STATISTICS_MAXIMUM"])
        except Exception:
            stats_min = None
            stats_max = None

        if stats_min is None or stats_max is None or not stats_max > stats_min:
            aux_min, aux_max = _read_aux_band1_stats(display_path)
            if aux_min is not None and aux_max is not None and aux_max > aux_min:
                stats_min = aux_min
                stats_max = aux_max

        if stats_min is None or stats_max is None or not stats_max > stats_min:
            if alpha_band is not None:
                data = np.ma.array(
                    ds.read(band, masked=False),
                    mask=(ds.read(alpha_band, masked=False) == 0),
                )
            else:
                data = ds.read(band, masked=True)
            if int(data.count()) > 0:
                stats_min = float(data.min())
                stats_max = float(data.max())
                try:
                    _write_aux_band1_stats(display_path, min_val=stats_min, max_val=stats_max)
                except Exception:
                    # Sidecar cache write failure is non-fatal for stats delivery.
                    pass

        return {
            "file_id": file_id,
            "path": str(display_path),
            "dtype": ds.dtypes[0],
            "nodata": None if nodata is None else float(nodata),
            "min": stats_min,
            "max": stats_max,
            "crs": ds.crs.to_string() if ds.crs else None,
            "band_count": int(ds.count),
            "alpha_band": alpha_band,
        }
