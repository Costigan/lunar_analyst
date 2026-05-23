from __future__ import annotations

from pathlib import Path
import time

import pytest

from backend.analyst_tools.catalog import get_tool_definition, list_tool_definitions
from backend.analyst_tools.client import LocalAnalystToolClient
from backend.api.dependencies import build_service_container
from backend.contracts.models import CreateScenarioRequest, JobEventName, JobStatus, ToolConfirmationMode, ToolVisibility
from backend.worker.gdal_runtime import configure_gdal_runtime


@pytest.fixture
def services(monkeypatch, tmp_path: Path):
    import backend.api.dependencies as dependencies_module

    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str((tmp_path / "workspace").resolve()))
    monkeypatch.delenv("LUNAR_ANALYST_CONFIG_TOML", raising=False)
    dependencies_module.SERVICES = None
    configure_gdal_runtime()
    built = build_service_container()
    try:
        yield built
    finally:
        built.job_service.shutdown()
        built.notebook_job_service.terminate_all_running(reason="test shutdown")
        built.marimo_service.stop_if_running()


def test_list_tool_definitions_exposes_canonical_handler_metadata() -> None:
    definitions = list_tool_definitions(include_system=True)
    by_name = {item.tool_name: item for item in definitions.definitions}

    raster_calc = by_name["raster.calculate"]
    assert raster_calc.handler_name == "raster_calculate"
    assert raster_calc.implementation_name == "raster_calculate"
    assert raster_calc.visibility == ToolVisibility.PUBLIC
    assert raster_calc.confirmation.mode == ToolConfirmationMode.ALWAYS
    assert raster_calc.confirmation.action_type == "launch_job"
    assert raster_calc.params_schema["type"] == "object"
    assert raster_calc.outputs_schema["type"] == "object"
    assert raster_calc.response_model_name == "RasterCalculateResult"


def test_list_tool_definitions_filters_system_and_draft_tools() -> None:
    visible = list_tool_definitions(include_system=False)
    names = {item.tool_name for item in visible.definitions}

    assert "ping" not in names
    assert "raster.calculate" in names
    assert "generate_horizon_profile" not in names

    all_defs = list_tool_definitions(include_drafts=True, include_system=True)
    all_names = {item.tool_name for item in all_defs.definitions}
    assert "ping" in all_names
    assert "generate_horizon_profile" in all_names


def test_get_tool_definition_resolves_by_handler_name_alias() -> None:
    definition = get_tool_definition("raster_calculate", include_system=True)
    assert definition.tool_name == "raster.calculate"
    assert definition.handler_name == "raster_calculate"
    assert definition.implementation_name == "raster_calculate"


def test_local_analyst_tool_client_invokes_system_tool(services) -> None:
    services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="toolsmoke",
            name="Tool Smoke",
            owner="test",
        )
    )

    client = LocalAnalystToolClient(services)
    response = client.invoke_tool("ping", {"message": "hello"})

    assert response.tool_name == "ping"
    assert response.job.job_type == "ping"
    assert response.result == {}

    deadline = time.monotonic() + 5.0
    final_status = response.job.status
    while time.monotonic() < deadline:
        job = services.job_service.get_job(response.run_id)
        final_status = job.status
        if final_status == JobStatus.COMPLETED:
            break
        time.sleep(0.05)

    assert final_status == JobStatus.COMPLETED
    events = services.job_service.list_job_events(response.run_id)
    completed = next(event for event in reversed(events) if event.event_name == JobEventName.JOB_COMPLETED)
    assert completed.data["result"]["ok"] is True
    assert completed.data["result"]["message"] == "hello"
