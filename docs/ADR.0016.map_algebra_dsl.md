# ADR.0016: Restricted Map Algebra DSL via Typed Job Handler

- Status: Accepted (compatibility-only path; deprecation proposed by ADR.0038)
- Date: 2026-03-05
- Owners: Architecture (Codex), Implementation (Gemini)
- Related: `AGENTS.md`, `docs/ADR.0001.process_model.md`, `docs/ADR.0002.scenario_filesystem_and_catalog.md`, `docs/ADR.0006.raster_delivery_crs_policy.md`, `docs/ADR.0011.ai_assistant_and_mcp.md`, `docs/ADR.0012.python_net_native_bridge.md`

## Context
Lunar Analyst currently allows agent-authored computation through `scenario.write_script` + `scenario.run_script`.
That path is flexible but has two problems:
1. Security and governance risk from arbitrary script execution.
2. High complexity for common raster analysis requests that should be one typed operation.

We need a safe, high-frequency analysis path that still follows architecture invariants:
- FastAPI remains the authoritative control plane.
- Heavy/long-running compute follows worker/job boundaries.
- `JobHandlers` signatures remain the source of truth for compute contracts.
- CRS handling remains explicit (no silent reprojection).
- Outputs are scenario-managed artifacts with lineage and file-id serving.

## Decision
Introduce a restricted raster expression DSL, exposed to assistants as `raster.calculate`, implemented through a typed `JobHandlers` job contract.

### 1) Execution Model and Boundaries
1. Add a typed handler contract in `backend/jobs/handlers.py` (for example `JobHandlers.raster_calculate`).
2. Add a thin assistant/MCP tool `raster.calculate` in `tool_registry.py` that launches this typed job.
3. `raster.calculate` is mutating and uses existing confirmation policy (`launch_job` action type).
4. No parallel compute-contract layer is introduced outside `JobHandlers`.

This keeps assistant ergonomics while preserving process and contract invariants.

### 2) `raster.calculate` v1 Contract
Required request fields:
- `scenario_id`
- `expression` (DSL string)
- `inputs` (map of variable name -> raster reference)

Optional request fields:
- `output_relative_path` (scenario-relative output path)
- `overwrite` (default `false`)
- `mode` (`queued` or `immediate`)
- `resampling` (default `bilinear`)
- `time_start_utc`, `time_stop_utc`, `time_step_hours` (required when temporal signal inputs are used)
- `horizons_relative_dir` (defaults to `lighting/horizons`)

Raster reference is path-first but supports immutable IDs:
- `relative_path` (preferred user-facing identity), or
- `product_id`
- `signal` for temporal streaming inputs:
  - `lighting_raster` (native `sun_fraction_u8`, byte `[0,255]` encoding sun fraction)
  - `earth_above_horizon` (native `earth_center_margin_deg_f32`, degrees)
  - `sun_above_horizon` (native `sun_center_margin_deg_f32`, degrees)

### 3) DSL Semantics (v1)
The DSL is expression-only and raster-valued.

Allowed:
- Arithmetic: `+`, `-`, `*`, `/`, `**`
- Comparisons: `>`, `<`, `>=`, `<=`, `==`, `!=`
- Boolean raster ops: `&`, `|`, `~`
- Variables: names from the `inputs` map only
- Functions: `where(cond, a, b)`, `slope(r)`, `aspect(r)`, `hillshade(r, az, el)`
- Temporal reducers: `min(t)`, `max(t)`, `avg(t)`, `std(t)` where `t` is a 3D temporal raster `[time, height, width]`

Temporal semantics:
- Expressions may combine 2D rasters and 3D temporal rasters using NumPy-style broadcasting.
- Final output must be 2D. If expression result remains 3D (`time > 1`), request fails unless reduced with `min/max/avg/std`.
- `slope`, `aspect`, and `hillshade` are 2D-only functions in v1.

Not allowed in v1:
- Statements (`if`, `for`, `while`, assignment)
- Attribute access
- Imports
- Arbitrary Python calls
- Arbitrary axis/index slicing syntax

Rationale: v1 outputs are persisted rasters with deterministic shape and bounded memory behavior.

### 4) Reprojection and Grid Alignment (Required in v1)
In v1, output grid is fixed to the scenario primary DEM grid (CRS, transform, width, height).
This keeps reprojection explicit by policy while avoiding per-call grid ambiguity.

Execution behavior:
- Inputs not already aligned to the scenario DEM grid are reprojected/resampled before expression evaluation.
- Reprojection details are emitted in result metadata and job events.
- Output analysis artifact stays in scenario DEM CRS/grid.
- Map display reprojection for web clients remains governed by ADR.0006 (display derivatives to `ESRI:103878` as needed).
- Temporal signal inputs are generated on DEM-constrained rectangular horizon patch windows and evaluated in streamed tile order.

### 5) Output and Lineage Contract
1. Handler writes a scenario-managed GeoTIFF output.
2. If `output_relative_path` is omitted, backend generates a deterministic name from expression hash + inputs.
3. Output is registered in `scenario.db` with product/file records and lineage metadata:
   - expression text and normalized AST hash
   - input references
   - scenario DEM grid reference and reprojection actions
   - function/operator set used
4. Output is served via existing file-id mapping endpoints, not raw absolute paths.

### 5.1) Output Data Type and Nodata Rules (v1)
1. Output dtype is determined by expression semantics when known.
2. If dtype cannot be determined confidently, default to `float32`.
3. Nodata propagation is enabled for non-byte outputs by default.
4. Byte outputs default to no nodata propagation unless explicitly configured by future policy.
5. Nodata behavior and chosen dtype are recorded in output metadata.

### 6) Security Model
1. Use Python `ast` in `eval` mode with explicit node allowlist.
2. Reject any node outside allowlist, including attribute access and comprehensions.
3. Evaluate only against a sealed function registry and variable map.
4. Enforce expression size/depth limits.
5. Run with job timeout + cooperative cancellation checkpoints.
6. Use tile/window streaming so memory usage is bounded by configured working-set limits.

No `eval/exec` of unrestricted code and no dynamic import path are permitted.

### 7) Progress, Cancellation, and Observability
Job emits structured progress events through existing job channels, with phases such as:
- parse_validate
- resolve_inputs
- reproject_align
- evaluate_tiles
- write_output
- register_artifact

Long-running evaluations must honor cancellation and return a typed canceled status.

### 8) Error Contract (v1)
`raster.calculate` failures return structured error codes with actionable details:
- `map_algebra_parse_error`: DSL parse failure (with location and token hint).
- `map_algebra_disallowed_syntax`: AST contains forbidden node/syntax.
- `map_algebra_unknown_variable`: expression references an undefined input variable.
- `map_algebra_unknown_function`: expression calls an unsupported function.
- `map_algebra_invalid_argument`: function/operator argument shape or type is invalid.
- `map_algebra_input_not_found`: referenced raster input cannot be resolved.
- `map_algebra_crs_transform_failed`: reprojection/alignment failed for an input.
- `map_algebra_grid_alignment_failed`: aligned raster grid could not be produced.
- `map_algebra_output_path_invalid`: output path fails scenario-root/path-safety checks.
- `map_algebra_output_exists`: output exists and `overwrite=false`.
- `map_algebra_timeout`: job exceeded configured execution timeout.
- `map_algebra_canceled`: job canceled by user/system request.
- `map_algebra_internal_error`: unexpected execution failure.
- `map_algebra_temporal_time_range_required`: temporal inputs were used without complete `[start, stop, step]`.
- `map_algebra_temporal_signal_unsupported`: unknown temporal signal alias in `inputs`.
- `map_algebra_temporal_horizons_not_found`: required horizons directory was not found.
- `map_algebra_temporal_reduce_required`: expression result is 3D and must be reduced to 2D.
- `map_algebra_temporal_stream_failed`: streaming temporal tile ingestion/evaluation failed.
- `map_algebra_temporal_axis_too_large`: requested temporal axis exceeds configured working-set limit.

## Consequences
Positive:
- Assistant gets a first-class, low-friction raster algebra tool without arbitrary Python execution.
- Compute remains in established job and process boundaries.
- Reprojection is supported in v1 with explicit, auditable grid policy.
- Outputs are immediately reusable by downstream jobs/tools through standard product/file registration.

Tradeoffs:
- Initial function set is intentionally narrow.
- We must maintain AST validation, function registry, and deterministic reprojection behavior.
- Some prior script-based one-offs will still require notebook/script routes.

## Out of Scope
- Full focal/zonal/global map algebra taxonomy.
- User-defined functions and custom kernels.
- Multi-output expressions in a single call.
- New API versioning changes; this is additive within existing `/api/v1` patterns.
- Per-call custom target grid overrides (v1 always uses scenario DEM grid).

## Implementation Plan
Implementation is delivered as additive vertical slices (~1 hour each), with each slice mergeable and reversible.

### Phase 0: Feature Flag and Contract Skeleton
Goal:
- Add a guarded path for `raster.calculate` so rollout can be controlled.

Primary files:
- `backend/jobs/handlers.py`
- `backend/services/assistant/tool_registry.py`
- `config/lunar_analyst.toml` (or equivalent backend config)

Work:
- Add config flag (for example `backend.features.enable_raster_calculate`).
- Add `JobHandlers.raster_calculate` request/response models and typed signature.
- Add tool metadata/schema behind the feature flag.

Acceptance:
- Feature off: tool is not listed/executable.
- Feature on: tool appears in schema and routes to typed job launch.

### Phase 1: DSL Parser and Static Validation
Goal:
- Implement safe parse/validate pipeline with deterministic errors before raster IO starts.

Primary files:
- `backend/jobs/map_algebra.py` (map algebra helper module)
- `backend/jobs/handlers.py`

Work:
- Implement AST parser in `eval` mode.
- Enforce node allowlist and max expression size/depth.
- Validate variable names against `inputs` map.
- Validate function names/signatures for v1 function set.
- Map failures to v1 error codes (`map_algebra_parse_error`, `map_algebra_disallowed_syntax`, etc.).

Acceptance:
- Allowed expressions compile to validated internal form.
- Forbidden syntax fails with deterministic error code + actionable detail.

### Phase 2: Input Resolution and Scenario Safety
Goal:
- Resolve input raster references safely and deterministically within scenario boundaries.

Primary files:
- `backend/jobs/handlers.py`
- `backend/services/assistant/tool_registry.py`
- `backend/jobs/runtime_context.py` (if helper extension is needed)

Work:
- Resolve each input by `relative_path` or `product_id` (path-first preferred).
- Normalize and validate all resolved paths are under scenario root.
- Reject missing/ambiguous inputs with typed errors.

Acceptance:
- Out-of-root and unresolved references fail with `map_algebra_input_not_found` or `map_algebra_output_path_invalid` as applicable.

### Phase 3: Grid Alignment and Reprojection to Scenario DEM Grid
Goal:
- Align all input rasters to scenario DEM grid using configured/default resampling.

Primary files:
- `backend/jobs/map_algebra.py`
- `backend/jobs/handlers.py`

Work:
- Read scenario DEM grid metadata (CRS/transform/shape) as canonical target.
- Reproject non-aligned inputs into target grid (default `bilinear`).
- Emit structured progress events for alignment phase.
- Record reprojection metadata for lineage.

Acceptance:
- Mixed-CRS inputs produce aligned arrays on DEM grid or fail with `map_algebra_crs_transform_failed` / `map_algebra_grid_alignment_failed`.

### Phase 4: Tile-wise Expression Evaluation + Nodata/Dtype Rules
Goal:
- Execute validated expressions in bounded-memory tiled processing.

Primary files:
- `backend/jobs/map_algebra.py`
- `backend/jobs/handlers.py`

Work:
- Implement tile/window execution loop with cancellation checkpoints.
- Apply nodata propagation for non-byte outputs; default no propagation for byte outputs.
- Determine output dtype from expression semantics, fallback `float32`.
- Emit evaluation progress events.

Acceptance:
- Large rasters execute without full-raster memory materialization.
- Cancellation during evaluation returns `map_algebra_canceled`.

### Phase 4b: Temporal Signal Inputs and 3D Reducers
Goal:
- Support DEM-constrained temporal signal streaming and 3D reducer semantics in `raster.calculate`.

Primary files:
- `backend/jobs/handlers.py`
- `backend/jobs/map_algebra.py`
- `backend/worker/lightmap_streaming.py` (reuse existing APIs; avoid contract duplication)

Work:
- Extend input reference schema with `signal` aliases (`lighting_raster`, `earth_above_horizon`, `sun_above_horizon`).
- Require and validate temporal range `[time_start_utc, time_stop_utc, time_step_hours]` when temporal inputs are used.
- Stream V2 signal chunks (`mode=signal_stream`) and evaluate expressions tile-wise.
- Add reducer functions `min/max/avg/std` over time axis.
- Enforce 2D final output requirement (`map_algebra_temporal_reduce_required` when violated).

Acceptance:
- Temporal signal expressions produce scenario-managed 2D output rasters.
- Reducers are deterministic and tested for shape/type/error behavior.

### Phase 5: Output Write, Registration, and Lineage
Goal:
- Persist output as scenario-managed product and integrate with existing file-id serving.

Primary files:
- `backend/jobs/handlers.py`
- `backend/services/artifact_catalog.py` (or existing registration path used by jobs)

Work:
- Resolve/create output path (deterministic default when omitted).
- Enforce overwrite policy with `map_algebra_output_exists`.
- Write GeoTIFF output with chosen dtype/nodata policy.
- Register product/file records and lineage metadata in `scenario.db`.
- Return product/file identifiers and output path metadata in handler response.

Acceptance:
- Output is visible to existing product/file listing APIs and retrievable by file-id endpoint.

### Phase 6: Assistant/MCP Tool Wiring and UX-Grade Errors
Goal:
- Provide a stable tool interface for models with correction-friendly failures.

Primary files:
- `backend/services/assistant/tool_registry.py`
- `backend/services/assistant/assistant_service.py` (only if needed for surfaced detail)

Work:
- Finalize JSON schema for `raster.calculate`.
- Map tool to `launch_job` confirmation action type.
- Ensure returned errors preserve `code`, `message`, and actionable `details`.

Acceptance:
- Assistant tool calls can recover from syntax/validation errors in follow-up turns without manual log inspection.

### Phase 7: Test Matrix and Contract Export
Goal:
- Lock behavior with worker + contract coverage before enabling by default.

Primary files:
- `backend/tests/worker/test_map_algebra_dsl.py` (new)
- `backend/tests/worker/test_map_algebra_handler.py` (new)
- `backend/tests/contract/test_mcp_tool_registry.py` (update)
- `backend/tests/contract/*` relevant job/tool contract suites (update/add)

Work:
- Add deterministic tests for:
  - AST allowlist/denylist and error codes
  - CRS alignment/reprojection behavior
  - nodata/dtype rules (`float32` fallback, byte exception)
  - output registration + file-id serving
  - progress and cancellation behavior
  - tool schema + confirmation metadata
- Export/verify OpenAPI and contract schemas if changed.

Acceptance:
- All required worker + contract tests pass in env_311.

### Phase 8: Controlled Rollout
Goal:
- Enable safely with observability and rollback readiness.

Primary files:
- `config/lunar_analyst.toml`
- operational docs/notes as needed

Work:
- Start with feature flag disabled by default.
- Enable in development scenarios first; capture run/error telemetry.
- Promote to default-on after validation and bug burn-down.

Acceptance:
- Rollback remains one-step (disable flag) with no schema/data migration dependency.

## Implementation Checklist
- [ ] Phase 0 complete: feature flag and `JobHandlers.raster_calculate` contract skeleton are merged.
- [ ] Phase 1 complete: AST parser/validator enforces allowlist and returns deterministic validation errors.
- [ ] Phase 2 complete: input resolution supports `relative_path`/`product_id` with scenario-root safety checks.
- [ ] Phase 3 complete: mixed-CRS inputs align to scenario DEM grid with default `bilinear` resampling.
- [ ] Phase 4 complete: tile-wise evaluator runs with cancellation checkpoints, dtype fallback, and nodata policy.
- [ ] Phase 4b complete: temporal signal inputs + `min/max/avg/std` reducers are implemented and validated.
- [ ] Phase 5 complete: output write + product/file registration + lineage metadata are persisted in `scenario.db`.
- [ ] Phase 6 complete: assistant/MCP `raster.calculate` schema/action-type wiring is enabled behind feature flag.
- [ ] Phase 7 complete: worker/contract tests added and passing; OpenAPI/contract exports refreshed if changed.
- [ ] Phase 8 complete: staged rollout executed with telemetry review and documented go/no-go decision.
- [ ] Validation requirements satisfied (security, CRS, output, lifecycle, and error-code coverage).
- [ ] Rollback path verified (feature-flag disable tested in a live dev environment).

## Validation Requirements
- Contract tests:
  - tool schema exposure and confirmation metadata
  - handler contract visibility in job definitions/routes
- Security tests:
  - disallow imports, attributes, unknown calls, oversized/deep AST
- CRS tests:
  - mixed-CRS inputs reproject to scenario DEM grid
  - output metadata records reprojection actions
- Output tests:
  - scenario-relative output path safety
  - product/file registration and file-id serving
  - dtype fallback to `float32` when semantics are unknown
  - nodata propagation for non-byte outputs and default byte behavior
- Lifecycle tests:
  - progress events emitted in order
  - cancellation during tile evaluation stops promptly and reports canceled status
- Error tests:
  - each defined v1 error code is emitted for at least one deterministic failure case
  - temporal-specific failures (`time_range_required`, `signal_unsupported`, `reduce_required`, `stream_failed`) are covered

## Rollback Plan
If v1 introduces correctness or stability regressions:
1. Disable `raster.calculate` tool registration behind feature flag.
2. Keep handler code path but deny launch for non-admin/testing contexts.
3. Fall back to existing typed jobs + script/notebook tools while preserving produced artifacts.
