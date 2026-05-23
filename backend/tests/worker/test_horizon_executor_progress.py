from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from backend.jobs.executors.horizons import execute_generate_horizons


def _artifact_registrar(**_kwargs: Any) -> None:
    return None


def _prepare_scenario(tmp_path: Path) -> tuple[Path, Path, Path]:
    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir(parents=True, exist_ok=True)
    dem = scenario_root / "primary_dem.tif"
    dem.write_bytes(b"fake-dem")
    horizons = scenario_root / "lighting" / "horizons"
    return scenario_root, dem, horizons


def test_execute_generate_horizons_forwards_native_progress(tmp_path: Path) -> None:
    scenario_root, dem, horizons = _prepare_scenario(tmp_path)
    emitted: list[dict[str, Any]] = []
    cancel_checks = 0

    class _Bridge:
        def GenerateHorizons(
            self,
            _scenario_root_dir: str,
            _dem_path: str,
            _surrounding_dem_paths: list[str],
            horizons_dir: str,
            _observer_elevation_meters: float,
            _overwrite_horizons: bool,
            _compress_horizons: bool,
            progress_callback,
            cancel_callback,
        ) -> None:
            nonlocal cancel_checks
            cancel_checks += 1
            assert cancel_callback() is False
            progress_callback(
                SimpleNamespace(
                    Percent=50.0,
                    Message="Generated 1/2 horizon patches.",
                    Stage="process_patches",
                    ProcessedPatches=1,
                    TotalPatches=2,
                    FileName="horizon_00000_00000_000.cbin",
                )
            )
            Path(horizons_dir, "horizon_00000_00000_000.cbin").write_bytes(b"horizon")

    moonlib = SimpleNamespace(MoonlibBridge=_Bridge)

    result = execute_generate_horizons(
        scenario_id="scenario-1",
        scenario_root_dir=str(scenario_root),
        dem_path=str(dem),
        horizons_dir=str(horizons),
        surrounding_dem_paths=None,
        observer_elevation_meters=0.0,
        overwrite_horizons=False,
        compress_horizons=False,
        moonlib_importer=lambda: moonlib,
        artifact_registrar=_artifact_registrar,
        emit_progress=emitted.append,
        is_cancel_requested=lambda: False,
    )

    assert result["scenario_id"] == "scenario-1"
    assert cancel_checks == 1
    assert emitted == [
        {
            "percent": 50.0,
            "message": "Generated 1/2 horizon patches.",
            "stage": "process_patches",
            "processed": 1,
            "total": 2,
            "file_name": "horizon_00000_00000_000.cbin",
        }
    ]


def test_execute_generate_horizons_falls_back_to_legacy_bridge_signature(tmp_path: Path) -> None:
    scenario_root, dem, horizons = _prepare_scenario(tmp_path)
    calls: list[int] = []

    class _Bridge:
        def GenerateHorizons(self, *args: Any) -> None:
            calls.append(len(args))
            if len(args) != 7:
                raise TypeError("legacy bridge accepts seven arguments")
            Path(args[3], "horizon_00000_00000_000.bin").write_bytes(b"horizon")

    moonlib = SimpleNamespace(MoonlibBridge=_Bridge)

    execute_generate_horizons(
        scenario_id="scenario-1",
        scenario_root_dir=str(scenario_root),
        dem_path=str(dem),
        horizons_dir=str(horizons),
        surrounding_dem_paths=None,
        observer_elevation_meters=0.0,
        overwrite_horizons=False,
        compress_horizons=False,
        moonlib_importer=lambda: moonlib,
        artifact_registrar=_artifact_registrar,
        emit_progress=lambda _payload: None,
        is_cancel_requested=lambda: False,
    )

    assert calls == [9, 7]
