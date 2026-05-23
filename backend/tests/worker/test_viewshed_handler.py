from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from backend.api.dependencies import build_service_container
from backend.api.errors import ApiError
from backend.contracts.models import CreateScenarioRequest, JobEventName
from backend.core.config import ESRI_103878_WKT
from backend.jobs.handlers import ToolImplementations
from backend.services.assistant.tool_registry import execute_tool
from backend.worker.gdal_runtime import configure_gdal_runtime


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


def test_generate_los_viewshed_single_observer_gdal(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="viewsheda",
            name="Viewshed A",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    transform = from_origin(0.0, 10.0, 1.0, 1.0)
    data = np.array(
        [
            [10.0, 10.0, 10.0, 10.0, 10.0],
            [10.0, 12.0, 12.0, 12.0, 10.0],
            [10.0, 12.0, 14.0, 12.0, 10.0],
            [10.0, 12.0, 12.0, 12.0, 10.0],
            [10.0, 10.0, 10.0, 10.0, 10.0],
        ],
        dtype=np.float32,
    )
    _write_raster(dem, data=data, crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)

    job = services.job_service.run_typed_job(
        "generate_los_viewshed",
        {
            "scenario_id": scenario.scenario_id,
            "observer_x": 2.5,
            "observer_y": 7.5,
            "observer_height_m": 2.0,
            "backend_mode": "gdal",
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["scenario_id"] == scenario.scenario_id
    assert result["observer_input_mode"] == "single"
    assert result["backend_mode_selected"] == "gdal"
    assert int(result["observer_count"]) == 1
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        arr = ds.read(1)
        assert arr.shape == (5, 5)
        assert ds.dtypes[0] == "uint8"


def test_generate_los_viewshed_auto_falls_back_to_gdal_when_cuda_unavailable(services, monkeypatch) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="viewshedb",
            name="Viewshed B",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    mask = scenario_root / "inputs" / "observers.tif"
    transform = from_origin(0.0, 20.0, 1.0, 1.0)
    dem_data = np.ones((20, 20), dtype=np.float32) * 10.0
    mask_data = np.ones((20, 20), dtype=np.uint8)
    _write_raster(dem, data=dem_data, crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)
    _write_raster(mask, data=mask_data, crs=ESRI_103878_WKT, transform=transform, nodata=0.0)

    monkeypatch.setattr(ToolImplementations, "_cuda_available", staticmethod(lambda: (False, "test_no_cuda")))
    monkeypatch.setattr(
        ToolImplementations,
        "_load_viewshed_runtime_config",
        staticmethod(
            lambda: {
                "auto_cuda_min_observers": 64,
                "auto_cuda_min_density": 0.01,
                "auto_cuda_min_adjacency_ratio": 0.1,
                "auto_cuda_min_largest_component": 16,
            }
        ),
    )

    job = services.job_service.run_typed_job(
        "generate_los_viewshed",
        {
            "scenario_id": scenario.scenario_id,
            "observer_mask": {"relative_path": "inputs/observers.tif", "threshold": 0.5},
            "backend_mode": "auto",
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["observer_input_mode"] == "mask"
    assert result["backend_mode_requested"] == "auto"
    assert result["backend_mode_selected"] == "gdal"
    assert bool(result["backend_fallback_applied"]) is True
    assert "test_no_cuda" in str(result["backend_fallback_reason"])


def test_generate_los_viewshed_forced_cuda_unavailable_raises(services, monkeypatch) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="viewshedc",
            name="Viewshed C",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    transform = from_origin(0.0, 10.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.ones((5, 5), dtype=np.float32) * 10.0,
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )

    monkeypatch.setattr(ToolImplementations, "_cuda_available", staticmethod(lambda: (False, "forced_test_no_cuda")))

    with pytest.raises(ApiError) as exc:
        services.job_service.run_typed_job(
            "generate_los_viewshed",
            {
                "scenario_id": scenario.scenario_id,
                "observer_x": 2.5,
                "observer_y": 7.5,
                "backend_mode": "cuda",
                "mode": "immediate",
            },
        )
    assert exc.value.code == "viewshed_cuda_unavailable"


def test_generate_los_viewshed_routing_cleanup_applies_to_mask_metrics(services, monkeypatch) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="viewshedcleanup",
            name="Viewshed Cleanup",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    mask = scenario_root / "inputs" / "observers.tif"
    transform = from_origin(0.0, 7.0, 1.0, 1.0)
    dem_data = np.ones((7, 7), dtype=np.float32) * 10.0
    mask_data = np.zeros((7, 7), dtype=np.uint8)
    mask_data[2:5, 2:5] = 1  # 3x3 connected blob
    mask_data[0, 0] = 1      # isolated speckle
    mask_data[6, 6] = 1      # isolated speckle
    _write_raster(dem, data=dem_data, crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)
    _write_raster(mask, data=mask_data, crs=ESRI_103878_WKT, transform=transform, nodata=0.0)

    monkeypatch.setattr(
        ToolImplementations,
        "_load_viewshed_runtime_config",
        staticmethod(
            lambda: {
                "routing_mask_cleanup": "opening",
                "routing_cleanup_iterations": 1,
                "auto_cuda_min_observers": 10_000,
                "auto_cuda_min_density": 1.0,
                "auto_cuda_min_adjacency_ratio": 1.0,
                "auto_cuda_min_largest_component": 10_000,
                "parabolic_error_tolerance_m": 1.0,
            }
        ),
    )

    job = services.job_service.run_typed_job(
        "generate_los_viewshed",
        {
            "scenario_id": scenario.scenario_id,
            "observer_mask": {"relative_path": "inputs/observers.tif", "threshold": 0.5},
            "backend_mode": "auto",
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    metrics = result["route_metrics"]
    assert metrics["routing_cleanup_mode"] == "opening"
    assert int(metrics["routing_cleanup_iterations"]) == 1
    assert int(metrics["observer_count"]) == 11  # selection unchanged
    assert int(metrics["component_count"]) == 1  # isolated pixels removed for routing metrics
    assert int(metrics["largest_component_size"]) == 9
    assert float(metrics["adjacency_ratio"]) == pytest.approx(1.0)


def test_terrain_viewshed_tool_executes_typed_job(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="viewshedd",
            name="Viewshed D",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    transform = from_origin(0.0, 10.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.ones((5, 5), dtype=np.float32) * 10.0,
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )

    payload = execute_tool(
        services,
        tool_name="terrain.viewshed",
        arguments={
            "scenario_id": scenario.scenario_id,
            "observer_x": 2.5,
            "observer_y": 7.5,
            "backend_mode": "gdal",
            "mode": "immediate",
        },
    )
    assert payload["run_id"]
    result = payload["result"]
    assert result["scenario_id"] == scenario.scenario_id
    assert result["backend_mode_selected"] == "gdal"


def test_analyze_observer_mask_connectivity_job(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="viewshedmetricsa",
            name="Viewshed Metrics A",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    mask = scenario_root / "inputs" / "observers.tif"
    transform = from_origin(0.0, 8.0, 1.0, 1.0)
    dem_data = np.ones((8, 8), dtype=np.float32) * 10.0
    mask_data = np.zeros((8, 8), dtype=np.uint8)
    mask_data[2:4, 2:4] = 1
    mask_data[6, 6] = 1
    _write_raster(dem, data=dem_data, crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)
    _write_raster(mask, data=mask_data, crs=ESRI_103878_WKT, transform=transform, nodata=0.0)

    job = services.job_service.run_typed_job(
        "analyze_observer_mask_connectivity",
        {
            "scenario_id": scenario.scenario_id,
            "observer_mask": {"relative_path": "inputs/observers.tif", "threshold": 0.5},
            "cleanup_mode": "none",
            "cleanup_iterations": 1,
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["scenario_id"] == scenario.scenario_id
    assert int(result["observer_count"]) == 5
    assert int(result["component_count"]) == 2
    assert int(result["largest_component_size"]) == 4
    assert float(result["adjacency_ratio"]) == pytest.approx(0.8)


def test_analyze_observer_mask_connectivity_tool_executes_typed_job(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="viewshedmetricsb",
            name="Viewshed Metrics B",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    mask = scenario_root / "inputs" / "observers.tif"
    transform = from_origin(0.0, 6.0, 1.0, 1.0)
    dem_data = np.ones((6, 6), dtype=np.float32) * 10.0
    mask_data = np.zeros((6, 6), dtype=np.uint8)
    mask_data[2:5, 2:5] = 1
    _write_raster(dem, data=dem_data, crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)
    _write_raster(mask, data=mask_data, crs=ESRI_103878_WKT, transform=transform, nodata=0.0)

    payload = execute_tool(
        services,
        tool_name="terrain.mask_connectivity_metrics",
        arguments={
            "scenario_id": scenario.scenario_id,
            "observer_mask": {"relative_path": "inputs/observers.tif", "threshold": 0.5},
            "cleanup_mode": "opening",
            "cleanup_iterations": 1,
            "mode": "immediate",
        },
    )
    assert payload["run_id"]
    result = payload["result"]
    assert result["scenario_id"] == scenario.scenario_id
    assert result["cleanup_mode"] == "opening"
    assert int(result["component_count"]) == 1


def test_generate_los_viewshed_allows_explicit_paths_without_runtime_context(monkeypatch, tmp_path: Path) -> None:
    scenario_root = (tmp_path / "scenario").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem = scenario_root / "dem.tif"
    mask = scenario_root / "inputs" / "observers.tif"
    transform = from_origin(0.0, 10.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.ones((6, 6), dtype=np.float32) * 10.0,
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    mask_data = np.zeros((6, 6), dtype=np.uint8)
    mask_data[2:4, 2:4] = 1
    _write_raster(mask, data=mask_data, crs=ESRI_103878_WKT, transform=transform, nodata=0.0)

    monkeypatch.setattr(
        "backend.jobs.handlers.resolve_scenario_paths",
        lambda _scenario_id: (_ for _ in ()).throw(RuntimeError("Scenario path resolver is not configured.")),
    )
    monkeypatch.setattr(
        "backend.jobs.handlers.register_generated_raster",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Generated raster registrar is not configured.")),
    )

    result = ToolImplementations.generate_los_viewshed(
        scenario_id="scn_local_script",
        scenario_root_dir=str(scenario_root),
        dem_path=str(dem),
        observer_mask={"relative_path": "inputs/observers.tif", "threshold": 0.5},
        backend_mode="gdal",
        overwrite_mode="always",
        output_relative_path="analysis/local_viewshed.tif",
        mode="immediate",
    )
    assert result.output_relative_path == "analysis/local_viewshed.tif"
    assert result.file_id.startswith("unregistered_fil_")
    assert result.product_id.startswith("unregistered_prd_")
    assert Path(result.output_path).exists()


def test_generate_los_viewshed_auto_falls_back_when_cuda_runtime_fails(services, monkeypatch) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="viewshed_cuda_runtime_fallback",
            name="Viewshed CUDA Runtime Fallback",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    transform = from_origin(0.0, 10.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.ones((6, 6), dtype=np.float32) * 10.0,
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )

    monkeypatch.setattr(ToolImplementations, "_cuda_available", staticmethod(lambda: (True, None)))
    monkeypatch.setattr(
        ToolImplementations,
        "_select_viewshed_backend",
        staticmethod(lambda **_kwargs: ("cuda", "forced_cuda_for_test")),
    )
    monkeypatch.setattr(
        ToolImplementations,
        "_run_viewshed_cuda",
        staticmethod(lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("CUDA_ERROR_CONTEXT_IS_DESTROYED"))),
    )

    job = services.job_service.run_typed_job(
        "generate_los_viewshed",
        {
            "scenario_id": scenario.scenario_id,
            "observer_x": 2.5,
            "observer_y": 7.5,
            "observer_height_m": 1.0,
            "target_height_m": 4.0,
            "backend_mode": "auto",
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["backend_mode_requested"] == "auto"
    assert result["backend_mode_selected"] == "gdal"
    assert bool(result["backend_fallback_applied"]) is True
    assert "cuda_runtime_failed" in str(result["backend_fallback_reason"])


def test_generate_los_viewshed_auto_high_observer_no_gdal_fallback_on_cuda_failure(services, monkeypatch) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="viewshedhighcudafallback",
            name="Viewshed CUDA High Observer No Fallback",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    mask = scenario_root / "inputs" / "observers.tif"
    transform = from_origin(0.0, 10.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.ones((20, 20), dtype=np.float32) * 10.0,
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        mask,
        data=np.ones((20, 20), dtype=np.uint8),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=0.0,
    )

    monkeypatch.setattr(ToolImplementations, "_cuda_available", staticmethod(lambda: (True, None)))
    monkeypatch.setattr(
        ToolImplementations,
        "_run_viewshed_cuda",
        staticmethod(lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("CUDA_ERROR_CONTEXT_IS_DESTROYED"))),
    )

    with pytest.raises(ApiError) as exc:
        services.job_service.run_typed_job(
            "generate_los_viewshed",
            {
                "scenario_id": scenario.scenario_id,
                "observer_mask": {"relative_path": "inputs/observers.tif", "threshold": 0.5},
                "target_height_m": 4.0,
                "backend_mode": "auto",
                "mode": "immediate",
            },
        )
    assert exc.value.code == "viewshed_cuda_runtime_failed"
    assert "fallback is disabled" in str(exc.value.message).lower()
