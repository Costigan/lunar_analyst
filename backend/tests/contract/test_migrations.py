from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.services.migrations import ensure_schema


def test_ensure_schema_creates_phase2_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "scenario.db"
    ensure_schema(db_path)
    assert db_path.exists()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        versions = conn.execute("SELECT version FROM schema_migrations").fetchall()

    expected = {
        "schema_migrations",
        "scenarios",
        "scenario_bootstrap_metadata",
        "products",
        "product_files",
        "jobs",
        "job_events",
        "layer_state",
        "imports",
        "exports",
    }
    assert expected.issubset(tables)
    assert (1,) in versions
    assert (2,) in versions
    assert (3,) in versions
