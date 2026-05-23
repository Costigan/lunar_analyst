from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from backend.jobs.runtime_context import emit_job_progress, is_job_cancel_requested
from backend.jobs.worker_protocol import build_worker_protocol_paths
from backend.worker import native_job_dispatcher


def _install_handler(monkeypatch, name: str, handler: Callable[..., Any]) -> None:
    setattr(handler, "__contract__", object())
    monkeypatch.setattr(
        native_job_dispatcher.ToolImplementations,
        name,
        staticmethod(handler),
        raising=False,
    )


def _write_context(tmp_path: Path, *, implementation_name: str, args: dict[str, Any] | None = None) -> Any:
    paths = build_worker_protocol_paths(tmp_path / "run")
    payload = {
        "protocol_version": 1,
        "implementation_name": implementation_name,
        "job_id": "job-1",
        "scenario_id": "scenario-1",
        "args": args or {},
        "progress_path": str(paths.progress_path),
        "result_path": str(paths.result_path),
        "cancel_path": str(paths.cancel_path),
        "stdout_log_path": str(paths.stdout_log_path),
        "stderr_log_path": str(paths.stderr_log_path),
    }
    paths.context_path.parent.mkdir(parents=True, exist_ok=True)
    paths.context_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return paths


def test_dispatcher_writes_handler_progress_to_jsonl(monkeypatch, tmp_path: Path) -> None:
    def handler() -> dict[str, str]:
        emit_job_progress({"percent": 25, "message": "Native phase started.", "stage": "native"})
        emit_job_progress({"message": "Native status without percent.", "event_kind": "native_status"})
        return {"status": "ok"}

    _install_handler(monkeypatch, "phase2_progress_test", handler)
    paths = _write_context(tmp_path, implementation_name="phase2_progress_test")

    code = native_job_dispatcher.run_from_context(paths.context_path)

    assert code == 0
    result = json.loads(paths.result_path.read_text(encoding="utf-8"))
    assert result == {"ok": True, "result": {"status": "ok"}}
    progress = [
        json.loads(line)
        for line in paths.progress_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert progress == [
        {"percent": 25.0, "message": "Native phase started.", "stage": "native"},
        {"message": "Native status without percent.", "event_kind": "native_status"},
    ]


def test_dispatcher_cancel_checker_reads_cancel_flag(monkeypatch, tmp_path: Path) -> None:
    def handler() -> dict[str, bool]:
        return {"cancelled": is_job_cancel_requested()}

    _install_handler(monkeypatch, "phase2_cancel_test", handler)
    paths = _write_context(tmp_path, implementation_name="phase2_cancel_test")
    paths.cancel_path.write_text("stop", encoding="utf-8")

    code = native_job_dispatcher.run_from_context(paths.context_path)

    assert code == 0
    result = json.loads(paths.result_path.read_text(encoding="utf-8"))
    assert result == {"ok": True, "result": {"cancelled": True}}


def test_dispatcher_clears_runtime_context_after_run(monkeypatch, tmp_path: Path) -> None:
    def handler() -> dict[str, str]:
        emit_job_progress({"percent": 5, "message": "Running."})
        return {"status": "ok"}

    _install_handler(monkeypatch, "phase2_cleanup_test", handler)
    paths = _write_context(tmp_path, implementation_name="phase2_cleanup_test")

    code = native_job_dispatcher.run_from_context(paths.context_path)

    assert code == 0
    assert not is_job_cancel_requested()
    emit_job_progress({"percent": 99, "message": "Should not be written."})
    progress = [
        json.loads(line)
        for line in paths.progress_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert progress == [{"percent": 5.0, "message": "Running."}]


def test_dispatcher_writes_failure_result_for_handler_exception(monkeypatch, tmp_path: Path) -> None:
    def handler() -> None:
        raise RuntimeError("boom")

    _install_handler(monkeypatch, "phase2_failure_test", handler)
    paths = _write_context(tmp_path, implementation_name="phase2_failure_test")

    code = native_job_dispatcher.run_from_context(paths.context_path)

    assert code == 1
    result = json.loads(paths.result_path.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert result["error"] == "boom"
    assert "traceback" in result
