from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class WorkerProtocolPaths:
    context_path: Path
    progress_path: Path
    result_path: Path
    cancel_path: Path
    stdout_log_path: Path
    stderr_log_path: Path


def build_worker_protocol_paths(run_dir: Path) -> WorkerProtocolPaths:
    root = run_dir.expanduser().resolve()
    return WorkerProtocolPaths(
        context_path=root / "context.json",
        progress_path=root / "progress.jsonl",
        result_path=root / "result.json",
        cancel_path=root / "cancel.flag",
        stdout_log_path=root / "runner_stdout.log",
        stderr_log_path=root / "runner_stderr.log",
    )


def worker_context_payload(
    *,
    implementation_name: str,
    job_id: str,
    scenario_id: str,
    args: dict[str, Any],
    paths: WorkerProtocolPaths,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "implementation_name": str(implementation_name),
        "job_id": str(job_id),
        "scenario_id": str(scenario_id),
        "args": dict(args),
        "progress_path": str(paths.progress_path),
        "result_path": str(paths.result_path),
        "cancel_path": str(paths.cancel_path),
        "stdout_log_path": str(paths.stdout_log_path),
        "stderr_log_path": str(paths.stderr_log_path),
    }


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_progress_event(progress_path: Path, payload: dict[str, Any]) -> None:
    item = normalize_progress_event(payload)
    if item is None:
        return
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    with progress_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, sort_keys=True) + "\n")


def normalize_progress_event(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return None

    item = dict(payload)
    item["message"] = message

    if "percent" in item:
        try:
            percent = float(item["percent"])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(percent):
            return None
        item["percent"] = percent

    stage = item.get("stage")
    if stage is not None and not isinstance(stage, str):
        item["stage"] = str(stage)

    return item


def read_progress_events_since_line(
    progress_path: Path,
    start_index: int,
) -> tuple[int, list[dict[str, Any]]]:
    if not progress_path.exists() or not progress_path.is_file():
        return max(0, int(start_index)), []

    lines = progress_path.read_text(encoding="utf-8", errors="replace").splitlines()
    normalized_index = max(0, int(start_index))
    if normalized_index >= len(lines):
        return len(lines), []

    events: list[dict[str, Any]] = []
    for line in lines[normalized_index:]:
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        item = normalize_progress_event(payload)
        if item is not None:
            events.append(item)
    return len(lines), events


def request_cancel(cancel_path: Path, reason: str = "cancel requested") -> None:
    cancel_path.parent.mkdir(parents=True, exist_ok=True)
    cancel_path.write_text(str(reason), encoding="utf-8")


def is_cancel_requested(cancel_path: Path) -> bool:
    return cancel_path.exists()
