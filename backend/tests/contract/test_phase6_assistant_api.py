from __future__ import annotations

import base64
from contextlib import asynccontextmanager
import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import build_service_container
from backend.worker.gdal_runtime import import_rasterio


def _write_config(path: Path, workspace: Path, assistant_db_rel: str) -> None:
    path.write_text(
        "\n".join(
            [
                "[backend]",
                f'workspace_root = "{workspace.as_posix()}"',
                "",
                "[backend.llm]",
                "enabled = true",
                'default_provider = "ollama"',
                'default_model = "qwen2.5-coder:7b-instruct-q4_K_M"',
                f'session_store_path = "{assistant_db_rel}"',
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


def _explicit_tool_prompt(*calls: tuple[str, dict[str, object]]) -> str:
    return "\n".join(
        f'{idx}) Call `{tool_name}` with {json.dumps(arguments)}'
        for idx, (tool_name, arguments) in enumerate(calls, start=1)
    )


def test_assistant_session_turn_confirmation_and_compaction(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace, ".assistant/assistant.db")
    _reset_services(monkeypatch, cfg)
    client = _create_test_client()

    created = client.post("/api/v1/assistant/sessions", json={"title": "Contract Session"})
    assert created.status_code == 200
    session_id = created.json()["session_id"]

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "assistant_api_scn", "name": "Assistant API Scenario", "owner": "test"},
    )
    assert scenario.status_code == 200
    created_scenario_id = scenario.json()["scenario_id"]
    scenario_dir = Path(scenario.json()["directory"])
    existing_script = scenario_dir / "existing_task.py"
    existing_script.write_text("print('preexisting')\n", encoding="utf-8")

    turn_set_scenario = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={"prompt": "set scenario assistant api"},
    )
    assert turn_set_scenario.status_code == 200
    payload_set = turn_set_scenario.json()
    assert payload_set["turn"]["status"] == "completed"
    assert payload_set["tool_calls"][0]["tool_name"] == "scenario.set_current"
    assert payload_set["tool_calls"][0]["result"]["status"] == "selected"
    assert payload_set["tool_calls"][0]["result"]["scenario"]["scenario_id"] == created_scenario_id

    overwrite_turn = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={
            "prompt": "write script \"existing_task.py\"\n```python\nprint('updated once')\n```",
        },
    )
    assert overwrite_turn.status_code == 200
    overwrite_payload = overwrite_turn.json()
    assert overwrite_payload["turn"]["status"] == "confirmation_required"
    overwrite_confirmation_id = overwrite_payload["confirmation"]["confirmation_id"]

    overwrite_decision = client.post(
        f"/api/v1/assistant/sessions/{session_id}/confirmations/{overwrite_confirmation_id}",
        json={"decision": "allow_once"},
    )
    assert overwrite_decision.status_code == 200
    assert overwrite_decision.json()["turn"]["status"] == "completed"

    overwrite_again = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={
            "prompt": "write script \"existing_task.py\"\n```python\nprint('updated twice')\n```",
        },
    )
    assert overwrite_again.status_code == 200
    assert overwrite_again.json()["turn"]["status"] == "completed"

    revoke = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={"prompt": "revoke overwrite approval \"existing_task.py\""},
    )
    assert revoke.status_code == 200
    assert revoke.json()["turn"]["status"] == "completed"

    overwrite_after_revoke = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={
            "prompt": "write script \"existing_task.py\"\n```python\nprint('updated after revoke')\n```",
        },
    )
    assert overwrite_after_revoke.status_code == 200
    assert overwrite_after_revoke.json()["turn"]["status"] == "confirmation_required"

    turn_1 = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={"prompt": "describe capabilities"},
    )
    assert turn_1.status_code == 200
    payload_1 = turn_1.json()
    assert payload_1["turn"]["status"] == "completed"
    assert payload_1["assistant_message"] is not None

    turn_2 = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={"prompt": 'launch job ping {"message":"hello"}'},
    )
    assert turn_2.status_code == 200
    payload_2 = turn_2.json()
    assert payload_2["turn"]["status"] == "confirmation_required"
    confirmation_id = payload_2["confirmation"]["confirmation_id"]

    decision = client.post(
        f"/api/v1/assistant/sessions/{session_id}/confirmations/{confirmation_id}",
        json={"decision": "allow_once"},
    )
    assert decision.status_code == 200
    payload_3 = decision.json()
    assert payload_3["turn"]["status"] == "completed"
    assert payload_3["assistant_message"] is not None

    compact = client.post(
        f"/api/v1/assistant/sessions/{session_id}:compact",
        json={"max_messages_to_compact": 3},
    )
    assert compact.status_code == 200
    assert compact.json()["compacted_message_count"] >= 1

    messages = client.get(f"/api/v1/assistant/sessions/{session_id}/messages")
    assert messages.status_code == 200
    assert len(messages.json()["messages"]) >= 3


def test_assistant_api_renders_table_outputs(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace, ".assistant/assistant.db")
    _reset_services(monkeypatch, cfg)
    client = _create_test_client()

    session_id = client.post("/api/v1/assistant/sessions", json={"title": "table outputs"}).json()["session_id"]
    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "assistant_table_scn", "name": "Assistant Table", "owner": "test"},
    ).json()
    scenario_id = scenario["scenario_id"]
    scenario_dir = Path(scenario["directory"])
    csv_path = scenario_dir / "outputs" / "sample_stats.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("crater_id,slope_deg\nA1,12.4\nA2,8.9\n", encoding="utf-8")

    response = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={
            "scenario_id": scenario_id,
            "prompt": _explicit_tool_prompt(
                ("artifact.describe_table", {"scenario_id": scenario_id, "relative_path": "outputs/sample_stats.csv"}),
            ),
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["turn"]["status"] == "completed"
    assert payload["assistant_message"]["outputs"][0]["kind"] == "table"
    assert payload["assistant_message"]["outputs"][0]["mime_type"] == "application/vnd.lunar-analyst.table+json"
    assert payload["assistant_message"]["outputs"][0]["data"]["rows"][0]["crater_id"] == "A1"
    assert payload["assistant_message"]["outputs"][1]["kind"] == "artifact_card"


def test_assistant_api_prefers_file_backed_plot_outputs_for_scenario_files(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace, ".assistant/assistant.db")
    _reset_services(monkeypatch, cfg)
    client = _create_test_client()

    session_id = client.post("/api/v1/assistant/sessions", json={"title": "plot outputs"}).json()["session_id"]
    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "assistant_plot_scn", "name": "Assistant Plot", "owner": "test"},
    ).json()
    scenario_id = scenario["scenario_id"]
    scenario_dir = Path(scenario["directory"])
    plot_path = scenario_dir / "slope_histogram.png"
    plot_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aW8sAAAAASUVORK5CYII="
        )
    )

    response = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={
            "scenario_id": scenario_id,
            "prompt": _explicit_tool_prompt(
                ("artifact.describe_plot", {"scenario_id": scenario_id, "relative_path": "slope_histogram.png"}),
            ),
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["turn"]["status"] == "completed"
    assert payload["assistant_message"]["outputs"][0]["kind"] == "plot"
    assert payload["assistant_message"]["outputs"][0]["storage"] == "file"
    assert payload["assistant_message"]["outputs"][0]["file_id"]
    assert payload["assistant_message"]["outputs"][0]["data"] == {}
    assert payload["assistant_message"]["outputs"][1]["kind"] == "artifact_card"


def test_assistant_api_renders_geotiff_preview_outputs(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace, ".assistant/assistant.db")
    _reset_services(monkeypatch, cfg)
    client = _create_test_client()

    session_id = client.post("/api/v1/assistant/sessions", json={"title": "geotiff outputs"}).json()["session_id"]
    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "assistant_tif_scn", "name": "Assistant TIF", "owner": "test"},
    ).json()
    scenario_id = scenario["scenario_id"]
    scenario_dir = Path(scenario["directory"])
    tif_path = scenario_dir / "outputs" / "hillshade.tif"
    tif_path.parent.mkdir(parents=True, exist_ok=True)

    rasterio = import_rasterio()
    data = (np.arange(64, dtype=np.uint8).reshape(8, 8) * 4).astype(np.uint8)
    with rasterio.open(tif_path, "w", driver="GTiff", width=8, height=8, count=1, dtype="uint8") as ds:
        ds.write(data, 1)

    response = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={
            "scenario_id": scenario_id,
            "prompt": _explicit_tool_prompt(
                ("artifact.preview_geotiff", {"scenario_id": scenario_id, "relative_path": "outputs/hillshade.tif"}),
            ),
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["turn"]["status"] == "completed"
    assert payload["assistant_message"]["outputs"][0]["kind"] == "image"
    assert payload["assistant_message"]["outputs"][0]["mime_type"] == "image/png"
    assert payload["assistant_message"]["outputs"][0]["storage"] == "file"
    assert payload["assistant_message"]["outputs"][0]["file_id"]
    assert payload["assistant_message"]["outputs"][1]["kind"] == "artifact_card"


def test_assistant_api_returns_geotiff_statistics(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace, ".assistant/assistant.db")
    _reset_services(monkeypatch, cfg)
    client = _create_test_client()

    session_id = client.post("/api/v1/assistant/sessions", json={"title": "geotiff stats"}).json()["session_id"]
    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "assistant_tif_stats_scn", "name": "Assistant TIF Stats", "owner": "test"},
    ).json()
    scenario_id = scenario["scenario_id"]
    scenario_dir = Path(scenario["directory"])
    tif_path = scenario_dir / "slope.tif"

    rasterio = import_rasterio()
    data = np.arange(16, dtype=np.float32).reshape(4, 4)
    with rasterio.open(tif_path, "w", driver="GTiff", width=4, height=4, count=1, dtype="float32") as ds:
        ds.write(data, 1)

    response = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={
            "scenario_id": scenario_id,
            "prompt": _explicit_tool_prompt(
                ("artifact.stats_geotiff", {"scenario_id": scenario_id, "relative_path": "slope.tif"}),
            ),
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["turn"]["status"] == "completed"
    assert payload["tool_calls"][0]["tool_name"] == "artifact.stats_geotiff"
    key_stats = payload["tool_calls"][0]["result"]["key_stats"]
    assert key_stats["valid_count"] == 16
    assert key_stats["total_count"] == 16
    assert key_stats["min"] == 0.0
    assert key_stats["max"] == 15.0
    assert "p50" in key_stats["percentiles"]


def test_assistant_api_write_run_and_describe_script_artifact(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace, ".assistant/assistant.db")
    _reset_services(monkeypatch, cfg)
    client = _create_test_client()

    session_id = client.post("/api/v1/assistant/sessions", json={"title": "script outputs"}).json()["session_id"]

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "assistant_script_scn", "name": "Assistant Script", "owner": "test"},
    ).json()
    scenario_id = scenario["scenario_id"]
    scenario_dir = Path(scenario["directory"])

    script = """from pathlib import Path
out = Path("generated_stats.csv")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("value\\n1\\n2\\n3\\n", encoding="utf-8")
print(out.as_posix())
"""

    write_response = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={
            "scenario_id": scenario_id,
            "prompt": f'write and run script "make_demo_table.py"\n```python\n{script}```',
        },
    )
    assert write_response.status_code == 200
    write_payload = write_response.json()
    assert write_payload["turn"]["status"] == "confirmation_required"
    assert write_payload["confirmation"]["tool_name"] == "scenario.write_run_script"
    confirmation_id = write_payload["confirmation"]["confirmation_id"]

    run_decision = client.post(
        f"/api/v1/assistant/sessions/{session_id}/confirmations/{confirmation_id}",
        json={"decision": "allow_once"},
    )
    assert run_decision.status_code == 200
    run_decision_payload = run_decision.json()
    assert run_decision_payload["turn"]["status"] == "completed"
    assert run_decision_payload["tool_calls"][0]["tool_name"] == "scenario.write_run_script"

    describe_response = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={
            "scenario_id": scenario_id,
            "prompt": 'describe table "generated_stats.csv"',
        },
    )
    assert describe_response.status_code == 200
    payload = describe_response.json()
    assert payload["turn"]["status"] == "completed"
    assert payload["tool_calls"][0]["tool_name"] == "artifact.describe_table"
    assert payload["assistant_message"]["outputs"][0]["kind"] == "table"
    assert payload["assistant_message"]["outputs"][0]["data"]["row_count"] == 3
    assert payload["assistant_message"]["outputs"][1]["kind"] == "artifact_card"


def test_assistant_api_captures_bug_report_bundle(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace, ".assistant/assistant.db")
    _reset_services(monkeypatch, cfg)
    client = _create_test_client()

    session_id = client.post("/api/v1/assistant/sessions", json={"title": "bug reports"}).json()["session_id"]
    turn_response = client.post(
        f"/api/v1/assistant/sessions/{session_id}/turns",
        json={"prompt": "describe capabilities"},
    )
    assert turn_response.status_code == 200
    turn_payload = turn_response.json()
    turn_id = turn_payload["turn"]["turn_id"]

    capture_response = client.post(
        f"/api/v1/assistant/sessions/{session_id}/bug-reports",
        json={
            "report_text": "The assistant skipped the expected response.",
            "program_state": {
                "active_scenario_id": "scn_bug",
                "active_assistant_session_id": session_id,
                "active_assistant_turn_id": turn_id,
                "active_provider_id": "ollama",
                "active_model_id": "qwen2.5-coder:7b-instruct-q4_K_M",
                "active_panel": "assistant",
                "assistant_prompt_draft": "describe capabilities",
                "workspace_state": {"theme": "light"},
            },
        },
    )
    assert capture_response.status_code == 200
    payload = capture_response.json()
    assert payload["bug_report"]["assistant_session_id"] == session_id
    assert payload["bug_report"]["assistant_turn_id"] == turn_id
    assert payload["bug_report"]["scenario_id"] == "scn_bug"
    assert payload["bug_report"]["report_text"] == "The assistant skipped the expected response."
    bundle_path = Path(payload["bundle_path"])
    assert bundle_path.exists()
    saved_bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert saved_bundle["bug_report_id"] == payload["bug_report"]["bug_report_id"]
    assert saved_bundle["program_state"]["active_assistant_turn_id"] == turn_id
    assert isinstance(saved_bundle["log_excerpt"], list)

    list_response = client.get("/api/v1/assistant/bug-reports")
    assert list_response.status_code == 200
    bug_reports = list_response.json()["bug_reports"]
    assert any(item["bug_report_id"] == payload["bug_report"]["bug_report_id"] for item in bug_reports)

    get_response = client.get(f"/api/v1/assistant/bug-reports/{payload['bug_report']['bug_report_id']}")
    assert get_response.status_code == 200
    assert get_response.json()["bug_report"]["bug_report_id"] == payload["bug_report"]["bug_report_id"]
