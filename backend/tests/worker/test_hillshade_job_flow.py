from __future__ import annotations

import json
import sqlite3
import shutil
import sys
import time
import types
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from backend.api.dependencies import build_service_container
from backend.jobs.handlers import JobHandlers
from backend.jobs.runtime_context import set_job_cancel_checker, set_job_progress_emitter
from backend.jobs.worker_protocol import write_progress_event


def _install_fake_moonlib(monkeypatch, target_module) -> None:
    class _Bridge:
        def GenerateHillshade(self, dem_path: str, hillshade_path: str) -> None:
            shutil.copy2(dem_path, hillshade_path)

        def GenerateHorizons(
            self,
            scenario_root_dir: str,
            dem_path: str,
            surrounding_dem_paths: list[str],
            horizons_dir: str,
            observer_elevation_meters: float,
            overwrite_horizons: bool,
            compress_horizons: bool,
            progress_callback=None,
            cancel_callback=None,
        ) -> None:
            if progress_callback is not None:
                progress_callback(
                    types.SimpleNamespace(
                        Percent=100.0,
                        Message="Fake horizons complete.",
                        Stage="complete",
                        ProcessedPatches=1,
                        TotalPatches=1,
                        FileName=None,
                    )
                )
            if cancel_callback is not None:
                assert cancel_callback() is False
            marker = Path(horizons_dir) / "horizons.done.txt"
            marker.write_text(
                "\n".join(
                    [
                        f"scenario_root_dir={scenario_root_dir}",
                        f"dem_path={dem_path}",
                        f"surrounding_dem_paths={surrounding_dem_paths}",
                        f"observer_elevation_meters={observer_elevation_meters}",
                        f"overwrite_horizons={overwrite_horizons}",
                        f"compress_horizons={compress_horizons}",
                    ]
                ),
                encoding="utf-8",
            )

        def GeneratePermanentShadowMap(
            self,
            scenario_root_dir: str,
            dem_path: str,
            surrounding_dem_paths: list[str],
            horizons_dir: str,
            output_path: str,
            progress_callback=None,
            cancel_callback=None,
        ) -> None:
            _ = surrounding_dem_paths
            if cancel_callback is not None:
                assert cancel_callback() is False
            if progress_callback is not None:
                progress_callback(
                    types.SimpleNamespace(
                        Percent=55.0,
                        Stage="native_execution",
                        Message="Fake PSR native progress.",
                    )
                )
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(
                "\n".join(
                    [
                        f"scenario_root_dir={scenario_root_dir}",
                        f"dem_path={dem_path}",
                        f"horizons_dir={horizons_dir}",
                    ]
                ),
                encoding="utf-8",
            )

    class _Moonlib:
        MoonlibBridge = _Bridge

    monkeypatch.setattr(target_module, "import_moonlib", lambda: _Moonlib)


def _write_test_dem(path: Path) -> None:
    data = np.array(
        [
            [100.0, 101.0, 102.0],
            [99.0, 100.0, 103.0],
            [98.0, 99.0, 101.0],
        ],
        dtype=np.float32,
    )
    transform = from_origin(0.0, 3.0, 1.0, 1.0)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=3,
        height=3,
        count=1,
        dtype="float32",
        transform=transform,
    ) as ds:
        ds.write(data, 1)


def _wait_for_terminal_job(
    job_service,
    job_id: str,
    *,
    timeout_seconds: float = 10.0,
) -> dict[str, object]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        job = job_service.get_job(job_id)
        status = job.status.value
        if status in {"completed", "failed", "cancelled"}:
            return job.model_dump(mode="json")
        time.sleep(0.05)
    raise AssertionError(f"job did not reach terminal status within timeout: {job_id}")


def test_job_handler_generate_horizons_calls_bridge(
    monkeypatch, tmp_path: Path
) -> None:
    import backend.jobs.handlers as handlers_module

    _install_fake_moonlib(monkeypatch, handlers_module)

    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem = scenario_root / "dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    _write_test_dem(dem)

    result = JobHandlers.generate_horizons(
        scenario_id="s2",
        scenario_root_dir=str(scenario_root),
        dem_path=str(dem),
        horizons_dir=str(horizons_dir),
        overwrite_horizons=True,
        compress_horizons=False,
    )

    assert result.scenario_id == "s2"
    assert result.scenario_root_dir == str(scenario_root.resolve())
    marker = horizons_dir / "horizons.done.txt"
    assert marker.exists()
    marker_text = marker.read_text(encoding="utf-8")
    assert "overwrite_horizons=True" in marker_text
    assert "compress_horizons=False" in marker_text
    db_path = Path(result.artifact_db_path or "")
    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT job_type, artifact_kind, artifact_path FROM artifact_output"
        ).fetchone()
    assert row == ("generate_horizons", "horizons", str(horizons_dir.resolve()))


def test_generate_horizons_queued_job_records_native_progress(
    monkeypatch, tmp_path: Path
) -> None:
    import backend.jobs.handlers as handlers_module
    import backend.api.dependencies as dependencies_module

    _install_fake_moonlib(monkeypatch, handlers_module)
    monkeypatch.setenv("LUNAR_ANALYST_NATIVE_INLINE_HANDLERS", "1")
    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(tmp_path / "workspace"))

    dependencies_module.SERVICES = build_service_container()
    try:
        scenario_root = tmp_path / "scenario"
        scenario_root.mkdir(parents=True, exist_ok=True)
        dem = scenario_root / "dem.tif"
        horizons_dir = scenario_root / "lighting" / "horizons"
        _write_test_dem(dem)

        queued = dependencies_module.SERVICES.job_service.run_typed_job(
            "generate_horizons",
            {
                "scenario_id": "s2",
                "scenario_root_dir": str(scenario_root),
                "dem_path": str(dem),
                "horizons_dir": str(horizons_dir),
                "overwrite_horizons": True,
                "compress_horizons": False,
            },
        )
        assert queued.status.value in {"queued", "running", "completed"}
        assert queued.job_type == "generate_horizons"
        terminal = _wait_for_terminal_job(dependencies_module.SERVICES.job_service, queued.job_id)
        assert terminal["status"] == "completed"

        events = [
            event.model_dump(mode="json")
            for event in dependencies_module.SERVICES.job_service.list_job_events(queued.job_id)
        ]
        assert [e["event_name"] for e in events] == [
            "job_queued",
            "job_started",
            "job_progress",
            "job_progress",
            "job_completed",
        ]
        assert events[2]["data"]["stage"] == "complete"
        assert events[2]["data"]["message"] == "Fake horizons complete."
        result = events[-1]["data"]["result"]
        assert result["scenario_id"] == "s2"
        assert Path(result["horizons_dir"]).exists()
        assert Path(result["artifact_db_path"]).exists()
    finally:
        dependencies_module.SERVICES.job_service.shutdown()
        dependencies_module.SERVICES.notebook_job_service.terminate_all_running(reason="test shutdown")
        dependencies_module.SERVICES.marimo_service.stop_if_running()
        dependencies_module.SERVICES = None


def _install_fake_gdal_for_native_reduce(
    monkeypatch,
    handlers_module,
    *,
    width: int = 3,
    height: int = 3,
    open_hook=None,
):
    monkeypatch.setattr(handlers_module, "configure_gdal_runtime", lambda: None)

    class _FakeBand:
        def __init__(self) -> None:
            self.writes: list[np.ndarray] = []
        def SetNoDataValue(self, _value: float) -> None:
            return None
        def Fill(self, _value: float) -> None:
            return None
        def WriteArray(self, arr: np.ndarray, *, xoff: int, yoff: int) -> None:
            assert xoff == 0 and yoff == 0
            self.writes.append(np.array(arr, copy=True))
        def FlushCache(self) -> None:
            return None

    class _FakeDataset:
        RasterXSize = width
        RasterYSize = height
        def __init__(self, band_count: int) -> None:
            self._bands = {i: _FakeBand() for i in range(1, band_count + 1)}
        def GetProjection(self) -> str:
            return ""
        def GetGeoTransform(self, *, can_return_null: bool = False):
            _ = can_return_null
            return (0.0, 1.0, 0.0, 0.0, 0.0, -1.0)
        def SetProjection(self, _projection: str) -> None:
            return None
        def SetGeoTransform(self, _gt) -> None:
            return None
        def GetRasterBand(self, index: int):
            return self._bands.get(index)
        def FlushCache(self) -> None:
            return None

    created: dict[str, object] = {}
    dem_ds = _FakeDataset(band_count=1)

    class _FakeDriver:
        def Create(self, path: str, _w: int, _h: int, count: int, _dtype: int, options=None):
            _ = options
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"fake")
            out_ds = _FakeDataset(band_count=count)
            created["path"] = path
            created["dataset"] = out_ds
            return out_ds

    def _fake_open(_path, _mode):
        if open_hook is not None:
            open_hook()
        return dem_ds

    fake_gdal = types.SimpleNamespace(
        GA_ReadOnly=0,
        GDT_Byte=1,
        GDT_Int16=3,
        GDT_UInt16=2,
        GDT_Int32=5,
        GDT_UInt32=4,
        GDT_Float32=6,
        GDT_Float64=7,
        UseExceptions=lambda: None,
        Open=_fake_open,
        GetDriverByName=lambda _name: _FakeDriver(),
    )
    fake_osgeo = types.ModuleType("osgeo")
    fake_osgeo.gdal = fake_gdal  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "osgeo", fake_osgeo)
    return created


def test_job_handler_generate_average_sun_fraction_raster_uses_native_reduce(
    monkeypatch, tmp_path: Path
) -> None:
    import backend.jobs.handlers as handlers_module

    created = _install_fake_gdal_for_native_reduce(monkeypatch, handlers_module)

    request_capture: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            return None

    def _fake_stream_tiles_v2(_client, request, **_kwargs):
        request_capture["request"] = request
        meta = types.SimpleNamespace(rank=3, patch_row=0, patch_col=0, width=3, height=3)
        arr = np.full((1, 3, 3), 0.5, dtype=np.float32)
        return iter([(meta, arr)])

    monkeypatch.setattr(handlers_module, "LightmapStreamingClient", _FakeClient)
    monkeypatch.setattr(handlers_module, "stream_tiles_v2", _fake_stream_tiles_v2)

    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem = scenario_root / "dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    output = scenario_root / "lighting" / "avg_sun.tif"
    _write_test_dem(dem)
    horizons_dir.mkdir(parents=True, exist_ok=True)

    result = JobHandlers.generate_average_sun_fraction_raster(
        scenario_id="s1",
        scenario_root_dir=str(scenario_root),
        dem_path=str(dem),
        horizons_dir=str(horizons_dir),
        output_path=str(output),
        time_start_utc="2027-01-01T00:00:00Z",
        time_stop_utc="2027-01-01T03:00:00Z",
        time_step_hours=1.0,
    )

    req = request_capture["request"]
    assert req.mode == "native_reduce"
    assert isinstance(req.reducers, list)
    assert req.reducers[0]["kind"] == "average_sun_fraction"
    assert result.reducer_kind == "generate_average_sun_fraction_raster"
    assert result.tiles_written == 1
    assert result.value_min == pytest.approx(0.5)
    assert result.value_max == pytest.approx(0.5)
    assert Path(result.artifact_db_path or "").exists()
    with sqlite3.connect(result.artifact_db_path) as conn:
        row = conn.execute(
            "SELECT job_type, artifact_kind, artifact_path FROM artifact_output"
        ).fetchone()
    assert row == ("generate_average_sun_fraction_raster", "raster", str(output.resolve()))

    out_ds = created["dataset"]
    band1 = out_ds.GetRasterBand(1)
    assert len(band1.writes) == 1


def test_native_reduce_lightmap_raster_writes_tile_progress_jsonl(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import backend.jobs.handlers as handlers_module

    _install_fake_gdal_for_native_reduce(monkeypatch, handlers_module, width=256, height=128)

    class _FakeStatus:
        job_id = "native-job-1"
        state = "Running"
        progress01 = 0.5
        tiles_produced = 1
        tiles_consumed = 1
        ready_queue_depth = 0
        free_buffer_count = 1
        message = "native status"

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def get_status(self, job_id: str):
            assert job_id == "native-job-1"
            return _FakeStatus()

    def _fake_stream_tiles_v2(_client, request, **_kwargs):
        for tile_id in range(1, 3):
            meta = types.SimpleNamespace(
                job_id="native-job-1",
                tile_id=tile_id,
                rank=3,
                patch_row=0,
                patch_col=0,
                width=128,
                height=128,
            )
            arr = np.full((1, 128, 128), float(tile_id), dtype=np.float32)
            yield meta, arr

    monkeypatch.setattr(handlers_module, "LightmapStreamingClient", _FakeClient)
    monkeypatch.setattr(handlers_module, "stream_tiles_v2", _fake_stream_tiles_v2)

    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem = scenario_root / "dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    output = scenario_root / "lighting" / "avg_sun.tif"
    _write_test_dem(dem)
    horizons_dir.mkdir(parents=True, exist_ok=True)

    progress_path = tmp_path / "progress.jsonl"
    set_job_progress_emitter(lambda payload: write_progress_event(progress_path, payload))
    try:
        result = JobHandlers._run_native_reduce_lightmap_raster(
            scenario_id="s-progress",
            scenario_root_dir=str(scenario_root),
            dem_path=str(dem),
            horizons_dir=str(horizons_dir),
            output_path=str(output),
            time_start_utc="2027-01-01T00:00:00Z",
            time_stop_utc="2027-01-01T03:00:00Z",
            time_step_hours=1.0,
            reducers=[
                {
                    "kind": "average_sun_fraction",
                    "output_normalized_01": True,
                    "output_type": "float32",
                }
            ],
            reducer_kind="generate_average_sun_fraction_raster",
            patch_width=128,
            patch_height=128,
            status_poll_interval_seconds=0.001,
        )
    finally:
        set_job_progress_emitter(None)

    assert result.tiles_written == 2
    progress = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tile_events = [event for event in progress if event.get("stage") == "native_reduce_tiles"]
    assert [event["processed"] for event in tile_events] == [1, 2]
    assert [event["total"] for event in tile_events] == [2, 2]
    assert tile_events[0]["percent"] == 50.0
    assert tile_events[-1]["percent"] == 100.0
    assert tile_events[0]["native_progress01"] == 0.5
    assert tile_events[0]["tiles_produced"] == 1
    assert tile_events[0]["tiles_consumed"] == 1
    assert progress[-1]["stage"] == "native_reduce_complete"


def test_native_reduce_bootstraps_before_python_gdal_open(
    monkeypatch, tmp_path: Path
) -> None:
    import backend.jobs.handlers as handlers_module

    client_initialized = {"value": False}

    def _assert_client_initialized() -> None:
        assert client_initialized["value"] is True

    _install_fake_gdal_for_native_reduce(
        monkeypatch,
        handlers_module,
        open_hook=_assert_client_initialized,
    )

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            client_initialized["value"] = True

    def _fake_stream_tiles_v2(_client, _request, **_kwargs):
        meta = types.SimpleNamespace(rank=3, patch_row=0, patch_col=0, width=3, height=3)
        arr = np.full((1, 3, 3), 0.5, dtype=np.float32)
        return iter([(meta, arr)])

    monkeypatch.setattr(handlers_module, "LightmapStreamingClient", _FakeClient)
    monkeypatch.setattr(handlers_module, "stream_tiles_v2", _fake_stream_tiles_v2)

    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem = scenario_root / "dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    output = scenario_root / "lighting" / "avg_sun.tif"
    _write_test_dem(dem)
    horizons_dir.mkdir(parents=True, exist_ok=True)

    JobHandlers.generate_average_sun_fraction_raster(
        scenario_id="s1",
        scenario_root_dir=str(scenario_root),
        dem_path=str(dem),
        horizons_dir=str(horizons_dir),
        output_path=str(output),
        time_start_utc="2027-01-01T00:00:00Z",
        time_stop_utc="2027-01-01T03:00:00Z",
        time_step_hours=1.0,
    )
    assert client_initialized["value"] is True


def test_job_handler_generate_combined_sun_earth_max_contiguous_duration_raster_builds_reducer(
    monkeypatch, tmp_path: Path
) -> None:
    import backend.jobs.handlers as handlers_module

    _install_fake_gdal_for_native_reduce(monkeypatch, handlers_module)
    request_capture: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            return None

    def _fake_stream_tiles_v2(_client, request, **_kwargs):
        request_capture["request"] = request
        meta = types.SimpleNamespace(rank=3, patch_row=0, patch_col=0, width=3, height=3)
        arr = np.full((1, 3, 3), 4.0, dtype=np.float32)
        return iter([(meta, arr)])

    monkeypatch.setattr(handlers_module, "LightmapStreamingClient", _FakeClient)
    monkeypatch.setattr(handlers_module, "stream_tiles_v2", _fake_stream_tiles_v2)

    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem = scenario_root / "primary_dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    output = scenario_root / "lighting" / "combined.tif"
    _write_test_dem(dem)
    horizons_dir.mkdir(parents=True, exist_ok=True)

    result = JobHandlers.generate_combined_sun_earth_max_contiguous_duration_raster(
        scenario_id="s2",
        scenario_root_dir=str(scenario_root),
        dem_path=str(dem),
        horizons_dir=str(horizons_dir),
        output_path=str(output),
        time_start_utc="2027-01-01T00:00:00Z",
        time_stop_utc="2027-01-01T03:00:00Z",
        time_step_hours=1.0,
        min_sun_fraction_u8=10,
        earth_threshold_deg=2.5,
        earth_threshold_reference="lower_limb_margin",
    )

    req = request_capture["request"]
    reducer = req.reducers[0]
    assert reducer["kind"] == "combined_sun_earth_contiguous_duration"
    assert reducer["sun_predicate"]["min_sun_fraction_u8"] == 10
    assert reducer["earth_margin_predicate"]["threshold_value"] == pytest.approx(2.5)
    assert reducer["earth_margin_predicate"]["reference"] == "lower_limb_margin"
    assert result.reducer_kind == "generate_combined_sun_earth_max_contiguous_duration_raster"
    assert result.value_min == pytest.approx(4.0)


def test_job_handler_generate_psr_raster_calls_bridge(
    monkeypatch, tmp_path: Path
) -> None:
    import backend.jobs.handlers as handlers_module

    _install_fake_moonlib(monkeypatch, handlers_module)

    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem = scenario_root / "primary_dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    output = scenario_root / "lighting" / "psr.tif"
    _write_test_dem(dem)
    horizons_dir.mkdir(parents=True, exist_ok=True)

    result = JobHandlers.generate_psr_raster(
        scenario_id="psr1",
        scenario_root_dir=str(scenario_root),
        dem_path="",
        horizons_dir="",
        output_path="",
    )

    assert result.scenario_id == "psr1"
    assert Path(result.output_path).exists()
    text = Path(result.output_path).read_text(encoding="utf-8")
    assert "dem_path=" in text
    assert "horizons_dir=" in text
    assert result.size_bytes > 0
    assert Path(result.artifact_db_path or "").exists()
    with sqlite3.connect(result.artifact_db_path) as conn:
        row = conn.execute(
            "SELECT job_type, artifact_kind, artifact_path FROM artifact_output ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    assert row == ("generate_psr_raster", "raster", str(output.resolve()))


def test_generate_psr_raster_writes_staged_progress_jsonl(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import backend.jobs.handlers as handlers_module

    _install_fake_moonlib(monkeypatch, handlers_module)

    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem = scenario_root / "primary_dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    _write_test_dem(dem)
    horizons_dir.mkdir(parents=True, exist_ok=True)

    progress_path = tmp_path / "progress.jsonl"
    set_job_progress_emitter(lambda payload: write_progress_event(progress_path, payload))
    try:
        result = JobHandlers.generate_psr_raster(
            scenario_id="psr-progress",
            scenario_root_dir=str(scenario_root),
            dem_path="",
            horizons_dir="",
            output_path="",
        )
    finally:
        set_job_progress_emitter(None)

    assert Path(result.output_path).exists()
    progress = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    stages = [event.get("stage") for event in progress]
    assert "psr_setup" in stages
    assert "psr_native_execution" in stages
    assert "native_execution" in stages
    assert "psr_validate_output" in stages
    assert "psr_register_artifact" in stages
    assert stages[-1] == "psr_complete"


def test_generate_psr_raster_observes_cancel_flag_before_native_execution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import backend.jobs.handlers as handlers_module

    _install_fake_moonlib(monkeypatch, handlers_module)

    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem = scenario_root / "primary_dem.tif"
    horizons_dir = scenario_root / "lighting" / "horizons"
    _write_test_dem(dem)
    horizons_dir.mkdir(parents=True, exist_ok=True)

    set_job_cancel_checker(lambda: True)
    try:
        with pytest.raises(RuntimeError, match="PSR raster generation canceled"):
            JobHandlers.generate_psr_raster(
                scenario_id="psr-cancel",
                scenario_root_dir=str(scenario_root),
                dem_path="",
                horizons_dir="",
                output_path="",
            )
    finally:
        set_job_cancel_checker(None)
