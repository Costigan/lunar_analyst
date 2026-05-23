# Improve Segment Generation

## Goal

Reduce the cost of segment generation in the horizon patch pipeline, with emphasis on algorithmic improvements before any GPU port.

The current profiling result for case 5 shows:

- `segment_generation` is about `25` seconds per patch.
- GPU work is about `2.5` to `4.0` seconds per patch.
- The patch queue stays effectively empty, so the GPU is starved by CPU-side segment preparation.

This document defines a concrete implementation plan to improve segment generation efficiently and safely.

## Scope

In scope:

- algorithmic improvements to segment generation
- data-flow improvements to avoid duplicate work
- CPU-side memory and allocation reductions
- follow-up profiling after each change
- design for a later GPU implementation if needed

Out of scope for the first passes:

- changing the scientific contract of generated horizons
- changing output file formats
- redesigning the horizon traversal kernel
- replacing the worker/pipeline architecture

## Current Hot Path

The current segment-generation work for case 5 flows through:

1. `GenerateHorizonsForPatches(...)`
2. `CalculateSubpatchRaySegments(...)`
3. `SubpatchSegmentCache.GetCenterSegments(...)`
4. `ComputeCenterSegments(...)`
5. `BuildRaySamples(...)`
6. `FitRaySegment(...)`
7. `FitPlanarToChordCubicWithTerrain(...)`
8. `ComputeChordToTerrain(...)`

The key problem is not quartic fitting. The main cost appears to be repeated geometric probing and repeated coordinate conversion.

## Main Findings From Code Review

### 1. `BuildRaySamples(...)` uses a linear exit search

`BuildRaySamples(...)` walks the ray outward in fixed increments until it leaves the DEM:

- `probe += step`
- each probe calls `TrySampleChord(...)`

This is expensive because:

- the number of probes grows with ray length
- every probe performs nontrivial math:
  - 3D vector step
  - cartesian to lat/lon conversion
  - `LonLatRad2RowCol(...)`
  - bounds check

This is the highest-value algorithmic target.

### 2. Chord-fit work recomputes spatial mapping that was already done

`FitPlanarToChordCubicWithTerrain(...)` calls `ComputeChordToTerrain(...)`, which again performs:

- vector-to-lat/lon conversion
- `LonLatRad2RowCol(...)`
- terrain sampling

That means sample-generation work is partly repeated instead of reused.

### 3. Per-DEM constants are recomputed too often

Inside the azimuth/DEM loops, the code recomputes:

- `mapRes`
- DEM width/height in meters
- `demSizeM`
- `rayLimit`

These are constant per DEM and should be precomputed once.

### 4. Small arrays and lists are allocated per ray

`FitRaySegment(...)` allocates fresh arrays for:

- `sArr`
- `vx`
- `vy`

`BuildRaySamples(...)` and chord fitting also use per-ray `List<>` allocations.

These are not the main bottleneck right now, but they are unnecessary and will become more visible after the algorithmic fixes land.

### 5. A direct GPU port of the current CPU algorithm is the wrong first move

The current algorithm is not GPU-friendly because it uses:

- variable-length search loops
- many double-precision trig and coordinate operations
- CPU object methods such as `ElevationMap.LonLatRad2RowCol(...)`
- repeated host-oriented indirection

This can be redesigned for GPU later, but it should not be ported as-is.

## Strategy

Work in small, measurable vertical slices. After each slice:

1. rebuild in `Release`
2. rerun case 5 with `QUADTREE_PIPELINE_PROFILE=1`
3. compare:
   - `segment_generation_sec`
   - effective patches/sec
   - queue depth behavior
   - GPU utilization

Do not bundle all changes together. Preserve the ability to see which change actually mattered.

## Phase 1: Replace Linear Exit Search

### Objective

Remove the `O(distance / step)` style exit search from `BuildRaySamples(...)`.

### Current Behavior

The function:

1. samples the start point
2. increments `probe` by a fixed step
3. tests whether the ray is still inside
4. repeats until out of bounds
5. then binary-searches the final crossing interval

This means many unnecessary calls to `TrySampleChord(...)` for long rays.

### Planned Change

Replace the current outward linear search with:

1. sample `startDist`
2. test `maxDist` or `rayLimit`
3. if `rayLimit` is inside, the ray never exits within the usable range
4. if `rayLimit` is outside, use binary search between:
   - `startDist`
   - `rayLimit`

If a fully direct binary search is not robust enough for all edge cases, use:

1. exponential bracketing
2. then binary search

This should dramatically reduce `TrySampleChord(...)` calls for long rays.

### Files Allowed To Change

- `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs`

### Acceptance Criteria

- segment generation remains functionally correct
- per-patch `segment_generation_sec` drops materially
- no regression in horizon correctness for existing tests

### Tests

- existing `HorizonGen.Tests`
- add a focused unit/regression test for `BuildRaySamples(...)` end-point behavior if practical

## Phase 2: Reuse Sample Metadata

### Objective

Stop recomputing lat/lon/pixel/terrain work during chord fitting.

### Planned Change

Replace the current sample tuple:

- `(double s, double x, double y)`

with a richer internal sample struct that carries:

- `sMeters` or `sKm`
- `pixelX`
- `pixelY`
- `latRad`
- `lonRad`
- `row`
- `col`
- `terrainHeightMeters`
- optionally `chordDistanceMeters`

Then:

- `BuildRaySamples(...)` computes these once
- `FitRaySegment(...)` consumes them directly
- `FitPlanarToChordCubicWithTerrain(...)` stops calling `ComputeChordToTerrain(...)`

### Rationale

This removes duplicate coordinate transforms and duplicate terrain lookups from the hot path.

### Files Allowed To Change

- `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs`
- only additional test files if needed

### Acceptance Criteria

- `FitPlanarToChordCubicWithTerrain(...)` no longer remaps each sample through `ComputeChordToTerrain(...)`
- segment generation time decreases further after Phase 1
- no correctness regression

## Phase 3: Hoist Per-DEM Constants

### Objective

Remove repeated invariant computation from the inner loops.

### Planned Change

Precompute a per-DEM descriptor containing:

- `mapRes`
- `demWidthM`
- `demHeightM`
- `demSizeM`
- `rayLimit`
- projection constants that are reused

Build this once per DEM outside the azimuth loops and pass the descriptor through `ComputeCenterSegments(...)`.

### Rationale

This is a low-risk cleanup that reduces repeated scalar work and clarifies the code.

### Acceptance Criteria

- no repeated per-DEM recomputation inside the inner azimuth loop
- no correctness regression

## Phase 4: Remove Per-Ray Heap Allocations

### Objective

Reduce allocation and GC overhead in the hot path.

### Planned Change

Convert per-ray temporary arrays and lists into:

- fixed-size local buffers where sample counts are bounded
- `stackalloc` spans if appropriate
- pooled buffers only if needed after profiling

Likely targets:

- `sArr`
- `vx`
- `vy`
- temporary arrays used in cubic fitting
- internal sample storage if a fixed upper bound is known

### Rationale

This should reduce allocator pressure and improve cache behavior, but it is secondary to the algorithmic fixes.

### Acceptance Criteria

- fewer allocations in the segment-generation path
- no correctness regression

## Phase 5: Parallelize Segment Generation More Aggressively

### Objective

Only after the algorithmic improvements land, revisit CPU parallelism for the producer side.

### Planned Change

Replace the single producer in `GenerateHorizonsForPatches(...)` with a bounded producer pool.

Important constraint:

- do this only after the core algorithm has been simplified and re-profiled

### Rationale

Parallelism helps throughput, but it should not be used to hide an avoidable algorithmic inefficiency.

### Acceptance Criteria

- queue depth is no longer near-zero all the time
- GPU workers are kept materially busier
- CPU scaling is measurable and stable

## GPU Offload Plan

Only consider this after Phases 1 through 4 are complete and measured.

### Question

Can segment generation move to the GPU?

### Answer

Yes, but not by porting the current CPU implementation directly.

### Recommended GPU Design

Create a GPU-specific segment-generation kernel that:

1. uses flat DEM data already resident on device
2. uses explicit stereographic math in kernel code
3. uses mostly `float`
4. avoids CPU object methods and delegates
5. computes one ray per `(center, azimuth, dem)` thread or work item
6. emits `RaySegment` directly

### Why This Fits The 5090 Mobile GPU

The 5090 mobile GPU is strong in FP32 but not FP64. A viable GPU implementation should therefore:

- keep the hot path in `float`
- reserve `double` for small setup work on CPU if necessary
- avoid an FP64-heavy direct translation of the current code

### Precondition For GPU Offload

Do not start GPU offload until:

- linear exit search is gone
- duplicate sample remapping is gone
- CPU hot path is re-profiled

Otherwise the GPU design will be based on a poor source algorithm.

## Implementation Order

Recommended order:

1. Phase 1: replace linear exit search
2. profile
3. Phase 2: reuse sample metadata
4. profile
5. Phase 3: hoist per-DEM constants
6. profile
7. Phase 4: remove per-ray allocations
8. profile
9. Phase 5: improve producer parallelism
10. profile
11. decide whether GPU offload is still worth doing

## Metrics To Compare After Each Phase

Always compare these before and after:

- average `segment_generation_sec`
- average `gpu_worker_total_sec`
- queue depth behavior
- effective patches/sec
- CPU utilization
- GPU utilization

Secondary metrics:

- `stream_sync_sec`
- file write time
- buffer reset and upload time

## Definition Of Done For This Workstream

- segment generation is no longer the overwhelmingly dominant stage, or
- segment generation is reduced enough that GPU workers become meaningfully occupied, and
- we have a clear measured decision on whether a GPU segment-generator is still worth building

## Risks

- geometric endpoint logic can be subtle; exit-search changes must preserve correctness
- richer sample structs can increase memory traffic if designed poorly
- aggressive allocation removal can hurt readability if done too early
- a premature GPU port can consume a lot of effort without fixing the actual bottleneck

## Rollback Approach

Each phase should be landed separately and be independently reversible.

If a phase causes correctness regressions:

1. revert that phase only
2. keep the instrumentation
3. move to the next safest optimization candidate
