from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from backend.notebook.job_sdk import NotebookJobContext


CONTEXT_PATH_ENV = "LUNAR_NOTEBOOK_CONTEXT_PATH"
_CURRENT_CONTEXT: NotebookJobContext | None = None
_JOB_RUNNER_CONTEXT_KEYS = frozenset(
    {
        "scenario_id",
        "job_id",
        "scenario_root_dir",
        "notebook_path",
        "result_path",
        "progress_path",
        "cancel_path",
    }
)


def set_current_context(context: NotebookJobContext | None) -> None:
    global _CURRENT_CONTEXT
    _CURRENT_CONTEXT = context


def get_context() -> NotebookJobContext:
    global _CURRENT_CONTEXT
    if _CURRENT_CONTEXT is not None:
        return _CURRENT_CONTEXT
    path_raw = os.getenv(CONTEXT_PATH_ENV, "").strip()
    if not path_raw:
        raise RuntimeError(
            f"Notebook runtime context not available. Set {CONTEXT_PATH_ENV} or call set_current_context()."
        )
    context_path = Path(path_raw).expanduser().resolve()
    payload = json.loads(context_path.read_text(encoding="utf-8"))
    _CURRENT_CONTEXT = NotebookJobContext(
        scenario_id=str(payload["scenario_id"]),
        job_id=str(payload["job_id"]),
        scenario_root_dir=Path(payload["scenario_root_dir"]).resolve(),
        params=payload.get("params", {}) if isinstance(payload.get("params", {}), dict) else {},
        progress_path=Path(payload["progress_path"]).resolve(),
        cancel_path=Path(payload["cancel_path"]).resolve(),
    )
    return _CURRENT_CONTEXT


def is_running_under_job_runner() -> bool:
    path_raw = os.getenv(CONTEXT_PATH_ENV, "").strip()
    if not path_raw:
        return False
    try:
        context_path = Path(path_raw).expanduser().resolve()
        payload = json.loads(context_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    return _JOB_RUNNER_CONTEXT_KEYS.issubset(payload.keys())


def infer_local_scenario_identity_and_root() -> tuple[str, Path] | None:
    candidates: list[Path] = []
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    if isinstance(main_file, str) and main_file.strip():
        candidates.append(Path(main_file).expanduser().resolve())
    try:
        candidates.append(Path.cwd().resolve())
    except Exception:
        pass

    seen: set[str] = set()
    for candidate in candidates:
        start = candidate if candidate.is_dir() else candidate.parent
        for current in (start, *start.parents):
            key = str(current).lower()
            if key in seen:
                continue
            seen.add(key)
            if _looks_like_scenario_root(current):
                return current.name, current.resolve()
    return None


def _looks_like_scenario_root(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    if (path / "scenario.db").exists():
        return True
    return any((path / name).exists() for name in ("primary_dem.tif", "dem.tif"))


def report_progress(*, percent: float, message: str, stage: str | None = None) -> None:
    get_context().report_progress(percent=percent, message=message, stage=stage)


def register_output(
    *,
    relative_path: str,
    kind: str,
    subkind: str,
    render_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    get_context().register_output(
        relative_path=relative_path,
        kind=kind,
        subkind=subkind,
        render_mode=render_mode,
        metadata=metadata,
    )


def is_cancelled() -> bool:
    return get_context().is_cancelled()


def safe_scenario_relative_path(raw: str, *, default: str = "dem.tif") -> str:
    rel = str(raw).strip().replace("\\", "/")
    if not rel:
        return default
    if rel.startswith("/") or rel.startswith("../") or "/../" in f"/{rel}/":
        raise ValueError(f"Invalid scenario-relative path: {raw}")
    return rel


def resolve_primary_dem_path(
    *,
    scenario_root_dir: Path | None = None,
    scenario_id: str | None = None,
) -> Path:
    import sqlite3

    context = None
    if scenario_root_dir is None or scenario_id is None:
        context = get_context()
    scenario_root = (
        Path(scenario_root_dir).expanduser().resolve()
        if scenario_root_dir is not None
        else Path(context.scenario_root_dir).resolve()
    )
    resolved_scenario_id = (
        str(scenario_id)
        if scenario_id is not None
        else str(context.scenario_id)
    )

    db_path = scenario_root / "scenario.db"
    if not db_path.exists():
        fallback = scenario_root / "dem.tif"
        if fallback.exists():
            return fallback
        raise FileNotFoundError(f"scenario.db not found and fallback DEM missing: {fallback}")

    query_with_id = "SELECT primary_dem_path FROM scenarios WHERE scenario_id = ? LIMIT 1"
    query_any = "SELECT primary_dem_path FROM scenarios LIMIT 1"
    dem_rel: str | None = None
    with sqlite3.connect(str(db_path)) as conn:
        if resolved_scenario_id:
            row = conn.execute(query_with_id, (resolved_scenario_id,)).fetchone()
            if row is not None and row[0]:
                dem_rel = str(row[0])
        if dem_rel is None:
            row = conn.execute(query_any).fetchone()
            if row is not None and row[0]:
                dem_rel = str(row[0])

    rel = safe_scenario_relative_path(dem_rel or "dem.tif")
    dem_path = (scenario_root / rel).resolve()
    if not dem_path.exists():
        raise FileNotFoundError(f"Primary DEM does not exist: {dem_path}")
    return dem_path


def register_output_if_available(
    *,
    relative_path: str,
    kind: str,
    subkind: str,
    render_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    try:
        register_output(
            relative_path=relative_path,
            kind=kind,
            subkind=subkind,
            render_mode=render_mode,
            metadata=metadata,
        )
        return True
    except Exception:
        # Allow direct script execution outside notebook runner context.
        return False


def replace_output_file(path: Path, *, remove_aux_xml: bool = True) -> None:
    target = Path(path)
    if target.exists() and target.is_file():
        target.unlink()
    if remove_aux_xml:
        aux = Path(str(target) + ".aux.xml")
        if aux.exists() and aux.is_file():
            aux.unlink()
