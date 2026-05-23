from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.jobs.worker_protocol import is_cancel_requested, write_progress_event


@dataclass
class NotebookJobContext:
    scenario_id: str
    job_id: str
    scenario_root_dir: Path
    params: dict[str, Any]
    progress_path: Path
    cancel_path: Path
    _outputs: list[dict[str, Any]] = field(default_factory=list)

    def report_progress(
        self,
        *,
        percent: float,
        message: str,
        stage: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "percent": float(percent),
            "message": str(message),
        }
        if stage is not None:
            payload["stage"] = str(stage)
        write_progress_event(self.progress_path, payload)

    def is_cancelled(self) -> bool:
        return is_cancel_requested(self.cancel_path)

    def register_output(
        self,
        *,
        relative_path: str,
        kind: str,
        subkind: str,
        render_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "relative_path": str(relative_path),
            "kind": str(kind),
            "subkind": str(subkind),
            "metadata": metadata or {},
        }
        if render_mode is not None:
            entry["render_mode"] = str(render_mode)
        self._outputs.append(entry)

    @property
    def outputs(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._outputs]
