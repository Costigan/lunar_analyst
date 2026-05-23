# QuadTree Horizon Update Notes

Date: 2026-05-16

This document records the recent QuadTree horizon-generation debugging work, the conclusions from the experiments, and the intended production path.

## Context

The `ReferenceHorizonGenerator` appears to be correct, but it is too slow for production horizon generation. The production candidate is `QuadTreeHorizonGenerator`, which is much faster but had visible shadow map artifacts.

The artifacts were investigated using the debug scenario described in `docs/DEBUG_PLAN.md`:

- Scenario: `/e/lunar_analyst_scenarios/debug_scenario`
- Timestamp: `2027-10-29T22:00:00Z`
- Primary visible seam: around pixels `x=127/128`, rows in the bottom half of the scenario.
- Diagnostic test:
  `native/new_horizon/tests/HorizonGen.Tests/DebugScenarioArtifactDiagnostics.cs`
- Typical command:
  `dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj --filter FullyQualifiedName~DebugScenarioArtifactDiagnostics -v minimal`

The key user-facing problem was visible discontinuity in shadow maps. We want horizons that are correct enough that these artifacts are not visible, while preserving as much of the current compute-time efficiency as possible.

## Algorithm Background

The current fast path uses subpatch ray approximations:

1. A 128x128 output patch is divided into 8x8 subpatches.
2. For each azimuth and DEM pass, one ray polynomial is fit at each subpatch center.
3. Each pixel selects one subpatch's polynomial.
4. The selected ray is translated to the pixel by offsetting the start pixel.
5. The GPU marches the ray through the DEM pyramid and writes a horizon angle.

This is much faster than per-pixel exact ray generation because expensive ray fitting is amortized across 64 pixels. The weakness is that the approximation is piecewise: adjacent pixels across a subpatch boundary can select different fitted ray paths.

## Experiments And Results

### 1. Tile-relative grid-convergence bin remap

Hypothesis:

The large artifact at 128 pixel patch boundaries might be caused by applying an integer azimuth-bin correction using 128x128 tile-relative pixel coordinates, while subpatch rays are actually fit in subpatch-local frames.

Change tested:

- Changed the subpatch kernel's bin correction to use subpatch-relative `dCol/dRow`.
- Also tested disabling the subpatch bin remap entirely with:
  `QUADTREE_DISABLE_SUBPATCH_BIN_REMAP=1`

Result:

The 128 pixel seam and a 24 pixel-looking artifact were greatly reduced or removed in shadow-map output.

Representative seam result after the subpatch-relative correction:

| Pair | Before/Problem Behavior | After Subpatch-relative Remap |
| --- | ---: | ---: |
| `127/128`, `y=199`, horizon delta | about `-0.170964 deg` | about `-0.009542 deg` |
| `127/128`, `y=199`, sun fraction delta | about `+0.376807` | about `+0.020139` |

Conclusion:

The 128 pixel artifact was largely caused by the tile-relative grid-convergence remap. The correction should stay in the production path.

### 2. Disabling the subpatch bin remap

Hypothesis:

The remaining 8 pixel artifacts might still be caused by the integer subpatch bin remap.

Command:

`QUADTREE_DISABLE_SUBPATCH_BIN_REMAP=1 dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj --filter FullyQualifiedName~DebugScenarioArtifactDiagnostics -v minimal`

Result:

The seam metrics were effectively identical to the corrected default.

Conclusion:

The remaining 8 pixel artifact is not caused by the subpatch integer bin remap.

### 3. Smaller subpatch size

Hypothesis:

If the artifact is from subpatch approximation error, reducing the subpatch size should improve quality.

Command:

`QUADTREE_PIPELINE_SUBPATCH_SIZE=4 dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj --filter FullyQualifiedName~DebugScenarioArtifactDiagnostics -v minimal`

Result:

Quality improved, but runtime was much worse.

Representative result:

| Variant | `127/128`, `y=199`, horizon delta | `127/128`, `y=199`, sun fraction delta | Runtime |
| --- | ---: | ---: | ---: |
| 8x8 corrected default | about `-0.0095 deg` | about `+0.0201` | about `3m42s` in diagnostic |
| 4x4 subpatches | about `-0.00322 deg` | about `+0.00691` | about `9m56s` in diagnostic |

Conclusion:

The remaining error scales with subpatch size, which strongly implicates the subpatch ray approximation. However, simply moving to 4x4 subpatches is probably too expensive for production.

### 4. Per-pixel ENU frame correction

Hypothesis:

The 8 pixel artifact might be due to computing each subpatch ray in one local ENU frame and reusing it for pixels whose local ENU frames differ slightly.

Two diagnostic approaches were tested:

1. A first-order local correction.
2. An exact precomputed per-pixel projection from the subpatch ENU basis to each pixel's up vector.

Representative first-order diagnostic:

| Variant | `127/128`, `y=199`, horizon delta | `127/128`, `y=199`, sun fraction delta |
| --- | ---: | ---: |
| Corrected default | about `-0.0095 deg` | about `+0.0201` |
| First-order pixel ENU correction | about `-0.00518 deg` | about `+0.01176` |

Representative exact/precomputed diagnostic:

| Variant | `127/128`, `y=199`, horizon delta |
| --- | ---: |
| Corrected default after current changes | `-0.010925055 deg` |
| Exact/precomputed pixel up projection | `-0.009886384 deg` |

Conclusion:

Frame mismatch contributes some error, but it is not the dominant cause of the remaining seams. The exact per-pixel projection did not significantly improve the result. This diagnostic path should not be part of the production fix unless future tests show a separate need.

### 5. Forced common subpatch owner

Hypothesis:

If the seam is caused by hard ownership switching between subpatch ray polynomials, then forcing pixels on both sides of an 8 pixel boundary to use the same owner should remove or greatly reduce the discontinuity.

Diagnostic modes tested:

- `QUADTREE_SUBPATCH_OWNER_DIAGNOSTIC=vertical-left`
- `QUADTREE_SUBPATCH_OWNER_DIAGNOSTIC=vertical-right`

Representative result for internal boundary `95/96`:

| Variant | `qt_delta_b_minus_a_deg` |
| --- | ---: |
| Default hard owner | `-0.013733387 deg` |
| Forced common left owner | `+0.000403285 deg` |
| Forced common right owner | `0.000000000 deg` |
| Reference pair delta | `-0.003142357 deg` |

Representative result for internal boundary `159/160`:

| Variant | `qt_delta_b_minus_a_deg` |
| --- | ---: |
| Default hard owner | `-0.002744913 deg` |
| Forced common left owner | `-0.003051877 deg` |
| Forced common right owner | `-0.001832724 deg` |
| Reference pair delta | `-0.003343105 deg` |

Conclusion:

This confirmed that the remaining internal 8 pixel seam is caused by the discontinuous switch from one subpatch ray polynomial to another. Forcing one owner is not a production solution because it moves bias around, but it proves the mechanism.

### 6. Bilinear interpolation of subpatch ray segments

Hypothesis:

If hard ownership is the mechanism, bilinear interpolation between surrounding subpatch ray polynomials should make the approximation vary continuously across subpatch and patch boundaries while preserving most of the performance.

Diagnostic implementation:

- Enabled by `QUADTREE_INTERPOLATE_SUBPATCH_SEGMENTS=1`.
- For each pixel and azimuth, the kernel loads four neighboring subpatch segments.
- Each source segment is first shifted to the target pixel.
- Segment fields are bilinearly interpolated.
- The kernel then marches one interpolated segment, not four separate rays.
- A one-subpatch halo was computed per 128x128 patch for the diagnostic implementation.

Representative quality results:

| Pair | Default hard owner | Interpolated segments | Reference / single-pixel comparison |
| --- | ---: | ---: | ---: |
| `127/128`, `y=199` patch boundary | `-0.010925055 deg` | `-0.001769423 deg` | single-pixel `-0.001749039 deg`; reference `-0.002563834 deg` |
| `127/128`, `y=192` patch boundary | `-0.010734200 deg` | `-0.000052929 deg` | reference `-0.002848983 deg` |
| `95/96`, `y=192` internal boundary | `-0.013733387 deg` | `-0.003051877 deg` | reference `-0.003142357 deg` |
| `159/160`, `y=192` internal boundary | `-0.002744913 deg` | `-0.003051877 deg` | reference `-0.003343105 deg` |

Runtime:

| Variant | Diagnostic runtime |
| --- | ---: |
| Default corrected 8x8 hard owner | about `3m42s` |
| Interpolated segments diagnostic | about `4m06s` |
| 4x4 subpatches | about `9m56s` |

Conclusion:

Bilinear segment interpolation is the best production direction found so far. It removes the confirmed hard-owner discontinuity while keeping the expensive GPU terrain traversal to one marched ray per pixel/azimuth/DEM. It is far cheaper than 4x4 subpatches in the diagnostic case.

## Production Implementation Status

After the experiments above, the production path was updated in `QuadTreeHorizonGenerator.cs`.

Implemented cleanup:

- Removed `QUADTREE_SUBPATCH_OWNER_DIAGNOSTIC` and the forced common-owner kernel branches.
- Removed `QUADTREE_APPLY_PIXEL_ENU_ROTATION` and the `FrameCorrection` buffer/path.
- Removed the env-gated interpolation behavior and made subpatch segment interpolation the normal path.
- Kept `QUADTREE_PIPELINE_SUBPATCH_SIZE` as a diagnostic/test knob for subpatch-size sensitivity.

Implemented production behavior:

- Each patch now uses an 18x18 subpatch-center window for 8x8 interpolation.
- Adjacent patch halo centers are reused through a per-job segment-center cache during `GenerateHorizonsForPatches`.
- The GPU kernel always bilinearly interpolates four shifted subpatch ray segments and then marches one ray.

## Production Plan

### Goal

Make bilinear subpatch segment interpolation the production path, without carrying diagnostic toggles or unused frame-correction machinery.

### Required behavior

For each pixel:

1. Identify the four surrounding subpatch segment centers.
2. Load the four ray segments for the current azimuth and DEM pass.
3. Shift each segment from its source center to the target pixel.
4. Bilinearly interpolate the shifted segment coefficients and distance mapping fields.
5. March one interpolated segment through the existing quadtree/horizon kernel.

This keeps terrain sampling and quadtree traversal at one ray march per pixel/azimuth/DEM.

### Segment grid ownership

The diagnostic initially computed a one-subpatch halo per 128x128 patch. The production update avoids recomputing shared halo centers for neighboring patches in the main patch pipeline.

Production model:

1. Treat subpatch ray segments as a grid over the primary DEM rather than as private per-patch data.
2. For each 128x128 patch, request the center grid window needed by that patch:
   - Current hard-owner interior grid: 16x16 centers for 8x8 subpatches.
   - Interpolation grid: 18x18 centers including a one-center halo.
3. Reuse segment centers that overlap adjacent patches.
4. Keep the GPU patch interface simple: each patch still receives a compact contiguous segment array for its required center window.

The cache key should include at least:

- Subpatch grid coordinate or center pixel coordinate.
- Azimuth index.
- DEM pass / DEM identity.
- Observer elevation.
- Projection/generation parameters that affect ray fitting.

The current implementation uses an in-memory cache during one horizon-generation job. If memory pressure becomes an issue for larger jobs, it can be chunked by patch row or by a larger segment-center tile size.

### Completed implementation steps

1. Remove diagnostic-only frame-correction and forced-owner code.
2. Convert interpolation to the default production subpatch behavior.
3. Replace per-patch halo recomputation with reusable segment-center generation.
4. Keep the existing GPU kernel shape: one output pixel x azimuth thread, one marched segment.
5. Run debug scenario diagnostics and inspect shadow-map output.

### Acceptance criteria

The following are the target quantitative checks from the known debug scenario:

- `95/96`, `y=192` internal boundary should be close to the reference delta:
  - observed interpolation: `-0.003051877 deg`
  - reference: `-0.003142357 deg`
- `127/128`, `y=199` patch boundary should be close to single-pixel behavior:
  - observed interpolation: `-0.001769423 deg`
  - single-pixel: `-0.001749039 deg`
- Shadow maps generated from the resulting horizons should show no visible 128 pixel, 24 pixel, or 8 pixel boundary artifacts in the known case.
- Runtime should be much closer to the current 8x8 hard-owner path than to the 4x4 subpatch path.

### Risks

1. **Interpolating all segment fields may not be mathematically ideal.**
   The diagnostic result is strong, but some fields such as `SStart`, `SEnd`, and planar-to-chord coefficients may need special handling if other scenarios show issues.

2. **Halo reuse adds CPU-side complexity.**
   The simple diagnostic recomputes halos per patch. The production cache must be carefully scoped to one horizon-generation job and must respect cancellation.

3. **Memory pressure.**
   Segment arrays are large because they are per azimuth, center, and DEM pass. Reuse should be bounded or chunked.

4. **Edge behavior at DEM boundaries.**
   Halo centers outside the primary DEM need explicit handling. The diagnostic clamps terrain sampling for out-of-bounds center elevation, but production should define and test boundary behavior.

5. **Grid-convergence interaction.**
   The subpatch-relative grid-convergence remap fixed a real artifact. With interpolated segments, the current diagnostic uses zero local `dCol/dRow` after interpolation. This is consistent with producing a pixel-centered interpolated segment, but should be reviewed against any remaining azimuth-frame requirements.

## Recommendation

Proceed with bilinear subpatch segment interpolation as the production path. Do not fall back to `ReferenceHorizonGenerator`, 4x4 subpatches, or hard common-owner selection.

The production implementation should be a cleanup and hardening of the interpolation prototype:

- no diagnostic fallback paths,
- no unused frame correction path,
- segment-center reuse across adjacent patches,
- focused regression coverage for the known seam case,
- manual shadow-map verification for the debug scenario.
