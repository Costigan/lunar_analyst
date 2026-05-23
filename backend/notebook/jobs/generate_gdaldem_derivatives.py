from __future__ import annotations

from pathlib import Path
from typing import Any

from osgeo import gdal

from backend.notebook.notebook_helper import register_output_if_available
from backend.notebook.notebook_helper import replace_output_file
from backend.notebook.notebook_helper import resolve_primary_dem_path
from backend.notebook.notebook_helper import resolve_scenario_identity_and_root, is_running_under_job_runner
from backend.worker.gdal_runtime import configure_gdal_runtime


def _run_derivative(
    *,
    dem_path: Path,
    output_path: Path,
    processing: str,
    options: Any,
) -> None:
    replace_output_file(output_path)
    ds = gdal.DEMProcessing(str(output_path), str(dem_path), processing, options=options)
    if ds is not None:
        ds = None
    if not output_path.exists():
        raise RuntimeError(f"{processing} output was not created: {output_path}")


scenario_id, scenario_root = (
    resolve_scenario_identity_and_root()
    if is_running_under_job_runner()
    else ("test_scenario", (Path.cwd() / "scenarios" / "test_scenario").resolve())
)

configure_gdal_runtime()
gdal.UseExceptions()

dem_path = resolve_primary_dem_path(
    scenario_root_dir=scenario_root,
    scenario_id=scenario_id,
)
dem_rel = dem_path.relative_to(scenario_root).as_posix()

common = gdal.DEMProcessingOptions(format="GTiff", computeEdges=True)
hillshade = gdal.DEMProcessingOptions(
    format="GTiff",
    zFactor=1.0,
    azimuth=315,
    altitude=45,
    computeEdges=True,
)

derivatives: list[tuple[str, str, Any]] = [
    ("hillshade", "hillshade.tif", hillshade),
    ("slope", "slope.tif", common),
    ("aspect", "aspect.tif", common),
    ("TRI", "tri.tif", common),
    ("TPI", "tpi.tif", common),
    ("roughness", "roughness.tif", common),
]

produced: list[str] = []
for processing, out_name, options in derivatives:
    out_path = (scenario_root / out_name).resolve()
    _run_derivative(
        dem_path=dem_path,
        output_path=out_path,
        processing=processing,
        options=options,
    )
    produced.append(out_name)
    register_output_if_available(
        relative_path=out_name,
        kind="raster",
        subkind=processing.lower(),
        render_mode="raster",
        metadata={"source_dem": dem_rel},
    )

payload = {
    "scenario_id": scenario_id,
    "scenario_root": str(scenario_root),
    "primary_dem": dem_rel,
    "generated": produced,
}
print(payload)
