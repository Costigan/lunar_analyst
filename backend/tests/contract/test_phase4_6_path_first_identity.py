from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import build_service_container


def _reset_services(monkeypatch, tmp_path: Path) -> None:
    import backend.api.dependencies as dependencies_module

    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    dependencies_module.SERVICES = build_service_container()


def _seed_tif(dst: Path) -> Path:
    src = Path("test_data/test_hillshade_viper.tif").resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as src_file, dst.open("wb") as dst_file:
        shutil.copyfileobj(src_file, dst_file)
    return dst


def _create_seeded_scenario(client: TestClient, scenario_root: str, tmp_path: Path) -> tuple[dict, Path]:
    created = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": scenario_root, "name": f"Scenario {scenario_root}", "owner": "tester"},
    )
    assert created.status_code == 200
    scenario = created.json()
    scenario_dir = Path(scenario["directory"]).resolve()
    _seed_tif(scenario_dir / "dem.tif")
    _seed_tif(scenario_dir / "hillshade.tif")
    (scenario_dir / "scenario.toml").write_text("schema_version = 1\n", encoding="utf-8")
    _seed_tif(scenario_dir / "display" / "hillshade.cog.preview.tif")
    return scenario, scenario_dir


def _file_rows(client: TestClient, scenario_id: str) -> list[dict]:
    products = client.get(f"/api/v1/scenarios/{scenario_id}/products")
    assert products.status_code == 200
    rows: list[dict] = []
    for product in products.json():
        files = client.get(f"/api/v1/products/{product['product_id']}/files")
        assert files.status_code == 200
        for file_row in files.json():
            rows.append(file_row)
    return rows


def test_explorer_nodes_default_view_hides_system_and_display_artifacts(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    client = TestClient(create_app())
    scenario, _ = _create_seeded_scenario(client, "phase46hide", tmp_path)

    nodes = client.get(f"/api/v1/scenarios/{scenario['scenario_id']}/explorer-nodes")
    assert nodes.status_code == 200
    payload = nodes.json()
    rels = {n["relative_path"] for n in payload}
    assert "dem.tif" in rels
    assert "hillshade.tif" in rels
    assert "scenario.db" not in rels
    assert "scenario.toml" not in rels
    assert "display/hillshade.cog.preview.tif" not in rels

    by_rel = {n["relative_path"]: n for n in payload if n["relative_path"]}
    assert by_rel["dem.tif"]["node_type"] == "file"
    assert by_rel["dem.tif"]["file_id"] is not None
    assert by_rel["hillshade.tif"]["product_id"] is not None


def test_explorer_nodes_include_hidden_shows_system_and_display_artifacts(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    client = TestClient(create_app())
    scenario, _ = _create_seeded_scenario(client, "phase46show", tmp_path)

    nodes = client.get(f"/api/v1/scenarios/{scenario['scenario_id']}/explorer-nodes?include_hidden=true")
    assert nodes.status_code == 200
    rels = {n["relative_path"] for n in nodes.json()}
    assert "scenario.db" in rels
    assert "scenario.toml" in rels
    assert "display/hillshade.cog.preview.tif" in rels


def test_reconcile_detects_filesystem_rename_as_remove_and_add(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)
    client = TestClient(create_app())
    scenario, scenario_dir = _create_seeded_scenario(client, "phase46rename", tmp_path)

    before_paths = {row["relative_path"] for row in _file_rows(client, scenario["scenario_id"])}
    assert "hillshade.tif" in before_paths

    target_dir = scenario_dir / "lighting"
    target_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "hillshade.tif").replace(target_dir / "hillshade.tif")

    listed = client.get(f"/api/v1/scenarios/{scenario['scenario_id']}/products")
    assert listed.status_code == 200
    after_paths = {row["relative_path"] for row in _file_rows(client, scenario["scenario_id"])}
    assert "hillshade.tif" not in after_paths
    assert "lighting/hillshade.tif" in after_paths


def test_move_path_endpoint_moves_file_updates_records_and_emits_layer_event(monkeypatch, tmp_path: Path) -> None:
    import backend.api.dependencies as dependencies_module

    _reset_services(monkeypatch, tmp_path)
    client = TestClient(create_app())
    scenario, scenario_dir = _create_seeded_scenario(client, "phase46move", tmp_path)
    scenario_id = scenario["scenario_id"]

    files = _file_rows(client, scenario_id)
    hillshade_row = next(row for row in files if row["relative_path"] == "hillshade.tif")
    layer = client.post(
        "/api/v1/layers",
        json={
            "scenario_id": scenario_id,
            "product_id": hillshade_row["product_id"],
            "title": "Moved Hillshade",
            "visible": True,
            "opacity": 1.0,
            "z_index": 10,
            "render_mode": "raster",
            "source_file_id": hillshade_row["file_id"],
            "style": {"brightness": 0, "contrast": 1, "colormap": "gray"},
        },
    )
    assert layer.status_code == 200

    moved = client.post(
        f"/api/v1/scenarios/{scenario_id}/paths:move",
        json={"source_relative_path": "hillshade.tif", "target_relative_path": "lighting/hillshade.tif"},
    )
    assert moved.status_code == 200
    payload = moved.json()
    assert payload["status"] == "moved"
    assert payload["moved_file_count"] == 1
    assert payload["updated_layer_count"] == 1
    assert not (scenario_dir / "hillshade.tif").exists()
    assert (scenario_dir / "lighting" / "hillshade.tif").exists()

    rows = _file_rows(client, scenario_id)
    paths = {row["relative_path"] for row in rows}
    assert "lighting/hillshade.tif" in paths
    assert "hillshade.tif" not in paths

    ws_events = dependencies_module.get_services().stores.ws_events
    assert any(
        event.get("event") == "layer_updated"
        and event.get("scenario_id") == scenario_id
        and event.get("data", {}).get("reason") == "source_path_moved"
        for event in ws_events
    )


def test_move_path_rolls_back_on_persist_failure(monkeypatch, tmp_path: Path) -> None:
    import backend.api.dependencies as dependencies_module

    _reset_services(monkeypatch, tmp_path)
    client = TestClient(create_app())
    scenario, scenario_dir = _create_seeded_scenario(client, "phase46rollback", tmp_path)
    scenario_id = scenario["scenario_id"]

    (scenario_dir / "multi").mkdir(parents=True, exist_ok=True)
    _seed_tif(scenario_dir / "multi" / "a.tif")
    _seed_tif(scenario_dir / "multi" / "b.tif")
    client.get(f"/api/v1/scenarios/{scenario_id}/products")

    original_paths = sorted(row["relative_path"] for row in _file_rows(client, scenario_id))
    services = dependencies_module.get_services()
    scenario_service = services.scenario_service
    original_persist = scenario_service._persist_product_file

    calls = {"count": 0}

    def _boom(record):
        calls["count"] += 1
        if calls["count"] >= 1:
            raise RuntimeError("forced persist failure")
        return original_persist(record)

    monkeypatch.setattr(scenario_service, "_persist_product_file", _boom)

    moved = client.post(
        f"/api/v1/scenarios/{scenario_id}/paths:move",
        json={"source_relative_path": "multi", "target_relative_path": "multi_renamed"},
    )
    assert moved.status_code >= 500
    assert (scenario_dir / "multi").exists()
    assert not (scenario_dir / "multi_renamed").exists()

    current_paths = sorted(row["relative_path"] for row in _file_rows(client, scenario_id))
    assert current_paths == original_paths


def test_reconcile_handles_product_file_store_mutation_during_stale_scan(
    monkeypatch, tmp_path: Path
) -> None:
    import backend.api.dependencies as dependencies_module

    _reset_services(monkeypatch, tmp_path)
    client = TestClient(create_app())
    scenario, scenario_dir = _create_seeded_scenario(client, "phase46mutscan", tmp_path)
    scenario_id = scenario["scenario_id"]

    listed = client.get(f"/api/v1/scenarios/{scenario_id}/products")
    assert listed.status_code == 200

    services = dependencies_module.get_services()
    scenario_service = services.scenario_service
    original_ensure_within_root = dependencies_module._ensure_within_root
    injected = {"done": False}

    def _mutating_ensure_within_root(root: Path, candidate: Path) -> None:
        original_ensure_within_root(root, candidate)
        if injected["done"]:
            return
        injected["done"] = True
        stores = services.stores
        sample = next(iter(stores.product_files.values()), None)
        if sample is None:
            return
        stores.product_files["fil_injected_mid_scan"] = dependencies_module.ProductFileRecord(
            file_id="fil_injected_mid_scan",
            product_id=sample.product_id,
            scenario_id=scenario_id,
            scenario_root=scenario_dir,
            relative_path="missing_mid_scan.tif",
            media_type="image/tiff",
            role="primary",
            created_at_utc=sample.created_at_utc,
        )

    monkeypatch.setattr(
        dependencies_module, "_ensure_within_root", _mutating_ensure_within_root
    )

    scenario_service.reconcile_scenario_filesystem(scenario_id, force=True)
    assert injected["done"] is True
