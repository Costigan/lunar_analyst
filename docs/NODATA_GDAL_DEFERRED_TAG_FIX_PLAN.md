# Plan: Deferred `GDAL_NODATA` Tag Fix for Map Display Derivatives

## Goal

Fix the browser map rendering failure where some warped display GeoTIFFs remain invisible because the frontend `OpenLayers + geotiff.js` path throws while reading `GDAL_NODATA` during source configuration.

This plan uses:

- a backend fix to make `NoData` metadata reliably indicate that invalid pixels are actually present in the display derivative, and
- a frontend belt-and-suspenders change that prefers a fast path when possible and degrades safely when `GDAL_NODATA` is deferred or unavailable.

The backend fix should push more rasters into the frontend fast path.

## Bug Summary

Observed failure:

- A warped map-display GeoTIFF can carry `GDAL_NODATA=-9999` even when all output pixels are valid.
- In some TIFF layouts, `geotiff.js` treats `GDAL_NODATA` as a deferred field.
- `OpenLayers` calls `getGDALNoData()` synchronously during `ol/source/GeoTIFF` source configuration.
- That synchronous call throws, source configuration aborts, and the layer remains invisible.

Confirmed behavior in this repo:

- `mons-malapert/slope.tif` failed in-browser when the warped display derivative carried `GDAL_NODATA`.
- Rewriting the same raster without nodata metadata made it render correctly.
- A different Float32 slope derivative in `test_scenario` still rendered with `NoData=-9999`, which indicates the bug depends on TIFF layout / deferred-field behavior, not just the semantic presence of nodata.

## Desired Contract

For map-display derivatives:

- If the derivative contains invalid pixels, it should carry nodata metadata.
- If the derivative contains no invalid pixels, it should not carry nodata metadata.

This keeps metadata semantically honest and avoids the browser failure mode for fully-valid display rasters.

## Frontend Plan

### Fast Path

Use the current normal `ol/source/GeoTIFF` path when nodata metadata is either:

- absent, or
- readable without throwing.

In this path, source creation should proceed normally and style setup can still use range and optional nodata controls.

### Slow Path

If nodata access throws during source configuration:

- catch the failure in the frontend map layer creation path,
- log a structured warning with file/layer identifiers,
- retry with a conservative fallback source configuration that does not depend on reading `GDAL_NODATA` synchronously.

Fallback expectations:

- the layer should still render if the raster is otherwise valid,
- transparency handling may be reduced for that one layer,
- the UI should prefer visible data over complete nodata semantics when the library stack cannot read nodata safely.

### Frontend Scope

Primary file:

- `backend/web/lunar_analyst/src/map/mapController.ts`

Possible supporting changes:

- `backend/web/lunar_analyst/src/map/rasterStyle.ts`

### Frontend Acceptance

- Browser no longer leaves the layer invisible when `GDAL_NODATA` is deferred.
- Console shows a controlled warning instead of an uncaught source-initialization failure.
- Existing layers that already render continue to render.

## Backend Plan

### Core Policy

Change display-derivative generation so that nodata metadata is preserved only when invalid output pixels are actually present.

This is presentation-layer normalization for browser map delivery. It does not change the scientific source raster.

### Fast Path: Geometric Decision

Before trusting geometry, verify that the source has no intrinsic invalid-pixel semantics:

- `src.nodata is None`
- source mask flags indicate all-valid

Do not treat alpha as validity for this decision.

If the source is all-valid, use geometry to predict whether reprojection must create invalid output pixels:

1. Build the source raster footprint polygon from bounds.
2. Densify the footprint edges before transformation.
3. Transform the footprint to target CRS.
4. Compare the transformed footprint to the target output rectangle implied by `calculate_default_transform(...)`.

Decision:

- If the transformed footprint clearly does not fill the target rectangle, preserve nodata metadata.
- If the transformed footprint clearly fills the target rectangle, clear nodata metadata.
- If the result is ambiguous, use the fallback path.

### Slow Path: Fallback Validation

Use fallback validation only when:

- the source is all-valid, and
- the geometric test is inconclusive.

Fallback rule:

- determine whether the warped output actually contains invalid pixels,
- preserve nodata if invalid pixels exist,
- clear nodata otherwise.

Implementation note:

- prefer capturing this during warp or with a targeted validation step,
- avoid a blanket extra full-raster pass for every derivative.

### Source Validity Override

If the source already carries nodata or a nontrivial validity mask:

- geometry alone is not sufficient,
- preserve nodata behavior by default,
- or use explicit fallback validation if later optimization is needed.

This avoids false-clearing nodata when invalid pixels originate in the source rather than from reprojection geometry.

### Backend Scope

Primary file:

- `backend/services/raster_delivery.py`

Secondary file if shared COG writing policy is updated:

- `backend/services/cog.py`

### Backend Acceptance

- Fully-valid warped display rasters do not carry nodata metadata.
- Warped rasters with genuine outside-footprint gaps still carry nodata metadata.
- Source rasters with existing nodata or nontrivial masks do not lose nodata semantics accidentally.

## Combined Behavior

With both changes in place:

- backend makes nodata metadata more truthful for display derivatives,
- frontend remains resilient if an upstream library still exposes a deferred nodata tag,
- more rasters should follow the frontend fast path because fewer fully-valid display derivatives will advertise nodata unnecessarily.

## Tests

### Backend Tests

- all-valid source, geometry predicts warped edge gaps: nodata preserved
- all-valid source, geometry predicts full coverage: nodata cleared
- all-valid source, geometry inconclusive, fallback finds no invalid pixels: nodata cleared
- source with nodata metadata: nodata preserved
- source with nontrivial validity mask: nodata preserved

### Frontend Tests

- layer creation succeeds when nodata metadata is absent
- layer creation succeeds when nodata metadata is present and readable
- layer creation degrades to fallback path when nodata access throws

### Regression Checks

- `mons-malapert` slope display derivative renders in-browser
- a raster that truly needs edge transparency still renders correctly with nodata preserved
- existing working Float32 slope cases remain visible

## Manual Verification

1. Regenerate the `mons-malapert` slope display derivative.
2. Confirm the derivative no longer carries nodata metadata when the warped output is fully valid.
3. Load the layer in the browser and confirm it renders without the deferred-tag error.
4. Load a raster known to produce warped-edge invalid pixels and confirm transparency behavior remains correct.

## Risks

- False positive `safe_to_clear_nodata` decisions would hide legitimate invalid-pixel semantics.
- Frontend fallback must not silently suppress true data problems without logging.

## Rollback

- Backend: restore unconditional nodata propagation for display derivatives.
- Frontend: remove fallback retry path and return to current strict source creation behavior.

Rollback is straightforward because both changes are localized to map-display raster handling.
