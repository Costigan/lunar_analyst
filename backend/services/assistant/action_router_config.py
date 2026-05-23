from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml

from backend.services.assistant.tool_registry import action_type_for_tool, list_tools_schema

_PLACEHOLDER_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass(frozen=True)
class ToolStepConfig:
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AgentStepConfig:
    objective: str
    allowed_tools: list[str]
    output_schema: dict[str, Any]
    max_iterations: int
    max_output_tokens: int
    timeout_ms: int


@dataclass(frozen=True)
class ActionSpecConfig:
    action_id: str
    priority: int
    patterns: list[re.Pattern[str]]
    deny_patterns: list[re.Pattern[str]]
    deny_if_complex: bool
    steps: list[ToolStepConfig | AgentStepConfig]


@dataclass(frozen=True)
class EntityKindRoutingRule:
    rule_id: str
    required_verbs: list[str]
    required_entity_kinds: list[str]
    intent_families: list[str]
    min_confidence: float
    allow_ambiguity: bool


_ALLOWED_BUILTIN_PLACEHOLDERS: set[str] = {
    "scenario_id",
    "segment",
    "visible",
    "toggle",
    "layer_name",
    "scenario_ref",
    "implementation_name",
    "params",
    "relative_path",
    "job_id",
    "head_lines",
    "tail_lines",
    "stream",
    "product_id",
    "source_path",
    "source_relative_path",
    "target_relative_path",
    "layer_id",
    "opacity",
    "visible_explicit",
    "layer_query",
    "expression",
    "output_relative_path",
}


def default_action_router_spec_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "assistant_action_router.yaml"


def load_action_router_specs(
    *,
    spec_path: str | Path | None,
) -> list[ActionSpecConfig]:
    path = Path(spec_path).resolve() if spec_path is not None else default_action_router_spec_path()
    if not path.exists():
        raise FileNotFoundError(f"Action router spec file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Action router spec root must be a mapping: {path}")
    version = raw.get("version")
    if version != 1:
        raise ValueError(f"Unsupported action router spec version={version!r}; expected 1")
    actions_raw = raw.get("actions")
    if not isinstance(actions_raw, list) or not actions_raw:
        raise ValueError("Action router spec must define a non-empty 'actions' list")

    available_tools = _available_tool_names()
    seen_ids: set[str] = set()
    loaded: list[ActionSpecConfig] = []
    for index, action_raw in enumerate(actions_raw):
        label = f"actions[{index}]"
        if not isinstance(action_raw, dict):
            raise ValueError(f"{label} must be a mapping")
        enabled = bool(action_raw.get("enabled", True))
        action_id = str(action_raw.get("action_id", "")).strip()
        if not action_id:
            raise ValueError(f"{label}.action_id is required")
        if action_id in seen_ids:
            raise ValueError(f"Duplicate action_id in router spec: {action_id}")
        seen_ids.add(action_id)
        if not enabled:
            continue
        priority = _as_int(action_raw.get("priority"), f"{label}.priority")
        deny_if_complex = bool(action_raw.get("deny_if_complex", False))
        patterns = _compile_pattern_list(action_raw.get("patterns"), field=f"{label}.patterns")
        deny_patterns = _compile_pattern_list(
            action_raw.get("deny_patterns", []),
            field=f"{label}.deny_patterns",
            allow_empty=True,
        )
        steps = _parse_steps(
            steps_raw=action_raw.get("steps"),
            field=f"{label}.steps",
            available_tools=available_tools,
        )
        _validate_placeholders(action_id=action_id, patterns=patterns, steps=steps)
        loaded.append(
            ActionSpecConfig(
                action_id=action_id,
                priority=priority,
                patterns=patterns,
                deny_patterns=deny_patterns,
                deny_if_complex=deny_if_complex,
                steps=steps,
            )
        )
    if not loaded:
        raise ValueError("Action router spec resolved to zero enabled actions")
    return loaded


def load_action_router_verb_aliases(
    *,
    spec_path: str | Path | None,
) -> dict[str, list[str]]:
    path = Path(spec_path).resolve() if spec_path is not None else default_action_router_spec_path()
    if not path.exists():
        return _default_verb_aliases()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return _default_verb_aliases()
    aliases_raw = raw.get("verb_aliases")
    if not isinstance(aliases_raw, dict):
        return _default_verb_aliases()
    merged = _default_verb_aliases()
    for canonical, values in aliases_raw.items():
        key = str(canonical).strip().lower()
        if not key:
            continue
        entries: list[str] = []
        if isinstance(values, list):
            entries = [str(item).strip() for item in values if str(item).strip()]
        elif isinstance(values, str) and values.strip():
            entries = [values.strip()]
        if not entries:
            continue
        merged.setdefault(key, [])
        merged[key].extend(entries)
    return merged


def load_entity_kind_routing_rules(
    *,
    spec_path: str | Path | None,
) -> list[EntityKindRoutingRule]:
    path = Path(spec_path).resolve() if spec_path is not None else default_action_router_spec_path()
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return []
    rules_raw = raw.get("entity_kind_routing_rules")
    if not isinstance(rules_raw, list):
        return []
    loaded: list[EntityKindRoutingRule] = []
    for idx, item in enumerate(rules_raw):
        if not isinstance(item, dict):
            continue
        label = f"entity_kind_routing_rules[{idx}]"
        rule_id = str(item.get("rule_id", "")).strip() or f"rule_{idx}"
        required_verbs = [
            str(value).strip().lower()
            for value in list(item.get("required_verbs", []))
            if str(value).strip()
        ]
        required_entity_kinds = [
            str(value).strip().lower()
            for value in list(item.get("required_entity_kinds", []))
            if str(value).strip()
        ]
        intent_families = [
            str(value).strip()
            for value in list(item.get("intent_families", []))
            if str(value).strip()
        ]
        try:
            min_confidence = float(item.get("min_confidence", 0.0))
        except Exception:
            raise ValueError(f"{label}.min_confidence must be numeric")
        allow_ambiguity = bool(item.get("allow_ambiguity", False))
        if not required_verbs or not required_entity_kinds:
            continue
        loaded.append(
            EntityKindRoutingRule(
                rule_id=rule_id,
                required_verbs=required_verbs,
                required_entity_kinds=required_entity_kinds,
                intent_families=intent_families,
                min_confidence=min_confidence,
                allow_ambiguity=allow_ambiguity,
            )
        )
    return loaded


def _default_verb_aliases() -> dict[str, list[str]]:
    return {
        "goto": ["go to", "zoom to", "zoom in on", "center on", "navigate to", "fly to"],
        "search": ["find", "search for", "look up", "lookup"],
        "show": ["turn on", "display", "reveal", "make visible"],
        "hide": ["turn off", "conceal", "make hidden"],
        "set_current": ["switch to", "use", "select", "change to"],
        "apply": ["set", "use", "apply"],
        "identify": ["identify"],
        "nearby": ["nearby", "near", "around"],
    }


def _compile_pattern_list(raw: Any, *, field: str, allow_empty: bool = False) -> list[re.Pattern[str]]:
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be a list")
    if not raw and not allow_empty:
        raise ValueError(f"{field} must be a non-empty list")
    compiled: list[re.Pattern[str]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field}[{idx}] must be a non-empty string")
        try:
            compiled.append(re.compile(item, re.IGNORECASE))
        except re.error as exc:
            raise ValueError(f"{field}[{idx}] invalid regex: {exc}") from exc
    return compiled


def _parse_steps(
    *,
    steps_raw: Any,
    field: str,
    available_tools: set[str],
) -> list[ToolStepConfig | AgentStepConfig]:
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError(f"{field} must be a non-empty list")
    parsed: list[ToolStepConfig | AgentStepConfig] = []
    for idx, step_raw in enumerate(steps_raw):
        label = f"{field}[{idx}]"
        if not isinstance(step_raw, dict):
            raise ValueError(f"{label} must be a mapping")
        kind = str(step_raw.get("kind", "")).strip().lower()
        if kind == "tool_call":
            tool_name = str(step_raw.get("tool_name", "")).strip()
            if not tool_name:
                raise ValueError(f"{label}.tool_name is required for tool_call step")
            if tool_name not in available_tools:
                raise ValueError(f"{label}.tool_name references unknown tool: {tool_name}")
            args = step_raw.get("arguments")
            if not isinstance(args, dict):
                raise ValueError(f"{label}.arguments must be an object")
            parsed.append(ToolStepConfig(tool_name=tool_name, arguments=args))
            continue
        if kind == "agent_call":
            objective = str(step_raw.get("objective", "")).strip()
            if not objective:
                raise ValueError(f"{label}.objective is required for agent_call step")
            raw_allowed = step_raw.get("allowed_tools")
            if not isinstance(raw_allowed, list) or not raw_allowed:
                raise ValueError(f"{label}.allowed_tools must be a non-empty list")
            allowed_tools: list[str] = []
            for tool in raw_allowed:
                name = str(tool).strip()
                if not name:
                    continue
                if name not in available_tools:
                    raise ValueError(f"{label}.allowed_tools references unknown tool: {name}")
                if action_type_for_tool(name) is not None:
                    raise ValueError(f"{label}.allowed_tools contains mutating tool not allowed in agent_call: {name}")
                allowed_tools.append(name)
            if not allowed_tools:
                raise ValueError(f"{label}.allowed_tools must contain at least one valid tool")
            output_schema = step_raw.get("output_schema")
            if not isinstance(output_schema, dict):
                raise ValueError(f"{label}.output_schema must be an object")
            max_iterations = _as_int(step_raw.get("max_iterations", 2), f"{label}.max_iterations")
            max_output_tokens = _as_int(step_raw.get("max_output_tokens", 512), f"{label}.max_output_tokens")
            timeout_ms = _as_int(step_raw.get("timeout_ms", 8000), f"{label}.timeout_ms")
            parsed.append(
                AgentStepConfig(
                    objective=objective,
                    allowed_tools=allowed_tools,
                    output_schema=output_schema,
                    max_iterations=max(1, max_iterations),
                    max_output_tokens=max(32, max_output_tokens),
                    timeout_ms=max(1000, timeout_ms),
                )
            )
            continue
        raise ValueError(f"{label}.kind must be one of: tool_call, agent_call")
    return parsed


def _validate_placeholders(
    *,
    action_id: str,
    patterns: list[re.Pattern[str]],
    steps: list[ToolStepConfig | AgentStepConfig],
) -> None:
    pattern_groups: set[str] = set()
    for pattern in patterns:
        pattern_groups.update(pattern.groupindex.keys())
    allowed = set(pattern_groups) | set(_ALLOWED_BUILTIN_PLACEHOLDERS)
    for step in steps:
        if isinstance(step, ToolStepConfig):
            placeholders = _collect_placeholders(step.arguments)
        else:
            placeholders = _collect_placeholders(step.objective)
        unknown = sorted([name for name in placeholders if name not in allowed])
        if unknown:
            raise ValueError(
                f"Unknown placeholder(s) in action {action_id}: {', '.join(unknown)}"
            )


def _collect_placeholders(value: Any) -> set[str]:
    collected: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            collected.update(_collect_placeholders(item))
        return collected
    if isinstance(value, list):
        for item in value:
            collected.update(_collect_placeholders(item))
        return collected
    if isinstance(value, str):
        for match in _PLACEHOLDER_PATTERN.finditer(value):
            collected.add(str(match.group(1)))
    return collected


def _available_tool_names() -> set[str]:
    names: set[str] = set()
    for item in list_tools_schema():
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            name = str(item.get("function", {}).get("name", "")).strip()
        if name:
            names.add(name)
    return names


def _as_int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
