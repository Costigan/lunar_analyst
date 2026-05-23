from __future__ import annotations

import types
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

import backend.notebook.notebook_helper as notebook_helper
from backend.core.config import ESRI_103878_WKT
from backend.jobs import raster_transform
from backend.jobs.raster_transform import RasterTransformError


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


def test_parse_validate_script_collects_usage_for_statement_block() -> None:
    parsed = raster_transform.parse_validate_script(
        "tmp = a + b\nresult = where(tmp > 5, tmp, b)",
        allowed_variables={"a", "b"},
    )
    assert parsed.script_form == "block"
    assert parsed.used_variables == {"a", "b"}
    assert parsed.used_functions == {"where"}
    assert ">" in parsed.used_operators


def test_parse_validate_script_accepts_np_where_alias() -> None:
    parsed = raster_transform.parse_validate_script(
        "result = np.where(a > 1, a, 0)",
        allowed_variables={"a"},
    )
    assert parsed.used_variables == {"a"}
    assert "where" in parsed.used_functions


def test_execute_script_accepts_np_where_alias() -> None:
    parsed = raster_transform.parse_validate_script(
        "result = np.where(a > 1, a, 0)",
        allowed_variables={"a"},
    )
    result = raster_transform.execute_script(
        parsed=parsed,
        variables={"a": np.array([[0.0, 2.0], [3.0, 1.0]], dtype=np.float32)},
        target_shape=(2, 2),
    )
    assert result.tolist() == [[0.0, 2.0], [3.0, 0.0]]


def test_parse_validate_script_ignores_import_numpy_as_np() -> None:
    parsed = raster_transform.parse_validate_script(
        "import numpy as np\nresult = np.where(a > 1, a, 0)",
        allowed_variables={"a"},
    )
    # Normalized script should not preserve the import statement.
    assert "import numpy as np" not in parsed.normalized_script
    result = raster_transform.execute_script(
        parsed=parsed,
        variables={"a": np.array([[0.0, 2.0], [3.0, 1.0]], dtype=np.float32)},
        target_shape=(2, 2),
    )
    assert result.tolist() == [[0.0, 2.0], [3.0, 0.0]]


def test_parse_validate_script_rejects_import_numpy_without_alias() -> None:
    with pytest.raises(RasterTransformError) as exc:
        raster_transform.parse_validate_script(
            "import numpy\nresult = 1",
            allowed_variables={"a"},
        )
    assert exc.value.code == "raster_transform_disallowed_syntax"
    assert "Unsupported statement type: Import" in exc.value.message


def test_parse_validate_script_rejects_np_functions_outside_facade() -> None:
    with pytest.raises(RasterTransformError) as exc:
        raster_transform.parse_validate_script(
            "result = np.sin(a)",
            allowed_variables={"a"},
        )
    assert exc.value.code == "raster_transform_unknown_function"
    assert "Unsupported function: np.sin" in exc.value.message


def test_parse_validate_script_rejects_reserved_np_input_name() -> None:
    with pytest.raises(RasterTransformError) as exc:
        raster_transform.parse_validate_script(
            "result = np + 1",
            allowed_variables={"np"},
        )
    assert exc.value.code == "raster_transform_invalid_argument"
    assert "reserved_names" in exc.value.details


def test_parse_validate_script_boolop_failure_has_repair_hint() -> None:
    with pytest.raises(RasterTransformError) as exc:
        raster_transform.parse_validate_script(
            "result = (a > 0) and (a < 10)",
            allowed_variables={"a"},
        )
    assert exc.value.code == "raster_transform_disallowed_syntax"
    assert "hint" in exc.value.details
    assert "&" in str(exc.value.details["hint"])


def test_parse_validate_script_unknown_function_suggests_where() -> None:
    with pytest.raises(RasterTransformError) as exc:
        raster_transform.parse_validate_script(
            "result = wher(a > 0, 1, 0)",
            allowed_variables={"a"},
        )
    assert exc.value.code == "raster_transform_unknown_function"
    suggestions = exc.value.details.get("suggestions", [])
    assert "where" in suggestions
    assert "hint" in exc.value.details


def test_parse_validate_script_requires_result_assignment() -> None:
    with pytest.raises(RasterTransformError) as exc:
        raster_transform.parse_validate_script(
            "tmp = a + 1",
            allowed_variables={"a"},
        )
    assert exc.value.code == "raster_transform_missing_result"


@pytest.mark.parametrize("fn_name", ["nodata", "nan", "null"])
def test_parse_validate_script_accepts_nodata_aliases(fn_name: str) -> None:
    parsed = raster_transform.parse_validate_script(
        f"result = where(a > 0, 1, {fn_name}())",
        allowed_variables={"a"},
    )
    assert "where" in parsed.used_functions
    assert fn_name in parsed.used_functions


def test_execute_script_with_nodata_aliases_finalizes_to_float_nodata() -> None:
    parsed = raster_transform.parse_validate_script(
        "result = where(a > 0, 1, nodata())",
        allowed_variables={"a"},
    )
    result = raster_transform.execute_script(
        parsed=parsed,
        variables={"a": np.array([[0.0, 1.0], [2.0, 0.0]], dtype=np.float32)},
        target_shape=(2, 2),
    )
    semantic = raster_transform.infer_result_semantic(result)
    out, dtype_name, nodata, valid_mask = raster_transform.finalize_output_array(
        result=result,
        semantic=semantic,
        used_variables=parsed.used_variables,
    )
    assert semantic == "continuous"
    assert dtype_name == "float32"
    assert nodata == -9999.0
    assert out.tolist() == [[-9999.0, 1.0], [1.0, -9999.0]]
    assert valid_mask is not None
    assert valid_mask.tolist() == [[False, True], [True, False]]


def test_enforce_plan_limits_rejects_large_full_extent_temporal(monkeypatch: pytest.MonkeyPatch) -> None:
    target_grid = raster_transform.TargetGrid(
        crs=ESRI_103878_WKT,
        transform=(0, 1, 0, 0, 0, -1),
        width=64,
        height=64,
        dem_path=Path(__file__),
    )
    parsed = raster_transform.parse_validate_script("result = avg(light)", allowed_variables={"light"})
    plan = raster_transform.build_plan(
        parsed=parsed,
        target_grid=target_grid,
        static_input_names=[],
        temporal_input_names=["light"],
        spatial_partitioning="forbidden",
        time_partitioning="forbidden",
        spatial_halo_pixels=0,
        patch_width=16,
        patch_height=16,
        time_count=128,
    )
    monkeypatch.setattr(
        raster_transform,
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
    with pytest.raises(RasterTransformError) as exc:
        raster_transform.enforce_plan_limits(plan)
    assert exc.value.code == "raster_transform_plan_too_large"


def test_notebook_helper_lazy_raster_np_where_materializes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario_root = (tmp_path / "scenario").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem_path = scenario_root / "dem.tif"
    input_path = scenario_root / "inputs" / "a.tif"
    transform = from_origin(0.0, 2.0, 1.0, 1.0)
    _write_raster(
        dem_path,
        data=np.array([[10.0, 20.0], [30.0, 40.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    _write_raster(
        input_path,
        data=np.array([[1.0, 3.0], [4.0, 2.0]], dtype=np.float32),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    )
    monkeypatch.setenv("LUNAR_NOTEBOOK_SCENARIO_ID", "scenario_1")
    monkeypatch.setenv("LUNAR_NOTEBOOK_SCENARIO_ROOT", str(scenario_root))

    dem = notebook_helper.scenario_dem()
    src = notebook_helper.raster_file("inputs/a.tif")
    expr = np.where((notebook_helper.slope_raster(dem) >= 0) & (src > 2), src, np.nan)
    values = expr.materialize()

    assert values.shape == (2, 2)
    assert np.isnan(values[0, 0])
    assert float(values[0, 1]) == pytest.approx(3.0)
    assert float(values[1, 0]) == pytest.approx(4.0)


def test_scenario_dem_uses_job_runner_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario_root = (tmp_path / "mons-mouton").resolve()
    dem_path = scenario_root / "primary_dem.tif"
    _write_raster(
        dem_path,
        data=np.arange(15, dtype=np.float32).reshape(3, 5),
        crs=ESRI_103878_WKT,
        transform=from_origin(100.0, 200.0, 20.0, 20.0),
        nodata=None,
    )

    ctx = types.SimpleNamespace(
        scenario_id="scn_mons-mouton",
        scenario_root_dir=scenario_root,
    )
    monkeypatch.setattr(raster_transform, "is_running_under_job_runner", lambda: True)
    monkeypatch.setattr(raster_transform, "get_context", lambda: ctx)
    monkeypatch.setenv("LUNAR_NOTEBOOK_SCENARIO_ID", "test_scenario")
    monkeypatch.setenv("LUNAR_NOTEBOOK_SCENARIO_ROOT", str((tmp_path / "test_scenario").resolve()))

    dem = raster_transform.scenario_dem()

    assert dem.grid is not None
    assert dem.grid.width == 5
    assert dem.grid.height == 3
    assert dem._node.params["scenario_id"] == "scn_mons-mouton"
    assert dem._node.params["scenario_root"] == str(scenario_root)
    assert dem._node.params["dem_path"] == str(dem_path.resolve())


def test_scenario_dem_infers_scenario_from_main_script_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario_root = (tmp_path / "mons-mouton").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "scenario.db").write_bytes(b"")
    dem_path = scenario_root / "primary_dem.tif"
    _write_raster(
        dem_path,
        data=np.arange(12, dtype=np.float32).reshape(3, 4),
        crs=ESRI_103878_WKT,
        transform=from_origin(0.0, 60.0, 10.0, 10.0),
        nodata=None,
    )
    script_path = scenario_root / "morning-sun.py"
    script_path.write_text("print('x')\n", encoding="utf-8")

    monkeypatch.setattr(raster_transform, "is_running_under_job_runner", lambda: False)
    monkeypatch.delenv("LUNAR_NOTEBOOK_SCENARIO_ID", raising=False)
    monkeypatch.delenv("LUNAR_NOTEBOOK_SCENARIO_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    import sys
    main_module = sys.modules["__main__"]
    original = getattr(main_module, "__file__", None)
    setattr(main_module, "__file__", str(script_path))
    try:
        dem = raster_transform.scenario_dem()
    finally:
        if original is None:
            delattr(main_module, "__file__")
        else:
            setattr(main_module, "__file__", original)

    assert dem.grid is not None
    assert dem.grid.width == 4
    assert dem.grid.height == 3
    assert dem._node.params["scenario_root"] == str(scenario_root)


def test_scenario_dem_uses_workspace_root_for_notebook_id_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = (tmp_path / "workspace").resolve()
    scenario_root = workspace_root / "workspace_case"
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem_path = scenario_root / "primary_dem.tif"
    _write_raster(
        dem_path,
        data=np.arange(9, dtype=np.float32).reshape(3, 3),
        crs=ESRI_103878_WKT,
        transform=from_origin(0.0, 30.0, 10.0, 10.0),
        nodata=None,
    )

    monkeypatch.setattr(raster_transform, "is_running_under_job_runner", lambda: False)
    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("LUNAR_NOTEBOOK_SCENARIO_ID", "workspace_case")
    monkeypatch.delenv("LUNAR_NOTEBOOK_SCENARIO_ROOT", raising=False)

    dem = raster_transform.scenario_dem()

    assert dem.grid is not None
    assert dem._node.params["scenario_root"] == str(scenario_root)
    assert dem._node.params["dem_path"] == str(dem_path.resolve())
