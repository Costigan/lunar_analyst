from __future__ import annotations

from pathlib import Path
from typing import Any

BASE_SCENARIO_SELECTOR = "test_scenario"


def _remove_path(root: Path, relative_path: str) -> None:
    target = (root / relative_path).resolve()
    if target.exists():
        if target.is_dir():
            import shutil

            shutil.rmtree(target)
        else:
            target.unlink()


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


def _ensure_landing_sites_summary_csv(root: Path) -> None:
    target = (root / "landing_sites_summary.csv").resolve()
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
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _source_channels(prediction: dict[str, Any]) -> set[str]:
    refs = prediction.get("source_references", [])
    return {
        str((item or {}).get("channel", "")).strip()
        for item in refs
        if isinstance(item, dict)
    }


def test_dom_env_001(isolated_scenario: Any, run_turn: Any) -> None:
    scenario = isolated_scenario("dom_env_001", BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()

    prediction = run_turn(
        prompt="What are the main illumination constraints near the lunar south pole for surface operations over a 14-Earth-day period?",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "respond"
    assert str(prediction.get("response_text", "")).strip()
    channels = _source_channels(prediction)
    assert "domain" in channels
    assert "procedural" in channels


def test_dom_env_002(isolated_scenario: Any, run_turn: Any) -> None:
    scenario = isolated_scenario("dom_env_002", BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()

    prediction = run_turn(
        prompt="Explain key hazards associated with permanently shadowed regions and how they impact traverse planning.",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "respond"
    assert str(prediction.get("response_text", "")).strip()
    channels = _source_channels(prediction)
    assert "domain" in channels
    assert "procedural" in channels


def test_dom_env_003(isolated_scenario: Any, run_turn: Any) -> None:
    scenario = isolated_scenario("dom_env_003", BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()

    prediction = run_turn(
        prompt="How should we interpret slope and roughness thresholds when screening candidate landing sites?",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "respond"
    assert str(prediction.get("response_text", "")).strip()
    channels = _source_channels(prediction)
    assert "domain" in channels
    assert "procedural" in channels


def test_dom_mission_001(isolated_scenario: Any, run_turn: Any) -> None:
    scenario = isolated_scenario("dom_mission_001", BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()

    prediction = run_turn(
        prompt="Propose a high-level workflow to downselect landing zones from regional DEM and illumination products in Lunar Analyst.",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "respond"
    assert str(prediction.get("response_text", "")).strip()
    channels = _source_channels(prediction)
    assert "procedural" in channels
    assert "domain" in channels


def test_dom_mission_002(isolated_scenario: Any, run_turn: Any) -> None:
    scenario = isolated_scenario("dom_mission_002", BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()

    prediction = run_turn(
        prompt="What evidence package should we produce for a mission review board to justify a selected south-pole landing site?",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "respond"
    assert str(prediction.get("response_text", "")).strip()
    channels = _source_channels(prediction)
    assert "procedural" in channels
    assert "domain" in channels


def test_dom_mission_003(isolated_scenario: Any, run_turn: Any) -> None:
    scenario = isolated_scenario("dom_mission_003", BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()

    prediction = run_turn(
        prompt="Given uncertain illumination forecasts, how should mission planners make robust decisions without over-claiming confidence?",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "respond"
    assert str(prediction.get("response_text", "")).strip()
    channels = _source_channels(prediction)
    assert "domain" in channels
    assert "procedural" in channels


def test_dom_env_004(isolated_scenario: Any, run_turn: Any) -> None:
    scenario = isolated_scenario("dom_env_004", BASE_SCENARIO_SELECTOR)
    _ensure_binary_mask_from_dem(scenario.root, output_relative_path="landing_sites_slope5.tif")
    _ensure_dem_percentiles_csv(scenario.root)
    assert (scenario.root / "landing_sites_slope5.tif").exists()
    assert (scenario.root / "dem_percentiles.csv").exists()

    prediction = run_turn(
        prompt="Given landing_sites_slope5.tif and dem_percentiles.csv already exist in this scenario, explain how those two artifacts together constrain candidate landing-zone selection.",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "respond"
    assert str(prediction.get("response_text", "")).strip()
    channels = _source_channels(prediction)
    assert "domain" in channels
    assert "procedural" in channels


def test_dom_mission_004(isolated_scenario: Any, run_turn: Any) -> None:
    scenario = isolated_scenario("dom_mission_004", BASE_SCENARIO_SELECTOR)
    _ensure_binary_mask_from_dem(scenario.root, output_relative_path="hillshade_script.tif")
    _ensure_landing_sites_summary_csv(scenario.root)
    assert (scenario.root / "hillshade_script.tif").exists()
    assert (scenario.root / "landing_sites_summary.csv").exists()

    prediction = run_turn(
        prompt="Use existing hillshade_script.tif and landing_sites_summary.csv context to propose a mission review narrative for why one candidate site should be preferred.",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "respond"
    assert str(prediction.get("response_text", "")).strip()
    channels = _source_channels(prediction)
    assert "domain" in channels
    assert "procedural" in channels


def test_dom_mission_005(isolated_scenario: Any, run_turn: Any) -> None:
    scenario = isolated_scenario("dom_mission_005", BASE_SCENARIO_SELECTOR)
    assert (scenario.root / "dem.tif").exists()
    _remove_path(scenario.root, "slope_le5.tif")
    assert not (scenario.root / "slope_le5.tif").exists()

    prediction = run_turn(
        prompt="If slope_le5.tif is not present, what should the analyst do first before making any landing-site recommendation?",
        scenario_id=scenario.scenario_id,
    )
    assert prediction["mode"] == "respond"
    assert str(prediction.get("response_text", "")).strip()
    channels = _source_channels(prediction)
    assert "procedural" in channels
    assert "domain" in channels
