# Plan: Explicit Alpha/Validity for Warped Map Display Derivatives

## Goal

Fix the map-display bug where warped rasters can lose correct transparency semantics because invalid output coverage is not encoded separately from raster sample values.

The preferred fix is:

- keep display sample values unchanged,
- synthesize explicit validity for warped display derivatives when needed, and
- have the frontend honor that validity channel directly.

This plan applies to all raster datatypes, including byte hillshades.

## Bug Summary

Current behavior:

- `backend/services/raster_delivery.py` warps map-display derivatives to `ESRI:103878`.
- If the source raster has no nodata metadata, the warped derivative can still contain uncovered collar pixels.
- Those uncovered pixels currently do not get a guaranteed explicit validity channel.
- For byte hillshades, uncovered pixels can collide with valid sample value `0`, producing hole-punched shadows in the browser.

Important framing:

- This is a display-encoding bug.
- It should be fixed in the map-delivery derivative path, not by changing scientific source rasters or remapping valid data values.

## Desired Contract

For map-display derivatives:

- Validity must be representable independently from sample values.
- Valid sample values must remain valid, including `0` for byte hillshades.
- If the warped derivative is fully valid, the backend may keep the simpler no-alpha fast path.
- If the warped derivative contains invalid output coverage, the derivative must carry explicit display validity.

Preferred representation:

- synthesize an alpha band for display derivatives when invalid output pixels are present.

Acceptable equivalent:

- a mask representation that the browser path can consume with equal reliability.

## Preferred Backend Policy

### Fully Valid Outputs

If the warped output is fully valid:

- keep the derivative compact,
- avoid adding an alpha band,
- clear nodata metadata when it is not semantically needed.

This preserves the current fast path and avoids unnecessary browser complexity.

### Outputs With Invalid Coverage

If the warped output contains invalid pixels because of reprojection geometry or source validity:

- preserve the display data bands unchanged,
- synthesize an explicit alpha/validity channel for the display derivative,
- make transparency depend on that channel rather than on any pixel-value sentinel.

### No Value Remapping

Do not:

- clamp byte data from `[0, 255]` to `[1, 255]`,
- reserve `0` as a special display-only value,
- or otherwise change valid raster sample values just to express transparency.

## Backend Implementation Plan

Primary file:

- `backend/services/raster_delivery.py`

Supporting tests:

- `backend/tests/services/test_raster_delivery.py`

### Step 1: Bump Display Derivative Policy Version

Update the display-derivative hash/version so previously generated buggy derivatives are not reused after the fix.

Acceptance:

- old derivatives are naturally invalidated and regenerated.

### Step 2: Detect Whether Output Validity Is Needed

During derivative generation, determine whether the warped output contains invalid pixels.

Cases that should trigger explicit validity:

- geometric collar/uncovered output created by reprojection,
- source nodata propagated into the warped output,
- source mask/invalid regions propagated into the warped output.

Acceptance:

- the code makes a deterministic yes/no decision: fully valid fast path vs explicit-validity path.

### Step 3: Synthesize Display Alpha When Needed

For derivatives that are not fully valid:

- generate an alpha band representing valid vs invalid output coverage,
- preserve data-band samples as-is,
- ensure invalid pixels become transparent through alpha rather than value collision.

Implementation notes:

- Prefer a backend-owned generation path that creates the alpha band directly from the warped-validity mask.
- Avoid relying on implicit browser heuristics around `GDAL_NODATA`.
- Keep the implementation scoped to map-display derivatives only.

Acceptance:

- a byte hillshade with valid zeros remains opaque at zero-valued interior pixels,
- uncovered collar regions become transparent,
- the same approach works for float and other datatypes.

### Step 4: Define Derivative Metadata Contract

Document and implement the backend contract for alpha-bearing display derivatives.

Questions the implementation must settle:

- whether alpha is appended as a second band for single-band rasters and as the last band for multiband rasters,
- whether nodata metadata is still kept, cleared, or treated as secondary when alpha is present,
- whether any render hint is needed for frontend layer setup.

Acceptance:

- the derivative layout is explicit and stable enough to test.

## Frontend Implementation Plan

Primary files:

- `backend/web/lunar_analyst/src/map/rasterStyle.ts`
- `backend/web/lunar_analyst/src/map/mapController.ts`

Supporting tests:

- `backend/web/lunar_analyst/src/__tests__/rasterStyle.test.ts`

### Step 5: Honor Explicit Alpha Independently of Nodata Metadata

Update the raster style/render path so transparency can follow an explicit alpha/validity band even when nodata metadata is absent or cleared.

The key requirement:

- valid sample value `0` must stay visible unless alpha says otherwise.

Acceptance:

- style masking no longer depends on a pixel-value collision to hide invalid warped regions.

### Step 6: Verify OpenLayers/GeoTIFF Source Behavior

Confirm how `ol/source/GeoTIFF` exposes alpha-bearing TIFFs in the current stack and adjust the style band selection accordingly.

Acceptance:

- layer creation remains stable for both old no-alpha display derivatives and new alpha-bearing ones.

## Test Plan

### Backend Tests

Add service-level coverage for:

- byte raster with no source nodata and warped uncovered collar:
  - derivative uses explicit validity,
  - valid zero-valued interior pixels remain valid,
  - uncovered regions are invalid/transparent.
- float raster with equivalent geometry:
  - same validity behavior without datatype-specific special casing.
- fully valid warped output:
  - no unnecessary alpha path is taken.

### Frontend Tests

Add frontend coverage for:

- style generation that respects alpha-bearing display derivatives,
- zero-valued byte pixels remaining visible when alpha indicates valid coverage,
- backward compatibility with existing single-band nodata-based layers.

### Manual Verification

Manual checks should include:

1. Load an oblique/rotated byte hillshade that contains valid zero-valued shadows.
2. Confirm collar regions are transparent.
3. Confirm interior shadow pixels with value `0` remain visible.
4. Repeat with a non-byte raster to confirm the fix is datatype-agnostic.

## Risks

- OpenLayers `GeoTIFF` band ordering or alpha handling may not behave exactly as assumed, requiring a small frontend adaptation.
- Generating alpha-bearing derivatives may increase file size for warped outputs that contain invalid regions.
- Reusing existing nodata behavior carelessly could reintroduce the deferred-`GDAL_NODATA` browser issue fixed on 2026-03-05.

## Rollback Plan

Rollback is straightforward because this is confined to display derivatives:

- restore the prior display-derivative generation path,
- bump the derivative policy version again if needed,
- regenerate display derivatives.

Source rasters and scenario-managed scientific artifacts are not mutated by this plan.

## Out of Scope

- changing source raster sample values,
- changing scientific hillshade generation,
- changing notebook/native raster outputs outside the map-display derivative path,
- broad frontend rendering redesign unrelated to display-validity handling.
