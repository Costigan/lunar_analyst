from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.services.nomenclature_service import NomenclatureService, ensure_nomenclature_schema


def _seed(db_path: Path) -> None:
    ensure_nomenclature_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO lunar_features(
                feature_id, name, clean_name, feature_type, diameter_km, importance_score,
                description, center_x, center_y, min_x, min_y, max_x, max_y
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
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
        conn.execute(
            """
            INSERT INTO lunar_features(
                feature_id, name, clean_name, feature_type, diameter_km, importance_score,
                description, center_x, center_y, min_x, min_y, max_x, max_y
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                2,
                "Malapert Massif",
                "malapert massif",
                "Massif",
                5.0,
                5.0,
                "Near south pole",
                50.0,
                80.0,
                40.0,
                70.0,
                60.0,
                90.0,
            ),
        )
        conn.execute(
            "INSERT INTO lunar_features_fts(lunar_features_fts) VALUES ('rebuild')"
        )
        conn.execute(
            "INSERT INTO lunar_features_rtree(feature_id, min_x, max_x, min_y, max_y) VALUES (?, ?, ?, ?, ?)",
            (1, 10.0, 10.0, 20.0, 20.0),
        )
        conn.execute(
            "INSERT INTO lunar_features_rtree(feature_id, min_x, max_x, min_y, max_y) VALUES (?, ?, ?, ?, ?)",
            (2, 40.0, 60.0, 70.0, 90.0),
        )
        conn.commit()


def test_resolve_exact_returns_structured_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "scenario_catalog.db"
    _seed(db_path)
    svc = NomenclatureService(db_path=db_path)

    item = svc.resolve_exact(name="Shackleton")
    assert item is not None
    assert item["name"] == "Shackleton"
    assert item["feature_type"] == "Crater"
    assert item["location"]["kind"] == "point"
    assert item["location"]["center"]["crs"] == "ESRI:103878"


def test_search_fuzzy_sorts_candidates(tmp_path: Path) -> None:
    db_path = tmp_path / "scenario_catalog.db"
    _seed(db_path)
    svc = NomenclatureService(db_path=db_path)

    items = svc.search_fuzzy(query="malap", limit=10)
    assert items
    assert items[0]["name"] == "Malapert Massif"
    assert float(items[0]["match_score"]) > 0


def test_nearby_returns_distance_sorted(tmp_path: Path) -> None:
    db_path = tmp_path / "scenario_catalog.db"
    _seed(db_path)
    svc = NomenclatureService(db_path=db_path)

    items = svc.nearby(x=9.0, y=19.0, limit=10)
    assert items
    assert items[0]["name"] == "Shackleton"
    assert float(items[0]["distance_m"]) <= float(items[-1]["distance_m"])


def test_extent_query_filters_by_bbox(tmp_path: Path) -> None:
    db_path = tmp_path / "scenario_catalog.db"
    _seed(db_path)
    svc = NomenclatureService(db_path=db_path)

    items = svc.get_features_in_extent(extent=[35.0, 65.0, 65.0, 95.0], types=["massif"], limit=10)
    assert len(items) == 1
    assert items[0]["name"] == "Malapert Massif"
