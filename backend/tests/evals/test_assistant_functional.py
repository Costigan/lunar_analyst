from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from backend.contracts.models import CreateLayerStateRequest, RenderMode
from backend.core.crs_semantics import crs_semantically_equivalent

BASE_SCENARIO_SELECTOR = "test_scenario"


def _tool_names(prediction: dict[str, Any]) -> list[str]:
    names: list[str] = []
    calls = prediction.get("tool_calls", [])
    if isinstance(calls, list):
        for call in calls:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name", "") or "").strip()
            if name:
                names.append(name)
    return names


def _prediction_has_required_tool(prediction: dict[str, Any], required_tools: set[str]) -> bool:
    names = _tool_names(prediction)
    return any(name in required_tools for name in names)


def _append_warning(prediction: dict[str, Any], warning: dict[str, Any]) -> None:
    existing = prediction.get("warnings")
    if not isinstance(existing, list):
        existing = []
        prediction["warnings"] = existing
    existing.append(dict(warning))


def _ensure_required_tool_with_followup(
    *,
    prediction: dict[str, Any],
    run_turn: Any,
    case_id: str,
    scenario_id: str,
    required_tools: set[str],
    followup_prompt: str,
) -> dict[str, Any]:
    if _prediction_has_required_tool(prediction, required_tools):
        return prediction

    first_tools = _tool_names(prediction)
    first = first_tools[0] if first_tools else "<none>"
    _append_warning(
        prediction,
        {
            "type": "first_turn_missing_required_tool",
            "expected_any": sorted(required_tools),
            "first_turn_tools": first_tools,
            "actual": first,
        },
    )

    followup = run_turn(
        prompt=followup_prompt,
        scenario_id=scenario_id,
        turn_label=f"{case_id}.followup",
    )
    return followup


def _remove_path(root: Path, relative_path: str) -> None:
    target = (root / relative_path).resolve()
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()


def _wait_for_path(path: Path, timeout_seconds: float = 20.0) -> bool:
    deadline = time.time() + max(0.5, timeout_seconds)
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.2)
    return path.exists()


def _ensure_binary_mask_from_dem(root: Path, *, output_relative_path: str) -> None:
    target = (root / output_relative_path).resolve()
    if target.exists():
        return
    dem_path = (root / "dem.tif").resolve()
    assert dem_path.exists(), "dem.tif is required for test setup"

    import numpy as np
    import rasterio

    with rasterio.open(dem_path) as src:
        dem = src.read(1, masked=True)
        profile = src.profile.copy()
    data = np.where(dem.mask, 0, 1).astype("uint8")
    profile.update(dtype="uint8", count=1, nodata=0, compress="lzw")
    target.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(target, "w", **profile) as dst:
        dst.write(data, 1)


def _ensure_dem_percentiles_csv(root: Path) -> None:
    target = (root / "dem_percentiles.csv").resolve()
    if target.exists():
        return
    dem_path = (root / "dem.tif").resolve()
    assert dem_path.exists(), "dem.tif is required for test setup"

    import numpy as np
    import rasterio

    with rasterio.open(dem_path) as src:
        dem = src.read(1, masked=True).compressed()
    p10, p50, p90 = np.percentile(dem, [10, 50, 90]).tolist()
    target.write_text(
        "p10,p50,p90\n"
        f"{p10:.6f},{p50:.6f},{p90:.6f}\n",
        encoding="utf-8",
    )


def _ensure_landing_sites_summary_csv(root: Path, *, relative_path: str = "landing_sites_summary.csv") -> None:
    target = (root / relative_path).resolve()
    if target.exists():
        return
    _ensure_binary_mask_from_dem(root, output_relative_path="landing_sites_slope5.tif")

    import numpy as np
    import rasterio

    mask_path = (root / "landing_sites_slope5.tif").resolve()
    with rasterio.open(mask_path) as src:
        arr = src.read(1, masked=True).filled(0).astype("uint8")
        transform = src.transform
    pixel_area = abs(float(transform.a) * float(transform.e))
    classes, counts = np.unique(arr, return_counts=True)
    lines = ["class,pixel_count,area_m2"]
    for cls, count in zip(classes.tolist(), counts.tolist()):
        lines.append(f"{int(cls)},{int(count)},{float(count * pixel_area):.6f}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _raster_meta(path: Path) -> tuple[Any, int]:
    import rasterio

    with rasterio.open(path) as ds:
        return ds.crs, int(ds.count)


def _is_esri_103878_crs(crs: Any) -> bool:
    return crs_semantically_equivalent(crs, "ESRI:103878")


def _prediction_uses_queued_raster_transform(prediction: dict[str, Any]) -> bool:
    calls = prediction.get("tool_calls", [])
    if not isinstance(calls, list):
        return False
    for call in calls:
        if not isinstance(call, dict):
            continue
        if str(call.get("name", "")).strip() != "raster.transform":
            continue
        args = call.get("arguments", {})
        if not isinstance(args, dict):
            continue
        mode = str(args.get("mode", "queued")).strip().lower() or "queued"
        if mode == "queued":
            return True
    return False


def _ensure_nir_red_inputs(scenario_root: Path) -> None:
    dem = (scenario_root / "dem.tif").resolve()
    assert dem.exists()
    nir = (scenario_root / "nir.tif").resolve()
    red = (scenario_root / "red.tif").resolve()
    if not nir.exists():
        shutil.copy2(dem, nir)
    if not red.exists():
        shutil.copy2(dem, red)


def _resolve_file_id_for_relative_path(eval_runtime: Any, scenario_id: str, relative_path: str) -> str | None:
    nodes = eval_runtime.services.product_service.list_explorer_nodes(scenario_id, include_hidden=True)
    rel_norm = relative_path.replace("\\", "/").strip().lower()
    for node in nodes:
        node_rel = str(getattr(node, "relative_path", "") or "").replace("\\", "/").strip().lower()
        file_id = getattr(node, "file_id", None)
        if node_rel == rel_norm and isinstance(file_id, str) and file_id.strip():
            return file_id
    return None


def _ensure_layer_for_file(eval_runtime: Any, scenario_id: str, relative_path: str) -> None:
    eval_runtime.services.scenario_service.reconcile_scenario_filesystem(scenario_id, force=True)
    file_id = _resolve_file_id_for_relative_path(eval_runtime, scenario_id, relative_path)
    assert file_id is not None, f"Unable to resolve file_id for {relative_path}"

    existing = eval_runtime.services.layer_service.list_layers(scenario_id)
    for layer in existing:
        if str(layer.source_file_id) == str(file_id):
            return

    next_z = max([int(layer.z_index) for layer in existing], default=0) + 1
    eval_runtime.services.layer_service.create_layer(
        CreateLayerStateRequest(
            scenario_id=scenario_id,
            product_id=None,
            title=relative_path,
            visible=True,
            opacity=1.0,
            z_index=next_z,
            render_mode=RenderMode.RASTER,
            source_file_id=file_id,
            style={},
        )
    )


def test_func_script_raster_001(isolated_scenario: Any, run_turn: Any) -> None:
    case_id = "func_script_raster_001"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()
    _remove_path(scenario.root, "landing_sites_slope5.tif")
    assert not (scenario.root / "landing_sites_slope5.tif").exists()

    prediction = run_turn(
        prompt="Write and run a Python script that reads dem.tif, computes a slope mask for slope <= 5 degrees, and saves landing_sites_slope5.tif.",
        scenario_id=scenario.scenario_id,
    )
    prediction = _ensure_required_tool_with_followup(
        prediction=prediction,
        run_turn=run_turn,
        case_id=case_id,
        scenario_id=scenario.scenario_id,
        required_tools={"scenario.write_run_script"},
        followup_prompt="Now execute the requested task by calling scenario.write_run_script and create landing_sites_slope5.tif in this scenario.",
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"scenario.write_run_script"})

    output = scenario.root / "landing_sites_slope5.tif"
    assert _wait_for_path(output)
    crs, bands = _raster_meta(output)
    assert _is_esri_103878_crs(crs)
    assert bands >= 1


def test_func_script_raster_002(isolated_scenario: Any, run_turn: Any) -> None:
    case_id = "func_script_raster_002"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()
    _remove_path(scenario.root, "hillshade_script.tif")
    assert not (scenario.root / "hillshade_script.tif").exists()

    prediction = run_turn(
        prompt="Create and execute a Python script that computes hillshade from dem.tif and writes hillshade_script.tif.",
        scenario_id=scenario.scenario_id,
    )
    prediction = _ensure_required_tool_with_followup(
        prediction=prediction,
        run_turn=run_turn,
        case_id=case_id,
        scenario_id=scenario.scenario_id,
        required_tools={"scenario.write_run_script"},
        followup_prompt="Now execute via scenario.write_run_script and generate hillshade_script.tif in the scenario root.",
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"scenario.write_run_script"})

    output = scenario.root / "hillshade_script.tif"
    assert _wait_for_path(output)
    crs, bands = _raster_meta(output)
    assert _is_esri_103878_crs(crs)
    assert bands >= 1


def test_func_script_vector_001(isolated_scenario: Any, run_turn: Any) -> None:
    case_id = "func_script_vector_001"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()
    _remove_path(scenario.root, "flat_areas.geojson")
    assert not (scenario.root / "flat_areas.geojson").exists()

    prediction = run_turn(
        prompt="Write and run a Python script that polygonizes flat areas from dem.tif into flat_areas.geojson.",
        scenario_id=scenario.scenario_id,
    )
    prediction = _ensure_required_tool_with_followup(
        prediction=prediction,
        run_turn=run_turn,
        case_id=case_id,
        scenario_id=scenario.scenario_id,
        required_tools={"scenario.write_run_script"},
        followup_prompt="Now call scenario.write_run_script to generate flat_areas.geojson from dem.tif.",
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"scenario.write_run_script"})

    output = scenario.root / "flat_areas.geojson"
    assert _wait_for_path(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    features = payload.get("features", [])
    assert isinstance(features, list)
    assert len(features) >= 1


def test_func_script_vector_002(isolated_scenario: Any, run_turn: Any) -> None:
    case_id = "func_script_vector_002"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()
    _remove_path(scenario.root, "candidate_ridges.geojson")
    assert not (scenario.root / "candidate_ridges.geojson").exists()

    prediction = run_turn(
        prompt="Author and run a script that extracts candidate ridge lines from a slope raster and saves candidate_ridges.geojson.",
        scenario_id=scenario.scenario_id,
    )
    prediction = _ensure_required_tool_with_followup(
        prediction=prediction,
        run_turn=run_turn,
        case_id=case_id,
        scenario_id=scenario.scenario_id,
        required_tools={"scenario.write_run_script"},
        followup_prompt="Now call scenario.write_run_script and create candidate_ridges.geojson in this scenario.",
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"scenario.write_run_script"})

    output = scenario.root / "candidate_ridges.geojson"
    assert _wait_for_path(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    features = payload.get("features", [])
    assert isinstance(features, list)
    assert len(features) >= 1


def test_func_dsl_raster_001(isolated_scenario: Any, run_turn: Any) -> None:
    case_id = "func_dsl_raster_001"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()
    _remove_path(scenario.root, "below_zero.tif")
    assert not (scenario.root / "below_zero.tif").exists()

    prediction = run_turn(
        prompt="Run raster.calculate with expression dem <= 0 using input dem=dem.tif and output below_zero.tif.",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"raster.calculate"})

    output = scenario.root / "below_zero.tif"
    assert _wait_for_path(output)
    crs, bands = _raster_meta(output)
    assert _is_esri_103878_crs(crs)
    assert bands >= 1


def test_func_dsl_raster_002(isolated_scenario: Any, run_turn: Any) -> None:
    case_id = "func_dsl_raster_002"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()
    _remove_path(scenario.root, "slope_le5.tif")
    assert not (scenario.root / "slope_le5.tif").exists()

    prediction = run_turn(
        prompt="Run raster.calculate with expression slope(dem) <= 5 using dem=dem.tif and save slope_le5.tif.",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"raster.calculate"})

    output = scenario.root / "slope_le5.tif"
    assert _wait_for_path(output)
    crs, bands = _raster_meta(output)
    assert _is_esri_103878_crs(crs)
    assert bands >= 1


def test_func_dsl_raster_003(isolated_scenario: Any, run_turn: Any) -> None:
    case_id = "func_dsl_raster_003"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()
    _remove_path(scenario.root, "slope_not_gt5.tif")
    assert not (scenario.root / "slope_not_gt5.tif").exists()

    prediction = run_turn(
        prompt="Run raster.calculate with expression not(slope(dem) > 5) using dem=dem.tif and save slope_not_gt5.tif.",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"raster.calculate"})

    output = scenario.root / "slope_not_gt5.tif"
    assert _wait_for_path(output)
    crs, bands = _raster_meta(output)
    assert _is_esri_103878_crs(crs)
    assert bands >= 1


def test_func_dsl_transform_001(isolated_scenario: Any, run_turn: Any) -> None:
    case_id = "func_dsl_transform_001"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    _ensure_nir_red_inputs(scenario.root)
    assert (scenario.root / "nir.tif").exists()
    assert (scenario.root / "red.tif").exists()
    _remove_path(scenario.root, "nd_index.tif")
    assert not (scenario.root / "nd_index.tif").exists()

    prediction = run_turn(
        prompt=(
            "Use raster.transform in immediate mode to compute nd=(nir-red)/(nir+red) "
            "from nir.tif and red.tif and write nd_index.tif."
        ),
        scenario_id=scenario.scenario_id,
    )
    if _prediction_uses_queued_raster_transform(prediction):
        prediction = run_turn(
            prompt=(
                "Re-run raster.transform in immediate mode for the same scenario and write nd_index.tif "
                "from nir.tif and red.tif now."
            ),
            scenario_id=scenario.scenario_id,
            turn_label=f"{case_id}.immediate_followup",
        )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"raster.transform"})

    output = scenario.root / "nd_index.tif"
    assert _wait_for_path(output)
    crs, bands = _raster_meta(output)
    assert _is_esri_103878_crs(crs)
    assert bands >= 1


def test_func_manipulate_001(isolated_scenario: Any, run_turn: Any, eval_runtime: Any) -> None:
    case_id = "func_manipulate_001"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    _ensure_binary_mask_from_dem(scenario.root, output_relative_path="slope_le5.tif")
    assert (scenario.root / "slope_le5.tif").exists()
    _ensure_layer_for_file(eval_runtime, scenario.scenario_id, "slope_le5.tif")

    prediction = run_turn(
        prompt="Display slope_le5.tif as a visible layer at 50% opacity.",
        scenario_id=scenario.scenario_id,
    )
    prediction = _ensure_required_tool_with_followup(
        prediction=prediction,
        run_turn=run_turn,
        case_id=case_id,
        scenario_id=scenario.scenario_id,
        required_tools={"layer.update_state", "layer.set_state"},
        followup_prompt="Now update the existing slope_le5.tif layer state to visible=true and opacity=0.5.",
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"layer.update_state", "layer.set_state"})

    layers = eval_runtime.services.layer_service.list_layers(scenario.scenario_id)
    target = None
    for layer in layers:
        try:
            rec = eval_runtime.services.file_service.get_file(layer.source_file_id)
            if str(rec.relative_path) == "slope_le5.tif":
                target = layer
                break
        except Exception:
            continue
    assert target is not None
    assert bool(target.visible) is True
    assert abs(float(target.opacity) - 0.5) <= 1e-6


def test_func_manipulate_002(isolated_scenario: Any, run_turn: Any, eval_runtime: Any) -> None:
    case_id = "func_manipulate_002"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    _ensure_binary_mask_from_dem(scenario.root, output_relative_path="hillshade_script.tif")
    assert (scenario.root / "hillshade_script.tif").exists()
    _ensure_layer_for_file(eval_runtime, scenario.scenario_id, "hillshade_script.tif")

    prediction = run_turn(
        prompt="Hide the hillshade_script.tif layer.",
        scenario_id=scenario.scenario_id,
    )
    prediction = _ensure_required_tool_with_followup(
        prediction=prediction,
        run_turn=run_turn,
        case_id=case_id,
        scenario_id=scenario.scenario_id,
        required_tools={"layer.update_state", "layer.set_state"},
        followup_prompt="Now update the existing hillshade_script.tif layer state to visible=false.",
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"layer.update_state", "layer.set_state"})

    layers = eval_runtime.services.layer_service.list_layers(scenario.scenario_id)
    target = None
    for layer in layers:
        try:
            rec = eval_runtime.services.file_service.get_file(layer.source_file_id)
            if str(rec.relative_path) == "hillshade_script.tif":
                target = layer
                break
        except Exception:
            continue
    assert target is not None
    assert bool(target.visible) is False


def test_func_manipulate_003(isolated_scenario: Any, run_turn: Any, eval_runtime: Any) -> None:
    case_id = "func_manipulate_003"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    _ensure_binary_mask_from_dem(scenario.root, output_relative_path="landing_sites_slope5.tif")
    assert (scenario.root / "landing_sites_slope5.tif").exists()
    assert (scenario.root / "dem.tif").exists()
    _ensure_layer_for_file(eval_runtime, scenario.scenario_id, "landing_sites_slope5.tif")
    _ensure_layer_for_file(eval_runtime, scenario.scenario_id, "dem.tif")

    prediction = run_turn(
        prompt="Bring landing_sites_slope5.tif above dem.tif in the layer order.",
        scenario_id=scenario.scenario_id,
    )
    prediction = _ensure_required_tool_with_followup(
        prediction=prediction,
        run_turn=run_turn,
        case_id=case_id,
        scenario_id=scenario.scenario_id,
        required_tools={"layer.reorder"},
        followup_prompt="Now call layer.reorder to place landing_sites_slope5.tif above dem.tif.",
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"layer.reorder"})

    layers = eval_runtime.services.layer_service.list_layers(scenario.scenario_id)
    above = None
    below = None
    for layer in layers:
        try:
            rec = eval_runtime.services.file_service.get_file(layer.source_file_id)
            rel = str(rec.relative_path)
            if rel == "landing_sites_slope5.tif":
                above = layer
            elif rel == "dem.tif":
                below = layer
        except Exception:
            continue
    assert above is not None
    assert below is not None
    assert int(above.z_index) > int(below.z_index)


def test_func_table_001(isolated_scenario: Any, run_turn: Any) -> None:
    case_id = "func_table_001"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    _ensure_binary_mask_from_dem(scenario.root, output_relative_path="landing_sites_slope5.tif")
    assert (scenario.root / "landing_sites_slope5.tif").exists()
    _remove_path(scenario.root, "landing_sites_summary.csv")
    assert not (scenario.root / "landing_sites_summary.csv").exists()

    prediction = run_turn(
        prompt="Generate a table summarizing area and pixel count for classes in landing_sites_slope5.tif and save landing_sites_summary.csv.",
        scenario_id=scenario.scenario_id,
    )
    prediction = _ensure_required_tool_with_followup(
        prediction=prediction,
        run_turn=run_turn,
        case_id=case_id,
        scenario_id=scenario.scenario_id,
        required_tools={"scenario.write_run_script"},
        followup_prompt="Now execute via scenario.write_run_script and create landing_sites_summary.csv with class, pixel_count, area_m2 columns.",
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"scenario.write_run_script"})

    output = scenario.root / "landing_sites_summary.csv"
    assert _wait_for_path(output)
    header = output.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    assert "class" in header
    assert "pixel_count" in header
    assert "area_m2" in header


def test_func_table_002(isolated_scenario: Any, run_turn: Any) -> None:
    case_id = "func_table_002"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()
    _remove_path(scenario.root, "dem_percentiles.csv")
    assert not (scenario.root / "dem_percentiles.csv").exists()

    prediction = run_turn(
        prompt="Create a CSV table of elevation percentiles (p10,p50,p90) for dem.tif named dem_percentiles.csv.",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"scenario.write_run_script"})

    output = scenario.root / "dem_percentiles.csv"
    assert _wait_for_path(output)
    header = output.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    assert "p10" in header
    assert "p50" in header
    assert "p90" in header


def test_func_plot_001(isolated_scenario: Any, run_turn: Any) -> None:
    case_id = "func_plot_001"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()
    _remove_path(scenario.root, "dem_histogram.png")
    assert not (scenario.root / "dem_histogram.png").exists()

    prediction = run_turn(
        prompt="Generate and save a histogram plot of elevations from dem.tif as dem_histogram.png.",
        scenario_id=scenario.scenario_id,
    )
    prediction = _ensure_required_tool_with_followup(
        prediction=prediction,
        run_turn=run_turn,
        case_id=case_id,
        scenario_id=scenario.scenario_id,
        required_tools={"scenario.write_run_script"},
        followup_prompt="Now call scenario.write_run_script with a valid relative_path and create dem_histogram.png.",
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"scenario.write_run_script"})

    output = scenario.root / "dem_histogram.png"
    assert _wait_for_path(output)
    assert output.suffix.lower() == ".png"


def test_func_script_raster_003(isolated_scenario: Any, run_turn: Any) -> None:
    case_id = "func_script_raster_003"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()
    _remove_path(scenario.root, "analysis/derived/terrain_mask.tif")
    assert not (scenario.root / "analysis/derived/terrain_mask.tif").exists()

    prediction = run_turn(
        prompt="Write and run a Python script that creates analysis/derived/terrain_mask.tif from dem.tif where elevation > 0.",
        scenario_id=scenario.scenario_id,
    )
    prediction = _ensure_required_tool_with_followup(
        prediction=prediction,
        run_turn=run_turn,
        case_id=case_id,
        scenario_id=scenario.scenario_id,
        required_tools={"scenario.write_run_script"},
        followup_prompt="Now execute via scenario.write_run_script and generate analysis/derived/terrain_mask.tif.",
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"scenario.write_run_script"})

    output = scenario.root / "analysis/derived/terrain_mask.tif"
    assert _wait_for_path(output)
    crs, bands = _raster_meta(output)
    assert _is_esri_103878_crs(crs)
    assert bands >= 1


def test_func_table_003(isolated_scenario: Any, run_turn: Any) -> None:
    case_id = "func_table_003"
    scenario = isolated_scenario(case_id, BASE_SCENARIO_SELECTOR)
    _ensure_binary_mask_from_dem(scenario.root, output_relative_path="landing_sites_slope5.tif")
    _ensure_landing_sites_summary_csv(scenario.root)
    _ensure_dem_percentiles_csv(scenario.root)
    assert (scenario.root / "landing_sites_slope5.tif").exists()
    _remove_path(scenario.root, "reports/landing_sites_summary_v2.csv")
    assert not (scenario.root / "reports/landing_sites_summary_v2.csv").exists()

    prediction = run_turn(
        prompt="Create and run a script that writes reports/landing_sites_summary_v2.csv summarizing class counts for landing_sites_slope5.tif.",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "tool_call"
    assert _prediction_has_required_tool(prediction, {"scenario.write_run_script"})

    output = scenario.root / "reports/landing_sites_summary_v2.csv"
    assert _wait_for_path(output)
    header = output.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    assert "class" in header
    assert ("pixel_count" in header) or ("count" in header)
