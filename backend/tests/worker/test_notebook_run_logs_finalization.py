from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from backend.api.dependencies import BoundedRunInfoMap, NotebookJobService
from backend.contracts.models import Job, JobMode, JobStatus


def _build_service(tmp_path: Path) -> NotebookJobService:
    stores = SimpleNamespace(
        notebook_run_info=BoundedRunInfoMap(max_items=10),
        jobs={},
        notebook_job_lock=threading.RLock(),
    )
    service = object.__new__(NotebookJobService)
    service._stores = stores  # type: ignore[attr-defined]
    service._scenario_service = SimpleNamespace()  # type: ignore[attr-defined]
    service._progress_reporter = None  # type: ignore[attr-defined]
    return service


def test_notebook_run_logs_is_final_only_after_process_and_streams_complete(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    run_id = "job-finalize"
    stdout_path = tmp_path / "runner_stdout.log"
    stderr_path = tmp_path / "runner_stderr.log"
    stdout_path.write_text("hello\n", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    stdout_done = threading.Event()
    stderr_done = threading.Event()

    service._stores.notebook_run_info[run_id] = {  # type: ignore[attr-defined]
        "stdout_log_path": str(stdout_path),
        "stderr_log_path": str(stderr_path),
        "process_exited": False,
        "logs_finalized": False,
        "stdout_done_event": stdout_done,
        "stderr_done_event": stderr_done,
    }

    first = service.get_notebook_run_logs(run_id=run_id, stream="combined", head_lines=0, tail_lines=50)
    assert first["is_final"] is False

    service._mark_notebook_run_process_exited(job_id=run_id)
    second = service.get_notebook_run_logs(run_id=run_id, stream="combined", head_lines=0, tail_lines=50)
    assert second["is_final"] is False

    stdout_done.set()
    stderr_done.set()
    third = service.get_notebook_run_logs(run_id=run_id, stream="combined", head_lines=0, tail_lines=50)
    assert third["is_final"] is True
    assert third["streams"]["stdout"]["is_final"] is True
    assert third["streams"]["stderr"]["is_final"] is True


def test_notebook_run_logs_unknown_job_terminal_is_final_true(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    run_id = "job-terminal"
    service._stores.jobs[run_id] = Job(  # type: ignore[attr-defined]
        job_id=run_id,
        scenario_id="scn_test",
        job_type="ping",
        mode=JobMode.QUEUED,
        status=JobStatus.COMPLETED,
        params={},
        requested_at_utc="2026-01-01T00-00-00",
        updated_at_utc="2026-01-01T00-00-00",
        finished_at_utc="2026-01-01T00-00-01",
    )

    payload = service.get_notebook_run_logs(run_id=run_id, stream="combined", head_lines=0, tail_lines=50)
    assert payload["is_final"] is True
    assert payload["pending"] is False


def test_notebook_progress_reader_uses_shared_malformed_line_handling(tmp_path: Path) -> None:
    service = _build_service(tmp_path)
    progress_path = tmp_path / "progress.jsonl"
    progress_path.write_text(
        "\n".join(
            [
                '{"message": "first", "percent": 5}',
                "not-json",
                '{"message": "native status", "event_kind": "native_status"}',
                '{"message": "bad percent", "percent": "not-a-number"}',
                json.dumps({"message": "last", "stage": 123, "percent": 50}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    next_index, events = service._read_progress_events_since_line(progress_path, 0)  # type: ignore[attr-defined]

    assert next_index == 5
    assert events == [
        {"message": "first", "percent": 5.0},
        {"message": "native status", "event_kind": "native_status"},
        {"message": "last", "stage": "123", "percent": 50.0},
    ]
    assert service._read_progress_events(progress_path) == events  # type: ignore[attr-defined]
