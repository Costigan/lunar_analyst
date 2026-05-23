from __future__ import annotations

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
    import backend.api.dependencies as deps

    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))
    monkeypatch.delenv("LUNAR_ANALYST_WORKSPACE_ROOT", raising=False)
    deps.SERVICES = build_service_container()


def test_mcp_initialize_list_and_read_tool(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace)
    _reset_services(monkeypatch, cfg)
    client = TestClient(create_app())

    init = client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert init.status_code == 200
    assert init.json()["result"]["serverInfo"]["name"] == "lunar-analyst-mcp"

    tools = client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert tools.status_code == 200
    tool_names = {item["name"] for item in tools.json()["result"]["tools"]}
    assert "capabilities.describe" in tool_names
    assert "scenario.list" in tool_names
    assert "raster.transform" in tool_names
    assert all("prefilter" not in name for name in tool_names)

    call = client.post(
        "/api/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "capabilities.describe", "arguments": {}},
        },
    )
    assert call.status_code == 200
    result = call.json()["result"]
    assert result["isError"] is False
    assert "structuredContent" in result
    structured = result["structuredContent"]
    assert "tool_names" in structured
    assert isinstance(structured["tool_names"], list)
    assert "capabilities.describe" in structured["tool_names"]
    assert "scenario.list" in structured["tool_names"]


def test_mcp_script_run_and_logs(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, workspace)
    _reset_services(monkeypatch, cfg)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "mcp_script_scn", "name": "MCP Script Scenario", "owner": "test"},
    )
    assert scenario.status_code == 200
    scenario_id = scenario.json()["scenario_id"]
    scenario_dir = Path(scenario.json()["directory"])
    script_rel = "scripts/hello_test.py"
    script_path = scenario_dir / "scripts" / "hello_test.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        "\n".join(
            [
                "print('hello-from-mcp-script')",
                "if __name__ == '__main__':",
                "    print('script-main-ran')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    listed = client.post(
        "/api/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "scenario.list_scripts", "arguments": {"scenario_id": scenario_id}},
        },
    )
    assert listed.status_code == 200
    listed_payload = listed.json()["result"]["structuredContent"]
    rel_paths = {item["relative_path"] for item in listed_payload["items"]}
    assert script_rel in rel_paths

    run = client.post(
        "/api/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "scenario.run_script",
                "arguments": {
                    "_confirmed": True,
                    "scenario_id": scenario_id,
                    "relative_path": script_rel,
                },
            },
        },
    )
    assert run.status_code == 200
    run_payload = run.json()["result"]["structuredContent"]
    run_id = run_payload["run_id"]
    assert run_id

    logs = client.post(
        "/api/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "runs.get_logs",
                "arguments": {"run_id": run_id, "stream": "stdout", "head_lines": 5, "tail_lines": 5},
            },
        },
    )
    assert logs.status_code == 200
    log_payload = logs.json()["result"]["structuredContent"]
    assert log_payload["total_lines"] >= 1
