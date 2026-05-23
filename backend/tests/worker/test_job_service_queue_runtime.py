from __future__ import annotations

import time
from pathlib import Path

import pytest

from backend.api.dependencies import build_service_container
from backend.contracts.models import JobStatus


def _build_services(monkeypatch, tmp_path: Path):
    import backend.api.dependencies as dependencies_module

    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.delenv("LUNAR_ANALYST_CONFIG_TOML", raising=False)
    dependencies_module.SERVICES = None
    return build_service_container()


def _wait_for_terminal_status(services, job_id: str, *, timeout_seconds: float = 5.0) -> str:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status = services.job_service.get_job(job_id).status.value
        if status in {"completed", "failed", "cancelled"}:
            return status
        time.sleep(0.02)
    raise AssertionError(f"job did not reach terminal status: {job_id}")


def test_job_service_queued_mode_returns_queued_then_completes(monkeypatch, tmp_path: Path) -> None:
    services = _build_services(monkeypatch, tmp_path)
    try:
        queued = services.job_service.run_typed_job("ping", {"message": "queued"})
        assert queued.status == JobStatus.QUEUED

        terminal = _wait_for_terminal_status(services, queued.job_id)
        assert terminal == "completed"

        events = services.job_service.list_job_events(queued.job_id)
        names = [event.event_name.value for event in events]
        assert names[0] == "job_queued"
        assert "job_started" in names
        assert names[-1] == "job_completed"
        ws_events = [
            event
            for event in list(services.stores.ws_events)
            if str(event.get("data", {}).get("job_id", "")) == queued.job_id
            and str(event.get("event", "")).strip() in {"job_queued", "job_started"}
        ]
        assert ws_events
        for event in ws_events:
            payload = event.get("data", {})
            assert payload.get("job_type") == "ping"
            assert payload.get("handler_name") == "ping"
            assert payload.get("title") == "ping"
    finally:
        services.job_service.shutdown()
        services.notebook_job_service.terminate_all_running(reason="test shutdown")
        services.marimo_service.stop_if_running()


def test_job_service_immediate_mode_executes_in_request(monkeypatch, tmp_path: Path) -> None:
    services = _build_services(monkeypatch, tmp_path)
    try:
        with pytest.raises(KeyError):
            services.job_service.run_typed_job(
                "run_notebook_definition",
                {
                    "scenario_id": "scn_missing",
                    "notebook_job_id": "missing",
                    "params": {},
                    "mode": "immediate",
                },
            )
        failed = next(iter(services.stores.jobs.values()))
        assert failed.status == JobStatus.FAILED
    finally:
        services.job_service.shutdown()
        services.notebook_job_service.terminate_all_running(reason="test shutdown")
        services.marimo_service.stop_if_running()


def test_lightmap_reduction_handler_uses_native_worker_by_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import backend.api.dependencies as dependencies_module

    services = _build_services(monkeypatch, tmp_path)
    monkeypatch.delenv("LUNAR_ANALYST_NATIVE_INLINE_HANDLERS", raising=False)
    worker_only_handlers = {
        "generate_horizons",
        "generate_average_sun_fraction_raster",
        "generate_earth_above_terrain_duration_raster",
        "generate_combined_sun_earth_max_contiguous_duration_raster",
        "generate_lightmap_timeseries",
        "generate_psr_raster",
    }
    assert worker_only_handlers.issubset(dependencies_module.JobService.WORKER_ONLY_HANDLER_NAMES)
    for name in worker_only_handlers:
        handler = getattr(dependencies_module.ToolImplementations, name)
        assert dependencies_module.JobService._is_worker_only_handler(name, handler)
    calls: list[tuple[str, dict[str, object]]] = []

    def _fake_native_worker(**kwargs):
        calls.append((kwargs["handler_name"], dict(kwargs["args"])))
        return dependencies_module.JobService._NativeWorkerResult(
            result={"status": "worker", "handler": kwargs["handler_name"]}
        )

    try:
        monkeypatch.setattr(
            services.job_service,
            "_run_native_handler_subprocess",
            _fake_native_worker,
        )
        job = services.job_service.run_typed_job(
            "generate_average_sun_fraction_raster",
            {
                "scenario_id": "s-worker",
                "scenario_root_dir": str(tmp_path / "scenario"),
                "dem_path": str(tmp_path / "scenario" / "dem.tif"),
                "horizons_dir": str(tmp_path / "scenario" / "lighting" / "horizons"),
                "output_path": str(tmp_path / "scenario" / "lighting" / "avg.tif"),
                "time_start_utc": "2027-01-01T00:00:00Z",
                "time_stop_utc": "2027-01-01T01:00:00Z",
                "time_step_hours": 1.0,
                "mode": "immediate",
            },
        )

        assert job.status == JobStatus.COMPLETED
        assert calls
        assert calls[0][0] == "generate_average_sun_fraction_raster"
    finally:
        services.job_service.shutdown()
        services.notebook_job_service.terminate_all_running(reason="test shutdown")
        services.marimo_service.stop_if_running()


def test_worker_only_debug_escape_hatch_allows_inline_execution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import backend.api.dependencies as dependencies_module

    services = _build_services(monkeypatch, tmp_path)
    monkeypatch.setenv("LUNAR_ANALYST_NATIVE_INLINE_HANDLERS", "1")
    calls: list[dict[str, object]] = []
    original = dependencies_module.ToolImplementations.generate_average_sun_fraction_raster

    def _fake_handler(**kwargs):
        calls.append(dict(kwargs))
        return {"status": "inline"}

    setattr(_fake_handler, "__contract__", getattr(original, "__contract__"))
    monkeypatch.setattr(
        dependencies_module.ToolImplementations,
        "generate_average_sun_fraction_raster",
        staticmethod(_fake_handler),
    )

    def _fail_native_worker(**_kwargs):
        raise AssertionError("worker-only debug escape hatch should execute inline")

    try:
        monkeypatch.setattr(
            services.job_service,
            "_run_native_handler_subprocess",
            _fail_native_worker,
        )
        job = services.job_service.run_typed_job(
            "generate_average_sun_fraction_raster",
            {
                "scenario_id": "s-inline",
                "scenario_root_dir": str(tmp_path / "scenario"),
                "dem_path": str(tmp_path / "scenario" / "dem.tif"),
                "horizons_dir": str(tmp_path / "scenario" / "lighting" / "horizons"),
                "output_path": str(tmp_path / "scenario" / "lighting" / "avg.tif"),
                "time_start_utc": "2027-01-01T00:00:00Z",
                "time_stop_utc": "2027-01-01T01:00:00Z",
                "time_step_hours": 1.0,
                "mode": "immediate",
            },
        )

        assert job.status == JobStatus.COMPLETED
        assert calls
        assert calls[0]["scenario_id"] == "s-inline"
    finally:
        services.job_service.shutdown()
        services.notebook_job_service.terminate_all_running(reason="test shutdown")
        services.marimo_service.stop_if_running()


def test_worker_only_job_keeps_control_plane_responsive(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import backend.api.dependencies as dependencies_module

    services = _build_services(monkeypatch, tmp_path)
    monkeypatch.delenv("LUNAR_ANALYST_NATIVE_INLINE_HANDLERS", raising=False)
    worker_started = False
    release_worker = False

    def _fake_native_worker(**kwargs):
        nonlocal worker_started, release_worker
        worker_started = True
        deadline = time.time() + 2.0
        while not release_worker and time.time() < deadline:
            time.sleep(0.01)
        return dependencies_module.JobService._NativeWorkerResult(
            result={"status": "worker", "handler": kwargs["handler_name"]}
        )

    try:
        monkeypatch.setattr(
            services.job_service,
            "_run_native_handler_subprocess",
            _fake_native_worker,
        )
        queued = services.job_service.run_typed_job(
            "generate_average_sun_fraction_raster",
            {
                "scenario_id": "s-responsive",
                "scenario_root_dir": str(tmp_path / "scenario"),
                "dem_path": str(tmp_path / "scenario" / "dem.tif"),
                "horizons_dir": str(tmp_path / "scenario" / "lighting" / "horizons"),
                "output_path": str(tmp_path / "scenario" / "lighting" / "avg.tif"),
                "time_start_utc": "2027-01-01T00:00:00Z",
                "time_stop_utc": "2027-01-01T01:00:00Z",
                "time_step_hours": 1.0,
            },
        )
        assert queued.status == JobStatus.QUEUED

        deadline = time.time() + 1.0
        while not worker_started and time.time() < deadline:
            time.sleep(0.01)
        assert worker_started
        assert services.job_service.get_job(queued.job_id).status in {
            JobStatus.QUEUED,
            JobStatus.RUNNING,
        }
        assert services.job_service.list_job_events(queued.job_id)

        release_worker = True
        terminal = _wait_for_terminal_status(services, queued.job_id)
        assert terminal == "completed"
    finally:
        release_worker = True
        services.job_service.shutdown()
        services.notebook_job_service.terminate_all_running(reason="test shutdown")
        services.marimo_service.stop_if_running()


def test_notebook_log_line_progress_streams_to_ws_without_job_event_or_workspace_message(
    monkeypatch,
    tmp_path: Path,
) -> None:
    services = _build_services(monkeypatch, tmp_path)
    try:
        job_id = "job-log-line"
        scenario_id = "scn_log"
        services.job_service._on_notebook_progress(  # type: ignore[attr-defined]
            job_id,
            scenario_id,
            {
                "event_kind": "log_line",
                "log_stream": "stdout",
                "log_line": "hello from fast script",
            },
        )

        ws_events = [
            event
            for event in list(services.stores.ws_events)
            if str(event.get("event", "")).strip() == "job_progress"
            and str(event.get("scenario_id", "")).strip() == scenario_id
            and str(event.get("data", {}).get("job_id", "")).strip() == job_id
        ]
        assert ws_events
        latest = ws_events[-1]
        payload = latest.get("data", {})
        assert payload.get("event_kind") == "log_line"
        assert payload.get("log_stream") == "stdout"
        assert payload.get("log_line") == "hello from fast script"

        assert services.job_service.list_job_events(job_id) == []
        messages_path = (
            services.stores.workspace_root
            / ".lunar_analyst"
            / "messages"
            / f"{scenario_id}.jsonl"
        )
        assert not messages_path.exists()
    finally:
        services.job_service.shutdown()
        services.notebook_job_service.terminate_all_running(reason="test shutdown")
        services.marimo_service.stop_if_running()
