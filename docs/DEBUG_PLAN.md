# Horizon/Lightmap Artifact Debug Plan

## Goal

Identify which horizon-generation or lightmap sun-fraction approximations are producing structured shadow artifacts in the debug scenario and related cases.

Observed artifact classes:

- 8x8 pixel brightness steps.
- 128x128 pixel tile-scale steps.
- Bands roughly orthogonal to sunlight direction, spaced about 24 pixels apart.
- Artifacts are most visible in long, gradual sun-shadow transitions and less visible in short transitions.

This document is both the current debug plan and a record of evidence gathered so far. The current strongest finding is that the 128x128 seam artifact is produced by patch-local quadtree horizon generation, not by `BuilderSunFraction`, not by true terrain discontinuity, and not primarily by hierarchical culling.

## Algorithms Under Test

### Reference Horizon Algorithm

File: `native/new_horizon/moonlib/horizon/ReferenceHorizonGenerator.cs`

Important code locations:

- `ReferenceHorizonGenerator.GenerateFromPixel(...)`: `native/new_horizon/moonlib/horizon/ReferenceHorizonGenerator.cs:78`
- Main horizon loop: `native/new_horizon/moonlib/horizon/ReferenceHorizonGenerator.cs:110`
- Per-azimuth oversampling: `native/new_horizon/moonlib/horizon/ReferenceHorizonGenerator.cs:143`
- Ray walk through nested DEMs: `native/new_horizon/moonlib/horizon/ReferenceHorizonGenerator.cs:154`
- Switch to next coarser DEM when the ray leaves the current DEM: `native/new_horizon/moonlib/horizon/ReferenceHorizonGenerator.cs:162`
- Terrain sample and caster vector construction: `native/new_horizon/moonlib/horizon/ReferenceHorizonGenerator.cs:174`
- Slope/max-horizon update: `native/new_horizon/moonlib/horizon/ReferenceHorizonGenerator.cs:182`
- Error-bounded step spacing: `native/new_horizon/moonlib/horizon/ReferenceHorizonGenerator.cs:255`

Reference algorithm summary:

- For one observer pixel, convert pixel position to lat/lon and a Moon ME observer vector.
- For each of 1440 horizon bins, oversample within the azimuth bin.
- March along the ray in Moon ME using an error-bounded distance sequence.
- At each sample, convert the sample point back to DEM row/column, switching from fine DEMs to coarser surrounding DEMs when the ray exits the current DEM.
- Sample terrain elevation, convert the terrain point to the observer local frame, compute `z / horizontal_distance`, and keep the maximum slope.
- Convert the maximum slope to a horizon angle.

Important approximations in the reference algorithm:

- It is still a sampled ray walk, not an analytic terrain horizon.
- Step spacing grows with distance, bounded by an angular error budget.
- It samples each terrain point bilinearly through `ElevationMap.GetElevation` behavior.
- It oversamples azimuth bins, but the final horizon is still 1440 samples.

This is slow but is currently treated as the diagnostic reference for these artifacts.

### QuadTree/GPU Horizon Algorithm

File: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs`

Important code locations:

- Constructor and hierarchy flag: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:310`
- Pipelined 128x128 patch generation: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:770`
- Fixed 128x128 patch size in the pipeline: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:797`
- Subpatch ray segment generation for each patch: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:871`
- GPU patch launch using subpatch kernel: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:916`
- Async patch kernel setup and debug flags: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:1029`
- Subpatch kernel launch using `DEFAULT_SUBPATCH_SIZE`: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:1097`
- Subpatch ray segment calculation: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:1425`
- Subpatch center selection: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:1491`
- Single-patch/non-pipelined path used by `GenerateHorizons(...)`: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:1843`
- Non-subpatch ray-casting path: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:1885`
- Subpatch GPU kernel: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:3113`
- Pixel-to-subpatch selection in the kernel: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:3133`
- Translation from subpatch center ray to pixel ray: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:3145`
- Integer grid-convergence bin correction: `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:3160`

QuadTree algorithm summary for production patch generation:

- The production path processes primary DEM patches in 128x128 tiles.
- It builds or loads multiresolution DEM pyramids once per DEM.
- For each 128x128 tile, it computes ray-segment approximations for a grid of subpatches. The default subpatch size is currently 8 pixels.
- Each subpatch has ray polynomial/segment data for each azimuth and DEM pass.
- The GPU kernel runs for each `(pixel, azimuth)` pair. The pixel selects the ray segment for its subpatch, translates the subpatch-center ray by pixel offset, marches through the pyramid, and updates the horizon angle.
- Multiple DEM passes are combined by taking the maximum horizon angle.
- The kernel writes horizon bins with a rounded integer grid-convergence correction.

Important approximations in the QuadTree algorithm:

- One fitted ray geometry is shared by every pixel inside an 8x8 subpatch.
- Subpatch ray geometry is fit from the subpatch center and then translated to neighboring pixels.
- Patch generation is local to 128x128 tiles, so adjacent pixels across a tile seam use different patch-local ray segment sets.
- Grid convergence is approximated linearly from the 128x128 tile center and applied as an integer horizon-bin shift.
- DEM pyramid traversal can use hierarchical culling unless disabled.
- Ray distance/path is represented by fitted segment polynomials, not by recomputing an exact Moon ME ray for every pixel.

## Debug Scenario And Reproduction

Scenario:

- `/e/lunar_analyst_scenarios/debug_scenario`
- Primary DEM: 256x256 pixels.
- The relevant seam for the current timestamp is the vertical boundary between the bottom-left and bottom-right 128x128 patches: adjacent pixels `(127,y)` and `(128,y)` for `y >= 128`.

Timestamp and sun vector:

- Timestamp: `2027-10-29T22:00:00`
- Sun vector in MOON_ME frame: `(-148156503600.3822, -1902200999.7069519, -3950834811.5279264)`

The diagnostic test is:

- `native/new_horizon/tests/HorizonGen.Tests/DebugScenarioArtifactDiagnostics.cs`

The diagnostic now matches horizon runner case 5 DEM inputs and uses `LightmapPipeline` for shadow generation.

Primary generated output root:

- `native/new_horizon/tests/HorizonGen.Tests/bin/Debug/net9.0/linux-x64/TestResults/DebugScenarioArtifactDiagnostics/debug_scenario_artifacts/case5_20271029T220000`

Useful generated files:

- `sun_pipeline/sun_image_2027-10-29T22-00-00.tif`
- `bottom_patch_boundary_x127_x128_2027-10-29T22-00-00.csv`
- `horizontal_neighbor_sun_fraction_delta_2027-10-29T22-00-00.tif`
- `horizontal_neighbor_sun_fraction_delta_byte_m05_p05_2027-10-29T22-00-00.tif`
- `reference_comparison/reference_pair_summary.csv`
- `single_pixel_patch_comparison/worst_bottom_patch_boundary_y199_summary.txt`
- `no_hierarchy/bottom_patch_boundary_x127_x128_2027-10-29T22-00-00.csv`

## Evidence Gathered So Far

### Case 5 Diagnostic Matches The Normal Pipeline

The diagnostic was corrected to use the same DEM list as `horizon_runner` case 5 rather than the surrounding DEM list from scenario metadata. After that change, the diagnostic shadow image matched the normal case 5 shadow output.

Implication:

- The diagnostic is now useful for reproducing the original artifact rather than introducing a separate diagnostic-only artifact.

### 128x128 Seam Is Numerically Large

For the seam `(127,y)` to `(128,y)`, rows `128..255`:

- Sampled-horizon delta range: approximately `-0.222` to `-0.060` degrees.
- Mean sampled-horizon delta: approximately `-0.140` degrees.
- Sun-fraction delta range: approximately `+0.109` to `+0.377`.
- Worst inspected row: `y=199`.

At `y=199`:

- Full-patch quadtree sampled horizon delta: `-0.172288775 deg`.
- Full-patch quadtree sun-fraction delta: about `+0.3768`.

Within-patch controls near the same area had much smaller adjacent-pixel deltas, about `0.01 deg` sampled horizon and about `0.02` sun fraction.

Implication:

- The visible seam corresponds to a real discontinuity in the quadtree-generated horizon values, not just display stretch.

### Reference Horizons Are Smooth Across The Same Seam

From `reference_comparison/reference_pair_summary.csv`, at `(127,199)` to `(128,199)` near sun azimuth `205.6649 deg`:

- Quadtree full-patch delta: `-0.172288775 deg`.
- Reference delta: `-0.002563834 deg`.

Implication:

- The terrain and reference horizon do not contain a physical discontinuity at the seam.
- The bug is in quadtree horizon generation or its patch-local approximations, not in sun-fraction integration alone.

### Hierarchical Culling Is Not The Main Cause Of This Seam

Important code issue found:

- The `QuadTreeHorizonGenerator` constructor previously forced `disableHierarchy = true`, so callers passing `disableHierarchy: false` were not actually enabling hierarchy.
- That override has been removed locally so the diagnostic can compare true hierarchy-enabled and hierarchy-disabled runs.

Comparison after making the toggle effective:

- Hierarchy enabled `y=199` sun-fraction delta: about `+0.3768`.
- Hierarchy disabled `y=199` sun-fraction delta: about `+0.3760`.

Implication:

- Hierarchical culling may still have separate errors, but it is not the primary cause of the current 128x128 seam artifact.

### Single-Pixel Quadtree Generation Removes The Seam

Diagnostic: generate `(127,199)` and `(128,199)` as independent `1x1` quadtree patches using the non-pipelined `GenerateHorizons(...)` path.

Result from `single_pixel_patch_comparison/worst_bottom_patch_boundary_y199_summary.txt`:

- Full 128x128 patch horizon delta: `-0.172288775 deg`.
- Single-pixel patch horizon delta: `-0.001749039 deg`.
- Single-pixel sun-fraction delta: `+0.004077762`.

Implication:

- The discontinuity is introduced by the 128x128 patch/subpatch context.
- This strongly implicates `CalculateSubpatchRaySegments(...)`, `QuadTreeSubpatchRayCastKernel(...)`, or patch-local grid-convergence/bin-shift behavior.

Caveat:

- The `1x1` result uses the non-subpatch `GenerateHorizons(...)` path, not a true production subpatch size of 1. It is still a useful discriminator because it removes the 128x128 patch-local subpatch context.

## Current Working Hypotheses

1. **Patch-local subpatch ray fitting causes the 128x128 seam.**
   Adjacent pixels across a 128x128 tile boundary use ray fits generated from different patch/subpatch centers. The full-patch result jumps, while the reference and single-pixel quadtree result are smooth.

2. **8x8 subpatch reuse likely causes 8x8 artifacts.**
   The kernel selects one subpatch ray geometry and translates it for every pixel in that subpatch. This can create smaller-scale steps at subpatch boundaries.

3. **Integer grid-convergence bin correction may cause the ~24 px bands.**
   The subpatch kernel computes `binOffset`, rounds it to an integer, and writes to `correctedAzIdx`. Smooth spatial variation in convergence can become stepwise in horizon-bin space.

4. **Sun-fraction integration amplifies horizon discontinuities but is probably not the root cause of the 128x128 seam.**
   `BuilderSunFraction` converts a horizon step near the solar limb into a large brightness step. It is an amplifier here.

5. **Hierarchical culling is not the primary current cause of the 128x128 seam.**
   The seam remains with hierarchy enabled and disabled.

## Updated Debug Priorities

### Priority 1: Isolate Subpatch Ray Translation Error

Target files:

- `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs`
- `native/new_horizon/tests/HorizonGen.Tests/DebugScenarioArtifactDiagnostics.cs`

Experiments:

1. Add a debug-only way to vary `DEFAULT_SUBPATCH_SIZE` through the generator constructor or an optional parameter to `GenerateHorizonsForPatches`.
2. Run the debug scenario with subpatch sizes `8`, `16`, `32`, `64`, and `128` first because these are already allowed.
3. If practical, add debug-only support for `4` and `2`. Avoid full `1x1` subpatches over 128x128 unless memory is controlled, because that implies `1440 * 16384 * numDems` segment records per tile.
4. Compare the bottom seam CSV and horizontal neighbor delta rasters for each subpatch size.

Expected interpretations:

- If the 128x128 seam magnitude changes with subpatch size, the subpatch polynomial fit/translation is involved.
- If the 8x8 artifacts change but the 128x128 seam remains, there are separate subpatch and patch-origin issues.
- If `128` subpatch size makes the 128x128 seam worse, patch-center ray reuse is a direct cause.

### Priority 2: Compare Subpatch Path To Non-Subpatch Path On A Small Patch

The current `1x1` non-subpatch result is smooth. Next, make a small diagnostic that runs the subpatch kernel for a tiny tile or selected pixels, if possible, and compare:

- non-subpatch `GenerateHorizons(...)`,
- production subpatch kernel,
- reference generator.

Interpretation:

- If non-subpatch is smooth but subpatch is not, focus on `CalculateSubpatchRaySegments(...)` and `QuadTreeSubpatchRayCastKernel(...)`.
- If both become discontinuous in a similar patch context, focus on patch-local grid convergence or tile origin handling.

### Priority 3: Disable Integer Grid-Convergence Bin Shift

Temporarily force `correctedAzIdx = azIdx` in `QuadTreeSubpatchRayCastKernel` around `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs:3160`.

Generate:

- `horizontal_neighbor_sun_fraction_delta_byte_m05_p05_2027-10-29T22-00-00.tif`
- seam CSVs,
- sampled horizon at sun azimuth.

Interpretation:

- If ~24 px bands disappear or shift, replace integer bin remapping with fractional azimuth sampling.
- If 128x128 seam remains unchanged, subpatch/patch-local ray fitting is separate from convergence quantization.

### Priority 4: Patch-Origin Dependence

Generate the same physical pixels using shifted or overlapping diagnostic patches, for example a patch origin shifted by 64 pixels if supported safely.

Interpretation:

- If the same pixel changes depending on patch ownership, the horizon is not a function only of observer position and terrain. That directly confirms patch-local approximation error.

### Priority 5: Ray Trace Around The Worst Seam

For `(127,199)` and `(128,199)`, inspect the sun azimuth and the max-difference azimuth using:

- `ReferenceRayEmulator.cs`
- `QuadTreeRayEmulator.cs`

Compare:

- ray sample positions,
- ray distances,
- terrain elevations,
- slopes,
- horizon-forming blocker.

Interpretation:

- Same blocker but different slope indicates distance/curvature or observer-frame model error.
- Different blocker indicates ray path or sampling location error.
- Missing blocker in quadtree indicates stepping, culling, or polynomial path approximation error.

## Existing Diagnostic Assets

Use these before adding more infrastructure:

- `native/new_horizon/moonlib/horizon/ReferenceHorizonGenerator.cs`: slow full-horizon reference.
- `native/new_horizon/moonlib/horizon/ReferenceRayEmulator.cs`: per-ray reference trace.
- `native/new_horizon/moonlib/horizon/QuadTreeRayEmulator.cs`: CPU emulator of the quadtree/GPU ray path.
- `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs`: production GPU implementation under investigation.
- `native/new_horizon/moonlib/pipeline/LightmapPipeline.cs`: normal lightmap pipeline used by case 9.
- `native/new_horizon/tests/HorizonGen.Tests/DebugScenarioArtifactDiagnostics.cs`: current debug-scenario artifact generator.

Existing tests worth reusing:

- `native/new_horizon/tests/HorizonGen.Tests/ThreeWayComparisonTest.cs`
- `native/new_horizon/tests/HorizonGen.Tests/SinglePointComparisonTests.cs`
- `native/new_horizon/tests/HorizonGen.Tests/SubpatchPolynomialTests.cs`
- `native/new_horizon/tests/HorizonGen.Tests/LightmapRegression.cs`
- `native/new_horizon/tests/HorizonGen.Tests/NearFieldTests.cs`
- `native/new_horizon/tests/HorizonGen.Tests/TestCaseReplayTests.cs`

## Diagnostic Principles

- Change one variable at a time.
- Use the same DEMs, patch, observer elevation, and sun vector across comparisons.
- Preserve raw intermediate rasters and CSVs, not only final images.
- Compare horizon angle, sun-center margin, and final sun fraction separately.
- Use seam-wide statistics rather than isolated visual inspection.
- Prefer selected pixels or selected rows for slow reference runs.
- Treat visual artifacts as hypotheses; confirm with numeric difference rasters and boundary statistics.

## Remaining Questions

1. Does changing subpatch size change the 128x128 seam magnitude?
2. Does changing subpatch size change the 8x8 artifact pattern?
3. Do the ~24 px bands align with integer `binOffsetInt` contours?
4. Does disabling grid-convergence bin shifting remove the ~24 px bands?
5. Does a shifted or overlapping patch produce different horizons for the same physical pixels?
6. At the worst seam, do the two full-patch quadtree pixels follow different ray paths to different blockers, or do they reach the same blocker with different geometry?

## Likely Fix Directions

### If Subpatch Ray Reuse Is Causal

- Add smaller or adaptive subpatches near shadow-transition-sensitive regions.
- Recompute exact per-pixel ray geometry for near field or high-error regions.
- Interpolate ray geometry between neighboring subpatch centers rather than using nearest-subpatch selection.
- Make subpatch ray fits overlap or use halos so patch-boundary pixels do not switch discontinuously between independent fit contexts.

### If Patch-Origin Dependence Is Causal

- Make ray generation independent of 128x128 patch origin where possible.
- Use per-pixel or per-smaller-block convergence and ray models.
- Add overlap/halo validation at patch boundaries.
- Add a regression test that compares adjacent pixels across patch seams against reference and against interior adjacent pixels.

### If Grid-Convergence Quantization Is Causal

- Stop integer-bin remapping inside horizon generation.
- Store horizons in a consistent azimuth frame.
- Apply grid convergence at lightmap sampling time using fractional azimuth interpolation.
- Add a diagnostic raster for fractional and integer bin offset.

### If Sun-Fraction Is Causal Or Too Sensitive

- Keep `BuilderSunFraction` but add a high-resolution reference solar-disk integrator for tests.
- Make azimuth-bin center/edge conventions explicit.
- Add tests for smooth horizon ramps and known partial-sun cases.

### If Hierarchical Culling Is Causal In Other Cases

- Rework block culling to be strictly conservative.
- Add guard bands in distance and height.
- Validate culling shortcuts against level-0 exhaustive traces in tests.

## Suggested Execution Order From Here

1. Add a debug parameter for subpatch size in `GenerateHorizonsForPatches` and run sizes `8`, `16`, `32`, `64`, `128` on the debug scenario.
2. Add the grid-convergence-disabled variant and inspect whether the ~24 px bands move or disappear.
3. Add shifted/overlapping patch diagnostics for the seam pixels.
4. Run reference/quadtree ray emulators at `(127,199)` and `(128,199)` for azimuth `~205.665 deg` and the max-difference azimuth.
5. Only after these discriminators, implement a production fix and convert the diagnostic into a focused regression test.
