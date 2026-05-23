from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .types import ScenarioRoot, UtcTimestamp


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolygonGeometry(StrictModel):
    type: str = Field(default="Polygon", pattern=r"^Polygon$")
    coordinates: list[list[list[float]]]


class Producer(str, Enum):
    IMPORT = "import"
    PYTHON_PIPELINE = "python_pipeline"
    NEW_HORIZON = "new_horizon"
    MANUAL = "manual"


class RenderMode(str, Enum):
    RASTER = "raster"
    VECTOR = "vector"


class JobMode(str, Enum):
    QUEUED = "queued"
    IMMEDIATE = "immediate"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Scenario(StrictModel):
    scenario_id: str
    scenario_root: ScenarioRoot
    name: str
    owner: str
    directory: str
    primary_dem_path: str = Field(pattern=r"^dem\.tif$")
    primary_dem_crs: str
    primary_dem_footprint: PolygonGeometry
    size_bytes: int = Field(ge=0)
    last_touched_utc: UtcTimestamp
    created_at_utc: UtcTimestamp
    updated_at_utc: UtcTimestamp


class Product(StrictModel):
    product_id: str
    scenario_id: str
    kind: str
    subkind: str
    producer: Producer
    crs: str
    footprint: PolygonGeometry
    created_at_utc: UtcTimestamp
    lineage: dict[str, Any] = Field(default_factory=dict)


class ProductFile(StrictModel):
    file_id: str
    product_id: str
    scenario_id: str
    relative_path: str
    media_type: str
    role: str
    created_at_utc: UtcTimestamp


class ExplorerNodeType(str, Enum):
    SCENARIO = "scenario"
    FOLDER = "folder"
    FILE = "file"
    COLLECTION = "collection"


class ExplorerNode(StrictModel):
    node_type: ExplorerNodeType
    name: str
    relative_path: str
    parent_relative_path: str | None = None
    is_renderable: bool
    is_hidden_default: bool
    product_id: str | None = None
    file_id: str | None = None
    kind: str | None = None
    subkind: str | None = None
    created_at_utc: UtcTimestamp | None = None
    modified_at_utc: UtcTimestamp | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    child_count: int | None = Field(default=None, ge=0)


class MoveScenarioPathRequest(StrictModel):
    source_relative_path: str = Field(min_length=1)
    target_relative_path: str = Field(min_length=1)


class MoveScenarioPathResponse(StrictModel):
    scenario_id: str
    status: str = Field(pattern=r"^(moved)$")
    source_relative_path: str
    target_relative_path: str
    moved_file_count: int = Field(ge=0)
    updated_layer_count: int = Field(ge=0)


class LayerState(StrictModel):
    layer_id: str
    scenario_id: str
    product_id: str | None = None
    title: str
    visible: bool
    opacity: float = Field(ge=0.0, le=1.0)
    z_index: int
    render_mode: RenderMode
    source_file_id: str
    style: dict[str, Any] = Field(default_factory=dict)
    updated_at_utc: UtcTimestamp


class Job(StrictModel):
    job_id: str
    scenario_id: str
    job_type: str
    mode: JobMode
    status: JobStatus
    params: dict[str, Any] = Field(default_factory=dict)
    requested_at_utc: UtcTimestamp
    started_at_utc: UtcTimestamp | None = None
    finished_at_utc: UtcTimestamp | None = None
    updated_at_utc: UtcTimestamp


class JobDefinitionType(str, Enum):
    NOTEBOOK = "notebook"
    NATIVE = "native"


class ToolVisibility(str, Enum):
    PUBLIC = "public"
    ADVANCED = "advanced"
    SYSTEM = "system"
    DRAFT = "draft"


class ToolConfirmationMode(str, Enum):
    NEVER = "never"
    ALWAYS = "always"
    CONDITIONAL = "conditional"


class ToolConfirmation(StrictModel):
    mode: ToolConfirmationMode = ToolConfirmationMode.NEVER
    action_type: str | None = None


class JobDefinitionParam(StrictModel):
    name: str
    type: str
    required: bool
    default: Any | None = None


class JobDefinition(StrictModel):
    job_definition_id: str
    job_type: JobDefinitionType
    title: str
    description: str = ""
    visibility: str = "default"
    tags: list[str] = Field(default_factory=list)
    handler_name: str
    implementation_name: str | None = None
    route_path: str
    params: list[JobDefinitionParam] = Field(default_factory=list)
    params_schema: dict[str, Any] = Field(default_factory=dict)
    outputs_schema: dict[str, Any] = Field(default_factory=dict)
    notebook_path: str | None = None
    notebook_hash: str | None = None


class JobDefinitionsResponse(StrictModel):
    definitions: list[JobDefinition] = Field(default_factory=list)


class ToolDefinition(StrictModel):
    tool_name: str
    title: str
    description: str = ""
    visibility: ToolVisibility = ToolVisibility.ADVANCED
    confirmation: ToolConfirmation = Field(default_factory=ToolConfirmation)
    tags: list[str] = Field(default_factory=list)
    handler_name: str
    implementation_name: str | None = None
    route_path: str
    params: list[JobDefinitionParam] = Field(default_factory=list)
    params_schema: dict[str, Any] = Field(default_factory=dict)
    outputs_schema: dict[str, Any] = Field(default_factory=dict)
    request_model_name: str | None = None
    response_model_name: str | None = None


class ToolDefinitionsResponse(StrictModel):
    definitions: list[ToolDefinition] = Field(default_factory=list)


class ToolInvocationRequest(StrictModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolRunResponse(StrictModel):
    tool_name: str
    job_id: str
    run_id: str
    job: Job
    result: dict[str, Any] = Field(default_factory=dict)


class JobEventName(str, Enum):
    JOB_QUEUED = "job_queued"
    JOB_STARTED = "job_started"
    JOB_PROGRESS = "job_progress"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    JOB_CANCELLED = "job_cancelled"
    LAYER_ADDED = "layer_added"
    LAYER_UPDATED = "layer_updated"
    LAYER_REMOVED = "layer_removed"
    MAP_ZOOM_REQUESTED = "map_zoom_requested"


class JobEvent(StrictModel):
    event_id: str
    job_id: str
    scenario_id: str
    event_name: JobEventName
    timestamp_utc: UtcTimestamp
    data: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(StrictModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str


class CreateScenarioRequest(StrictModel):
    scenario_root: ScenarioRoot
    name: str
    owner: str


class RegisterProductRequest(StrictModel):
    scenario_id: str
    kind: str
    subkind: str
    producer: Producer
    crs: str
    footprint: PolygonGeometry
    lineage: dict[str, Any] = Field(default_factory=dict)


class ImportGeoTiffRequest(StrictModel):
    source_path: str
    kind: str = "imports"
    subkind: str = "geotiff"
    bypass_cog: bool = False
    producer: Producer = Producer.IMPORT
    lineage: dict[str, Any] = Field(default_factory=dict)


class CreateLayerStateRequest(StrictModel):
    scenario_id: str
    product_id: str | None = None
    title: str
    visible: bool = True
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    z_index: int = 0
    render_mode: RenderMode
    source_file_id: str
    style: dict[str, Any] = Field(default_factory=dict)


class UpdateLayerStateRequest(StrictModel):
    title: str | None = None
    visible: bool | None = None
    opacity: float | None = Field(default=None, ge=0.0, le=1.0)
    z_index: int | None = None
    style: dict[str, Any] | None = None


class CreateNotebookSessionRequest(StrictModel):
    client_name: str = Field(min_length=1, max_length=64)


class NotebookSession(StrictModel):
    session_id: str
    api_token: str
    client_name: str
    created_at_utc: UtcTimestamp
    last_seen_at_utc: UtcTimestamp


class MarimoLaunchRequest(StrictModel):
    attach_url: str | None = None
    command: list[str] | None = None
    cwd: str | None = None
    scenario_id: str | None = None
    restart_if_running: bool = False


class MarimoStatus(StrictModel):
    status: str
    mode: str
    pid: int | None = None
    base_url: str | None = None
    log_path: str | None = None
    command: list[str] = Field(default_factory=list)
    cwd: str | None = None
    started_at_utc: UtcTimestamp | None = None


class MarimoOpenNotebookRequest(StrictModel):
    scenario_id: str = Field(min_length=1)
    relative_path: str | None = None
    create_new: bool = False
    restart_if_running: bool = True


class MarimoOpenNotebookResponse(StrictModel):
    status: str = Field(pattern=r"^(ready)$")
    scenario_id: str
    relative_path: str
    absolute_file_path: str
    file_url: str
    file_name: str
    notebook_capability: str = Field(pattern=r"^(marimo_notebook)$")
    created_new: bool = False
    modified_at_utc: UtcTimestamp | None = None


class ScenarioPythonEntry(StrictModel):
    scenario_id: str
    relative_path: str
    notebook_job_id: str
    entry_kind: str = Field(pattern=r"^(marimo_notebook|script)$")
    title: str


class CreateScenarioPythonFileRequest(StrictModel):
    kind: str = Field(pattern=r"^(notebook|script)$")


class ScenarioTextFileResponse(StrictModel):
    scenario_id: str
    relative_path: str
    file_name: str
    content: str
    entry_kind: str = Field(pattern=r"^(marimo_notebook|script)$")
    modified_at_utc: UtcTimestamp | None = None


class UpdateScenarioTextFileRequest(StrictModel):
    content: str


class ScenarioEditableFileResponse(StrictModel):
    scenario_id: str
    relative_path: str
    file_name: str
    content: str
    file_kind: str = Field(pattern=r"^(text|csv)$")
    modified_at_utc: UtcTimestamp | None = None


class ImagePixelSize(StrictModel):
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class ImageProjectedBounds(StrictModel):
    min_x: float
    min_y: float
    max_x: float
    max_y: float


class ImageLonLatBounds(StrictModel):
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


class ImageAffineTransform(StrictModel):
    a: float
    b: float
    c: float
    d: float
    e: float
    f: float


class ScenarioImageProjectionInfo(StrictModel):
    crs_authority: str | None = None
    crs_code: str | None = None
    name: str
    proj4: str | None = None


class ScenarioImageGeoreferencingInfo(StrictModel):
    is_georeferenced: bool
    pixel_origin: str = Field(pattern=r"^(upper_left)$")
    transform: ImageAffineTransform | None = None
    projection: ScenarioImageProjectionInfo | None = None
    bounds_projected: ImageProjectedBounds | None = None
    can_calculate_lonlat: bool = False
    geographic_crs_name: str | None = None
    geographic_crs_proj4: str | None = None
    lonlat_bounds: ImageLonLatBounds | None = None


class ScenarioImageMetadataResponse(StrictModel):
    scenario_id: str
    relative_path: str
    file_name: str
    media_type: str
    pixel_size: ImagePixelSize
    georeferencing: ScenarioImageGeoreferencingInfo
    modified_at_utc: UtcTimestamp | None = None


class ScenarioImageReadoutPixel(StrictModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    in_bounds: bool


class ScenarioImageProjectedReadout(StrictModel):
    available: bool
    crs_name: str | None = None
    easting: float | None = None
    northing: float | None = None


class ScenarioImageGeographicReadout(StrictModel):
    available: bool
    longitude: float | None = None
    latitude: float | None = None


class ScenarioImageReadoutResponse(StrictModel):
    scenario_id: str
    relative_path: str
    pixel: ScenarioImageReadoutPixel
    projected: ScenarioImageProjectedReadout
    geographic: ScenarioImageGeographicReadout


class LintScenarioPythonFileRequest(StrictModel):
    relative_path: str = Field(min_length=1)


class LintScenarioPythonFileResponse(StrictModel):
    scenario_id: str
    relative_path: str
    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int


class WorkspaceMessageEntry(StrictModel):
    entry_id: str
    scenario_id: str
    created_at_utc: UtcTimestamp
    level: str = Field(pattern=r"^(info|success|warning|error)$")
    source: str
    text: str


class WorkspaceMessageListResponse(StrictModel):
    entries: list[WorkspaceMessageEntry] = Field(default_factory=list)


class ZoomToFileMapCommandRequest(StrictModel):
    file_id: str = Field(min_length=1)
    padding_px: int | None = Field(default=None, ge=0)
    max_zoom: float | None = Field(default=None, ge=0.0)


class MapCommandQueuedResponse(StrictModel):
    status: str = Field(pattern=r"^(queued)$")
    event: str = Field(pattern=r"^(map_zoom_requested)$")


class ResolveHorizonSetRequest(StrictModel):
    dem_file_id: str
    attach_product: bool = True
    materialize: bool = False
    observer_height_m: float = Field(default=2.0, ge=0.0)
    azimuth_step_deg: float = Field(default=0.25, gt=0.0)
    algorithm_id: str = "new_horizon"
    algorithm_version: str = "v1"
    params: dict[str, Any] = Field(default_factory=dict)


class ResolveHorizonSetResponse(StrictModel):
    horizon_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str = Field(pattern=r"^(ready|building|queued)$")
    product_id: str | None = None
    reference_count: int = Field(ge=0)
    shared_storage_path: str | None = None


class HorizonSetStatusResponse(StrictModel):
    horizon_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    key_version: int = Field(ge=1)
    algorithm_id: str
    algorithm_version: str
    status: str = Field(pattern=r"^(building|ready|failed)$")
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    dem_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dem_crs: str
    reference_count: int = Field(ge=0)
    created_at_utc: UtcTimestamp
    updated_at_utc: UtcTimestamp


class HorizonSetDetachResponse(StrictModel):
    scenario_id: str
    product_id: str
    horizon_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str = Field(pattern=r"^(detached)$")


class DiscoverScenariosRequest(StrictModel):
    dry_run: bool = False
    include_existing: bool = False
    reconcile_missing: bool = False
    scenario_roots: list[str] | None = None


class ScenarioDiscoveryResult(StrictModel):
    scenario_root: str
    scenario_id: str | None = None
    status: str = Field(pattern=r"^(ingested|updated|skipped|forgotten|error)$")
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


class DiscoverScenariosResponse(StrictModel):
    workspace_root: str
    last_run_utc: UtcTimestamp
    discovered_count: int = Field(ge=0)
    ingested_count: int = Field(ge=0)
    updated_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    results: list[ScenarioDiscoveryResult] = Field(default_factory=list)


class DiscoveryStatusResponse(StrictModel):
    workspace_root: str
    last_run_utc: UtcTimestamp | None = None
    discovered_count: int = Field(ge=0)
    ingested_count: int = Field(ge=0)
    updated_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    results: list[ScenarioDiscoveryResult] = Field(default_factory=list)


class ReingestScenarioRequest(StrictModel):
    dry_run: bool = False


class ReingestScenarioResponse(StrictModel):
    scenario_id: str
    status: str = Field(pattern=r"^(updated|skipped|error)$")
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ForgetScenarioResponse(StrictModel):
    scenario_id: str
    status: str = Field(pattern=r"^(forgotten)$")
