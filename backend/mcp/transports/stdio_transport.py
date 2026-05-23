from __future__ import annotations

import json
import sys
from typing import Any

from backend.mcp.server import McpServer


def run_stdio(server: McpServer, services: Any) -> int:
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Request must be an object.")
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue
        response = server.handle_jsonrpc(services, payload)
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
    return 0
