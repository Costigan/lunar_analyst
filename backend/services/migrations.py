from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


SCHEMA_VERSION = 3
@contextmanager
def _connect_sqlite(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at_utc TEXT NOT NULL
        )
        """
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _apply_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scenarios (
            scenario_id TEXT PRIMARY KEY,
            scenario_root TEXT NOT NULL,
            name TEXT NOT NULL,
            owner TEXT NOT NULL,
            directory TEXT NOT NULL,
            primary_dem_path TEXT NOT NULL,
            primary_dem_crs TEXT NOT NULL,
            primary_dem_footprint TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            last_touched_utc TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS products (
            product_id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            subkind TEXT NOT NULL,
            producer TEXT NOT NULL,
            crs TEXT NOT NULL,
            footprint TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            lineage TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS product_files (
            file_id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL,
            scenario_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            media_type TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            job_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            params TEXT NOT NULL,
            requested_at_utc TEXT NOT NULL,
            started_at_utc TEXT,
            finished_at_utc TEXT,
            updated_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS job_events (
            event_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            scenario_id TEXT NOT NULL,
            event_name TEXT NOT NULL,
            timestamp_utc TEXT NOT NULL,
            data TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS layer_state (
            layer_id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            product_id TEXT,
            title TEXT NOT NULL,
            visible INTEGER NOT NULL,
            opacity REAL NOT NULL,
            z_index INTEGER NOT NULL,
            render_mode TEXT NOT NULL,
            source_file_id TEXT NOT NULL,
            style TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS imports (
            import_id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            source_path TEXT NOT NULL,
            output_file_id TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS exports (
            export_id TEXT PRIMARY KEY,
            scenario_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            destination_path TEXT NOT NULL,
            created_at_utc TEXT NOT NULL
        );
        """
    )


def _apply_v2(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scenario_bootstrap_metadata (
            scenario_id TEXT PRIMARY KEY,
            config_rel_path TEXT NOT NULL,
            config_sha256 TEXT NOT NULL,
            dem_primary_original_path TEXT NOT NULL,
            dem_primary_canonical_relative_path TEXT NOT NULL,
            surrounding_dem_paths_json TEXT NOT NULL,
            time_start_utc TEXT NOT NULL,
            time_end_utc TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );
        """
    )


def _apply_v3(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "scenario_bootstrap_metadata"):
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(scenario_bootstrap_metadata)").fetchall()
        }
        if "time_stop_utc" not in cols:
            conn.execute(
                "ALTER TABLE scenario_bootstrap_metadata ADD COLUMN time_stop_utc TEXT"
            )
            conn.execute(
                "UPDATE scenario_bootstrap_metadata SET time_stop_utc = time_end_utc WHERE time_stop_utc IS NULL"
            )
        if "time_step_hours" not in cols:
            conn.execute(
                "ALTER TABLE scenario_bootstrap_metadata ADD COLUMN time_step_hours REAL"
            )
            conn.execute(
                "UPDATE scenario_bootstrap_metadata SET time_step_hours = 1.0 WHERE time_step_hours IS NULL"
            )


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect_sqlite(db_path) as conn:
        _ensure_version_table(conn)
        applied = {
            int(row[0]) for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        if 1 not in applied:
            _apply_v1(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at_utc) VALUES (1, strftime('%Y-%m-%dT%H-%M-%S', 'now'))"
            )
        if 2 not in applied:
            _apply_v2(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at_utc) VALUES (2, strftime('%Y-%m-%dT%H-%M-%S', 'now'))"
            )
        if 3 not in applied:
            _apply_v3(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at_utc) VALUES (3, strftime('%Y-%m-%dT%H-%M-%S', 'now'))"
            )
        conn.commit()

        # Backfill for existing databases created before migrations were introduced.
        if not _table_exists(conn, "schema_migrations"):
            _ensure_version_table(conn)
            conn.commit()

