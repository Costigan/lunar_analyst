from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import build_service_container
from backend.contracts.models import JobEventName


def _reset_services(monkeypatch, tmp_path: Path) -> None:
    import backend.api.dependencies as dependencies_module

    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    dependencies_module.SERVICES = build_service_container()


def _write_test_geotiff(dst: Path) -> Path:
    src = Path("test_data/test_hillshade_viper.tif").resolve()
    shutil.copy2(src, dst)
    return dst


def test_workspace_root_resolves_from_toml_when_env_not_set(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LUNAR_ANALYST_WORKSPACE_ROOT", raising=False)
    config_dir = tmp_path / "cfg"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "lunar_analyst.toml"
    config_path.write_text(
        "[backend]\nworkspace_root = \"../workspace_from_config\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))

    services = build_service_container()
    expected = (config_dir / ".." / "workspace_from_config").resolve()
    assert services.stores.workspace_root == expected
    assert (expected / "scenario_catalog.db").exists()


def test_create_scenario_import_geotiff_and_serve_file_by_id_with_range(
    monkeypatch, tmp_path: Path
) -> None:
    _reset_services(monkeypatch, tmp_path)
    source_tif = _write_test_geotiff(tmp_path / "input.tif")

    client = TestClient(create_app())
    created = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase2demo", "name": "Phase2 Demo", "owner": "tester"},
    )
    assert created.status_code == 200
    scenario_id = created.json()["scenario_id"]

    imported = client.post(
        f"/api/v1/scenarios/{scenario_id}/imports/geotiff",
        json={"source_path": str(source_tif)},
    )
    assert imported.status_code == 200
    product_id = imported.json()["product_id"]

    import backend.api.dependencies as dependencies_module

    services = dependencies_module.SERVICES
    file_id = next(fid for fid, rec in services.stores.product_files.items() if rec.product_id == product_id)
    file_response = client.get(f"/api/v1/files/{file_id}", headers={"Range": "bytes=0-31"})
    assert file_response.status_code == 206
    assert file_response.headers["accept-ranges"] == "bytes"
    assert len(file_response.content) == 32

    record = services.stores.product_files[file_id]
    path = (record.scenario_root / record.relative_path).resolve()
    assert path.name.endswith(".cog.tif")
    with sqlite3.connect(Path(created.json()["directory"]) / "scenario.db") as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert {"schema_migrations", "scenarios", "products", "product_files", "imports"}.issubset(tables)


def test_geotiff_import_bypass_cog_keeps_native_extension(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    source_tif = _write_test_geotiff(tmp_path / "input_native.tif")
    client = TestClient(create_app())
    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase2native", "name": "Phase2 Native", "owner": "tester"},
    ).json()

    response = client.post(
        f"/api/v1/scenarios/{scenario['scenario_id']}/imports/geotiff",
        json={"source_path": str(source_tif), "bypass_cog": True},
    )
    assert response.status_code == 200

    import backend.api.dependencies as dependencies_module

    services = dependencies_module.SERVICES
    product_id = response.json()["product_id"]
    file_id = next(fid for fid, rec in services.stores.product_files.items() if rec.product_id == product_id)
    record = services.stores.product_files[file_id]
    assert record.relative_path.endswith(".native.tif")


def test_files_endpoint_rejects_out_of_root_registered_path(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    client = TestClient(create_app(), raise_server_exceptions=False)
    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase2safe", "name": "Phase2 Safe", "owner": "tester"},
    ).json()

    import backend.api.dependencies as dependencies_module

    services = dependencies_module.SERVICES
    scenario_root = Path(scenario["directory"]).resolve()
    fid = "fil_badpath"
    services.stores.product_files[fid] = dependencies_module.ProductFileRecord(
        file_id=fid,
        product_id="prd_x",
        scenario_id=scenario["scenario_id"],
        scenario_root=scenario_root,
        relative_path="../../outside.tif",
        media_type="image/tiff",
        role="primary",
        created_at_utc="2026-01-01T00-00-00",
    )

    response = client.get(f"/api/v1/files/{fid}")
    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == "internal_error"


def test_websocket_events_stream_job_and_layer_events(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    source_tif = _write_test_geotiff(tmp_path / "input_ws.tif")
    client = TestClient(create_app())
    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase2ws", "name": "Phase2 WS", "owner": "tester"},
    ).json()
    imported = client.post(
        f"/api/v1/scenarios/{scenario['scenario_id']}/imports/geotiff",
        json={"source_path": str(source_tif)},
    ).json()

    import backend.api.dependencies as dependencies_module

    services = dependencies_module.SERVICES
    product_id = imported["product_id"]
    file_id = next(fid for fid, rec in services.stores.product_files.items() if rec.product_id == product_id)

    with client.websocket_connect("/api/v1/events") as ws:
        layer = client.post(
            "/api/v1/layers",
            json={
                "scenario_id": scenario["scenario_id"],
                "title": "Imported Raster",
                "render_mode": "raster",
                "source_file_id": file_id,
                "opacity": 0.9,
                "z_index": 1,
                "visible": True,
            },
        )
        assert layer.status_code == 200

        client.post("/api/v1/jobs/ping", json="hello")

        first = ws.receive_json()
        second = ws.receive_json()
        assert first["schema_version"] == "1.0"
        assert first["event"] in {e.value for e in JobEventName}
        assert second["schema_version"] == "1.0"
        assert second["event"] in {e.value for e in JobEventName}


def test_id_path_accessor_resolves_ids_and_paths(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    source_tif = _write_test_geotiff(tmp_path / "input_ids.tif")
    client = TestClient(create_app())

    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase2ids", "name": "Phase2 IDs", "owner": "tester"},
    ).json()
    imported = client.post(
        f"/api/v1/scenarios/{scenario['scenario_id']}/imports/geotiff",
        json={"source_path": str(source_tif)},
    ).json()

    import backend.api.dependencies as dependencies_module

    services = dependencies_module.SERVICES
    accessor = services.id_path_accessor
    product_id = imported["product_id"]
    file_id = accessor.latest_file_id_for_product(product_id)

    file_path = accessor.file_path_from_id(file_id)
    assert accessor.file_id_from_path(file_path) == file_id
    assert accessor.product_id_from_file_id(file_id) == product_id
    assert accessor.scenario_id_from_file_id(file_id) == scenario["scenario_id"]
    assert accessor.scenario_root_from_id(scenario["scenario_id"]) == Path(
        scenario["directory"]
    ).resolve()
    assert accessor.scenario_id_from_root(scenario["directory"]) == scenario["scenario_id"]
