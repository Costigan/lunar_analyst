from __future__ import annotations

import types
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from backend.api.dependencies import build_service_container
from backend.api.errors import ApiError
from backend.contracts.models import CreateScenarioRequest, JobEventName, Producer, RegisterProductRequest
from backend.core.config import ESRI_103878_WKT
from backend.services.assistant.tool_registry import execute_tool
from backend.worker.gdal_runtime import configure_gdal_runtime


LUNAR_LONG_LAT_PROJ4 = "+proj=longlat +R=1737400 +no_defs"


def _write_raster(
    path: Path,
    *,
    data: np.ndarray,
    crs: str,
    transform,
    nodata: float | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=int(data.shape[1]),
        height=int(data.shape[0]),
        count=1,
        dtype=str(data.dtype),
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as ds:
        ds.write(data, 1)


@pytest.fixture
def services(monkeypatch, tmp_path: Path):
    import backend.api.dependencies as dependencies_module

    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str((tmp_path / "workspace").resolve()))
    monkeypatch.delenv("LUNAR_ANALYST_CONFIG_TOML", raising=False)
    dependencies_module.SERVICES = None
    configure_gdal_runtime()
    built = build_service_container()
    try:
        yield built
    finally:
        built.job_service.shutdown()
        built.notebook_job_service.terminate_all_running(reason="test shutdown")
        built.marimo_service.stop_if_running()


def _extract_result(services, job_id: str) -> dict[str, object]:
    events = services.job_service.list_job_events(job_id)
    for event in reversed(events):
        if event.event_name == JobEventName.JOB_COMPLETED:
            payload = event.data.get("result", {})
            return payload if isinstance(payload, dict) else {}
    return {}


def test_raster_calculate_writes_registered_output(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalgebraa",
            name="Map Algebra A",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    a_path = scenario_root / "inputs" / "a.tif"
    b_path = scenario_root / "inputs" / "b.tif"
    transform = from_origin(-1.0, 1.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.array([[10.0, 12.0], [11.0, 9.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        a_path,
        data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        b_path,
        data=np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )

    job = services.job_service.run_typed_job(
        "raster_calculate",
        {
            "scenario_id": scenario.scenario_id,
            "expression": "where(a > 2, a + b, b)",
            "inputs": {
                "a": {"relative_path": "inputs/a.tif"},
                "b": {"relative_path": "inputs/b.tif"},
            },
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["scenario_id"] == scenario.scenario_id
    assert result["output_dtype"] == "float32"
    assert result["file_id"]
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    assert file_path.exists()
    with rasterio.open(file_path) as ds:
        values = ds.read(1)
        assert values.shape == (2, 2)
        assert float(values[0, 0]) == pytest.approx(5.0)
        assert float(values[1, 0]) == pytest.approx(10.0)


def test_raster_calculate_can_publish_visible_layer(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalgebra_publish_layer",
            name="Map Algebra Publish Layer",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    slope_path = scenario_root / "slope.tif"
    transform = from_origin(-1.0, 1.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.array([[10.0, 12.0], [11.0, 9.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        slope_path,
        data=np.array([[2.0, 7.0], [4.5, 8.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )

    job = services.job_service.run_typed_job(
        "raster_calculate",
        {
            "scenario_id": scenario.scenario_id,
            "expression": "slope <= 5",
            "inputs": {
                "slope": {"relative_path": "slope.tif"},
            },
            "output_relative_path": "landing_sites3.tif",
            "mode": "immediate",
            "publish_layer": {
                "enabled": True,
                "title": "landing_sites3",
                "visible": True,
                "opacity": 0.9,
                "on_existing": "update",
                "transparent_background": True,
            },
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["published_layer_id"]
    assert result["published_layer_title"] == "landing_sites3"
    assert result["published_layer_visible"] is True
    assert result["output_dtype"] == "uint8"
    assert result["output_nodata"] == 0.0

    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        assert ds.nodata == 0.0
        values = ds.read(1)
        assert values.tolist() == [[1, 0], [1, 0]]

    layers = services.layer_service.list_layers(scenario.scenario_id)
    published = [layer for layer in layers if layer.layer_id == result["published_layer_id"]]
    assert len(published) == 1
    assert published[0].visible is True
    assert published[0].title == "landing_sites3"
    assert published[0].source_file_id == result["file_id"]


def test_raster_calculate_mask_publish_without_transparent_background_keeps_no_nodata(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalgebra_pub_no_trans_bg",
            name="Map Algebra Publish Layer No Transparent BG",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    slope_path = scenario_root / "slope.tif"
    transform = from_origin(-1.0, 1.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.array([[10.0, 12.0], [11.0, 9.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        slope_path,
        data=np.array([[2.0, 7.0], [4.5, 8.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )

    job = services.job_service.run_typed_job(
        "raster_calculate",
        {
            "scenario_id": scenario.scenario_id,
            "expression": "slope <= 5",
            "inputs": {
                "slope": {"relative_path": "slope.tif"},
            },
            "output_relative_path": "landing_sites3.tif",
            "mode": "immediate",
            "publish_layer": {
                "enabled": True,
                "title": "landing_sites3",
                "visible": True,
                "opacity": 0.9,
                "on_existing": "update",
            },
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["output_dtype"] == "uint8"
    assert result["output_nodata"] is None

    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        assert ds.nodata is None
        values = ds.read(1)
        assert values.tolist() == [[1, 0], [1, 0]]


def test_raster_calculate_supports_product_id_inputs_and_reprojection(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalgebrab",
            name="Map Algebra B",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    a_path = scenario_root / "inputs" / "a.tif"
    b_path = scenario_root / "inputs" / "b_webmerc.tif"
    dem_transform = from_origin(0.0, 2000.0, 1000.0, 1000.0)
    longlat_transform = from_origin(-0.1, 0.1, 0.1, 0.1)
    _write_raster(
        dem,
        data=np.array([[100.0, 101.0], [102.0, 103.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=dem_transform,
        nodata=-9999.0,
    )
    _write_raster(
        a_path,
        data=np.array([[1.0, 1.0], [1.0, 1.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=dem_transform,
        nodata=-9999.0,
    )
    _write_raster(
        b_path,
        data=np.array([[2.0, 2.0], [2.0, 2.0]], dtype=np.float32),
        crs=LUNAR_LONG_LAT_PROJ4,
        transform=longlat_transform,
        nodata=-9999.0,
    )

    product = services.scenario_service._register_product_internal(
        RegisterProductRequest(
            scenario_id=scenario.scenario_id,
            kind="analysis",
            subkind="input",
            producer=Producer.MANUAL,
            crs=LUNAR_LONG_LAT_PROJ4,
            footprint=scenario.primary_dem_footprint,
            lineage={"source": "test"},
        )
    )
    services.scenario_service._register_file(
        product_id=product.product_id,
        scenario_id=scenario.scenario_id,
        scenario_root=scenario_root,
        relative_path="inputs/b_webmerc.tif",
        media_type="image/tiff",
        role="primary",
    )

    job = services.job_service.run_typed_job(
        "raster_calculate",
        {
            "scenario_id": scenario.scenario_id,
            "expression": "a + b",
            "inputs": {
                "a": {"relative_path": "inputs/a.tif"},
                "b": {"product_id": product.product_id},
            },
            "output_relative_path": "outputs/reproj_sum.tif",
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["output_relative_path"] == "outputs/reproj_sum.tif"
    assert "b" in result["reprojected_inputs"]
    lineage = services.product_service.get_product(str(result["product_id"])).lineage
    assert lineage["source"] == "map_algebra"


def test_raster_calculate_label_regions_generates_int32_labels(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalgebra_label_regions",
            name="Map Algebra Label Regions",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    mask_path = scenario_root / "inputs" / "mask.tif"
    transform = from_origin(0.0, 3.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.ones((3, 4), dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        mask_path,
        data=np.array(
            [
                [1, 1, 0, 0],
                [0, 1, 0, 1],
                [0, 0, 0, 1],
            ],
            dtype=np.uint8,
        ),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=0.0,
    )

    job = services.job_service.run_typed_job(
        "raster_calculate",
        {
            "scenario_id": scenario.scenario_id,
            "expression": "label_regions(mask > 0)",
            "inputs": {"mask": {"relative_path": "inputs/mask.tif"}},
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["output_dtype"] == "int32"
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        arr = ds.read(1)
        assert ds.dtypes[0] == "int32"
        assert arr.tolist() == [
            [1, 1, 0, 0],
            [0, 1, 0, 2],
            [0, 0, 0, 2],
        ]


def test_raster_calculate_find_borders_tool_path(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalgebra_find_borders",
            name="Map Algebra Find Borders",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    mask_path = scenario_root / "inputs" / "mask.tif"
    transform = from_origin(0.0, 5.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.ones((5, 5), dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        mask_path,
        data=np.array(
            [
                [0, 0, 0, 0, 0],
                [0, 1, 1, 1, 0],
                [0, 1, 1, 1, 0],
                [0, 1, 1, 1, 0],
                [0, 0, 0, 0, 0],
            ],
            dtype=np.uint8,
        ),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=0.0,
    )

    payload = execute_tool(
        services,
        tool_name="raster.calculate",
        arguments={
            "scenario_id": scenario.scenario_id,
            "expression": "find_borders(mask > 0)",
            "inputs": {"mask": {"relative_path": "inputs/mask.tif"}},
            "mode": "immediate",
        },
    )
    result = payload["result"]
    assert result["output_dtype"] == "uint8"
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        arr = ds.read(1)
        assert arr.tolist() == [
            [0, 0, 0, 0, 0],
            [0, 1, 1, 1, 0],
            [0, 1, 0, 1, 0],
            [0, 1, 1, 1, 0],
            [0, 0, 0, 0, 0],
        ]


def test_raster_calculate_label_regions_with_cleanup_breaks_bridge(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalgebra_label_regions_cleanup",
            name="Map Algebra Label Regions Cleanup",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    mask_path = scenario_root / "inputs" / "mask.tif"
    transform = from_origin(0.0, 7.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.ones((7, 13), dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        mask_path,
        data=np.array(
            [
                [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0],
                [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            ],
            dtype=np.uint8,
        ),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=0.0,
    )

    job = services.job_service.run_typed_job(
        "raster_calculate",
        {
            "scenario_id": scenario.scenario_id,
            "expression": 'label_regions(mask > 0, "erosion", 1)',
            "inputs": {"mask": {"relative_path": "inputs/mask.tif"}},
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        arr = ds.read(1)
        assert ds.dtypes[0] == "int32"
        assert int(arr.max()) == 2
        assert int(arr[3, 3]) != int(arr[3, 9])


def test_raster_calculate_region_sizes_generates_size_raster(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalgebra_region_sizes",
            name="Map Algebra Region Sizes",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    mask_path = scenario_root / "inputs" / "mask.tif"
    transform = from_origin(0.0, 3.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.ones((3, 4), dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        mask_path,
        data=np.array(
            [
                [1, 1, 0, 0],
                [0, 1, 0, 1],
                [0, 0, 0, 1],
            ],
            dtype=np.uint8,
        ),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=0.0,
    )

    job = services.job_service.run_typed_job(
        "raster_calculate",
        {
            "scenario_id": scenario.scenario_id,
            "expression": "region_sizes(mask > 0)",
            "inputs": {"mask": {"relative_path": "inputs/mask.tif"}},
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["output_dtype"] == "int32"
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        arr = ds.read(1)
        assert ds.dtypes[0] == "int32"
        assert arr.tolist() == [
            [3, 3, 0, 0],
            [0, 3, 0, 2],
            [0, 0, 0, 2],
        ]


def test_raster_calculate_filter_regions_by_size_preserves_shape_after_cleanup(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalg_filter_regions_shape",
            name="Map Algebra Filter Regions Preserve Shape",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    mask_path = scenario_root / "inputs" / "mask.tif"
    transform = from_origin(0.0, 7.0, 1.0, 1.0)
    mask_data = np.array(
        [
            [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
            [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
            [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0],
            [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
            [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
            [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        ],
        dtype=np.uint8,
    )
    _write_raster(
        dem,
        data=np.ones((7, 13), dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        mask_path,
        data=mask_data,
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=0.0,
    )

    job = services.job_service.run_typed_job(
        "raster_calculate",
        {
            "scenario_id": scenario.scenario_id,
            "expression": 'filter_regions_by_size(mask > 0, 9, ">=", "erosion", 1)',
            "inputs": {"mask": {"relative_path": "inputs/mask.tif"}},
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["output_dtype"] == "uint8"
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        arr = ds.read(1)
        # Bridge is restored because kept regions are projected back to original labels.
        assert int(arr[3, 6]) == 1
        assert int(arr[1, 1]) == 1
        assert int(arr[1, 11]) == 1


def test_raster_calculate_returns_output_exists_error_code(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalgebrac",
            name="Map Algebra C",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    a_path = scenario_root / "inputs" / "a.tif"
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.array([[1.0, 1.0], [1.0, 1.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        a_path,
        data=np.array([[2.0, 3.0], [4.0, 5.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    first = services.job_service.run_typed_job(
        "raster_calculate",
        {
            "scenario_id": scenario.scenario_id,
            "expression": "a * 2",
            "inputs": {"a": {"relative_path": "inputs/a.tif"}},
            "output_relative_path": "outputs/existing.tif",
            "mode": "immediate",
        },
    )
    assert first.status.value == "completed"

    with pytest.raises(ApiError) as exc:
        services.job_service.run_typed_job(
            "raster_calculate",
            {
                "scenario_id": scenario.scenario_id,
                "expression": "a * 3",
                "inputs": {"a": {"relative_path": "inputs/a.tif"}},
                "output_relative_path": "outputs/existing.tif",
                "overwrite_mode": "never",
                "mode": "immediate",
            },
        )
    assert exc.value.code == "map_algebra_output_exists"


def test_raster_calculate_default_overwrite_mode_requires_confirmation(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalgebrae",
            name="Map Algebra E",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    a_path = scenario_root / "inputs" / "a.tif"
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.array([[1.0, 1.0], [1.0, 1.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        a_path,
        data=np.array([[2.0, 3.0], [4.0, 5.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    first = services.job_service.run_typed_job(
        "raster_calculate",
        {
            "scenario_id": scenario.scenario_id,
            "expression": "a * 2",
            "inputs": {"a": {"relative_path": "inputs/a.tif"}},
            "output_relative_path": "outputs/existing_ask.tif",
            "overwrite_mode": "always",
            "mode": "immediate",
        },
    )
    assert first.status.value == "completed"

    with pytest.raises(ApiError) as exc:
        services.job_service.run_typed_job(
            "raster_calculate",
            {
                "scenario_id": scenario.scenario_id,
                "expression": "a * 3",
                "inputs": {"a": {"relative_path": "inputs/a.tif"}},
                "output_relative_path": "outputs/existing_ask.tif",
                "mode": "immediate",
            },
        )
    assert exc.value.code == "map_algebra_overwrite_confirmation_required"


def test_raster_calculate_tool_executes_typed_job(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalgebrad",
            name="Map Algebra D",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    a_path = scenario_root / "inputs" / "a.tif"
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        a_path,
        data=np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    payload = execute_tool(
        services,
        tool_name="raster.calculate",
        arguments={
            "scenario_id": scenario.scenario_id,
            "expression": "a + 1",
            "inputs": {"a": {"relative_path": "inputs/a.tif"}},
            "mode": "immediate",
        },
    )
    assert payload["run_id"]
    result = payload["result"]
    assert result["scenario_id"] == scenario.scenario_id
    assert result["output_dtype"] == "float32"


def test_raster_calculate_supports_temporal_signal_inputs_with_reducer(services, monkeypatch) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalgebrae",
            name="Map Algebra E",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    horizons = scenario_root / "lighting" / "horizons"
    horizons.mkdir(parents=True, exist_ok=True)
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            return

    chunk0 = np.array(
        [
            [[[10.0, 20.0], [30.0, 40.0]]],
            [[[20.0, 30.0], [40.0, 50.0]]],
        ],
        dtype=np.float32,
    )
    chunk1 = np.array(
        [
            [[[30.0, 40.0], [50.0, 60.0]]],
        ],
        dtype=np.float32,
    )
    meta0 = types.SimpleNamespace(
        patch_row=0,
        patch_col=0,
        width=2,
        height=2,
        rank=4,
        time_offset=0,
        time_count=2,
    )
    meta1 = types.SimpleNamespace(
        patch_row=0,
        patch_col=0,
        width=2,
        height=2,
        rank=4,
        time_offset=2,
        time_count=1,
    )

    import backend.jobs.handlers as handlers_module

    monkeypatch.setattr(handlers_module, "LightmapStreamingClient", _FakeClient)
    monkeypatch.setattr(
        handlers_module,
        "stream_tiles_v2",
        lambda *_args, **_kwargs: iter([(meta0, chunk0), (meta1, chunk1)]),
    )

    job = services.job_service.run_typed_job(
        "raster_calculate",
        {
            "scenario_id": scenario.scenario_id,
            "expression": "avg(light) / 10",
            "inputs": {"light": {"signal": "lighting_raster"}},
            "time_start_utc": "2027-01-01T00:00:00Z",
            "time_stop_utc": "2027-01-01T02:00:00Z",
            "time_step_hours": 1.0,
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["scenario_id"] == scenario.scenario_id
    assert result["output_dtype"] == "float32"
    assert result["temporal_inputs"] == ["light"]
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        arr = ds.read(1)
        assert arr.shape == (2, 2)
        assert float(arr[0, 0]) == pytest.approx(2.0)
        assert float(arr[1, 1]) == pytest.approx(5.0)


def test_raster_calculate_requires_temporal_reducer_for_multi_time_output(services, monkeypatch) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="mapalgebraf",
            name="Map Algebra F",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    horizons = scenario_root / "lighting" / "horizons"
    horizons.mkdir(parents=True, exist_ok=True)
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            return

    chunk = np.array(
        [
            [[[10.0, 20.0], [30.0, 40.0]]],
            [[[20.0, 30.0], [40.0, 50.0]]],
        ],
        dtype=np.float32,
    )
    meta = types.SimpleNamespace(
        patch_row=0,
        patch_col=0,
        width=2,
        height=2,
        rank=4,
        time_offset=0,
        time_count=2,
    )

    import backend.jobs.handlers as handlers_module

    monkeypatch.setattr(handlers_module, "LightmapStreamingClient", _FakeClient)
    monkeypatch.setattr(
        handlers_module,
        "stream_tiles_v2",
        lambda *_args, **_kwargs: iter([(meta, chunk)]),
    )

    with pytest.raises(ApiError) as exc:
        services.job_service.run_typed_job(
            "raster_calculate",
            {
                "scenario_id": scenario.scenario_id,
                "expression": "light > 0",
                "inputs": {"light": {"signal": "lighting_raster"}},
                "time_start_utc": "2027-01-01T00:00:00Z",
                "time_stop_utc": "2027-01-01T01:00:00Z",
                "time_step_hours": 1.0,
                "mode": "immediate",
            },
        )
    assert exc.value.code == "map_algebra_temporal_reduce_required"
