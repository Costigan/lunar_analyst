from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from backend.api.dependencies_constants import DEFAULT_WORKSPACE_REL, WORKSPACE_ROOT_ENV
from backend.contracts.models import JobEvent, JobEventName
from backend.core.config import load_app_config as core_load_app_config
from backend.core.config import resolve_config_path as core_resolve_config_path
from backend.core.config import resolve_config_relative_path as core_resolve_config_relative_path


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def utc_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def dir_size_bytes(path: Path) -> int:
    _, total, _ = dir_tree_stats(path)
    return total


def dir_stats(path: Path) -> tuple[int, int]:
    file_count, total, _ = dir_tree_stats(path)
    return file_count, total


def dir_tree_stats(path: Path) -> tuple[int, int, str]:
    file_count = 0
    total = 0
    latest_mtime_ns = path.stat().st_mtime_ns if path.exists() else 0
    for node in path.rglob("*"):
        if node.is_file():
            stat = node.stat()
            file_count += 1
            total += stat.st_size
            if stat.st_mtime_ns > latest_mtime_ns:
                latest_mtime_ns = stat.st_mtime_ns
    latest_dt = datetime.fromtimestamp(latest_mtime_ns / 1_000_000_000, tz=timezone.utc)
    last_touched_utc = latest_dt.strftime("%Y-%m-%dT%H-%M-%S")
    return file_count, total, last_touched_utc


def ensure_within_root(root: Path, candidate: Path) -> None:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if root_resolved == candidate_resolved:
        return
    if root_resolved not in candidate_resolved.parents:
        raise PermissionError(f"Path escapes allowed root: {candidate_resolved}")


def unique_file_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for idx in range(1, 10000):
        candidate = path.with_name(f"{stem}.{idx}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Unable to allocate unique path for {path}")


def normalize_primary_dem_path(raw: str) -> str:
    if raw == "dem.tif":
        return raw
    if raw.endswith(".dem.tif") or raw.startswith("dem/"):
        return "dem.tif"
    return "dem.tif"


def normalize_relative_path(raw: str) -> str:
    rel = str(raw).strip().replace("\\", "/")
    rel = re.sub(r"/{2,}", "/", rel)
    rel = rel.lstrip("/")
    rel = rel.rstrip("/")
    if rel in {"", "."}:
        return ""
    parts = [p for p in rel.split("/") if p and p != "."]
    if any(part == ".." for part in parts):
        raise ValueError(f"Path traversal is not allowed: {raw}")
    return "/".join(parts)


def parent_relative_path(relative_path: str) -> str | None:
    rel = normalize_relative_path(relative_path)
    if not rel:
        return None
    parent = Path(rel).parent.as_posix()
    return None if parent == "." else parent


def is_hidden_default_path(relative_path: str) -> bool:
    rel = normalize_relative_path(relative_path).lower()
    if rel in {"scenario.db", "scenario.toml"}:
        return True
    name = Path(rel).name
    return rel.startswith("display/") or ".cog." in name


def is_renderable_relative_path(relative_path: str) -> bool:
    suffix = Path(relative_path).suffix.lower()
    return suffix in {".tif", ".tiff", ".geojson", ".json"}


def guess_media_type_from_path(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower()
    if suffix in {".tif", ".tiff"}:
        return "image/tiff"
    if suffix == ".geojson":
        return "application/geo+json"
    if suffix == ".json":
        return "application/json"
    return "application/octet-stream"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def preferred_repo_python() -> Path | None:
    candidate = (repo_root() / ".venv" / "bin" / "python").expanduser()
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def build_notebook_runner_env() -> dict[str, str]:
    env = os.environ.copy()
    root = repo_root().resolve()
    moonlayers_pkg_root = (root / "moonlayers_pkg").resolve()
    existing = env.get("PYTHONPATH", "").strip()
    pythonpath_entries = [str(root)]
    if moonlayers_pkg_root.exists() and moonlayers_pkg_root.is_dir():
        pythonpath_entries.append(str(moonlayers_pkg_root))
    if existing:
        pythonpath_entries.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    return env


def build_marimo_launch_env() -> dict[str, str]:
    env = os.environ.copy()
    root = repo_root().resolve()
    moonlayers_pkg_root = (root / "moonlayers_pkg").resolve()
    existing = env.get("PYTHONPATH", "").strip()
    pythonpath_entries = [str(root), str(moonlayers_pkg_root)]
    if existing:
        pythonpath_entries.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    return env


def resolve_config_path() -> Path:
    return core_resolve_config_path()


def load_app_config() -> dict[str, Any]:
    return core_load_app_config(strict=True)


def resolve_workspace_root() -> Path:
    env_override = os.getenv(WORKSPACE_ROOT_ENV)
    if env_override and env_override.strip():
        return Path(env_override).expanduser().resolve()

    config = load_app_config()
    backend_cfg = config.get("backend", {})
    if isinstance(backend_cfg, dict):
        cfg_root = backend_cfg.get("workspace_root")
        if isinstance(cfg_root, str) and cfg_root.strip():
            config_path = resolve_config_path()
            return core_resolve_config_relative_path(cfg_root, config_path=config_path)

    return Path(DEFAULT_WORKSPACE_REL).expanduser().resolve()


def load_llm_config() -> dict[str, Any]:
    config = load_app_config()
    backend_cfg = config.get("backend", {})
    if not isinstance(backend_cfg, dict):
        return {}
    llm_cfg = backend_cfg.get("llm", {})
    if not isinstance(llm_cfg, dict):
        return {}
    return dict(llm_cfg)


def resolve_action_router_spec_path(llm_cfg: dict[str, Any]) -> str | None:
    raw = llm_cfg.get("action_router_spec_path")
    if isinstance(raw, str) and raw.strip():
        config_path = resolve_config_path()
        resolved = core_resolve_config_relative_path(raw.strip(), config_path=config_path)
        return str(resolved)
    return None


def resolve_assistant_store_path(workspace_root: Path, llm_cfg: dict[str, Any]) -> Path:
    raw = llm_cfg.get("session_store_path")
    if isinstance(raw, str) and raw.strip():
        candidate = Path(raw.strip()).expanduser()
        resolved = candidate.resolve() if candidate.is_absolute() else (workspace_root / candidate).resolve()
        if workspace_root != resolved and workspace_root not in resolved.parents:
            raise PermissionError(f"Assistant session store escapes workspace root: {resolved}")
        if resolved.suffix.lower() == ".json":
            return resolved.with_suffix(".db")
        return resolved
    return (workspace_root / ".assistant" / "assistant_sessions.db").resolve()


def resolve_assistant_legacy_json_path(
    workspace_root: Path,
    llm_cfg: dict[str, Any],
    *,
    assistant_store_path: Path,
) -> Path | None:
    raw = llm_cfg.get("session_store_legacy_json_path")
    if isinstance(raw, str) and raw.strip():
        candidate = Path(raw.strip()).expanduser()
        resolved = candidate.resolve() if candidate.is_absolute() else (workspace_root / candidate).resolve()
        if workspace_root != resolved and workspace_root not in resolved.parents:
            raise PermissionError(f"Assistant legacy session store escapes workspace root: {resolved}")
        return resolved

    raw_store = llm_cfg.get("session_store_path")
    if isinstance(raw_store, str) and raw_store.strip():
        candidate = Path(raw_store.strip()).expanduser()
        configured_path = candidate.resolve() if candidate.is_absolute() else (workspace_root / candidate).resolve()
        if workspace_root != configured_path and workspace_root not in configured_path.parents:
            raise PermissionError(f"Assistant session store escapes workspace root: {configured_path}")
        if configured_path.suffix.lower() == ".json":
            return configured_path

    default_legacy = (workspace_root / ".assistant" / "assistant_sessions.json").resolve()
    if default_legacy == assistant_store_path:
        return None
    return default_legacy


def create_unique_scenario_python_file(
    scenario_root: Path,
    *,
    stem_prefix: str,
    suffix: str,
    initial_content: str,
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    counter = 0
    while True:
        extra = "" if counter == 0 else f"_{counter:02d}"
        candidate = (scenario_root / f"{stem_prefix}_{stamp}{extra}{suffix}").resolve()
        ensure_within_root(scenario_root, candidate)
        if not candidate.exists():
            candidate.write_text(initial_content, encoding="utf-8")
            return candidate
        counter += 1


def messages_log_dir(workspace_root: Path) -> Path:
    return (workspace_root / ".lunar_analyst" / "messages").resolve()


def messages_log_path(workspace_root: Path, scenario_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(scenario_id))
    path = (messages_log_dir(workspace_root) / f"{safe_id}.jsonl").resolve()
    ensure_within_root(workspace_root, path)
    return path


def append_workspace_message(
    workspace_root: Path,
    *,
    scenario_id: str,
    level: str,
    source: str,
    text: str,
) -> dict[str, Any]:
    entry = {
        "entry_id": f"msg_{uuid4().hex[:16]}",
        "scenario_id": scenario_id,
        "created_at_utc": utc_now(),
        "level": level,
        "source": source,
        "text": text,
    }
    path = messages_log_path(workspace_root, scenario_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def read_workspace_messages(workspace_root: Path, scenario_id: str) -> list[dict[str, Any]]:
    path = messages_log_path(workspace_root, scenario_id)
    if not path.exists() or not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def clear_workspace_messages(workspace_root: Path, scenario_id: str) -> None:
    path = messages_log_path(workspace_root, scenario_id)
    if path.exists():
        path.unlink()


def workspace_message_from_job_event(
    event_name: JobEventName,
    *,
    workspace_root: Path,
    scenario_id: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    job_id = str(data.get("job_id", "")).strip()
    job_label = job_id or str(data.get("job_type", "")).strip() or "job"
    percent = data.get("percent")
    message = str(data.get("message", "")).strip()
    if event_name == JobEventName.JOB_QUEUED:
        text = f"{job_label}: queued"
        level = "info"
    elif event_name == JobEventName.JOB_STARTED:
        text = f"{job_label}: started"
        level = "info"
    elif event_name == JobEventName.JOB_PROGRESS:
        if str(data.get("event_kind", "")).strip().lower() == "log_line":
            return None
        suffix = ""
        if isinstance(percent, (int, float)):
            suffix = f" ({int(round(float(percent)))}%)"
        text = f"{job_label}: {message or 'progress update'}{suffix}"
        level = "info"
    elif event_name == JobEventName.JOB_COMPLETED:
        text = f"{job_label}: completed"
        level = "success"
    elif event_name == JobEventName.JOB_FAILED:
        text = f"{job_label}: failed"
        if message:
            text = f"{text} - {message}"
        level = "error"
    elif event_name == JobEventName.JOB_CANCELLED:
        text = f"{job_label}: cancelled"
        reason = str(data.get("reason", "")).strip()
        if reason:
            text = f"{text} - {reason}"
        level = "warning"
    else:
        return None
    return append_workspace_message(
        workspace_root=workspace_root,
        scenario_id=scenario_id,
        level=level,
        source="job",
        text=text,
    )


def new_job_event(
    job_id: str,
    scenario_id: str,
    event_name: JobEventName,
    data: dict[str, Any],
) -> JobEvent:
    return JobEvent(
        event_id=str(uuid4()),
        job_id=job_id,
        scenario_id=scenario_id,
        event_name=event_name,
        timestamp_utc=utc_now(),
        data=data,
    )


def serialize_result(result: Any) -> Any:
    if isinstance(result, BaseModel):
        return result.model_dump()
    return result


def default_polygon() -> dict[str, Any]:
    return {
        "type": "Polygon",
        "coordinates": [[[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0], [-1.0, -1.0]]],
    }
