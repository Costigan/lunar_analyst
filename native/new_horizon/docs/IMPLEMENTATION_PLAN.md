Implementation Plan: Hybrid Near-Field Reference + QuadTree Horizon
===================================================================

Goal
----
Blend the exact cartesian ray used by the reference generator for the first 50 m with the existing QuadTree ray caster for the rest of the scene.  Each azimuth’s final horizon angle becomes the max of both results so that near-field alignment always matches the reference implementation without sacrificing the QuadTree performance envelope.

Assumptions
-----------
* The existing reference pipeline in `horizongen/ReferenceHorizonGenerator.cs` can be refactored into a reusable helper that returns the 1440-bin horizon for a given observer pixel without touching disk.
* `QuadTreeHorizonGenerator.GenerateHorizons(...)` remains the orchestrator invoked by `horizon_runner` when the QuadTree path is requested.
* We keep the 50 m cutoff configurable so we can tune it after profiling.

Work Breakdown
--------------

1. Surface a hybrid-mode configuration knob
   * Add `NearFieldClampMeters` (default `50f`) plus a `bool EnableNearFieldReferenceMerge` to `QuadTreeHorizonGenerator`.
   * Thread the value through the public `GenerateHorizons` overloads and expose CLI / config hook in `horizon_runner/Program.cs`.
   * When disabled the code path should behave exactly like the current QuadTree-only implementation so regression testing is easy.

2. Extract a limited-range reference horizon helper
   * In `ReferenceHorizonGenerator`, add an internal method (e.g., `ComputeHorizonSegment(PixelOrigin origin, List<ElevationMap> dems, double maxRangeMeters)`) that reuses the existing per-azimuth logic but:
     * Uses the requested `maxRangeMeters` to clamp arc lengths and ray steps (stop adding samples once the planar distance exceeds the cap).
     * Returns only the `float[]` elevation array instead of packaging `ViewshedEntry` metadata.
   * Keep the existing public API by layering it on top of the new helper with `maxRangeMeters = double.PositiveInfinity`.
   * Ensure the helper can accept preloaded DEM instances so QuadTree and reference passes share the same `ElevationMap` objects and observer offset, avoiding resampling differences.

3. Start QuadTree rays at the cutoff distance
   * Extend `QuadTreeHorizonGenerator.CalculateRaySegments(...)` so it evaluates each polynomial at `s = NearFieldClampMeters` (if the segment starts before the cutoff) to derive a new starting pixel, chord distance, and `SStart`.  Skip the ray entirely if the DEM segment never reaches the cutoff.
   * Update `RaySegment` to record both the clamped starting `SStart` (for spherical drop) and `SStartPlanar` if the kernel needs planar offsets.  Audit the GPU kernel (in `QuadTreeRayEmulator`/production kernel source) to ensure loops that currently assume `SStart == 0` instead begin at the clamped chord.
   * Confirm that the kernel’s conservative bounds and guard distances account for the non-zero origin; adjust epsilon/step calculations if they used `segment.StartPixel` directly.

4. Merge horizons on the host
   * After `LaunchRayCasting(...)` produces the QuadTree horizon for each azimuth, invoke the new reference helper with the same observer pixel(s) and `maxRangeMeters = NearFieldClampMeters`.
   * Combine the arrays in managed code: `finalHorizon[az] = Math.Max(quadtree[az], referenceNearField[az])`.  Keep the original QuadTree result array for diagnostics if needed.
   * Update writers (e.g., output files in `output/`) so they use `finalHorizon`, and optionally emit both components in debug builds for validation.

5. Update debugging & comparison tools
   * `horizon_runner/HorizonComparator` should understand the hybrid mode by plotting the three curves (reference-only, QuadTree-only, merged) so we can verify that only the near bins diverge.
   * Extend any existing log or trace structures to include the cutoff distance so future investigations know the exact hybrid boundary.

6. Validation steps
   * Unit-test the new helper by constructing an in-memory DEM and verifying that clamping at 50 m matches the first samples of the unconstrained reference result.
   * Regression-test the GPU path with hybrid mode disabled to ensure no behavior change.
   * Run an end-to-end horizon generation with the hybrid enabled, confirm that the near-field mismatch disappears, and capture timing overhead.

Risks & Mitigations
-------------------
* **Double work per azimuth** – Mitigated because the reference pass only marches 50 m; document expected overhead in profiling.
* **DEM sampling mismatch** – Share `ElevationMap` instances between both passes and ensure the observer height interpolation path is identical.
* **Kernel assumptions about zero-distance start** – Audit and add asserts to catch segments that still begin inside the forbidden zone.
