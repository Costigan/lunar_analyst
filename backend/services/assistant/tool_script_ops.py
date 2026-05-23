from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.contracts.models import JobMode

from backend.services.assistant.tool_artifact_resolution import resolve_relative_path

if TYPE_CHECKING:
    from backend.api.dependencies import ServiceContainer


def write_scenario_script(
    services: "ServiceContainer",
    *,
    scenario_id: str,
    relative_path: str,
    content: str,
    overwrite: bool,
) -> dict[str, Any]:
    if not scenario_id or not relative_path:
        raise ValueError("scenario_id and relative_path are required")
    if not relative_path.lower().endswith(".py"):
        raise ValueError("relative_path must end with .py")

    scenario = services.scenario_service.get_scenario(scenario_id)
    scenario_root = Path(scenario.directory).expanduser().resolve()
    target = resolve_relative_path(scenario_root, relative_path)

    existed_before = target.exists()
    if existed_before and not overwrite:
        raise FileExistsError(f"Script already exists: {relative_path}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    return {
        "scenario_id": scenario_id,
        "relative_path": target.relative_to(scenario_root).as_posix(),
        "script_path": str(target),
        "bytes_written": target.stat().st_size,
        "existed_before": existed_before,
        "overwrite": overwrite,
    }


def run_scenario_python_entry(
    services: "ServiceContainer",
    *,
    scenario_id: str,
    relative_path: str,
    expect_marimo: bool,
    runtime_mode: str,
    completed_result_reader: Any,
    jsonable_converter: Any,
) -> dict[str, Any]:
    if not scenario_id or not relative_path:
        raise ValueError("scenario_id and relative_path are required")

    notebook_job_id = services.notebook_job_service.resolve_scenario_notebook_job_id(
        scenario_id=scenario_id,
        relative_path=relative_path,
        expect_marimo=expect_marimo,
    )

    runtime_mode_norm = str(runtime_mode or "osgeo").strip().lower() or "osgeo"
    if runtime_mode_norm not in {"osgeo", "moonlib"}:
        raise ValueError("runtime_mode must be one of: osgeo, moonlib")

    job = services.job_service.run_typed_job(
        "run_notebook_definition",
        {
            "scenario_id": scenario_id,
            "notebook_job_id": notebook_job_id,
            "params": {},
            "runtime_mode": runtime_mode_norm,
            "mode": JobMode.IMMEDIATE.value,
        },
    )

    result_payload = completed_result_reader(services, job.job_id)
    run_meta = dict(services.stores.notebook_run_info.get(job.job_id, {}))

    return {
        "run_id": job.job_id,
        "job_id": job.job_id,
        "status": job.status.value,
        "scenario_id": scenario_id,
        "notebook_job_id": notebook_job_id,
        "runtime_mode": runtime_mode_norm,
        "relative_path": relative_path,
        "result": jsonable_converter(result_payload),
        "run_metadata": jsonable_converter(run_meta),
    }
