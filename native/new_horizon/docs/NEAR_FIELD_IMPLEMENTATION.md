# Near-Field Reference Merge

## Problem Statement

- The reference horizon generator (CPU) is treated as the ground-truth ray caster, especially within the first few dozen meters, where the QuadTree/geometric approximations risk underestimating slope.
- QuadTree excels beyond ~50 m because it works entirely in DEM pixel space with cubic fits and hierarchical culling, but it uses approximations near the observer.
- Instead of perfectly matching the reference math in that near-field region, we can simply re-use the reference logic there and take the max of both results per azimuth.

## Implementation Overview

1. **Configuration knobs** (`horizongen/QuadTreeHorizonGenerator.cs`)
   - Added constructor parameters `enableNearFieldReferenceMerge` (default `false`) and `nearFieldClampMeters` (default `50f`). (`lines 14-43` and constructor).
   - `Program.cs` now reads `QUADTREE_NEARFIELD_MERGE` and `QUADTREE_NEARFIELD_METERS` to toggle the new feature when running mode 2. (`horizon_runner/Program.cs:33-74`).

2. **Reference helper for limited ranges**
   - `ReferenceHorizonGenerator` exposes `ComputeLimitedHorizon(origin, dems, maxRangeMeters)` to compute the same 1440-bin horizon but only marching to the requested range (50 m by default). (`horizongen/ReferenceHorizonGenerator.cs:80-210`).
   - The helper reuses the existing per-azimuth logic, returns angles in radians, and can be run with or without `Parallel.For` to balance CPU utilization.

3. **Kernel parameter plumbing**
   - Introduced a `KernelParams` struct passed to `QuadTreeRayCastKernel` so that the GPU code knows both the observer elevation and the minimum traverse distance. (`horizongen/QuadTreeHorizonGenerator.cs:166-175`, kernel signature and launch around `lines 799-835`).
   - `runtimeStart = max(seg.SStart, kernelParams.MinTraverseDistance)`, so rays can begin at 50 m if the merge is enabled, or at the original start when disabled. (`lines ~965-1010`).

4. **Host-side near-field block**
   - After the GPU pass finishes and horizon arrays are copied back (`lines 854-879`), the code checks `_enableNearFieldReferenceMerge` and, if true, calls `ComputeNearFieldBlock` to produce a `float[]` per pixel containing the near-field reference horizon (`lines 882-907`).
   - `ComputeNearFieldBlock` iterates through each pixel in the tile, builds a `PixelOrigin` at the DEM post and calls `ComputeLimitedHorizon` with the same `ElevationMap` objects to ensure identical terrain sampling. The result is a dense array sized `[pixels * 1440]`.
   - The host then clones the GPU horizon array to `_qt.bin`, takes the per-index `max(qt, near)` and writes `_near.bin` as well for diagnostics. (`lines 858-873`).

5. **Comparator/tooling updates**
   - `HorizonComparator.RunComparisonForPoint` now loads up to three files: the merged horizon, `_qt.bin`, and `_near.bin`. It plots Reference (red), QuadTree merged (blue), QuadTree raw (orange), and Near-Field reference (green) so we can see where the merge takes effect. (`horizongen/HorizonComparator.cs:95-210`).
   - The single-point regression test runs the QuadTree generator with the near-field merge enabled so mismatches trigger immediately during CI (`tests/HorizonGen.Tests/SinglePointComparisonTests.cs:39-60`).

## Expected Behavior / Diagnostics

- With the feature **disabled** (default), the runtime and outputs match the previous QuadTree-only implementation. No `_qt.bin` or `_near.bin` side files are written.
- With the feature **enabled**, each output horizon now represents `max(quadtree, reference within NearFieldClampMeters)`. Two additional files per tile (`*_qt.bin`, `*_near.bin`) are produced to help debug the merge.
- Any slowdown originates from the CPU near-field pass (`ComputeNearFieldBlock`), since it launches a reference ray for every pixel and azimuth. The GPU kernel itself continues to operate in single precision, as before.

## Follow-up Work

- Evaluate whether the near-field reference sampling can be shared among nearby pixels (e.g., downsampled grid) to reduce CPU cost.
- Consider moving portions of the near-field computation onto the GPU or reducing the azimuth count for the near-field pass.
- Revisit the unit test tolerance once we validate the hybrid output matches reference within the intended budget.
