from __future__ import annotations

import json
import sqlite3
import shutil
from pathlib import Path

import pytest
import rasterio
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.routers.lunar_analyst import ESRI_103878_WKT
from backend.api.dependencies import build_service_container
from backend.worker.gdal_runtime import configure_gdal_runtime


def _write_config(
    config_path: Path,
    *,
    hillshade_rel_path: str | None,
    web_mount_path: str | None = "/lunar_analyst",
    scenario_root_rel_path: str | None = None,
    colormap_app_rel_path: str | None = None,
    colormap_scenario_rel_path: str | None = None,
) -> None:
    lines: list[str] = []
    if web_mount_path is not None:
        lines.extend(
            [
                "[backend.web]",
                f'mount_path = "{web_mount_path}"',
                "",
            ]
        )
    lines.append("[backend.lunar_analyst]")
    if hillshade_rel_path is not None:
        lines.append(f'hillshade_path = "{hillshade_rel_path}"')
    if scenario_root_rel_path is not None:
        lines.append(f'scenario_root_dir = "{scenario_root_rel_path}"')
    if colormap_app_rel_path is not None:
        lines.append(f'colormap_app_path = "{colormap_app_rel_path}"')
    if colormap_scenario_rel_path is not None:
        lines.append(f'colormap_scenario_path = "{colormap_scenario_rel_path}"')
    lines.extend(
        [
            'moon_trek_capabilities_url = "https://trek.nasa.gov/tiles/Moon/SP/LRO_WAC_Mosaic_SPole60_100mp/1.0.0/WMTSCapabilities.xml"',
            'moon_trek_layer = "LRO_WAC_Mosaic_SPole60_100mp"',
            'moon_trek_tile_matrix_set = "default028mm"',
            'moon_trek_style = "default"',
        ]
    )
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _reset_services(monkeypatch, tmp_path: Path) -> None:
    import backend.api.dependencies as dependencies_module

    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    dependencies_module.SERVICES = build_service_container()


@pytest.fixture(autouse=True)
def _isolated_services(monkeypatch, tmp_path: Path) -> None:
    _reset_services(monkeypatch, tmp_path)


def test_map_milestone_config_endpoint_returns_defaults(
    monkeypatch, tmp_path: Path
) -> None:
    hillshade = tmp_path / "hillshade.tif"
    hillshade.write_bytes(b"abcd1234")
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(config_path, hillshade_rel_path="./hillshade.tif")
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))

    client = TestClient(create_app())
    response = client.get("/api/v1/lunar-analyst/config")
    assert response.status_code == 200
    payload = response.json()
    assert payload["projection"]["code"] == "ESRI:103878"
    assert payload["moon_trek"]["layer"] == "LRO_WAC_Mosaic_SPole60_100mp"
    assert payload["hillshade"]["url"] == "/api/v1/lunar-analyst/hillshade"
    assert payload["hillshade"]["native_url"] == "/api/v1/lunar-analyst/hillshade/native"
    assert payload["hillshade"]["path"] == str(hillshade.resolve())


def test_map_milestone_hillshade_native_supports_range_requests(
    monkeypatch, tmp_path: Path
) -> None:
    hillshade = tmp_path / "hillshade.tif"
    hillshade_bytes = b"0123456789"
    hillshade.write_bytes(hillshade_bytes)
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(config_path, hillshade_rel_path="./hillshade.tif")
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))

    client = TestClient(create_app())
    response = client.get(
        "/api/v1/lunar-analyst/hillshade/native",
        headers={"Range": "bytes=0-3"},
    )
    assert response.status_code == 206
    assert response.content == hillshade_bytes[:4]
    assert response.headers["accept-ranges"] == "bytes"


def test_map_milestone_missing_path_uses_error_envelope(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(config_path, hillshade_rel_path=None)
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))

    client = TestClient(create_app())
    response = client.get("/api/v1/lunar-analyst/config")
    assert response.status_code == 503
    payload = response.json()
    assert set(payload.keys()) == {"code", "message", "details", "request_id"}
    assert payload["code"] == "lunar_analyst_not_configured"


def test_map_milestone_hillshade_warps_oblique_source_to_esri_103878(
    monkeypatch, tmp_path: Path
) -> None:
    configure_gdal_runtime()
    hillshade = tmp_path / "hillshade_oblique.tif"
    scenario_root = tmp_path / "scenario_root"
    shutil.copy2(Path("test_data/test_hillshade_viper.tif").resolve(), hillshade)
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        hillshade_rel_path="./hillshade_oblique.tif",
        scenario_root_rel_path="./scenario_root",
    )
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))

    client = TestClient(create_app())
    response = client.get("/api/v1/lunar-analyst/hillshade")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/tiff"

    derivatives = list(
        (scenario_root / "display" / "lunar_analyst_hillshade" / "esri_103878").glob(
            "*.cog.tif"
        )
    )
    assert len(derivatives) == 1
    with rasterio.open(derivatives[0]) as ds:
        assert ds.crs is not None
        crs_wkt = ds.crs.to_wkt().lower()
        assert 'latitude_of_origin",-90' in crs_wkt
        assert 'central_meridian",0' in crs_wkt
        assert len(ds.overviews(1)) > 0

    db_path = scenario_root / "scenario.db"
    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT role, target_crs, source_path, derivative_path
            FROM map_display_derivative
            """
        ).fetchone()
    assert row is not None
    role, target_crs, source_path, derivative_path = row
    assert role == "display_map"
    assert target_crs == "ESRI:103878"
    assert source_path == str(hillshade.resolve())
    assert Path(derivative_path).exists()


def test_map_milestone_native_hillshade_returns_unwarped_source(
    monkeypatch, tmp_path: Path
) -> None:
    hillshade = tmp_path / "hillshade_oblique.tif"
    shutil.copy2(Path("test_data/test_hillshade_viper.tif").resolve(), hillshade)
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(config_path, hillshade_rel_path="./hillshade_oblique.tif")
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))

    client = TestClient(create_app())
    response = client.get("/api/v1/lunar-analyst/hillshade/native")
    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith('filename="hillshade_oblique.tif"')


def test_root_redirects_to_map_milestone_ui(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(config_path, hillshade_rel_path=None, web_mount_path="/lunar_analyst")
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))
    client = TestClient(create_app())
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (301, 302, 307, 308)
    assert response.headers["location"] == "/lunar_analyst/"


def test_map_milestone_route_defaults_to_react_ui(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(config_path, hillshade_rel_path=None, web_mount_path="/lunar_analyst")
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))
    client = TestClient(create_app())
    response = client.get("/lunar_analyst/")
    assert response.status_code == 200
    assert "<!doctype html>" in response.text
    assert "<title>Lunar Analyst</title>" in response.text
    assert '<div id="root"></div>' in response.text


def test_map_milestone_route_supports_react_ui_switch(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(config_path, hillshade_rel_path=None, web_mount_path="/lunar_analyst")
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))
    client = TestClient(create_app())
    response = client.get("/lunar_analyst/?ui=react")
    assert response.status_code == 200
    assert "<!doctype html>" in response.text
    assert "<title>Lunar Analyst</title>" in response.text
    assert '<div id="root"></div>' in response.text


def test_root_redirects_to_configured_mount_path(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(config_path, hillshade_rel_path=None, web_mount_path="/ui")
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))

    client = TestClient(create_app())
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (301, 302, 307, 308)
    assert response.headers["location"] == "/ui/"


def test_map_milestone_colormaps_merge_builtin_app_and_scenario(
    monkeypatch, tmp_path: Path
) -> None:
    hillshade = tmp_path / "hillshade.tif"
    hillshade.write_bytes(b"abcd1234")
    app_colormaps = tmp_path / "app_colormaps.json"
    scenario_colormaps = tmp_path / "scenario_colormaps.json"
    app_colormaps.write_text(
        """
{
  "colormaps": [
    {
      "id": "gray",
      "name": "Gray App Override",
      "stops": [
        {"value": 0.0, "color": [1, 1, 1, 1]},
        {"value": 1.0, "color": [254, 254, 254, 1]}
      ]
    },
    {
      "id": "app_only",
      "name": "App Only",
      "stops": [
        {"value": 0.0, "color": [0, 0, 0, 1]},
        {"value": 1.0, "color": [255, 0, 0, 1]}
      ]
    }
  ]
}
        """.strip(),
        encoding="utf-8",
    )
    scenario_colormaps.write_text(
        """
{
  "colormaps": [
    {
      "id": "gray",
      "name": "Gray Scenario Override",
      "stops": [
        {"value": 0.0, "color": [2, 2, 2, 1]},
        {"value": 1.0, "color": [253, 253, 253, 1]}
      ]
    },
    {
      "id": "scenario_only",
      "name": "Scenario Only",
      "stops": [
        {"value": 0.0, "color": [0, 0, 0, 1]},
        {"value": 1.0, "color": [0, 255, 0, 1]}
      ]
    }
  ]
}
        """.strip(),
        encoding="utf-8",
    )

    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        hillshade_rel_path="./hillshade.tif",
        colormap_app_rel_path="./app_colormaps.json",
        colormap_scenario_rel_path="./scenario_colormaps.json",
    )
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))

    client = TestClient(create_app())
    response = client.get("/api/v1/lunar-analyst/colormaps")
    assert response.status_code == 200
    payload = response.json()
    ids = [c["id"] for c in payload["colormaps"]]
    assert "viridis" in ids
    assert "app_only" in ids
    assert "scenario_only" in ids

    by_id = {c["id"]: c for c in payload["colormaps"]}
    assert by_id["gray"]["name"] == "Gray Scenario Override"


def test_map_milestone_raster_file_endpoint_serves_map_display_derivative(
    monkeypatch, tmp_path: Path
) -> None:
    configure_gdal_runtime()
    hillshade = tmp_path / "hillshade_oblique.tif"
    scenario_root = tmp_path / "scenario_root"
    shutil.copy2(Path("test_data/test_hillshade_viper.tif").resolve(), hillshade)
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(
        config_path,
        hillshade_rel_path="./hillshade_oblique.tif",
        scenario_root_rel_path="./scenario_root",
    )
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))

    client = TestClient(create_app())
    boot = client.post("/api/v1/lunar-analyst/bootstrap")
    assert boot.status_code == 200
    source_file_id = boot.json()["source_file_id"]

    response = client.get(f"/api/v1/lunar-analyst/files/{source_file_id}/raster")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/tiff"


def test_map_milestone_vector_file_endpoint_normalizes_geojson_to_map_crs(tmp_path: Path) -> None:
    client = TestClient(create_app())
    created = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "vector_normalize", "name": "Vector Normalize", "owner": "tester"},
    )
    assert created.status_code == 200
    scenario = created.json()
    scenario_dir = Path(scenario["directory"])

    source = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "latlon_bbox"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [0.0, -89.0],
                            [1.0, -89.0],
                            [1.0, -88.5],
                            [0.0, -88.5],
                            [0.0, -89.0],
                        ]
                    ],
                },
            }
        ],
    }
    (scenario_dir / "latlon_bbox.geojson").write_text(json.dumps(source), encoding="utf-8")

    import backend.api.dependencies as dependencies_module

    services = dependencies_module.SERVICES
    services.scenario_service.reconcile_scenario_filesystem(scenario["scenario_id"], force=True)
    file_id = next(
        file_id
        for file_id, record in services.stores.product_files.items()
        if record.scenario_id == scenario["scenario_id"] and record.relative_path == "latlon_bbox.geojson"
    )

    response = client.get(f"/api/v1/lunar-analyst/files/{file_id}/vector")
    assert response.status_code == 200
    payload = response.json()
    assert payload["crs"]["properties"]["name"] == "urn:ogc:def:crs:ESRI::103878"

    ring = payload["features"][0]["geometry"]["coordinates"][0]
    xs = [float(vertex[0]) for vertex in ring]
    ys = [float(vertex[1]) for vertex in ring]
    assert any(abs(value) > 180.0 for value in [*xs, *ys])
    assert all(abs(x) < 3040000.0 for x in xs)
    assert all(abs(y) < 3040000.0 for y in ys)


def test_map_milestone_vector_file_endpoint_supports_feature_level_crs_override(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app())
    created = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "vector_mixed_crs", "name": "Vector Mixed CRS", "owner": "tester"},
    )
    assert created.status_code == 200
    scenario = created.json()
    scenario_dir = Path(scenario["directory"])

    source = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "wgs84_default"},
                "geometry": {"type": "Point", "coordinates": [0.0, -89.0]},
            },
            {
                "type": "Feature",
                "properties": {"name": "already_map_crs", "crs": ESRI_103878_WKT},
                "geometry": {"type": "Point", "coordinates": [1234.5, -678.25]},
            },
        ],
    }
    (scenario_dir / "mixed_crs.geojson").write_text(json.dumps(source), encoding="utf-8")

    import backend.api.dependencies as dependencies_module

    services = dependencies_module.SERVICES
    services.scenario_service.reconcile_scenario_filesystem(scenario["scenario_id"], force=True)
    file_id = next(
        file_id
        for file_id, record in services.stores.product_files.items()
        if record.scenario_id == scenario["scenario_id"] and record.relative_path == "mixed_crs.geojson"
    )

    response = client.get(f"/api/v1/lunar-analyst/files/{file_id}/vector")
    assert response.status_code == 200
    payload = response.json()
    first = payload["features"][0]["geometry"]["coordinates"]
    second = payload["features"][1]["geometry"]["coordinates"]

    assert first != [0.0, -89.0]
    assert second[0] == pytest.approx(1234.5, abs=1e-6)
    assert second[1] == pytest.approx(-678.25, abs=1e-6)


def test_bootstrap_supports_scenario_id_query_override(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(config_path, hillshade_rel_path=None)
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))

    client = TestClient(create_app())
    created = client.post(
        "/api/v1/scenarios",
        json={"scenario_root": "scenario_query_override", "name": "Scenario Query", "owner": "tester"},
    )
    assert created.status_code == 200
    scenario_id = created.json()["scenario_id"]

    response = client.post(f"/api/v1/lunar-analyst/bootstrap?scenario_id={scenario_id}")
    assert response.status_code == 200
    assert response.json()["scenario_id"] == scenario_id


def test_bootstrap_rejects_unknown_scenario_id_override(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "lunar_analyst.toml"
    _write_config(config_path, hillshade_rel_path=None)
    monkeypatch.setenv("LUNAR_ANALYST_CONFIG_TOML", str(config_path))

    client = TestClient(create_app())
    response = client.post("/api/v1/lunar-analyst/bootstrap?scenario_id=scn_missing")
    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == "scenario_not_found"
