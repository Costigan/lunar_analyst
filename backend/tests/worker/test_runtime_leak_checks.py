from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from backend.api.dependencies import build_service_container
from backend.tests.support.leak_checks import (
    assert_no_new_non_daemon_threads,
    assert_process_exits,
    snapshot_non_daemon_threads,
)


def test_shutdown_services_no_non_daemon_thread_leak() -> None:
    before = snapshot_non_daemon_threads()
    services = build_service_container()
    try:
        services.job_service.shutdown()
        services.notebook_job_service.terminate_all_running(reason="test shutdown")
    finally:
        services.assistant_service.shutdown()
    assert_no_new_non_daemon_threads(before, timeout_seconds=2.0)


def test_notebook_runner_process_shutdown_is_detected(tmp_path: Path) -> None:
    services = build_service_container()
    cancel_path = tmp_path / "cancel.flag"
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with services.stores.notebook_job_lock:
            services.stores.notebook_job_processes["job-running"] = process
            services.stores.notebook_job_cancel_paths["job-running"] = cancel_path

        services.notebook_job_service.terminate_all_running(reason="leak-check test")
        assert_process_exits(process, timeout_seconds=5.0)
        assert cancel_path.exists()
    finally:
        if process.poll() is None:
            process.terminate()
            time.sleep(0.05)
        services.job_service.shutdown()
        services.assistant_service.shutdown()
