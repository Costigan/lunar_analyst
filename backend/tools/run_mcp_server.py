from __future__ import annotations

from backend.api.dependencies import get_services
from backend.mcp.server import McpServer
from backend.mcp.transports.stdio_transport import run_stdio


def main() -> int:
    services = get_services()
    server = McpServer()
    return run_stdio(server, services)


if __name__ == "__main__":
    raise SystemExit(main())
