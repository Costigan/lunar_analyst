from __future__ import annotations

import sqlite3
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import build_service_container


def _reset_services(monkeypatch, tmp_path: Path) -> None:
    import backend.api.dependencies as dependencies_module

    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    dependencies_module.SERVICES = build_service_container()


def _import_product(client: TestClient, scenario_id: str, source_tif: Path) -> tuple[str, str]:
    imported = client.post(
        f"/api/v1/scenarios/{scenario_id}/imports/geotiff",
        json={
            "source_path": str(source_tif),
            "kind": "imports",
            "subkind": "geotiff",
            "bypass_cog": True,
        },
    )
    assert imported.status_code == 200
    product_id = imported.json()["product_id"]
    files = client.get(f"/api/v1/products/{product_id}/files")
    assert files.status_code == 200
    file_id = files.json()[-1]["file_id"]
    return product_id, file_id


def test_phase3_1_scenario_scoped_layers_and_events(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    source_tif = tmp_path / "input.tif"
    shutil.copy2(Path("test_data/test_hillshade_viper.tif").resolve(), source_tif)
    client = TestClient(create_app())

    scenario_a = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase31a", "name": "Phase31 A", "owner": "tester"},
    ).json()
    scenario_b = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase31b", "name": "Phase31 B", "owner": "tester"},
    ).json()

    product_a, file_a = _import_product(client, scenario_a["scenario_id"], source_tif)
    product_b, file_b = _import_product(client, scenario_b["scenario_id"], source_tif)

    layer_a = client.post(
        "/api/v1/layers",
        json={
            "scenario_id": scenario_a["scenario_id"],
            "product_id": product_a,
            "title": "Layer A",
            "visible": True,
            "opacity": 1.0,
            "z_index": 10,
            "render_mode": "raster",
            "source_file_id": file_a,
            "style": {"brightness": 0, "contrast": 1, "colormap": "gray"},
        },
    )
    assert layer_a.status_code == 200
    layer_b = client.post(
        "/api/v1/layers",
        json={
            "scenario_id": scenario_b["scenario_id"],
            "product_id": product_b,
            "title": "Layer B",
            "visible": True,
            "opacity": 1.0,
            "z_index": 10,
            "render_mode": "raster",
            "source_file_id": file_b,
            "style": {"brightness": 0, "contrast": 1, "colormap": "gray"},
        },
    )
    assert layer_b.status_code == 200

    scoped_a = client.get(f"/api/v1/scenarios/{scenario_a['scenario_id']}/layers")
    scoped_b = client.get(f"/api/v1/scenarios/{scenario_b['scenario_id']}/layers")
    assert scoped_a.status_code == 200
    assert scoped_b.status_code == 200
    assert [entry["title"] for entry in scoped_a.json()] == ["Layer A"]
    assert [entry["title"] for entry in scoped_b.json()] == ["Layer B"]

    update_b = client.patch(
        f"/api/v1/layers/{layer_b.json()['layer_id']}",
        json={"opacity": 0.62, "z_index": 50},
    )
    assert update_b.status_code == 200
    assert update_b.json()["opacity"] == 0.62

    # Scenario explorer/map sync depends on this footprint metadata.
    listed = client.get("/api/v1/scenarios")
    assert listed.status_code == 200
    by_id = {entry["scenario_id"]: entry for entry in listed.json()}
    assert by_id[scenario_a["scenario_id"]]["primary_dem_footprint"]["type"] == "Polygon"
    assert by_id[scenario_b["scenario_id"]]["primary_dem_footprint"]["type"] == "Polygon"


def test_phase3_1_ws_events_include_scenario_context(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    source_tif = tmp_path / "input.tif"
    shutil.copy2(Path("test_data/test_hillshade_viper.tif").resolve(), source_tif)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase31ws", "name": "Phase31 WS", "owner": "tester"},
    ).json()
    product_id, file_id = _import_product(client, scenario["scenario_id"], source_tif)
    layer = client.post(
        "/api/v1/layers",
        json={
            "scenario_id": scenario["scenario_id"],
            "product_id": product_id,
            "title": "WS Layer",
            "visible": True,
            "opacity": 1.0,
            "z_index": 10,
            "render_mode": "raster",
            "source_file_id": file_id,
            "style": {"brightness": 0, "contrast": 1, "colormap": "gray"},
        },
    ).json()

    patched = client.patch(
        f"/api/v1/layers/{layer['layer_id']}",
        json={"opacity": 0.77},
    )
    assert patched.status_code == 200

    import backend.api.dependencies as dependencies_module

    ws_events = dependencies_module.get_services().stores.ws_events
    assert any(
        event.get("event") == "layer_updated"
        and event.get("scenario_id") == scenario["scenario_id"]
        for event in ws_events
    )


def test_phase3_1_layer_create_rejects_cross_scenario_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _reset_services(monkeypatch, tmp_path)
    source_tif = tmp_path / "cross_input.tif"
    shutil.copy2(Path("test_data/test_hillshade_viper.tif").resolve(), source_tif)
    client = TestClient(create_app())

    scenario_a = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase31crossa", "name": "Phase31 Cross A", "owner": "tester"},
    ).json()
    scenario_b = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase31crossb", "name": "Phase31 Cross B", "owner": "tester"},
    ).json()
    product_a, file_a = _import_product(client, scenario_a["scenario_id"], source_tif)

    response = client.post(
        "/api/v1/layers",
        json={
            "scenario_id": scenario_b["scenario_id"],
            "product_id": product_a,
            "title": "Cross Scenario Layer",
            "visible": True,
            "opacity": 1.0,
            "z_index": 10,
            "render_mode": "raster",
            "source_file_id": file_a,
            "style": {"brightness": 0, "contrast": 1, "colormap": "gray"},
        },
    )
    assert response.status_code == 404


def test_phase3_1_layer_colormap_persists_across_restart(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _reset_services(monkeypatch, tmp_path)
    source_tif = tmp_path / "persist_input.tif"
    shutil.copy2(Path("test_data/test_hillshade_viper.tif").resolve(), source_tif)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase31persist", "name": "Phase31 Persist", "owner": "tester"},
    ).json()
    product_id, file_id = _import_product(client, scenario["scenario_id"], source_tif)

    created = client.post(
        "/api/v1/layers",
        json={
            "scenario_id": scenario["scenario_id"],
            "product_id": product_id,
            "title": "Persist Layer",
            "visible": True,
            "opacity": 1.0,
            "z_index": 10,
            "render_mode": "raster",
            "source_file_id": file_id,
            "style": {"brightness": 0.0, "contrast": 1.0, "colormap": "viridis"},
        },
    )
    assert created.status_code == 200

    _reset_services(monkeypatch, tmp_path)
    restarted_client = TestClient(create_app())
    listed = restarted_client.get(f"/api/v1/scenarios/{scenario['scenario_id']}/layers")
    assert listed.status_code == 200
    payload = listed.json()
    assert len(payload) == 1
    assert payload[0]["title"] == "Persist Layer"
    assert payload[0]["source_file_id"] == file_id
    assert payload[0]["style"]["colormap"] == "viridis"
    assert payload[0]["style"]["brightness"] == 0.0
    assert payload[0]["style"]["contrast"] == 1.0


def test_phase3_1_stale_layer_removed_when_source_file_deleted(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _reset_services(monkeypatch, tmp_path)
    source_tif = tmp_path / "stale_input.tif"
    shutil.copy2(Path("test_data/test_hillshade_viper.tif").resolve(), source_tif)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase31stale", "name": "Phase31 Stale", "owner": "tester"},
    ).json()
    product_id, file_id = _import_product(client, scenario["scenario_id"], source_tif)

    created = client.post(
        "/api/v1/layers",
        json={
            "scenario_id": scenario["scenario_id"],
            "product_id": product_id,
            "title": "Stale Layer",
            "visible": True,
            "opacity": 1.0,
            "z_index": 10,
            "render_mode": "raster",
            "source_file_id": file_id,
            "style": {"brightness": 0.0, "contrast": 1.0, "colormap": "viridis"},
        },
    )
    assert created.status_code == 200

    files = client.get(f"/api/v1/products/{product_id}/files")
    assert files.status_code == 200
    rel_path = files.json()[-1]["relative_path"]
    scenario_root = Path(scenario["directory"]).resolve()
    stale_source = (scenario_root / rel_path).resolve()
    stale_source.unlink()

    _reset_services(monkeypatch, tmp_path)
    restarted_client = TestClient(create_app())
    listed = restarted_client.get(f"/api/v1/scenarios/{scenario['scenario_id']}/layers")
    assert listed.status_code == 200
    assert listed.json() == []

    scenario_db = scenario_root / "scenario.db"
    with sqlite3.connect(scenario_db) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM layer_state WHERE scenario_id = ?",
            (scenario["scenario_id"],),
        ).fetchone()
    assert row is not None
    assert int(row[0]) == 0


def test_phase3_1_layer_rebinds_to_latest_product_file_when_source_file_id_is_stale(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _reset_services(monkeypatch, tmp_path)
    source_tif = tmp_path / "rebind_input.tif"
    shutil.copy2(Path("test_data/test_hillshade_viper.tif").resolve(), source_tif)
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase31rebind", "name": "Phase31 Rebind", "owner": "tester"},
    ).json()
    product_id, file_id = _import_product(client, scenario["scenario_id"], source_tif)

    created = client.post(
        "/api/v1/layers",
        json={
            "scenario_id": scenario["scenario_id"],
            "product_id": product_id,
            "title": "Rebind Layer",
            "visible": True,
            "opacity": 1.0,
            "z_index": 10,
            "render_mode": "raster",
            "source_file_id": file_id,
            "style": {"brightness": 0.0, "contrast": 1.0, "colormap": "viridis"},
        },
    )
    assert created.status_code == 200

    scenario_root = Path(scenario["directory"]).resolve()
    scenario_db = scenario_root / "scenario.db"
    rebound_file_id = "fil_rebind12345678"
    with sqlite3.connect(scenario_db) as conn:
        row = conn.execute(
            """
            SELECT product_id, scenario_id, relative_path, media_type, role
            FROM product_files
            WHERE file_id = ?
            """,
            (file_id,),
        ).fetchone()
        assert row is not None
        conn.execute("DELETE FROM product_files WHERE file_id = ?", (file_id,))
        conn.execute(
            """
            INSERT INTO product_files(
                file_id, product_id, scenario_id, relative_path, media_type, role, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rebound_file_id,
                str(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]),
                "2099-01-01T00-00-00",
            ),
        )
        conn.commit()

    _reset_services(monkeypatch, tmp_path)
    restarted_client = TestClient(create_app())
    listed = restarted_client.get(f"/api/v1/scenarios/{scenario['scenario_id']}/layers")
    assert listed.status_code == 200
    payload = listed.json()
    assert len(payload) == 1
    assert payload[0]["title"] == "Rebind Layer"
    rebound_source_file_id = str(payload[0]["source_file_id"])
    assert rebound_source_file_id != file_id

    with sqlite3.connect(scenario_db) as conn:
        rebound_row = conn.execute(
            "SELECT source_file_id FROM layer_state WHERE scenario_id = ?",
            (scenario["scenario_id"],),
        ).fetchone()
    assert rebound_row is not None
    assert str(rebound_row[0]) == rebound_source_file_id
