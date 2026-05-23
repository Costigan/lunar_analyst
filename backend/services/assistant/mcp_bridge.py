from __future__ import annotations

from typing import Any

from backend.services.assistant.tool_registry import execute_tool, list_tools_schema


def mcp_list_tools() -> list[dict[str, Any]]:
    return list_tools_schema()


def mcp_call_tool(services: Any, *, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return execute_tool(services, tool_name=name, arguments=arguments)
