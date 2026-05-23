from __future__ import annotations

import dataclasses
from dataclasses import dataclass
import datetime as _dt
import json
import math
from pathlib import Path
import re
import time
from typing import Any, TYPE_CHECKING, Callable

import jsonschema

from backend.contracts.assistant_models import AssistantConfirmationActionType
from backend.contracts.events import WsEnvelope
from backend.contracts.models import (
    CreateLayerStateRequest,
    ImportGeoTiffRequest,
    JobDefinitionType,
    JobEventName,
    JobMode,
    JobStatus,
    ToolConfirmationMode,
    ToolDefinition,
    ToolVisibility,
    MoveScenarioPathRequest,
    RenderMode,
    UpdateLayerStateRequest,
)
from backend.core.config import ESRI_103878_WKT, repo_root, resolve_config_path, load_app_config
from backend.services.nomenclature_service import NomenclatureService
from backend.services.assistant.nomenclature_variants import resolve_feature_with_variants
from backend.services.colormap_support import (
    normalize_colormap,
    read_colormap_file,
    resolve_colormap_registry,
)
from backend.services.assistant.product_describer import (
    describe_geotiff,
    describe_geotiff_stats,
    describe_plot,
    describe_table,
    render_geotiff_preview_png,
)
from backend.services.assistant.tool_logs import (
    extract_completed_job_result as _tool_extract_completed_job_result,
    read_log_slice as _tool_read_log_slice,
)
from backend.services.assistant.tool_scenario_matching import (
    match_scenario as _tool_match_scenario,
    scenario_candidates_for_hint as _tool_scenario_candidates_for_hint,
    scenario_match_score as _tool_scenario_match_score,
)
from backend.services.assistant.tool_script_ops import (
    run_scenario_python_entry as _tool_run_scenario_python_entry,
    write_scenario_script as _tool_write_scenario_script,
)
from backend.services.assistant.tool_artifact_resolution import (
    find_file_id_for_path as _artifact_find_file_id_for_path,
    find_or_register_file_id_for_path as _artifact_find_or_register_file_id_for_path,
    guess_media_type_for_artifact_path as _artifact_guess_media_type_for_artifact_path,
    infer_scenario_id_for_path as _artifact_infer_scenario_id_for_path,
    is_path_within_root as _artifact_is_path_within_root,
    register_scenario_file_id_for_path as _artifact_register_scenario_file_id_for_path,
    resolve_artifact_identity as _artifact_resolve_artifact_identity,
    resolve_artifact_path as _artifact_resolve_artifact_path,
    resolve_relative_path as _artifact_resolve_relative_path,
)
from backend.services.assistant.tool_layer_resolution import (
    resolve_layer_id_by_name as _layer_resolve_layer_id_by_name,
)

if TYPE_CHECKING:
    from backend.api.dependencies import ServiceContainer


def _make_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return _make_jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _make_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_make_jsonable(item) for item in value]
    return str(value)


@dataclass(frozen=True)
class StaticAssistantToolSpec:
    description: str
    params_schema: dict[str, Any]
    confirmation_mode: ToolConfirmationMode = ToolConfirmationMode.NEVER
    action_type: AssistantConfirmationActionType | None = None


STATIC_ASSISTANT_TOOL_SPECS: dict[str, StaticAssistantToolSpec] = {
    "capabilities.describe": StaticAssistantToolSpec(
        description="Describe Lunar Analyst capabilities and common workflows.",
        params_schema={"type": "object", "additionalProperties": False},
    ),
    "tools.search": StaticAssistantToolSpec(
        description="Search tools by name/keywords and return compact tool metadata.",
        params_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "tools.describe": StaticAssistantToolSpec(
        description="Describe a specific tool by exact tool name, including argument schema.",
        params_schema={
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
            },
            "required": ["tool_name"],
            "additionalProperties": False,
        },
    ),
    "scenario.list": StaticAssistantToolSpec(
        description="List known scenarios.",
        params_schema={"type": "object", "additionalProperties": False},
    ),
    "scenario.get": StaticAssistantToolSpec(
        description="Get a scenario by scenario_id.",
        params_schema={
            "type": "object",
            "properties": {"scenario_id": {"type": "string"}},
            "required": ["scenario_id"],
            "additionalProperties": False,
        },
    ),
    "scenario.set_current": StaticAssistantToolSpec(
        description="Set the current scenario by id or flexible name match.",
        params_schema={
            "type": "object",
            "properties": {"scenario_ref": {"type": "string"}},
            "required": ["scenario_ref"],
            "additionalProperties": False,
        },
    ),
    "scenario.list_scripts": StaticAssistantToolSpec(
        description="List runnable Python scripts in a scenario.",
        params_schema={
            "type": "object",
            "properties": {"scenario_id": {"type": "string"}},
            "required": ["scenario_id"],
            "additionalProperties": False,
        },
    ),
    "scenario.list_notebooks": StaticAssistantToolSpec(
        description="List runnable Marimo notebooks in a scenario.",
        params_schema={
            "type": "object",
            "properties": {"scenario_id": {"type": "string"}},
            "required": ["scenario_id"],
            "additionalProperties": False,
        },
    ),
    "scenario.run_script": StaticAssistantToolSpec(
        description="Run a Python script in a scenario.",
        params_schema={
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string"},
                "relative_path": {"type": "string"},
                "runtime_mode": {"type": "string", "enum": ["osgeo", "moonlib"]},
            },
            "required": ["scenario_id", "relative_path"],
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        action_type=AssistantConfirmationActionType.LAUNCH_JOB,
    ),
    "scenario.run_marimo_notebook": StaticAssistantToolSpec(
        description="Run a Marimo notebook in a scenario.",
        params_schema={
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string"},
                "relative_path": {"type": "string"},
                "runtime_mode": {"type": "string", "enum": ["osgeo", "moonlib"]},
            },
            "required": ["scenario_id", "relative_path"],
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        action_type=AssistantConfirmationActionType.LAUNCH_JOB,
    ),
    "scenario.write_script": StaticAssistantToolSpec(
        description="Create or overwrite a Python script in a scenario.",
        params_schema={
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string"},
                "relative_path": {"type": "string"},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["scenario_id", "relative_path", "content"],
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.CONDITIONAL,
        action_type=AssistantConfirmationActionType.WRITE_NOTEBOOK,
    ),
    "scenario.write_run_script": StaticAssistantToolSpec(
        description="Create or overwrite a Python script in a scenario, then run it immediately.",
        params_schema={
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string"},
                "relative_path": {"type": "string"},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean"},
                "runtime_mode": {"type": "string", "enum": ["osgeo", "moonlib"]},
            },
            "required": ["scenario_id", "relative_path", "content"],
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.CONDITIONAL,
        action_type=AssistantConfirmationActionType.LAUNCH_JOB,
    ),
    "scenario.revoke_script_overwrite": StaticAssistantToolSpec(
        description="Revoke session-scoped overwrite approval for a script.",
        params_schema={
            "type": "object",
            "properties": {"scenario_id": {"type": "string"}, "relative_path": {"type": "string"}},
            "required": ["scenario_id", "relative_path"],
            "additionalProperties": False,
        },
    ),
    "scenario.rag_ingest": StaticAssistantToolSpec(
        description="Ingest git-managed RAG corpus files into the global retrieval index.",
        params_schema={
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string"},
                "relative_root": {"type": "string"},
                "rebuild": {"type": "boolean"},
                "respect_directives": {"type": "boolean"},
                "extensions": {"type": "array", "items": {"type": "string"}},
                "mode": {"type": "string", "enum": ["queued", "immediate"]},
            },
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        action_type=AssistantConfirmationActionType.LAUNCH_JOB,
    ),
    "jobs.list_predefined": StaticAssistantToolSpec(
        description="List predefined scenario-independent jobs.",
        params_schema={"type": "object", "additionalProperties": False},
    ),
    "jobs.run_predefined": StaticAssistantToolSpec(
        description="Run a predefined tool by implementation name and params.",
        params_schema={
            "type": "object",
            "properties": {
                "implementation_name": {"type": "string"},
                "handler_name": {"type": "string"},
                "params": {"type": "object"},
            },
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        action_type=AssistantConfirmationActionType.LAUNCH_JOB,
    ),
    "product.list": StaticAssistantToolSpec(
        description="List products for a scenario_id.",
        params_schema={
            "type": "object",
            "properties": {"scenario_id": {"type": "string"}},
            "required": ["scenario_id"],
            "additionalProperties": False,
        },
    ),
    "product.files": StaticAssistantToolSpec(
        description="List files for a product_id.",
        params_schema={
            "type": "object",
            "properties": {"product_id": {"type": "string"}},
            "required": ["product_id"],
            "additionalProperties": False,
        },
    ),
    "artifact.describe_geotiff": StaticAssistantToolSpec(
        description="Describe a GeoTIFF artifact and return metadata only.",
        params_schema={
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "path": {"type": "string"},
                "scenario_id": {"type": "string"},
                "relative_path": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ),
    "artifact.preview_geotiff": StaticAssistantToolSpec(
        description="Generate a preview image for a GeoTIFF artifact and return the generated preview artifact reference.",
        params_schema={
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "path": {"type": "string"},
                "scenario_id": {"type": "string"},
                "relative_path": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ),
    "artifact.stats_geotiff": StaticAssistantToolSpec(
        description="Compute summary statistics for a GeoTIFF artifact.",
        params_schema={
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "path": {"type": "string"},
                "scenario_id": {"type": "string"},
                "relative_path": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ),
    "artifact.describe_table": StaticAssistantToolSpec(
        description="Describe a tabular artifact (CSV) and return typed table preview outputs when available.",
        params_schema={
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "path": {"type": "string"},
                "scenario_id": {"type": "string"},
                "relative_path": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ),
    "artifact.describe_plot": StaticAssistantToolSpec(
        description="Describe an image/plot artifact and return typed plot outputs when available.",
        params_schema={
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "path": {"type": "string"},
                "scenario_id": {"type": "string"},
                "relative_path": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ),
    "runs.get_status": StaticAssistantToolSpec(
        description="Get status for a job id (run_id accepted for compatibility).",
        params_schema={
            "type": "object",
            "properties": {"job_id": {"type": "string"}, "run_id": {"type": "string"}},
            "additionalProperties": False,
        },
    ),
    "runs.get_logs": StaticAssistantToolSpec(
        description="Get job logs with configurable head/tail slices.",
        params_schema={
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "run_id": {"type": "string"},
                "head_lines": {"type": "integer"},
                "tail_lines": {"type": "integer"},
                "stream": {"type": "string", "enum": ["stdout", "stderr", "combined"]},
            },
            "additionalProperties": False,
        },
    ),
    "runs.cancel": StaticAssistantToolSpec(
        description="Cancel a running job id (run_id accepted for compatibility).",
        params_schema={
            "type": "object",
            "properties": {"job_id": {"type": "string"}, "run_id": {"type": "string"}},
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        action_type=AssistantConfirmationActionType.LAUNCH_JOB,
    ),
    "job.launch": StaticAssistantToolSpec(
        description="Launch an existing job by tool implementation name and params.",
        params_schema={
            "type": "object",
            "properties": {
                "implementation_name": {"type": "string"},
                "handler_name": {"type": "string"},
                "params": {"type": "object"},
            },
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        action_type=AssistantConfirmationActionType.LAUNCH_JOB,
    ),
    "job.cancel": StaticAssistantToolSpec(
        description="Cancel a running job by job_id.",
        params_schema={
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        action_type=AssistantConfirmationActionType.LAUNCH_JOB,
    ),
    "layer.list_visible": StaticAssistantToolSpec(
        description="List currently visible layers for a scenario.",
        params_schema={
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string"},
                "base_layer_visible": {"type": "boolean"},
                "base_layer_title": {"type": "string"},
            },
            "required": ["scenario_id"],
            "additionalProperties": False,
        },
    ),
    "scenario.import_geotiff": StaticAssistantToolSpec(
        description="Import a GeoTIFF into a scenario.",
        params_schema={
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string"},
                "source_path": {"type": "string"},
                "kind": {"type": "string"},
                "subkind": {"type": "string"},
                "bypass_cog": {"type": "boolean"},
            },
            "required": ["scenario_id", "source_path"],
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        action_type=AssistantConfirmationActionType.IMPORT_FILE,
    ),
    "scenario.move_path": StaticAssistantToolSpec(
        description="Move a scenario-relative path.",
        params_schema={
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string"},
                "source_relative_path": {"type": "string"},
                "target_relative_path": {"type": "string"},
            },
            "required": ["scenario_id", "source_relative_path", "target_relative_path"],
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        action_type=AssistantConfirmationActionType.MOVE_PATH,
    ),
    "layer.update_state": StaticAssistantToolSpec(
        description="Update layer visibility/opacity/style fields.",
        params_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string"},
                "scenario_id": {"type": "string"},
                "layer_name": {"type": "string"},
                "title": {"type": "string"},
                "visible": {"type": "boolean"},
                "opacity": {"type": "number"},
                "z_index": {"type": "integer"},
                "style": {"type": "object"},
            },
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        action_type=AssistantConfirmationActionType.UPDATE_LAYER_STATE,
    ),
    "colormap.list": StaticAssistantToolSpec(
        description="List known colormaps and default-colormap filename rules for a scenario.",
        params_schema={
            "type": "object",
            "properties": {"scenario_id": {"type": "string"}},
            "required": ["scenario_id"],
            "additionalProperties": False,
        },
    ),
    "layer.apply_colormap": StaticAssistantToolSpec(
        description="Apply a known colormap id to a target layer.",
        params_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string"},
                "scenario_id": {"type": "string"},
                "layer_name": {"type": "string"},
                "colormap": {"type": "string"},
            },
            "required": ["colormap"],
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        action_type=AssistantConfirmationActionType.UPDATE_LAYER_STATE,
    ),
    "colormap.create_simple": StaticAssistantToolSpec(
        description="Create a simple colormap definition object (continuous, discrete, threshold).",
        params_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "mode": {"type": "string", "enum": ["continuous", "discrete", "threshold", "cyclic"]},
                "stops": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "number"},
                            "color": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 3,
                                "maxItems": 4,
                            },
                        },
                        "required": ["value", "color"],
                        "additionalProperties": False,
                    },
                },
                "parameters": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                },
                "cyclic": {"type": "object"},
            },
            "required": ["id", "name", "mode", "stops"],
            "additionalProperties": False,
        },
    ),
    "colormap.save_scenario": StaticAssistantToolSpec(
        description="Save a colormap definition into the scenario-local colormap file.",
        params_schema={
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string"},
                "colormap": {"type": "object"},
            },
            "required": ["scenario_id", "colormap"],
            "additionalProperties": False,
        },
        confirmation_mode=ToolConfirmationMode.ALWAYS,
        action_type=AssistantConfirmationActionType.WRITE_NOTEBOOK,
    ),
    "location.search": StaticAssistantToolSpec(
        description="Fuzzy search lunar nomenclature and return scored candidate matches.",
        params_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "feature_type": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "location.goto": StaticAssistantToolSpec(
        description="Resolve an exact lunar feature name and queue a map zoom command to its point/region.",
        params_schema={
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string"},
                "name": {"type": "string"},
                "feature_id": {"type": "string"},
                "feature_type": {"type": "string"},
                "padding_px": {"type": "integer", "minimum": 0, "maximum": 2048},
                "max_zoom": {"type": "number"},
            },
            "required": ["scenario_id", "name"],
            "additionalProperties": False,
        },
    ),
    "location.identify": StaticAssistantToolSpec(
        description="Return named features near a projected point in ESRI:103878, optionally filtered by type and radius.",
        params_schema={
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "feature_type": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                "radius_m": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["x", "y"],
            "additionalProperties": False,
        },
    ),
    "location.pin_feature": StaticAssistantToolSpec(
        description="Pin a resolved nomenclature feature in the assistant session working set (session-scoped memory).",
        params_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "name": {"type": "string"},
                "feature_id": {"type": "string"},
                "feature_type": {"type": "string"},
            },
            "required": ["session_id", "name"],
            "additionalProperties": False,
        },
    ),
    "location.list_pins": StaticAssistantToolSpec(
        description="List session-scoped pinned nomenclature features.",
        params_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
    ),
    "location.set_layer_filter": StaticAssistantToolSpec(
        description="Set session-scoped nomenclature layer visibility and type filters.",
        params_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "visible": {"type": "boolean"},
                "types": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
    ),
}

TOOL_DESCRIPTIONS: dict[str, str] = {
    name: spec.description for name, spec in STATIC_ASSISTANT_TOOL_SPECS.items()
}
TOOL_ARGUMENT_SCHEMAS: dict[str, dict[str, Any]] = {
    name: spec.params_schema for name, spec in STATIC_ASSISTANT_TOOL_SPECS.items()
}
TOOL_CONFIRMATIONS: dict[str, tuple[ToolConfirmationMode, AssistantConfirmationActionType | None]] = {
    name: (spec.confirmation_mode, spec.action_type)
    for name, spec in STATIC_ASSISTANT_TOOL_SPECS.items()
    if spec.confirmation_mode != ToolConfirmationMode.NEVER or spec.action_type is not None
}


def _model_facing_description(tool_name: str, base_description: str) -> str:
    description = str(base_description or "").strip()
    if tool_name == "raster.calculate":
        return (
            description
            + " Use DSL expression variables that exactly match `inputs` keys. "
            + "For slope-threshold masks, prefer `inputs: {slope: {relative_path: 'slope.tif'}}` with expression `slope <= X`. "
            + "If user asks for non-selected pixels to be transparent, use `where(slope <= X, 1, nodata())`."
            + " Region utilities are available: `label_regions(mask[, cleanup_mode[, cleanup_iterations]])` (8-connected integer labels), `region_sizes(mask[, cleanup_mode[, cleanup_iterations]])` (component-size raster), `filter_regions_by_size(mask, threshold, comparator[, cleanup_mode[, cleanup_iterations]])` (shape-preserving filter from cleaned seeds; comparator is '>=' or '<='), and `find_borders(mask)` (inner border mask). Cleanup modes: none|erosion|opening."
            + " When user asks to add/show the result as a layer in the same step, set `publish_layer.enabled=true` and include `publish_layer.visible=true`."
        ).strip()
    if tool_name == "raster.transform":
        return (
            description
            + " Prefer this as the canonical multi-step raster authoring surface."
            + " Always assign the final output to `result`."
            + " Use elementwise boolean operators with parentheses (`&`, `|`, `~`) instead of `and/or/not`."
            + " `np.where(...)` is accepted as an alias for `where(...)`."
            + " For temporal transforms, prefer reserved `inputs.times` with start_utc/stop_utc/step_hours and temporal inputs via `temporal_source` + `times='times'`."
            + " Treat legacy `signal` and top-level `time_*` fields as compatibility-only."
        ).strip()
    if tool_name == "layer.update_state":
        return (
            description
            + " Use this to turn on/off or style existing layers by `layer_id`, `layer_name`, or `title`; avoid read-only inventory loops when direct update is requested."
        ).strip()
    if tool_name == "scenario.import_geotiff":
        return (
            description
            + " Import only when introducing a new GeoTIFF source; do not re-import an already imported product just to change visibility/style."
        ).strip()
    if tool_name == "colormap.create_simple":
        return (
            description
            + " For discrete interval palettes, provide explicit hard-step stop pairs."
        ).strip()
    if tool_name == "layer.apply_colormap":
        return (
            description
            + " Prefer this over generic style mutation when the task is just choosing a known colormap."
        ).strip()
    return description


def _assert_static_tool_contracts() -> None:
    for tool_name, spec in STATIC_ASSISTANT_TOOL_SPECS.items():
        schema = spec.params_schema
        if not isinstance(schema, dict):
            raise RuntimeError(f"Assistant tool schema must be an object: {tool_name}")
        if schema.get("type") != "object":
            raise RuntimeError(f"Assistant tool schema must declare type=object: {tool_name}")
        if "additionalProperties" not in schema:
            raise RuntimeError(f"Assistant tool schema must declare additionalProperties: {tool_name}")
        # Validate schema shape up front so model/tool execution cannot diverge at runtime.
        jsonschema.Draft202012Validator.check_schema(schema)


_assert_static_tool_contracts()


def _canonical_tool_definition(tool_name: str) -> ToolDefinition | None:
    from backend.analyst_tools.catalog import get_tool_definition as get_canonical_tool_definition

    try:
        return get_canonical_tool_definition(
            tool_name,
            include_drafts=True,
            include_system=True,
        )
    except KeyError:
        return None


def _assistant_action_type_from_value(value: str | None) -> AssistantConfirmationActionType | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return AssistantConfirmationActionType(raw)
    except ValueError:
        return None


def _confirmation_for_tool(
    tool_name: str,
) -> tuple[ToolConfirmationMode, AssistantConfirmationActionType | None]:
    static = TOOL_CONFIRMATIONS.get(tool_name)
    if static is not None:
        return static
    definition = _canonical_tool_definition(tool_name)
    if definition is None:
        return ToolConfirmationMode.NEVER, None
    return (
        definition.confirmation.mode,
        _assistant_action_type_from_value(definition.confirmation.action_type),
    )


def action_type_for_tool(tool_name: str) -> AssistantConfirmationActionType | None:
    _mode, action_type = _confirmation_for_tool(tool_name)
    return action_type


def tool_argument_schema_for_model(tool_name: str) -> dict[str, Any]:
    """Return model-facing tool schema (can be narrower than canonical execution schema)."""
    if tool_name == "raster.calculate":
        return {
            "type": "object",
            "properties": {
                "scenario_id": {"type": "string"},
                "expression": {"type": "string"},
                "inputs": {
                    "type": "object",
                    "minProperties": 1,
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "relative_path": {"type": "string"},
                            "product_id": {"type": "string"},
                            "signal": {
                                "type": "string",
                                "enum": [
                                    "lighting_raster",
                                    "earth_above_horizon",
                                    "sun_above_horizon",
                                ],
                            },
                        },
                        "additionalProperties": False,
                    },
                },
                "output_relative_path": {"type": "string"},
                "overwrite_mode": {"type": "string", "enum": ["ask", "never", "always"]},
                "resampling": {"type": "string", "enum": ["nearest", "bilinear", "cubic"]},
                "mode": {"type": "string", "enum": ["queued", "immediate"]},
                "publish_layer": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "title": {"type": "string"},
                        "visible": {"type": "boolean"},
                        "opacity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "z_index": {"type": "integer"},
                        "style": {"type": "object"},
                        "on_existing": {"type": "string", "enum": ["update", "error", "new"]},
                        "transparent_background": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["scenario_id", "expression", "inputs"],
            "additionalProperties": False,
        }
    if tool_name == "raster.transform":
        schema = dict(tool_argument_schema_for_tool(tool_name))
        properties = schema.get("properties", {})
        if isinstance(properties, dict) and "overwrite" in properties:
            properties = dict(properties)
            properties.pop("overwrite", None)
            schema["properties"] = properties
        return schema
    return tool_argument_schema_for_tool(tool_name)


def tool_argument_schema_for_tool(tool_name: str) -> dict[str, Any]:
    definition = _canonical_tool_definition(tool_name)
    if definition is not None:
        return dict(definition.params_schema)
    static = TOOL_ARGUMENT_SCHEMAS.get(tool_name)
    if static is not None:
        return dict(static)
    raise KeyError(f"No tool argument schema registered for assistant tool: {tool_name}")


def _validate_tool_call_arguments(tool_name: str, arguments: dict[str, Any]) -> None:
    schema = tool_argument_schema_for_tool(tool_name)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(arguments), key=lambda err: list(err.absolute_path))
    if not errors:
        return
    first = errors[0]
    path_bits = [str(part) for part in first.absolute_path]
    arg_path = ".".join(path_bits) if path_bits else "$"
    raise ValueError(
        f"Invalid arguments for {tool_name} at {arg_path}: {first.message}"
    )


def _assistant_catalog_tools() -> list[ToolDefinition]:
    from backend.analyst_tools.catalog import list_tool_definitions

    response = list_tool_definitions(include_drafts=False, include_system=False)
    return [
        definition
        for definition in response.definitions
        if definition.visibility in {ToolVisibility.PUBLIC, ToolVisibility.ADVANCED}
    ]


def _static_tool_entry(name: str, description: str) -> dict[str, Any]:
    confirmation_mode, action_type = _confirmation_for_tool(name)
    return {
        "name": name,
        "description": _model_facing_description(name, description),
        "requires_confirmation": confirmation_mode != ToolConfirmationMode.NEVER,
        "confirmation_mode": confirmation_mode.value,
        "action_type": action_type.value if action_type is not None else None,
    }


def _canonical_tool_entry(definition: ToolDefinition) -> dict[str, Any]:
    confirmation_mode, action_type = _confirmation_for_tool(definition.tool_name)
    description = _model_facing_description(definition.tool_name, definition.description or definition.title)
    return {
        "name": definition.tool_name,
        "description": description,
        "requires_confirmation": confirmation_mode != ToolConfirmationMode.NEVER,
        "confirmation_mode": confirmation_mode.value,
        "action_type": action_type.value if action_type is not None else None,
        "visibility": definition.visibility.value,
        "implementation_name": definition.implementation_name or definition.handler_name,
        "handler_name": definition.handler_name,
        "tags": list(definition.tags),
    }


def list_tools_schema() -> list[dict[str, Any]]:
    payload: dict[str, dict[str, Any]] = {}
    for name, description in sorted(TOOL_DESCRIPTIONS.items()):
        payload[name] = _static_tool_entry(name, description)
    for definition in _assistant_catalog_tools():
        payload[definition.tool_name] = _canonical_tool_entry(definition)
    return [payload[name] for name in sorted(payload)]


def list_tools_schema_filtered(*, selected_tool_names: set[str] | None = None) -> list[dict[str, Any]]:
    tools = list_tools_schema()
    if not selected_tool_names:
        return tools
    selected = {str(name).strip() for name in selected_tool_names if str(name).strip()}
    return [item for item in tools if str(item.get("name", "")).strip() in selected]


def list_tools_for_model(*, selected_tool_names: set[str] | None = None) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    schema_entries = {item["name"]: item for item in list_tools_schema_filtered(selected_tool_names=selected_tool_names)}
    for name, entry in sorted(schema_entries.items()):
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(entry.get("description", "")).strip(),
                    "parameters": tool_argument_schema_for_model(name),
                },
            }
        )
    return tools


def select_tool_names_for_prompt(
    *,
    prompt: str,
    max_tools: int = 18,
) -> set[str]:
    query = str(prompt or "").lower()
    query_tokens = {token for token in re.findall(r"[a-z0-9_\.]+", query) if len(token) >= 2}
    all_tools = list_tools_schema()
    if not query_tokens:
        return {
            "capabilities.describe",
            "tools.search",
            "tools.describe",
            "layer.update_state",
            "product.list",
            "product.files",
            "scenario.import_geotiff",
            "raster.calculate",
            "raster.transform",
            "runs.get_status",
        }
    scores: list[tuple[float, str]] = []
    for item in all_tools:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        description = str(item.get("description", "")).lower()
        name_lower = name.lower()
        score = 0.0
        if name_lower in query:
            score += 1000.0
        name_tokens = {token for token in re.split(r"[^a-z0-9_]+", name_lower) if token}
        overlap = query_tokens & name_tokens
        score += float(len(overlap) * 90)
        for token in query_tokens:
            if token in description:
                score += 6.0
        if "." in name_lower:
            namespace = name_lower.split(".", 1)[0]
            if namespace in query_tokens:
                score += 25.0
        if score > 0.0:
            scores.append((score, name))
    scores.sort(key=lambda item: (-item[0], item[1]))
    selected: set[str] = {
        "capabilities.describe",
        "tools.search",
        "tools.describe",
        "layer.update_state",
        "runs.get_status",
    }
    cap = max(8, int(max_tools))
    for _score, name in scores:
        selected.add(name)
        if len(selected) >= cap:
            break
    return selected


def capabilities_text() -> str:
    return (
        "Lunar Analyst can manage scenarios, import and catalog geospatial products, render map layers, "
        "launch typed compute jobs, stream job events, and inspect artifacts (GeoTIFFs, tables, plots). "
        "Use `tools.search` for keyword lookup and `tools.describe` for exact tool contract/schema lookup when needed. "
        "Use metadata tools, preview tools, and stats tools deliberately rather than expecting one tool to do all three. "
        "If an inspection tool exists, use it instead of describing an artifact from memory. "
        "If no typed job exists and script tools are available, prefer `scenario.write_run_script` over separate write "
        "and run calls, then inspect the resulting artifacts. Scenario scripts run with the scenario root as the "
        "working directory, so prefer scenario-relative paths instead of absolute local paths. New user-requested "
        "outputs should default to the scenario top-level directory unless the user asks for a different location."
    )


def _capabilities_payload() -> dict[str, Any]:
    tools = list_tools_schema()
    tool_names = [
        str(item.get("name", "")).strip()
        for item in tools
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]
    return {
        "text": capabilities_text(),
        "tool_count": len(tool_names),
        "tool_names": tool_names,
        "tools": [
            {
                "name": str(item.get("name", "")).strip(),
                "requires_confirmation": bool(item.get("requires_confirmation", False)),
                "action_type": item.get("action_type"),
            }
            for item in tools
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ],
    }


ToolExecutor = Callable[["ServiceContainer", dict[str, Any]], dict[str, Any]]


def _tool_capabilities_describe(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    _ = services, arguments
    return _capabilities_payload()


def _tool_tools_search(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    _ = services
    query = str(arguments.get("query", "")).strip().lower()
    if not query:
        raise ValueError("query is required")
    limit = int(arguments.get("limit", 10) or 10)
    limit = max(1, min(50, limit))
    query_tokens = {token for token in re.findall(r"[a-z0-9_\.]+", query) if len(token) >= 2}
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in list_tools_schema():
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        description = str(item.get("description", "")).lower()
        name_lower = name.lower()
        score = 0.0
        if query in name_lower:
            score += 1000.0
        for token in query_tokens:
            if token in name_lower:
                score += 90.0
            if token in description:
                score += 6.0
        if score <= 0.0:
            continue
        scored.append((score, item))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("name", ""))))
    items = [
        {
            "name": str(item.get("name", "")).strip(),
            "description": str(item.get("description", "")).strip(),
            "requires_confirmation": bool(item.get("requires_confirmation", False)),
            "action_type": item.get("action_type"),
        }
        for _score, item in scored[:limit]
    ]
    return {
        "query": query,
        "count": len(items),
        "items": items,
    }


def _tool_tools_describe(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    _ = services
    tool_name_arg = str(arguments.get("tool_name", "")).strip()
    if not tool_name_arg:
        raise ValueError("tool_name is required")
    schema = tool_argument_schema_for_tool(tool_name_arg)
    meta = next(
        (
            item
            for item in list_tools_schema()
            if str(item.get("name", "")).strip() == tool_name_arg
        ),
        None,
    )
    if meta is None:
        raise KeyError(f"Unknown tool: {tool_name_arg}")
    return {
        "tool_name": tool_name_arg,
        "description": str(meta.get("description", "")).strip(),
        "requires_confirmation": bool(meta.get("requires_confirmation", False)),
        "action_type": meta.get("action_type"),
        "parameters": schema,
    }


def _tool_scenario_list(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    _ = arguments
    items = [item.model_dump(mode="json") for item in services.scenario_service.list_scenarios()]
    return {"items": items, "count": len(items)}


def _tool_scenario_get(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = str(arguments.get("scenario_id", "")).strip()
    if not scenario_id:
        raise ValueError("scenario_id is required")
    return services.scenario_service.get_scenario(scenario_id).model_dump(mode="json")


def _tool_scenario_set_current(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scenario_ref = str(arguments.get("scenario_ref", "")).strip()
    if not scenario_ref:
        raise ValueError("scenario_ref is required")
    return _match_scenario(services, scenario_ref)


def _tool_scenario_list_scripts(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = str(arguments.get("scenario_id", "")).strip()
    if not scenario_id:
        raise ValueError("scenario_id is required")
    items = services.notebook_job_service.list_scenario_python_entries(scenario_id)
    scripts = [item for item in items if item.get("entry_kind") == "script"]
    return {"scenario_id": scenario_id, "items": scripts, "count": len(scripts)}


def _tool_scenario_list_notebooks(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = str(arguments.get("scenario_id", "")).strip()
    if not scenario_id:
        raise ValueError("scenario_id is required")
    items = services.notebook_job_service.list_scenario_python_entries(scenario_id)
    notebooks = [item for item in items if item.get("entry_kind") == "marimo_notebook"]
    return {"scenario_id": scenario_id, "items": notebooks, "count": len(notebooks)}


def _tool_scenario_revoke_script_overwrite(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    _ = services, arguments
    return {
        "revoked": False,
        "message": "Session-scoped overwrite revocation is available through assistant session runtime only.",
    }


def _tool_scenario_run_script(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return _run_scenario_python_entry(
        services,
        scenario_id=str(arguments.get("scenario_id", "")).strip(),
        relative_path=str(arguments.get("relative_path", "")).strip(),
        expect_marimo=False,
        runtime_mode=str(arguments.get("runtime_mode", "osgeo")).strip(),
    )


def _tool_scenario_run_marimo_notebook(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return _run_scenario_python_entry(
        services,
        scenario_id=str(arguments.get("scenario_id", "")).strip(),
        relative_path=str(arguments.get("relative_path", "")).strip(),
        expect_marimo=True,
        runtime_mode=str(arguments.get("runtime_mode", "osgeo")).strip(),
    )


def _tool_scenario_write_script(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return _write_scenario_script(
        services,
        scenario_id=str(arguments.get("scenario_id", "")).strip(),
        relative_path=str(arguments.get("relative_path", "")).strip(),
        content=str(arguments.get("content", "")),
        overwrite=bool(arguments.get("overwrite", False)),
    )


def _tool_scenario_write_run_script(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    write_result = _write_scenario_script(
        services,
        scenario_id=str(arguments.get("scenario_id", "")).strip(),
        relative_path=str(arguments.get("relative_path", "")).strip(),
        content=str(arguments.get("content", "")),
        overwrite=bool(arguments.get("overwrite", False)),
    )
    run_result = _run_scenario_python_entry(
        services,
        scenario_id=str(write_result.get("scenario_id", "")).strip(),
        relative_path=str(write_result.get("relative_path", "")).strip(),
        expect_marimo=False,
        runtime_mode=str(arguments.get("runtime_mode", "osgeo")).strip(),
    )
    return {
        "scenario_id": write_result["scenario_id"],
        "relative_path": write_result["relative_path"],
        "write": write_result,
        "run": run_result,
        "job_id": run_result["job_id"],
        "run_id": run_result["run_id"],
        "status": run_result["status"],
        "result": run_result.get("result", {}),
        "run_metadata": run_result.get("run_metadata", {}),
    }


def _tool_scenario_rag_ingest(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = str(arguments.get("scenario_id", "")).strip() or "global"
    relative_root = str(arguments.get("relative_root", "")).strip()
    rebuild = bool(arguments.get("rebuild", False))
    respect_directives = bool(arguments.get("respect_directives", True))
    raw_extensions = arguments.get("extensions")
    extensions: list[str] | None = None
    if isinstance(raw_extensions, list):
        extensions = [str(item).strip() for item in raw_extensions if str(item).strip()]
    raw_mode = str(arguments.get("mode", JobMode.IMMEDIATE.value)).strip().lower()
    mode = JobMode.IMMEDIATE if raw_mode == JobMode.IMMEDIATE.value else JobMode.QUEUED
    job = services.job_service.run_typed_job(
        "assistant_rag_ingest",
        {
            "scenario_id": scenario_id,
            "relative_root": relative_root,
            "rebuild": rebuild,
            "respect_directives": respect_directives,
            "extensions": extensions,
            "mode": mode.value,
        },
    )
    return {
        "job_id": job.job_id,
        "run_id": job.job_id,
        "job": job.model_dump(mode="json"),
        "result": _extract_completed_job_result(services, job.job_id),
    }


def _tool_jobs_list_predefined(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    _ = arguments
    response = services.notebook_job_service.list_job_definitions(scenario_id=None)
    items = [
        item.model_dump(mode="json")
        for item in response.definitions
        if item.job_type == JobDefinitionType.NATIVE and item.visibility != "system"
    ]
    return {"items": items, "count": len(items)}


def _tool_jobs_run_predefined(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    implementation_name = str(arguments.get("implementation_name", arguments.get("handler_name", ""))).strip()
    raw_params = arguments.get("params", {})
    if not implementation_name:
        raise ValueError("implementation_name is required")
    if not isinstance(raw_params, dict):
        raise ValueError("params must be an object")
    job = services.job_service.run_typed_job(implementation_name, raw_params)
    return {
        "job_id": job.job_id,
        "run_id": job.job_id,
        "job": job.model_dump(mode="json"),
        "result": _extract_completed_job_result(services, job.job_id),
    }


def _tool_runs_get_status(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    run_id = str(arguments.get("run_id", arguments.get("job_id", ""))).strip()
    if not run_id:
        raise ValueError("run_id is required")
    job = services.job_service.get_job(run_id)
    events = services.job_service.list_job_events(run_id)
    return {
        "job_id": run_id,
        "run_id": run_id,
        "job": job.model_dump(mode="json"),
        "event_count": len(events),
        "has_result": any(event.event_name == JobEventName.JOB_COMPLETED for event in events),
        "run_metadata": dict(services.stores.notebook_run_info.get(run_id, {})),
    }


def _tool_runs_get_logs(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    run_id = str(arguments.get("run_id", arguments.get("job_id", ""))).strip()
    if not run_id:
        raise ValueError("run_id is required")
    head_lines = int(arguments.get("head_lines", 40) or 0)
    tail_lines = int(arguments.get("tail_lines", 80) or 0)
    head_lines = max(0, min(2000, head_lines))
    tail_lines = max(0, min(2000, tail_lines))
    stream = str(arguments.get("stream", "stdout")).strip().lower() or "stdout"
    run_info = services.stores.notebook_run_info.get(run_id)
    if not run_info:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            run_info = services.stores.notebook_run_info.get(run_id)
            if run_info:
                break
            time.sleep(0.05)
    if not run_info:
        job = services.job_service.get_job(run_id)
        pending = job.status in {JobStatus.QUEUED, JobStatus.RUNNING}
        empty_slice = {
            "job_id": run_id,
            "run_id": run_id,
            "head": [],
            "tail": [],
            "total_lines": 0,
            "total_bytes": 0,
            "path_exists": False,
            "status": job.status.value,
            "pending": pending,
        }
        if stream == "stdout":
            return dict(empty_slice, stream="stdout")
        if stream == "stderr":
            return dict(empty_slice, stream="stderr")
        if stream == "combined":
            return {
                "job_id": run_id,
                "run_id": run_id,
                "stream": "combined",
                "streams": {
                    "stdout": dict(empty_slice, stream="stdout"),
                    "stderr": dict(empty_slice, stream="stderr"),
                },
                "total_bytes": 0,
                "total_lines": 0,
                "status": job.status.value,
                "pending": pending,
            }
        raise ValueError("stream must be one of: stdout, stderr, combined")
    stdout_path = Path(str(run_info.get("stdout_log_path", "")).strip())
    stderr_path = Path(str(run_info.get("stderr_log_path", "")).strip())
    if stream == "stdout":
        return _read_log_slice(run_id=run_id, stream="stdout", path=stdout_path, head_lines=head_lines, tail_lines=tail_lines)
    if stream == "stderr":
        return _read_log_slice(run_id=run_id, stream="stderr", path=stderr_path, head_lines=head_lines, tail_lines=tail_lines)
    if stream == "combined":
        stdout = _read_log_slice(run_id=run_id, stream="stdout", path=stdout_path, head_lines=head_lines, tail_lines=tail_lines)
        stderr = _read_log_slice(run_id=run_id, stream="stderr", path=stderr_path, head_lines=head_lines, tail_lines=tail_lines)
        return {
            "job_id": run_id,
            "run_id": run_id,
            "stream": "combined",
            "streams": {
                "stdout": stdout,
                "stderr": stderr,
            },
            "total_bytes": int(stdout.get("total_bytes", 0)) + int(stderr.get("total_bytes", 0)),
            "total_lines": int(stdout.get("total_lines", 0)) + int(stderr.get("total_lines", 0)),
        }
    raise ValueError("stream must be one of: stdout, stderr, combined")


def _tool_runs_cancel(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    run_id = str(arguments.get("run_id", arguments.get("job_id", ""))).strip()
    if not run_id:
        raise ValueError("run_id is required")
    job = services.job_service.cancel_job(run_id)
    return {"job_id": run_id, "run_id": run_id, "job": job.model_dump(mode="json")}


def _tool_job_launch(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    implementation_name = str(arguments.get("implementation_name", arguments.get("handler_name", ""))).strip()
    raw_params = arguments.get("params", {})
    if not implementation_name:
        raise ValueError("implementation_name is required")
    if not isinstance(raw_params, dict):
        raise ValueError("params must be an object")
    job = services.job_service.run_typed_job(implementation_name, raw_params)
    return job.model_dump(mode="json")


def _tool_job_cancel(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    job_id = str(arguments.get("job_id", "")).strip()
    if not job_id:
        raise ValueError("job_id is required")
    return services.job_service.cancel_job(job_id).model_dump(mode="json")


def _tool_product_list(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = str(arguments.get("scenario_id", "")).strip()
    if not scenario_id:
        raise ValueError("scenario_id is required")
    items = [item.model_dump(mode="json") for item in services.product_service.list_products(scenario_id)]
    return {"items": items, "count": len(items)}


def _tool_product_files(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    product_id = str(arguments.get("product_id", "")).strip()
    if not product_id:
        raise ValueError("product_id is required")
    items = [item.model_dump(mode="json") for item in services.product_service.list_product_files(product_id)]
    return {"items": items, "count": len(items)}


def _tool_artifact_describe_geotiff(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    path, source_file_id = _resolve_artifact_identity(services, arguments)
    return describe_geotiff(path, source_file_id=source_file_id)


def _tool_artifact_stats_geotiff(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    path, source_file_id = _resolve_artifact_identity(services, arguments)
    return describe_geotiff_stats(path, source_file_id=source_file_id)


def _tool_artifact_describe_table(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    path, source_file_id = _resolve_artifact_identity(services, arguments)
    return describe_table(path, source_file_id=source_file_id)


def _tool_artifact_describe_plot(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    path, source_file_id = _resolve_artifact_identity(services, arguments)
    return describe_plot(path, source_file_id=source_file_id)


def _tool_artifact_preview_geotiff(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    path, source_file_id = _resolve_artifact_identity(services, arguments)
    return _preview_geotiff_artifact(
        services,
        path=path,
        source_file_id=source_file_id,
        scenario_id_hint=str(arguments.get("scenario_id", "")).strip() or None,
    )


def _tool_scenario_import_geotiff(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = str(arguments.get("scenario_id", "")).strip()
    source_path = str(arguments.get("source_path", "")).strip()
    kind = str(arguments.get("kind", "imports")).strip()
    subkind = str(arguments.get("subkind", "geotiff")).strip()
    if not scenario_id or not source_path:
        raise ValueError("scenario_id and source_path are required")
    normalized_relative = source_path.replace("\\", "/").strip().lstrip("./")
    resolved_relative_path: Path | None = None
    if normalized_relative and not Path(source_path).expanduser().is_absolute():
        try:
            resolved_relative_path = Path(
                services.scenario_service.resolve_scenario_file(scenario_id, normalized_relative)
            ).resolve()
        except Exception:
            raise ValueError(
                f"Scenario file not found: {normalized_relative}. "
                "Provide an existing relative path under the active scenario root."
            )
    if normalized_relative:
        services.scenario_service.reconcile_scenario_filesystem(scenario_id, force=True)
        target_record = None
        for record in services.stores.product_files.values():
            if str(getattr(record, "scenario_id", "")).strip() != scenario_id:
                continue
            record_relative = str(getattr(record, "relative_path", "")).replace("\\", "/").strip().lstrip("./")
            if record_relative.lower() == normalized_relative.lower():
                target_record = record
                break
        if target_record is not None:
            layers = services.layer_service.list_layers(scenario_id)
            existing_layer = next(
                (
                    layer
                    for layer in layers
                    if str(getattr(layer, "source_file_id", "")).strip() == str(target_record.file_id)
                ),
                None,
            )
            if existing_layer is not None:
                updated = services.layer_service.update_layer(
                    existing_layer.layer_id,
                    UpdateLayerStateRequest(
                        visible=True,
                        title=existing_layer.title or Path(normalized_relative).name,
                    ),
                )
                return {
                    "mode": "existing_file_layer_updated",
                    "scenario_id": scenario_id,
                    "file_id": str(target_record.file_id),
                    "relative_path": str(target_record.relative_path),
                    "layer": updated.model_dump(mode="json"),
                }
            next_z = max((int(getattr(layer, "z_index", 0)) for layer in layers), default=0) + 1
            created = services.layer_service.create_layer(
                CreateLayerStateRequest(
                    scenario_id=scenario_id,
                    product_id=str(getattr(target_record, "product_id", "")).strip() or None,
                    title=Path(normalized_relative).name,
                    visible=True,
                    opacity=1.0,
                    z_index=next_z,
                    render_mode=RenderMode.RASTER,
                    source_file_id=str(target_record.file_id),
                    style={},
                )
            )
            return {
                "mode": "existing_file_layer_created",
                "scenario_id": scenario_id,
                "file_id": str(target_record.file_id),
                "relative_path": str(target_record.relative_path),
                "layer": created.model_dump(mode="json"),
            }
        if resolved_relative_path is not None and resolved_relative_path.exists() and resolved_relative_path.is_file():
            source_path = str(resolved_relative_path)
    req = ImportGeoTiffRequest(
        source_path=source_path,
        kind=kind,
        subkind=subkind,
        bypass_cog=bool(arguments.get("bypass_cog", False)),
    )
    return services.scenario_service.import_geotiff(scenario_id, req).model_dump(mode="json")


def _tool_scenario_move_path(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = str(arguments.get("scenario_id", "")).strip()
    source_relative_path = str(arguments.get("source_relative_path", "")).strip()
    target_relative_path = str(arguments.get("target_relative_path", "")).strip()
    if not scenario_id or not source_relative_path or not target_relative_path:
        raise ValueError(
            "scenario_id, source_relative_path, and target_relative_path are required"
        )
    req = MoveScenarioPathRequest(
        source_relative_path=source_relative_path,
        target_relative_path=target_relative_path,
    )
    return services.scenario_service.move_scenario_path(scenario_id, req).model_dump(mode="json")


def _tool_colormap_list(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = str(arguments.get("scenario_id", "")).strip()
    if not scenario_id:
        raise ValueError("scenario_id is required")
    scenario_root = services.scenario_service.resolve_scenario_root(scenario_id)
    app_cfg = load_app_config()
    backend_cfg = app_cfg.get("backend", {}) if isinstance(app_cfg, dict) else {}
    map_cfg = backend_cfg.get("lunar_analyst", {}) if isinstance(backend_cfg, dict) else {}
    if not isinstance(map_cfg, dict):
        map_cfg = {}
    return resolve_colormap_registry(
        repo_root=repo_root(),
        config_path=resolve_config_path(),
        map_cfg=map_cfg,
        scenario_root=scenario_root,
    )


def _tool_colormap_create_simple(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    _ = services
    raw = dict(arguments)
    normalized = normalize_colormap(raw)
    if normalized is None:
        raise ValueError("Invalid colormap payload. Required: id, name, stops[>=2], mode.")
    return {"colormap": normalized}


def _tool_colormap_save_scenario(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = str(arguments.get("scenario_id", "")).strip()
    raw_colormap = arguments.get("colormap")
    if not scenario_id:
        raise ValueError("scenario_id is required")
    if not isinstance(raw_colormap, dict):
        raise ValueError("colormap is required and must be an object")
    normalized = normalize_colormap(raw_colormap)
    if normalized is None:
        raise ValueError("Invalid colormap payload.")
    scenario_root = services.scenario_service.resolve_scenario_root(scenario_id)
    local_path = (scenario_root / "colormaps" / "local" / "map_colormaps.json").resolve()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_colormap_file(local_path)
    by_id = {str(item.get("id", "")).strip(): item for item in existing}
    by_id[str(normalized["id"])] = normalized
    payload = {"colormaps": sorted(by_id.values(), key=lambda item: str(item.get("name", "")).lower())}
    local_path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    return {
        "scenario_id": scenario_id,
        "path": str(local_path),
        "saved_colormap_id": normalized["id"],
        "count": len(payload["colormaps"]),
    }


def _tool_layer_apply_colormap(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    colormap_id = str(arguments.get("colormap", "")).strip()
    if not colormap_id:
        raise ValueError("colormap is required")
    layer_id = str(arguments.get("layer_id", "")).strip()
    if not layer_id:
        scenario_id = str(arguments.get("scenario_id", "")).strip()
        layer_name = str(arguments.get("layer_name", "")).strip()
        if not scenario_id or not layer_name:
            raise ValueError("layer_id is required, or provide scenario_id + layer_name")
        layer_id = _resolve_layer_id_by_name(
            services,
            scenario_id=scenario_id,
            layer_name=layer_name,
        )
    if not layer_id:
        raise ValueError("layer_id is required")
    layer = services.stores.layers.get(layer_id)
    if layer is None:
        raise KeyError(f"Layer not found: {layer_id}")
    style = dict(getattr(layer, "style", {}) or {})
    style["colormap"] = colormap_id
    return services.layer_service.update_layer(
        layer_id,
        UpdateLayerStateRequest(style=style),
    ).model_dump(mode="json")


def _tool_layer_list_visible(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = str(arguments.get("scenario_id", "")).strip()
    if not scenario_id:
        raise ValueError("scenario_id is required")
    base_layer_visible = bool(arguments.get("base_layer_visible", True))
    base_layer_title = str(arguments.get("base_layer_title", "Moon Trek Base")).strip() or "Moon Trek Base"
    layers = services.layer_service.list_layers(scenario_id)
    visible_items = []
    for layer in layers:
        if bool(getattr(layer, "visible", False)):
            if hasattr(layer, "model_dump"):
                visible_items.append(layer.model_dump(mode="json"))
            else:
                visible_items.append(dict(layer))
    if base_layer_visible:
        visible_items.append(
            {
                "layer_id": "base:moon_trek",
                "scenario_id": scenario_id,
                "product_id": None,
                "title": base_layer_title,
                "visible": True,
                "opacity": 1.0,
                "z_index": 0,
                "render_mode": "base",
                "source_file_id": "moon_trek",
            }
        )
    visible_items.sort(key=lambda item: int(item.get("z_index", 0)), reverse=True)
    return {"scenario_id": scenario_id, "items": visible_items, "count": len(visible_items)}


def _tool_layer_update_state(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    layer_id = str(arguments.get("layer_id", "")).strip()
    if not layer_id:
        scenario_id = str(arguments.get("scenario_id", "")).strip()
        layer_name = str(arguments.get("layer_name", "")).strip()
        if not scenario_id or not layer_name:
            raise ValueError("layer_id is required, or provide scenario_id + layer_name")
        layer_id = _resolve_layer_id_by_name(
            services,
            scenario_id=scenario_id,
            layer_name=layer_name,
        )
    if not layer_id:
        raise ValueError("layer_id is required")
    update = UpdateLayerStateRequest(
        title=arguments.get("title"),
        visible=arguments.get("visible"),
        opacity=arguments.get("opacity"),
        z_index=arguments.get("z_index"),
        style=arguments.get("style"),
    )
    return services.layer_service.update_layer(layer_id, update).model_dump(mode="json")


def _nomenclature_service(services: "ServiceContainer") -> NomenclatureService:
    return NomenclatureService(db_path=services.stores.catalog_db_path)


def _tool_location_search(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")
    feature_type = str(arguments.get("feature_type", "")).strip() or None
    limit = int(arguments.get("limit", 10) or 10)
    items = _nomenclature_service(services).search_fuzzy(query=query, limit=limit, feature_type=feature_type)
    return {"query": query, "feature_type": feature_type, "count": len(items), "items": items}


def _feature_extent_from_payload(feature: dict[str, Any]) -> list[float]:
    """Extract a safe non-degenerate extent [min_x,min_y,max_x,max_y] from a nomenclature feature payload.

    If the feature provides a region, normalize degenerate or zero-area regions by expanding
    to a buffered box centered on the region center. If only a center point is provided,
    return a buffered box around it. When available, prefer using feature.diameter_km to
    derive a sensible buffer so large features (e.g., mountains) are not zoomed to tiny boxes.
    """
    MIN_BUFFER_M = 1000.0

    def _buffer_from_feature() -> float:
        """Return half-width buffer in map units derived from feature.diameter_km if present."""
        diameter = feature.get("diameter_km")
        if isinstance(diameter, (int, float)) and diameter > 0:
            # diameter_km is in kilometers; convert to meters and take half
            return float(diameter) * 500.0
        return 0.0

    feature_buffer = _buffer_from_feature()

    location = feature.get("location")
    if isinstance(location, dict):
        region = location.get("region")
        if isinstance(region, dict):
            min_x = region.get("min_x")
            min_y = region.get("min_y")
            max_x = region.get("max_x")
            max_y = region.get("max_y")
            if all(isinstance(v, (int, float)) for v in (min_x, min_y, max_x, max_y)):
                min_xf = float(min_x)
                min_yf = float(min_y)
                max_xf = float(max_x)
                max_yf = float(max_y)
                dx = max_xf - min_xf
                dy = max_yf - min_yf
                # If the region is degenerate or extremely small, or simply small relative to
                # the feature size, expand it using a buffer derived from the feature diameter
                # or a fallback minimum.
                if dx <= 0.0 or dy <= 0.0 or (dx < 1e-3 and dy < 1e-3) or (max(dx, dy) < max(MIN_BUFFER_M * 2.0, feature_buffer * 0.1)):
                    # buffer candidates: explicit feature-derived half-diameter, 10% of larger dimension, or MIN_BUFFER_M
                    size_based = max(abs(dx), abs(dy)) * 0.1
                    buffer = max(MIN_BUFFER_M, feature_buffer, size_based)
                    cx = (min_xf + max_xf) / 2.0
                    cy = (min_yf + max_yf) / 2.0
                    return [cx - buffer, cy - buffer, cx + buffer, cy + buffer]
                return [min_xf, min_yf, max_xf, max_yf]
        center = location.get("center")
        if isinstance(center, dict):
            x = center.get("x")
            y = center.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                xf = float(x)
                yf = float(y)
                # For pure points, buffer by the feature-derived size when available, otherwise MIN_BUFFER_M
                buffer = max(MIN_BUFFER_M, feature_buffer)
                return [xf - buffer, yf - buffer, xf + buffer, yf + buffer]
    raise ValueError("resolved feature does not include a usable location")


def _tool_location_goto(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = str(arguments.get("scenario_id", "")).strip()
    name = str(arguments.get("name", "")).strip()
    feature_id = str(arguments.get("feature_id", "")).strip() or None
    if not scenario_id:
        raise ValueError("scenario_id is required")
    if not name and not feature_id:
        raise ValueError("name or feature_id is required")

    feature_type = str(arguments.get("feature_type", "")).strip() or None
    if feature_id:
        resolved = _nomenclature_service(services).get_feature(int(feature_id))
    else:
        resolved = resolve_feature_with_variants(_nomenclature_service(services), name, feature_type)

    if resolved is None:
        if feature_id:
            raise KeyError(f"No nomenclature match found for feature_id: {feature_id}")
        raise KeyError(f"No exact nomenclature match found for: {name}")
    extent = _feature_extent_from_payload(resolved)
    padding_px = int(arguments.get("padding_px", 32) or 32)
    # Keep assistant-driven nomenclature zoom behavior aligned with the UI nomenclature pane.
    # Without an explicit max_zoom cap, OL can occasionally settle on an overly deep zoom
    # level when map visibility/size changes race with deferred fit application.
    max_zoom_arg = arguments.get("max_zoom")
    max_zoom = float(max_zoom_arg) if isinstance(max_zoom_arg, (int, float)) else 11.0
    payload = WsEnvelope(
        event=JobEventName.MAP_ZOOM_REQUESTED,
        scenario_id=scenario_id,
        timestamp_utc=_dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S"),
        data={
            "scenario_id": scenario_id,
            "extent": extent,
            "padding_px": max(0, padding_px),
            "max_zoom": max_zoom,
        },
    )
    services.stores.ws_events.append(payload.model_dump(mode="json"))
    return {
        "status": "queued",
        "event": "map_zoom_requested",
        "scenario_id": scenario_id,
        "feature": resolved,
        "extent": extent,
        "padding_px": max(0, padding_px),
        "max_zoom": max_zoom,
    }


def _tool_location_identify(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    x = float(arguments.get("x"))
    y = float(arguments.get("y"))
    feature_type = str(arguments.get("feature_type", "")).strip() or None
    limit = int(arguments.get("limit", 25) or 25)
    radius_m = float(arguments["radius_m"]) if arguments.get("radius_m") is not None else None
    items = _nomenclature_service(services).nearby(
        x=x,
        y=y,
        limit=limit,
        feature_type=feature_type,
        radius_m=radius_m,
    )
    return {
        "query_point": {"x": x, "y": y, "crs": "ESRI:103878"},
        "feature_type": feature_type,
        "radius_m": radius_m,
        "count": len(items),
        "items": items,
    }


def _location_pin_store(services: "ServiceContainer") -> dict[str, list[dict[str, Any]]]:
    store = getattr(services.stores, "_location_pins", None)
    if isinstance(store, dict):
        return store
    created: dict[str, list[dict[str, Any]]] = {}
    setattr(services.stores, "_location_pins", created)
    return created


def _tool_location_pin_feature(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    session_id = str(arguments.get("session_id", "")).strip()
    name = str(arguments.get("name", "")).strip()
    feature_id = str(arguments.get("feature_id", "")).strip() or None
    if not session_id:
        raise ValueError("session_id is required")
    if not name and not feature_id:
        raise ValueError("name or feature_id is required")

    feature_type = str(arguments.get("feature_type", "")).strip() or None
    if feature_id:
        resolved = _nomenclature_service(services).get_feature(int(feature_id))
    else:
        resolved = resolve_feature_with_variants(_nomenclature_service(services), name, feature_type)

    if resolved is None:
        if feature_id:
            raise KeyError(f"No nomenclature match found for feature_id: {feature_id}")
        raise KeyError(f"No exact nomenclature match found for: {name}")
    pins = _location_pin_store(services)
    session_pins = list(pins.get(session_id, []))
    feature_id = int(resolved.get("feature_id", -1))
    session_pins = [item for item in session_pins if int(item.get("feature_id", -1)) != feature_id]
    session_pins.append(resolved)
    pins[session_id] = session_pins
    return {"session_id": session_id, "count": len(session_pins), "items": session_pins, "pinned": resolved}


def _tool_location_list_pins(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    session_id = str(arguments.get("session_id", "")).strip()
    if not session_id:
        raise ValueError("session_id is required")
    pins = _location_pin_store(services)
    items = list(pins.get(session_id, []))
    return {"session_id": session_id, "count": len(items), "items": items}


def _tool_location_set_layer_filter(
    services: "ServiceContainer",
    arguments: dict[str, Any],
) -> dict[str, Any]:
    session_id = str(arguments.get("session_id", "")).strip()
    if not session_id:
        raise ValueError("session_id is required")
    visible = arguments.get("visible")
    if visible is None:
        visible = True
    types_raw = arguments.get("types")
    types: list[str] = []
    if isinstance(types_raw, list):
        types = [str(item).strip() for item in types_raw if str(item).strip()]
    store = getattr(services.stores, "_nomenclature_layer_filters", None)
    if not isinstance(store, dict):
        store = {}
        setattr(services.stores, "_nomenclature_layer_filters", store)
    state = {"session_id": session_id, "visible": bool(visible), "types": types}
    store[session_id] = state
    return state


REGISTRY_TOOL_EXECUTORS: dict[str, ToolExecutor] = {
    "capabilities.describe": _tool_capabilities_describe,
    "tools.search": _tool_tools_search,
    "tools.describe": _tool_tools_describe,
    "scenario.list": _tool_scenario_list,
    "scenario.get": _tool_scenario_get,
    "scenario.set_current": _tool_scenario_set_current,
    "scenario.list_scripts": _tool_scenario_list_scripts,
    "scenario.list_notebooks": _tool_scenario_list_notebooks,
    "scenario.run_script": _tool_scenario_run_script,
    "scenario.run_marimo_notebook": _tool_scenario_run_marimo_notebook,
    "scenario.write_script": _tool_scenario_write_script,
    "scenario.write_run_script": _tool_scenario_write_run_script,
    "scenario.revoke_script_overwrite": _tool_scenario_revoke_script_overwrite,
    "scenario.rag_ingest": _tool_scenario_rag_ingest,
    "jobs.list_predefined": _tool_jobs_list_predefined,
    "jobs.run_predefined": _tool_jobs_run_predefined,
    "runs.get_status": _tool_runs_get_status,
    "runs.get_logs": _tool_runs_get_logs,
    "runs.cancel": _tool_runs_cancel,
    "job.launch": _tool_job_launch,
    "job.cancel": _tool_job_cancel,
    "product.list": _tool_product_list,
    "product.files": _tool_product_files,
    "artifact.describe_geotiff": _tool_artifact_describe_geotiff,
    "artifact.preview_geotiff": _tool_artifact_preview_geotiff,
    "artifact.stats_geotiff": _tool_artifact_stats_geotiff,
    "artifact.describe_table": _tool_artifact_describe_table,
    "artifact.describe_plot": _tool_artifact_describe_plot,
    "scenario.import_geotiff": _tool_scenario_import_geotiff,
    "scenario.move_path": _tool_scenario_move_path,
    "colormap.list": _tool_colormap_list,
    "colormap.create_simple": _tool_colormap_create_simple,
    "colormap.save_scenario": _tool_colormap_save_scenario,
    "layer.apply_colormap": _tool_layer_apply_colormap,
    "layer.list_visible": _tool_layer_list_visible,
    "layer.update_state": _tool_layer_update_state,
    "location.search": _tool_location_search,
    "location.goto": _tool_location_goto,
    "location.identify": _tool_location_identify,
    "location.pin_feature": _tool_location_pin_feature,
    "location.list_pins": _tool_location_list_pins,
    "location.set_layer_filter": _tool_location_set_layer_filter,
}


def execute_tool(
    services: "ServiceContainer",
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object")
    _validate_tool_call_arguments(tool_name, arguments)
    registry_executor = REGISTRY_TOOL_EXECUTORS.get(tool_name)
    if registry_executor is not None:
        return registry_executor(services, arguments)
    canonical_tool = _canonical_tool_definition(tool_name)
    if canonical_tool is not None:
        implementation_name = canonical_tool.implementation_name or canonical_tool.handler_name
        job = services.job_service.run_typed_job(implementation_name, arguments)
        return {
            "tool_name": canonical_tool.tool_name,
            "job_id": job.job_id,
            "run_id": job.job_id,
            "job": job.model_dump(mode="json"),
            "result": _extract_completed_job_result(services, job.job_id),
        }
    raise KeyError(f"Unsupported assistant tool: {tool_name}")


def _resolve_layer_id_by_name(
    services: "ServiceContainer",
    *,
    scenario_id: str,
    layer_name: str,
) -> str:
    return _layer_resolve_layer_id_by_name(
        services,
        scenario_id=scenario_id,
        layer_name=layer_name,
    )


def _resolve_artifact_path(services: "ServiceContainer", arguments: dict[str, Any]) -> Path:
    return _artifact_resolve_artifact_path(services, arguments)


def _write_scenario_script(
    services: "ServiceContainer",
    *,
    scenario_id: str,
    relative_path: str,
    content: str,
    overwrite: bool,
) -> dict[str, Any]:
    return _tool_write_scenario_script(
        services,
        scenario_id=scenario_id,
        relative_path=relative_path,
        content=content,
        overwrite=overwrite,
    )


def _run_scenario_python_entry(
    services: "ServiceContainer",
    *,
    scenario_id: str,
    relative_path: str,
    expect_marimo: bool,
    runtime_mode: str = "osgeo",
) -> dict[str, Any]:
    return _tool_run_scenario_python_entry(
        services,
        scenario_id=scenario_id,
        relative_path=relative_path,
        expect_marimo=expect_marimo,
        runtime_mode=runtime_mode,
        completed_result_reader=_extract_completed_job_result,
        jsonable_converter=_make_jsonable,
    )


def _preview_geotiff_artifact(
    services: "ServiceContainer",
    *,
    path: Path,
    source_file_id: str | None,
    scenario_id_hint: str | None = None,
) -> dict[str, Any]:
    preview_bytes, preview_meta = render_geotiff_preview_png(path)
    preview_file_id: str | None = None
    preview_relative_path: str | None = None
    source_product_id: str | None = None
    scenario_id: str | None = str(scenario_id_hint or "").strip() or None

    if scenario_id and source_file_id is None:
        try:
            services.scenario_service.reconcile_scenario_filesystem(scenario_id, force=True)
        except Exception:
            pass
        source_file_id = _find_file_id_for_path(services, path)

    if source_file_id:
        try:
            source_record = services.product_service.get_file_record(source_file_id)
            scenario_id = source_record.scenario_id
            source_product_id = source_record.product_id
        except Exception:
            source_product_id = None

    if scenario_id:
        scenario = services.scenario_service.get_scenario(scenario_id)
        scenario_root = Path(scenario.directory).expanduser().resolve()
        preview_dir = _resolve_relative_path(scenario_root, ".assistant_previews")
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_name = f"{path.stem}.preview.png"
        preview_path = (preview_dir / preview_name).resolve()
        preview_path.write_bytes(preview_bytes)
        preview_relative_path = preview_path.relative_to(scenario_root).as_posix()
        preview_file_id = _find_file_id_for_path(services, preview_path)
        if preview_file_id is None and source_product_id:
            record = services.scenario_service._register_file(  # type: ignore[attr-defined]
                product_id=source_product_id,
                scenario_id=scenario_id,
                scenario_root=scenario_root,
                relative_path=preview_relative_path,
                media_type="image/png",
                role="preview",
            )
            preview_file_id = record.file_id

    artifacts: list[dict[str, Any]] = []
    if preview_file_id:
        artifacts.append(
            {
                "output_id": f"{path.name}-preview",
                "kind": "image",
                "mime_type": "image/png",
                "storage": "file",
                "title": f"{path.name} preview",
                "caption": "Raster preview",
                "file_id": preview_file_id,
                "data": {},
                "metadata": {
                    "width": preview_meta["width"],
                    "height": preview_meta["height"],
                    "alt": f"Preview of {path.name}",
                    "source_file_id": source_file_id,
                    "generated_relative_path": preview_relative_path,
                },
            }
        )
        artifacts.append(
            {
                "output_id": f"{path.name}-preview-artifact-card",
                "kind": "artifact_card",
                "mime_type": "application/vnd.lunar-analyst.artifact-card+json",
                "storage": "inline",
                "title": f"{path.name} preview",
                "caption": None,
                "file_id": None,
                "data": {
                    "name": Path(preview_relative_path or f"{path.stem}.preview.png").name,
                    "path": preview_relative_path or str(path),
                    "suffix": ".png",
                    "size_bytes": int(len(preview_bytes)),
                    "summary_text": f"Preview image generated for `{path.name}`.",
                    "key_stats": {
                        "width": preview_meta["width"],
                        "height": preview_meta["height"],
                        "source_file_id": source_file_id,
                    },
                    "source_file_id": preview_file_id,
                },
                "metadata": {},
            }
        )

    return {
        "summary_text": f"GeoTIFF preview generated for `{path.name}`.",
        "key_stats": {
            "preview_width": preview_meta["width"],
            "preview_height": preview_meta["height"],
            "preview_file_id": preview_file_id,
            "preview_relative_path": preview_relative_path,
        },
        "warnings": [],
        "source_files": [str(path)],
        "artifact_file_id": source_file_id,
        "generated_file_id": preview_file_id,
        "generated_relative_path": preview_relative_path,
        "artifacts": artifacts,
    }


def _resolve_artifact_identity(services: "ServiceContainer", arguments: dict[str, Any]) -> tuple[Path, str | None]:
    return _artifact_resolve_artifact_identity(services, arguments)


def _find_or_register_file_id_for_path(
    services: "ServiceContainer",
    path: Path,
    *,
    scenario_id_hint: str | None = None,
) -> str | None:
    return _artifact_find_or_register_file_id_for_path(
        services,
        path,
        scenario_id_hint=scenario_id_hint,
    )


def _infer_scenario_id_for_path(services: "ServiceContainer", path: Path) -> str | None:
    return _artifact_infer_scenario_id_for_path(services, path)


def _register_scenario_file_id_for_path(
    services: "ServiceContainer",
    path: Path,
    *,
    scenario_id: str,
) -> str | None:
    return _artifact_register_scenario_file_id_for_path(
        services,
        path,
        scenario_id=scenario_id,
    )


def _guess_media_type_for_artifact_path(relative_path: str) -> str:
    return _artifact_guess_media_type_for_artifact_path(relative_path)


def _find_file_id_for_path(services: "ServiceContainer", path: Path) -> str | None:
    return _artifact_find_file_id_for_path(services, path)


def _resolve_relative_path(scenario_root: Path, relative_path: str) -> Path:
    return _artifact_resolve_relative_path(scenario_root, relative_path)


def _is_path_within_root(path: Path, root: Path) -> bool:
    return _artifact_is_path_within_root(path, root)


def _match_scenario(services: "ServiceContainer", scenario_ref: str) -> dict[str, Any]:
    return _tool_match_scenario(
        services,
        scenario_ref,
        dem_extent_reader=_scenario_dem_extent,
    )


def _scenario_candidates_for_hint(scenarios: list[Any]) -> list[dict[str, Any]]:
    return _tool_scenario_candidates_for_hint(scenarios)


def _scenario_match_score(candidate: dict[str, Any], needle: str) -> int:
    return _tool_scenario_match_score(candidate, needle)


def _scenario_dem_extent(scenario: Any) -> list[float] | None:
    try:
        footprint = scenario.primary_dem_footprint.model_dump(mode="json")
    except Exception:
        footprint = None
    extent = _footprint_extent(footprint if isinstance(footprint, dict) else {})
    if _is_placeholder_extent(extent):
        extent = _dem_extent_from_primary_dem_path(
            path_text=getattr(scenario, "primary_dem_path", ""),
            scenario_directory=getattr(scenario, "directory", ""),
        )
    return extent


def _dem_extent_from_primary_dem_path(*, path_text: str, scenario_directory: str) -> list[float] | None:
    raw = str(path_text or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        base_dir = Path(str(scenario_directory or "").strip())
        if str(base_dir).strip():
            path = (base_dir / path).resolve()
    try:
        from backend.worker.gdal_runtime import import_rasterio

        rasterio = import_rasterio()
    except Exception:
        return None
    try:
        with rasterio.open(path) as dataset:
            src_crs = dataset.crs
            bounds = dataset.bounds
        left = float(getattr(bounds, "left", 0.0))
        bottom = float(getattr(bounds, "bottom", 0.0))
        right = float(getattr(bounds, "right", 0.0))
        top = float(getattr(bounds, "top", 0.0))
        target_crs = rasterio.crs.CRS.from_wkt(ESRI_103878_WKT)
        if src_crs is not None and src_crs != target_crs:
            left, bottom, right, top = rasterio.warp.transform_bounds(
                src_crs,
                target_crs,
                left,
                bottom,
                right,
                top,
                densify_pts=21,
            )
    except Exception:
        return None
    extent = [left, bottom, right, top]
    if any(not math.isfinite(item) for item in extent):
        return None
    return extent


def _is_placeholder_extent(extent: list[float] | None) -> bool:
    if not extent or len(extent) != 4:
        return False
    target = [-1.0, -1.0, 1.0, 1.0]
    return all(abs(float(value) - expected) <= 1e-9 for value, expected in zip(extent, target))


def _footprint_extent(footprint: dict[str, Any]) -> list[float] | None:
    coords = footprint.get("coordinates")
    if not isinstance(coords, list) or not coords:
        return None
    ring = coords[0]
    if not isinstance(ring, list) or not ring:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for point in ring:
        if not isinstance(point, list) or len(point) < 2:
            continue
        try:
            x = float(point[0])
            y = float(point[1])
        except (TypeError, ValueError):
            continue
        xs.append(x)
        ys.append(y)
    if not xs or not ys:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _extract_completed_job_result(services: "ServiceContainer", job_id: str) -> dict[str, Any]:
    return _tool_extract_completed_job_result(services, job_id)


def _read_log_slice(
    *,
    run_id: str,
    stream: str,
    path: Path,
    head_lines: int,
    tail_lines: int,
) -> dict[str, Any]:
    return _tool_read_log_slice(
        run_id=run_id,
        stream=stream,
        path=path,
        head_lines=head_lines,
        tail_lines=tail_lines,
    )
