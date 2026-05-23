# ADR 0018: Worker-Hosted Raster Transform Scripts

## 1. Status
**Accepted (Amended by ADR.0038 for agent reliability policy and internal eval pre-filter guidance)**

## 2. Context
`lunar_analyst` currently offers two ways to derive rasters:

1. **Map Algebra DSL (`raster.calculate`, ADR 0016)**  
   A restricted expression language implemented through `JobHandlers.raster_calculate`.
   It is safe and bounded, but it is awkward for multi-step logic because it lacks intermediate variables and is intentionally narrow.

2. **Scenario Scripts (`scenario.write_run_script`)**  
   Full Python flexibility, but the caller must manage GIS engineering details such as raster alignment, projection, scenario-safe output paths, and temporal signal handling.

There is a missing middle ground:

- More expressive than the expression DSL
- Familiar to agents and users who already know NumPy
- Still executed through a typed job contract
- Still backed by scenario-managed alignment, lineage, progress, and artifact registration

The intended pattern is:

1. Define one raster-transform authoring model based on NumPy-style array-parallel operations over raster-aware values.
2. Use that same model locally in notebooks and scripts through an ergonomic Python helper layer.
3. Expose that same model remotely through a typed job contract so agents can invoke it safely through FastAPI, jobs, and MCP.

The job/tool surface is therefore not a separate conceptual model.
It is the serialized, governed execution surface for the same raster-transform pattern.

## 3. Decision
We will introduce a shared raster-transform pattern and expose it as an additive raster operation named `raster.transform`.

The pattern is defined first in terms of authoring semantics: users write NumPy-style array-parallel transforms over raster inputs with a sealed helper/function surface.
That pattern is then exposed remotely through:

- typed handler: `JobHandlers.raster_transform`
- assistant/MCP tool: `raster.transform`

`raster.transform` sits beside `raster.calculate`.
It does not replace the DSL initially.
If it proves to be the better abstraction, it may later inherit the `raster.calculate` name.

The new operation is defined by these principles:

1. **Array-transform contract**  
   The user supplies a restricted Python-like transform over aligned array inputs.
   The core abstraction is an array-to-array transform, not tile streaming.

2. **Worker-hosted execution**  
   Execution occurs in the compute worker, not in the FastAPI control-plane process.

3. **Partitioning is an execution detail**  
   The worker may evaluate the transform over full arrays or over partitions of arbitrary size.
   Spatial and temporal partitioning are optimization choices, not the user-facing semantic model.

4. **Stateless per-invocation semantics**  
   The transform is defined only in terms of its provided inputs and scalar metadata.
   It must not rely on cross-partition mutable state.

5. **Typed job contract remains authoritative for remote execution**  
   The remote/public contract is defined by a typed `JobHandlers.raster_transform` signature and the generated API/tooling surface.
   Notebook and script helpers may be more ergonomic, but they must lower into the same execution model rather than define a separate remote contract.

## 4. Public Contract

### 4.1 Remote Operation Name
- Typed handler: `JobHandlers.raster_transform`
- Assistant/MCP tool: `raster.transform`

This is the remote job/tool exposure of the shared raster-transform pattern.
It is additive while `raster.calculate` remains available.

### 4.2 Request Contract
Required request fields:

- `scenario_id`
- `script`
- `inputs` as a map of variable name to input binding

Optional request fields:

- `output_relative_path` (scenario-relative output path; deterministic path generated when omitted)
- `overwrite` (default `false`)
- `mode` (`queued` or `immediate`)
- `resampling` (`nearest | bilinear | cubic`, default `bilinear`)
- `spatial_partitioning` (`auto | allowed | forbidden`, default `auto`)
- `time_partitioning` (`auto | allowed | forbidden`, default `auto`)
- `spatial_halo_pixels` (default `0`)
- `horizons_relative_dir` (default `lighting/horizons`)
- `observer_elevation_meters` (default `0.0`, only used when required by temporal signal sourcing)

Input bindings in v1 are divided into two categories:

1. **Raster bindings**
   These bind a variable name to a raster source by:
   - scenario-relative raster path
   - `product_id`

2. **Special reserved bindings**
   These bind a variable name to planner-visible non-raster domain inputs that govern raster sourcing.
   The reserved temporal domain binding for v1 is:
   - `times`

The `times` binding defines the temporal domain used by horizon-derived inputs.
Conceptually it carries:

- `start_utc`
- `stop_utc`
- `step_hours`

Horizon-derived sources are input bindings, not script-callable raster functions.
The v1 horizon-derived binding set is:

- `sun_fraction()`
- `sun_over_horizon_deg()`
- `earth_over_horizon_deg()`
- `station_over_horizon_deg("station name")`

These bindings appear in the `inputs` map and reference the reserved `times` binding.
They are not part of the script execution namespace.

Thresholded horizon helpers are explicitly out of scope for v1.
Users should express them in the script itself with normal comparisons and `where(...)`.

Raster binding identity is path-first but supports immutable IDs:

- `relative_path` (preferred user-facing identity), or
- `product_id`

Implementation note:

- The current implementation uses top-level `time_start_utc`, `time_stop_utc`, and `time_step_hours` request fields together with `signal`-style temporal input references.
- The target model described by this ADR is a reserved `times` binding plus horizon-derived input bindings in the `inputs` map.
- The implementation should converge to that model through an additive migration rather than a breaking rewrite.

### 4.3 Script Forms and Return Contract
Two authoring forms are supported:

1. **Single expression**
   Example:
   ```python
   where((slope(dem) < 15) & (dem > 2000), 1.0, 0.0)
   ```
   If the input parses as a single expression, the backend treats that value as the result.

2. **Multi-statement block**
   Example:
   ```python
   slope_deg = slope(dem)
   is_flat = slope_deg < 15
   is_elevated = dem > 2000
   result = where(is_flat & is_elevated, 1.0, 0.0)
   ```
   If the input parses as a statement block, the script must assign the output to `result`.

Rules:

- Single-expression input is internally normalized to `result = <expression>`.
- Multi-statement input must assign `result`.
- Multi-statement input does not use implicit "last expression wins" semantics.

### 4.4 Allowed Semantics in v1
Allowed in v1:

- Multiple assignment statements to intermediate variables
- Numeric and boolean literals
- NumPy-style vectorized arithmetic, comparisons, boolean operations, and broadcasting
- Direct calls to a sealed function registry
- Comments and blank lines

Not allowed in v1:

- `for`
- `while`
- Imports
- Attribute access outside the sealed safe namespace
- Mutation of external/global state
- User-defined functions
- Multi-output returns

Control flow is intentionally narrow in v1.
The design goal is vectorized array transforms, not general-purpose Python programming.

### 4.5 Alignment with Notebook and Script Authoring
`raster.transform` should mirror the local Python authoring model used in scripts and notebooks.
The remote job contract is the serialized, worker-executed form of the same conceptual operation.

That means the same concepts should exist in both places:

- raster sources
- derived rasters
- aligned shared grid
- `[time, y, x]` temporal convention
- partitioning hints
- missing-data behavior
- final 2D persisted output

The local notebook/script API is the ergonomic authoring surface over the same planner/runtime model.
`raster.transform` adds the extra metadata required for governed worker execution and persistence, such as:

- serializable input identities
- scenario/output path information
- lineage metadata
- explicit execution hints
- worker/runtime options
- stable helper/function identifiers

A notebook or script expression should be lowerable into a `raster.transform` request with minimal semantic loss.
If the local Python API and the job contract diverge materially, the design has failed.

Normative boundary:

- The shared raster/planner model described later in this ADR is the implementation substrate for both notebook/script helpers and `JobHandlers.raster_transform`.
- `JobHandlers.raster_transform` remains the authoritative wire contract for remote execution.
- Notebook/script helpers must not introduce an independent remote contract or bypass scenario/job governance when used through FastAPI.

### 4.6 Response Contract
`JobHandlers.raster_transform` should return a typed result including at least:

- `scenario_id`
- `output_relative_path`
- `output_path`
- `product_id`
- `file_id`
- `output_dtype`
- `output_nodata`
- `target_crs`
- `target_width`
- `target_height`
- `script`
- `script_hash`
- `used_variables`
- `used_functions`
- `used_operators`
- `reprojected_inputs`
- `temporal_inputs`
- `time_start_utc`, `time_stop_utc`, `time_step_hours` when applicable
- `planner_summary` including selected execution strategy and resource estimate
- `artifact_db_path`
- `progress_events`

The exact response model should be versioned with the handler signature and exported through the generated API/tool schema.

### 4.7 Progress and Error Contract
Jobs must emit structured progress events with stable phases.
Minimum v1 phases:

- `parse_validate`
- `build_plan`
- `estimate_resources`
- `resolve_inputs`
- `reproject_align`
- `execute_transform`
- `write_output`
- `register_artifact`

`raster.transform` failures must return typed machine-readable codes.
Minimum v1 error taxonomy:

- `raster_transform_parse_error`
- `raster_transform_disallowed_syntax`
- `raster_transform_missing_result`
- `raster_transform_unknown_variable`
- `raster_transform_unknown_function`
- `raster_transform_invalid_argument`
- `raster_transform_input_not_found`
- `raster_transform_crs_transform_failed`
- `raster_transform_grid_alignment_failed`
- `raster_transform_output_path_invalid`
- `raster_transform_output_exists`
- `raster_transform_plan_too_large`
- `raster_transform_temporal_time_range_required`
- `raster_transform_temporal_signal_unsupported`
- `raster_transform_temporal_horizons_not_found`
- `raster_transform_temporal_reduce_required`
- `raster_transform_temporal_stream_failed`
- `raster_transform_canceled`
- `raster_transform_internal_error`

Planning-time rejection for oversized requests must use `raster_transform_plan_too_large`.
Its details should include the estimated working set and the principal reason the planner could not produce an accepted strategy.

## 5. Array Semantics

### 5.1 Shape and Alignment
For a given invocation, all raster-valued inputs are aligned to a shared target grid before evaluation.

For v1, the target grid remains the active scenario DEM grid:

- CRS
- transform
- width
- height

This preserves the current explicit reprojection policy and keeps output artifacts scenario-managed and predictable.

### 5.2 Rank Polymorphism
The transform follows standard NumPy broadcasting semantics.

This allows combinations such as:

- 2D raster with 2D raster
- 2D raster with 3D temporal cube
- Scalar with 2D raster
- Scalar with 3D temporal cube

### 5.3 Canonical Temporal Axis Order
When temporal signals are present, arrays use shape:

`[time, y, x]`

This is the canonical axis order for v1 because:

- 2D rasters remain naturally shaped as `[y, x]`
- 2D rasters broadcast cleanly across time
- Temporal reductions such as `np.max(a, axis=0)` naturally produce `[y, x]`

### 5.4 Output Shape
Persisted output remains a single raster product.
Therefore, the final `result` must be 2D (`[y, x]`) at write time.

If a script produces a 3D result, it must reduce the time axis explicitly before output persistence.

### 5.5 Input Validity and NODATA Semantics
NumPy execution does not provide GDAL-style nodata semantics.
Therefore, nodata handling is defined as an input/output validity rule, not as part of intermediate NumPy execution.

Normative v1 rules:

- NumPy operates on plain arrays only.
- No validity masks are propagated through intermediate expressions.
- Validity is computed for the final output from the participating bound raster inputs.
- For v1, participating inputs are approximated as all bound raster inputs.
- Scalar literals and other non-raster values do not participate in output validity.

GeoTIFF-backed raster inputs:

- If an input is known fully valid, it does not participate in mask combination work.
- If an input has nodata metadata or an explicit validity mask, it contributes per-pixel validity.
- The final output validity is the conjunction of participating input validities.
- Equivalently, output invalidity is the union of participating input invalidities.
- If any participating input is fully invalid over a region, the output is fully invalid over that region.

Horizon-derived inputs:

- Horizon patches are either fully valid or fully invalid.
- If the required horizon file for a patch is present, all values computed from that patch are valid.
- If the required horizon file for a patch is missing, the entire patch is invalid.
- Missing-horizon patches must be short-circuited to output nodata without invoking the raster-transform function on those patches.
- Horizon-derived inputs contribute patch-valid / patch-invalid regions only; they do not introduce per-pixel validity masks.

Finalization/write semantics:

- The transform computes plain NumPy results first.
- After evaluation, the final output validity mask is applied.
- Invalid output cells are encoded using the output raster's nodata representation when persisted.

This model is intentionally conservative.
In particular, `where(...)` is not required to implement branch-sensitive validity in v1.
If one participating input is invalid at a cell, the final output may be marked invalid at that cell even when a branch-sensitive interpretation could have preserved a valid result.

## 6. Execution Model

### 6.1 Worker Placement
The transform executes in the compute worker.
FastAPI remains the authoritative control plane and launches the work through the existing job path.

### 6.2 Partitioning
The worker may evaluate the transform:

- over the full raster extent, or
- over spatial partitions, or
- over temporal partitions, or
- over combined spatial/temporal partitions

Partition size is not part of the public semantics.
The full-raster case is simply a degenerate partitioning strategy.

### 6.3 Partitioning Hints
Because some transforms are partition-safe and others are not, the request may carry execution hints.

Initial hint model:

- `spatial_partitioning`: `auto | allowed | forbidden`
- `time_partitioning`: `auto | allowed | forbidden`
- `spatial_halo_pixels`: optional non-negative integer

Semantics:

- `auto`: worker decides
- `allowed`: worker may partition along that axis
- `forbidden`: worker must evaluate without partitioning along that axis
- `spatial_halo_pixels`: optional overlap hint for neighborhood-style spatial transforms

These hints are advisory execution controls, not a second programming model.

### 6.4 Partition Invariance
Some useful NumPy transforms depend only on pointwise or broadcast semantics.
Others may depend on adjacent cells, for example cellular automata or stencil-like operations.

The system does not attempt to prove whether a user-supplied transform is partition-safe.
That property is undecidable in general.

Instead:

- The caller declares whether partitioning is allowed.
- If partitioning is allowed and the transform is not actually partition-safe, boundary artifacts are the caller's responsibility.
- If partitioning is forbidden, the worker must use a compatible full-extent strategy for that axis.

### 6.5 Planning-Time Resource Gate
Before execution begins, the planner may reject a transform that cannot be executed within configured working-set limits.

The planner should estimate peak working-set size from factors such as:

- target grid width and height
- temporal depth
- dtype size
- expected intermediate array count
- whether spatial partitioning is feasible
- whether time partitioning is feasible

Normative v1 behavior:

- Large 2D full-extent plans are allowed when the estimated working set is within configured limits.
- Plans that require full `x, y` materialization together with a large time axis may be rejected at planning time.
- The system is not required to start execution and fail later when the planner can determine ahead of time that the request is too large.
- Planning rejection must be returned as `raster_transform_plan_too_large` with actionable details about the estimated working set and the dimension or planning constraint that forced rejection.

## 7. Function and Namespace Model
The transform executes against a sealed namespace that is curated by the backend.

The v1 execution namespace includes:

- Numeric and boolean literals
- Vectorized arithmetic/comparison/boolean operators defined by the allowed syntax
- `where`
- `slope`
- `aspect`
- `hillshade`
- temporal reducers `min`, `max`, `avg`, `std`
- Scalar metadata needed by temporal execution, such as `time_step_hours`

The exact namespace is versioned and treated as part of the job contract.

Horizon-derived sources are not part of this namespace.
They are input bindings resolved before script execution.
In particular, the following are binds-layer concepts rather than script-callable functions:

- `sun_fraction`
- `sun_over_horizon_deg`
- `earth_over_horizon_deg`
- `station_over_horizon_deg`

The corresponding notebook/script library should expose matching helpers so users do not learn two different models.
V1 helper functions should include:

- `scenario_dem()`
- `raster_file(path)`
- `slope_raster(input_raster)`
- `aspect_raster(input_raster)`
- `hillshade_raster(input_raster, azimuth_deg=315, elevation_deg=45)`

Deferred helper functions for a later phase:

- `tri_raster(input_raster)`
- `tpi_raster(input_raster)`
- `roughness_raster(input_raster)`

These helpers should return lazy `Raster` instances rather than immediately materialized arrays.
They are part of an extensible library surface that can grow over time.

This ADR does not require `RestrictedPython` specifically.
The normative requirement is a restricted execution environment in the compute worker with an explicit syntax and symbol allowlist.
If `RestrictedPython` is used, it is an implementation choice rather than the center of the decision.

## 8. Security and Resource Model
Security goals for v1:

- Prevent code injection outside the allowed execution subset
- Prevent access to imports, arbitrary filesystem APIs, and unapproved Python object graphs
- Keep script execution within the compute-worker boundary

Non-goals for v1:

- Strong denial-of-service resistance against all long-running or high-memory user transforms beyond the planning-time working-set gate

The worker may still apply normal job cancellation, progress reporting, and operational safeguards, but v1 does not claim full DoS hardening for arbitrary user-authored array transforms.
The planning-time working-set gate is required; broader adversarial-resource hardening is not.

## 9. Output, Lineage, and Observability
`raster.transform` uses the same output-management pattern as `raster.calculate`:

- Scenario-managed output path
- Scenario-root safety validation
- GeoTIFF persistence
- File/product registration
- File-id-backed serving

Lineage and observability follow the same model as before, extended for the transform source:

- normalized script text
- script hash
- input references
- target grid reference
- reprojection/alignment actions
- execution hints
- function/helper set used
- safe-namespace version

Jobs emit structured progress and preserve typed machine-readable error codes.
At minimum, the error taxonomy should include `raster_transform_plan_too_large` for planning-stage rejection when requests exceed configured working-set limits.

## 10. Consequences

### Pros
- More expressive than the current expression DSL while preserving typed job boundaries
- Better aligned with agent familiarity with NumPy
- Keeps GIS alignment and artifact registration in backend-owned infrastructure
- Supports both simple expression-style requests and multi-step vectorized transforms
- Supports full-raster or partitioned execution without changing the public programming model

### Tradeoffs
- Validation and restricted execution are more complex than the current AST-expression DSL
- General Python control flow remains intentionally limited
- Partitioning correctness cannot be inferred automatically in the general case
- Some transforms will be rejected at planning time when they require a full-extent working set that exceeds configured limits

## 11. Out of Scope
- Retiring or renaming `raster.calculate` in this ADR
- General-purpose Python scripting inside the backend process
- Automatic proof that a transform is safe to partition
- Multi-output products from one invocation
- New focal/zonal/global operator taxonomies beyond what can be expressed through the restricted array-transform model

## 12. Implementation Plan
Implementation should be delivered as additive vertical slices that are individually mergeable and reversible.
The phases below are intentionally ordered so contract and planner behavior stabilize before broader execution and notebook ergonomics are layered on top.

### Phase 0: Feature Flag and Contract Skeleton
Goal:

- Introduce the typed remote contract and tool schema behind a feature flag without yet committing to full execution breadth.

Primary files:

- `backend/jobs/handlers.py`
- `backend/services/assistant/tool_registry.py`
- `config/lunar_analyst.toml` (or equivalent backend config)

Work:

- Add config flag for `raster.transform`.
- Add `JobHandlers.raster_transform` request/response models and typed signature.
- Export assistant/MCP tool schema behind the feature flag.
- Keep `raster.calculate` unchanged and available.

Acceptance:

- Feature off: tool is not listed/executable.
- Feature on: tool appears in job definitions and tool schema with the agreed request/response shape.

### Phase 1: Script Parser and Static Validation
Goal:

- Implement deterministic parse/validate behavior before any raster IO or worker execution begins.

Primary files:

- `backend/jobs/raster_transform.py` (or equivalent helper module)
- `backend/jobs/handlers.py`

Work:

- Support single-expression and statement-block forms.
- Normalize expression form to implicit `result`.
- Require explicit `result` assignment for statement blocks.
- Enforce syntax allowlist, symbol allowlist, and complexity limits.
- Map failures to stable `raster_transform_*` error codes.

Acceptance:

- Valid scripts compile to an internal validated form.
- Forbidden syntax and missing `result` fail deterministically with typed errors.

### Phase 2: Planner Core and Lazy Raster Graph
Goal:

- Introduce the shared lazy `Raster`/planner substrate used by both local authoring and remote execution planning.

Primary files:

- notebook/script helper library module(s)
- `backend/jobs/raster_transform.py`
- `backend/jobs/handlers.py`

Work:

- Implement `Raster` source/derived nodes.
- Collect dependencies from a transform into a planner-visible graph.
- Verify shape, rank, and grid compatibility.
- Model execution hints without executing the transform yet.

Acceptance:

- Planner can produce a deterministic plan summary for supported v1 transforms.
- The same planner substrate can represent both local helper-authored transforms and remote `script` requests.

### Phase 3: Planning-Time Resource Gate
Goal:

- Reject oversized requests before execution when the planner can prove they exceed configured working-set limits.

Primary files:

- `backend/jobs/raster_transform.py`
- `backend/jobs/handlers.py`
- backend config module(s)

Work:

- Estimate peak working-set size from grid shape, temporal depth, dtype, intermediate count, and partition feasibility.
- Add configurable thresholds for accepted plans.
- Return `raster_transform_plan_too_large` with actionable details when the estimate exceeds limits.

Acceptance:

- Large but reasonable 2D full-extent plans are accepted.
- Oversized full-extent temporal plans are rejected deterministically before execution starts.

### Phase 4: Input Resolution, Alignment, and Output Safety
Goal:

- Reuse scenario-safe raster resolution and DEM-grid alignment before transform execution.

Primary files:

- `backend/jobs/handlers.py`
- `backend/jobs/raster_transform.py`

Work:

- Resolve inputs by `relative_path`, `product_id`, or `signal`.
- Normalize and validate scenario-root safety for all input and output paths.
- Align raster inputs to the scenario DEM grid with configured/default resampling.
- Record reprojection/alignment actions for lineage and progress events.

Acceptance:

- Mixed-CRS supported inputs align to the DEM grid or fail with deterministic typed errors.
- Out-of-root and unresolved paths fail safely.

### Phase 4A: Bind Model Alignment and Validity Semantics
Goal:

- Align the implementation with the final bind-domain design for temporal inputs and conservative output validity.

Primary files:

- `backend/jobs/handlers.py`
- `backend/jobs/raster_transform.py`
- notebook/script helper library module(s)
- assistant/tool schema export modules

Work:

- Introduce a reserved `times` binding model in the request/input contract.
- Represent horizon-derived sources as bind-layer inputs rather than script-callable functions.
- Keep existing top-level temporal request fields only as a temporary compatibility path during migration.
- Compute final output validity from all bound raster inputs rather than relying on intermediate NumPy semantics.
- Short-circuit missing horizon patches to output nodata without invoking the transform over those patches.
- Record missing-horizon patch counts and related observability details in lineage/progress where practical.

Acceptance:

- The request model supports the reserved `times` binding and horizon-derived bindings.
- Missing horizon files invalidate whole patches without failing the whole job.
- Conservative output validity is applied at finalize/write time.

### Phase 5: Worker Execution for Accepted 2D Plans
Goal:

- Execute accepted non-temporal and 2D-final-result transforms in the compute worker with cancellation and progress.

Primary files:

- `backend/jobs/handlers.py`
- `backend/jobs/raster_transform.py`
- worker-side execution module(s)

Work:

- Execute accepted plans in the compute worker.
- Support full-extent and partitioned spatial execution for accepted v1 transforms.
- Emit stable progress phases.
- Preserve cooperative cancellation.

Acceptance:

- Supported 2D transforms produce scenario-managed output rasters.
- Cancellation during execution returns `raster_transform_canceled`.

### Phase 6: Temporal Arrays and Time-Axis Semantics
Goal:

- Add temporal signal sourcing and `[time, y, x]` execution semantics on top of the existing planner/runtime.

Primary files:

- `backend/jobs/handlers.py`
- `backend/jobs/raster_transform.py`
- `backend/worker/lightmap_streaming.py` (reuse existing APIs; avoid contract duplication)

Work:

- Support the reserved `times` binding and horizon-derived temporal input bindings in the request/input model.
- Validate `start_utc`, `stop_utc`, and `step_hours` on the `times` binding when temporal inputs are present.
- Support broadcasting across 2D and 3D inputs.
- Enforce final persisted result must be 2D.

Acceptance:

- Temporal transforms produce deterministic 2D outputs or fail with the defined temporal typed errors.

### Phase 7: Notebook and Script Authoring Surface
Goal:

- Expose the same planner/runtime model through ergonomic local Python helpers.

Primary files:

- notebook/script helper library module(s)
- documentation/examples as needed

Work:

- Add v1 helper functions: `scenario_dem`, `raster_file`, `slope_raster`, `aspect_raster`, `hillshade_raster`.
- Validate that helper-authored transforms lower to the same planner/runtime model used by `JobHandlers.raster_transform`.
- Document semantic parity expectations and any intentional v1 limitations.

Acceptance:

- Local helper-authored transforms and remote handler-authored transforms behave equivalently for covered v1 cases.

### Phase 8: Output Registration, Contract Verification, and Rollout
Goal:

- Complete artifact registration, contract export, and controlled rollout with rollback readiness.

Primary files:

- `backend/jobs/handlers.py`
- `backend/services/artifact_catalog.py` (or existing registration path)
- contract test modules
- config/docs updates as needed

Work:

- Persist output as a scenario-managed product/file artifact with lineage metadata.
- Export/verify OpenAPI and contract schemas if changed.
- Add tests for parser, planner, resource gate, alignment, execution, progress, cancellation, and tool schema.
- Start rollout behind the feature flag and validate rollback by disabling the flag.

Acceptance:

- Output artifacts are registered and file-id served.
- Contract and worker tests pass for all touched behavior.
- Rollback remains one-step via feature-flag disable.

### Immediate Implementation Alignment Plan
The current implementation is a useful first slice, but it does not yet fully match the refined bind-domain and validity model above.
The next implementation updates should proceed in this order:

1. **Bind contract migration**
   Add reserved `times` binding support and horizon-derived bind specs in the handler/tool schema.
   Keep the existing top-level temporal fields as a temporary compatibility layer while tests and callers are migrated.

2. **Validity model update**
   Replace any assumption that NumPy result arrays implicitly carry nodata semantics.
   Compute final output validity from participating bound raster inputs only at finalize/write time.

3. **Missing horizon patch short-circuit**
   Detect missing horizon files at patch load time.
   Mark the whole patch invalid and write output nodata directly without calling the transform for that patch.

4. **Notebook/script parity update**
   Expose the same `times` binding and horizon-derived bind concepts through the local helper/planner surface so local scripts and remote jobs share one conceptual model.

5. **Contract and regression updates**
   Update tests, examples, `HOW_TO_TEST`, and generated contract artifacts to reflect the reserved `times` binding, horizon-derived bind model, and conservative output-validity semantics.

## 13. Comparison with Existing DSL
| Feature | `raster.calculate` (ADR 0016) | `raster.transform` (ADR 0018) |
| :--- | :--- | :--- |
| Authoring model | Restricted expression DSL | Restricted array-transform script |
| Statements | No | Yes |
| Intermediate variables | No | Yes |
| Return contract | Expression value | Expression value or explicit `result` |
| Control flow | None | Intentionally narrow; no `for`/`while` in v1 |
| Execution abstraction | Expression evaluation | Array transform over aligned inputs |
| Partitioning | Internal implementation detail | Internal implementation detail with explicit hints |

## 14. Python Authoring Model Sketch
This section is normative for the shared authoring/runtime pattern, but non-normative in exact class names and helper signatures.
It sketches the local Python API that `raster.transform` should mirror and that `JobHandlers.raster_transform` should lower through.

### 14.1 Core Types
The notebook/script library should introduce a `Raster` class representing both raster identity and georeferencing metadata, not just already-loaded array data.
This `Raster` model is the shared execution substrate used by notebook/script authoring and by the remote transform planner; it is not a separate competing contract.

The class should support:

- lazy full-raster reads
- lazy block reads
- iteration over blocks when requested by the planner
- deferred derived-raster expressions
- access to grid metadata needed for compatibility checks
- access to planner metadata such as partitionability and preferred block shape

For horizon-backed and moonlib-backed sources, `Raster` should also support:

- block fetches routed through moonlib's queued loading/streaming paths
- explicit handling of missing horizon files through nodata or mask-aware metadata
- planner-visible information about whether spatial and temporal partitioning are practical

### 14.2 Suggested Class Sketch
```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class GridSpec:
    crs: Any
    transform: Any
    width: int
    height: int
    time_count: int | None = None


@dataclass(frozen=True)
class ExecutionHints:
    spatial_partitioning: str = "auto"
    time_partitioning: str = "auto"
    spatial_halo_pixels: int = 0


class Raster:
    grid: GridSpec
    dtype: str
    name: str | None
    execution_hints: ExecutionHints

    def read(self, window=None) -> Any:
        """Materialize the full raster or a requested window."""

    def blocks(self, plan=None) -> Iterator[Any]:
        """Yield lazily materialized blocks according to an execution plan."""

    def materialize(self) -> Any:
        """Materialize the full raster."""

    def planner_info(self) -> dict[str, Any]:
        """Return compatibility and execution metadata for planning."""
```

### 14.3 Suggested Source and Derived Types
Concrete implementations can vary, but the object model should distinguish between source rasters and derived rasters.

Suggested source nodes:

- `ScenarioDemRaster`
- `FileRaster`
- `MoonlibSignalRaster`

Suggested derived node:

- `DerivedRaster`

`DerivedRaster` should carry:

- the operation identifier
- references to input rasters
- scalar parameters
- any operation-specific metadata needed by the planner

### 14.4 Suggested Helper Functions
The v1 Python helper library should start with:

```python
def scenario_dem() -> Raster: ...
def raster_file(path: str | Path) -> Raster: ...
def slope_raster(src: Raster) -> Raster: ...
def aspect_raster(src: Raster) -> Raster: ...
def hillshade_raster(
    src: Raster,
    azimuth_deg: float = 315.0,
    elevation_deg: float = 45.0,
) -> Raster: ...
```

These helpers should build lazy raster-expression nodes.
They should not force immediate data reads except when explicitly requested.

Later helper expansion may add:

- `tri_raster`
- `tpi_raster`
- `roughness_raster`

### 14.5 `let`-Style Binding Pattern in Python
The useful part of a Lisp-style `let` for this system is not syntax.
It is:

- binding names to lazy raster expressions
- resolving dependencies between bindings
- checking compatibility
- choosing full-raster versus partitioned execution
- materializing only at the end

The recommended Python pattern is an explicit environment object plus a `raster_let(...)` builder:

```python
from types import SimpleNamespace
from typing import Any, Callable


class RasterLet:
    def __init__(self, bindings: dict[str, Any]) -> None:
        self.bindings = bindings

    def eval(self, body: Callable[[Any], Any]) -> Any:
        env = SimpleNamespace()
        resolved: dict[str, Any] = {}

        for name, value in self.bindings.items():
            resolved_value = value(env) if callable(value) else value
            resolved[name] = resolved_value
            setattr(env, name, resolved_value)

        validate_bindings(resolved)
        return plan_result(body(env), resolved)


def raster_let(**bindings: Any) -> RasterLet:
    return RasterLet(bindings)
```

This allows dependent bindings:

```python
result = raster_let(
    dem=scenario_dem(),
    slope=lambda r: slope_raster(r.dem),
    rough=lambda r: roughness_raster(r.dem),
).eval(
    lambda r: np.where((r.slope < 15) & (r.rough < 5), r.dem, np.nan)
)
```

This is the Python equivalent of `let` for lazy raster expressions.

### 14.6 Planner Responsibilities
The planning step behind `raster_let(...).eval(...)` should:

- collect all reachable raster nodes
- verify shape, rank, and grid compatibility
- align inputs when needed
- inspect execution hints
- decide full-raster versus spatial/time partitioned execution
- estimate peak working-set size before execution
- reject plans that exceed configured working-set limits
- account for halo requirements when declared
- route moonlib-backed rasters through the appropriate block fetch path
- define missing-horizon behavior as nodata or mask-aware propagation

This planning model is what notebook/script helpers and `raster.transform` should both lower into before handing work to the compute worker.

### 14.7 NumPy Integration
To keep notebook code natural, the `Raster` type should eventually support NumPy-style expression building through mechanisms such as `__array_ufunc__` and, where practical, `__array_function__`.

That would allow expressions like:

```python
result = np.where((slope_raster(dem) < 15) & (dem > 2000), dem, np.nan)
```

to build lazy raster-expression graphs instead of forcing immediate materialization.
