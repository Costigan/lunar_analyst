from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import logging
import os
import runpy
import traceback
from pathlib import Path
from typing import Any

from backend.jobs.runtime_context import set_job_cancel_checker, set_job_progress_emitter
from backend.jobs.worker_protocol import write_progress_event
from backend.notebook.job_sdk import NotebookJobContext
from backend.notebook.runtime import CONTEXT_PATH_ENV, set_current_context
from backend.worker.gdal_runtime import resolve_gdal_data_dir, resolve_proj_data_dir


_RUNTIME_MODES = {"osgeo", "moonlib"}
logger = logging.getLogger(__name__)


def _parse_runtime_mode_pragma(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for _idx, line in zip(range(40), handle):
                text = str(line).strip()
                if not text.startswith("#"):
                    continue
                lowered = text.lower()
                marker = "lunar_runtime:"
                pos = lowered.find(marker)
                if pos < 0:
                    continue
                value = text[pos + len(marker) :].strip().lower()
                if value in _RUNTIME_MODES:
                    return value
    except Exception:
        return None
    return None


def _resolve_runtime_mode(payload: dict[str, Any], *, notebook_path: Path) -> str:
    explicit = str(payload.get("runtime_mode", "")).strip().lower()
    if explicit in _RUNTIME_MODES:
        return explicit
    params = payload.get("params", {})
    if isinstance(params, dict):
        from_params = str(params.get("runtime_mode", "")).strip().lower()
        if from_params in _RUNTIME_MODES:
            return from_params
    pragma = _parse_runtime_mode_pragma(notebook_path)
    if pragma in _RUNTIME_MODES:
        return pragma
    return "osgeo"


def _configure_osgeo_env() -> dict[str, str]:
    updates: dict[str, str] = {}
    gdal_data = resolve_gdal_data_dir()
    proj_data = resolve_proj_data_dir()
    if gdal_data is not None and gdal_data.exists():
        os.environ["GDAL_DATA"] = str(gdal_data)
        updates["GDAL_DATA"] = str(gdal_data)
    if proj_data is not None and proj_data.exists():
        os.environ["PROJ_LIB"] = str(proj_data)
        os.environ["PROJ_DATA"] = str(proj_data)
        updates["PROJ_LIB"] = str(proj_data)
        updates["PROJ_DATA"] = str(proj_data)
    return updates


def _scan_mode_conflicts(*, runtime_mode: str, notebook_path: Path) -> None:
    try:
        tree = ast.parse(notebook_path.read_text(encoding="utf-8"), filename=str(notebook_path))
    except Exception:
        return
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(str(alias.name).split(".", 1)[0].lower())
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(str(node.module).split(".", 1)[0].lower())
    if runtime_mode == "osgeo":
        disallowed = sorted(name for name in imports if name in {"moonlib", "pythonnet", "clr"})
        if disallowed:
            joined = ", ".join(disallowed)
            raise RuntimeError(
                "script_runtime_mode_conflict: runtime_mode=osgeo is incompatible with imports "
                f"[{joined}]. Re-run with runtime_mode='moonlib'."
            )


def _configure_runtime_environment(*, runtime_mode: str, notebook_path: Path) -> None:
    _scan_mode_conflicts(runtime_mode=runtime_mode, notebook_path=notebook_path)
    if runtime_mode == "osgeo":
        updates = _configure_osgeo_env()
        if updates:
            logger.info(
                "notebook runner runtime_mode=osgeo configured GDAL/PROJ env: %s",
                json.dumps(updates, sort_keys=True),
            )
        return
    logger.info("notebook runner runtime_mode=moonlib (no osgeo env override applied)")


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location("lunar_notebook_job", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load notebook module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _defines_top_level_run(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run":
            return True
    return False


def _coerce_system_exit(exc: SystemExit) -> tuple[bool, str]:
    code = exc.code
    if code is None:
        return True, ""
    if isinstance(code, int):
        if code == 0:
            return True, ""
        return False, f"Script exited with status code {code}."
    text = str(code).strip()
    if not text:
        return False, "Script exited with non-zero status."
    return False, text


def run_from_context(context_path: Path) -> int:
    payload = json.loads(context_path.read_text(encoding="utf-8"))
    notebook_path = Path(payload["notebook_path"]).resolve()
    result_path = Path(payload["result_path"]).resolve()
    progress_path = Path(payload["progress_path"]).resolve()
    cancel_path = Path(payload["cancel_path"]).resolve()
    scenario_root_dir = Path(payload["scenario_root_dir"]).resolve()
    params = payload.get("params", {})
    runtime_mode = _resolve_runtime_mode(payload, notebook_path=notebook_path)
    context = NotebookJobContext(
        scenario_id=str(payload["scenario_id"]),
        job_id=str(payload["job_id"]),
        scenario_root_dir=scenario_root_dir,
        params=params if isinstance(params, dict) else {},
        progress_path=progress_path,
        cancel_path=cancel_path,
    )
    previous_context_path = os.environ.get(CONTEXT_PATH_ENV)
    os.environ[CONTEXT_PATH_ENV] = str(context_path)
    set_current_context(context)
    set_job_progress_emitter(
        lambda payload: write_progress_event(progress_path, payload)
    )
    set_job_cancel_checker(lambda: context.is_cancelled())
    try:
        _configure_runtime_environment(runtime_mode=runtime_mode, notebook_path=notebook_path)
        try:
            if _defines_top_level_run(notebook_path):
                module = _load_module(notebook_path)
                run_callable = getattr(module, "run", None)
                if run_callable is None or not callable(run_callable):
                    raise RuntimeError(
                        f"Notebook job {notebook_path} defines run but it is not callable."
                    )
                raw_result = run_callable(context)
                result = raw_result if isinstance(raw_result, dict) else {"value": raw_result}
            else:
                # Compatibility path for script-style jobs implemented under
                # `if __name__ == "__main__": ...`.
                runpy.run_path(str(notebook_path), run_name="__main__")
                result = {}
        except SystemExit as exc:
            ok_exit, message = _coerce_system_exit(exc)
            if ok_exit:
                # Preserve compatibility with script-style workflows that end with
                # `raise SystemExit(0)`/`sys.exit(0)`.
                result = {}
            else:
                raise RuntimeError(message or "Script exited unexpectedly.") from exc
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "result": result,
                    "outputs": context.outputs,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return 0
    except Exception as exc:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "outputs": context.outputs,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return 1
    finally:
        set_current_context(None)
        set_job_progress_emitter(None)
        set_job_cancel_checker(None)
        if previous_context_path is None:
            os.environ.pop(CONTEXT_PATH_ENV, None)
        else:
            os.environ[CONTEXT_PATH_ENV] = previous_context_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a notebook job script in headless mode.")
    parser.add_argument("--context", required=True, help="Path to runner context JSON.")
    args = parser.parse_args(argv)
    return run_from_context(Path(args.context))


if __name__ == "__main__":
    raise SystemExit(main())
