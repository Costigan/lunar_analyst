from backend.notebook.notebook_helper import (
    scenario_dem, hillshade_raster, raster_let, write_output_raster,
    register_output_if_available, safe_scenario_relative_path
    )
from pathlib import Path
import os

#os.environ['LUNAR_NOTEBOOK_SCENARIO_ID'] = 'mons-mouton'

dem = scenario_dem()

# Low-angle "morning" light from the East
morning_sun = raster_let(
    dem=dem,
    shade=lambda r: hillshade_raster(r.dem, azimuth_deg=90.0, elevation_deg=15.0)
).eval(
    lambda r: r.shade
)

# 1. Setup paths (relative to the scenario root)
rel_path = "hillshade.morning-sun.tif"
target_path = Path(safe_scenario_relative_path(rel_path))

# 2. Get the target grid (georeferencing) from the Scenario DEM
target_grid = dem.grid  # This contains CRS, Transform, Width, and Height

# 3. Materialize the data
data = morning_sun.materialize()

# 4. Write to disk
write_output_raster(
    output_path=target_path,
    target_grid=target_grid,
    array=data,
    overwrite=True
)

# 5. Register the artifact so it shows up in the Layer Manager/Catalog
register_output_if_available(
    relative_path=rel_path,
    kind="raster",
    subkind="analysis_result",
    metadata={"description": "Flat areas above 2000m"}
)
