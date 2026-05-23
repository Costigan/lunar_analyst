from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_CACHE_KEY = "moon_trek_catalog_v1"
_ISO_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class TrekCatalogCacheSnapshot:
    layers: list[dict[str, Any]]
    fetched_at_utc: str
    expires_at_utc: str


class TrekCatalogCacheRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._ensure_schema()

    def load_snapshot(self, *, include_expired: bool = False) -> TrekCatalogCacheSnapshot | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT layers_json, fetched_at_utc, expires_at_utc
                FROM moon_trek_catalog_cache
                WHERE cache_key = ?
                """,
                (_CACHE_KEY,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        layers_raw = row[0]
        fetched_at_utc = str(row[1])
        expires_at_utc = str(row[2])
        try:
            parsed_layers = json.loads(layers_raw)
        except Exception:
            return None
        if not isinstance(parsed_layers, list):
            return None
        layers = [entry for entry in parsed_layers if isinstance(entry, dict)]
        if not include_expired and _is_expired(expires_at_utc):
            return None
        return TrekCatalogCacheSnapshot(
            layers=layers,
            fetched_at_utc=fetched_at_utc,
            expires_at_utc=expires_at_utc,
        )

    def save_snapshot(self, *, layers: list[dict[str, Any]], fetched_at_utc: str, ttl_seconds: int) -> None:
        expires_at_utc = _add_seconds_iso(fetched_at_utc, ttl_seconds=max(1, int(ttl_seconds)))
        now_utc = _utc_now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO moon_trek_catalog_cache(
                    cache_key, layers_json, fetched_at_utc, expires_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    layers_json = excluded.layers_json,
                    fetched_at_utc = excluded.fetched_at_utc,
                    expires_at_utc = excluded.expires_at_utc,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    _CACHE_KEY,
                    json.dumps(layers),
                    fetched_at_utc,
                    expires_at_utc,
                    now_utc,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS moon_trek_catalog_cache (
                    cache_key TEXT PRIMARY KEY,
                    layers_json TEXT NOT NULL,
                    fetched_at_utc TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_moon_trek_catalog_cache_expires
                ON moon_trek_catalog_cache (expires_at_utc);
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.commit()
        return conn


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime(_ISO_UTC_FORMAT)


def _parse_utc_iso(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value).strip(), _ISO_UTC_FORMAT).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _add_seconds_iso(value: str, *, ttl_seconds: int) -> str:
    parsed = _parse_utc_iso(value)
    if parsed is None:
        parsed = datetime.now(timezone.utc)
    return (parsed + timedelta(seconds=max(1, int(ttl_seconds)))).strftime(_ISO_UTC_FORMAT)


def _is_expired(expires_at_utc: str) -> bool:
    parsed = _parse_utc_iso(expires_at_utc)
    if parsed is None:
        return True
    return parsed <= datetime.now(timezone.utc)
