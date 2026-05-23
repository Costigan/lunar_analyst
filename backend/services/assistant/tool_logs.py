from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.contracts.models import JobEventName

if TYPE_CHECKING:
    from backend.api.dependencies import ServiceContainer


def extract_completed_job_result(services: "ServiceContainer", job_id: str) -> dict[str, Any]:
    events = services.job_service.list_job_events(job_id)
    for event in reversed(events):
        if event.event_name == JobEventName.JOB_COMPLETED:
            result = event.data.get("result", {})
            return result if isinstance(result, dict) else {}
    return {}


def read_log_slice(
    *,
    run_id: str,
    stream: str,
    path: Path,
    head_lines: int,
    tail_lines: int,
) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {
            "job_id": run_id,
            "run_id": run_id,
            "stream": stream,
            "path": str(path),
            "exists": False,
            "total_bytes": 0,
            "total_lines": 0,
            "head": [],
            "tail": [],
        }

    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    return {
        "job_id": run_id,
        "run_id": run_id,
        "stream": stream,
        "path": str(path),
        "exists": True,
        "total_bytes": path.stat().st_size,
        "total_lines": len(lines),
        "head": lines[:head_lines],
        "tail": list(deque(lines, maxlen=tail_lines)) if tail_lines > 0 else [],
    }
