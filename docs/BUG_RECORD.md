# Bug Record

This file tracks significant bugs that were diagnosed and fixed.
Add new entries at the top.

## 2026-03-07 - Warped display rasters and notebook scripts mishandled validity and scenario-root resolution

### Symptom
- A generated hillshade could render with transparent holes inside valid dark-shadow regions after map-display reprojection.
- Reproduced with a byte hillshade created from `mons-mouton/morning-sun.py`.
- The same script, when launched from the Jobs Manager, initially produced a raster with the `test_scenario` DEM dimensions instead of the selected `mons-mouton` scenario dimensions.
- The same script, when run directly in VS Code, wrote `hillshade.morning-sun.tif` into `D:\projects\lunar_analyst\` instead of the scenario folder.

### Root Cause
- Display-derivative validity:
  - `backend/services/raster_delivery.py` could warp a source raster into a rectangular display derivative without encoding explicit validity when uncovered output pixels existed.
  - For byte rasters, uncovered warp collar pixels could land as numeric `0`, colliding with valid hillshade shadow values of `0`.
  - The map render path then had no reliable way to distinguish invalid output coverage from valid zero-valued pixels.
- Notebook job scenario resolution:
  - `backend.notebook.job_runner` correctly wrote notebook context for the selected scenario.
  - But `scenario_dem()` in `backend/jobs/raster_transform.py` resolved scenario identity only from `LUNAR_NOTEBOOK_SCENARIO_ID` / `LUNAR_NOTEBOOK_SCENARIO_ROOT`, then fell back to `test_scenario`.
  - It did not consult notebook job-runner context, so Jobs Manager runs could still read the wrong DEM.
- Standalone script output resolution:
  - `safe_scenario_relative_path()` validates a relative path string but does not anchor it to the scenario root.
  - Direct scripts that passed `Path(safe_scenario_relative_path(...))` into `write_output_raster()` wrote relative to the process working directory rather than the scenario directory.
  - Standalone scripts also needed scenario inference from the script path when neither notebook job context nor `LUNAR_NOTEBOOK_SCENARIO_*` environment variables were present.

### Solution
- Backend display delivery:
  - Updated `backend/services/raster_delivery.py` so warped display derivatives synthesize an explicit alpha band when the warp creates invalid output coverage.
  - Preserved display data-band sample values exactly, including valid byte value `0`.
  - Tagged derivatives with `LUNAR_DISPLAY_ALPHA_BAND` and exposed `alpha_band` through raster-stats delivery.
- Frontend raster styling:
  - Updated raster styling and layer hydration to honor explicit `alphaBand` metadata.
  - Cleared stale persisted `nodataCutoff` style values when raster stats report no nodata, so older layer state no longer masks valid zero-valued pixels.
- Notebook/runtime resolution:
  - Updated `backend/jobs/raster_transform.py` so `scenario_dem()` and related helpers use notebook job-runner context first.
  - Added standalone scenario-root inference from the executing script path (and cwd fallback) in `backend/notebook/runtime.py`.
  - Wrapped `backend/notebook/notebook_helper.py::write_output_raster()` so relative output paths are resolved against the active scenario root automatically.

### Validation
- Backend tests:
  - `python -m pytest backend/tests/worker/test_notebook_helper.py backend/tests/worker/test_raster_transform_runtime.py -q`
  - `python -m pytest backend/tests/worker/test_raster_transform_runtime.py -q`
- Frontend tests:
  - `npm run test -- src/__tests__/rasterStyle.test.ts src/__tests__/rasterStatsStyle.test.ts`
- Frontend build:
  - `npm run build`
- Manual outcome:
  - Running `morning-sun.py` from the Jobs Manager with `mons-mouton` selected produced the correct raster dimensions and correct rendering, with no transparent holes inside the valid rotated footprint.
  - Running the same script directly from the `mons-mouton` scenario now writes the output into that scenario directory instead of the repo root.

## 2026-03-05 - Some warped Float32 GeoTIFF layers stayed invisible because deferred `GDAL_NODATA` aborted OpenLayers source setup

### Symptom
- Some scenario raster layers rendered correctly in QGIS but were invisible in the browser map.
- Reproduced with:
  - `D:\lunar_analyst_scenarios\mons-malapert\slope.tif`
- Browser console showed:
  - `Field 'GDAL_NODATA' (42113) is deferred. Use loadValue() to load it asynchronously.`

### Root Cause
- The backend map-display warp path preserved nodata metadata on warped derivatives whenever the source raster had `nodata`, even if the warped output had no invalid pixels.
- For some TIFF layouts, `geotiff.js` treated `GDAL_NODATA` as a deferred field because the ASCII tag value was stored out-of-line in the TIFF IFD.
- OpenLayers `ol/source/GeoTIFF` called `getGDALNoData()` synchronously during source configuration.
- That synchronous read threw for deferred `GDAL_NODATA`, which aborted source initialization and left the layer invisible.
- The problem only appeared for some files because the internal TIFF layout determined whether `geotiff.js` had already materialized the tag value.

### Solution
- Backend:
  - Updated `backend/services/raster_delivery.py` so warped display derivatives now use a hybrid nodata policy.
  - A geometry fast path keeps nodata when the transformed source footprint clearly cannot fill the rectangular output raster.
  - Otherwise the backend inspects the warped output for invalid pixels and removes nodata metadata in-place when the result is fully valid.
  - Bumped the derivative hash version so old buggy derivatives are not reused.
- Frontend:
  - Added `backend/web/lunar_analyst/src/map/geotiffNodataPatch.ts` to patch `geotiff.js` so deferred `GDAL_NODATA` returns `null` instead of throwing.
  - Added scenario raster source error logging in `backend/web/lunar_analyst/src/map/mapController.ts` for better diagnostics if GeoTIFF setup still fails.

### Validation
- Backend tests:
  - `python -m pytest backend/tests/services/test_raster_delivery.py -q`
- Frontend tests:
  - `npm run test -- src/__tests__/geotiffNodataPatch.test.ts src/__tests__/rasterStyle.test.ts`
- Frontend build:
  - `npm run build`
- Manual outcome:
  - A nodata-stripped copy of the failing `mons-malapert` slope raster rendered correctly, confirming the diagnosis.
  - The implemented backend change prevents unnecessary nodata metadata on fully valid warped display rasters.
  - The frontend patch keeps direct-source GeoTIFFs from failing on the deferred-tag path.

## 2026-02-20 - Warped GeoTIFF nodata corners rendered black instead of transparent

### Symptom
- After loading a warped raster layer in the map, corner regions that should have been nodata/transparent appeared black.
- Reproduced with:
  - `D:\lunar_analyst_scenarios\test_scenario\lighting\lightmap_longest_sun_duration_20270901_20280301.tif`

### Root Cause
- Backend warping and nodata propagation were correct (`nodata=-9999` present in warped output).
- In the frontend render path, OpenLayers `GeoTIFF` composes tiles with an alpha band for nodata pixels.
- For nodata pixels, OpenLayers can emit band-1 value `0` with alpha `0`.
- Our style logic only masked using band-1 numeric nodata / under-range checks, so some nodata pixels were not masked and rendered as black.

### Solution
- Updated raster style masking to also treat alpha-band-zero pixels as transparent when nodata masking is active.
- Changes:
  - `backend/web/lunar_analyst/src/map/rasterStyle.ts`
  - `backend/web/lunar_analyst/src/__tests__/rasterStyle.test.ts`
- Rebuilt frontend assets so the backend-served `dist` bundle included the fix.

### Validation
- Frontend test:
  - `npm --prefix backend/web/lunar_analyst run test -- src/__tests__/rasterStyle.test.ts`
- Frontend build:
  - `npm --prefix backend/web/lunar_analyst run build`
- Manual outcome:
  - Warped nodata corner regions now render transparent rather than black.
