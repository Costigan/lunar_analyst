from __future__ import annotations

from typing import Any

from backend.services.assistant.mcp_bridge import mcp_call_tool, mcp_list_tools
from backend.services.assistant.tool_registry import action_type_for_tool


class McpServer:
    protocol_version = "2025-03-26"

    def handle_jsonrpc(self, services: Any, payload: dict[str, Any]) -> dict[str, Any]:
        method = str(payload.get("method", "")).strip()
        req_id = payload.get("id")
        params = payload.get("params", {})
        if not isinstance(params, dict):
            params = {}

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": self.protocol_version,
                    "serverInfo": {"name": "lunar-analyst-mcp", "version": "0.1.0"},
                    "capabilities": {"tools": {"listChanged": False}},
                }
                return _ok(req_id, result)
            if method == "tools/list":
                tools = []
                for tool in mcp_list_tools():
                    tools.append(
                        {
                            "name": tool["name"],
                            "description": tool.get("description", ""),
                            "inputSchema": {"type": "object", "additionalProperties": True},
                            "annotations": {
                                "requiresConfirmation": bool(tool.get("requires_confirmation", False)),
                                "actionType": tool.get("action_type"),
                            },
                        }
                    )
                return _ok(req_id, {"tools": tools})
            if method == "tools/call":
                name = str(params.get("name", "")).strip()
                arguments = params.get("arguments", {})
                if not isinstance(arguments, dict):
                    arguments = {}
                action_type = action_type_for_tool(name)
                if action_type is not None and not bool(arguments.get("_confirmed", False)):
                    return _error(
                        req_id,
                        code=-32001,
                        message="confirmation_required",
                        data={
                            "tool_name": name,
                            "action_type": action_type.value,
                            "hint": "Resubmit with arguments._confirmed=true if caller has user approval.",
                        },
                    )
                # Strip transport-level confirmation metadata before tool schema validation.
                tool_arguments = dict(arguments)
                tool_arguments.pop("_confirmed", None)
                result = mcp_call_tool(services, name=name, arguments=tool_arguments)
                return _ok(
                    req_id,
                    {
                        "content": [{"type": "text", "text": _render_tool_result(name, result)}],
                        "structuredContent": result,
                        "isError": False,
                    },
                )
            return _error(req_id, code=-32601, message=f"Method not found: {method}")
        except Exception as exc:
            return _error(req_id, code=-32000, message="tool_error", data={"error": str(exc)})


def _render_tool_result(name: str, result: dict[str, Any]) -> str:
    keys = ", ".join(sorted(result.keys())[:8])
    return f"{name} completed. keys: {keys or '(none)'}"


def _ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, *, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    if data:
        payload["error"]["data"] = data
    return payload
