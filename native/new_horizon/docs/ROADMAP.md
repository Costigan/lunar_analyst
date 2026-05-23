# new_horizon Implementation Roadmap

## 1. Core Algorithm & Performance Optimization
- [ ] **Resolve Performance Regressions**: Investigate and fix the 4× performance regression in the `Subpatch` and `Hybrid` kernels to match the baseline `Compact` mode performance.
- [ ] **Kernel & Memory Tuning**: Implement GPU memory coalescing and explore kernel fusion to reduce global memory requests and instruction overhead.
- [ ] **Reference Implementation Parallelization**: Optimize the CPU-based `ReferenceHorizonGenerator` using multi-threading to speed up ground-truth validation.
- [ ] **I/O & Pipeline Improvements**: Enhance the asynchronous processing pipeline to further overlap disk I/O with GPU computation and reduce CPU-side stalls.
- [ ] **Adaptive Step Refinement**: Tune adaptive stepping constants and hierarchy drill-down heuristics to balance angular error budgets with traversal speed.

## 2. Geodetic Accuracy & Geometric Consistency
- [ ] **Standardize Coordinate Frames**: Rigorously enforce the ENU (East-North-Up) local tangent frame across all modules, eliminating legacy NED or non-standard transformations.
- [ ] **Analytic GPU Geodesic Generation**: Finalize the implementation of on-the-fly analytic ray generation on the GPU to eliminate heavy CPU pre-calculation of polynomials.
- [ ] **Hybrid Merge Validation**: Resolve residual elevation discrepancies (e.g., the 0.25° delta) in the near-field reference merge path.
- [ ] **Singularity Robustness**: Enhance geodetic math and testing coverage for polar latitudes and other map projection singularities.
- [ ] **Planetary Generalization**: Extend the geodetic engine to support non-lunar bodies (Mars, asteroids) by parameterizing datum-specific constants.

## 3. Tooling, Visualization & Debugging
- [ ] **Form2 Maturity**: Complete the migration of the `CompareHorizons` tool to the modernized split-panel layout with full-resolution lightmap overlays.
- [ ] **Real-Time Ray Tracing**: Implement interactive 3D ray-path visualization on the map to allow immediate inspection of "Skip vs. Drill" decisions.
- [ ] **Unified Vector Engine**: Standardize Sun, Earth, and satellite vector calculations using core library geodetic utilities instead of disparate matrix implementations.
- [ ] **External Data Integration**: Add support for loading and overlaying external map layers from sources like Moon Trek or QuickMap.

## 4. Scaling & Architecture
- [ ] **Configuration Externalization**: Move hardcoded DEM paths and simulation parameters from source code to a structured configuration system (e.g., YAML or JSON).
- [ ] **Advanced Pyramid Management**: Implement intelligent caching and invalidation strategies for `.pyr.bin` files to handle large-scale data updates.
- [ ] **Distributed Processing**: Explore multi-GPU support and multi-node distribution for processing planetary-scale datasets.
- [ ] **Python Integration (LCB)**: Migrate core logic to a high-performance DLL to support the "Lunar Compute Bridge" architecture for Python/F# interop.
- [ ] **Real-Time Pipeline**: Evolve the batch processing engine into a streaming pipeline capable of supporting live mission operations.

## 5. Reliability, Testing & Documentation
- [ ] **Emulator Synchronization**: Ensure CPU emulators (`ReferenceRayEmulator`, `QuadTreeRayEmulator`) are bit-perfect matches for their production counterparts to enable reliable "white-box" debugging.
- [ ] **Automated Benchmarking**: Establish a CI/CD performance baseline to automatically detect and flag algorithmic or resource regressions.
- [ ] **Expanded Synthetic Testing**: Build a comprehensive library of synthetic DEM test cases (craters, ridges, flats) to deterministically verify corner cases.
- [ ] **Documentation Completion**: Finalize high-level API documentation, detailed algorithm descriptions, and developer onboarding guides.
