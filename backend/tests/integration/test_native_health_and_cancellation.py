from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import build_service_container
from backend.contracts.models import Job, JobEvent, JobEventName, JobMode, JobStatus
from backend.worker.native_bootstrap import NativeBootstrapError


def test_native_health_endpoint_reports_bootstrap_status(monkeypatch) -> None:
    import backend.api.routers.v1 as v1_module

    monkeypatch.setattr(v1_module, "bootstrap_status", lambda: {"loaded": False})
    client = TestClient(create_app())
    response = client.get("/api/v1/health/native")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["native"]["loaded"] is False


def test_native_health_probe_reports_degraded_on_bootstrap_error(monkeypatch) -> None:
    import backend.api.routers.v1 as v1_module

    monkeypatch.setattr(v1_module, "bootstrap_status", lambda: {"loaded": False})

    def _raise_bootstrap_error(*args, **kwargs):
        raise NativeBootstrapError("native bootstrap failed")

    monkeypatch.setattr(v1_module, "bootstrap_pythonnet", _raise_bootstrap_error)

    client = TestClient(create_app())
    response = client.get("/api/v1/health/native?probe=true")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert "native bootstrap failed" in payload["native_error"]


def test_cancel_job_emits_cancelled_event(monkeypatch) -> None:
    import backend.api.dependencies as dependencies_module

    dependencies_module.SERVICES = build_service_container()
    services = dependencies_module.SERVICES
    stores = services.job_service._stores
    stores.jobs["job-running"] = Job(
        job_id="job-running",
        scenario_id="s-cancel",
        job_type="generate_hillshade",
        mode=JobMode.QUEUED,
        status=JobStatus.RUNNING,
        params={},
        requested_at_utc="2026-01-01T00-00-00",
        started_at_utc="2026-01-01T00-00-01",
        finished_at_utc=None,
        updated_at_utc="2026-01-01T00-00-01",
    )
    stores.job_events["job-running"] = [
        JobEvent(
            event_id="evt-started",
            job_id="job-running",
            scenario_id="s-cancel",
            event_name=JobEventName.JOB_STARTED,
            timestamp_utc="2026-01-01T00-00-01",
            data={},
        )
    ]

    client = TestClient(create_app())
    response = client.post("/api/v1/jobs/job-running/cancel")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "cancelled"
    assert payload["finished_at_utc"] is not None

    events = client.get("/api/v1/jobs/job-running/events").json()
    assert events[-1]["event_name"] == "job_cancelled"
    assert events[-1]["data"]["reason"] == "cancel requested"


def test_shutdown_services_terminates_active_notebook_runner_processes(tmp_path: Path) -> None:
    import backend.api.dependencies as dependencies_module

    dependencies_module.SERVICES = build_service_container()
    services = dependencies_module.SERVICES
    cancel_path = tmp_path / "cancel.flag"
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    with services.stores.notebook_job_lock:
        services.stores.notebook_job_processes["job-running"] = process
        services.stores.notebook_job_cancel_paths["job-running"] = cancel_path

    dependencies_module.shutdown_services()

    deadline = time.time() + 5.0
    while process.poll() is None and time.time() < deadline:
        time.sleep(0.05)
    assert process.poll() is not None
    assert cancel_path.exists()
