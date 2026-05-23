from __future__ import annotations

from backend.api.job_runtime import discover_tool_implementations
from backend.contracts.models import ToolDefinition, ToolDefinitionsResponse, ToolVisibility


def list_tool_definitions(
    *,
    include_drafts: bool = False,
    include_system: bool = True,
) -> ToolDefinitionsResponse:
    definitions: list[ToolDefinition] = []
    for spec in discover_tool_implementations(include_drafts=include_drafts).values():
        definition = spec.tool_definition
        if definition.visibility == ToolVisibility.SYSTEM and not include_system:
            continue
        definitions.append(definition)
    definitions.sort(key=lambda item: (item.visibility.value, item.tool_name))
    return ToolDefinitionsResponse(definitions=definitions)


def get_tool_definition(
    tool_name: str,
    *,
    include_drafts: bool = False,
    include_system: bool = True,
) -> ToolDefinition:
    needle = str(tool_name).strip()
    if not needle:
        raise KeyError("tool_name is required")
    for definition in list_tool_definitions(
        include_drafts=include_drafts,
        include_system=include_system,
    ).definitions:
        if definition.tool_name == needle or definition.implementation_name == needle or definition.handler_name == needle:
            return definition
    raise KeyError(f"Tool not found: {tool_name}")
