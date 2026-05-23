from __future__ import annotations

from contextlib import asynccontextmanager
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
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _reset_services(monkeypatch, config_path: Path) -> None:
    import backend.api.app as app_module
    import backend.api.dependencies as deps

    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))
    monkeypatch.delenv("LUNAR_ANALYST_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(app_module, "bootstrap_status", lambda: "skipped")
    monkeypatch.setattr(app_module, "bootstrap_pythonnet", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module, "configure_gdal_runtime", lambda: None)
    deps.SERVICES = build_service_container()


def _create_test_client() -> TestClient:
    app = create_app()

    @asynccontextmanager
    async def _noop_lifespan(_: object):
        yield

    app.router.lifespan_context = _noop_lifespan
    return TestClient(app)


def test_assistant_ws_streams_events(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace)
    _reset_services(monkeypatch, cfg)
    client = _create_test_client()

    session = client.post("/api/v1/assistant/sessions", json={"title": "WS Session"})
    assert session.status_code == 200
    session_id = session.json()["session_id"]

    with client.websocket_connect(f"/api/v1/assistant/sessions/{session_id}/events") as ws:
        turn = client.post(
            f"/api/v1/assistant/sessions/{session_id}/turns",
            json={"prompt": "describe capabilities"},
        )
        assert turn.status_code == 200
        payload = ws.receive_json()
        assert payload["session_id"] == session_id
        assert payload["event"] in {
            "assistant_turn_started",
            "assistant_tool_call_proposed",
            "assistant_tool_call_started",
            "assistant_tool_call_completed",
            "assistant_turn_completed",
        }
