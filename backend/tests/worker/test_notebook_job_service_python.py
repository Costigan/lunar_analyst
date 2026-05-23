from __future__ import annotations

import sys
from pathlib import Path

import backend.api.dependencies as deps


def test_notebook_job_service_python_executable_falls_back_to_current_python(
    monkeypatch,
    tmp_path: Path,
) -> None:
    service = object.__new__(deps.NotebookJobService)
    missing = tmp_path / "missing-python" / "python.exe"
    monkeypatch.setattr(
        deps,
        "_load_app_config",
        lambda: {"backend": {"notebook_jobs": {"python_executable": str(missing)}}},
    )

    selected = deps.NotebookJobService._python_executable(service)

    assert selected == str(Path(sys.executable).expanduser())
