from __future__ import annotations

import json
from urllib.parse import quote
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import build_service_container


def _write_config(path: Path, workspace: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "[backend]",
                f'workspace_root = "{workspace.as_posix()}"',
                "",
                "[backend.llm]",
                "enabled = true",
                "",
                "[backend.llm.ollama]",
                "enabled = false",
                "",
                "[backend.mcp]",
                "enabled = true",
                "http_enabled = true",
                "stdio_enabled = true",
                "sse_enabled = true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _reset_services(monkeypatch, config_path: Path) -> None:
    import backend.api.dependencies as deps

    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))
    deps.SERVICES = build_service_container()


def test_mcp_sse_initialize_round_trip(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace)
    _reset_services(monkeypatch, cfg)
    client = TestClient(create_app())
    opened = client.get("/api/v1/mcp/sse?oneshot=true")
    assert opened.status_code == 200
    assert opened.headers.get("content-type", "").startswith("text/event-stream")
    session_id = str(opened.headers.get("x-mcp-sse-session-id", "")).strip()
    post_path = str(opened.headers.get("x-mcp-sse-post-path", "")).strip()
    assert session_id
    assert post_path == f"/api/v1/mcp/sse/{session_id}"

    posted = client.post(
        post_path,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert posted.status_code == 200
    payload = posted.json()
    assert payload.get("jsonrpc") == "2.0"
    assert isinstance(payload.get("result"), dict)


def test_mcp_sse_message_unknown_session_returns_404(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace)
    _reset_services(monkeypatch, cfg)
    client = TestClient(create_app())

    posted = client.post(
        "/api/v1/mcp/sse/missing_session",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert posted.status_code == 404


def test_mcp_sse_post_root_compat_handles_jsonrpc(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace)
    _reset_services(monkeypatch, cfg)
    client = TestClient(create_app())

    posted = client.post(
        "/api/v1/mcp/sse",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert posted.status_code == 200
    payload = posted.json()
    assert payload.get("jsonrpc") == "2.0"
    assert isinstance(payload.get("result"), dict)


def test_mcp_sse_message_compat_accepts_encoded_endpoint_descriptor(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace)
    _reset_services(monkeypatch, cfg)
    client = TestClient(create_app())
    opened = client.get("/api/v1/mcp/sse?oneshot=true")
    assert opened.status_code == 200
    session_id = str(opened.headers.get("x-mcp-sse-session-id", "")).strip()
    post_path = str(opened.headers.get("x-mcp-sse-post-path", "")).strip()
    assert session_id
    assert post_path

    descriptor = quote(json.dumps({"session_id": session_id, "post_path": post_path}), safe="")
    posted = client.post(
        f"/api/v1/mcp/{descriptor}",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert posted.status_code == 200
    payload = posted.json()
    assert payload.get("jsonrpc") == "2.0"
    assert isinstance(payload.get("result"), dict)
