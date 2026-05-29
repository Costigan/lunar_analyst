# ADR.0060: Native Horizon Regression Scenario Matrix

- Status: Proposed
- Date: 2026-05-29
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `native/new_horizon/docs/DESIGN.md`, `native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj`, `native/new_horizon/tests/HorizonGen.Development.Tests/HorizonGen.Development.Tests.csproj`

## Context

The native C# horizon engine has two different test needs:

1. Regression tests that should run by default and protect stable behavior.
2. Development tests that were used while designing, debugging, tuning, and comparing horizon algorithms.

The project now separates these into:

- `HorizonGen.Tests`: the default regression suite included in `native/new_horizon/new_horizon.sln`.
- `HorizonGen.Development.Tests`: explicit algorithm-development tests that are not included in the default solution.

Coverage reports are useful, but line coverage alone is not enough for this codebase. The highest-risk failures are scientific and geometric: CRS mistakes, horizon angle convention mistakes, tile-edge errors, multi-DEM ordering issues, compression loss, progress/cancellation contract drift, and streaming reduction mismatches. The regression suite therefore needs a small, intentional scenario matrix in addition to raw coverage metrics.

## Decision

Maintain a small matrix of deterministic native horizon regression scenarios in `HorizonGen.Tests`. The matrix should be portable, synthetic or repository-local where practical, and fast enough for ordinary `dotnet test native/new_horizon/new_horizon.sln` runs.

Development-only tests that depend on private external DEM paths, emit large diagnostic artifacts, benchmark performance, or intentionally compare unfinished algorithms should remain in `HorizonGen.Development.Tests`.

Implement the matrix in phases. The first phase establishes reliable default-suite coverage for core contracts and reference-level synthetic horizon behavior. A second phase must add stable production `QuadTreeHorizonGenerator` regression coverage without moving broad development harnesses back into the default suite.

## Regression Scenario Matrix

### Phase 1: Default Regression Spine

Phase 1 establishes the deterministic default suite. It covers stable library contracts, CRS behavior, storage formats, worker-facing progress/streaming contracts, and small synthetic reference-emulator horizon scenarios.

Phase 1 is complete when `dotnet test native/new_horizon/new_horizon.sln -v minimal` passes and includes coverage for the scenarios listed below.

### 1. Flat DEM Horizon Baseline

Purpose: protect horizon sign conventions, curvature handling, finite output, and basic generator wiring.

Scenario:

- Synthetic flat DEM.
- Observer inside the DEM.
- Horizon output has the expected length.
- Horizon values are finite.
- Expected sign/shape behavior is asserted with a documented tolerance.

Notes:

- This should stay small and deterministic.
- If full `QuadTreeHorizonGenerator` execution is too expensive or hardware-sensitive, use a smaller synthetic path or a lower-level emulator check.

### 2. Single Obstacle / Known Azimuth

Purpose: protect azimuth indexing, east/north orientation, slope-to-angle conversion, and obstacle detection.

Scenario:

- Synthetic DEM with one elevated obstacle or wall.
- Observer at a known pixel.
- The maximum horizon response occurs near the expected azimuth.
- The elevation angle is positive and within a documented tolerance of the expected slope.

Notes:

- This is the minimum regression for "the algorithm sees terrain in the right direction."
- Prefer synthetic DEMs over external VIPER datasets for default regression.

### 3. Multi-DEM Overlap and Ordering

Purpose: protect the rule that multiple DEMs are processed together and combined into one horizon product.

Scenario:

- Two or more small synthetic DEMs with controlled overlap.
- One DEM contains a lower-resolution or farther obstacle.
- Another DEM contains a closer or higher obstacle in the same or nearby azimuth.
- The combined horizon reflects the maximum visible elevation according to the documented ordering/accumulation rule.

Notes:

- The test should explicitly assert the expected dominant DEM or dominant obstacle.
- External VIPER multi-DEM comparisons belong in `HorizonGen.Development.Tests` unless reduced to a small checked-in fixture.

### 4. CRS Transform Edge Cases

Purpose: protect the custom hot-path CRS math from diverging from GDAL/OSR behavior.

Scenario:

- LongLat identity transforms.
- Stereographic to LongLat and LongLat to Stereographic round trips.
- Oblique stereographic center-point handling.
- Pixel-to-CRS and CRS-to-pixel affine round trips.
- Bounding-box projection between maps.

Notes:

- Use deterministic pseudo-random samples with fixed seeds where fuzzing is valuable.
- Keep GDAL comparison tests in the default suite when they remain fast and portable.

### 5. Tile Boundary and Patch Layout

Purpose: protect tile naming, patch alignment, tile enumeration, and edge behavior near patch boundaries.

Scenario:

- Bounding boxes round outward to patch-size multiples.
- Spiral patch enumeration returns a stable order.
- Horizon tile filenames parse and build consistently.
- Partitioned tile-store lookup prefers compressed partitioned files over legacy flat files.
- Invalid or conflicting tile files are handled without silent overwrite.

Notes:

- These tests guard filesystem compatibility and are important even when horizon math is unchanged.

### 6. Horizon Compression Round Trips

Purpose: protect horizon storage compatibility and quantization behavior.

Scenario:

- Empty, single-value, small, large, positive, negative, boundary, and mixed delta streams.
- Float horizon encode/decode round trips to expected quantized values.
- Invalid lengths and null inputs throw expected exceptions.
- Out-of-range deltas clamp or fail according to the documented codec contract.

Notes:

- These are high-value regression tests because compressed files are part of the scenario artifact contract.

### 7. Lightmap Streaming and Reduction

Purpose: protect native lightmap streaming contracts used by Python/FastAPI worker paths.

Scenario:

- Empty horizon directory emits terminal completion.
- One synthetic tile streams into a registered buffer.
- V2 chunked output reassembles to V1 output.
- Float32 margin output emits the expected chunk shape.
- Native reduction matches the V1 mean for the same synthetic tile/time sample.

Notes:

- These are contract tests as much as numerical tests.
- Keep test data synthetic and local.

### 8. Progress and Cancellation Contracts

Purpose: protect the shape of progress records and callback/cancellation delegates consumed by the worker protocol.

Scenario:

- Horizon progress records preserve processed count, total count, percent, stage, message, and filename.
- PSR progress records preserve percent, stage, and message.
- Progress callbacks can be invoked.
- Cancellation callbacks can be invoked and observed.

Notes:

- These tests are intentionally small. They catch contract drift before it leaks into FastAPI worker integration.

### 9. Pipeline Validation and Small Synthetic Execution

Purpose: protect pipeline-level preconditions and output layout without requiring large external DEMs.

Scenario:

- Invalid DEM dimensions are rejected with an actionable message.
- A small valid synthetic DEM generates the expected number of horizon tile files.
- Expected tile filenames exist.
- Generated files have the expected shape/length when practical.

Notes:

- Full VIPER DEM generation is development or benchmark coverage, not default regression coverage.

## Phase 2: Production QuadTree Regression Coverage

Phase 2 adds small, stable tests for the production horizon path. These tests should exercise `QuadTreeHorizonGenerator` directly while staying portable and fast enough for the default solution test command.

Phase 2 must not reintroduce broad algorithm-development harnesses into `HorizonGen.Tests`. Instead, reduce the behavior to narrow synthetic regression cases with explicit tolerances.

### Reference Oracle Policy

Reference implementations are correctness oracles, not bulk generators, for default regression tests.

`ReferenceHorizonGenerator` is too slow to generate horizons for every observer pixel in even a small DEM during routine default test runs. Phase 2 tests must therefore use it only for selected observer pixels.

`ReferenceRayEmulator` may be used for selected observer pixel plus azimuth probes. It is appropriate when a test needs to validate a specific direction such as "east of the observer" or a known obstacle azimuth.

Phase 2 correctness checks should combine:

- full-output invariants from `QuadTreeHorizonGenerator`, such as shape, finite values, plausible bounds, and absence of initialization sentinels;
- sampled pixel/bin comparisons against `ReferenceHorizonGenerator`;
- sampled pixel/azimuth comparisons against `ReferenceRayEmulator`;
- relative assertions, such as obstacle-side azimuths exceeding opposite-side azimuths or multi-DEM output exceeding inner-only output where the fixture intentionally places a dominant outer obstacle.

Default regression tests must not require reference generation for every pixel in a tile, patch, or DEM.

### 10. QuadTree Synthetic Smoke Test

Purpose: protect the production generator from basic execution regressions without depending on external DEMs.

Scenario:

- Synthetic DEM small enough for routine default test runs.
- Direct call to `QuadTreeHorizonGenerator.GenerateHorizons`.
- Output length matches `width * height * 1440`.
- Output values are finite.
- Broad sign or shape expectations are asserted where stable.

Notes:

- This test should avoid strict numerical comparison until a stable synthetic fixture and tolerance are proven.
- It should fail on obvious production-path breakage such as empty output, NaN output, wrong array shape, or gross sign convention drift.

### 11. QuadTree Known-Obstacle Behavior

Purpose: protect production-path azimuth orientation and obstacle visibility.

Scenario:

- Synthetic stereographic DEM with one elevated obstacle or wall.
- Observer and obstacle placement produce an unambiguous expected azimuth.
- `QuadTreeHorizonGenerator` result shows the obstacle-side azimuth has a materially higher horizon angle than the opposite direction.

Notes:

- Use broad tolerances initially, then tighten only after repeated stability across local and CI environments.
- If the full GPU path is hardware-sensitive, prefer the production CPU/device fallback mode already supported by the generator rather than depending on a specific GPU model.

### 12. QuadTree Tile Boundary Regression

Purpose: protect production horizon behavior near patch and tile edges.

Scenario:

- Synthetic DEM sized around one or two patch boundaries.
- Generate horizons for a tile that includes pixels near a boundary.
- Assert output shape, finite values, and absence of initialization sentinels.
- If a stable fixture can be found, compare adjacent non-obstacle pixels with a documented smoothness tolerance.

Notes:

- This should not generate large diagnostic artifacts.
- Any smoothness assertion must be tolerant enough to avoid invalidating real terrain discontinuities.

### 13. QuadTree Multi-DEM Synthetic Regression

Purpose: protect production multi-DEM accumulation with a portable fixture.

Scenario:

- Two small synthetic DEMs with compatible CRS and controlled overlap.
- Inner DEM is flat or contains a low obstacle.
- Outer DEM contains a clearly dominant obstacle in a known azimuth.
- Direct `QuadTreeHorizonGenerator` output reflects the dominant outer obstacle within a broad documented tolerance.

Notes:

- This scenario should be added only after the single-DEM QuadTree tests are stable.
- If direct production multi-DEM behavior is too costly for default tests, keep this scenario deferred with a documented reason and continue covering multi-DEM behavior through reference-level regression tests plus development tests.

### 14. Coverage Report Review

Purpose: use coverage data to guide Phase 2 rather than relying on intuition.

Scenario:

- Generate a coverage report for `dotnet test native/new_horizon/new_horizon.sln`.
- Inspect `moonlib/horizon` and streaming coverage.
- Identify production-path branches only covered by `HorizonGen.Development.Tests` or not covered at all.
- Add small default-suite regressions for high-risk uncovered branches.

Notes:

- Coverage percentage is not the acceptance criterion.
- The goal is to identify missing high-risk behavior, especially in production `QuadTreeHorizonGenerator` paths.

## Exclusions From Default Regression

The default regression suite should not include tests that:

- require private `/d/...` or `/c/...` datasets;
- benchmark runtime or GPU throughput;
- generate large diagnostic CSV, PNG, TIFF, or horizon artifacts;
- are expected to fail while algorithm work is in progress;
- compare old and new algorithm variants for exploratory analysis;
- depend on workstation-specific graphics libraries such as `libgdiplus`;
- require a specific GPU model or local driver configuration.

Such tests belong in `HorizonGen.Development.Tests` and should be run explicitly:

```bash
dotnet test native/new_horizon/tests/HorizonGen.Development.Tests/HorizonGen.Development.Tests.csproj
```

## Acceptance Criteria

The default native regression suite is healthy when:

- `dotnet test native/new_horizon/new_horizon.sln -v minimal` runs only regression tests and passes.
- Each scenario class above has at least one deterministic test in `HorizonGen.Tests`, or a documented reason it is deferred.
- Phase 2 production QuadTree scenarios have either stable default-suite tests or explicit deferral notes explaining why they remain covered only by development tests.
- Coverage reports show meaningful execution of the core native surfaces, but coverage percentage is treated as secondary to the scenario matrix.
- Any new bug fix in native horizon math, CRS handling, compression, tile storage, streaming, or worker-facing contracts adds a regression test to the closest matrix category.

## Consequences

Positive:

- Default `dotnet test` runs are faster and more reliable.
- Regression coverage is easier to reason about than a raw line-coverage number.
- Development harnesses remain available without making the default suite noisy.
- Future contributors have a concrete checklist for adding meaningful native tests.

Tradeoffs:

- Some algorithm paths will only be exercised by explicit development tests until reduced synthetic regression cases are added.
- Coverage percentages may initially look lower after separating development tests, but the signal is cleaner.
- The matrix must be maintained as native horizon behavior evolves.

## Rollback

If the split creates unexpected workflow friction, the development project can be added to a separate solution or CI job without changing the default regression suite. The default suite should remain limited to stable regression scenarios.
