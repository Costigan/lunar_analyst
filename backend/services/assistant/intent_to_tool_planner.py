from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.services.assistant.prompt_classifier import SegmentClassification
from backend.services.assistant.entity_reference_resolver import SegmentEntityResolution

logger = logging.getLogger(__name__)

INTENT_TO_TOOL_PLANNABLE_FAMILIES: set[str] = {
    "layer_style_update",
    "layer_visibility_update",
    "artifact_inspection",
    "scenario_context_management",
    "compute_job_control",
    "programmatic_workflow_authoring",
    "lunar_environment_reasoning",
    "surface_route_planning",
    "evidence_packaging",
    "location_navigation",
}


@dataclass(frozen=True)
class MappedToolStep:
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class IntentToolPlan:
    intent_family: str
    tool_steps: list[MappedToolStep] = field(default_factory=list)
    requires_clarification: bool = False
    clarification_message: str | None = None
    blocking_reason_code: str | None = None
    model_handoff_prompt: str | None = None
    response_guardrails: dict[str, Any] = field(default_factory=dict)


class IntentToToolPlanner:
    def map(
        self,
        *,
        classification: SegmentClassification,
        scenario_id: str | None,
        entity_resolution: SegmentEntityResolution | None = None,
    ) -> IntentToolPlan | None:
        family = str(classification.intent_family or "").strip()
        props = dict(classification.intent_properties or {})
        if classification.segment_class != "intent_family" or not family or not isinstance(props, dict):
            return None
        typed = self._map_entity_kind_aware(
            classification=classification,
            scenario_id=scenario_id,
            entity_resolution=entity_resolution,
        )
        if typed is not None:
            return typed
        if family == "layer_style_update":
            return self._map_layer_style_update(properties=props, scenario_id=scenario_id)
        if family == "layer_visibility_update":
            return self._map_layer_visibility_update(properties=props, scenario_id=scenario_id)
        if family == "artifact_inspection":
            return self._map_artifact_inspection(properties=props, scenario_id=scenario_id)
        if family == "scenario_context_management":
            return self._map_scenario_context(properties=props)
        if family == "compute_job_control":
            return self._map_compute_job_control(properties=props)
        if family == "programmatic_workflow_authoring":
            return self._map_programmatic_workflow(properties=props, scenario_id=scenario_id)
        if family == "lunar_environment_reasoning":
            return self._map_lunar_environment_reasoning(properties=props)
        if family == "surface_route_planning":
            return self._map_surface_route_planning(properties=props, scenario_id=scenario_id)
        if family == "evidence_packaging":
            return self._map_evidence_packaging(properties=props, scenario_id=scenario_id)
        if family == "location_navigation":
            return self._map_location_navigation(
                properties=props,
                scenario_id=scenario_id,
                entity_resolution=entity_resolution,
            )
        return None

    @staticmethod
    def _clarification(
        *,
        family: str,
        message: str,
        reason_code: str,
    ) -> IntentToolPlan:
        return IntentToolPlan(
            intent_family=family,
            requires_clarification=True,
            clarification_message=message,
            blocking_reason_code=reason_code,
        )

    def _map_entity_kind_aware(
        self,
        *,
        classification: SegmentClassification,
        scenario_id: str | None,
        entity_resolution: SegmentEntityResolution | None,
    ) -> IntentToolPlan | None:
        if entity_resolution is None:
            return None
        family = str(classification.intent_family or "").strip() or "intent_family"
        canonical_operation = str(entity_resolution.canonical_operation or "").strip().lower()
        classified_operation = str(classification.intent_properties.get("operation", "")).strip().lower()
        target_kind = str(entity_resolution.target_kind or "").strip().lower()
        target_mention = str(entity_resolution.target_mention or "").strip()
        target_resolved_id = str(entity_resolution.target_resolved_id or "").strip()
        if not canonical_operation:
            return None
        if classified_operation and classified_operation not in {"show", "hide", "goto", "search", "set_current"}:
            return None

        if target_kind == "ambiguous_layer_or_file" and canonical_operation == "show":
            return self._clarification(
                family=family,
                message="`show` is ambiguous here; please specify whether you mean the layer or the file.",
                reason_code="ambiguous_target_layer_or_file",
            )

        scenario = str(scenario_id or "").strip()

        if canonical_operation in {"goto", "show"} and target_kind == "feature":
            if not scenario:
                return self._clarification(
                    family=family,
                    message="Please select an active scenario before navigating to a feature.",
                    reason_code="missing_scenario_id",
                )
            args: dict[str, Any] = {"scenario_id": scenario, "name": target_mention}
            if target_resolved_id.startswith("feature:"):
                args["feature_id"] = target_resolved_id.split(":", 1)[1]
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[MappedToolStep(tool_name="location.goto", arguments=args)],
            )

        if canonical_operation in {"show", "hide"} and target_kind == "layer":
            if not scenario:
                return self._clarification(
                    family=family,
                    message="Please select an active scenario before changing layer visibility.",
                    reason_code="missing_scenario_id",
                )
            visible = canonical_operation == "show"
            layer_name = target_mention
            if target_resolved_id.startswith("layer:"):
                layer_name = target_resolved_id.split(":", 1)[1]
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[
                    MappedToolStep(
                        tool_name="layer.update_state",
                        arguments={
                            "scenario_id": scenario,
                            "layer_name": layer_name,
                            "visible": visible,
                        },
                    )
                ],
            )

        if canonical_operation == "show" and target_kind == "file":
            if not scenario:
                return self._clarification(
                    family=family,
                    message="Please select an active scenario before showing a file as a layer.",
                    reason_code="missing_scenario_id",
                )
            source_path = target_mention
            if target_resolved_id.startswith("file:"):
                source_path = target_resolved_id.split(":", 1)[1]
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[
                    MappedToolStep(
                        tool_name="scenario.import_geotiff",
                        arguments={
                            "scenario_id": scenario,
                            "source_path": source_path,
                        },
                    )
                ],
            )

        return None

    @staticmethod
    def _layer_ref(properties: dict[str, Any]) -> str:
        target = properties.get("target")
        if isinstance(target, dict):
            layer_ref = str(target.get("layer_ref", "")).strip()
            if layer_ref:
                return layer_ref
        return ""

    def _map_layer_style_update(
        self,
        *,
        properties: dict[str, Any],
        scenario_id: str | None,
    ) -> IntentToolPlan:
        family = "layer_style_update"
        scenario = str(scenario_id or "").strip()
        if not scenario:
            return self._clarification(
                family=family,
                message="Please select an active scenario before updating layer style.",
                reason_code="missing_scenario_id",
            )
        operation = str(properties.get("operation", "")).strip().lower()
        if operation in {"list", "list_colormaps"}:
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[
                    MappedToolStep(
                        tool_name="colormap.list",
                        arguments={"scenario_id": scenario},
                    )
                ],
            )
        layer_ref = self._layer_ref(properties)
        if not layer_ref:
            return self._clarification(
                family=family,
                message="Please specify which layer to style.",
                reason_code="missing_layer_ref",
            )
        style = properties.get("style")
        if not isinstance(style, dict):
            return self._clarification(
                family=family,
                message="Please provide the style update details (for example a colormap id).",
                reason_code="missing_style_payload",
            )
        style_kind = str(style.get("kind", "")).strip().lower()
        if style_kind == "colormap":
            colormap_ref = str(style.get("colormap_ref", "")).strip()
            if not colormap_ref:
                return self._clarification(
                    family=family,
                    message="Please provide the colormap name to apply.",
                    reason_code="missing_colormap_ref",
                )
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[
                    MappedToolStep(
                        tool_name="layer.apply_colormap",
                        arguments={
                            "scenario_id": scenario,
                            "layer_name": layer_ref,
                            "colormap": colormap_ref,
                        },
                    )
                ],
            )

        update_style: dict[str, Any] = {}
        for field_name in ("opacity", "brightness", "contrast"):
            if field_name in style:
                update_style[field_name] = style[field_name]
        if "parameters" in style and isinstance(style.get("parameters"), dict):
            update_style.update(dict(style.get("parameters", {})))
        if not update_style:
            return self._clarification(
                family=family,
                message="I need concrete style fields to update.",
                reason_code="empty_style_update",
            )
        return IntentToolPlan(
            intent_family=family,
            tool_steps=[
                MappedToolStep(
                    tool_name="layer.update_state",
                    arguments={
                        "scenario_id": scenario,
                        "layer_name": layer_ref,
                        "style": update_style,
                    },
                )
            ],
        )

    def _map_layer_visibility_update(
        self,
        *,
        properties: dict[str, Any],
        scenario_id: str | None,
    ) -> IntentToolPlan:
        family = "layer_visibility_update"
        scenario = str(scenario_id or "").strip()
        if not scenario:
            return self._clarification(
                family=family,
                message="Please select an active scenario before updating layer visibility.",
                reason_code="missing_scenario_id",
            )
        layer_ref = self._layer_ref(properties)
        if not layer_ref:
            return self._clarification(
                family=family,
                message="Please specify which layer visibility to update.",
                reason_code="missing_layer_ref",
            )
        operation = str(properties.get("operation", "")).strip().lower()
        visible: bool | None = None
        if operation == "show":
            visible = True
        elif operation == "hide":
            visible = False
        elif operation == "set":
            if "visible" not in properties or not isinstance(properties.get("visible"), bool):
                return self._clarification(
                    family=family,
                    message="Please specify whether the layer should be visible or hidden.",
                    reason_code="missing_visible_for_set",
                )
            visible = bool(properties.get("visible"))
        elif operation == "toggle":
            return self._clarification(
                family=family,
                message="Toggle is ambiguous in deterministic mode; tell me explicitly to show or hide the layer.",
                reason_code="toggle_requires_explicit_state",
            )
        else:
            return self._clarification(
                family=family,
                message="Please specify whether to show or hide the layer.",
                reason_code="unsupported_visibility_operation",
            )

        args: dict[str, Any] = {
            "scenario_id": scenario,
            "layer_name": layer_ref,
            "visible": visible,
        }
        if isinstance(properties.get("z_index"), int):
            args["z_index"] = int(properties["z_index"])
        return IntentToolPlan(
            intent_family=family,
            tool_steps=[MappedToolStep(tool_name="layer.update_state", arguments=args)],
        )

    def _map_artifact_inspection(
        self,
        *,
        properties: dict[str, Any],
        scenario_id: str | None,
    ) -> IntentToolPlan:
        family = "artifact_inspection"
        operation = str(properties.get("operation", "")).strip().lower()
        target = properties.get("target")
        if not isinstance(target, dict):
            return self._clarification(
                family=family,
                message="Please specify the target artifact (file id or relative path).",
                reason_code="missing_artifact_target",
            )
        args: dict[str, Any] = {}
        file_id = str(target.get("file_id", "")).strip()
        relative_path = str(target.get("relative_path", "")).strip()
        if file_id:
            args["file_id"] = file_id
        elif relative_path:
            scenario = str(scenario_id or "").strip()
            if not scenario:
                return self._clarification(
                    family=family,
                    message="Please select an active scenario before inspecting a relative-path artifact.",
                    reason_code="missing_scenario_id",
                )
            args["scenario_id"] = scenario
            args["relative_path"] = relative_path
        else:
            return self._clarification(
                family=family,
                message="Please provide artifact file_id or relative_path.",
                reason_code="missing_artifact_locator",
            )

        if operation == "stats":
            tool_name = "artifact.stats_geotiff"
        elif operation == "preview":
            tool_name = "artifact.preview_geotiff"
        elif operation == "describe":
            suffix = Path(relative_path).suffix.lower()
            if suffix in {".csv", ".tsv"}:
                tool_name = "artifact.describe_table"
            elif suffix in {".png", ".jpg", ".jpeg", ".webp"}:
                tool_name = "artifact.describe_plot"
            else:
                tool_name = "artifact.describe_geotiff"
        elif operation == "readout":
            return self._clarification(
                family=family,
                message="Readout requires coordinates and is not yet mapped deterministically. Please provide x/y and CRS.",
                reason_code="readout_not_yet_mapped",
            )
        else:
            return self._clarification(
                family=family,
                message="Please choose describe, preview, or stats for artifact inspection.",
                reason_code="unsupported_artifact_operation",
            )
        return IntentToolPlan(
            intent_family=family,
            tool_steps=[MappedToolStep(tool_name=tool_name, arguments=args)],
        )

    def _map_scenario_context(self, *, properties: dict[str, Any]) -> IntentToolPlan:
        family = "scenario_context_management"
        operation = str(properties.get("operation", "")).strip().lower()
        if operation == "list":
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[MappedToolStep(tool_name="scenario.list", arguments={})],
            )
        if operation in {"set_current", "select"}:
            scenario_ref = str(properties.get("scenario_ref", "")).strip()
            if not scenario_ref:
                return self._clarification(
                    family=family,
                    message="Please specify which scenario to select.",
                    reason_code="missing_scenario_ref",
                )
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[MappedToolStep(tool_name="scenario.set_current", arguments={"scenario_ref": scenario_ref})],
            )
        return self._clarification(
            family=family,
            message="Please specify list or set/select for scenario context.",
            reason_code="unsupported_scenario_operation",
        )

    def _map_compute_job_control(self, *, properties: dict[str, Any]) -> IntentToolPlan:
        family = "compute_job_control"
        operation = str(properties.get("operation", "")).strip().lower()
        if operation == "launch":
            implementation_name = str(
                properties.get("implementation_name", properties.get("job_definition_ref", ""))
            ).strip()
            if not implementation_name:
                return self._clarification(
                    family=family,
                    message="Please specify which job implementation to launch.",
                    reason_code="missing_implementation_name",
                )
            params = properties.get("params")
            if not isinstance(params, dict):
                params = {}
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[
                    MappedToolStep(
                        tool_name="jobs.run_predefined",
                        arguments={"implementation_name": implementation_name, "params": dict(params)},
                    )
                ],
            )

        job_ref = properties.get("job_ref")
        job_id = ""
        if isinstance(job_ref, dict):
            job_id = str(job_ref.get("job_id", job_ref.get("run_id", ""))).strip()
        if not job_id:
            job_id = str(properties.get("job_id", properties.get("run_id", ""))).strip()
        if not job_id:
            return self._clarification(
                family=family,
                message="Please provide a job id.",
                reason_code="missing_job_ref",
            )
        if operation == "status":
            tool_name = "runs.get_status"
            args = {"job_id": job_id}
        elif operation == "cancel":
            tool_name = "runs.cancel"
            args = {"job_id": job_id}
        elif operation == "logs":
            options = properties.get("log_options")
            if not isinstance(options, dict):
                options = {}
            args = {"job_id": job_id}
            if "head_lines" in options:
                args["head_lines"] = options.get("head_lines")
            if "tail_lines" in options:
                args["tail_lines"] = options.get("tail_lines")
            if "stream" in options:
                args["stream"] = options.get("stream")
            tool_name = "runs.get_logs"
        else:
            return self._clarification(
                family=family,
                message="Please specify launch, status, logs, or cancel for job control.",
                reason_code="unsupported_job_control_operation",
            )
        return IntentToolPlan(
            intent_family=family,
            tool_steps=[MappedToolStep(tool_name=tool_name, arguments=args)],
        )

    def _map_programmatic_workflow(
        self,
        *,
        properties: dict[str, Any],
        scenario_id: str | None,
    ) -> IntentToolPlan:
        family = "programmatic_workflow_authoring"
        scenario = str(scenario_id or "").strip()
        if not scenario:
            return self._clarification(
                family=family,
                message="Please select an active scenario before running or authoring scripts.",
                reason_code="missing_scenario_id",
            )
        operation = str(properties.get("operation", "")).strip().lower()
        path_ref = properties.get("path_ref")
        relative_path = ""
        if isinstance(path_ref, dict):
            relative_path = str(path_ref.get("relative_path", path_ref.get("path", ""))).strip()
        if not relative_path:
            relative_path = str(properties.get("relative_path", "")).strip()
        runtime_mode = str(properties.get("runtime_mode", "osgeo")).strip().lower() or "osgeo"
        if runtime_mode not in {"osgeo", "moonlib"}:
            runtime_mode = "osgeo"

        if operation == "run":
            if not relative_path:
                return self._clarification(
                    family=family,
                    message="Please specify the script or notebook path to run.",
                    reason_code="missing_path_for_run",
                )
            tool_name = "scenario.run_marimo_notebook" if relative_path.endswith(".marimo.py") else "scenario.run_script"
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[
                    MappedToolStep(
                        tool_name=tool_name,
                        arguments={
                            "scenario_id": scenario,
                            "relative_path": relative_path,
                            "runtime_mode": runtime_mode,
                        },
                    )
                ],
            )

        content_spec = properties.get("content_spec")
        content = ""
        if isinstance(content_spec, dict):
            content = str(content_spec.get("content", content_spec.get("text", ""))).strip()
        if operation in {"write", "edit", "write_and_run"}:
            if not relative_path or not content:
                return self._clarification(
                    family=family,
                    message="Please provide both relative path and script content.",
                    reason_code="missing_path_or_content_for_write",
                )
            if operation == "write_and_run":
                return IntentToolPlan(
                    intent_family=family,
                    tool_steps=[
                        MappedToolStep(
                            tool_name="scenario.write_run_script",
                            arguments={
                                "scenario_id": scenario,
                                "relative_path": relative_path,
                                "content": content,
                                "overwrite": operation == "edit",
                                "runtime_mode": runtime_mode,
                            },
                        )
                    ],
                )
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[
                    MappedToolStep(
                        tool_name="scenario.write_script",
                        arguments={
                            "scenario_id": scenario,
                            "relative_path": relative_path,
                            "content": content,
                            "overwrite": operation == "edit",
                        },
                    )
                ],
            )

        return self._clarification(
            family=family,
            message="Please specify write, edit, run, or write_and_run.",
            reason_code="unsupported_programmatic_workflow_operation",
        )

    def _map_lunar_environment_reasoning(self, *, properties: dict[str, Any]) -> IntentToolPlan:
        family = "lunar_environment_reasoning"
        question_type = str(properties.get("question_type", "")).strip().lower()
        if question_type not in {"fact_query", "interpretation", "mission_impact", "method_guidance"}:
            return self._clarification(
                family=family,
                message="Please specify whether this is a fact query, interpretation, mission impact, or method guidance request.",
                reason_code="missing_question_type",
            )
        phenomena = properties.get("phenomena")
        region_ref = str(properties.get("region_ref", "")).strip()
        underconstrained = not region_ref and not isinstance(phenomena, list)
        guidance = (
            "Provide an evidence-backed lunar environment analysis. "
            "Cite concrete evidence references when available, and clearly separate observed facts from inference. "
            "Include an explicit uncertainty section that states assumptions and what additional data would reduce uncertainty."
        )
        if underconstrained:
            guidance += " The request is underconstrained; explicitly call this out and provide bounded alternatives."
        return IntentToolPlan(
            intent_family=family,
            tool_steps=[],
            model_handoff_prompt=guidance,
            response_guardrails={
                "evidence_required": True,
                "uncertainty_required": True,
                "underconstrained": underconstrained,
            },
        )

    def _map_surface_route_planning(
        self,
        *,
        properties: dict[str, Any],
        scenario_id: str | None,
    ) -> IntentToolPlan:
        family = "surface_route_planning"
        operation = str(properties.get("operation", "")).strip().lower()
        origin_ref = str(properties.get("origin_ref", "")).strip()
        destination_ref = str(properties.get("destination_ref", "")).strip()
        if operation not in {"plan", "compare", "validate"}:
            return self._clarification(
                family=family,
                message="Please specify whether you want to plan, compare, or validate a route.",
                reason_code="unsupported_route_operation",
            )
        if not origin_ref or not destination_ref:
            return self._clarification(
                family=family,
                message="Please provide both origin and destination references for route planning.",
                reason_code="missing_route_endpoints",
            )
        query = f"route planning terrain viewshed connectivity {origin_ref} {destination_ref}".strip()
        guidance = (
            "Prepare a route-planning recommendation with constraints, tradeoffs, and alternatives. "
            "Use available terrain/connectivity tooling evidence when relevant, and include explicit uncertainty and data gaps."
        )
        return IntentToolPlan(
            intent_family=family,
            tool_steps=[
                MappedToolStep(
                    tool_name="tools.search",
                    arguments={"query": query, "limit": 5},
                )
            ],
            model_handoff_prompt=guidance,
            response_guardrails={
                "evidence_required": True,
                "uncertainty_required": True,
                "requires_alternatives": operation in {"plan", "compare"},
                "scenario_id_present": bool(str(scenario_id or "").strip()),
            },
        )

    def _map_evidence_packaging(
        self,
        *,
        properties: dict[str, Any],
        scenario_id: str | None,
    ) -> IntentToolPlan:
        family = "evidence_packaging"
        operation = str(properties.get("operation", "")).strip().lower()
        scope = properties.get("scope")
        if operation not in {"assemble", "export", "summarize"}:
            return self._clarification(
                family=family,
                message="Please specify whether to assemble, export, or summarize evidence.",
                reason_code="unsupported_evidence_operation",
            )
        if not isinstance(scope, dict) or not scope:
            return self._clarification(
                family=family,
                message="Please provide evidence scope (for example scenario, site candidate, or run id).",
                reason_code="missing_evidence_scope",
            )
        scenario_ref = str(scope.get("scenario_ref", "")).strip()
        if not scenario_ref and not str(scenario_id or "").strip():
            return self._clarification(
                family=family,
                message="Please select an active scenario or provide scenario_ref for evidence packaging.",
                reason_code="missing_scenario_scope",
            )
        guidance = (
            "Assemble an evidence package summary with explicit provenance. "
            "List included artifacts, data lineage, and confidence/uncertainty notes."
        )
        return IntentToolPlan(
            intent_family=family,
            tool_steps=[
                MappedToolStep(
                    tool_name="tools.search",
                    arguments={"query": "artifact describe table plot geotiff export", "limit": 5},
                )
            ],
            model_handoff_prompt=guidance,
            response_guardrails={
                "evidence_required": True,
                "uncertainty_required": True,
                "provenance_required": bool(properties.get("provenance_required", True)),
            },
        )

    def _map_location_navigation(
        self,
        *,
        properties: dict[str, Any],
        scenario_id: str | None,
        entity_resolution: SegmentEntityResolution | None = None,
    ) -> IntentToolPlan:
        family = "location_navigation"
        operation = str(properties.get("operation", "")).strip().lower()
        feature_ref = str(properties.get("feature_ref", "")).strip()
        feature_type = str(properties.get("context_filter", "")).strip() or None

        # Helper to find a resolved feature ID in the mentions.
        def _find_resolved_feature_id() -> str | None:
            if not entity_resolution:
                return None
            for mention in entity_resolution.mentions:
                if mention.kind == "feature" and mention.resolved_id:
                    # feature:2317 -> 2317
                    return mention.resolved_id.split(":")[-1]
            return None

        feature_id = _find_resolved_feature_id()
        logger.info(
            "intent_to_tool_planner location_navigation family=%s operation=%s feature_ref=%s feature_id=%s",
            family,
            operation,
            feature_ref,
            feature_id,
        )

        if operation in {"find", "search"}:
            if not feature_ref:
                return self._clarification(
                    family=family,
                    message="Please provide a feature name to search.",
                    reason_code="missing_feature_ref",
                )
            args: dict[str, Any] = {"query": feature_ref}
            if feature_type:
                args["feature_type"] = feature_type
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[MappedToolStep(tool_name="location.search", arguments=args)],
            )

        if operation == "goto":
            scenario = str(scenario_id or "").strip()
            if not scenario:
                return self._clarification(
                    family=family,
                    message="Please select an active scenario before navigating to a feature.",
                    reason_code="missing_scenario_id",
                )
            if not feature_ref and not feature_id:
                return self._clarification(
                    family=family,
                    message="Please provide the exact feature name to navigate to.",
                    reason_code="missing_feature_ref",
                )

            args: dict[str, Any] = {"scenario_id": scenario, "name": feature_ref}
            if feature_id:
                args["feature_id"] = feature_id
            if feature_type:
                args["feature_type"] = feature_type
            if isinstance(properties.get("zoom_level"), (int, float)):
                args["max_zoom"] = float(properties["zoom_level"])
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[MappedToolStep(tool_name="location.goto", arguments=args)],
            )

        if operation in {"identify", "nearby"}:
            point = properties.get("point")
            if not isinstance(point, dict):
                return self._clarification(
                    family=family,
                    message="Please provide projected x/y coordinates in ESRI:103878 for identify/nearby.",
                    reason_code="missing_point",
                )
            x = point.get("x")
            y = point.get("y")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                return self._clarification(
                    family=family,
                    message="Identify/nearby requires numeric x and y coordinates.",
                    reason_code="invalid_point",
                )
            args = {"x": float(x), "y": float(y)}
            if feature_type:
                args["feature_type"] = feature_type
            if isinstance(properties.get("radius_m"), (int, float)):
                args["radius_m"] = float(properties["radius_m"])
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[MappedToolStep(tool_name="location.identify", arguments=args)],
            )

        if operation == "pin":
            session_id = str(properties.get("session_id", "")).strip()
            if not session_id:
                return self._clarification(
                    family=family,
                    message="Pinning requires a session_id.",
                    reason_code="missing_session_id",
                )
            if not feature_ref and not feature_id:
                return self._clarification(
                    family=family,
                    message="Please provide a feature name to pin.",
                    reason_code="missing_feature_ref",
                )

            args = {"session_id": session_id, "name": feature_ref}
            if feature_id:
                args["feature_id"] = feature_id
            if feature_type:
                args["feature_type"] = feature_type
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[MappedToolStep(tool_name="location.pin_feature", arguments=args)],
            )

        if operation in {"list_pins", "pins"}:
            session_id = str(properties.get("session_id", "")).strip()
            if not session_id:
                return self._clarification(
                    family=family,
                    message="Please provide session_id to list pinned features.",
                    reason_code="missing_session_id",
                )
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[MappedToolStep(tool_name="location.list_pins", arguments={"session_id": session_id})],
            )

        if operation == "set_visibility":
            session_id = str(properties.get("session_id", "")).strip()
            if not session_id:
                return self._clarification(
                    family=family,
                    message="set_visibility requires a session_id.",
                    reason_code="missing_session_id",
                )
            args: dict[str, Any] = {"session_id": session_id}
            if isinstance(properties.get("visibility_state"), bool):
                args["visible"] = bool(properties["visibility_state"])
            elif isinstance(properties.get("visibility_state"), str):
                state = str(properties.get("visibility_state", "")).strip().lower()
                if state in {"show", "visible", "on"}:
                    args["visible"] = True
                elif state in {"hide", "hidden", "off"}:
                    args["visible"] = False
            if feature_type:
                args["types"] = [feature_type]
            return IntentToolPlan(
                intent_family=family,
                tool_steps=[MappedToolStep(tool_name="location.set_layer_filter", arguments=args)],
            )

        return self._clarification(
            family=family,
            message="Specify find, goto, identify, nearby, pin, or list_pins for location navigation.",
            reason_code="unsupported_location_navigation_operation",
        )
