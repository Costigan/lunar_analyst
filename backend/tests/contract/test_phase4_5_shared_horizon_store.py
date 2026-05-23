from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import build_service_container


def _reset_services(monkeypatch, tmp_path: Path) -> None:
    import backend.api.dependencies as dependencies_module

    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    dependencies_module.SERVICES = build_service_container()


def _write_test_geotiff(dst: Path) -> Path:
    src = Path("test_data/test_hillshade_viper.tif").resolve()
    shutil.copy2(src, dst)
    return dst


def _file_id_for_product(services, product_id: str) -> str:
    return next(
        file_id
        for file_id, record in services.stores.product_files.items()
        if record.product_id == product_id
    )


def test_catalog_schema_includes_shared_horizon_tables(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase45schema", "name": "Phase 4.5 Schema", "owner": "tester"},
    )
    assert response.status_code == 200
    catalog_db = Path(tmp_path / "workspace" / "scenario_catalog.db")
    with sqlite3.connect(catalog_db) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert {"scenario_catalog", "horizon_sets", "horizon_set_refs"}.issubset(tables)


def test_shared_horizon_resolve_reuse_inspect_and_detach(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    source_tif = _write_test_geotiff(tmp_path / "shared_dem.tif")
    client = TestClient(create_app())

    scenario1 = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase45a", "name": "Phase 4.5 A", "owner": "tester"},
    ).json()
    scenario2 = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase45b", "name": "Phase 4.5 B", "owner": "tester"},
    ).json()

    imported1 = client.post(
        f"/api/v1/scenarios/{scenario1['scenario_id']}/imports/geotiff",
        json={"source_path": str(source_tif), "bypass_cog": True, "kind": "dem", "subkind": "primary"},
    ).json()
    imported2 = client.post(
        f"/api/v1/scenarios/{scenario2['scenario_id']}/imports/geotiff",
        json={"source_path": str(source_tif), "bypass_cog": True, "kind": "dem", "subkind": "primary"},
    ).json()

    import backend.api.dependencies as dependencies_module

    services = dependencies_module.SERVICES
    dem_file_id_1 = _file_id_for_product(services, imported1["product_id"])
    dem_file_id_2 = _file_id_for_product(services, imported2["product_id"])

    resolved1 = client.post(
        f"/api/v1/scenarios/{scenario1['scenario_id']}/horizon-sets:resolve",
        json={"dem_file_id": dem_file_id_1, "attach_product": True},
    )
    assert resolved1.status_code == 200
    payload1 = resolved1.json()
    assert payload1["status"] == "ready"
    assert payload1["product_id"] is not None
    assert payload1["reference_count"] == 1

    resolved2 = client.post(
        f"/api/v1/scenarios/{scenario2['scenario_id']}/horizon-sets:resolve",
        json={"dem_file_id": dem_file_id_2, "attach_product": True},
    )
    assert resolved2.status_code == 200
    payload2 = resolved2.json()
    assert payload2["horizon_key"] == payload1["horizon_key"]
    assert payload2["reference_count"] == 2

    shared_dir = Path(payload1["shared_storage_path"])
    assert shared_dir.exists()
    assert (shared_dir / "manifest.json").exists()

    inspected = client.get(f"/api/v1/horizon-sets/{payload1['horizon_key']}")
    assert inspected.status_code == 200
    inspect_payload = inspected.json()
    assert inspect_payload["status"] == "ready"
    assert inspect_payload["reference_count"] == 2
    assert inspect_payload["horizon_key"] == payload1["horizon_key"]

    detached = client.delete(
        f"/api/v1/scenarios/{scenario1['scenario_id']}/horizon-sets/{payload1['product_id']}"
    )
    assert detached.status_code == 200
    assert detached.json()["status"] == "detached"

    listed = client.get(f"/api/v1/scenarios/{scenario1['scenario_id']}/products")
    assert listed.status_code == 200
    listed_ids = {item["product_id"] for item in listed.json()}
    assert payload1["product_id"] not in listed_ids

    inspected_after = client.get(f"/api/v1/horizon-sets/{payload1['horizon_key']}")
    assert inspected_after.status_code == 200
    assert inspected_after.json()["reference_count"] == 1
