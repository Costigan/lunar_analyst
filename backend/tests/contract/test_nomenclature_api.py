from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.dependencies import build_service_container
from backend.services.nomenclature_service import ensure_nomenclature_schema


def _reset_services(monkeypatch, tmp_path: Path) -> Path:
    import backend.api.dependencies as dependencies_module

    workspace = tmp_path / "workspace"
    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(workspace))
    dependencies_module.SERVICES = build_service_container()
    return workspace / "scenario_catalog.db"


def _seed(db_path: Path) -> None:
    ensure_nomenclature_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO lunar_features(
                name, clean_name, feature_type, diameter_km, importance_score, description,
                center_x, center_y, min_x, min_y, max_x, max_y
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Shackleton",
                "shackleton",
                "Crater",
                21.0,
                21.0,
                "South-pole crater",
                10.0,
                20.0,
                None,
                None,
                None,
                None,
            ),
        )
        feature_id = int(conn.execute("SELECT feature_id FROM lunar_features WHERE name='Shackleton'").fetchone()[0])
        conn.execute(
            "INSERT INTO lunar_features_rtree(feature_id, min_x, max_x, min_y, max_y) VALUES (?, ?, ?, ?, ?)",
            (feature_id, 10.0, 10.0, 20.0, 20.0),
        )
        conn.execute("INSERT INTO lunar_features_fts(lunar_features_fts) VALUES ('rebuild')")
        conn.commit()


def test_nomenclature_search_resolve_and_nearby(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LUNAR_ANALYST_SKIP_IMPORT_TIME_NATIVE_PREFLIGHT", "1")
    from backend.api.app import create_app

    db_path = _reset_services(monkeypatch, tmp_path)
    _seed(db_path)

    client = TestClient(create_app())

    search = client.get("/api/v1/nomenclature/search", params={"query": "Shack"})
    assert search.status_code == 200
    payload = search.json()
    assert payload["count"] >= 1

    resolved = client.get("/api/v1/nomenclature/resolve", params={"name": "Shackleton"})
    assert resolved.status_code == 200
    assert resolved.json()["name"] == "Shackleton"

    nearby = client.get("/api/v1/nomenclature/nearby", params={"x": 11.0, "y": 19.0})
    assert nearby.status_code == 200
    nearby_payload = nearby.json()
    assert nearby_payload["count"] >= 1
    assert nearby_payload["items"][0]["name"] == "Shackleton"


def test_nomenclature_resolve_404(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LUNAR_ANALYST_SKIP_IMPORT_TIME_NATIVE_PREFLIGHT", "1")
    from backend.api.app import create_app

    db_path = _reset_services(monkeypatch, tmp_path)
    _seed(db_path)
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/v1/nomenclature/resolve", params={"name": "NotAFeature"})
    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == "nomenclature_not_found"
    assert payload.get("details", {}).get("name") == "NotAFeature"
