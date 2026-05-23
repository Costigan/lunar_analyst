from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from backend.jobs.handlers import ToolImplementations
from backend.jobs.runtime_context import set_job_cancel_checker, set_job_progress_emitter
from backend.jobs.worker_protocol import (
    is_cancel_requested,
    write_json_file,
    write_progress_event,
)


def _serialize_result(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def run_from_context(context_path: Path) -> int:
    payload = json.loads(context_path.read_text(encoding="utf-8"))
    implementation_name = str(payload.get("implementation_name", "")).strip()
    args = payload.get("args", {})
    if not isinstance(args, dict):
        raise ValueError("Native worker context args must be a JSON object.")
    result_path = Path(payload.get("result_path", "")).expanduser().resolve()
    result_path.parent.mkdir(parents=True, exist_ok=True)
    progress_raw = str(payload.get("progress_path", "")).strip()
    progress_path = Path(progress_raw).expanduser().resolve() if progress_raw else None
    cancel_raw = str(payload.get("cancel_path", "")).strip()
    cancel_path = Path(cancel_raw).expanduser().resolve() if cancel_raw else None

    handler = getattr(ToolImplementations, implementation_name, None)
    if handler is None or not callable(handler) or not hasattr(handler, "__contract__"):
        write_json_file(
            result_path,
            {"ok": False, "error": f"Tool implementation not found: {implementation_name}"},
        )
        return 1

    if progress_path is not None:
        set_job_progress_emitter(lambda item: write_progress_event(progress_path, item))
    if cancel_path is not None:
        set_job_cancel_checker(lambda: is_cancel_requested(cancel_path))

    try:
        result = handler(**args)
    except Exception as exc:
        write_json_file(
            result_path,
            {
                "ok": False,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        return 1
    finally:
        set_job_progress_emitter(None)
        set_job_cancel_checker(None)

    write_json_file(
        result_path,
        {"ok": True, "result": _serialize_result(result)},
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a native tool implementation in isolated worker process.")
    parser.add_argument("--context", required=True, help="Path to JSON context payload.")
    args = parser.parse_args(argv)
    return run_from_context(Path(args.context).expanduser().resolve())


if __name__ == "__main__":
    code = main()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        # Avoid CLR/GDAL finalizer shutdown instability by hard exiting the worker process.
        os._exit(int(code))
