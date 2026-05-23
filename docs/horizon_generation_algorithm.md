# Horizon Generation Algorithm

This document describes the current horizon-generation algorithm implemented in
[`native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs`](/e/projects/lunar_analyst/native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs).
It focuses on the patch-based GPU pipeline used by `horizon_runner` run mode `5`,
which is the path currently used to generate horizon files for PSR analysis.

The intent of this document is:

- explain the end-to-end algorithm as it exists now
- separate the geometric model from the implementation details
- record the simplifying assumptions explicitly
- explain why the algorithm is structured as it is

## Overview

The generator computes a horizon angle for every:

- pixel in a `128 x 128` patch
- azimuth bin in a `1440`-bin compass

The high-level structure is:

1. build DEM pyramids
2. split the full raster into `128 x 128` patches
3. for each patch, fit ray polynomials at subpatch centers on the CPU
4. send those fitted ray segments to the GPU
5. ray march each pixel/azimuth through the active DEM pyramid hierarchy
6. store the maximum apparent terrain slope as a horizon angle
7. write one horizon file per patch

The algorithm is not tracing exact geodesics per pixel. It is a hybrid:

- CPU side:
  fit a local approximation of each ray in projected pixel space
- GPU side:
  evaluate that approximation cheaply for every pixel and azimuth

The entire design exists to avoid the cost of doing exact map projection and
exact 3D geometry from scratch for every pixel/azimuth/sample on the GPU.

## Inputs and Outputs

### Inputs

- one or more `ElevationMap` DEMs, ordered from finest to coarser coverage
- observer height above terrain, in meters
- patch list
- output directory

### Outputs

- one horizon file per `128 x 128` patch
- each file contains `1440` horizon angles per pixel

The downstream PSR code consumes these horizon files and is assumed to be correct
for the current analysis; this document only covers horizon generation.

## Coordinate Model

The implementation mixes three related coordinate systems:

1. **Pixel coordinates**
   Used for polynomial fitting and GPU raster traversal.

2. **Projected map coordinates**
   Derived from the DEM geotransform. Used for map resolution and projection
   metadata.

3. **Moon-centered 3D coordinates**
   Used when converting observer and sample locations into vectors for distance
   and slope geometry.

This is a deliberate design choice. The projected raster is the natural space
for efficient GPU traversal, while the 3D model is the natural space for lunar
curvature and horizon geometry.

## Patch Tiling

The raster is processed in fixed `128 x 128` patches.

### Why `128 x 128`

This is a practical engineering choice:

- small enough to keep per-patch buffers bounded
- large enough that CPU setup overhead is not dominant
- convenient for GPU launch geometry and horizon-file layout

This is not a geometric requirement. It is a batching choice.

### Consequences

- patch boundaries are implementation boundaries, not scientific boundaries
- approximation quality near patch boundaries depends on the subpatch-halo design
- progress reporting is naturally patch-based

## DEM Pyramid Construction

Before patch processing starts, each DEM is converted into a pyramid.

### Purpose

The pyramid allows the GPU to:

- skip terrain blocks that cannot change the horizon
- sample coarser levels first
- descend to finer levels only when necessary

### Simplifying Assumption

Each coarser level stores a conservative height summary sufficient for
hierarchical culling.

### Design Choice

Pyramids are built once per run and cached to disk. This amortizes the CPU cost
across many horizon runs.

## Compact Mode vs Subpatch Mode

The current pipeline uses the subpatch path, not the old single-center compact
path.

### Compact Mode

Compact mode fits one ray family at the patch center and reuses it across the
whole patch.

### Subpatch Mode

Subpatch mode fits rays at multiple centers inside and around the patch, then
interpolates between them at runtime.

### Why Subpatch Mode Exists

A single polynomial family is not accurate enough across a full `128 x 128`
patch, especially:

- far from the patch center
- near projection distortion
- near DEM edges

Subpatch mode reduces approximation error by localizing the fit.

## Subpatch Layout and Halo

The patch is subdivided into interior subpatches of configurable size. The code
currently supports sizes that divide `128`, including `8`, `16`, `32`, `64`,
and `128`.

The implementation adds a one-subpatch halo around the interior grid.

### Why the Halo Exists

Without a halo, interpolation would degrade near patch edges because a pixel at
the edge would only have one-sided local models available.

The halo allows bilinear interpolation between neighboring subpatch-center fits
even at the patch boundary.

### Simplifying Assumption

The subpatch center outside the physical DEM can be clamped back inside the DEM
without introducing unacceptable local error.

### Design Choice

Clamping the requested center to valid DEM bounds is cheaper and simpler than
special-casing the interpolation logic for every edge condition.

## Patch-Center and Subpatch-Center Observer Model

For each fitted center, the code:

1. converts the center pixel to projected coordinates
2. inverse-projects that point to latitude/longitude
3. samples terrain elevation at the center pixel
4. adds observer elevation above terrain
5. constructs an observer 3D vector on the moon

### Design Choice

The observer is defined locally at each fitted center rather than globally once
for the entire patch.

### Why

This makes the local ray geometry more accurate, especially in subpatch mode,
because each local fit uses an observer position consistent with that fit’s
center.

## Azimuth Discretization

Each patch uses `1440` azimuth bins.

### Meaning

This is a fixed angular discretization of the horizon:

- `360 degrees / 1440 = 0.25 degrees` per bin

### Simplifying Assumption

The horizon can be represented adequately at quarter-degree azimuth resolution
for the intended lighting analyses.

### Design Tradeoff

More azimuth bins improve angular fidelity but scale both:

- CPU polynomial fitting cost
- GPU traversal cost

## DEM Segment Decomposition

Each azimuth is split across the available DEMs. The code builds a
`DemSegmentContext` per DEM with:

- DEM reference
- average map resolution
- a traversal-distance limit

### Why multiple DEM segments exist

The system supports nested or staged DEM coverage. A ray may begin on a fine DEM
and continue on coarser DEMs at longer range.

### Simplifying Assumption

Each DEM can be represented as a contiguous segment of useful ray coverage with
its own fitted polynomial.

### Current limit heuristic

The ray limit for a DEM is capped by:

- the configured maximum range
- approximately `1.2 x` the smaller DEM dimension in map meters

This is a heuristic intended to avoid fitting rays beyond the DEM’s useful
coverage.

## Ray Sample Construction

For a single fitted center, azimuth, and DEM segment, the CPU first constructs a
small set of ray samples.

### Current sample strategy

`BuildRaySamples(...)`:

1. samples the starting distance
2. tests the target `maxDist`
3. if `maxDist` is out of bounds, binary-searches the last valid in-bounds point
4. distributes up to 10 intermediate samples over the valid span
5. appends the final in-bounds sample if needed
6. calls `EnsureMinimumSamples(...)`

`EnsureMinimumSamples(...)` enforces:

- at least `4` samples
- at least `100` meters of span

### Why so few samples

The goal is not to represent terrain in detail at this stage. The samples are
used only to fit the local ray polynomial and the planar-to-chord correction.

### Simplifying Assumptions

- the local projected ray can be approximated from a small number of samples
- the end of the valid interval is more important than dense interior sampling
- a short fallback extension is sufficient to stabilize the fit

### Design Choice

The current fast version uses direct endpoint testing plus binary refinement
instead of a coarse outward march. This was chosen for CPU performance.

## What a `RaySample` Contains

Each sample stores:

- distance along the ray, in meters
- pixel `x, y`
- latitude and longitude
- DEM row and column
- terrain height at the sampled point

### Why this structure exists

It avoids recomputing projection and DEM sampling during later fitting stages.

### Tradeoff

This increases CPU-side sample-generation work, but it greatly simplifies the
later fitting code.

## Polynomial Ray Fit in Projected Pixel Space

After sample generation, the code fits a quartic-without-intercept form in the
local parameter `ds`:

- `x(ds) = x0 + a1*ds + a2*ds^2 + a3*ds^3 + a4*ds^4`
- `y(ds) = y0 + b1*ds + b2*ds^2 + b3*ds^3 + b4*ds^4`

The fitted parameter is normalized over the segment span, then rescaled into the
stored coefficients.

### Critical unit rule

The stored segment parameter is in **kilometers**, not meters.

This matters because the GPU kernel evaluates the polynomials in kilometers.
Using meters in the fit while evaluating in kilometers produces incorrect ray
geometry.

### Why a quartic

This is a pragmatic compromise:

- linear is too inaccurate
- cubic is often insufficient over longer spans or distorted projection regions
- quartic captures curvature without an excessive coefficient count

### Simplifying Assumption

The projected ray path over a DEM segment is smooth enough that a low-order
polynomial is sufficient.

## Planar-to-Chord Correction

The projected ray fit gives a path in pixel space. The GPU still needs a mapping
from planar traversal distance to true geometric distance from the observer.

The code stores a cubic-without-intercept:

- `chord_delta ~= c1*p + c2*p^2 + c3*p^3`

where `p` is planar distance in meters from the segment start.

### Purpose

This correction accounts for the fact that:

- the ray is fitted in projected 2D space
- the horizon geometry is physically 3D on a curved moon

### Two correction models in the code

The implementation currently contains both models behind a flag:

1. **DEM-elevation correction**
   Uses the sampled terrain elevation at each fit sample when computing chord
   distance.

2. **Fixed-radius spherical correction**
   Uses a sphere with radius:
   `reference_radius + elevation_at_observer_pixel`

### Current flag

`UseDemElevationChordCorrection` selects the active model.

### Design discussion

#### DEM-elevation correction

Pros:

- tracks actual terrain heights in the fitted target

Cons:

- mixes topography into what is conceptually a geometry correction
- can produce unstable fits
- is more expensive

#### Fixed-radius spherical correction

Pros:

- cleaner geometric interpretation
- cheaper
- usually smoother near the observer

Cons:

- ignores per-sample terrain in the correction fit

### Simplifying Assumption

The planar-to-chord mismatch is smooth and well-approximated by a cubic over the
segment span.

### Fallback behavior

If the fitted `c1` falls outside `[0.5, 2.0]`, the code falls back to:

- `c1 = 1`
- `c2 = 0`
- `c3 = 0`

This treats planar distance as chord distance locally.

### Why this fallback exists

It is safer to degrade to identity than to propagate an unstable correction that
would distort the GPU traversal geometry.

## Grid Convergence Compensation

The code computes grid-convergence information at the tile center:

- center convergence
- `dGamma/dx`
- `dGamma/dy`

### Why

In a stereographic projection, map north and true north diverge as position
changes. A ray family fitted at one local frame must be corrected when applied
across the patch.

### Current design

The GPU kernel applies a local correction based on tile-relative position.

### Simplifying Assumption

A first-order local gradient of grid convergence is sufficient over the patch.

This avoids refitting azimuths per pixel.

## GPU Ray Traversal

The GPU kernel operates per:

- pixel
- azimuth
- DEM pass

For each pixel/azimuth, it:

1. identifies the four neighboring subpatch centers
2. shifts their segments from center-relative to pixel-relative form
3. bilinearly interpolates the four segments
4. marches along the interpolated ray
5. updates the maximum apparent slope

### Why interpolate segments instead of fitting per pixel

Per-pixel fitting would be far too expensive. Interpolating nearby local fits is
the core approximation that makes the algorithm practical.

## Segment Shifting and Interpolation

Each stored `RaySegment` is defined relative to its fit center. Before marching
for a specific pixel, the kernel shifts the segment to the pixel’s local frame.

Then it bilinearly interpolates the four neighboring segments.

### Simplifying Assumption

Segment coefficients vary smoothly enough across subpatch centers that bilinear
interpolation is acceptable.

### Design Choice

This avoids fitting an explicit 2D field of polynomial coefficients.

## Near-Field and Far-Field Distance Model

The kernel uses two distance treatments:

- for very short range, use the segment parameter directly
- beyond a threshold, use the planar-to-chord correction

The current threshold is `0.5 km`.

### Why the split exists

Near the observer, the difference between planar and chord distance is small, and
using the direct parameter is stable and cheap.

Farther out, curvature matters and the correction becomes useful.

### Simplifying Assumption

The local short-range geometry is close enough to flat that direct parameter
distance is acceptable below the threshold.

## Horizon Slope Calculation

At each sample point, the kernel samples level-0 terrain with bilinear
interpolation and computes an apparent slope from the observer to terrain.

### Short range

At short range, it uses a flat-Euclidean approximation:

- `slope = delta_height / distance`

### Longer range

At longer range, it uses spherical geometry to compute the terrain point’s local
vertical and horizontal coordinates relative to the observer.

### Why two formulas exist

The exact spherical calculation is more expensive and less necessary very close to
the observer.

## Hierarchical Culling

The normal path is hierarchical. For each sample position:

1. choose a starting pyramid level based on distance and map resolution
2. read the block’s conservative maximum height
3. estimate whether that block could exceed the current horizon slope
4. if not, skip ahead to the block exit
5. if yes, descend to a finer level
6. at level 0, sample bilinearly and update the horizon

### Why this works

If even the maximum possible terrain in a block cannot exceed the current
horizon, the entire block can be skipped safely.

### Simplifying Assumption

The stored pyramid maxima are conservative enough that skipped blocks cannot hide
the true horizon.

## Adaptive Step Size

When the kernel is not using fixed debug steps, it adapts the march step using:

- projected ray tangent magnitude
- current horizon margin
- angular step heuristics
- a minimum floor tied to the active DEM map resolution

### Why

Small steps are needed where the ray geometry or horizon changes quickly. Large
steps are safe where the terrain is clearly below the current horizon.

The minimum floor prevents the near-field angular heuristic from forcing
sub-raster sampling intervals. The DEM cannot support terrain detail below its
own sample spacing, so stepping much more densely than the raster is treated as
unnecessary work.

The current floor is:

- `0.5 x` active DEM map resolution by default
- `0.8 x` active DEM map resolution for DEM 0 after the ray is at least `100 m`
  from the observer

### Design Choice

The adaptive rule is heuristic, not analytically optimal. It is tuned to reduce
work while preserving horizon fidelity.

The margin-based component uses the gap between the current best horizon slope
and the current sample slope. If the sample is far below the known horizon, the
kernel can take a larger step; if the sample is near the horizon, the step is
smaller. This is based on an assumed maximum terrain rise rate, not on a formal
proof. The current implementation intentionally keeps this rule simple to avoid
adding state, branches, and register pressure to the hot GPU loop.

## Multiple DEM Passes

The GPU processes all DEMs for a patch by launching one pass per DEM on the same
stream and accumulating into one output horizon buffer.

### Why

This lets the system combine nested or staged DEM coverage without writing a
different kernel for every DEM configuration.

### Simplifying Assumption

Later DEM passes can refine the horizon without needing a separate merge stage on
the CPU.

### Current Buffer Representation

The GPU accumulation buffer stores apparent horizon slope, not angle. Each DEM
pass reads the prior slope, updates it with `max(existingSlope, sampleSlope)`,
and writes slope back to the buffer.

This avoids converting slope to angle with `atan` at the end of each DEM pass
and then converting angle back to slope with `tan` at the start of the next pass.
The conversion to degrees happens once after all DEM passes for the patch are
complete.

## Output Format

After GPU traversal:

1. the accumulated horizon slopes are copied back to the CPU
2. slopes are converted to horizon angles in degrees
3. the result is written to a patch file

Compressed output can be used when requested.

## Pipeline Structure

The patch-processing path uses a producer-consumer pipeline:

1. **CPU producer**
   builds subpatch ray segments per patch

2. **GPU workers**
   launch the ray-casting kernel on pooled GPU streams

### Why

This overlaps CPU fitting with GPU traversal and keeps the GPU fed when CPU
segment generation is not the bottleneck.

### Design Choice

The implementation uses:

- one producer
- multiple GPU workers
- a bounded channel
- a stream pool

This is meant to create backpressure naturally rather than allowing unbounded
queue growth.

## Major Simplifying Assumptions Summary

The algorithm depends on the following assumptions:

1. A local projected ray can be represented by a low-order polynomial.
2. The polynomial coefficients vary smoothly enough across subpatch centers that
   bilinear interpolation is valid.
3. A small number of samples is sufficient to fit the local ray and chord
   correction.
4. Grid convergence varies slowly enough across a patch to be handled with a
   first-order local correction.
5. The planar-to-chord mismatch is smooth enough to fit with a cubic.
6. Near the observer, a simpler short-range distance and slope model is
   acceptable.
7. Pyramid block maxima are conservative enough for safe culling.
8. The chosen azimuth resolution is sufficient for the scientific use case.

None of these assumptions is mathematically exact. They are engineering
approximations chosen to make a very large horizon problem computationally
tractable.

## Important Design Tradeoffs

### Accuracy vs Throughput

Nearly every major choice in the implementation is a tradeoff:

- more subpatch centers increase accuracy and CPU cost
- more azimuth bins increase angular fidelity and total cost
- more ray samples improve fit stability but increase setup time
- stronger culling heuristics reduce work but increase approximation risk

### Smooth Geometry vs Real Terrain in Chord Correction

This is an especially important tradeoff:

- DEM-elevation chord correction is closer to the sampled terrain
- fixed-radius chord correction is cleaner geometrically and cheaper

The code currently keeps both because performance and correctness tuning may
require comparing them directly.

### Locality vs Global Exactness

The algorithm chooses local approximations repeatedly:

- local fit centers
- local grid-convergence correction
- local short-range slope model

This sacrifices exact global geodesic behavior in favor of a model that can run
at usable speed.

## What This Algorithm Is Not

To avoid confusion, the current algorithm is not:

- an exact per-pixel geodesic ray tracer
- a pure flat-Earth viewshed
- a pure analytic spherical model
- a terrain-only line-of-sight solver without projection effects

It is a hybrid projected-raster / spherical-geometry approximation pipeline.

## Current Tunable Knobs

The main knobs visible in the current code are:

- `MAX_CONCURRENT_GPU_OPS`
- `_pipelineSubpatchSize`
- `UseDemElevationChordCorrection`
- `MIN_RAY_SAMPLE_COUNT`
- `MIN_RAY_SAMPLE_SPAN_METERS`
- `MIN_PLANAR_SPAN_FOR_CHORD_FIT_METERS`
- `_nearFieldClampMeters`
- `MIN_ADAPTIVE_STEP_RESOLUTION_FACTOR`
- `PRIMARY_DEM_FAR_MIN_STEP_DISTANCE_METERS`
- `PRIMARY_DEM_FAR_MIN_STEP_RESOLUTION_FACTOR`
- hierarchy enable/disable debug flags
- `QUADTREE_PIPELINE_PROFILE`
- `QUADTREE_TRAVERSAL_PROFILE`

These should be treated as algorithmic controls, not just performance controls,
because several of them affect correctness and approximation behavior.

`QUADTREE_PIPELINE_PROFILE` is a runtime environment flag for host-side pipeline
timing logs. `QUADTREE_TRAVERSAL_PROFILE` is a compile-time symbol for GPU
traversal counters. They are deliberately separate: traversal counters add a
kernel buffer, atomic updates, and hot-loop branches, so they must be compiled
out for normal performance runs.

## Practical Reading Order for the Code

If you want to trace the algorithm in code, the most useful order is:

1. `GenerateHorizonsForPatches(...)`
2. `CalculateSubpatchRaySegments(...)`
3. `SubpatchSegmentCache.ComputeCenterSegments(...)`
4. `BuildRaySamples(...)`
5. `TrySampleChord(...)`
6. `FitRaySegment(...)`
7. `FitPlanarToChordCubicWithTerrain(...)`
8. `QuadTreeSubpatchRayCastKernel(...)`

That path corresponds closely to the actual runtime flow.

## Appendix: Recent Chord-Correction Comparison

The implementation currently contains two planar-to-chord correction models
behind a code flag:

- `UseDemElevationChordCorrection = true`
- `UseDemElevationChordCorrection = false`

These modes were compared recently on the Haworth scenario using run mode `5`.

### Test setup

- scenario: Haworth
- patches: `208`
- command:

```bash
dotnet run -c Release --project native/new_horizon/horizon_runner -- 5
```

- other important state:
  - fast sample-generation path enabled
  - kilometer-unit fix in `FitRaySegment(...)` present
  - no pipeline profiling environment flag
  - traversal-counter instrumentation not compiled into the production kernel

### Observed timings

#### DEM-elevation chord correction

With:

- `UseDemElevationChordCorrection = true`

Observed result:

- `208` horizon files in about `7.00` minutes
- average about `2.02 sec/patch`

#### Fixed-radius spherical chord correction

With:

- `UseDemElevationChordCorrection = false`

Observed result:

- average about `1.91 sec/patch` after compiling traversal-counter
  instrumentation out of the production kernel

### Interpretation

At least in this comparison:

- the fixed-radius spherical correction was modestly faster
- the difference was on the order of a few percent, not an order of magnitude

This is consistent with the code structure:

- both modes use the same overall pipeline
- both modes use the same polynomial ray fit
- both modes use the same GPU traversal kernel
- only the chord-correction target changes

The likely reason the fixed-radius path is faster is that it avoids using
per-sample terrain height inside the chord-fit target and can produce slightly
simpler correction behavior.

An intermediate build that kept traversal-counter code in the kernel behind only
a runtime flag measured about `2.03 sec/patch` on Haworth. Moving those counters
behind the `QUADTREE_TRAVERSAL_PROFILE` compile-time symbol restored the
production path to about `1.91 sec/patch`.

### Important caveat

Performance and correctness are both sensitive to the exact combination of:

- sample-generation strategy
- ray-fit units
- chord-correction model
- warning/logging behavior
- GPU-stage workload caused by the resulting coefficients

So these measurements should be treated as observations of one known code state,
not as a general proof that one correction model is always superior.

### Current practical guidance

If comparing chord-correction modes in the future:

1. keep the sample-generation path fixed
2. keep the ray-fit units fixed
3. disable runtime pipeline profiling unless profiling overhead is itself under
   study
4. do not compile with `QUADTREE_TRAVERSAL_PROFILE` for production timing
5. compare both:
   - patch throughput
   - downstream PSR correctness

The chord-correction model affects both runtime and output quality, because the
resulting correction coefficients are consumed directly by the GPU traversal
kernel.

Traversal counters are intentionally guarded by the `QUADTREE_TRAVERSAL_PROFILE`
compile-time symbol. Defining that symbol builds a diagnostic kernel with
counter buffers and atomic updates; normal builds omit that parameter and all
counter branches from the hot ray loop.
