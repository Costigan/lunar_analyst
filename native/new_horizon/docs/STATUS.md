Status: Hybrid Near-Field Merge Prototype
=========================================

Summary of Work
---------------
* Documented the hybrid idea in `docs/IMPLEMENTATION_PLAN.md`.
* Refactored `ReferenceHorizonGenerator` so it can emit limited-range (e.g., 50 m) horizons without disk I/O and share DEM instances.
* Extended `QuadTreeHorizonGenerator` with optional near-field merging: configurable clamp distance, kernel params that honor a non-zero start, and host-side blending that also writes `_qt.bin` / `_near.bin` diagnostics.
* Updated `horizon_runner` to expose the feature via `QUADTREE_NEARFIELD_*` env vars, `HorizonComparator` to plot reference/raw/merged/near curves, and the single-point regression test to run with near-field merging enabled.

Current Status
--------------
* Feature is functional: the pipeline now produces hybrid horizons and emits raw/near components for inspection.
* `dotnet test` currently fails because `CompareSinglePoint_ReferenceVsQuadTree` still sees a 0.292° delta at azimuth 281°. Artifacts (plots, CSV, emulator traces) are written under `tests/HorizonGen.Tests/TestResults/.../SinglePointComparison/`.
* No code paths outside the QuadTree flow are affected unless `QUADTREE_NEARFIELD_MERGE` is set or the new ctor parameters are used.

Next Steps
----------
1. Analyze the remaining 0.29° discrepancy — inspect `ref_vs_qt.csv`, `reference_trace.csv`, and `quadtree_trace.csv` to confirm whether the difference is beyond 50 m or caused by clamp logic.
2. Decide on adjustments: increase the clamp distance, relax the unit-test tolerance, or fix any residual slope computation issues revealed by the traces.
3. Once the delta is resolved, re-run `dotnet test` and baseline performance to quantify the hybrid overhead.***
