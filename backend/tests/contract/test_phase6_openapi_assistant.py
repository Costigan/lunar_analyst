from __future__ import annotations

from backend.api.app import create_app


def test_openapi_includes_assistant_routes() -> None:
    app = create_app()
    schema = app.openapi()
    paths = schema.get("paths", {})
    assert "/api/v1/assistant/sessions" in paths
    assert "/api/v1/assistant/sessions/{session_id}/turns" in paths
    assert "/api/v1/assistant/providers" in paths


def test_openapi_includes_mcp_route() -> None:
    app = create_app()
    schema = app.openapi()
    paths = schema.get("paths", {})
    assert "/api/v1/mcp" in paths
