from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import build_service_container
from backend.api.dependencies import _build_notebook_runner_env


def _write_config(
    config_path: Path,
    *,
    workspace_root: Path,
    notebook_roots: list[Path],
    run_dir_retention_hours: float | None = None,
) -> None:
    rel_workspace = workspace_root.as_posix()
    roots_json = json.dumps([root.as_posix() for root in notebook_roots])
    retention_line = (
        [f"run_dir_retention_hours = {float(run_dir_retention_hours)}"]
        if run_dir_retention_hours is not None
        else []
    )
    config_path.write_text(
        "\n".join(
            [
                "[backend]",
                f'workspace_root = "{rel_workspace}"',
                "",
                "[backend.notebook_jobs]",
                f"search_roots = {roots_json}",
                f'python_executable = "{Path(sys.executable).as_posix()}"',
                *retention_line,
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _reset_services(monkeypatch, config_path: Path) -> None:
    import backend.api.dependencies as dependencies_module

    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))
    monkeypatch.delenv("LUNAR_ANALYST_WORKSPACE_ROOT", raising=False)
    dependencies_module.SERVICES = build_service_container()


def _wait_for_terminal_job(
    client: TestClient,
    job_id: str,
    *,
    timeout_seconds: float = 20.0,
) -> dict[str, object]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        status = str(payload.get("status", "")).strip().lower()
        if status in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"job did not reach terminal status within timeout: {job_id}")


def _write_notebook_job(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "demo_runner.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "",
                "def run(context):",
                '    context.report_progress(percent=12.5, message="booting", stage="setup")',
                '    out = context.scenario_root_dir / "outputs" / "demo_notebook_output.tif"',
                "    out.parent.mkdir(parents=True, exist_ok=True)",
                "    out.write_bytes(b'NB-DEMO')",
                "    context.register_output(",
                '        relative_path="outputs/demo_notebook_output.tif",',
                '        kind="analysis",',
                '        subkind="notebook_output",',
                '        render_mode="raster",',
                '        metadata={"producer": "phase4_8_test"},',
                "    )",
                '    context.report_progress(percent=86.0, message="registered", stage="emit")',
                '    return {"summary": "ok"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "demo.job.json").write_text(
        json.dumps(
            {
                "job_id": "demo-notebook-job",
                "title": "Demo Notebook Job",
                "notebook_path": "demo_runner.py",
                "description": "Test notebook job definition.",
                "visibility": "default",
                "tags": ["demo", "phase4_8"],
                "params_schema": {
                    "type": "object",
                    "properties": {
                        "alpha": {"type": "number"},
                    },
                    "required": [],
                },
                "outputs_schema": {
                    "type": "array",
                    "items": {"type": "object"},
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_notebook_job_script_mode(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "script_mode_runner.py").write_text(
        "\n".join(
            [
                "from backend.notebook.runtime import get_context, register_output, report_progress",
                "",
                "ctx = get_context()",
                'report_progress(percent=5.0, message="script start", stage="boot")',
                'out = ctx.scenario_root_dir / "outputs" / "script_mode_output.tif"',
                "out.parent.mkdir(parents=True, exist_ok=True)",
                "out.write_bytes(b'SCRIPT-MODE')",
                "register_output(",
                '    relative_path="outputs/script_mode_output.tif",',
                '    kind="analysis",',
                '    subkind="script_mode",',
                '    render_mode="raster",',
                '    metadata={"style": "script"},',
                ")",
                'report_progress(percent=88.0, message="script done", stage="emit")',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "script-mode.job.json").write_text(
        json.dumps(
            {
                "job_id": "script-mode-job",
                "title": "Script Mode Job",
                "notebook_path": "script_mode_runner.py",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_named_notebook_job(
    root: Path,
    *,
    job_id: str,
    title: str,
    script_name: str,
    marker_name: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / script_name).write_text(
        "\n".join(
            [
                "def run(context):",
                f'    return {{"marker": "{marker_name}"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / f"{job_id}.job.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "title": title,
                "notebook_path": script_name,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_named_notebook_job_with_console_output(
    root: Path,
    *,
    job_id: str,
    title: str,
    script_name: str,
    stdout_marker: str,
    stderr_marker: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / script_name).write_text(
        "\n".join(
            [
                "import sys",
                "",
                "def run(context):",
                f'    print("{stdout_marker}")',
                f'    print("{stderr_marker}", file=sys.stderr)',
                '    return {"ok": True}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / f"{job_id}.job.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "title": title,
                "notebook_path": script_name,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_scenario_root_script(path: Path, *, with_run: bool = False) -> None:
    if with_run:
        path.write_text(
            "\n".join(
                [
                    "def run(context):",
                    "    out = context.scenario_root_dir / 'outputs' / 'implicit_run_output.tif'",
                    "    out.parent.mkdir(parents=True, exist_ok=True)",
                    "    out.write_bytes(b'IMPLICIT-RUN')",
                    "    context.register_output(",
                    "        relative_path='outputs/implicit_run_output.tif',",
                    "        kind='analysis',",
                    "        subkind='implicit',",
                    "    )",
                    "    return {'ok': True}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return

    path.write_text(
        "\n".join(
            [
                "from backend.notebook.runtime import get_context, register_output",
                "",
                "ctx = get_context()",
                "out = ctx.scenario_root_dir / 'outputs' / 'implicit_script_output.tif'",
                "out.parent.mkdir(parents=True, exist_ok=True)",
                "out.write_bytes(b'IMPLICIT-SCRIPT')",
                "register_output(",
                "    relative_path='outputs/implicit_script_output.tif',",
                "    kind='analysis',",
                "    subkind='implicit',",
                ")",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_phase4_8_job_definitions_and_notebook_execution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    notebook_root = tmp_path / "notebook_jobs"
    _write_notebook_job(notebook_root)
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        workspace_root=tmp_path / "workspace",
        notebook_roots=[notebook_root],
    )
    _reset_services(monkeypatch, config_path)

    client = TestClient(create_app())
    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase48", "name": "Phase 4.8", "owner": "tester"},
    )
    assert scenario.status_code == 200
    scenario_id = scenario.json()["scenario_id"]

    defs = client.get("/api/v1/job-definitions")
    assert defs.status_code == 200
    definitions = defs.json()["definitions"]
    by_id = {item["job_definition_id"]: item for item in definitions}
    assert "notebook:demo-notebook-job" in by_id
    assert by_id["notebook:demo-notebook-job"]["job_type"] == "notebook"
    assert by_id["notebook:demo-notebook-job"]["handler_name"] == "run_notebook_definition"
    assert "native:ping" in by_id

    job_response = client.post(
        "/api/v1/jobs/run-notebook-definition",
        json={
            "scenario_id": scenario_id,
            "notebook_job_id": "demo-notebook-job",
            "params": {"alpha": 0.25},
        },
    )
    assert job_response.status_code == 200
    job_payload = job_response.json()
    assert job_payload["job_type"] == "run_notebook_definition"
    assert job_payload["status"] in {"queued", "running", "completed"}
    job_id = job_payload["job_id"]
    terminal = _wait_for_terminal_job(client, job_id)
    assert terminal["status"] == "completed"

    events = client.get(f"/api/v1/jobs/{job_id}/events")
    assert events.status_code == 200
    event_payload = events.json()
    event_names = [entry["event_name"] for entry in event_payload]
    assert "job_progress" in event_names
    assert event_names[-1] == "job_completed"
    result = event_payload[-1]["data"]["result"]
    assert result["notebook_job_id"] == "demo-notebook-job"
    assert len(result["outputs"]) == 1
    assert result["outputs"][0]["relative_path"] == "outputs/demo_notebook_output.tif"
    assert result["outputs"][0]["product_id"].startswith("prd_")
    assert result["outputs"][0]["file_id"].startswith("fil_")

    products = client.get(f"/api/v1/scenarios/{scenario_id}/products")
    assert products.status_code == 200
    assert any(
        product["lineage"].get("source") == "notebook_job"
        and product["lineage"].get("notebook_job_id") == "demo-notebook-job"
        for product in products.json()
    )


def test_phase4_8_rejects_notebook_definition_out_of_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    notebook_root = tmp_path / "notebook_jobs"
    notebook_root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside_runner.py"
    outside.write_text("def run(context):\n    return {'ok': True}\n", encoding="utf-8")
    (notebook_root / "bad.job.json").write_text(
        json.dumps(
            {
                "job_id": "bad-job",
                "title": "Bad Job",
                "notebook_path": "../outside_runner.py",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        workspace_root=tmp_path / "workspace",
        notebook_roots=[notebook_root],
    )
    _reset_services(monkeypatch, config_path)

    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.get("/api/v1/job-definitions")
    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == "internal_error"


def test_phase4_8_job_definitions_scenario_query_param_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured_root = tmp_path / "configured_jobs"
    _write_named_notebook_job(
        configured_root,
        job_id="configured-job",
        title="Configured Job",
        script_name="configured_runner.py",
        marker_name="configured",
    )
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        workspace_root=tmp_path / "workspace",
        notebook_roots=[configured_root],
    )
    _reset_services(monkeypatch, config_path)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase48query", "name": "Phase 4.8 Query", "owner": "tester"},
    )
    assert scenario.status_code == 200
    scenario_payload = scenario.json()
    scenario_id = scenario_payload["scenario_id"]
    scenario_root = Path(scenario_payload["directory"]).resolve()
    scenario_jobs_root = scenario_root / ".notebook_jobs"
    _write_named_notebook_job(
        scenario_jobs_root,
        job_id="scenario-job",
        title="Scenario Job",
        script_name="scenario_runner.py",
        marker_name="scenario",
    )

    no_query = client.get("/api/v1/job-definitions")
    assert no_query.status_code == 200
    ids_no_query = {
        item["job_definition_id"]
        for item in no_query.json()["definitions"]
        if item["job_type"] == "notebook"
    }
    assert "notebook:configured-job" in ids_no_query
    assert "notebook:scenario-job" not in ids_no_query

    bad_query = client.get("/api/v1/job-definitions", params={"scenario_id": "scn_missing"})
    assert bad_query.status_code == 200
    ids_bad_query = {
        item["job_definition_id"]
        for item in bad_query.json()["definitions"]
        if item["job_type"] == "notebook"
    }
    assert "notebook:configured-job" in ids_bad_query
    assert "notebook:scenario-job" not in ids_bad_query

    valid_query = client.get("/api/v1/job-definitions", params={"scenario_id": scenario_id})
    assert valid_query.status_code == 200
    ids_valid_query = {
        item["job_definition_id"]
        for item in valid_query.json()["definitions"]
        if item["job_type"] == "notebook"
    }
    assert "notebook:configured-job" in ids_valid_query
    assert "notebook:scenario-job" in ids_valid_query


def test_phase4_8_notebook_script_mode_without_run_function(
    monkeypatch,
    tmp_path: Path,
) -> None:
    notebook_root = tmp_path / "notebook_jobs_script_mode"
    _write_notebook_job_script_mode(notebook_root)
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        workspace_root=tmp_path / "workspace",
        notebook_roots=[notebook_root],
    )
    _reset_services(monkeypatch, config_path)
    client = TestClient(create_app())
    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase48script", "name": "Phase 4.8 Script", "owner": "tester"},
    )
    assert scenario.status_code == 200
    scenario_id = scenario.json()["scenario_id"]

    response = client.post(
        "/api/v1/jobs/run-notebook-definition",
        json={
            "scenario_id": scenario_id,
            "notebook_job_id": "script-mode-job",
            "params": {},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"queued", "running", "completed"}
    job_id = payload["job_id"]
    terminal = _wait_for_terminal_job(client, job_id)
    assert terminal["status"] == "completed"
    events = client.get(f"/api/v1/jobs/{job_id}/events")
    assert events.status_code == 200
    result = events.json()[-1]["data"]["result"]
    assert result["notebook_job_id"] == "script-mode-job"
    assert len(result["outputs"]) == 1
    assert result["outputs"][0]["relative_path"] == "outputs/script_mode_output.tif"


def test_phase4_8_maps_horizon_dimension_runtime_error_to_422(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        workspace_root=tmp_path / "workspace",
        notebook_roots=[],
    )
    _reset_services(monkeypatch, config_path)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase48dims", "name": "Phase 4.8 Dims", "owner": "tester"},
    )
    assert scenario.status_code == 200
    scenario_id = scenario.json()["scenario_id"]

    import backend.jobs.handlers as handlers_module

    def raise_dem_dimension_error(
        scenario_id: str,
        notebook_job_id: str,
        params: dict[str, object] | None = None,
        runtime_mode: str = "osgeo",
    ) -> dict[str, object]:
        _ = runtime_mode
        raise RuntimeError("DEM width (4200) must be an even multiple of 128. (Parameter 'primaryDem')")

    monkeypatch.setattr(handlers_module, "execute_notebook_job", raise_dem_dimension_error)

    response = client.post(
        "/api/v1/jobs/run-notebook-definition",
        json={
            "scenario_id": scenario_id,
            "notebook_job_id": "demo-notebook-job",
            "params": {},
            "mode": "immediate",
        },
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "invalid_dem_dimensions"
    assert "DEM width (4200)" in payload["message"]
    assert payload["details"]["dimension"] == "width"
    assert payload["details"]["value"] == 4200
    assert payload["details"]["multiple"] == 128
    assert "native_error" in payload["details"]


def test_phase4_8_notebook_runner_persists_stdout_and_stderr_logs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    notebook_root = tmp_path / "notebook_jobs_logs"
    stdout_marker = "NOTEBOOK-STDOUT-MARKER"
    stderr_marker = "NOTEBOOK-STDERR-MARKER"
    _write_named_notebook_job_with_console_output(
        notebook_root,
        job_id="logs-job",
        title="Logs Job",
        script_name="logs_runner.py",
        stdout_marker=stdout_marker,
        stderr_marker=stderr_marker,
    )
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        workspace_root=tmp_path / "workspace",
        notebook_roots=[notebook_root],
    )
    _reset_services(monkeypatch, config_path)
    client = TestClient(create_app())
    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase48logs", "name": "Phase 4.8 Logs", "owner": "tester"},
    )
    assert scenario.status_code == 200
    scenario_payload = scenario.json()
    scenario_id = scenario_payload["scenario_id"]
    scenario_root = Path(scenario_payload["directory"]).resolve()

    response = client.post(
        "/api/v1/jobs/run-notebook-definition",
        json={
            "scenario_id": scenario_id,
            "notebook_job_id": "logs-job",
            "params": {},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"queued", "running", "completed"}
    terminal = _wait_for_terminal_job(client, str(payload["job_id"]))
    assert terminal["status"] == "completed"
    logs_response = client.get(
        f"/api/v1/jobs/{payload['job_id']}/logs",
        params={"stream": "combined", "head_lines": 0, "tail_lines": 200},
    )
    assert logs_response.status_code == 200
    logs_payload = logs_response.json()
    assert logs_payload["stream"] == "combined"
    assert logs_payload["is_final"] is True
    assert logs_payload["streams"]["stdout"]["is_final"] is True
    assert logs_payload["streams"]["stderr"]["is_final"] is True
    assert stdout_marker in "\n".join(logs_payload["streams"]["stdout"]["tail"])
    assert stderr_marker in "\n".join(logs_payload["streams"]["stderr"]["tail"])

    runs_root = (scenario_root / ".notebook_jobs" / "runs").resolve()
    run_dirs = [item for item in runs_root.iterdir() if item.is_dir()]
    assert run_dirs
    assert any(
        stdout_marker in (run_dir / "runner_stdout.log").read_text(encoding="utf-8")
        and stderr_marker in (run_dir / "runner_stderr.log").read_text(encoding="utf-8")
        for run_dir in run_dirs
        if (run_dir / "runner_stdout.log").exists() and (run_dir / "runner_stderr.log").exists()
    )


def test_phase4_8_implicit_scenario_scripts_discovery_fallback_and_valid_query(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured_root = tmp_path / "configured_jobs"
    _write_named_notebook_job(
        configured_root,
        job_id="configured-job",
        title="Configured Job",
        script_name="configured_runner.py",
        marker_name="configured",
    )
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        workspace_root=tmp_path / "workspace",
        notebook_roots=[configured_root],
    )
    _reset_services(monkeypatch, config_path)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase48implicit", "name": "Phase 4.8 Implicit", "owner": "tester"},
    )
    assert scenario.status_code == 200
    scenario_payload = scenario.json()
    scenario_id = scenario_payload["scenario_id"]
    scenario_root = Path(scenario_payload["directory"]).resolve()
    _write_scenario_root_script(scenario_root / "ad_hoc_task.py")
    (scenario_root / ".hidden_task.py").write_text("print('hidden')\n", encoding="utf-8")
    (scenario_root / "_private_task.py").write_text("print('private')\n", encoding="utf-8")

    no_query = client.get("/api/v1/job-definitions")
    assert no_query.status_code == 200
    ids_no_query = {
        item["job_definition_id"]
        for item in no_query.json()["definitions"]
        if item["job_type"] == "notebook"
    }
    assert "notebook:configured-job" in ids_no_query
    assert "notebook:script-ad_hoc_task" not in ids_no_query

    bad_query = client.get("/api/v1/job-definitions", params={"scenario_id": "scn_missing"})
    assert bad_query.status_code == 200
    ids_bad_query = {
        item["job_definition_id"]
        for item in bad_query.json()["definitions"]
        if item["job_type"] == "notebook"
    }
    assert "notebook:configured-job" in ids_bad_query
    assert "notebook:script-ad_hoc_task" not in ids_bad_query

    valid_query = client.get("/api/v1/job-definitions", params={"scenario_id": scenario_id})
    assert valid_query.status_code == 200
    by_id = {
        item["job_definition_id"]: item
        for item in valid_query.json()["definitions"]
        if item["job_type"] == "notebook"
    }
    assert "notebook:configured-job" in by_id
    assert "notebook:script-ad_hoc_task" in by_id
    assert "notebook:script-.hidden_task" not in by_id
    assert "notebook:script-_private_task" not in by_id
    assert "implicit" in by_id["notebook:script-ad_hoc_task"]["tags"]


def test_phase4_8_explicit_job_json_overrides_implicit_scenario_script_on_collision(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        workspace_root=tmp_path / "workspace",
        notebook_roots=[],
    )
    _reset_services(monkeypatch, config_path)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase48collision", "name": "Phase 4.8 Collision", "owner": "tester"},
    )
    assert scenario.status_code == 200
    scenario_payload = scenario.json()
    scenario_id = scenario_payload["scenario_id"]
    scenario_root = Path(scenario_payload["directory"]).resolve()
    _write_scenario_root_script(scenario_root / "collide.py")
    scenario_jobs_root = scenario_root / ".notebook_jobs"
    _write_named_notebook_job(
        scenario_jobs_root,
        job_id="script-collide",
        title="Explicit Collision Winner",
        script_name="explicit_collision_runner.py",
        marker_name="explicit",
    )

    valid_query = client.get("/api/v1/job-definitions", params={"scenario_id": scenario_id})
    assert valid_query.status_code == 200
    notebook_defs = [
        item
        for item in valid_query.json()["definitions"]
        if item["job_definition_id"] == "notebook:script-collide"
    ]
    assert len(notebook_defs) == 1
    assert notebook_defs[0]["title"] == "Explicit Collision Winner"
    assert "implicit" not in notebook_defs[0]["tags"]


def test_phase4_8_implicit_scenario_script_executes_without_job_json(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        workspace_root=tmp_path / "workspace",
        notebook_roots=[],
    )
    _reset_services(monkeypatch, config_path)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase48exec", "name": "Phase 4.8 Exec", "owner": "tester"},
    )
    assert scenario.status_code == 200
    scenario_id = scenario.json()["scenario_id"]
    scenario_root = Path(scenario.json()["directory"]).resolve()
    _write_scenario_root_script(scenario_root / "quick_test_task.py")

    defs = client.get("/api/v1/job-definitions", params={"scenario_id": scenario_id})
    assert defs.status_code == 200
    notebook_ids = {
        item["job_definition_id"]
        for item in defs.json()["definitions"]
        if item["job_type"] == "notebook"
    }
    assert "notebook:script-quick_test_task" in notebook_ids

    response = client.post(
        "/api/v1/jobs/run-notebook-definition",
        json={
            "scenario_id": scenario_id,
            "notebook_job_id": "script-quick_test_task",
            "params": {},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"queued", "running", "completed"}
    terminal = _wait_for_terminal_job(client, str(payload["job_id"]))
    assert terminal["status"] == "completed"
    events = client.get(f"/api/v1/jobs/{payload['job_id']}/events")
    assert events.status_code == 200
    result = events.json()[-1]["data"]["result"]
    assert result["notebook_job_id"] == "script-quick_test_task"
    assert len(result["outputs"]) == 1
    assert result["outputs"][0]["relative_path"] == "outputs/implicit_script_output.tif"


def test_phase4_8_implicit_configured_root_script_discovery_and_execution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured_root = tmp_path / "configured_jobs"
    configured_root.mkdir(parents=True, exist_ok=True)
    _write_scenario_root_script(configured_root / "root_task.py")
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        workspace_root=tmp_path / "workspace",
        notebook_roots=[configured_root],
    )
    _reset_services(monkeypatch, config_path)
    client = TestClient(create_app())
    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase48root", "name": "Phase 4.8 Root", "owner": "tester"},
    )
    assert scenario.status_code == 200
    scenario_id = scenario.json()["scenario_id"]

    defs = client.get("/api/v1/job-definitions")
    assert defs.status_code == 200
    by_id = {
        item["job_definition_id"]: item
        for item in defs.json()["definitions"]
        if item["job_type"] == "notebook"
    }
    assert "notebook:script-root_task" in by_id
    assert "configured-script" in by_id["notebook:script-root_task"]["tags"]
    assert "implicit" in by_id["notebook:script-root_task"]["tags"]

    response = client.post(
        "/api/v1/jobs/run-notebook-definition",
        json={
            "scenario_id": scenario_id,
            "notebook_job_id": "script-root_task",
            "params": {},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"queued", "running", "completed"}
    terminal = _wait_for_terminal_job(client, str(payload["job_id"]))
    assert terminal["status"] == "completed"
    events = client.get(f"/api/v1/jobs/{payload['job_id']}/events")
    assert events.status_code == 200
    result = events.json()[-1]["data"]["result"]
    assert result["notebook_job_id"] == "script-root_task"
    assert len(result["outputs"]) == 1
    assert result["outputs"][0]["relative_path"] == "outputs/implicit_script_output.tif"


def test_phase4_8_notebook_run_root_fallback_when_scenario_root_run_dir_is_readonly(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured_root = tmp_path / "configured_jobs"
    _write_named_notebook_job(
        configured_root,
        job_id="configured-job",
        title="Configured Job",
        script_name="configured_runner.py",
        marker_name="configured",
    )
    workspace_root = tmp_path / "workspace"
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        workspace_root=workspace_root,
        notebook_roots=[configured_root],
    )
    _reset_services(monkeypatch, config_path)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase48fallback", "name": "Phase 4.8 Fallback", "owner": "tester"},
    )
    assert scenario.status_code == 200
    scenario_payload = scenario.json()
    scenario_id = scenario_payload["scenario_id"]
    scenario_root = Path(scenario_payload["directory"]).resolve()

    original_mkdir = Path.mkdir

    def patched_mkdir(
        self: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        resolved = self.resolve()
        if str(resolved).startswith(str(scenario_root)) and ".notebook_jobs" in resolved.parts:
            raise PermissionError("simulated readonly scenario notebook run directory")
        return original_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", patched_mkdir)

    response = client.post(
        "/api/v1/jobs/run-notebook-definition",
        json={
            "scenario_id": scenario_id,
            "notebook_job_id": "configured-job",
            "params": {},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"queued", "running", "completed"}
    terminal = _wait_for_terminal_job(client, str(payload["job_id"]))
    assert terminal["status"] == "completed"

    fallback_root = (workspace_root / ".notebook_job_runs" / scenario_id).resolve()
    assert fallback_root.exists()
    run_dirs = [item for item in fallback_root.iterdir() if item.is_dir()]
    assert run_dirs
    assert (run_dirs[0] / "context.json").exists()
    assert (run_dirs[0] / "result.json").exists()


def test_phase4_8_notebook_run_root_prunes_old_run_dirs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured_root = tmp_path / "configured_jobs"
    _write_named_notebook_job(
        configured_root,
        job_id="configured-job",
        title="Configured Job",
        script_name="configured_runner.py",
        marker_name="configured",
    )
    workspace_root = tmp_path / "workspace"
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        workspace_root=workspace_root,
        notebook_roots=[configured_root],
        run_dir_retention_hours=24.0,
    )
    _reset_services(monkeypatch, config_path)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase48prune", "name": "Phase 4.8 Prune", "owner": "tester"},
    )
    assert scenario.status_code == 200
    payload = scenario.json()
    scenario_id = payload["scenario_id"]
    scenario_root = Path(payload["directory"]).resolve()
    runs_root = (scenario_root / ".notebook_jobs" / "runs").resolve()
    old_run = runs_root / "nbr_old"
    old_run.mkdir(parents=True, exist_ok=True)
    stale_timestamp = 1_577_836_800  # Jan 1, 2020 UTC
    os.utime(old_run, (stale_timestamp, stale_timestamp))
    removed_paths: list[Path] = []
    original_rmtree = shutil.rmtree

    def patched_rmtree(path, ignore_errors=False, onerror=None):
        removed_paths.append(Path(path).resolve())
        return original_rmtree(path, ignore_errors=ignore_errors, onerror=onerror)

    monkeypatch.setattr(shutil, "rmtree", patched_rmtree)

    response = client.post(
        "/api/v1/jobs/run-notebook-definition",
        json={
            "scenario_id": scenario_id,
            "notebook_job_id": "configured-job",
            "params": {},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"queued", "running", "completed"}
    terminal = _wait_for_terminal_job(client, str(payload["job_id"]))
    assert terminal["status"] == "completed"
    assert old_run.resolve() in removed_paths


def test_phase4_8_notebook_runner_env_includes_repo_and_moonlayers_pkg() -> None:
    env = _build_notebook_runner_env()
    pythonpath = env.get("PYTHONPATH", "")
    entries = pythonpath.split(os.pathsep) if pythonpath else []
    assert entries
    assert entries[0].endswith("lunar_analyst")
    assert any(entry.endswith("moonlayers_pkg") for entry in entries)
