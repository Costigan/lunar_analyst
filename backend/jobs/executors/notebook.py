from __future__ import annotations

from typing import Any, Callable


def execute_run_notebook_definition(
    *,
    scenario_id: str,
    notebook_job_id: str,
    params: dict[str, Any] | None,
    runtime_mode: str,
    notebook_executor: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    raw = notebook_executor(
        scenario_id=scenario_id,
        notebook_job_id=notebook_job_id,
        params=params or {},
        runtime_mode=str(runtime_mode or "osgeo"),
    )
    return {
        "scenario_id": scenario_id,
        "notebook_job_id": notebook_job_id,
        "notebook_path": str(raw["notebook_path"]),
        "notebook_hash": str(raw["notebook_hash"]),
        "outputs": [item for item in raw.get("outputs", []) if isinstance(item, dict)],
        "result": raw.get("result", {}),
        "progress_events": [
            item for item in raw.get("progress_events", []) if isinstance(item, dict)
        ],
    }
