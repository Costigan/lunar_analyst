from __future__ import annotations

from pathlib import Path

from backend.notebook.notebook_helper import bool_param, create_moonlib_bridge, directory_file_stats
from backend.notebook.notebook_helper import get_context, is_cancelled, register_output_if_available
from backend.notebook.notebook_helper import report_progress, resolve_dem_path_from_params
from backend.notebook.notebook_helper import to_dotnet_string_list, write_json, resolve_scenario_relative_dir

ctx = get_context()
scenario_id = str(ctx.scenario_id)
scenario_root = Path(ctx.scenario_root_dir).resolve()
params = ctx.params if isinstance(ctx.params, dict) else {}

horizons_rel, horizons_dir = resolve_scenario_relative_dir(
    scenario_root=scenario_root,
    raw=str(params.get("horizons_relative_dir", "lighting/horizons")).strip(),
    default="lighting/horizons",
    create=True,
)

overwrite_horizons = bool_param(params, "overwrite_horizons", False)
compress_horizons = bool_param(params, "compress_horizons", True)

if is_cancelled():
    raise RuntimeError("Job cancelled before native horizons generation started.")

report_progress(percent=5.0, message="Bootstrapping native bridge runtime", stage="bootstrap")
bridge = create_moonlib_bridge(force_bootstrap=True)
from System import Single

dem_path = resolve_dem_path_from_params(
    scenario_root=scenario_root,
    scenario_id=scenario_id,
    params=params,
)
dem_rel = dem_path.relative_to(scenario_root).as_posix()

surrounding_dem_paths_raw = params.get("surrounding_dem_paths", [])
if not isinstance(surrounding_dem_paths_raw, list):
    raise ValueError("params.surrounding_dem_paths must be a list of paths.")
surrounding_dem_paths = to_dotnet_string_list(surrounding_dem_paths_raw)
observer_elevation_meters = float(params.get("observer_elevation_meters", 0.0))

report_progress(percent=30.0, message="Running MoonlibBridge.GenerateHorizons", stage="native")
bridge.GenerateHorizons(
    str(scenario_root),
    str(dem_path),
    surrounding_dem_paths,
    str(horizons_dir),
    Single(observer_elevation_meters),
    bool(overwrite_horizons),
    bool(compress_horizons),
)

if is_cancelled():
    raise RuntimeError("Job cancelled during native horizons generation.")

if compress_horizons:
    report_progress(percent=70.0, message="Compressing horizon tiles (.cbin)", stage="compress")
    bridge.CompressHorizonsDirectory(str(horizons_dir), True, False)
    remaining_bin = list(horizons_dir.glob("horizon_*.bin"))
    if remaining_bin:
        raise RuntimeError(
            "Compression requested but uncompressed horizon tiles remain: "
            f"{remaining_bin[0].name}"
        )

file_count, size_bytes = directory_file_stats(horizons_dir)

manifest_rel = f"{horizons_rel.rstrip('/')}/horizons_manifest.json"
manifest_path = (scenario_root / manifest_rel).resolve()
manifest = {
    "scenario_id": scenario_id,
    "scenario_root": str(scenario_root),
    "dem_relative_path": dem_rel,
    "horizons_relative_dir": horizons_rel,
    "overwrite_horizons": bool(overwrite_horizons),
    "compress_horizons": bool(compress_horizons),
    "file_count": file_count,
    "size_bytes": size_bytes,
}
write_json(manifest_path, manifest, indent=2, sort_keys=True)

register_output_if_available(
    relative_path=manifest_rel,
    kind="analysis",
    subkind="horizons_manifest",
    metadata={
        "source_dem": dem_rel,
        "horizons_relative_dir": horizons_rel,
        "file_count": file_count,
        "size_bytes": size_bytes,
    },
)
report_progress(percent=95.0, message="Horizons generation complete", stage="finalize")
print(manifest)
