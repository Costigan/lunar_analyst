from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from backend.services.artifact_catalog import register_artifact_output
from backend.worker.native_bootstrap import import_moonlib


def execute_generate_horizons(
    *,
    scenario_id: str,
    scenario_root_dir: str,
    dem_path: str,
    horizons_dir: str,
    surrounding_dem_paths: list[str] | None,
    observer_elevation_meters: float,
    overwrite_horizons: bool,
    compress_horizons: bool,
    moonlib_importer: Callable[[], Any] = import_moonlib,
    artifact_registrar: Callable[..., Any] = register_artifact_output,
    emit_progress: Callable[[dict[str, Any]], None] | None = None,
    is_cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    scenario_root = Path(scenario_root_dir).expanduser().resolve()
    dem = Path(dem_path).expanduser().resolve()
    horizons = Path(horizons_dir).expanduser().resolve()
    surrounding_paths = [
        Path(item).expanduser().resolve()
        for item in (surrounding_dem_paths or [])
        if str(item).strip()
    ]

    if not scenario_root.exists() or not scenario_root.is_dir():
        raise FileNotFoundError(f"Scenario root directory does not exist: {scenario_root}")
    if not dem.exists():
        raise FileNotFoundError(f"DEM file does not exist: {dem}")
    for surrounding_path in surrounding_paths:
        if not surrounding_path.exists():
            raise FileNotFoundError(f"Surrounding DEM file does not exist: {surrounding_path}")

    horizons.mkdir(parents=True, exist_ok=True)
    moonlib = moonlib_importer()
    bridge = moonlib.MoonlibBridge()
    dotnet_surrounding_dems: Any
    observer_elevation_arg: Any
    try:
        from System import Single, String  # type: ignore
        from System.Collections.Generic import List as DotNetList  # type: ignore

        dotnet_surrounding_dems = DotNetList[String]()
        for surrounding_path in surrounding_paths:
            dotnet_surrounding_dems.Add(String(str(surrounding_path)))
        observer_elevation_arg = Single(float(observer_elevation_meters))
    except ModuleNotFoundError:
        # Test environments can stub MoonlibBridge without pythonnet installed.
        dotnet_surrounding_dems = [str(path) for path in surrounding_paths]
        observer_elevation_arg = float(observer_elevation_meters)

    def _emit_progress_from_native(progress: Any) -> None:
        if emit_progress is None:
            return

        def _get(name: str, default: Any = None) -> Any:
            return getattr(progress, name, default)

        payload: dict[str, Any] = {
            "message": str(_get("Message", "Horizon generation progress.")),
        }
        percent = _get("Percent")
        if percent is not None:
            try:
                payload["percent"] = float(percent)
            except (TypeError, ValueError):
                pass
        stage = _get("Stage")
        if stage is not None:
            payload["stage"] = str(stage)
        processed = _get("ProcessedPatches")
        if processed is not None:
            try:
                payload["processed"] = int(processed)
            except (TypeError, ValueError):
                pass
        total = _get("TotalPatches")
        if total is not None:
            try:
                payload["total"] = int(total)
            except (TypeError, ValueError):
                pass
        file_name = _get("FileName")
        if file_name is not None:
            payload["file_name"] = str(file_name)
        emit_progress(payload)

    def _is_cancel_requested_for_native() -> bool:
        if is_cancel_requested is None:
            return False
        return bool(is_cancel_requested())

    try:
        bridge.GenerateHorizons(
            str(scenario_root),
            str(dem),
            dotnet_surrounding_dems,
            str(horizons),
            observer_elevation_arg,
            bool(overwrite_horizons),
            bool(compress_horizons),
            _emit_progress_from_native,
            _is_cancel_requested_for_native,
        )
    except TypeError:
        bridge.GenerateHorizons(
            str(scenario_root),
            str(dem),
            dotnet_surrounding_dems,
            str(horizons),
            observer_elevation_arg,
            bool(overwrite_horizons),
            bool(compress_horizons),
        )

    if bool(compress_horizons):
        if emit_progress is not None:
            emit_progress(
                {
                    "percent": 95.0,
                    "message": "Compressing horizon tiles.",
                    "stage": "compress",
                }
            )
        bridge.CompressHorizonsDirectory(
            str(horizons),
            True,
            False,
        )
        remaining_bin = list(horizons.glob("horizon_*.bin"))
        if remaining_bin:
            raise RuntimeError(
                "Compression requested but uncompressed horizon tiles remain: "
                f"{remaining_bin[0].name}"
            )

    size_bytes = sum(path.stat().st_size for path in horizons.rglob("*") if path.is_file())
    artifact_registrar(
        scenario_root_dir=scenario_root,
        scenario_id=scenario_id,
        job_type="generate_horizons",
        artifact_kind="horizons",
        artifact_path=horizons,
        size_bytes=size_bytes,
        metadata={
            "overwrite_horizons": bool(overwrite_horizons),
            "compress_horizons": bool(compress_horizons),
            "dem_path": str(dem),
            "surrounding_dem_paths": [str(path) for path in surrounding_paths],
            "observer_elevation_meters": float(observer_elevation_meters),
        },
    )

    return {
        "scenario_id": scenario_id,
        "scenario_root_dir": str(scenario_root),
        "dem_path": str(dem),
        "horizons_dir": str(horizons),
        "overwrite_horizons": bool(overwrite_horizons),
        "compress_horizons": bool(compress_horizons),
        "artifact_db_path": str((scenario_root / "scenario.db").resolve()),
    }
