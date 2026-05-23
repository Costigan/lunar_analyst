from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable

from backend.services.assistant.action_router_config import (
    ActionSpecConfig,
    AgentStepConfig,
    ToolStepConfig,
    default_action_router_spec_path,
    load_action_router_specs,
)
from backend.services.assistant.scenario_ref_normalization import normalize_scenario_reference

_PLACEHOLDER_EXACT_PATTERN = re.compile(r"^\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}$")
_PLACEHOLDER_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_COMPLEXITY_MARKER_RE = re.compile(
    r"\b(if|when|unless|only if|because|while|except|provided that)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlannedToolStep:
    tool_name: str
    arguments_template: dict[str, Any]
    arguments: dict[str, Any]


@dataclass(frozen=True)
class PlannedAgentStep:
    objective: str
    allowed_tools: list[str]
    output_schema: dict[str, Any]
    max_iterations: int
    max_output_tokens: int
    timeout_ms: int


PlannedStep = PlannedToolStep | PlannedAgentStep


@dataclass(frozen=True)
class PlannedAction:
    action_id: str
    segment: str
    slots: dict[str, Any]
    steps: list[PlannedStep]


@dataclass(frozen=True)
class ActionSpec:
    action_id: str
    priority: int
    patterns: list[re.Pattern[str]]
    deny_patterns: list[re.Pattern[str]]
    deny_if_complex: bool
    steps: list[ToolStepConfig | AgentStepConfig]


@dataclass(frozen=True)
class CommandPlan:
    actions: list[PlannedAction]
    unmatched_segments: list[str]

    @property
    def is_fully_matched(self) -> bool:
        return len(self.actions) > 0 and len(self.unmatched_segments) == 0


@dataclass(frozen=True)
class ScenarioCommandContext:
    scenario_refs: set[str]
    layer_names: set[str]
    layer_ids: set[str]


def _normalize_scenario_reference(raw: str) -> str:
    return normalize_scenario_reference(raw)


def _clean_layer_name(raw: str) -> str:
    cleaned = str(raw or "").strip().strip('"').strip("'")
    if not cleaned:
        return ""
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(" .,:;!?")
    return cleaned


def _looks_like_file_reference(raw: str) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return False
    return bool(re.search(r"\.(tif|tiff|csv|json|geojson|png|jpg|jpeg|webp|txt|md|py)\b", text))


def _split_prompt_segments(prompt: str) -> list[str]:
    text = str(prompt or "").strip()
    if not text:
        return []
    segments: list[str] = []
    current: list[str] = []
    quote_char: str | None = None
    index = 0
    while index < len(text):
        ch = text[index]
        if quote_char is None and ch in {'"', "'"}:
            quote_char = ch
            current.append(ch)
            index += 1
            continue
        if quote_char is not None:
            current.append(ch)
            if ch == quote_char:
                quote_char = None
            index += 1
            continue

        lowered_tail = text[index:].lower()
        if lowered_tail.startswith(" and then "):
            segment = "".join(current).strip(" ,")
            if segment:
                segments.append(segment)
            current = []
            index += len(" and then ")
            continue
        if lowered_tail.startswith(" then "):
            segment = "".join(current).strip(" ,")
            if segment:
                segments.append(segment)
            current = []
            index += len(" then ")
            continue
        if ch in {";", "\n"}:
            segment = "".join(current).strip(" ,")
            if segment:
                segments.append(segment)
            current = []
            index += 1
            continue
        current.append(ch)
        index += 1

    tail = "".join(current).strip(" ,")
    if tail:
        segments.append(tail)
    return segments


def _render_template(template: Any, slots: dict[str, Any]) -> Any:
    if isinstance(template, dict):
        return {key: _render_template(value, slots) for key, value in template.items()}
    if isinstance(template, list):
        return [_render_template(item, slots) for item in template]
    if isinstance(template, str):
        exact = _PLACEHOLDER_EXACT_PATTERN.match(template)
        if exact:
            return slots.get(exact.group(1))

        def _replace(match: re.Match[str]) -> str:
            name = str(match.group(1))
            value = slots.get(name)
            return "" if value is None else str(value)

        return _PLACEHOLDER_PATTERN.sub(_replace, template)
    return template


def _extract_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text[index:])
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _match_identifier(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if match is None:
        return None
    return str(match.group(1)).strip()


def _match_quoted(text: str) -> str | None:
    match = re.search(r'"([^"]+)"', text)
    if match is None:
        return None
    return str(match.group(1)).strip()


def _extract_slope_threshold(text: str) -> float | None:
    direct = re.search(r"slope[^\n\r]*?(?:<=|≤)\s*([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    if direct:
        try:
            return float(direct.group(1))
        except Exception:
            return None
    phrase = re.search(
        r"slope[^\n\r]*?(?:less than or equal to|at most|no more than|[0-9]+(?:\.[0-9]+)?\s*degrees?\s*or\s*less)\s*([0-9]+(?:\.[0-9]+)?)",
        text,
        re.IGNORECASE,
    )
    if phrase:
        try:
            return float(phrase.group(1))
        except Exception:
            pass
    reverse_phrase = re.search(
        r"slope[^\n\r]*?\b([0-9]+(?:\.[0-9]+)?)\s*degrees?\s*or\s*less\b",
        text,
        re.IGNORECASE,
    )
    if reverse_phrase:
        try:
            return float(reverse_phrase.group(1))
        except Exception:
            return None
    return None


def _extract_output_tif_name(text: str) -> str | None:
    match = re.search(r"(?:named|called)\s+([A-Za-z0-9_.-]+\.(?:tif|tiff))\b", text, re.IGNORECASE)
    if match is None:
        match = re.search(r"\b([A-Za-z0-9_.-]+\.(?:tif|tiff))\b", text, re.IGNORECASE)
    if match is None:
        return None
    return str(match.group(1)).strip()


class HybridCommandRouter:
    def __init__(
        self,
        *,
        enabled: bool = True,
        spec_path: str | Path | None = None,
        enable_agent_substeps: bool = False,
        scenario_context_resolver: Callable[[str | None], ScenarioCommandContext | None] | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._enable_agent_substeps = bool(enable_agent_substeps)
        self._scenario_context_resolver = scenario_context_resolver
        self._spec_path = Path(spec_path).resolve() if spec_path is not None else default_action_router_spec_path()
        loaded_specs = load_action_router_specs(spec_path=self._spec_path)
        self._specs = sorted(
            [
                ActionSpec(
                    action_id=item.action_id,
                    priority=item.priority,
                    patterns=item.patterns,
                    deny_patterns=item.deny_patterns,
                    deny_if_complex=item.deny_if_complex,
                    steps=item.steps,
                )
                for item in loaded_specs
            ],
            key=lambda item: item.priority,
            reverse=True,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def spec_path(self) -> Path:
        return self._spec_path

    def plan(self, *, prompt: str, scenario_id: str | None) -> CommandPlan:
        if not self._enabled:
            return CommandPlan(actions=[], unmatched_segments=[])
        segments = _split_prompt_segments(prompt)
        if not segments:
            return CommandPlan(actions=[], unmatched_segments=[])

        planned: list[PlannedAction] = []
        unmatched: list[str] = []
        for segment in segments:
            action = self._plan_segment(segment=segment, scenario_id=scenario_id)
            if action is None:
                unmatched.append(segment)
                continue
            planned.append(action)
        return CommandPlan(actions=planned, unmatched_segments=unmatched)

    def _plan_segment(self, *, segment: str, scenario_id: str | None) -> PlannedAction | None:
        context: ScenarioCommandContext | None = None
        if callable(self._scenario_context_resolver):
            try:
                context = self._scenario_context_resolver(scenario_id)
            except Exception:
                context = None
        for spec in self._specs:
            if spec.deny_if_complex and _COMPLEXITY_MARKER_RE.search(segment):
                continue
            if any(pattern.search(segment) for pattern in spec.deny_patterns):
                continue
            if not self._enable_agent_substeps and any(isinstance(step, AgentStepConfig) for step in spec.steps):
                continue
            for pattern in spec.patterns:
                match = pattern.match(segment)
                if match is None:
                    continue
                slots = {k: v for k, v in match.groupdict().items()}
                slots["segment"] = segment
                if scenario_id:
                    slots["scenario_id"] = scenario_id
                normalized = self._normalize_slots(spec.action_id, slots)
                if normalized is None:
                    continue
                if not self._slots_match_context(spec.action_id, normalized, context):
                    continue
                planned_steps: list[PlannedStep] = []
                for step in spec.steps:
                    if isinstance(step, ToolStepConfig):
                        planned_steps.append(
                            PlannedToolStep(
                                tool_name=step.tool_name,
                                arguments_template=dict(step.arguments),
                                arguments=_render_template(step.arguments, normalized),
                            )
                        )
                    else:
                        planned_steps.append(
                            PlannedAgentStep(
                                objective=str(_render_template(step.objective, normalized)),
                                allowed_tools=list(step.allowed_tools),
                                output_schema=dict(step.output_schema),
                                max_iterations=int(step.max_iterations),
                                max_output_tokens=int(step.max_output_tokens),
                                timeout_ms=int(step.timeout_ms),
                            )
                        )
                return PlannedAction(
                    action_id=spec.action_id,
                    segment=segment,
                    slots=normalized,
                    steps=planned_steps,
                )
        return None

    def _slots_match_context(
        self,
        action_id: str,
        slots: dict[str, Any],
        context: ScenarioCommandContext | None,
    ) -> bool:
        if context is None:
            return True
        if action_id == "scenario.switch":
            raw_ref = str(slots.get("scenario_ref", "")).strip()
            if not raw_ref:
                return False
            normalized_ref = _normalize_scenario_reference(raw_ref)
            if not normalized_ref:
                return False
            refs = {item for item in context.scenario_refs if item}
            return normalized_ref in refs
        if action_id in {"layer.set_visible_by_name", "layer.resolve_visibility_with_agent"}:
            key = "layer_name" if action_id == "layer.set_visible_by_name" else "layer_query"
            raw_name = _clean_layer_name(str(slots.get(key, "")))
            if not raw_name:
                return False
            normalized_name = _normalize_scenario_reference(raw_name)
            if not normalized_name:
                return False
            for candidate in context.layer_names:
                normalized_candidate = _normalize_scenario_reference(str(candidate))
                if not normalized_candidate:
                    continue
                if normalized_candidate == normalized_name:
                    return True
                if normalized_name in normalized_candidate:
                    return True
            return False
        if action_id == "layer.set_state_by_id":
            layer_id = str(slots.get("layer_id", "")).strip()
            if not layer_id:
                return False
            return layer_id in {str(item).strip() for item in context.layer_ids if str(item).strip()}
        return True

    def _normalize_slots(self, action_id: str, slots: dict[str, Any]) -> dict[str, Any] | None:
        normalized = dict(slots)
        segment = str(normalized.get("segment", ""))
        lower_segment = segment.lower()
        if action_id == "capabilities.describe":
            return normalized
        if action_id == "jobs.list_predefined":
            return normalized
        if action_id in {"jobs.run_predefined", "job.launch"}:
            name = str(normalized.get("implementation_name", "")).strip()
            if not name:
                return None
            normalized["implementation_name"] = name.replace("-", "_")
            normalized["params"] = _extract_json_object(segment) or {}
            return normalized
        if action_id == "scenario.switch":
            scenario_ref = _normalize_scenario_reference(str(normalized.get("scenario_ref", "")))
            if not scenario_ref:
                return None
            normalized["scenario_ref"] = scenario_ref
            return normalized
        if action_id == "scenario.list":
            return normalized
        if action_id in {"scenario.list_scripts", "scenario.list_notebooks"}:
            if not normalized.get("scenario_id"):
                return None
            return normalized
        if action_id in {"scenario.run_script", "scenario.run_marimo_notebook"}:
            if not normalized.get("scenario_id"):
                return None
            rel = _match_quoted(segment) or str(normalized.get("relative_path", "")).strip()
            rel = _clean_layer_name(rel)
            if not rel:
                return None
            normalized["relative_path"] = rel
            return normalized
        if action_id == "runs.get_logs":
            job_id = str(normalized.get("job_id", "")).strip()
            if not job_id:
                return None
            head_match = re.search(r"\bhead\s+([0-9]+)", lower_segment)
            tail_match = re.search(r"\btail\s+([0-9]+)", lower_segment)
            normalized["job_id"] = job_id
            normalized["head_lines"] = int(head_match.group(1)) if head_match else 40
            normalized["tail_lines"] = int(tail_match.group(1)) if tail_match else 80
            normalized["stream"] = "combined" if "combined" in lower_segment else ("stderr" if "stderr" in lower_segment else "stdout")
            return normalized
        if action_id in {"runs.get_status", "runs.cancel"}:
            job_id = str(normalized.get("job_id", "")).strip()
            if not job_id:
                return None
            normalized["job_id"] = job_id
            return normalized
        if action_id == "scenario.revoke_script_overwrite":
            if not normalized.get("scenario_id"):
                return None
            rel = _match_quoted(segment) or str(normalized.get("relative_path", "")).strip()
            rel = _clean_layer_name(rel)
            if not rel:
                return None
            normalized["relative_path"] = rel
            return normalized
        if action_id == "product.list":
            scenario_ref = str(normalized.get("scenario_ref", "")).strip()
            if scenario_ref:
                normalized["scenario_id"] = scenario_ref
            if not normalized.get("scenario_id"):
                return None
            return normalized
        if action_id == "product.files":
            product_id = str(normalized.get("product_id", "")).strip()
            if not product_id:
                return None
            normalized["product_id"] = product_id
            return normalized
        if action_id == "scenario.import_geotiff":
            if not normalized.get("scenario_id"):
                explicit_scenario = _match_identifier(lower_segment, r"scenario(?:_id)?\s+([a-z0-9_\-]+)")
                if explicit_scenario:
                    normalized["scenario_id"] = explicit_scenario
            source_path = _match_quoted(segment)
            if not normalized.get("scenario_id") or not source_path:
                return None
            normalized["source_path"] = source_path
            return normalized
        if action_id == "scenario.move_path":
            if not normalized.get("scenario_id"):
                explicit_scenario = _match_identifier(lower_segment, r"scenario(?:_id)?\s+([a-z0-9_\-]+)")
                if explicit_scenario:
                    normalized["scenario_id"] = explicit_scenario
            quoted = re.findall(r'"([^"]+)"', segment)
            if not normalized.get("scenario_id") or len(quoted) < 2:
                return None
            normalized["source_relative_path"] = str(quoted[0]).strip()
            normalized["target_relative_path"] = str(quoted[1]).strip()
            return normalized
        if action_id in {
            "raster.calculate.slope_threshold_mask_transparent",
            "raster.calculate.slope_threshold_mask_binary",
        }:
            if not normalized.get("scenario_id"):
                return None
            threshold = _extract_slope_threshold(segment)
            if threshold is None:
                return None
            output_name = _extract_output_tif_name(segment)
            if output_name:
                output_relative_path = output_name
            else:
                threshold_label = str(threshold).replace(".", "p")
                output_relative_path = f"slope_le_{threshold_label}deg_mask.tif"
            if action_id == "raster.calculate.slope_threshold_mask_transparent":
                normalized["expression"] = f"where(slope <= {threshold}, 1, nodata())"
            else:
                normalized["expression"] = f"slope <= {threshold}"
            normalized["output_relative_path"] = output_relative_path
            return normalized
        if action_id == "layer.set_state_by_id":
            layer_id = str(normalized.get("layer_id", "")).strip()
            if not layer_id:
                return None
            opacity_match = re.search(r"\bopacity\s+([0-9]+(?:\.[0-9]+)?)", lower_segment)
            visible_explicit: bool | None = None
            if "visible true" in lower_segment:
                visible_explicit = True
            elif "visible false" in lower_segment:
                visible_explicit = False
            normalized["layer_id"] = layer_id
            normalized["opacity"] = float(opacity_match.group(1)) if opacity_match else None
            normalized["visible_explicit"] = visible_explicit
            if normalized["opacity"] is None and normalized["visible_explicit"] is None:
                return None
            return normalized
        if action_id in {"layer.set_visible_by_name", "layer.resolve_visibility_with_agent"}:
            layer_name = _clean_layer_name(str(normalized.get("layer_name", "")))
            toggle = str(normalized.get("toggle", "")).strip().lower()
            if action_id == "layer.resolve_visibility_with_agent":
                layer_query = _clean_layer_name(str(normalized.get("layer_query", "")))
                if not layer_query or not toggle:
                    return None
                if _looks_like_file_reference(layer_query):
                    return None
                normalized["layer_query"] = layer_query
                normalized["visible"] = toggle in {"on", "show", "visible"}
                return normalized
            if not layer_name or not toggle:
                return None
            if _looks_like_file_reference(layer_name):
                return None
            normalized["layer_name"] = layer_name
            normalized["visible"] = toggle in {"on", "show", "visible"}
            return normalized
        if action_id == "layer.list_visible":
            return normalized
        return None
