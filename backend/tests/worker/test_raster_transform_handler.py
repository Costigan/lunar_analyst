from __future__ import annotations

import types
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
from backend.jobs import raster_transform as raster_transform_module
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


def test_raster_transform_writes_registered_output(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="rastertransforma",
            name="Raster Transform A",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    a_path = scenario_root / "inputs" / "a.tif"
    b_path = scenario_root / "inputs" / "b.tif"
    transform = from_origin(-1.0, 1.0, 1.0, 1.0)
    _write_raster(dem, data=np.array([[10.0, 12.0], [11.0, 9.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)
    _write_raster(a_path, data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)
    _write_raster(b_path, data=np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)

    job = services.job_service.run_typed_job(
        "raster_transform",
        {
            "scenario_id": scenario.scenario_id,
            "script": "tmp = a + b\nresult = where(tmp > 9, tmp, b)",
            "inputs": {
                "a": {"relative_path": "inputs/a.tif"},
                "b": {"relative_path": "inputs/b.tif"},
            },
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["scenario_id"] == scenario.scenario_id
    assert result["file_id"]
    assert result["planner_summary"]["execution_strategy"] == "full_extent_static"
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        values = ds.read(1)
        assert float(values[0, 0]) == pytest.approx(5.0)
        assert float(values[1, 0]) == pytest.approx(10.0)


def test_raster_transform_tool_executes_typed_job(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="rastertransformb",
            name="Raster Transform B",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    a_path = scenario_root / "inputs" / "a.tif"
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(dem, data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)
    _write_raster(a_path, data=np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)

    payload = execute_tool(
        services,
        tool_name="raster.transform",
        arguments={
            "scenario_id": scenario.scenario_id,
            "script": "result = a + 1",
            "inputs": {"a": {"relative_path": "inputs/a.tif"}},
            "mode": "immediate",
        },
    )
    assert payload["run_id"]
    result = payload["result"]
    assert result["scenario_id"] == scenario.scenario_id
    assert result["output_dtype"] == "float32"


def test_raster_transform_overwrite_mode_ask_requires_confirmation(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="rastertransform_overwrite_ask",
            name="Raster Transform Overwrite Ask",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(
        scenario_root / "dem.tif",
        data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        scenario_root / "inputs" / "a.tif",
        data=np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        scenario_root / "nd_index.tif",
        data=np.array([[0.0, 0.0], [0.0, 0.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )

    with pytest.raises(ApiError) as exc:
        services.job_service.run_typed_job(
            "raster_transform",
            {
                "scenario_id": scenario.scenario_id,
                "script": "result = a + 1",
                "inputs": {"a": {"relative_path": "inputs/a.tif"}},
                "output_relative_path": "nd_index.tif",
                "overwrite_mode": "ask",
                "mode": "immediate",
            },
        )
    assert exc.value.code == "map_algebra_overwrite_confirmation_required"


def test_raster_transform_legacy_overwrite_alias_still_supported(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="rastertransform_overwrite_alias",
            name="Raster Transform Overwrite Alias",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(
        scenario_root / "dem.tif",
        data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        scenario_root / "inputs" / "a.tif",
        data=np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        scenario_root / "nd_index.tif",
        data=np.array([[0.0, 0.0], [0.0, 0.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )

    job = services.job_service.run_typed_job(
        "raster_transform",
        {
            "scenario_id": scenario.scenario_id,
            "script": "result = a + 1",
            "inputs": {"a": {"relative_path": "inputs/a.tif"}},
            "output_relative_path": "nd_index.tif",
            "overwrite": True,
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        values = ds.read(1)
    assert float(values[0, 0]) == pytest.approx(6.0)


def test_raster_transform_prefilter_validator_reports_parse_failure_stage(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="rastertransform_prefilter_a",
            name="Raster Transform Prefilter A",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    a_path = scenario_root / "inputs" / "a.tif"
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(dem, data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)
    _write_raster(a_path, data=np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)

    prefilter = ToolImplementations._raster_transform_prefilter_validate(
        arguments={
            "scenario_id": scenario.scenario_id,
            "script": "result = (a > 0) and (a < 10)",
            "inputs": {"a": {"relative_path": "inputs/a.tif"}},
        }
    )
    assert prefilter["eligible"] is False
    assert prefilter["failure_stage"] == "parse_validate"
    error = prefilter.get("error", {})
    assert isinstance(error, dict)
    assert error.get("code") == "raster_transform_disallowed_syntax"


def test_raster_transform_prefilter_validator_reports_estimate_resources_stage(services, monkeypatch) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="rastertransform_prefilter_b",
            name="Raster Transform Prefilter B",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    a_path = scenario_root / "inputs" / "a.tif"
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(dem, data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)
    _write_raster(a_path, data=np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)

    monkeypatch.setattr(
        raster_transform_module,
        "load_app_config",
        lambda strict=False: {
            "backend": {
                "raster_transform": {
                    "max_estimated_working_set_bytes": 1,
                    "max_temporal_full_extent_bytes": 1,
                    "max_tiled_temporal_working_set_bytes": 1,
                }
            }
        },
    )

    prefilter = ToolImplementations._raster_transform_prefilter_validate(
        arguments={
            "scenario_id": scenario.scenario_id,
            "script": "result = a + 1",
            "inputs": {"a": {"relative_path": "inputs/a.tif"}},
        }
    )
    assert prefilter["eligible"] is False
    assert prefilter["failure_stage"] == "estimate_resources"
    error = prefilter.get("error", {})
    assert isinstance(error, dict)
    assert error.get("code") == "raster_transform_plan_too_large"


def test_raster_transform_supports_temporal_signal_inputs_tiled(services, monkeypatch) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="rastertransformc",
            name="Raster Transform C",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    horizons_dir.mkdir(parents=True, exist_ok=True)
    (horizons_dir / "horizon_00000_00000_000.bin").write_bytes(b"x")
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(dem, data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            return

    chunk0 = np.array([[[[10.0, 20.0], [30.0, 40.0]]], [[[20.0, 30.0], [40.0, 50.0]]]], dtype=np.float32)
    chunk1 = np.array([[[[30.0, 40.0], [50.0, 60.0]]]], dtype=np.float32)
    meta0 = types.SimpleNamespace(patch_row=0, patch_col=0, width=2, height=2, rank=4, time_offset=0, time_count=2)
    meta1 = types.SimpleNamespace(patch_row=0, patch_col=0, width=2, height=2, rank=4, time_offset=2, time_count=1)

    import backend.jobs.handlers as handlers_module

    monkeypatch.setattr(handlers_module, "LightmapStreamingClient", _FakeClient)
    monkeypatch.setattr(
        handlers_module,
        "stream_tiles_v2",
        lambda *_args, **_kwargs: iter([(meta0, chunk0), (meta1, chunk1)]),
    )

    job = services.job_service.run_typed_job(
        "raster_transform",
        {
            "scenario_id": scenario.scenario_id,
            "script": "tmp = avg(light)\nresult = tmp / 10",
            "inputs": {
                "times": {
                    "kind": "times",
                    "start_utc": "2027-01-01T00:00:00Z",
                    "stop_utc": "2027-01-01T02:00:00Z",
                    "step_hours": 1.0,
                },
                "light": {"temporal_source": "sun_fraction", "times": "times"},
            },
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["planner_summary"]["execution_strategy"] == "tiled_temporal"
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        arr = ds.read(1)
        assert float(arr[0, 0]) == pytest.approx(2.0)
        assert float(arr[1, 1]) == pytest.approx(5.0)


def test_raster_transform_time_domain_mismatch_returns_repair_hint(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="rastertransform_time_mismatch",
            name="Raster Transform Time Mismatch",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    horizons_dir.mkdir(parents=True, exist_ok=True)
    (horizons_dir / "horizon_00000_00000_000.bin").write_bytes(b"x")
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(dem, data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)

    with pytest.raises(ApiError) as exc:
        services.job_service.run_typed_job(
            "raster_transform",
            {
                "scenario_id": scenario.scenario_id,
                "script": "result = avg(light)",
                "inputs": {
                    "times": {
                        "kind": "times",
                        "start_utc": "2027-01-01T00:00:00Z",
                        "stop_utc": "2027-01-01T02:00:00Z",
                        "step_hours": 1.0,
                    },
                    "light": {"temporal_source": "sun_fraction", "times": "times"},
                },
                "time_start_utc": "2027-01-01T00:00:00Z",
                "time_stop_utc": "2027-01-01T03:00:00Z",
                "time_step_hours": 1.0,
                "mode": "immediate",
            },
        )
    assert exc.value.code == "raster_transform_invalid_argument"
    details = exc.value.details if isinstance(exc.value.details, dict) else {}
    assert "hint" in details


def test_raster_transform_supports_temporal_signal_inputs_full_extent(services, monkeypatch) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="rastertransformd",
            name="Raster Transform D",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    horizons_dir.mkdir(parents=True, exist_ok=True)
    (horizons_dir / "horizon_00000_00000_000.bin").write_bytes(b"x")
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(dem, data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            return

    chunk0 = np.array([[[[10.0, 20.0], [30.0, 40.0]]], [[[20.0, 30.0], [40.0, 50.0]]]], dtype=np.float32)
    chunk1 = np.array([[[[30.0, 40.0], [50.0, 60.0]]]], dtype=np.float32)
    meta0 = types.SimpleNamespace(patch_row=0, patch_col=0, width=2, height=2, rank=4, time_offset=0, time_count=2)
    meta1 = types.SimpleNamespace(patch_row=0, patch_col=0, width=2, height=2, rank=4, time_offset=2, time_count=1)

    import backend.jobs.handlers as handlers_module

    monkeypatch.setattr(handlers_module, "LightmapStreamingClient", _FakeClient)
    monkeypatch.setattr(
        handlers_module,
        "stream_tiles_v2",
        lambda *_args, **_kwargs: iter([(meta0, chunk0), (meta1, chunk1)]),
    )

    job = services.job_service.run_typed_job(
        "raster_transform",
        {
            "scenario_id": scenario.scenario_id,
            "script": "tmp = avg(light)\nresult = tmp / 10",
            "inputs": {
                "times": {
                    "kind": "times",
                    "start_utc": "2027-01-01T00:00:00Z",
                    "stop_utc": "2027-01-01T02:00:00Z",
                    "step_hours": 1.0,
                },
                "light": {"temporal_source": "sun_fraction", "times": "times"},
            },
            "spatial_partitioning": "forbidden",
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["planner_summary"]["execution_strategy"] == "full_extent_temporal"
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        arr = ds.read(1)
        assert float(arr[0, 0]) == pytest.approx(2.0)
        assert float(arr[1, 1]) == pytest.approx(5.0)


def test_raster_transform_rejects_oversized_plan(services, monkeypatch) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="rastertransforme",
            name="Raster Transform E",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    horizons_dir.mkdir(parents=True, exist_ok=True)
    (horizons_dir / "horizon_00000_00000_000.bin").write_bytes(b"x")
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(dem, data=np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)

    monkeypatch.setattr(
        raster_transform_module,
        "load_app_config",
        lambda strict=False: {
            "backend": {
                "raster_transform": {
                    "max_estimated_working_set_bytes": 1,
                    "max_temporal_full_extent_bytes": 1,
                    "max_tiled_temporal_working_set_bytes": 1,
                }
            }
        },
    )

    with pytest.raises(ApiError) as exc:
        services.job_service.run_typed_job(
            "raster_transform",
            {
                "scenario_id": scenario.scenario_id,
                "script": "result = avg(light)",
                "inputs": {
                    "times": {
                        "kind": "times",
                        "start_utc": "2027-01-01T00:00:00Z",
                        "stop_utc": "2027-01-01T02:00:00Z",
                        "step_hours": 1.0,
                    },
                    "light": {"temporal_source": "sun_fraction", "times": "times"},
                },
                "spatial_partitioning": "forbidden",
                "mode": "immediate",
            },
        )
    assert exc.value.code == "raster_transform_plan_too_large"


def test_raster_transform_requires_temporal_reducer_for_multi_time_output(
    services, monkeypatch
) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="rastertransformf",
            name="Raster Transform F",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    horizons_dir.mkdir(parents=True, exist_ok=True)
    (horizons_dir / "horizon_00000_00000_000.bin").write_bytes(b"x")
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
        [[[[10.0, 20.0], [30.0, 40.0]]], [[[20.0, 30.0], [40.0, 50.0]]]],
        dtype=np.float32,
    )
    chunk1 = np.array([[[[30.0, 40.0], [50.0, 60.0]]]], dtype=np.float32)
    meta0 = types.SimpleNamespace(
        patch_row=0, patch_col=0, width=2, height=2, rank=4, time_offset=0, time_count=2
    )
    meta1 = types.SimpleNamespace(
        patch_row=0, patch_col=0, width=2, height=2, rank=4, time_offset=2, time_count=1
    )

    import backend.jobs.handlers as handlers_module

    monkeypatch.setattr(handlers_module, "LightmapStreamingClient", _FakeClient)
    monkeypatch.setattr(
        handlers_module,
        "stream_tiles_v2",
        lambda *_args, **_kwargs: iter([(meta0, chunk0), (meta1, chunk1)]),
    )

    with pytest.raises(ApiError) as exc:
        services.job_service.run_typed_job(
            "raster_transform",
            {
                "scenario_id": scenario.scenario_id,
                "script": "result = light",
                "inputs": {
                    "times": {
                        "kind": "times",
                        "start_utc": "2027-01-01T00:00:00Z",
                        "stop_utc": "2027-01-01T02:00:00Z",
                        "step_hours": 1.0,
                    },
                    "light": {"temporal_source": "sun_fraction", "times": "times"},
                },
                "spatial_partitioning": "forbidden",
                "mode": "immediate",
            },
        )
    assert exc.value.code == "raster_transform_temporal_reduce_required"


def test_raster_transform_applies_conservative_validity_from_all_bound_inputs(services) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="rastertransformg",
            name="Raster Transform G",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    a_path = scenario_root / "inputs" / "a.tif"
    b_path = scenario_root / "inputs" / "b.tif"
    c_path = scenario_root / "inputs" / "c.tif"
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(dem, data=np.array([[1.0, 1.0], [1.0, 1.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)
    _write_raster(a_path, data=np.array([[2.0, 3.0], [4.0, 5.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)
    _write_raster(b_path, data=np.array([[10.0, 10.0], [10.0, 10.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)
    _write_raster(c_path, data=np.array([[-9999.0, 9.0], [9.0, 9.0]], dtype=np.float32), crs=ESRI_103878_WKT, transform=transform, nodata=-9999.0)

    job = services.job_service.run_typed_job(
        "raster_transform",
        {
            "scenario_id": scenario.scenario_id,
            "script": "result = a + b",
            "inputs": {
                "a": {"relative_path": "inputs/a.tif"},
                "b": {"relative_path": "inputs/b.tif"},
                "c": {"relative_path": "inputs/c.tif"},
            },
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        arr = ds.read(1)
        assert float(arr[0, 0]) == pytest.approx(-9999.0)
        assert float(arr[0, 1]) == pytest.approx(13.0)


def test_raster_transform_marks_missing_horizon_patches_nodata(services, monkeypatch) -> None:
    scenario = services.scenario_service.create_scenario(
        CreateScenarioRequest(
            scenario_root="rastertransformh",
            name="Raster Transform H",
            owner="test",
        )
    )
    scenario_root = Path(scenario.directory).resolve()
    dem = scenario_root / "dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    horizons_dir.mkdir(parents=True, exist_ok=True)
    transform = from_origin(0.0, 256.0, 1.0, 1.0)
    _write_raster(
        dem,
        data=np.ones((256, 256), dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    (horizons_dir / "horizon_00000_00000_000.bin").write_bytes(b"x")

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            return

    chunk = np.array([[[[10.0] * 128] * 128]], dtype=np.float32)
    meta = types.SimpleNamespace(
        patch_row=0, patch_col=0, width=128, height=128, rank=4, time_offset=0, time_count=1
    )

    import backend.jobs.handlers as handlers_module

    monkeypatch.setattr(handlers_module, "LightmapStreamingClient", _FakeClient)
    monkeypatch.setattr(
        handlers_module,
        "stream_tiles_v2",
        lambda *_args, **_kwargs: iter([(meta, chunk)]),
    )

    job = services.job_service.run_typed_job(
        "raster_transform",
        {
            "scenario_id": scenario.scenario_id,
            "script": "result = avg(light)",
            "inputs": {
                "times": {
                    "kind": "times",
                    "start_utc": "2027-01-01T00:00:00Z",
                    "stop_utc": "2027-01-01T00:00:00Z",
                    "step_hours": 1.0,
                },
                "light": {"temporal_source": "sun_fraction", "times": "times"},
            },
            "mode": "immediate",
        },
    )
    result = _extract_result(services, job.job_id)
    assert result["planner_summary"]["execution_strategy"] == "tiled_temporal"
    file_path, _ = services.product_service.resolve_file_path(str(result["file_id"]))
    with rasterio.open(file_path) as ds:
        arr = ds.read(1)
        assert float(arr[0, 0]) == pytest.approx(10.0)
        assert float(arr[0, 200]) == pytest.approx(-9999.0)
