from __future__ import annotations

import asyncio
import mimetypes
import logging
import inspect
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pyproj import CRS, Transformer

from backend.analyst_tools.catalog import get_tool_definition, list_tool_definitions
from backend.analyst_tools.client import LocalAnalystToolClient
from backend.api.dependencies import (
    MarimoLaunchConflictError,
    ServiceContainer,
    _append_workspace_message,
    _clear_workspace_messages,
    _read_workspace_messages,
    get_services,
)
from backend.api.job_runtime import build_job_router, discover_tool_implementations
from backend.core.config import ESRI_103878_WKT
from backend.core.config import load_app_config, repo_root, resolve_config_path
from backend.contracts.events import WsEnvelope
from backend.contracts.models import (
    CreateLayerStateRequest,
    CreateScenarioPythonFileRequest,
    ExplorerNode,
    DiscoverScenariosRequest,
    DiscoverScenariosResponse,
    DiscoveryStatusResponse,
    CreateNotebookSessionRequest,
    CreateScenarioRequest,
    ImportGeoTiffRequest,
    JobDefinitionsResponse,
    JobEvent,
    JobEventName,
    Job,
    LayerState,
    MarimoLaunchRequest,
    MarimoOpenNotebookRequest,
    MarimoOpenNotebookResponse,
    MarimoStatus,
    LintScenarioPythonFileRequest,
    LintScenarioPythonFileResponse,
    NotebookSession,
    MoveScenarioPathRequest,
    MoveScenarioPathResponse,
    ForgetScenarioResponse,
    HorizonSetDetachResponse,
    HorizonSetStatusResponse,
    ProductFile,
    Product,
    RegisterProductRequest,
    ResolveHorizonSetRequest,
    ResolveHorizonSetResponse,
    ReingestScenarioRequest,
    ReingestScenarioResponse,
    Scenario,
    ScenarioEditableFileResponse,
    ScenarioImageGeoreferencingInfo,
    ScenarioImageGeographicReadout,
    ScenarioImageMetadataResponse,
    ScenarioImageProjectionInfo,
    ScenarioImageProjectedReadout,
    ScenarioImageReadoutPixel,
    ScenarioImageReadoutResponse,
    ScenarioPythonEntry,
    ScenarioTextFileResponse,
    ImageAffineTransform,
    ImageLonLatBounds,
    ImagePixelSize,
    ImageProjectedBounds,
    ToolDefinition,
    ToolDefinitionsResponse,
    ToolInvocationRequest,
    ToolRunResponse,
    MapCommandQueuedResponse,
    ZoomToFileMapCommandRequest,
    UpdateLayerStateRequest,
    UpdateScenarioTextFileRequest,
    WorkspaceMessageEntry,
    WorkspaceMessageListResponse,
)
from backend.services.raster_delivery import ensure_map_display_raster
from backend.services.colormap_support import resolve_colormap_registry, resolve_default_colormap_for_name
from backend.services.assistant.product_describer import render_geotiff_preview_png
from backend.services.vector_delivery import LUNAR_GEOGRAPHIC_CRS
from backend.worker.gdal_runtime import import_rasterio
from backend.worker.native_bootstrap import (
    NativeBootstrapError,
    bootstrap_pythonnet,
    bootstrap_status,
)

router = APIRouter(prefix="/api/v1", tags=["v1"])
DEFAULT_MAP_ZOOM_PADDING_PX = 32
LUNAR_GEOGRAPHIC_CRS_NAME = "IAU Moon Geographic"
logger = logging.getLogger(__name__)


def _read_event_batch(events: Any, cursor: int) -> tuple[int, list[dict[str, Any]]]:
    reader = getattr(events, "read_since", None)
    if callable(reader):
        next_cursor, payloads = reader(cursor)
        return int(next_cursor), list(payloads)
    if cursor < 0:
        cursor = 0
    total = len(events)
    if cursor >= total:
        return total, []
    return total, list(events[cursor:])

async def _wait_event_batch(
    events: Any,
    cursor: int,
    *,
    timeout_seconds: float = 30.0,
) -> tuple[int, list[dict[str, Any]]]:
    waiter = getattr(events, "wait_for_events", None)
    if callable(waiter):
        next_cursor, payloads = await asyncio.to_thread(waiter, cursor, timeout_seconds)
        return int(next_cursor), list(payloads)
    return _read_event_batch(events, cursor)


def _image_pixel_size(path: Path) -> tuple[int, int]:
    try:
        rasterio = import_rasterio()
        with rasterio.open(path) as dataset:
            return int(dataset.width), int(dataset.height)
    except Exception:
        from PIL import Image

        with Image.open(path) as image:
            return int(image.width), int(image.height)


def _scenario_image_georef_payload(path: Path) -> tuple[int, int, ScenarioImageGeoreferencingInfo]:
    width, height = _image_pixel_size(path)
    try:
        rasterio = import_rasterio()
        with rasterio.open(path) as dataset:
            transform = dataset.transform
            crs = dataset.crs
            if not crs:
                return width, height, ScenarioImageGeoreferencingInfo(
                    is_georeferenced=False,
                    pixel_origin="upper_left",
                )

            projection_name = crs.to_string()
            authority = None
            code = None
            try:
                pyproj_crs = CRS.from_user_input(crs)
                projection_name = pyproj_crs.name or projection_name
                auth = pyproj_crs.to_authority()
                if auth:
                    authority, code = auth
            except Exception:
                pyproj_crs = None

            bounds_projected = ImageProjectedBounds(
                min_x=float(dataset.bounds.left),
                min_y=float(dataset.bounds.bottom),
                max_x=float(dataset.bounds.right),
                max_y=float(dataset.bounds.top),
            )

            lonlat_bounds = None
            can_calculate_lonlat = False
            try:
                transformer = Transformer.from_crs(crs, CRS.from_user_input(LUNAR_GEOGRAPHIC_CRS), always_xy=True)
                xs = [dataset.bounds.left, dataset.bounds.left, dataset.bounds.right, dataset.bounds.right]
                ys = [dataset.bounds.bottom, dataset.bounds.top, dataset.bounds.bottom, dataset.bounds.top]
                lons, lats = transformer.transform(xs, ys)
                lonlat_bounds = ImageLonLatBounds(
                    min_lon=float(min(lons)),
                    min_lat=float(min(lats)),
                    max_lon=float(max(lons)),
                    max_lat=float(max(lats)),
                )
                can_calculate_lonlat = True
            except Exception:
                lonlat_bounds = None

            return width, height, ScenarioImageGeoreferencingInfo(
                is_georeferenced=True,
                pixel_origin="upper_left",
                transform=ImageAffineTransform(
                    a=float(transform.a),
                    b=float(transform.b),
                    c=float(transform.c),
                    d=float(transform.d),
                    e=float(transform.e),
                    f=float(transform.f),
                ),
                projection=ScenarioImageProjectionInfo(
                    crs_authority=authority,
                    crs_code=code,
                    name=projection_name,
                    proj4=(str(crs.to_proj4()).strip() or None),
                ),
                bounds_projected=bounds_projected,
                can_calculate_lonlat=can_calculate_lonlat,
                geographic_crs_name=LUNAR_GEOGRAPHIC_CRS_NAME if can_calculate_lonlat else None,
                geographic_crs_proj4=LUNAR_GEOGRAPHIC_CRS if can_calculate_lonlat else None,
                lonlat_bounds=lonlat_bounds,
            )
    except Exception:
        return width, height, ScenarioImageGeoreferencingInfo(
            is_georeferenced=False,
            pixel_origin="upper_left",
        )


def _scenario_image_readout_payload(
    path: Path,
    *,
    pixel_x: int,
    pixel_y: int,
) -> tuple[int, int, ScenarioImageProjectedReadout, ScenarioImageGeographicReadout]:
    width, height = _image_pixel_size(path)
    in_bounds = 0 <= pixel_x < width and 0 <= pixel_y < height
    if not in_bounds:
        return width, height, ScenarioImageProjectedReadout(available=False), ScenarioImageGeographicReadout(available=False)

    try:
        rasterio = import_rasterio()
        with rasterio.open(path) as dataset:
            if not dataset.crs:
                return width, height, ScenarioImageProjectedReadout(available=False), ScenarioImageGeographicReadout(available=False)
            x, y = dataset.transform * (pixel_x + 0.5, pixel_y + 0.5)
            try:
                projection_name = CRS.from_user_input(dataset.crs).name
            except Exception:
                projection_name = dataset.crs.to_string()
            projected = ScenarioImageProjectedReadout(
                available=True,
                crs_name=projection_name,
                easting=float(x),
                northing=float(y),
            )
            try:
                transformer = Transformer.from_crs(dataset.crs, CRS.from_user_input(LUNAR_GEOGRAPHIC_CRS), always_xy=True)
                lon, lat = transformer.transform(float(x), float(y))
                geographic = ScenarioImageGeographicReadout(
                    available=True,
                    longitude=float(lon),
                    latitude=float(lat),
                )
            except Exception:
                geographic = ScenarioImageGeographicReadout(available=False)
            return width, height, projected, geographic
    except Exception:
        return width, height, ScenarioImageProjectedReadout(available=False), ScenarioImageGeographicReadout(available=False)


def _is_tiff_path(path: Path) -> bool:
    return path.suffix.lower() in {".tif", ".tiff"}


def _resolve_ws_cursor(raw_cursor: str | None, events: Any) -> int:
    value = str(raw_cursor or "").strip().lower()
    if value == "latest":
        return int(len(events))
    if not value:
        return 0
    try:
        parsed = int(value)
    except ValueError:
        return 0
    return max(0, parsed)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/native")
def native_health(probe: bool = False) -> dict[str, object]:
    diagnostics: dict[str, object] = {"status": "ok", "native": bootstrap_status()}
    if not probe:
        return diagnostics

    try:
        handle = bootstrap_pythonnet(force=False, verify_bridge_smoke=True)
        diagnostics["native"] = {
            "loaded": True,
            "runtime": handle.runtime,
            "moonlib_dll": str(handle.moonlib_dll),
            "dotnet_runtime_config": (
                str(handle.dotnet_runtime_config)
                if handle.dotnet_runtime_config is not None
                else None
            ),
            "expected_target_framework": handle.expected_target_framework,
            "smoke_check": handle.smoke_check,
        }
    except NativeBootstrapError as exc:
        diagnostics["status"] = "degraded"
        diagnostics["native_error"] = str(exc)
    return diagnostics


@router.post("/scenarios", response_model=Scenario)
def create_scenario(
    request: CreateScenarioRequest,
    services: ServiceContainer = Depends(get_services),
) -> Scenario:
    return services.scenario_service.create_scenario(request)


@router.get("/scenarios", response_model=list[Scenario])
def list_scenarios(services: ServiceContainer = Depends(get_services)) -> list[Scenario]:
    return services.scenario_service.list_scenarios()


@router.post("/scenarios:discover", response_model=DiscoverScenariosResponse)
def discover_scenarios(
    request: DiscoverScenariosRequest,
    services: ServiceContainer = Depends(get_services),
) -> DiscoverScenariosResponse:
    return services.scenario_service.discover_scenarios(request)


@router.get("/scenarios/discovery-status", response_model=DiscoveryStatusResponse)
def get_discovery_status(
    services: ServiceContainer = Depends(get_services),
) -> DiscoveryStatusResponse:
    return services.scenario_service.get_discovery_status()


@router.post(
    "/scenarios/{scenario_id}:reingest",
    response_model=ReingestScenarioResponse,
)
def reingest_scenario(
    scenario_id: str,
    request: ReingestScenarioRequest,
    services: ServiceContainer = Depends(get_services),
) -> ReingestScenarioResponse:
    return services.scenario_service.reingest_scenario(scenario_id, request)


@router.delete("/scenarios/{scenario_id}", response_model=ForgetScenarioResponse)
def forget_scenario(
    scenario_id: str,
    services: ServiceContainer = Depends(get_services),
) -> ForgetScenarioResponse:
    return services.scenario_service.forget_scenario(scenario_id)


@router.get("/scenarios/{scenario_id}", response_model=Scenario)
def get_scenario(
    scenario_id: str,
    services: ServiceContainer = Depends(get_services),
) -> Scenario:
    return services.scenario_service.get_scenario(scenario_id)


@router.post("/products", response_model=Product)
def register_product(
    request: RegisterProductRequest,
    services: ServiceContainer = Depends(get_services),
) -> Product:
    return services.product_service.register_product(request)


@router.get("/products/{product_id}", response_model=Product)
def get_product(
    product_id: str,
    services: ServiceContainer = Depends(get_services),
) -> Product:
    return services.product_service.get_product(product_id)


@router.get("/products/{product_id}/files", response_model=list[ProductFile])
def list_product_files(
    product_id: str,
    services: ServiceContainer = Depends(get_services),
) -> list[ProductFile]:
    return services.product_service.list_product_files(product_id)


@router.get("/scenarios/{scenario_id}/products", response_model=list[Product])
def list_products(
    scenario_id: str,
    services: ServiceContainer = Depends(get_services),
) -> list[Product]:
    return services.product_service.list_products(scenario_id)


@router.get("/scenarios/{scenario_id}/explorer-nodes", response_model=list[ExplorerNode])
def list_explorer_nodes(
    scenario_id: str,
    include_hidden: bool = False,
    services: ServiceContainer = Depends(get_services),
) -> list[ExplorerNode]:
    return services.product_service.list_explorer_nodes(
        scenario_id,
        include_hidden=include_hidden,
    )


@router.get("/scenarios/{scenario_id}/python-entries", response_model=list[ScenarioPythonEntry])
def list_scenario_python_entries(
    scenario_id: str,
    services: ServiceContainer = Depends(get_services),
) -> list[ScenarioPythonEntry]:
    return [
        ScenarioPythonEntry.model_validate(item)
        for item in services.notebook_job_service.list_scenario_python_entries(scenario_id)
    ]


def _resolve_python_entry_kind(
    services: ServiceContainer,
    *,
    scenario_id: str,
    relative_path: str,
) -> str:
    for item in services.notebook_job_service.list_scenario_python_entries(scenario_id):
        if str(item.get("relative_path", "")).lower() == relative_path.lower():
            return str(item.get("entry_kind", "script"))
    return "marimo_notebook" if relative_path.lower().endswith(".mo.py") else "script"


@router.post("/scenarios/{scenario_id}/python-files", response_model=ScenarioTextFileResponse)
def create_scenario_python_file(
    scenario_id: str,
    request: CreateScenarioPythonFileRequest,
    services: ServiceContainer = Depends(get_services),
) -> ScenarioTextFileResponse:
    path = services.scenario_service.create_scenario_python_file(scenario_id, kind=request.kind)
    scenario_root = services.scenario_service.resolve_scenario_root(scenario_id)
    relative_path = path.relative_to(scenario_root).as_posix()
    return ScenarioTextFileResponse(
        scenario_id=scenario_id,
        relative_path=relative_path,
        file_name=path.name,
        content=path.read_text(encoding="utf-8"),
        entry_kind=_resolve_python_entry_kind(
            services,
            scenario_id=scenario_id,
            relative_path=relative_path,
        ),
        modified_at_utc=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H-%M-%S"),
    )


@router.get("/scenarios/{scenario_id}/python-files", response_model=ScenarioTextFileResponse)
def read_scenario_python_file(
    scenario_id: str,
    relative_path: str,
    services: ServiceContainer = Depends(get_services),
) -> ScenarioTextFileResponse:
    path, content = services.scenario_service.read_scenario_text_file(scenario_id, relative_path)
    scenario_root = services.scenario_service.resolve_scenario_root(scenario_id)
    normalized_relative_path = path.relative_to(scenario_root).as_posix()
    return ScenarioTextFileResponse(
        scenario_id=scenario_id,
        relative_path=normalized_relative_path,
        file_name=path.name,
        content=content,
        entry_kind=_resolve_python_entry_kind(
            services,
            scenario_id=scenario_id,
            relative_path=normalized_relative_path,
        ),
        modified_at_utc=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H-%M-%S"),
    )


@router.put("/scenarios/{scenario_id}/python-files", response_model=ScenarioTextFileResponse)
def update_scenario_python_file(
    scenario_id: str,
    relative_path: str,
    request: UpdateScenarioTextFileRequest,
    services: ServiceContainer = Depends(get_services),
) -> ScenarioTextFileResponse:
    path = services.scenario_service.write_scenario_text_file(scenario_id, relative_path, request.content)
    scenario_root = services.scenario_service.resolve_scenario_root(scenario_id)
    normalized_relative_path = path.relative_to(scenario_root).as_posix()
    return ScenarioTextFileResponse(
        scenario_id=scenario_id,
        relative_path=normalized_relative_path,
        file_name=path.name,
        content=request.content,
        entry_kind=_resolve_python_entry_kind(
            services,
            scenario_id=scenario_id,
            relative_path=normalized_relative_path,
        ),
        modified_at_utc=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H-%M-%S"),
    )


@router.post("/scenarios/{scenario_id}/python-files:lint", response_model=LintScenarioPythonFileResponse)
def lint_scenario_python_file(
    scenario_id: str,
    request: LintScenarioPythonFileRequest,
    services: ServiceContainer = Depends(get_services),
) -> LintScenarioPythonFileResponse:
    path = services.scenario_service.resolve_scenario_text_file(scenario_id, request.relative_path)
    python_executable = services.notebook_job_service._python_executable()  # noqa: SLF001
    completed = subprocess.run(
        [python_executable, "-m", "py_compile", str(path)],
        cwd=str(path.parent),
        text=True,
        capture_output=True,
        check=False,
    )
    ok = completed.returncode == 0
    message_text = f"{request.relative_path}: lint {'passed' if ok else 'failed'}"
    if completed.stderr.strip():
        message_text = f"{message_text}\n{completed.stderr.strip()}"
    _append_workspace_message(
        services.stores.workspace_root,
        scenario_id=scenario_id,
        level="success" if ok else "error",
        source="python",
        text=message_text,
    )
    return LintScenarioPythonFileResponse(
        scenario_id=scenario_id,
        relative_path=request.relative_path,
        ok=ok,
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
    )


def _resolve_editable_file_kind(relative_path: str) -> str:
    suffix = relative_path.lower().rsplit(".", 1)[-1] if "." in relative_path else ""
    if suffix == "csv":
        return "csv"
    return "text"


@router.get("/scenarios/{scenario_id}/editable-files", response_model=ScenarioEditableFileResponse)
def read_scenario_editable_file(
    scenario_id: str,
    relative_path: str,
    services: ServiceContainer = Depends(get_services),
) -> ScenarioEditableFileResponse:
    path, content = services.scenario_service.read_scenario_editable_file(scenario_id, relative_path)
    scenario_root = services.scenario_service.resolve_scenario_root(scenario_id)
    normalized_relative_path = path.relative_to(scenario_root).as_posix()
    return ScenarioEditableFileResponse(
        scenario_id=scenario_id,
        relative_path=normalized_relative_path,
        file_name=path.name,
        content=content,
        file_kind=_resolve_editable_file_kind(normalized_relative_path),
        modified_at_utc=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H-%M-%S"),
    )


@router.put("/scenarios/{scenario_id}/editable-files", response_model=ScenarioEditableFileResponse)
def update_scenario_editable_file(
    scenario_id: str,
    relative_path: str,
    request: UpdateScenarioTextFileRequest,
    services: ServiceContainer = Depends(get_services),
) -> ScenarioEditableFileResponse:
    path = services.scenario_service.write_scenario_editable_file(scenario_id, relative_path, request.content)
    scenario_root = services.scenario_service.resolve_scenario_root(scenario_id)
    normalized_relative_path = path.relative_to(scenario_root).as_posix()
    return ScenarioEditableFileResponse(
        scenario_id=scenario_id,
        relative_path=normalized_relative_path,
        file_name=path.name,
        content=request.content,
        file_kind=_resolve_editable_file_kind(normalized_relative_path),
        modified_at_utc=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H-%M-%S"),
    )


@router.get("/scenarios/{scenario_id}/files:raw")
def get_scenario_raw_file(
    scenario_id: str,
    relative_path: str,
    services: ServiceContainer = Depends(get_services),
) -> FileResponse:
    path = services.scenario_service.resolve_scenario_file(scenario_id, relative_path)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/scenarios/{scenario_id}/image-preview")
def get_scenario_image_preview(
    scenario_id: str,
    relative_path: str,
    services: ServiceContainer = Depends(get_services),
) -> Response:
    path = services.scenario_service.resolve_scenario_file(scenario_id, relative_path)
    if _is_tiff_path(path):
        preview_bytes, _preview_meta = render_geotiff_preview_png(path)
        return Response(content=preview_bytes, media_type="image/png")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/scenarios/{scenario_id}/image-metadata", response_model=ScenarioImageMetadataResponse)
def get_scenario_image_metadata(
    scenario_id: str,
    relative_path: str,
    services: ServiceContainer = Depends(get_services),
) -> ScenarioImageMetadataResponse:
    path = services.scenario_service.resolve_scenario_file(scenario_id, relative_path)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    width, height, georeferencing = _scenario_image_georef_payload(path)
    return ScenarioImageMetadataResponse(
        scenario_id=scenario_id,
        relative_path=relative_path,
        file_name=path.name,
        media_type=media_type,
        pixel_size=ImagePixelSize(width=width, height=height),
        georeferencing=georeferencing,
        modified_at_utc=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H-%M-%S"),
    )


@router.get("/scenarios/{scenario_id}/image-readout", response_model=ScenarioImageReadoutResponse)
def get_scenario_image_readout(
    scenario_id: str,
    relative_path: str,
    pixel_x: int,
    pixel_y: int,
    services: ServiceContainer = Depends(get_services),
) -> ScenarioImageReadoutResponse:
    path = services.scenario_service.resolve_scenario_file(scenario_id, relative_path)
    width, height, projected, geographic = _scenario_image_readout_payload(
        path,
        pixel_x=pixel_x,
        pixel_y=pixel_y,
    )
    return ScenarioImageReadoutResponse(
        scenario_id=scenario_id,
        relative_path=relative_path,
        pixel=ScenarioImageReadoutPixel(
            x=pixel_x,
            y=pixel_y,
            in_bounds=0 <= pixel_x < width and 0 <= pixel_y < height,
        ),
        projected=projected,
        geographic=geographic,
    )


@router.get("/scenarios/{scenario_id}/messages", response_model=WorkspaceMessageListResponse)
def list_workspace_messages(
    scenario_id: str,
    services: ServiceContainer = Depends(get_services),
) -> WorkspaceMessageListResponse:
    entries = [
        WorkspaceMessageEntry.model_validate(item)
        for item in _read_workspace_messages(services.stores.workspace_root, scenario_id)
    ]
    return WorkspaceMessageListResponse(entries=entries)


@router.delete("/scenarios/{scenario_id}/messages", response_model=WorkspaceMessageListResponse)
def clear_workspace_messages(
    scenario_id: str,
    services: ServiceContainer = Depends(get_services),
) -> WorkspaceMessageListResponse:
    _clear_workspace_messages(services.stores.workspace_root, scenario_id)
    return WorkspaceMessageListResponse(entries=[])


@router.post(
    "/scenarios/{scenario_id}/paths:move",
    response_model=MoveScenarioPathResponse,
)
def move_scenario_path(
    scenario_id: str,
    request: MoveScenarioPathRequest,
    services: ServiceContainer = Depends(get_services),
) -> MoveScenarioPathResponse:
    try:
        return services.scenario_service.move_scenario_path(scenario_id, request)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "move_path_failed",
                "message": "Failed to move scenario path.",
                "details": {"error": str(exc)},
            },
        ) from exc


@router.post("/scenarios/{scenario_id}/imports/geotiff", response_model=Product)
def import_geotiff(
    scenario_id: str,
    request: ImportGeoTiffRequest,
    services: ServiceContainer = Depends(get_services),
) -> Product:
    return services.scenario_service.import_geotiff(scenario_id, request)


@router.get("/files/{file_id}")
def get_file(
    file_id: str,
    services: ServiceContainer = Depends(get_services),
) -> FileResponse:
    path, record = services.product_service.resolve_file_path(file_id)
    return FileResponse(path, media_type=record.media_type, filename=path.name)


@router.post(
    "/scenarios/{scenario_id}/horizon-sets:resolve",
    response_model=ResolveHorizonSetResponse,
)
def resolve_horizon_set(
    scenario_id: str,
    request: ResolveHorizonSetRequest,
    services: ServiceContainer = Depends(get_services),
) -> ResolveHorizonSetResponse:
    return services.shared_horizon_store_service.resolve(
        scenario_id=scenario_id,
        request=request,
    )


@router.get("/horizon-sets/{horizon_key}", response_model=HorizonSetStatusResponse)
def get_horizon_set(
    horizon_key: str,
    services: ServiceContainer = Depends(get_services),
) -> HorizonSetStatusResponse:
    return services.shared_horizon_store_service.inspect(horizon_key)


@router.delete(
    "/scenarios/{scenario_id}/horizon-sets/{product_id}",
    response_model=HorizonSetDetachResponse,
)
def detach_horizon_set(
    scenario_id: str,
    product_id: str,
    services: ServiceContainer = Depends(get_services),
) -> HorizonSetDetachResponse:
    return services.shared_horizon_store_service.detach(
        scenario_id=scenario_id,
        product_id=product_id,
    )


def _list_job_implementations_payload() -> dict[str, Any]:
    implementations = []
    for spec in discover_tool_implementations().values():
        params = []
        for param in spec.signature.parameters.values():
            annotation = (
                param.annotation.__name__
                if isinstance(param.annotation, type)
                else str(param.annotation)
            )
            if annotation.startswith("<class '") and annotation.endswith("'>"):
                annotation = annotation[8:-2]
            has_default = param.default is not inspect._empty
            default_value = None if not has_default else param.default
            if hasattr(default_value, "value"):
                default_value = default_value.value
            params.append(
                {
                    "name": param.name,
                    "type": annotation,
                    "required": not has_default,
                    "default": default_value,
                }
            )
        implementations.append(
            {
                "implementation_name": spec.implementation_name,
                "handler_name": spec.implementation_name,
                "route_path": f"/api/v1{spec.route_path}",
                "params": params,
            }
        )
    implementations.sort(key=lambda item: item["implementation_name"])
    return {"implementations": implementations, "handlers": implementations}


@router.get("/jobs/implementations")
def list_job_implementations() -> dict[str, Any]:
    return _list_job_implementations_payload()


@router.get("/jobs/handlers")
def list_job_handlers() -> dict[str, Any]:
    return _list_job_implementations_payload()


@router.get("/job-definitions", response_model=JobDefinitionsResponse)
def list_job_definitions(
    scenario_id: str | None = None,
    services: ServiceContainer = Depends(get_services),
) -> JobDefinitionsResponse:
    return services.notebook_job_service.list_job_definitions(scenario_id=scenario_id)


@router.get("/tools", response_model=ToolDefinitionsResponse)
def list_tools(
    include_drafts: bool = False,
    include_system: bool = True,
) -> ToolDefinitionsResponse:
    return list_tool_definitions(include_drafts=include_drafts, include_system=include_system)


@router.get("/tools/{tool_name}", response_model=ToolDefinition)
def get_tool(
    tool_name: str,
    include_drafts: bool = False,
    include_system: bool = True,
) -> ToolDefinition:
    try:
        return get_tool_definition(tool_name, include_drafts=include_drafts, include_system=include_system)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="tool_not_found") from exc


@router.post("/tools/{tool_name}/runs", response_model=ToolRunResponse)
def run_tool(
    tool_name: str,
    request: ToolInvocationRequest,
    services: ServiceContainer = Depends(get_services),
) -> ToolRunResponse:
    client = LocalAnalystToolClient(services)
    try:
        return client.invoke_tool(tool_name, request.arguments)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="tool_not_found") from exc


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(
    job_id: str,
    services: ServiceContainer = Depends(get_services),
) -> Job:
    return services.job_service.get_job(job_id)


@router.post("/jobs/{job_id}/cancel", response_model=Job)
def cancel_job(
    job_id: str,
    services: ServiceContainer = Depends(get_services),
) -> Job:
    return services.job_service.cancel_job(job_id)


@router.get("/jobs/{job_id}/events", response_model=list[JobEvent])
def list_job_events(
    job_id: str,
    services: ServiceContainer = Depends(get_services),
) -> list[JobEvent]:
    return services.job_service.list_job_events(job_id)


@router.get("/jobs/{job_id}/logs")
def get_job_logs(
    job_id: str,
    stream: str = "combined",
    head_lines: int = 0,
    tail_lines: int = 120,
    services: ServiceContainer = Depends(get_services),
) -> dict[str, Any]:
    return services.notebook_job_service.get_notebook_run_logs(
        run_id=job_id,
        stream=stream,
        head_lines=head_lines,
        tail_lines=tail_lines,
    )


def _layer_default_colormap(
    *,
    services: ServiceContainer,
    scenario_id: str,
    source_file_id: str,
) -> tuple[str, str | None]:
    file_record = services.product_service.get_file_record(source_file_id)
    scenario_root = services.scenario_service.resolve_scenario_root(scenario_id)
    app_cfg = load_app_config()
    backend = app_cfg.get("backend", {}) if isinstance(app_cfg, dict) else {}
    map_cfg = backend.get("lunar_analyst", {}) if isinstance(backend, dict) else {}
    if not isinstance(map_cfg, dict):
        map_cfg = {}
    registry = resolve_colormap_registry(
        repo_root=repo_root(),
        config_path=resolve_config_path(),
        map_cfg=map_cfg,
        scenario_root=scenario_root,
    )
    return resolve_default_colormap_for_name(
        file_name=Path(str(file_record.relative_path)).name,
        colormaps=list(registry.get("colormaps", [])),
        rules=list(registry.get("rules", [])),
        fallback_default=str(registry.get("default", "gray")),
    )


@router.post("/layers", response_model=LayerState)
def create_layer(
    request: CreateLayerStateRequest,
    services: ServiceContainer = Depends(get_services),
) -> LayerState:
    if request.render_mode == "raster":
        style = dict(request.style or {})
        if not str(style.get("colormap", "")).strip():
            colormap_id, _matched_rule = _layer_default_colormap(
                services=services,
                scenario_id=request.scenario_id,
                source_file_id=request.source_file_id,
            )
            style["colormap"] = colormap_id
            request = request.model_copy(update={"style": style})
    return services.layer_service.create_layer(request)


@router.patch("/layers/{layer_id}", response_model=LayerState)
def update_layer(
    layer_id: str,
    request: UpdateLayerStateRequest,
    services: ServiceContainer = Depends(get_services),
) -> LayerState:
    return services.layer_service.update_layer(layer_id, request)


@router.delete("/layers/{layer_id}")
def delete_layer(
    layer_id: str,
    services: ServiceContainer = Depends(get_services),
) -> dict[str, str]:
    services.layer_service.delete_layer(layer_id)
    return {"status": "deleted", "layer_id": layer_id}


@router.get("/scenarios/{scenario_id}/layers", response_model=list[LayerState])
def list_layers(
    scenario_id: str,
    services: ServiceContainer = Depends(get_services),
) -> list[LayerState]:
    layers = services.layer_service.list_layers(scenario_id)
    for layer in layers:
        source_file_id = str(layer.source_file_id)
        try:
            record = services.product_service.get_file_record(source_file_id)
            logger.info(
                "layer list diagnostic scenario_id=%s layer_id=%s layer_scenario_id=%s source_file_id=%s file_scenario_id=%s file_relative_path=%s",
                scenario_id,
                layer.layer_id,
                layer.scenario_id,
                source_file_id,
                record.scenario_id,
                record.relative_path,
            )
            if record.scenario_id != scenario_id:
                logger.warning(
                    "layer list diagnostic cross-scenario source detected request_scenario_id=%s layer_id=%s source_file_id=%s file_scenario_id=%s file_relative_path=%s",
                    scenario_id,
                    layer.layer_id,
                    source_file_id,
                    record.scenario_id,
                    record.relative_path,
                )
        except Exception as exc:
            logger.warning(
                "layer list diagnostic unresolved source request_scenario_id=%s layer_id=%s source_file_id=%s error=%s",
                scenario_id,
                layer.layer_id,
                source_file_id,
                exc,
            )
    return layers


@router.post(
    "/scenarios/{scenario_id}/map-commands/zoom-to-file",
    response_model=MapCommandQueuedResponse,
)
def queue_zoom_to_file_map_command(
    scenario_id: str,
    request: ZoomToFileMapCommandRequest,
    services: ServiceContainer = Depends(get_services),
) -> MapCommandQueuedResponse:
    scenario = services.scenario_service.get_scenario(scenario_id)
    record = services.product_service.get_file_record(request.file_id)
    if record.scenario_id != scenario_id:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "file_not_found_in_scenario",
                "message": "File was not found in the requested scenario.",
                "details": {
                    "scenario_id": scenario_id,
                    "file_id": request.file_id,
                },
            },
        )
    source_path, _ = services.product_service.resolve_file_path(request.file_id)
    product = services.product_service.get_product(record.product_id)
    try:
        display_path = ensure_map_display_raster(
            source_path=source_path,
            scenario_root_dir=record.scenario_root,
            scenario_id=scenario_id,
            kind=product.kind,
            product_id=record.product_id,
            source_file_id=request.file_id,
            target_crs_wkt=ESRI_103878_WKT,
            target_crs_label="ESRI:103878",
        )
        rasterio = import_rasterio()
        with rasterio.open(display_path) as dataset:
            bounds = dataset.bounds
            extent = [
                float(bounds.left),
                float(bounds.bottom),
                float(bounds.right),
                float(bounds.top),
            ]
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_map_zoom_file",
                "message": "Map zoom command requires a valid raster file.",
                "details": {
                    "scenario_id": scenario_id,
                    "file_id": request.file_id,
                    "error": str(exc),
                },
            },
        ) from exc

    padding_px = request.padding_px if request.padding_px is not None else DEFAULT_MAP_ZOOM_PADDING_PX
    payload_data: dict[str, Any] = {
        "scenario_id": scenario.scenario_id,
        "file_id": request.file_id,
        "extent": extent,
        "padding_px": padding_px,
    }
    if request.max_zoom is not None:
        payload_data["max_zoom"] = float(request.max_zoom)
    payload = WsEnvelope(
        event=JobEventName.MAP_ZOOM_REQUESTED,
        scenario_id=scenario.scenario_id,
        timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S"),
        data=payload_data,
    )
    services.stores.ws_events.append(payload.model_dump(mode="json"))
    return MapCommandQueuedResponse(status="queued", event="map_zoom_requested")


@router.websocket("/events")
async def ws_events(websocket: WebSocket) -> None:
    if not await _authorize_ws_if_required(websocket):
        return
    await websocket.accept()
    services = get_services()
    cursor = _resolve_ws_cursor(websocket.query_params.get("cursor"), services.stores.ws_events)
    try:
        while True:
            events = services.stores.ws_events
            cursor, payloads = await _wait_event_batch(events, cursor)
            for payload in payloads:
                await websocket.send_json(payload)
    except WebSocketDisconnect:
        return


@router.post("/notebook/sessions", response_model=NotebookSession)
def create_notebook_session(
    request: CreateNotebookSessionRequest,
    services: ServiceContainer = Depends(get_services),
) -> NotebookSession:
    return services.notebook_session_service.create_session(request.client_name)


@router.get("/notebook/sessions/{session_id}", response_model=NotebookSession)
def get_notebook_session(
    session_id: str,
    services: ServiceContainer = Depends(get_services),
) -> NotebookSession:
    return services.notebook_session_service.get_session(session_id)


@router.websocket("/notebook/events")
async def notebook_ws_events(websocket: WebSocket) -> None:
    if not await _authorize_ws_token(websocket, mandatory=True):
        return
    await websocket.accept()
    services = get_services()
    cursor = _resolve_ws_cursor(websocket.query_params.get("cursor"), services.stores.ws_events)
    try:
        while True:
            events = services.stores.ws_events
            cursor, payloads = await _wait_event_batch(events, cursor)
            for payload in payloads:
                await websocket.send_json(payload)
    except WebSocketDisconnect:
        return


@router.post("/marimo/launch", response_model=MarimoStatus)
def launch_marimo(
    request: MarimoLaunchRequest,
    services: ServiceContainer = Depends(get_services),
) -> MarimoStatus:
    try:
        return services.marimo_service.launch_or_attach(request)
    except MarimoLaunchConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "marimo_launch_conflict",
                "message": exc.message,
                "details": exc.details,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_marimo_launch", "message": str(exc), "details": {}},
        ) from exc


@router.get("/marimo/status", response_model=MarimoStatus)
def marimo_status(
    services: ServiceContainer = Depends(get_services),
) -> MarimoStatus:
    return services.marimo_service.status()


@router.post("/marimo/open-notebook", response_model=MarimoOpenNotebookResponse)
def open_marimo_notebook(
    request: MarimoOpenNotebookRequest,
    services: ServiceContainer = Depends(get_services),
) -> MarimoOpenNotebookResponse:
    try:
        return services.marimo_service.open_notebook(request)
    except MarimoLaunchConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "marimo_launch_conflict",
                "message": exc.message,
                "details": exc.details,
            },
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "notebook_target_not_found", "message": str(exc), "details": {}},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_notebook_target", "message": str(exc), "details": {}},
        ) from exc


@router.post("/marimo/stop")
def stop_marimo(
    services: ServiceContainer = Depends(get_services),
) -> dict[str, object]:
    stopped = services.marimo_service.stop_if_running()
    return {"status": "ok", "stopped": stopped}


async def _authorize_ws_if_required(websocket: WebSocket) -> bool:
    services = get_services()
    if services.notebook_session_service.is_auth_required():
        return await _authorize_ws_token(websocket, mandatory=True)
    return True


async def _authorize_ws_token(websocket: WebSocket, mandatory: bool) -> bool:
    services = get_services()
    token = websocket.query_params.get("token") or websocket.headers.get("x-lunar-session-token")
    session = services.notebook_session_service.validate_token(token)
    if session is None and mandatory:
        await websocket.close(code=4401, reason="Notebook session token is required.")
        return False
    return True


router.include_router(build_job_router())
