from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

import backend.notebook.notebook_helper as notebook_helper
from backend.worker.lightmap_streaming import TemporalSignalSpecPy


def test_bool_param_parses_common_values() -> None:
    params = {
        "a": True,
        "b": "true",
        "c": "0",
        "d": "off",
        "e": 2,
        "f": "",
    }
    assert notebook_helper.bool_param(params, "a", False) is True
    assert notebook_helper.bool_param(params, "b", False) is True
    assert notebook_helper.bool_param(params, "c", True) is False
    assert notebook_helper.bool_param(params, "d", True) is False
    assert notebook_helper.bool_param(params, "e", False) is True
    assert notebook_helper.bool_param(params, "f", True) is False
    assert notebook_helper.bool_param(params, "missing", True) is True


def test_resolve_dem_path_from_params_uses_explicit_relative_path(tmp_path: Path) -> None:
    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem_path = scenario_root / "inputs" / "dem.tif"
    dem_path.parent.mkdir(parents=True, exist_ok=True)
    dem_path.write_bytes(b"")

    resolved = notebook_helper.resolve_dem_path_from_params(
        scenario_root=scenario_root,
        scenario_id="scenario_1",
        params={"dem_relative_path": "inputs/dem.tif"},
    )

    assert resolved == dem_path.resolve()


def test_resolve_dem_path_from_params_falls_back_to_primary_dem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(parents=True, exist_ok=True)
    fallback = scenario_root / "primary_dem.tif"
    fallback.write_bytes(b"")

    calls: list[tuple[Path, str]] = []

    def _fake_primary_dem(*, scenario_root_dir: Path, scenario_id: str) -> Path:
        calls.append((scenario_root_dir, scenario_id))
        return fallback

    monkeypatch.setattr(notebook_helper, "resolve_primary_dem_path", _fake_primary_dem)

    resolved = notebook_helper.resolve_dem_path_from_params(
        scenario_root=scenario_root,
        scenario_id="scenario_2",
        params={},
    )

    assert resolved == fallback.resolve()
    assert calls == [(scenario_root, "scenario_2")]


def test_resolve_scenario_relative_dir_creates_directory(tmp_path: Path) -> None:
    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(parents=True, exist_ok=True)

    relative, resolved = notebook_helper.resolve_scenario_relative_dir(
        scenario_root=scenario_root,
        raw="lighting/horizons",
        default="lighting/horizons",
        create=True,
    )

    assert relative == "lighting/horizons"
    assert resolved == (scenario_root / "lighting" / "horizons").resolve()
    assert resolved.is_dir()


def test_resolve_scenario_identity_and_root_uses_context_under_job_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scenario_root = (tmp_path / "runner_scenario").resolve()
    ctx = types.SimpleNamespace(
        scenario_id="runner_scenario",
        scenario_root_dir=scenario_root,
    )
    monkeypatch.setattr(notebook_helper, "is_running_under_job_runner", lambda: True)
    monkeypatch.setattr(notebook_helper, "get_context", lambda: ctx)

    scenario_id, resolved_root = notebook_helper.resolve_scenario_identity_and_root()

    assert scenario_id == "runner_scenario"
    assert resolved_root == scenario_root


def test_resolve_scenario_identity_and_root_uses_env_when_not_under_job_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scenario_root = (tmp_path / "env_scenario").resolve()
    monkeypatch.setattr(notebook_helper, "is_running_under_job_runner", lambda: False)
    monkeypatch.setenv("LUNAR_NOTEBOOK_SCENARIO_ID", "env_scenario")
    monkeypatch.setenv("LUNAR_NOTEBOOK_SCENARIO_ROOT", str(scenario_root))

    scenario_id, resolved_root = notebook_helper.resolve_scenario_identity_and_root()

    assert scenario_id == "env_scenario"
    assert resolved_root == scenario_root


def test_resolve_scenario_identity_and_root_defaults_when_not_under_job_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notebook_helper, "is_running_under_job_runner", lambda: False)
    monkeypatch.delenv("LUNAR_NOTEBOOK_SCENARIO_ID", raising=False)
    monkeypatch.delenv("LUNAR_NOTEBOOK_SCENARIO_ROOT", raising=False)

    scenario_id, resolved_root = notebook_helper.resolve_scenario_identity_and_root(
        default_scenario_id="test_scenario",
        default_scenario_parent_dir="/e/lunar_analyst_scenarios",
    )

    assert scenario_id == "test_scenario"
    assert resolved_root == Path("/e/lunar_analyst_scenarios/test_scenario").resolve()


def test_resolve_scenario_identity_and_root_infers_script_scenario_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scenario_root = (tmp_path / "mons-mouton").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.db").write_bytes(b"")
    script_path = scenario_root / "morning-sun.py"
    script_path.write_text("print('x')\n", encoding="utf-8")

    main_module = sys.modules["__main__"]
    original = getattr(main_module, "__file__", None)
    monkeypatch.setattr(notebook_helper, "is_running_under_job_runner", lambda: False)
    monkeypatch.delenv("LUNAR_NOTEBOOK_SCENARIO_ID", raising=False)
    monkeypatch.delenv("LUNAR_NOTEBOOK_SCENARIO_ROOT", raising=False)
    setattr(main_module, "__file__", str(script_path))
    try:
        scenario_id, resolved_root = notebook_helper.resolve_scenario_identity_and_root()
    finally:
        if original is None:
            delattr(main_module, "__file__")
        else:
            setattr(main_module, "__file__", original)

    assert scenario_id == "mons-mouton"
    assert resolved_root == scenario_root


def test_write_output_raster_resolves_relative_path_against_scenario_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scenario_root = (tmp_path / "mons-mouton").resolve()
    captured: dict[str, Any] = {}

    def _fake_write_output_raster(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 123

    monkeypatch.setattr(
        notebook_helper,
        "resolve_scenario_identity_and_root",
        lambda **_kwargs: ("mons-mouton", scenario_root),
    )
    monkeypatch.setattr(notebook_helper, "_write_output_raster", _fake_write_output_raster)

    result = notebook_helper.write_output_raster(
        output_path=Path("hillshade.morning-sun.tif"),
        target_grid=types.SimpleNamespace(width=1, height=1, crs="x", transform=(0, 1, 0, 0, 0, -1)),
        array=np.zeros((1, 1), dtype=np.uint8),
        overwrite=True,
    )

    assert result == 123
    assert captured["output_path"] == (scenario_root / "hillshade.morning-sun.tif").resolve()


def test_directory_file_stats_counts_nested_files(tmp_path: Path) -> None:
    root = tmp_path / "stats"
    (root / "a").mkdir(parents=True, exist_ok=True)
    (root / "a" / "f1.bin").write_bytes(b"abc")
    (root / "a" / "f2.bin").write_bytes(b"de")
    (root / "f3.bin").write_bytes(b"")

    file_count, size_bytes = notebook_helper.directory_file_stats(root)

    assert file_count == 3
    assert size_bytes == 5


def test_write_json_writes_pretty_json_with_newline(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "payload.json"
    payload = {"b": 2, "a": 1}

    notebook_helper.write_json(target, payload)

    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text) == payload


def test_label_regions_labels_8_connected_components() -> None:
    mask = np.array(
        [
            [1, 0, 0, 0],
            [0, 1, 0, 1],
            [0, 0, 0, 1],
        ],
        dtype=np.uint8,
    )

    labels = notebook_helper.label_regions(mask)

    assert labels.dtype == np.int32
    assert int(labels[0, 0]) == int(labels[1, 1])
    assert int(labels[1, 3]) == int(labels[2, 3])
    assert int(labels[0, 0]) != int(labels[1, 3])
    assert int(labels.max()) == 2


def test_label_regions_with_erosion_breaks_one_pixel_bridge() -> None:
    mask = np.array(
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

    labels = notebook_helper.label_regions(mask, cleanup_mode="erosion", cleanup_iterations=1)

    assert int(labels.max()) == 2
    assert int(labels[3, 3]) != int(labels[3, 9])


def test_label_regions_rejects_invalid_cleanup_mode() -> None:
    with pytest.raises(ValueError, match="cleanup_mode"):
        notebook_helper.label_regions(np.ones((3, 3), dtype=np.uint8), cleanup_mode="bad_mode")


def test_region_sizes_returns_component_size_raster() -> None:
    mask = np.array(
        [
            [1, 1, 0, 0],
            [0, 1, 0, 1],
            [0, 0, 0, 1],
        ],
        dtype=np.uint8,
    )

    sizes = notebook_helper.region_sizes(mask)

    assert sizes.dtype == np.int32
    assert sizes.tolist() == [
        [3, 3, 0, 0],
        [0, 3, 0, 2],
        [0, 0, 0, 2],
    ]


def test_region_sizes_with_erosion_breaks_one_pixel_bridge() -> None:
    mask = np.array(
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

    sizes = notebook_helper.region_sizes(mask, cleanup_mode="erosion", cleanup_iterations=1)

    assert int(sizes[3, 3]) == 9
    assert int(sizes[3, 9]) == 9
    assert int(sizes[3, 6]) == 0


def test_region_sizes_rejects_invalid_cleanup_mode() -> None:
    with pytest.raises(ValueError, match="cleanup_mode"):
        notebook_helper.region_sizes(np.ones((3, 3), dtype=np.uint8), cleanup_mode="bad_mode")


def test_filter_regions_by_size_gte_keeps_large_component() -> None:
    mask = np.array(
        [
            [1, 1, 0, 0],
            [0, 1, 0, 1],
            [0, 0, 0, 1],
        ],
        dtype=np.uint8,
    )

    filtered = notebook_helper.filter_regions_by_size(mask, 3, ">=")

    assert filtered.dtype == np.bool_
    assert filtered.tolist() == [
        [True, True, False, False],
        [False, True, False, False],
        [False, False, False, False],
    ]


def test_filter_regions_by_size_lte_keeps_small_component() -> None:
    mask = np.array(
        [
            [1, 1, 0, 0],
            [0, 1, 0, 1],
            [0, 0, 0, 1],
        ],
        dtype=np.uint8,
    )

    filtered = notebook_helper.filter_regions_by_size(mask, 2, "<=")

    assert filtered.tolist() == [
        [False, False, False, False],
        [False, False, False, True],
        [False, False, False, True],
    ]


def test_filter_regions_by_size_preserves_shape_after_cleanup() -> None:
    mask = np.array(
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

    filtered = notebook_helper.filter_regions_by_size(
        mask,
        9,
        ">=",
        cleanup_mode="erosion",
        cleanup_iterations=1,
    )

    assert bool(filtered[3, 6]) is True
    assert bool(filtered[1, 1]) is True
    assert bool(filtered[1, 11]) is True


def test_filter_regions_by_size_rejects_invalid_comparator() -> None:
    with pytest.raises(ValueError, match="comparator"):
        notebook_helper.filter_regions_by_size(np.ones((3, 3), dtype=np.uint8), 1, "==")


def test_find_borders_returns_inner_border_mask() -> None:
    mask = np.array(
        [
            [0, 0, 0, 0, 0],
            [0, 1, 1, 1, 0],
            [0, 1, 1, 1, 0],
            [0, 1, 1, 1, 0],
            [0, 0, 0, 0, 0],
        ],
        dtype=bool,
    )

    borders = notebook_helper.find_borders(mask)

    expected = np.array(
        [
            [0, 0, 0, 0, 0],
            [0, 1, 1, 1, 0],
            [0, 1, 0, 1, 0],
            [0, 1, 1, 1, 0],
            [0, 0, 0, 0, 0],
        ],
        dtype=bool,
    )
    assert borders.dtype == np.bool_
    assert np.array_equal(borders, expected)


def test_compute_mask_connectivity_metrics_reports_expected_tuple() -> None:
    mask = np.array(
        [
            [1, 0, 0, 0],
            [1, 0, 0, 1],
            [0, 0, 0, 1],
        ],
        dtype=np.uint8,
    )

    component_count, largest_component, adjacency_ratio = notebook_helper.compute_mask_connectivity_metrics(mask)

    assert component_count == 2
    assert largest_component == 2
    assert adjacency_ratio == pytest.approx(1.0)


def test_compute_mask_connectivity_metrics_opening_cleanup_can_remove_small_components() -> None:
    mask = np.array(
        [
            [0, 0, 0],
            [0, 1, 0],
            [0, 0, 0],
        ],
        dtype=np.uint8,
    )

    component_count, largest_component, adjacency_ratio = notebook_helper.compute_mask_connectivity_metrics(
        mask,
        cleanup_mode="opening",
        cleanup_iterations=1,
    )

    assert component_count == 0
    assert largest_component == 0
    assert adjacency_ratio == 0.0


def test_array_helpers_require_2d_input() -> None:
    bad = np.zeros((2, 2, 2), dtype=np.uint8)

    with pytest.raises(ValueError, match="2D"):
        notebook_helper.label_regions(bad)
    with pytest.raises(ValueError, match="2D"):
        notebook_helper.region_sizes(bad)
    with pytest.raises(ValueError, match="2D"):
        notebook_helper.filter_regions_by_size(bad, 1, ">=")
    with pytest.raises(ValueError, match="2D"):
        notebook_helper.find_borders(bad)
    with pytest.raises(ValueError, match="2D"):
        notebook_helper.compute_mask_connectivity_metrics(bad)


def test_bootstrap_native_and_register_gdal_invokes_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap_calls: list[tuple[bool, bool]] = []

    def _fake_bootstrap_pythonnet(*, force: bool, verify_bridge_smoke: bool) -> None:
        bootstrap_calls.append((force, verify_bridge_smoke))

    monkeypatch.setattr(notebook_helper, "bootstrap_pythonnet", _fake_bootstrap_pythonnet)

    class _FakeGdal:
        registered = False

        @classmethod
        def AllRegister(cls) -> None:
            cls.registered = True

        @classmethod
        def GetDriverCount(cls) -> int:
            return 11 if cls.registered else 0

    fake_osgeo = types.ModuleType("OSGeo")
    fake_gdal_module = types.ModuleType("OSGeo.GDAL")
    fake_gdal_module.Gdal = _FakeGdal  # type: ignore[attr-defined]
    fake_osgeo.GDAL = fake_gdal_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "OSGeo", fake_osgeo)
    monkeypatch.setitem(sys.modules, "OSGeo.GDAL", fake_gdal_module)

    count = notebook_helper.bootstrap_native_and_register_gdal(
        force=False,
        verify_bridge_smoke=True,
    )

    assert bootstrap_calls == [(False, True)]
    assert count == 11


def test_bootstrap_native_and_register_gdal_raises_when_no_drivers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        notebook_helper,
        "bootstrap_pythonnet",
        lambda **_kwargs: None,
    )

    class _FakeGdal:
        @staticmethod
        def AllRegister() -> None:
            return None

        @staticmethod
        def GetDriverCount() -> int:
            return 0

    fake_osgeo = types.ModuleType("OSGeo")
    fake_gdal_module = types.ModuleType("OSGeo.GDAL")
    fake_gdal_module.Gdal = _FakeGdal  # type: ignore[attr-defined]
    fake_osgeo.GDAL = fake_gdal_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "OSGeo", fake_osgeo)
    monkeypatch.setitem(sys.modules, "OSGeo.GDAL", fake_gdal_module)

    with pytest.raises(RuntimeError, match="driver_count=0"):
        notebook_helper.bootstrap_native_and_register_gdal()


def test_create_moonlib_bridge_bootstraps_and_returns_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    def _fake_bootstrap_native_and_register_gdal(
        *,
        force: bool,
        verify_bridge_smoke: bool,
    ) -> int:
        calls.append(("bootstrap", (force, verify_bridge_smoke)))
        return 3

    class _Bridge:
        pass

    fake_moonlib = types.SimpleNamespace(MoonlibBridge=_Bridge)

    def _fake_import_moonlib(
        *,
        force_bootstrap: bool,
        verify_bridge_smoke: bool,
    ) -> object:
        calls.append(("import", (force_bootstrap, verify_bridge_smoke)))
        return fake_moonlib

    monkeypatch.setattr(
        notebook_helper,
        "bootstrap_native_and_register_gdal",
        _fake_bootstrap_native_and_register_gdal,
    )
    monkeypatch.setattr(notebook_helper, "import_moonlib", _fake_import_moonlib)

    bridge = notebook_helper.create_moonlib_bridge(
        force_bootstrap=False,
        verify_bridge_smoke=True,
    )

    assert isinstance(bridge, _Bridge)
    assert calls == [
        ("bootstrap", (False, True)),
        ("import", (False, True)),
    ]


def test_run_lightmap_streaming_raster_job_writes_transformed_tile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scenario_root = (tmp_path / "scenario").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem_path = (scenario_root / "dem.tif").resolve()
    dem_path.write_bytes(b"")
    horizons_dir = (scenario_root / "lighting" / "horizons").resolve()
    horizons_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        notebook_helper,
        "_resolve_runtime_context",
        lambda: ("scenario_1", scenario_root, {}),
    )
    monkeypatch.setattr(notebook_helper, "configure_gdal_runtime", lambda: None)
    monkeypatch.setattr(
        notebook_helper,
        "resolve_dem_path_from_params",
        lambda **_kwargs: dem_path,
    )
    monkeypatch.setattr(
        notebook_helper,
        "resolve_scenario_relative_dir",
        lambda **_kwargs: ("lighting/horizons", horizons_dir),
    )
    monkeypatch.setattr(notebook_helper, "replace_output_file", lambda _path: None)

    register_calls: list[dict[str, object]] = []

    def _fake_register_output_if_available(**kwargs: object) -> bool:
        register_calls.append(dict(kwargs))
        return True

    monkeypatch.setattr(
        notebook_helper,
        "register_output_if_available",
        _fake_register_output_if_available,
    )

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            return

    monkeypatch.setattr(notebook_helper, "LightmapStreamingClient", _FakeClient)

    tile = np.full((2, 3, 4), 255, dtype=np.uint8)
    tile_meta = types.SimpleNamespace(
        patch_row=0,
        patch_col=0,
        width=4,
        height=3,
    )
    monkeypatch.setattr(
        notebook_helper,
        "stream_tiles",
        lambda *_args, **_kwargs: iter([(tile_meta, tile)]),
    )

    writes: list[tuple[np.ndarray, int, int]] = []

    class _FakeBand:
        def SetNoDataValue(self, _value: float) -> None:
            return

        def Fill(self, _value: float) -> None:
            return

        def WriteArray(self, arr: np.ndarray, *, xoff: int, yoff: int) -> None:
            writes.append((np.array(arr, copy=True), int(xoff), int(yoff)))

        def FlushCache(self) -> None:
            return

    class _FakeDataset:
        RasterXSize = 4
        RasterYSize = 3

        def __init__(self, band: _FakeBand) -> None:
            self._band = band

        def GetProjection(self) -> str:
            return ""

        def GetGeoTransform(
            self,
            *,
            can_return_null: bool = False,
        ) -> tuple[float, float, float, float, float, float] | None:
            _ = can_return_null
            return (0.0, 1.0, 0.0, 0.0, 0.0, -1.0)

        def SetProjection(self, _projection: str) -> None:
            return

        def SetGeoTransform(self, _gt: tuple[float, float, float, float, float, float]) -> None:
            return

        def GetRasterBand(self, _index: int) -> _FakeBand | None:
            return self._band

        def FlushCache(self) -> None:
            return

    fake_band = _FakeBand()
    dem_ds = _FakeDataset(fake_band)
    out_ds = _FakeDataset(fake_band)

    class _FakeDriver:
        def Create(
            self,
            _path: str,
            _width: int,
            _height: int,
            _bands: int,
            _dtype: int,
            *,
            options: list[str] | None = None,
        ) -> _FakeDataset:
            _ = options
            return out_ds

    fake_driver = _FakeDriver()

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
        Open=lambda _path, _mode: dem_ds,
        GetDriverByName=lambda _name: fake_driver,
    )
    fake_osgeo = types.ModuleType("osgeo")
    fake_osgeo.gdal = fake_gdal  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "osgeo", fake_osgeo)

    def _transform(tile_3d: np.ndarray) -> np.ndarray:
        tile_2d = tile_3d.mean(axis=0, dtype=np.float32)
        tile_2d = (tile_2d / np.float32(255.0)).astype(np.float32, copy=False)
        return tile_2d

    result = notebook_helper.run_lightmap_streaming_raster_job(
        config=notebook_helper.LightmapRunConfig(
            time_start_utc="2027-09-01T00:00:00",
            time_stop_utc="2027-09-01T02:00:00",
            time_step_hours=2.0,
            default_output_relative_path="lighting/out.tif",
            output_dtype="float32",
        ),
        tile_transform=_transform,
    )

    assert len(writes) == 1
    written_arr, xoff, yoff = writes[0]
    assert xoff == 0
    assert yoff == 0
    assert written_arr.shape == (3, 4)
    assert written_arr.dtype == np.float32
    assert float(written_arr.min()) == pytest.approx(1.0)
    assert float(written_arr.max()) == pytest.approx(1.0)
    assert result["tiles_written"] == 1
    assert result["output_relative_path"] == "lighting/out.tif"
    assert register_calls[0]["relative_path"] == "lighting/out.tif"


def test_run_lightmap_signal_streaming_raster_job_reduces_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scenario_root = (tmp_path / "scenario").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem_path = (scenario_root / "dem.tif").resolve()
    dem_path.write_bytes(b"")
    horizons_dir = (scenario_root / "lighting" / "horizons").resolve()
    horizons_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        notebook_helper, "_resolve_runtime_context", lambda: ("scenario_1", scenario_root, {})
    )
    monkeypatch.setattr(notebook_helper, "configure_gdal_runtime", lambda: None)
    monkeypatch.setattr(notebook_helper, "resolve_dem_path_from_params", lambda **_kwargs: dem_path)
    monkeypatch.setattr(
        notebook_helper, "resolve_scenario_relative_dir", lambda **_kwargs: ("lighting/horizons", horizons_dir)
    )
    monkeypatch.setattr(notebook_helper, "replace_output_file", lambda _path: None)
    monkeypatch.setattr(notebook_helper, "register_output_if_available", lambda **_kwargs: True)

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            return

    monkeypatch.setattr(notebook_helper, "LightmapStreamingClient", _FakeClient)

    chunk0 = np.full((2, 1, 3, 4), 10.0, dtype=np.float32)
    chunk1 = np.full((2, 1, 3, 4), 20.0, dtype=np.float32)
    meta0 = types.SimpleNamespace(
        patch_row=0,
        patch_col=0,
        width=4,
        height=3,
        rank=4,
        time_offset=0,
        time_count=2,
    )
    meta1 = types.SimpleNamespace(
        patch_row=0,
        patch_col=0,
        width=4,
        height=3,
        rank=4,
        time_offset=2,
        time_count=2,
    )
    monkeypatch.setattr(
        notebook_helper,
        "stream_tiles_v2",
        lambda *_args, **_kwargs: iter([(meta0, chunk0), (meta1, chunk1)]),
    )

    writes: list[np.ndarray] = []

    class _FakeBand:
        def SetNoDataValue(self, _value: float) -> None: ...
        def Fill(self, _value: float) -> None: ...
        def WriteArray(self, arr: np.ndarray, *, xoff: int, yoff: int) -> None:
            assert xoff == 0 and yoff == 0
            writes.append(np.array(arr, copy=True))
        def FlushCache(self) -> None: ...

    class _FakeDataset:
        RasterXSize = 4
        RasterYSize = 3
        def __init__(self) -> None:
            self.band = _FakeBand()
        def GetProjection(self) -> str: return ""
        def GetGeoTransform(self, *, can_return_null: bool = False):
            _ = can_return_null
            return (0.0, 1.0, 0.0, 0.0, 0.0, -1.0)
        def SetProjection(self, _projection: str) -> None: ...
        def SetGeoTransform(self, _gt) -> None: ...
        def GetRasterBand(self, _index: int): return self.band
        def FlushCache(self) -> None: ...

    dem_ds = _FakeDataset()
    out_ds = _FakeDataset()

    class _FakeDriver:
        def Create(self, *_args, **_kwargs):
            return out_ds

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
        Open=lambda _path, _mode: dem_ds,
        GetDriverByName=lambda _name: _FakeDriver(),
    )
    fake_osgeo = types.ModuleType("osgeo")
    fake_osgeo.gdal = fake_gdal  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "osgeo", fake_osgeo)

    class _Reducer:
        def init_tile_state(self, _tile_meta: object):
            return {"sum": np.zeros((3, 4), dtype=np.float32), "count": 0}
        def update(self, state: dict[str, object], tile_chunk: np.ndarray, _tile_meta: object):
            state["sum"] = state["sum"] + tile_chunk[:, 0].sum(axis=0, dtype=np.float32)
            state["count"] = int(state["count"]) + int(tile_chunk.shape[0])
            return state
        def finalize(self, state: dict[str, object], _tile_meta: object) -> np.ndarray:
            return (state["sum"] / np.float32(state["count"])).astype(np.float32, copy=False)

    result = notebook_helper.run_lightmap_signal_streaming_raster_job(
        config=notebook_helper.LightmapRunConfig(
            time_start_utc="2027-09-01T00:00:00",
            time_stop_utc="2027-09-01T03:00:00",
            time_step_hours=1.0,
            default_output_relative_path="lighting/out_signal_v2.tif",
            output_dtype="float32",
        ),
        signals=[TemporalSignalSpecPy(signal="sun_fraction_u8")],
        reducer=_Reducer(),
    )

    assert len(writes) == 1
    written = writes[0]
    assert written.shape == (3, 4)
    assert written.dtype == np.float32
    assert float(written.min()) == pytest.approx(15.0)
    assert float(written.max()) == pytest.approx(15.0)
    assert result["tiles_written"] == 1


def test_run_lightmap_native_reduction_raster_job_writes_multiband_tiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scenario_root = (tmp_path / "scenario").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem_path = (scenario_root / "dem.tif").resolve()
    dem_path.write_bytes(b"")
    horizons_dir = (scenario_root / "lighting" / "horizons").resolve()
    horizons_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        notebook_helper, "_resolve_runtime_context", lambda: ("scenario_1", scenario_root, {})
    )
    monkeypatch.setattr(notebook_helper, "configure_gdal_runtime", lambda: None)
    monkeypatch.setattr(notebook_helper, "resolve_dem_path_from_params", lambda **_kwargs: dem_path)
    monkeypatch.setattr(
        notebook_helper, "resolve_scenario_relative_dir", lambda **_kwargs: ("lighting/horizons", horizons_dir)
    )
    monkeypatch.setattr(notebook_helper, "replace_output_file", lambda _path: None)
    monkeypatch.setattr(notebook_helper, "register_output_if_available", lambda **_kwargs: True)

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            return

    monkeypatch.setattr(notebook_helper, "LightmapStreamingClient", _FakeClient)

    reduced = np.stack(
        [
            np.full((3, 4), 1.5, dtype=np.float32),
            np.full((3, 4), 7.0, dtype=np.float32),
        ],
        axis=0,
    )
    meta = types.SimpleNamespace(
        patch_row=0,
        patch_col=0,
        width=4,
        height=3,
        rank=3,
    )
    monkeypatch.setattr(
        notebook_helper,
        "stream_tiles_v2",
        lambda *_args, **_kwargs: iter([(meta, reduced)]),
    )

    band_writes: dict[int, list[np.ndarray]] = {1: [], 2: []}

    class _FakeBand:
        def __init__(self, band_index: int) -> None:
            self._band_index = band_index
        def SetNoDataValue(self, _value: float) -> None: ...
        def Fill(self, _value: float) -> None: ...
        def WriteArray(self, arr: np.ndarray, *, xoff: int, yoff: int) -> None:
            assert xoff == 0 and yoff == 0
            band_writes[self._band_index].append(np.array(arr, copy=True))
        def FlushCache(self) -> None: ...

    class _FakeDataset:
        RasterXSize = 4
        RasterYSize = 3
        def __init__(self, band_count: int = 2) -> None:
            self._bands = {i: _FakeBand(i) for i in range(1, band_count + 1)}
        def GetProjection(self) -> str: return ""
        def GetGeoTransform(self, *, can_return_null: bool = False):
            _ = can_return_null
            return (0.0, 1.0, 0.0, 0.0, 0.0, -1.0)
        def SetProjection(self, _projection: str) -> None: ...
        def SetGeoTransform(self, _gt) -> None: ...
        def GetRasterBand(self, index: int): return self._bands.get(index)
        def FlushCache(self) -> None: ...

    dem_ds = _FakeDataset()
    out_ds = _FakeDataset(band_count=2)

    class _FakeDriver:
        def Create(self, *_args, **_kwargs):
            return out_ds

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
        Open=lambda _path, _mode: dem_ds,
        GetDriverByName=lambda _name: _FakeDriver(),
    )
    fake_osgeo = types.ModuleType("osgeo")
    fake_osgeo.gdal = fake_gdal  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "osgeo", fake_osgeo)

    result = notebook_helper.run_lightmap_native_reduction_raster_job(
        config=notebook_helper.LightmapRunConfig(
            time_start_utc="2027-09-01T00:00:00",
            time_stop_utc="2027-09-01T03:00:00",
            time_step_hours=1.0,
            default_output_relative_path="lighting/out_reduce_v2.tif",
            output_dtype="float32",
        ),
        reducers=[
            {"kind": "average_sun_fraction"},
            {"kind": "cumulative_duration_where", "sun_predicate": {"min_sun_fraction_u8": 1}},
        ],
    )

    assert len(band_writes[1]) == 1
    assert len(band_writes[2]) == 1
    assert float(band_writes[1][0].min()) == pytest.approx(1.5)
    assert float(band_writes[2][0].max()) == pytest.approx(7.0)
    assert result["reducer_count"] == 2
    assert result["tiles_written"] == 1
