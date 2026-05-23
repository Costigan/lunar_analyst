from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.services.assistant.tool_registry import action_type_for_tool


@dataclass(frozen=True)
class SegmentOutcome:
    segment_id: str
    prompt_class: str
    required: bool
    status: str
    postcondition_checked: bool
    postcondition_passed: bool | None


def _intent_family_is_mutating(*, intent_family: str, intent_properties: dict[str, Any]) -> bool:
    family = str(intent_family or "").strip()
    operation = str(intent_properties.get("operation", "")).strip().lower()
    if family in {
        "artifact_inspection",
        "lunar_environment_reasoning",
        "surface_route_planning",
        "evidence_packaging",
        "location_navigation",
    }:
        return False
    if family == "layer_style_update" and operation in {"list", "list_colormaps"}:
        return False
    if family == "scenario_context_management" and operation == "list":
        return False
    if family == "compute_job_control" and operation in {"status", "logs"}:
        return False
    return True


def _segment_prompt_class(segment: dict[str, Any]) -> str:
    classification = dict(segment.get("classification", {}) or {})
    classification_label = str(classification.get("label", "") or "")
    if classification_label in {"command", "create_product"}:
        return "mutating"
    if classification_label == "intent_family":
        intent_family = str(classification.get("intent_family", "")).strip()
        intent_properties = classification.get("intent_properties")
        if not isinstance(intent_properties, dict):
            intent_properties = {}
        return "mutating" if _intent_family_is_mutating(intent_family=intent_family, intent_properties=intent_properties) else "read_only"
    return "read_only"


def _evaluate_mutating_postcondition(
    *,
    tool_name: str,
    result: dict[str, Any],
    current_scenario_id: str | None,
) -> bool | None:
    if tool_name == "scenario.set_current":
        next_id = str((result.get("scenario") or {}).get("scenario_id", "")).strip()
        return bool(next_id and (current_scenario_id is None or next_id == current_scenario_id))
    if tool_name in {"layer.update_state", "layer.apply_colormap"}:
        return bool(result.get("layer_id") or result.get("layer"))
    if tool_name in {"scenario.write_script", "scenario.write_run_script"}:
        return bool(result.get("relative_path") or result.get("script_path"))
    return None


def compute_success_semantics(
    *,
    execution_plan_segments: list[dict[str, Any]],
    tool_calls: list[Any],
    current_scenario_id: str | None,
) -> tuple[str, list[SegmentOutcome]]:
    mutating_completed = False
    any_completed = False
    mutating_postcondition_checks: list[bool] = []
    for call in tool_calls:
        tool_name = str(getattr(call, "tool_name", "") or "")
        status = str(getattr(call, "status", "") or "")
        if status == "completed":
            any_completed = True
        if action_type_for_tool(tool_name) is None:
            continue
        if status == "completed":
            mutating_completed = True
            result = dict(getattr(call, "result", {}) or {})
            check = _evaluate_mutating_postcondition(
                tool_name=tool_name,
                result=result,
                current_scenario_id=current_scenario_id,
            )
            if check is not None:
                mutating_postcondition_checks.append(bool(check))
    if not mutating_completed:
        mutating_postcondition = False
    elif not mutating_postcondition_checks:
        mutating_postcondition = True
    else:
        mutating_postcondition = all(mutating_postcondition_checks)

    outcomes: list[SegmentOutcome] = []
    required_outcomes: list[str] = []
    for segment in execution_plan_segments:
        execution_mode = str(segment.get("execution_mode", "llm"))
        required = bool(segment.get("required", True))
        prompt_class = _segment_prompt_class(segment)
        if execution_mode == "blocked":
            status = "blocked"
            checked = prompt_class == "mutating"
            passed = False if checked else None
        elif execution_mode == "deterministic":
            if prompt_class == "mutating":
                status = "completed" if mutating_completed else "failed"
                checked = True
                passed = mutating_postcondition if mutating_completed else False
            else:
                status = "completed" if any_completed else "failed"
                checked = False
                passed = None
        else:
            prompt_class = "read_only"
            status = "completed"
            checked = False
            passed = None
        outcomes.append(
            SegmentOutcome(
                segment_id=str(segment.get("segment_id", "")),
                prompt_class=prompt_class,
                required=required,
                status=status,
                postcondition_checked=checked,
                postcondition_passed=passed,
            )
        )
        if required:
            required_outcomes.append(status)

    if not required_outcomes:
        aggregate = "success"
    elif all(item == "completed" for item in required_outcomes):
        aggregate = "success"
    elif any(item == "completed" for item in required_outcomes):
        aggregate = "partial_success"
    else:
        aggregate = "failed"
    return aggregate, outcomes
