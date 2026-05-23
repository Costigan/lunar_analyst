from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CLEAN_RE = re.compile(r"[^a-z0-9]+")


def clean_name(value: str) -> str:
    text = str(value or "").strip().lower()
    cleaned = _CLEAN_RE.sub(" ", text)
    return " ".join(part for part in cleaned.split() if part)


def _norm_type(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _type_clause_expr() -> str:
    # Normalize separators so type-token matching can handle values such as
    # "Mons, montes" or "Crater, craters".
    return "(' ' || lower(replace(replace(COALESCE(feature_type, ''), ',', ' '), '/', ' ')) || ' ')"


def _type_like_param(norm_type: str) -> str:
    return f"% {norm_type} %"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_nomenclature_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS lunar_features (
                feature_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                clean_name TEXT NOT NULL,
                feature_type TEXT,
                diameter_km REAL,
                importance_score REAL,
                description TEXT,
                center_x REAL,
                center_y REAL,
                min_x REAL,
                min_y REAL,
                max_x REAL,
                max_y REAL,
                origin_description TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_lunar_features_clean_name
            ON lunar_features(clean_name);

            CREATE INDEX IF NOT EXISTS idx_lunar_features_type
            ON lunar_features(feature_type);

            CREATE INDEX IF NOT EXISTS idx_lunar_features_importance
            ON lunar_features(importance_score);

            CREATE VIRTUAL TABLE IF NOT EXISTS lunar_features_fts USING fts5(
                name,
                clean_name,
                feature_type,
                content='lunar_features',
                content_rowid='feature_id'
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS lunar_features_rtree USING rtree(
                feature_id,
                min_x, max_x,
                min_y, max_y
            );

            CREATE TABLE IF NOT EXISTS nomenclature_dataset_metadata (
                dataset_key TEXT PRIMARY KEY,
                source_uri TEXT NOT NULL,
                source_revision TEXT,
                source_sha256 TEXT,
                ingested_at_utc TEXT NOT NULL
            );

            CREATE TRIGGER IF NOT EXISTS lunar_features_ai AFTER INSERT ON lunar_features BEGIN
                INSERT INTO lunar_features_fts(rowid, name, clean_name, feature_type)
                VALUES (new.feature_id, new.name, new.clean_name, COALESCE(new.feature_type, ''));
            END;

            CREATE TRIGGER IF NOT EXISTS lunar_features_ad AFTER DELETE ON lunar_features BEGIN
                INSERT INTO lunar_features_fts(lunar_features_fts, rowid, name, clean_name, feature_type)
                VALUES('delete', old.feature_id, old.name, old.clean_name, COALESCE(old.feature_type, ''));
            END;

            CREATE TRIGGER IF NOT EXISTS lunar_features_au AFTER UPDATE ON lunar_features BEGIN
                INSERT INTO lunar_features_fts(lunar_features_fts, rowid, name, clean_name, feature_type)
                VALUES('delete', old.feature_id, old.name, old.clean_name, COALESCE(old.feature_type, ''));
                INSERT INTO lunar_features_fts(rowid, name, clean_name, feature_type)
                VALUES (new.feature_id, new.name, new.clean_name, COALESCE(new.feature_type, ''));
            END;
            """
        )
        conn.commit()
    finally:
        conn.close()


def _location_payload(row: sqlite3.Row) -> dict[str, Any]:
    center_x = row["center_x"]
    center_y = row["center_y"]
    min_x = row["min_x"]
    min_y = row["min_y"]
    max_x = row["max_x"]
    max_y = row["max_y"]

    has_region = all(v is not None for v in (min_x, min_y, max_x, max_y))
    region = None
    if has_region:
        region = {
            "min_x": float(min_x),
            "min_y": float(min_y),
            "max_x": float(max_x),
            "max_y": float(max_y),
            "crs": "ESRI:103878",
        }

    center = None
    if center_x is not None and center_y is not None:
        center = {"x": float(center_x), "y": float(center_y), "crs": "ESRI:103878"}
    elif region is not None:
        center = {
            "x": (float(min_x) + float(max_x)) / 2.0,
            "y": (float(min_y) + float(max_y)) / 2.0,
            "crs": "ESRI:103878",
        }

    return {
        "kind": "region" if region is not None else "point",
        "center": center,
        "region": region,
    }


def _feature_payload(row: sqlite3.Row, *, include_distance: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "feature_id": int(row["feature_id"]),
        "name": str(row["name"]),
        "feature_type": str(row["feature_type"] or "").strip() or None,
        "location": _location_payload(row),
        "description": str(row["description"] or "").strip() or None,
        "diameter_km": float(row["diameter_km"]) if row["diameter_km"] is not None else None,
        "importance_score": float(row["importance_score"]) if row["importance_score"] is not None else 0.0,
    }
    if include_distance:
        payload["distance_m"] = float(row["distance_m"]) if row["distance_m"] is not None else None
    return payload


@dataclass(frozen=True)
class NomenclatureService:
    db_path: Path

    def __post_init__(self) -> None:
        ensure_nomenclature_schema(self.db_path)

    def resolve_exact(self, *, name: str, feature_type: str | None = None) -> dict[str, Any] | None:
        clean = clean_name(name)
        if not clean:
            return None
        norm_type = _norm_type(feature_type)
        conn = _connect(self.db_path)
        try:
            where = "clean_name = ?"
            params: list[Any] = [clean]
            if norm_type:
                where += f" AND {_type_clause_expr()} LIKE ?"
                params.append(_type_like_param(norm_type))
            row = conn.execute(
                f"""
                SELECT *
                FROM lunar_features
                WHERE {where}
                ORDER BY COALESCE(importance_score, 0) DESC, name ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
            if row is None:
                return None
            return _feature_payload(row)
        finally:
            conn.close()

    def get_feature(self, feature_id: int) -> dict[str, Any] | None:
        conn = _connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM lunar_features WHERE feature_id = ?",
                [int(feature_id)],
            ).fetchone()
            if row is None:
                return None
            return _feature_payload(row)
        finally:
            conn.close()

    def search_fuzzy(self, *, query: str, limit: int = 10, feature_type: str | None = None) -> list[dict[str, Any]]:
        clean = clean_name(query)
        if not clean:
            return []
        cap = max(1, min(200, int(limit)))
        norm_type = _norm_type(feature_type)
        conn = _connect(self.db_path)
        try:
            where = "(clean_name = ? OR clean_name LIKE ? OR clean_name LIKE ?)"
            params: list[Any] = [clean, f"{clean}%", f"%{clean}%"]
            if norm_type:
                where += f" AND {_type_clause_expr()} LIKE ?"
                params.append(_type_like_param(norm_type))
            rows = conn.execute(
                f"""
                SELECT
                    feature_id, name, feature_type, description, diameter_km, importance_score,
                    center_x, center_y, min_x, min_y, max_x, max_y,
                    CASE
                      WHEN clean_name = ? THEN 100.0
                      WHEN name = ? THEN 98.0
                      WHEN clean_name LIKE ? THEN 85.0
                      WHEN clean_name LIKE ? THEN 65.0
                      ELSE 0.0
                    END AS match_score
                FROM lunar_features
                WHERE {where}
                ORDER BY match_score DESC, COALESCE(importance_score, 0) DESC, name ASC
                LIMIT ?
                """,
                [clean, query.strip(), f"{clean}%", f"%{clean}%", *params, cap],
            ).fetchall()
            return [
                {
                    **_feature_payload(row),
                    "match_score": float(row["match_score"]),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def nearby(
        self,
        *,
        x: float,
        y: float,
        limit: int = 25,
        feature_type: str | None = None,
        radius_m: float | None = None,
    ) -> list[dict[str, Any]]:
        cap = max(1, min(500, int(limit)))
        norm_type = _norm_type(feature_type)
        conn = _connect(self.db_path)
        try:
            where = "center_x IS NOT NULL AND center_y IS NOT NULL"
            params: list[Any] = [float(x), float(y), float(x), float(y)]
            if norm_type:
                where += f" AND {_type_clause_expr()} LIKE ?"
                params.append(_type_like_param(norm_type))
            radius = float(radius_m) if radius_m is not None else None
            if radius is not None and radius > 0:
                where += " AND (((center_x - ?) * (center_x - ?)) + ((center_y - ?) * (center_y - ?))) <= (? * ?)"
                params.extend([float(x), float(x), float(y), float(y), radius, radius])
            rows = conn.execute(
                f"""
                SELECT
                    feature_id, name, feature_type, description, diameter_km, importance_score,
                    center_x, center_y, min_x, min_y, max_x, max_y,
                    sqrt(((center_x - ?) * (center_x - ?)) + ((center_y - ?) * (center_y - ?))) AS distance_m
                FROM lunar_features
                WHERE {where}
                ORDER BY distance_m ASC, COALESCE(importance_score, 0) DESC, name ASC
                LIMIT ?
                """,
                [*params, cap],
            ).fetchall()
            return [_feature_payload(row, include_distance=True) for row in rows]
        finally:
            conn.close()

    def get_features_in_extent(self, *, extent: list[float], types: list[str] | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        if len(extent) != 4:
            raise ValueError("extent must be [min_x, min_y, max_x, max_y]")
        min_x, min_y, max_x, max_y = [float(v) for v in extent]
        cap = max(1, min(5000, int(limit)))
        normalized_types = [t.strip().lower() for t in (types or []) if str(t).strip()]

        conn = _connect(self.db_path)
        try:
            where = "NOT (COALESCE(max_x, center_x) < ? OR COALESCE(min_x, center_x) > ? OR COALESCE(max_y, center_y) < ? OR COALESCE(min_y, center_y) > ?)"
            params: list[Any] = [min_x, max_x, min_y, max_y]
            if normalized_types:
                type_clauses: list[str] = []
                for item in normalized_types:
                    type_clauses.append(f"{_type_clause_expr()} LIKE ?")
                    params.append(_type_like_param(item))
                where += " AND (" + " OR ".join(type_clauses) + ")"
            rows = conn.execute(
                f"""
                SELECT *
                FROM lunar_features
                WHERE {where}
                ORDER BY COALESCE(importance_score, 0) DESC, name ASC
                LIMIT ?
                """,
                [*params, cap],
            ).fetchall()
            return [_feature_payload(row) for row in rows]
        finally:
            conn.close()
