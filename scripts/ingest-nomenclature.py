#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyproj import Transformer

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.dependency_helpers import resolve_workspace_root
from backend.services.nomenclature_service import clean_name, ensure_nomenclature_schema

SOURCE_CRS = "EPSG:4326"
TARGET_CRS = "ESRI:103878"
DEFAULT_DATASET_KEY = "usgs_iau_moon"
DEFAULT_SOURCE_URI = "https://planetarynames.wr.usgs.gov/GIS_Downloads"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _f(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _s(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _xy_from_lonlat(transformer: Transformer, lon: float | None, lat: float | None) -> tuple[float | None, float | None]:
    if lon is None or lat is None:
        return None, None
    try:
        x, y = transformer.transform(lon, lat)
        return float(x), float(y)
    except Exception:
        return None, None


def _normalize_record(raw: dict[str, Any], transformer: Transformer) -> dict[str, Any] | None:
    name = _s(raw.get("name") or raw.get("feature_name") or raw.get("feature") or raw.get("NAME") or raw.get("FEATURE"))
    if not name:
        return None
    feature_type = _s(
        raw.get("feature_type")
        or raw.get("feature")
        or raw.get("feat_type")
        or raw.get("type")
        or raw.get("FEATURE_TYPE")
        or raw.get("FEATURE")
    )
    description = _s(
        raw.get("description")
        or raw.get("origin_description")
        or raw.get("origin")
        or raw.get("notes")
        or raw.get("ORIGIN")
    )

    center_x = _f(raw.get("center_x") or raw.get("x"))
    center_y = _f(raw.get("center_y") or raw.get("y"))

    lon = _f(raw.get("lon") or raw.get("longitude") or raw.get("center_lon") or raw.get("center_long"))
    lat = _f(raw.get("lat") or raw.get("latitude") or raw.get("center_lat"))

    if center_x is None or center_y is None:
        x, y = _xy_from_lonlat(transformer, lon, lat)
        if center_x is None:
            center_x = x
        if center_y is None:
            center_y = y

    min_x = _f(raw.get("min_x"))
    min_y = _f(raw.get("min_y"))
    max_x = _f(raw.get("max_x"))
    max_y = _f(raw.get("max_y"))

    if any(v is None for v in (min_x, min_y, max_x, max_y)):
        min_lon = _f(raw.get("min_lon"))
        min_lat = _f(raw.get("min_lat"))
        max_lon = _f(raw.get("max_lon"))
        max_lat = _f(raw.get("max_lat"))
        if all(v is not None for v in (min_lon, min_lat, max_lon, max_lat)):
            p1 = _xy_from_lonlat(transformer, min_lon, min_lat)
            p2 = _xy_from_lonlat(transformer, max_lon, max_lat)
            if p1[0] is not None and p2[0] is not None and p1[1] is not None and p2[1] is not None:
                min_x = min(p1[0], p2[0])
                max_x = max(p1[0], p2[0])
                min_y = min(p1[1], p2[1])
                max_y = max(p1[1], p2[1])

    if center_x is None or center_y is None:
        if all(v is not None for v in (min_x, min_y, max_x, max_y)):
            center_x = (float(min_x) + float(max_x)) / 2.0
            center_y = (float(min_y) + float(max_y)) / 2.0

    if center_x is None or center_y is None:
        return None

    diameter_km = _f(raw.get("diameter_km") or raw.get("diam_km") or raw.get("diameter") or raw.get("DIAMETER"))
    if diameter_km is None:
        diameter_km = 0.0
    importance = _f(raw.get("importance_score"))
    if importance is None:
        importance = float(diameter_km)

    return {
        "name": name,
        "clean_name": clean_name(name),
        "feature_type": feature_type,
        "diameter_km": float(diameter_km),
        "importance_score": float(importance),
        "description": description,
        "center_x": float(center_x),
        "center_y": float(center_y),
        "min_x": min_x,
        "min_y": min_y,
        "max_x": max_x,
        "max_y": max_y,
        "origin_description": _s(raw.get("origin_description")),
    }


def _iter_csv(path: Path, transformer: Transformer) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            record = _normalize_record(raw, transformer)
            if record is not None:
                rows.append(record)
    return rows


def _extract_extent_from_geometry(geometry: dict[str, Any], transformer: Transformer) -> tuple[float, float, float, float] | None:
    gtype = str(geometry.get("type", "")).strip()
    coords = geometry.get("coordinates")
    points: list[tuple[float, float]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if len(node) >= 2 and isinstance(node[0], (int, float)) and isinstance(node[1], (int, float)):
                points.append((float(node[0]), float(node[1])))
                return
            for item in node:
                _walk(item)

    if gtype in {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}:
        _walk(coords)
    if not points:
        return None
    xy: list[tuple[float, float]] = []
    for lon, lat in points:
        x, y = _xy_from_lonlat(transformer, lon, lat)
        if x is not None and y is not None:
            xy.append((x, y))
    if not xy:
        return None
    xs = [p[0] for p in xy]
    ys = [p[1] for p in xy]
    return min(xs), min(ys), max(xs), max(ys)


def _iter_geojson(path: Path, transformer: Transformer) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    feats = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(feats, list):
        return []
    rows: list[dict[str, Any]] = []
    for feat in feats:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties")
        if not isinstance(props, dict):
            props = {}
        geom = feat.get("geometry")
        if isinstance(geom, dict):
            extent = _extract_extent_from_geometry(geom, transformer)
            if extent is not None:
                props = dict(props)
                props.setdefault("min_x", extent[0])
                props.setdefault("min_y", extent[1])
                props.setdefault("max_x", extent[2])
                props.setdefault("max_y", extent[3])
        record = _normalize_record(props, transformer)
        if record is not None:
            rows.append(record)
    return rows


def ingest(
    *,
    input_path: Path,
    db_path: Path,
    dataset_key: str,
    source_uri: str,
    source_revision: str | None,
) -> tuple[int, int]:
    ensure_nomenclature_schema(db_path)
    # Lunar CRS transforms are valid here; disable PROJ's Earth/Moon guard for this ingest utility.
    os.environ.setdefault("PROJ_IGNORE_CELESTIAL_BODY", "YES")
    transformer = Transformer.from_crs(SOURCE_CRS, TARGET_CRS, always_xy=True)

    suffix = input_path.suffix.lower()
    if suffix not in {".csv", ".json", ".geojson"}:
        raise ValueError(f"Unsupported input suffix '{suffix}'. Expected one of: .csv, .json, .geojson")
    if suffix in {".json", ".geojson"}:
        rows = _iter_geojson(input_path, transformer)
    else:
        rows = _iter_csv(input_path, transformer)

    conn = sqlite3.connect(db_path)
    inserted = 0
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")

        conn.execute("DELETE FROM lunar_features")
        conn.execute("DELETE FROM lunar_features_rtree")
        conn.execute("DELETE FROM lunar_features_fts")

        for row in rows:
            cur = conn.execute(
                """
                INSERT INTO lunar_features(
                    name, clean_name, feature_type, diameter_km, importance_score, description,
                    center_x, center_y, min_x, min_y, max_x, max_y, origin_description
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["name"],
                    row["clean_name"],
                    row["feature_type"],
                    row["diameter_km"],
                    row["importance_score"],
                    row["description"],
                    row["center_x"],
                    row["center_y"],
                    row["min_x"],
                    row["min_y"],
                    row["max_x"],
                    row["max_y"],
                    row["origin_description"],
                ),
            )
            feature_id = int(cur.lastrowid)
            inserted += 1
            min_x = row["min_x"] if row["min_x"] is not None else row["center_x"]
            max_x = row["max_x"] if row["max_x"] is not None else row["center_x"]
            min_y = row["min_y"] if row["min_y"] is not None else row["center_y"]
            max_y = row["max_y"] if row["max_y"] is not None else row["center_y"]
            conn.execute(
                "INSERT INTO lunar_features_rtree(feature_id, min_x, max_x, min_y, max_y) VALUES (?, ?, ?, ?, ?)",
                (feature_id, min_x, max_x, min_y, max_y),
            )

        conn.execute("INSERT INTO lunar_features_fts(lunar_features_fts) VALUES ('rebuild')")
        conn.execute(
            """
            INSERT INTO nomenclature_dataset_metadata(
                dataset_key, source_uri, source_revision, source_sha256, ingested_at_utc
            ) VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(dataset_key) DO UPDATE SET
                source_uri = excluded.source_uri,
                source_revision = excluded.source_revision,
                source_sha256 = excluded.source_sha256,
                ingested_at_utc = excluded.ingested_at_utc
            """,
            (
                dataset_key,
                source_uri,
                source_revision,
                _sha256(input_path),
                _utc_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    skipped = 0
    return inserted, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest lunar nomenclature into <workspace_root>/scenario_catalog.db")
    parser.add_argument("input", type=Path, help="Path to source CSV or GeoJSON")
    parser.add_argument("--db", type=Path, default=None, help="Override DB path (default: <workspace_root>/scenario_catalog.db)")
    parser.add_argument("--dataset-key", default=DEFAULT_DATASET_KEY)
    parser.add_argument("--source-uri", default=DEFAULT_SOURCE_URI)
    parser.add_argument("--source-revision", default=None)
    args = parser.parse_args()

    input_path = args.input.expanduser().resolve()
    if not input_path.exists() or not input_path.is_file():
        raise SystemExit(f"Input not found: {input_path}")

    workspace_root = resolve_workspace_root()
    db_path = args.db.expanduser().resolve() if args.db else (workspace_root / "scenario_catalog.db").resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    source_uri = str(args.source_uri).strip() or str(input_path)
    inserted, skipped = ingest(
        input_path=input_path,
        db_path=db_path,
        dataset_key=str(args.dataset_key).strip() or DEFAULT_DATASET_KEY,
        source_uri=source_uri,
        source_revision=str(args.source_revision).strip() if args.source_revision else None,
    )
    print(
        "Ingested nomenclature rows into "
        f"{db_path}: inserted={inserted}, skipped={skipped}, dataset_key={str(args.dataset_key).strip() or DEFAULT_DATASET_KEY}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
