from __future__ import annotations

import json
from pathlib import Path

import pytest

import backend.notebook.runtime as runtime


@pytest.fixture(autouse=True)
def _reset_runtime_state(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime.set_current_context(None)
    monkeypatch.delenv(runtime.CONTEXT_PATH_ENV, raising=False)
    yield
    runtime.set_current_context(None)


def _write_context_payload(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def test_is_running_under_job_runner_false_when_context_env_missing() -> None:
    assert runtime.is_running_under_job_runner() is False


def test_is_running_under_job_runner_false_for_non_runner_context_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context_path = tmp_path / "context.json"
    _write_context_payload(
        context_path,
        {
            "scenario_id": "scenario_1",
            "job_id": "job_1",
            "scenario_root_dir": str(tmp_path / "scenario"),
            "progress_path": str(tmp_path / "progress.jsonl"),
            "cancel_path": str(tmp_path / "cancel.flag"),
            "params": {},
        },
    )
    monkeypatch.setenv(runtime.CONTEXT_PATH_ENV, str(context_path))

    assert runtime.is_running_under_job_runner() is False


def test_is_running_under_job_runner_true_for_runner_context_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context_path = tmp_path / "context.json"
    _write_context_payload(
        context_path,
        {
            "scenario_id": "scenario_1",
            "job_id": "job_1",
            "scenario_root_dir": str(tmp_path / "scenario"),
            "notebook_path": str(tmp_path / "job.py"),
            "result_path": str(tmp_path / "result.json"),
            "progress_path": str(tmp_path / "progress.jsonl"),
            "cancel_path": str(tmp_path / "cancel.flag"),
            "params": {},
        },
    )
    monkeypatch.setenv(runtime.CONTEXT_PATH_ENV, str(context_path))

    assert runtime.is_running_under_job_runner() is True
