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


def _write_config_toml(
    config_path: Path,
    *,
    auto_discover_on_startup: bool,
    reconcile_missing_on_startup: bool = False,
) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_root = str((config_path.parent / ".." / "workspace").resolve()).replace("\\", "/")
    config_path.write_text(
        (
            "[backend]\n"
            f'workspace_root = "{workspace_root}"\n'
            "\n"
            "[backend.scenario_discovery]\n"
            f"auto_discover_on_startup = {'true' if auto_discover_on_startup else 'false'}\n"
            f"reconcile_missing_on_startup = {'true' if reconcile_missing_on_startup else 'false'}\n"
        ),
        encoding="utf-8",
    )


def _write_test_geotiff(dst: Path) -> Path:
    src = Path("test_data/test_hillshade_viper.tif").resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as src_file, dst.open("wb") as dst_file:
        shutil.copyfileobj(src_file, dst_file)
    return dst


def _write_scenario_toml(
    scenario_dir: Path,
    *,
    primary_path: str,
    surrounding_paths: list[str] | None = None,
    start_utc: str = "2026-03-01T00:00:00Z",
    stop_utc: str = "2026-03-10T00:00:00Z",
    time_step_hours: float = 1.0,
) -> None:
    surrounding = surrounding_paths or []
    def _toml_escape(value: str) -> str:
        return value.replace("\\", "\\\\")

    surrounding_toml = ", ".join(f'"{_toml_escape(item)}"' for item in surrounding)
    content = (
        "schema_version = 1\n\n"
        "[dem]\n"
        f'primary_path = "{_toml_escape(primary_path)}"\n'
        f"surrounding_paths = [{surrounding_toml}]\n\n"
        "[time_interval]\n"
        f'start_utc = "{start_utc}"\n'
        f'stop_utc = "{stop_utc}"\n'
        f"time_step_hours = {time_step_hours}\n"
    )
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "scenario.toml").write_text(content, encoding="utf-8")


def test_discover_ingests_scenario_toml_with_copy_and_persists_metadata(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    external_dem = _write_test_geotiff(tmp_path / "external_primary.tif")
    external_surround = _write_test_geotiff(tmp_path / "external_surround.tif")
    scenario_dir = workspace / "phase45tomlcopy"
    _write_scenario_toml(
        scenario_dir,
        primary_path=str(external_dem),
        surrounding_paths=[str(external_surround)],
    )

    client = TestClient(create_app())
    response = client.post("/api/v1/scenarios:discover", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ingested_count"] == 1
    assert payload["error_count"] == 0

    dem_path = scenario_dir / "dem.tif"
    assert dem_path.exists()
    assert external_dem.exists()

    scenario_id = "scn_phase45tomlcopy"
    db_path = scenario_dir / "scenario.db"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT dem_primary_original_path, dem_primary_canonical_relative_path,
                   surrounding_dem_paths_json, time_start_utc, time_stop_utc, time_step_hours
            FROM scenario_bootstrap_metadata
            WHERE scenario_id = ?
            """,
            (scenario_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == str(external_dem.resolve())
    assert row[1] == "dem.tif"
    assert "external_surround.tif" in row[2]
    assert row[3] == "2026-03-01T00:00:00Z"
    assert row[4] == "2026-03-10T00:00:00Z"
    assert row[5] == 1.0


def test_discover_renames_inside_scenario_dem_to_canonical(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "phase45tomlrename"
    original_inside = _write_test_geotiff(scenario_dir / "input_dem.tif")
    _write_scenario_toml(
        scenario_dir,
        primary_path=str(original_inside),
    )

    client = TestClient(create_app())
    response = client.post("/api/v1/scenarios:discover", json={})
    assert response.status_code == 200
    assert not original_inside.exists()
    assert (scenario_dir / "dem.tif").exists()


def test_discover_noop_when_primary_already_canonical_and_idempotent(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "phase45tomlnoop"
    canonical = _write_test_geotiff(scenario_dir / "dem.tif")
    _write_scenario_toml(
        scenario_dir,
        primary_path=str(canonical),
    )

    client = TestClient(create_app())
    first = client.post("/api/v1/scenarios:discover", json={})
    assert first.status_code == 200
    assert first.json()["ingested_count"] == 1

    second = client.post("/api/v1/scenarios:discover", json={})
    assert second.status_code == 200
    assert second.json()["skipped_count"] == 1
    assert second.json()["ingested_count"] == 0
    assert (scenario_dir / "dem.tif").exists()

    status = client.get("/api/v1/scenarios/discovery-status")
    assert status.status_code == 200
    assert status.json()["skipped_count"] == 1


def test_discover_registers_canonical_hillshade_when_file_added(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "phase45hillshade"
    canonical = _write_test_geotiff(scenario_dir / "dem.tif")
    _write_scenario_toml(scenario_dir, primary_path=str(canonical))

    client = TestClient(create_app())
    first = client.post("/api/v1/scenarios:discover", json={})
    assert first.status_code == 200
    assert first.json()["ingested_count"] == 1

    _write_test_geotiff(scenario_dir / "hillshade.tif")
    second = client.post("/api/v1/scenarios:discover", json={})
    assert second.status_code == 200
    assert second.json()["updated_count"] == 1

    products = client.get("/api/v1/scenarios/scn_phase45hillshade/products")
    assert products.status_code == 200
    product_rows = products.json()
    hillshade = [p for p in product_rows if p["kind"] == "lighting" and p["subkind"] == "hillshade"]
    assert hillshade, "Expected canonical hillshade product to be registered"


def test_discover_rebuilds_missing_scenario_db_from_filesystem(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "phase45rebuilddb"
    canonical = _write_test_geotiff(scenario_dir / "dem.tif")
    _write_scenario_toml(scenario_dir, primary_path=str(canonical))
    _write_test_geotiff(scenario_dir / "hillshade.tif")

    client = TestClient(create_app())
    first = client.post("/api/v1/scenarios:discover", json={})
    assert first.status_code == 200
    assert first.json()["ingested_count"] == 1
    db_path = scenario_dir / "scenario.db"
    assert db_path.exists()
    db_path.unlink()
    assert not db_path.exists()

    second = client.post("/api/v1/scenarios:discover", json={})
    assert second.status_code == 200
    assert second.json()["updated_count"] == 1
    assert db_path.exists()

    products = client.get("/api/v1/scenarios/scn_phase45rebuilddb/products")
    assert products.status_code == 200
    kinds = {(item["kind"], item["subkind"]) for item in products.json()}
    assert ("dem", "primary") in kinds
    assert ("lighting", "hillshade") in kinds


def test_discover_rejects_invalid_scenario_toml(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "phase45badcfg"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "scenario.toml").write_text(
        (
            "schema_version = 1\n"
            "unexpected_key = true\n"
            "[dem]\n"
            'primary_path = "missing.tif"\n'
            "[time_interval]\n"
            'start_utc = "2026-03-01T00:00:00Z"\n'
            'stop_utc = "2026-03-10T00:00:00Z"\n'
            "time_step_hours = 1\n"
        ),
        encoding="utf-8",
    )

    client = TestClient(create_app())
    response = client.post("/api/v1/scenarios:discover", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["error_count"] == 1
    assert payload["results"][0]["status"] == "error"
    assert "Unknown top-level keys" in payload["results"][0]["reason"]


def test_reingest_endpoint_updates_existing_scenario(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    external_dem = _write_test_geotiff(tmp_path / "external_reingest.tif")
    scenario_dir = workspace / "phase45reingest"
    _write_scenario_toml(scenario_dir, primary_path=str(external_dem))

    client = TestClient(create_app())
    first = client.post("/api/v1/scenarios:discover", json={})
    assert first.status_code == 200
    assert first.json()["ingested_count"] == 1

    escaped_external = str(external_dem).replace("\\", "\\\\")
    updated_toml = (
        "schema_version = 1\n\n"
        "[dem]\n"
        f'primary_path = "{escaped_external}"\n'
        "surrounding_paths = []\n\n"
        "[time_interval]\n"
        'start_utc = "2026-04-01T00:00:00Z"\n'
        'stop_utc = "2026-04-05T00:00:00Z"\n'
        "time_step_hours = 2.5\n"
    )
    (scenario_dir / "scenario.toml").write_text(updated_toml, encoding="utf-8")

    reingest = client.post(
        "/api/v1/scenarios/scn_phase45reingest:reingest",
        json={},
    )
    assert reingest.status_code == 200
    assert reingest.json()["status"] == "updated"


def test_discover_reports_error_for_missing_primary_dem(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    scenario_dir = workspace / "phase45missingdem"
    _write_scenario_toml(
        scenario_dir,
        primary_path=str(tmp_path / "does_not_exist.tif"),
    )

    client = TestClient(create_app())
    response = client.post("/api/v1/scenarios:discover", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["error_count"] == 1
    assert payload["results"][0]["status"] == "error"
    assert "primary_path does not exist" in payload["results"][0]["reason"]


def test_discover_accepts_utc_without_trailing_z(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    external_dem = _write_test_geotiff(tmp_path / "external_noz.tif")
    scenario_dir = workspace / "phase45noz"
    _write_scenario_toml(
        scenario_dir,
        primary_path=str(external_dem),
        start_utc="2026-03-01T00:00:00",
        stop_utc="2026-03-02T00:00:00",
        time_step_hours=3,
    )

    client = TestClient(create_app())
    response = client.post("/api/v1/scenarios:discover", json={})
    assert response.status_code == 200
    assert response.json()["ingested_count"] == 1


def test_discover_rejects_non_z_timezone(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    external_dem = _write_test_geotiff(tmp_path / "external_tz.tif")
    scenario_dir = workspace / "phase45badtz"
    _write_scenario_toml(
        scenario_dir,
        primary_path=str(external_dem),
        start_utc="2026-03-01T00:00:00+00:00",
        stop_utc="2026-03-02T00:00:00Z",
        time_step_hours=1,
    )

    client = TestClient(create_app())
    response = client.post("/api/v1/scenarios:discover", json={})
    assert response.status_code == 200
    assert response.json()["error_count"] == 1
    reason = response.json()["results"][0]["reason"]
    assert "timezone must be UTC" in reason


def test_startup_auto_discovery_registers_scenario_when_enabled(monkeypatch, tmp_path: Path) -> None:
    import backend.api.dependencies as dependencies_module

    workspace = tmp_path / "workspace"
    config_path = tmp_path / "cfg" / "lunar_analyst.toml"
    _write_config_toml(config_path, auto_discover_on_startup=True)
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))
    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(workspace))
    dependencies_module.SERVICES = build_service_container()

    external_dem = _write_test_geotiff(tmp_path / "external_autostart.tif")
    scenario_dir = workspace / "phase45autodiscover"
    _write_scenario_toml(
        scenario_dir,
        primary_path=str(external_dem),
    )

    with TestClient(create_app()) as client:
        listed = client.get("/api/v1/scenarios")
        assert listed.status_code == 200
        scenario_ids = {item["scenario_id"] for item in listed.json()}
        assert "scn_phase45autodiscover" in scenario_ids


def test_startup_auto_discovery_does_not_run_when_disabled(monkeypatch, tmp_path: Path) -> None:
    import backend.api.dependencies as dependencies_module

    workspace = tmp_path / "workspace"
    config_path = tmp_path / "cfg" / "lunar_analyst.toml"
    _write_config_toml(config_path, auto_discover_on_startup=False)
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))
    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(workspace))
    dependencies_module.SERVICES = build_service_container()

    external_dem = _write_test_geotiff(tmp_path / "external_manualonly.tif")
    scenario_dir = workspace / "phase45manualonly"
    _write_scenario_toml(
        scenario_dir,
        primary_path=str(external_dem),
    )

    with TestClient(create_app()) as client:
        listed = client.get("/api/v1/scenarios")
        assert listed.status_code == 200
        scenario_ids = {item["scenario_id"] for item in listed.json()}
        assert "scn_phase45manualonly" not in scenario_ids

        manual = client.post("/api/v1/scenarios:discover", json={})
        assert manual.status_code == 200
        assert manual.json()["ingested_count"] == 1


def test_delete_scenario_forgets_catalog_only_and_can_be_rediscovered(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    external_dem = _write_test_geotiff(tmp_path / "external_deleteapi.tif")
    scenario_dir = workspace / "phase45deleteapi"
    _write_scenario_toml(
        scenario_dir,
        primary_path=str(external_dem),
    )

    client = TestClient(create_app())
    discovered = client.post("/api/v1/scenarios:discover", json={})
    assert discovered.status_code == 200
    assert discovered.json()["ingested_count"] == 1

    deleted = client.delete("/api/v1/scenarios/scn_phase45deleteapi")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "forgotten"
    assert scenario_dir.exists()

    listed = client.get("/api/v1/scenarios")
    assert listed.status_code == 200
    scenario_ids = {item["scenario_id"] for item in listed.json()}
    assert "scn_phase45deleteapi" not in scenario_ids

    rediscovered = client.post("/api/v1/scenarios:discover", json={"include_existing": True})
    assert rediscovered.status_code == 200
    listed_after = client.get("/api/v1/scenarios")
    scenario_ids_after = {item["scenario_id"] for item in listed_after.json()}
    assert "scn_phase45deleteapi" in scenario_ids_after


def test_startup_reconcile_forgets_missing_scenarios_when_enabled(monkeypatch, tmp_path: Path) -> None:
    import backend.api.dependencies as dependencies_module

    workspace = tmp_path / "workspace"
    _reset_services(monkeypatch, tmp_path)
    with TestClient(create_app()) as client:
        created = client.post(
            "/api/v1/scenarios",
            json={"scenario_root": "phase45missing", "name": "Missing", "owner": "tester"},
        )
        assert created.status_code == 200
    catalog_db = workspace / "scenario_catalog.db"
    missing_dir = (workspace / "phase45missing_missing").resolve()
    with sqlite3.connect(catalog_db) as conn:
        conn.execute(
            "UPDATE scenario_catalog SET directory = ? WHERE scenario_id = ?",
            (str(missing_dir), "scn_phase45missing"),
        )
        conn.commit()

    config_path = tmp_path / "cfg" / "lunar_analyst.toml"
    _write_config_toml(
        config_path,
        auto_discover_on_startup=True,
        reconcile_missing_on_startup=True,
    )
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))
    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(workspace))
    dependencies_module.SERVICES = build_service_container()

    with TestClient(create_app()) as startup_client:
        listed = startup_client.get("/api/v1/scenarios")
        assert listed.status_code == 200
        scenario_ids = {item["scenario_id"] for item in listed.json()}
        assert "scn_phase45missing" not in scenario_ids
