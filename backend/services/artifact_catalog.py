from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    scenario_id: str
    job_type: str
    artifact_kind: str
    artifact_path: str
    size_bytes: int
    created_at_utc: str
    metadata_json: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _db_path(scenario_root_dir: Path) -> Path:
    return scenario_root_dir / "scenario.db"


def ensure_schema(scenario_root_dir: Path) -> Path:
    scenario_root_dir.mkdir(parents=True, exist_ok=True)
    db_path = _db_path(scenario_root_dir)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_output (
                artifact_id TEXT PRIMARY KEY,
                scenario_id TEXT NOT NULL,
                job_type TEXT NOT NULL,
                artifact_kind TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at_utc TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        conn.commit()
    return db_path


def register_artifact_output(
    *,
    scenario_root_dir: Path,
    scenario_id: str,
    job_type: str,
    artifact_kind: str,
    artifact_path: Path,
    size_bytes: int,
    metadata: dict[str, object] | None = None,
) -> ArtifactRecord:
    db_path = ensure_schema(scenario_root_dir)
    payload = ArtifactRecord(
        artifact_id=str(uuid4()),
        scenario_id=scenario_id,
        job_type=job_type,
        artifact_kind=artifact_kind,
        artifact_path=str(artifact_path),
        size_bytes=int(size_bytes),
        created_at_utc=_utc_now(),
        metadata_json=json.dumps(metadata or {}, sort_keys=True),
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO artifact_output (
                artifact_id,
                scenario_id,
                job_type,
                artifact_kind,
                artifact_path,
                size_bytes,
                created_at_utc,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.artifact_id,
                payload.scenario_id,
                payload.job_type,
                payload.artifact_kind,
                payload.artifact_path,
                payload.size_bytes,
                payload.created_at_utc,
                payload.metadata_json,
            ),
        )
        conn.commit()

    return payload
