from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NotebookJobMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    notebook_path: str = Field(min_length=1)
    description: str = ""
    visibility: str = "default"
    tags: list[str] = Field(default_factory=list)
    params_schema: dict[str, Any] = Field(default_factory=dict)
    outputs_schema: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveredNotebookJob:
    metadata: NotebookJobMetadata
    definition_path: Path
    notebook_path: Path
    notebook_hash: str


def resolve_notebook_job_roots(
    *,
    config: dict[str, Any],
    config_path: Path,
    workspace_root: Path,
) -> list[Path]:
    backend_cfg = config.get("backend", {})
    notebook_jobs_cfg = (
        backend_cfg.get("notebook_jobs", {})
        if isinstance(backend_cfg, dict)
        else {}
    )
    roots_raw = (
        notebook_jobs_cfg.get("search_roots", [])
        if isinstance(notebook_jobs_cfg, dict)
        else []
    )
    roots: list[Path] = []
    if isinstance(roots_raw, list):
        for value in roots_raw:
            if not isinstance(value, str) or not value.strip():
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = (config_path.parent / candidate).resolve()
            else:
                candidate = candidate.resolve()
            roots.append(candidate)
    if not roots:
        roots = [(workspace_root / "notebook_jobs").resolve()]
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def discover_notebook_jobs(roots: list[Path]) -> list[DiscoveredNotebookJob]:
    found: list[DiscoveredNotebookJob] = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for definition in sorted(root.rglob("*.job.json")):
            payload = json.loads(definition.read_text(encoding="utf-8"))
            metadata = NotebookJobMetadata.model_validate(payload)
            notebook_path = (definition.parent / metadata.notebook_path).resolve()
            _ensure_within_root(root.resolve(), notebook_path)
            if not notebook_path.exists() or not notebook_path.is_file():
                raise FileNotFoundError(f"Notebook job script missing: {notebook_path}")
            notebook_hash = hashlib.sha256(notebook_path.read_bytes()).hexdigest()
            found.append(
                DiscoveredNotebookJob(
                    metadata=metadata,
                    definition_path=definition.resolve(),
                    notebook_path=notebook_path,
                    notebook_hash=notebook_hash,
                )
            )

    found.sort(key=lambda item: (item.metadata.job_id, str(item.notebook_path)))
    return found


def _ensure_within_root(root: Path, candidate: Path) -> None:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if root_resolved == candidate_resolved:
        return
    if root_resolved not in candidate_resolved.parents:
        raise PermissionError(f"Path escapes notebook job root: {candidate_resolved}")
