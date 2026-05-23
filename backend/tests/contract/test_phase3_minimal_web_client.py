from __future__ import annotations

import shutil
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.dependencies import build_service_container
from backend.contracts.models import JobEventName


def _reset_services(monkeypatch, tmp_path: Path) -> None:
    import backend.api.dependencies as dependencies_module

    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    dependencies_module.SERVICES = build_service_container()


def _write_config(config_path: Path, hillshade_path: Path) -> None:
    config_path.write_text(
        "\n".join(
            [
                "[backend]",
                'workspace_root = "../workspace"',
                "",
                "[backend.lunar_analyst]",
                f'hillshade_path = "{hillshade_path.as_posix()}"',
                'scenario_root_name = "phase3-map"',
                'scenario_name = "Phase3 Map"',
                'scenario_owner = "tester"',
                'hillshade_kind = "lighting"',
                'hillshade_subkind = "hillshade"',
                "bootstrap_bypass_cog = true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _wait_for_job_events(
    client: TestClient,
    job_id: str,
    *,
    timeout_seconds: float = 10.0,
) -> list[dict]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        events = client.get(f"/api/v1/jobs/{job_id}/events")
        assert events.status_code == 200
        payload = events.json()
        names = {event.get("event_name") for event in payload}
        if {"job_progress", "job_completed"}.issubset(names):
            return payload
        time.sleep(0.05)
    raise AssertionError(f"job events did not reach completed state in time: {job_id}")


def test_phase3_bootstrap_hydrates_api_backed_layers_and_job_handlers(
    monkeypatch, tmp_path: Path
) -> None:
    _reset_services(monkeypatch, tmp_path)
    hillshade = tmp_path / "hillshade.tif"
    shutil.copy2(Path("test_data/test_hillshade_viper.tif").resolve(), hillshade)
    cfg = tmp_path / "lunar_analyst.toml"
    _write_config(cfg, hillshade)
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(cfg))

    client = TestClient(create_app())

    bootstrap = client.post("/api/v1/lunar-analyst/bootstrap")
    assert bootstrap.status_code == 200
    boot = bootstrap.json()
    scenario_id = boot["scenario_id"]
    product_id = boot["product_id"]

    layers = client.get(f"/api/v1/scenarios/{scenario_id}/layers")
    assert layers.status_code == 200
    layer_payload = layers.json()
    assert len(layer_payload) >= 1
    layer = layer_payload[0]
    assert layer["render_mode"] == "raster"

    update = client.patch(
        f"/api/v1/layers/{layer['layer_id']}",
        json={"opacity": 0.85, "style": {"brightness": 0.1, "contrast": 1.2, "colormap": "viridis"}},
    )
    assert update.status_code == 200
    updated = update.json()
    assert updated["opacity"] == 0.85
    assert updated["style"]["colormap"] == "viridis"

    files = client.get(f"/api/v1/products/{product_id}/files")
    assert files.status_code == 200
    assert len(files.json()) >= 1

    handlers = client.get("/api/v1/jobs/handlers")
    assert handlers.status_code == 200
    names = {entry["handler_name"] for entry in handlers.json()["handlers"]}
    assert "ping" in names


def test_phase3_smoke_create_scenario_launch_job_observe_events_and_layer(
    monkeypatch, tmp_path: Path
) -> None:
    _reset_services(monkeypatch, tmp_path)
    source_tif = tmp_path / "input.tif"
    shutil.copy2(Path("test_data/test_hillshade_viper.tif").resolve(), source_tif)

    client = TestClient(create_app())
    scenario = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "phase3smoke", "name": "Phase3 Smoke", "owner": "tester"},
    ).json()
    scenario_id = scenario["scenario_id"]

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
    file_id = files.json()[-1]["file_id"]

    response = client.post("/api/v1/jobs/ping", json="phase3")
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    events = _wait_for_job_events(client, job_id)
    event_names = {event["event_name"] for event in events}
    assert {"job_progress", "job_completed"}.issubset(event_names)

    layer_create = client.post(
        "/api/v1/layers",
        json={
            "scenario_id": scenario_id,
            "product_id": product_id,
            "title": "Smoke Layer",
            "visible": True,
            "opacity": 1.0,
            "z_index": 11,
            "render_mode": "raster",
            "source_file_id": file_id,
            "style": {"brightness": 0, "contrast": 1, "colormap": "gray"},
        },
    )
    assert layer_create.status_code == 200

    layers = client.get(f"/api/v1/scenarios/{scenario_id}/layers")
    assert layers.status_code == 200
    assert any(layer["title"] == "Smoke Layer" for layer in layers.json())
    assert "job_progress" in {e.value for e in JobEventName}
