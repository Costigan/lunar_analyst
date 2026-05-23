# ADR.0036: High-Performance Lunar Viewshed Implementation

## Status
Accepted

## Context
The Lunar Analyst requires a high-performance viewshed calculation engine to support terrain and lighting analysis. The implementation must handle the specific geometry of the Moon (tight curvature, no atmospheric refraction) and support three primary input scenarios:
1.  A single observer point.
2.  A list of observer points ("sparse observer list").
3.  A boolean raster of observer points ("dense observer mask").

Performance targets are defined by a benchmark matrix across hardware tiers (e.g., laptop GPU vs. workstation), with SLO targets for p50/p95 latency.

## Decision
We will use a **hybrid implementation**:
- **Base engine (default):** `osgeo.gdal.ViewshedGenerate` for single-observer and small observer-list workloads.
- **High-volume engine:** custom **Numba CUDA** discrete ray-casting for dense many-observer workloads.

Complexity by mode:
- **Single observer over an N x N DEM (GDAL):** $O(N^2)$ traversal work.
- **Small observer list (GDAL loop + merge):** approximately linear in observer count times per-observer traversal/IO cost.
- **Observer list / observer mask (CUDA path):** scales with observer count and average ray length; directional batching improves constants but does not reduce worst-case asymptotic work.

### 1. Algorithm: Discrete Ray Casting (Max-Slope Walk)
We will use a "Max-Slope Walk" algorithm with **Parametric Ray Traversal**:
-   **Major-Axis Normalization:** The number of steps ($N$) is set to the maximum pixel-delta ($\max(|dx|, |dy|)$) to provide an optimal azimuth-dependent step size.
-   **Parametric Traversal:** Each step $i$ calculates coordinates and distance using Fused Multiply-Add (FMA) logic ($x = x_{obs} + i \cdot x_{inc}$). 
-   **Sub-Pixel Radial Step:** CUDA ray marching uses a configurable radial step in pixel units (`backend.viewshed.cuda_ray_step_size_pixels`, default `0.5`) to reduce angular aliasing/skip-through artifacts.
-   **Supercover Cell Traversal:** For each marched segment, we mark/evaluate all crossed cells (supercover traversal) rather than only one sampled cell. This is required to avoid pinhole gaps in the visibility raster.
-   **Model-Dependent Precision:** 
    -   **Standard Mode (FP32):** Default for the parabolic approximation model to maximize throughput.
    -   **High-Fidelity Mode (FP64):** Automatically triggered if the maximum ray distance exceeds a configured threshold or explicitly requested. Uses exact spherical geometry to prevent numeric drift over long-range horizons.

### 2. Lunar Curvature and Error Envelope
Default curvature correction uses a **small-angle parabolic sag approximation** relative to the observer-referenced baseline:
$$Z_{adj} = Z_{raw} - \frac{d^2}{2R_{moon}}$$
where $R_{moon} \approx 1,737,400$ meters. 

**Approximation Error:**
The error between the parabolic approximation and exact spherical geometry is approximately $\frac{d^4}{8R^3}$. For lunar radii, this yields:
-   **25 km:** ~1 cm error
-   **50 km:** ~15 cm error
-   **100 km:** ~2.4 m error
The system will provide distance-based guidance to operators and automatically switch to FP64/Exact-Sphere if the error exceeds configured tolerance.

**Auto-Switch Contract:**
- Configuration key: `backend.viewshed.parabolic_error_tolerance_m` (meters; default set by mission profile).
- For a requested run, estimate max parabolic-vs-spherical drop error from the requested max range.
- If estimated error `>` `parabolic_error_tolerance_m`, force High-Fidelity Mode (FP64 exact-sphere).
- If `force_parabolic=true` is requested while estimate exceeds tolerance, reject with a validation error unless an explicit override flag is provided.

### 3. Many-Observer Optimization
-   **Directional Passes:** For high-volume observer cases, the engine iterates through discrete azimuths (360-720 directions).
-   **Direction Lattice Control:** Direction count is configurable (`backend.viewshed.cuda_ray_direction_count`, default `720`).
-   **Directional Warps:** Threads in a CUDA warp walk rays in the same direction to improve memory coalescing under favorable spatial layouts. Actual gains are hardware and data-dependent.
-   **Execution Profiles:** The system differentiates between a "sparse observer list" (optimized for block-level parallelism) and a "dense observer mask" (optimized for directional sweeps).

### 3.1 Observer Spatial Distribution Policy
Observer count alone is not sufficient for backend routing. Spatial distribution materially affects performance:
- **Clustered/adjacent observers:** often higher cache locality and better coalescing potential for directional CUDA sweeps.
- **Dispersed/isotropic observers:** lower locality; CUDA gains may diminish relative to GDAL looping for modest counts.
- **Routing metrics (computed pre-run):**
  - `observer_count`
  - `observer_density` (observers / DEM pixels)
  - `adjacency_ratio` (fraction of observer pixels with 4/8-neighbor observer support)
  - `component_count` and largest connected component size in observer mask
- `auto` routing will use both count and distribution metrics, not count alone.

### 4. Hardware and Performance Strategy
-   **Execution Engine:** Numba (CPU/CUDA) using FP32/FP64 paths.
-   **Instruction Density:** Implementation recognizes that while texture/read-only caches reduce memory latency, they do not change the worst-case asymptotic work. The core optimization focus remains on **ALU instruction throughput**.
-   **Control Flow:** Kernel design will minimize divergence and maximize occupancy, with specific strategies (active masks vs. early exit) tuned via profiling of warp execution efficiency and memory throughput.

### 5. Integration and Lifecycle
-   **Control Plane:** Exposed via FastAPI handler-centered contracts. Compute logic lives in `backend/jobs/handlers.py` method bodies, and tool/job signatures remain the source of truth for generated routes.
-   **Operations:** Must support cancellation and provide structured progress events during long-running directional sweeps.
-   **Base-Case Binding:** `osgeo.gdal.ViewshedGenerate` is invoked from handler logic for single/small-list cases. Multi-observer base-case behavior is implemented as per-observer GDAL calls with deterministic merge semantics (`any_visible`, `visibility_count`, or profile-specific reducer).

### 5.1 Backend Routing Policy
- Runtime selector: `backend.viewshed.backend_mode = gdal | cuda | auto`.
- `gdal`: force GDAL implementation for all supported inputs.
- `cuda`: force CUDA implementation (error if CUDA unavailable).
- `auto`: select backend using calibrated threshold model based on observer count plus spatial distribution metrics.

### 5.2 Threshold Calibration Plan (GDAL -> CUDA Switch)
The switch threshold will be empirical and hardware-tier specific.

Phase 1: Benchmark corpus
- DEM sizes: representative lunar products (for example 2k, 4k, 8k, 10k).
- Observer workloads:
  - Single observer
  - Small list (e.g., 4, 8, 16, 32, 64)
  - Boolean masks with controlled distributions:
    - clustered blobs (high adjacency)
    - corridor/linear patterns
    - uniformly random sparse points (low adjacency)
    - checkerboard-like separated points
- Max range bins and both precision modes (parabolic and exact-sphere where applicable).

Phase 2: Measure
- p50/p95 wall time, throughput (observers/sec), peak memory, and cancellation responsiveness.
- Record crossover points where CUDA outperforms GDAL by a target margin (e.g., >=20% p95 improvement).

Phase 3: Fit routing rule
- Derive initial auto-rule from benchmark matrix:
  - base threshold by `observer_count`
  - adjust threshold downward for high `adjacency_ratio` / large connected components
  - adjust threshold upward for highly dispersed observers
- Persist rule as config defaults with per-hardware override capability.

Phase 4: Validate and lock
- Add regression benchmarks in CI/perf harness for representative tiers.
- Document threshold defaults and expected operating envelope in this ADR and operator docs.

### 5.3 Lunar Analyst Product Integration
The viewshed capability is integrated as both a user-runnable job and an assistant-callable tool through the existing handler-contract pipeline.

- **Handler contract location:** `backend/jobs/handlers.py` under `ToolImplementations.*` with `@contract(...)`.
- **Generated job route:** Additive `POST /api/v1/jobs/...` route generated from the typed handler signature.
- **Jobs UI exposure:** Appears in `GET /api/v1/job-definitions` and is runnable from the Tools panel (same launch/cancel/status flow as existing jobs).
- **Assistant/MCP exposure:** Public tool definition is discovered by assistant catalog and callable through standard tool selection (`tools.search` / `tools.describe` / tool call loop).
- **Visibility policy:** initial rollout should use `ToolVisibility.PUBLIC` once ratified; use `DRAFT` only during signature review.
- **Confirmation policy:** mutating job launch remains confirmation-gated as `launch_job` per existing assistant policy.
- **Execution selectors in request schema:**
  - `backend_mode`: `gdal | cuda | auto`
  - `merge_mode` for multi-observer GDAL loops (for example `any_visible`, `visibility_count`)
  - precision controls (`force_parabolic`, optional explicit high-fidelity request)
- **Operational contract:** support cancellation checkpoints and structured progress events for both GDAL-loop and CUDA paths; emit backend selection and fallback reason in progress/log metadata.
- **Artifact contract:** output raster must be registered to scenario artifacts/catalog with CRS and parameter lineage metadata (including selected backend and routing metrics).

### 5.4 Detailed Implementation Plan
Phase A: Contract ratification and API surface
1. Add/ratify typed request/response models in `backend/jobs/handlers.py`:
- Request fields: `scenario_id`, DEM source/path policy, observer inputs (`single`, `list`, `mask`), observer height, target height, max range, curvature/precision options, `backend_mode`, `merge_mode`, output path/overwrite policy.
- Response fields: output artifact identity (`file_id`, `product_id`, `output_path`), backend selected, routing metrics snapshot, run stats, and progress trace summary.
2. Add handler contract entry in `ToolImplementations`:
- Canonical name: `ToolImplementations.generate_los_viewshed`.
- Public tool identity (for assistant and Tools panel) and confirmation metadata (`launch_job`).
3. Export/refresh generated contracts:
- Update generated OpenAPI/contracts artifacts after signature ratification.

Phase B: GDAL base implementation (must land first)
1. Implement single-observer path using `osgeo.gdal.ViewshedGenerate` in handler logic.
2. Implement small-list path as deterministic per-observer GDAL runs with merge reducer:
- `merge_mode=any_visible`: binary union.
- `merge_mode=visibility_count`: integer count raster.
3. Implement validation/error mapping:
- CRS mismatch/invalid coordinates
- out-of-root/invalid output path
- output exists without overwrite permission
- cancellation and runtime failure mapping to stable machine codes.
4. Register outputs in scenario artifact/catalog with lineage metadata:
- backend used (`gdal`)
- parameters hash
- observer workload summary.

Phase C: Routing metrics and `auto` policy
1. Add pre-run metric computation for observer-list/mask inputs:
- `observer_count`, `observer_density`, `adjacency_ratio`, `component_count`, `largest_component_size`.
2. Implement `auto` selector:
- initial rule-based policy seeded from benchmark defaults.
- emit `route_selected`, `route_reason`, and metric payload in progress/log events.
3. Configuration keys:
- `backend.viewshed.backend_mode`
- threshold and weighting keys for count/distribution-based crossover decisions.

Phase D: CUDA implementation (phase-gated after GDAL)
1. Implement Numba CUDA execution path for high-volume list/mask workloads.
2. Keep output semantics compatible with GDAL path for overlapping inputs:
- same merge behavior definitions
- same nodata/visibility encoding conventions.
3. Add CUDA preflight checks:
- device availability/capability checks
- required kernel parameter constraints.
4. Add controlled fallback:
- in `auto`: fallback to GDAL with explicit event/log annotation.
- in forced `cuda`: fail fast with actionable error.

Phase E: Product-surface integration
1. Jobs UI path:
- verify presence in `GET /api/v1/job-definitions`.
- verify launch/cancel/status behavior through existing jobs lifecycle.
2. Assistant/MCP path:
- verify tool discoverability via `tools.search`/`tools.describe`.
- verify direct call execution with confirmation-gated launch.
3. Ensure no duplicate compute-contract layer is introduced outside `ToolImplementations` signatures + handler bodies.

Phase F: Verification and acceptance
1. Unit/worker tests:
- observer input parsing/normalization
- merge reducers
- route selector behavior (`gdal`, `cuda`, `auto`)
- fallback and error taxonomy.
2. Contract tests:
- generated job route and schema shape
- event payload shape for progress/cancellation.
3. Integration tests:
- end-to-end run from job launch to artifact registration.
- cancellation during GDAL loop and CUDA path.
4. Scientific/regression tests:
- near-horizon edge cases.
- toleranced comparisons against legacy baseline.
- GDAL-vs-CUDA parity checks in overlapping regimes.

Phase G: Threshold calibration and operationalization
1. Run benchmark matrix (DEM sizes, observer counts, clustered/dispersed distributions, range bins, precision modes).
2. Identify GDAL->CUDA crossover per hardware tier and set initial defaults.
3. Persist defaults in config and document operator guidance for `gdal | cuda | auto`.
4. Recalibration policy:
- rerun benchmark suite when CUDA kernel strategy or hardware tier assumptions change.

## Validation & Acceptance
Implementation is not complete without:
1.  **Numeric Validation:** Regression tests against exact-spherical reference values for various distances and terrains.
2.  **Horizon Classification:** Specific tests for near-horizon visibility edge cases.
3.  **Performance Suite:** Benchmarking of all three observer profiles (Single, List, Mask) against SLO targets.
4.  **Routing Validation:** Evidence-backed `auto` backend routing thresholds, including adjacency-sensitive mask distributions.
5.  **Backend Parity Checks:** For overlapping supported regimes, GDAL and CUDA outputs are compared with defined tolerance/consistency expectations.

## Consequences

### Expected Benefits
-   **Scalability:** Feasible "Many-to-Many" visibility for dense lunar landing zone analysis using CUDA path where it materially helps.
-   **Accuracy:** Explicit control over the performance/precision tradeoff via the FP32/FP64 mode switch.
-   **Efficiency:** Low-volume cases leverage mature GDAL LOS behavior; high-volume cases use CUDA with distribution-aware routing.

### Known Risks
-   **Numeric Degradation:** Silent quality loss if ray lengths grow without a corresponding switch to high-fidelity mode (mitigated by automatic thresholds).
-   **Hardware Dependency:** Accelerated performance is tied to NVIDIA GPU availability and can vary materially by GPU architecture and memory bandwidth.
-   **Routing Misclassification:** A poor auto-threshold can route workloads to the slower backend (mitigated by calibration and periodic benchmark refresh).

### Fallback Behavior
-   **CPU Fallback:** A multi-threaded Numba CPU implementation will serve as the fallback. 
-   **Precision Fallback:** High-fidelity (exact-sphere/FP64) remains available on CPU when CUDA path is unavailable; if runtime exceeds latency budgets, the system warns and offers parabolic mode as an operator-approved tradeoff.
-   **Backend Fallback:** In `auto` mode, if CUDA is unavailable or fails preflight/runtime checks, execution falls back to GDAL with explicit event/log annotation.
