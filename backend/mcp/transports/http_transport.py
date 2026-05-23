from __future__ import annotations

from typing import Any

from backend.mcp.server import McpServer


def handle_http_mcp(server: McpServer, services: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return server.handle_jsonrpc(services, payload)
