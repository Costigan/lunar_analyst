# Phase 4.5: Shared Horizon Store Plan (Schema + DB/API Tasks)

Date: 2026-02-16
Status: Partially Implemented (MVP slice)
Scope: eliminate duplicate horizon storage across scenarios that differ only by time interval while preserving scenario-driven workflows.

## 1. Problem Statement
Horizon artifacts are large and time-independent for a fixed spatial/algorithm configuration. When scenarios vary by mission time window but share DEM/CRS/analysis extent, per-scenario horizon duplication is wasteful.

Goal:
- Store each unique horizon set once.
- Let many scenarios reference that set.
- Keep existing scenario/product/file semantics usable from API/UI/notebooks.

Non-goals:
- Full object-store migration for all product types.
- Breaking API changes under `/api/v1`.

## 2. Proposed Model

Use a workspace-level shared horizon store keyed by a deterministic `horizon_key`, plus scenario-level link records.

### 2.1 Horizon Key
`horizon_key` is content-addressed from normalized inputs:
- `key_version`
- DEM content hash
- DEM CRS
- DEM geotransform hash
- analysis extent hash
- observer-height policy
- azimuth step
- algorithm id/version
- normalized algorithm parameters

Canonical formula (v1):
- `horizon_key = sha256(canonical_json(normalized_inputs))`

Store lowercase hex (`64` chars).

## 3. DB Schema Changes

## 3.1 Workspace-level DB (global)
Use workspace catalog DB (`scenario_catalog.db`) for shared horizon metadata.

### Table: `horizon_sets`
- `horizon_key TEXT PRIMARY KEY`
- `key_version INTEGER NOT NULL`
- `algorithm_id TEXT NOT NULL`
- `algorithm_version TEXT NOT NULL`
- `dem_content_sha256 TEXT NOT NULL`
- `dem_crs TEXT NOT NULL`
- `dem_geotransform_hash TEXT NOT NULL`
- `analysis_extent_hash TEXT NOT NULL`
- `observer_policy_json TEXT NOT NULL`
- `params_json TEXT NOT NULL`
- `storage_rel_dir TEXT NOT NULL` (path under workspace shared store root)
- `status TEXT NOT NULL` (`building|ready|failed`)
- `file_count INTEGER NOT NULL DEFAULT 0`
- `total_bytes INTEGER NOT NULL DEFAULT 0`
- `created_at_utc TEXT NOT NULL`
- `updated_at_utc TEXT NOT NULL`
- `last_accessed_at_utc TEXT`

Indexes:
- `idx_horizon_sets_dem_hash` on `dem_content_sha256`
- `idx_horizon_sets_status` on `status`

### Table: `horizon_set_refs`
- `scenario_id TEXT NOT NULL`
- `horizon_key TEXT NOT NULL`
- `product_id TEXT NOT NULL`
- `access_mode TEXT NOT NULL` (`reference|materialized`)
- `materialized_relative_path TEXT` (nullable; scenario-local mirror if materialized)
- `pinned INTEGER NOT NULL DEFAULT 0`
- `created_at_utc TEXT NOT NULL`
- `updated_at_utc TEXT NOT NULL`
- PRIMARY KEY (`scenario_id`, `product_id`)

Indexes:
- `idx_horizon_set_refs_horizon_key` on `horizon_key`
- `idx_horizon_set_refs_scenario_id` on `scenario_id`

Notes:
- `horizon_set_refs` gives explicit reference tracking for GC and audit.
- `product_id` ties shared horizon usage to existing product model.

## 3.2 Scenario DB (per-scenario)
No required breaking table changes for MVP.

MVP behavior:
- Keep scenario `products`/`product_files` records as normal.
- Add lineage fields indicating shared backing:
  - `lineage.horizon_key`
  - `lineage.storage_scope = "shared_workspace"`
  - `lineage.access_mode = "reference|materialized"`

Optional Phase 2 (if needed):
- Add `external_file_backing(file_id, store_kind, store_key, relative_path, updated_at_utc)` for generic non-local file backing.

## 4. Filesystem Layout

Workspace-shared store root:
- `{workspace_root}/_shared/horizons/{horizon_key}/`

Contents:
- native horizon files as generated (`horizon_*.bin` / `.cbin`)
- manifest:
  - `manifest.json` (counts, byte size, key inputs, algorithm metadata)

Rules:
- Shared directory is immutable once `status=ready`.
- Rebuild creates a temp dir then atomic move.

## 5. API Changes (`/api/v1`, additive only)

### 5.1 Resolve/Attach
`POST /api/v1/scenarios/{scenario_id}/horizon-sets:resolve`

Request:
- `dem_file_id`
- horizon parameters required for key
- `attach_product` (bool, default true)
- `materialize` (bool, default false)

Response:
- `horizon_key`
- `status` (`ready|building|queued`)
- `product_id` (if attached)
- `reference_count`
- `shared_storage_path` (diagnostic/admin only)

Behavior:
- If `horizon_key` exists and `ready`, attach reference immediately.
- If missing, enqueue generation job and return `building/queued`.

### 5.2 Inspect
`GET /api/v1/horizon-sets/{horizon_key}`
- metadata, status, counts, algorithm version, reference stats.

### 5.3 Materialize (optional endpoint; can also be resolve flag)
`POST /api/v1/scenarios/{scenario_id}/horizon-sets/{horizon_key}:materialize`
- creates scenario-local copy/link and updates `access_mode`.

### 5.4 Detach
`DELETE /api/v1/scenarios/{scenario_id}/horizon-sets/{product_id}`
- removes scenario reference
- does not delete shared set directly.

### 5.5 GC Preview + GC Apply (admin)
`GET /api/v1/admin/horizon-sets/gc-preview`
`POST /api/v1/admin/horizon-sets/gc-apply`
- identifies and prunes unpinned, unreferenced sets with retention window.

## 6. Service/Resolver Changes

Add services:
- `HorizonKeyService` (normalize + hash key inputs)
- `SharedHorizonStoreService` (lookup, create, status, manifest, refs, GC)
- `HorizonReferenceService` (scenario attach/detach/materialize)

Update path resolution:
- Use unified accessor (`IdPathAccessor`) for ID/path lookup.
- For shared horizon products, resolve to shared workspace path via `horizon_key`.

## 6.1 Native Compute Boundary (Polling Model)

Decision:
- Use polling for native progress/cancellation status instead of Python callbacks into C#.
- Rationale: lower interop risk and avoids Python callback/GIL complexity in first implementation.

### C# bridge surface (proposed)
- `StartGenerateHorizons(demPath, surroundingDemPaths, horizonsDir, overwriteHorizons, compressHorizons) -> job_id`
- `GetHorizonJobStatus(job_id) -> {state, percent, message, file_count, total_bytes, elapsed_seconds}`
- `CancelHorizonJob(job_id) -> bool`
- `GetHorizonJobResult(job_id) -> {state, horizons_dir, file_count, total_bytes, elapsed_seconds, warnings}`

`state` values:
- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`

### Python raw wrapper contract (proposed)
Raw method remains scenario-agnostic:

`generate_horizons_raw(dem_path: str, surrounding_dem_paths: list[str], horizons_dir: str, overwrite_horizons: bool = False, compress_horizons: bool = True) -> HorizonGenerationResult`

Proposed result payload:
- `status` (`completed|reused_existing|cancelled`)
- `horizons_dir`
- `file_count`
- `total_bytes`
- `compress_horizons`
- `overwrite_horizons`
- `elapsed_seconds`
- `dem_path`
- `surrounding_dem_paths`
- `warnings`

### Poll loop behavior
- Start native job once; receive `job_id`.
- Poll `GetHorizonJobStatus` every `0.5-1.0s` (adaptive allowed).
- Emit backend `job_progress` events only when status/progress materially changes.
- Check cancellation between polls; call `CancelHorizonJob` when requested.
- On terminal state:
  - `completed`: call `GetHorizonJobResult`, normalize result, continue shared-store attach flow.
  - `failed/cancelled`: emit terminal event and propagate failure/cancel status.
- Apply timeout and stale-status guards for stuck jobs.

## 6.2 File-Based Scenario Bootstrap Path (`scenario.toml`)

Goal:
- Allow scenario creation without the web UI by dropping a directory under `workspace_root` with a `scenario.toml`.
- Ingest that directory into canonical scenario state (`scenario.db`, registered products/files), then use normal APIs/jobs.

### Discovery/Ingest Model
- Start with explicit discovery endpoint/command (scan once), then optionally add filesystem watch.
- Discovery target: immediate children of `workspace_root` containing `scenario.toml`.
- Scenario ID/name source: directory name (slug rules from existing scenario conventions).
- Ingest must be idempotent: unchanged `scenario.toml` should no-op.

### Minimal `scenario.toml` Contract (v1)
```toml
schema_version = 1

[dem]
primary_path = "D:/data/dem/main.tif"
surrounding_paths = ["D:/data/dem/a.tif", "D:/data/dem/b.tif"]

[time_interval]
start_utc = "2026-03-01T00:00:00Z"
stop_utc = "2026-03-10T00:00:00Z"
time_step_hours = 1
```

### `scenario.toml` Schema (v1, concrete)

Required top-level keys:
- `schema_version` (integer, must equal `1`)
- `[dem]` table
- `[time_interval]` table

`[dem]`:
- `primary_path` (string, non-empty, absolute or scenario-relative path)
- `surrounding_paths` (array of strings, optional, default `[]`)

`[time_interval]`:
- `start_utc` (string, ISO 8601 UTC timestamp; trailing `Z` optional)
- `stop_utc` (string, ISO 8601 UTC timestamp; trailing `Z` optional)
- `time_step_hours` (number, `> 0`)
- validation: `start_utc < stop_utc`; if timezone suffix is present, it must be `Z`

Optional `[metadata]`:
- `owner` (string, optional)
- `notes` (string, optional)
- `tags` (array of strings, optional)

Unknown fields:
- reject unknown top-level tables/keys for v1 (strict parse).

Path normalization:
- scenario-relative paths resolve against the directory containing `scenario.toml`.
- all resolved paths stored as normalized absolute paths in ingest metadata.

### DEM Placement/Canonicalization Rules (agreed)
- If `dem.primary_path` is outside scenario directory:
  - copy into scenario directory at canonical DEM path.
- If `dem.primary_path` is inside scenario directory but not canonical name:
  - rename/move to canonical DEM path.
- If already canonical:
  - no file move/copy.

In all cases:
- register canonical file in `scenario.db` as scenario DEM product/file.
- preserve original `dem.primary_path` as provenance/lineage metadata.

### Surrounding DEM Handling (v1)
- Keep surrounding DEM references as validated input paths for compute.
- Persist normalized absolute paths (and optional checksums when available) in scenario metadata.
- Do not copy surrounding DEMs in v1 unless explicitly requested later.

### Time Interval Handling (v1)
- Persist `start_utc`/`stop_utc` and `time_step_hours` in scenario metadata.
- Validate format and ordering (`start_utc < stop_utc`, `time_step_hours > 0`).
- Horizon sharing remains time-independent (time interval does not contribute to `horizon_key`).

### Additive API Endpoints for Discovery/Ingest (proposed)

`POST /api/v1/scenarios:discover`
- purpose: scan `workspace_root` children for `scenario.toml` and ingest.
- request:
  - `dry_run` (bool, default `false`)
  - `include_existing` (bool, default `false`)
  - `scenario_roots` (array of strings, optional; limits scan set)
- response:
  - `workspace_root`
  - `discovered_count`
  - `ingested_count`
  - `updated_count`
  - `skipped_count`
  - `error_count`
  - `results[]` entries:
    - `scenario_root`
    - `scenario_id` (nullable for errors)
    - `status` (`ingested|updated|skipped|error`)
    - `reason` (nullable)
    - `warnings[]`

`GET /api/v1/scenarios/discovery-status`
- purpose: return last discovery run summary for operator visibility.
- response:
  - `last_run_utc` (nullable)
  - `workspace_root`
  - summary counters (same names as discover response)
  - `results[]` (same structure, bounded recent set)

`POST /api/v1/scenarios/{scenario_id}:reingest`
- purpose: re-run ingest from that scenario directory's `scenario.toml`.
- request:
  - `dry_run` (bool, default `false`)
- response:
  - `scenario_id`
  - `status` (`updated|skipped|error`)
  - `reason` (nullable)
  - `warnings[]`

Notes:
- Discovery/ingest is additive and non-breaking under `/api/v1`.
- Initial implementation can run synchronously; convert to job-backed flow later if scan size grows.
- Operator runbook: `docs/SCENARIO_OPERATOR_WORKFLOW.md`.

## 7. Migration Plan (Tasks)

### Task Group A: Schema + Metadata
- [x] A1. Add workspace DB migration for `horizon_sets` and `horizon_set_refs`.
- [x] A2. Add dataclasses/models for horizon-set metadata.
- [x] A3. Add migration tests (idempotent + downgrade safety notes).

Acceptance:
- tables created idempotently;
- existing scenarios unaffected.

### Task Group B: Keying + Store
- [x] B1. Implement `HorizonKeyService` canonicalization.
- [x] B2. Implement shared store layout + manifest writer.
- [x] B3. Implement atomic finalize (`building -> ready`) and failure state.

Acceptance:
- same normalized inputs produce same key;
- interrupted build cannot produce partial `ready`.

### Task Group C: API + Job Flow
- [x] C1. Add resolve/inspect/attach/detach endpoints.
- [ ] C2. Wire resolve endpoint to existing job system for cache miss builds.
- [ ] C3. Ensure progress/cancellation events include `horizon_key`.
- [ ] C4. Implement native polling bridge (`Start/GetStatus/Cancel/GetResult`) and Python poll loop wrapper for horizon generation.

Acceptance:
- cache hit returns attach without recompute;
- cache miss creates one build, concurrent callers dedupe on `horizon_key`.
- long-running horizon jobs report progress and support cancellation through polling without Python callbacks.

### Task Group D: Scenario Integration
- [x] D1. Register shared horizon products in scenario `products`.
- [x] D2. Persist lineage fields for shared backing.
- [ ] D3. Add optional materialization path.

Acceptance:
- scenario APIs list horizon products normally;
- lineage clearly identifies shared vs local storage.

### Task Group E: GC + Operations
- [ ] E1. Reference counting and pin semantics.
- [ ] E2. GC preview/apply endpoints + retention window.
- [ ] E3. Structured audit logs for attach/detach/gc.

Acceptance:
- no referenced/pinned set is deleted;
- GC operations are auditable.

### Task Group F: File-Based Scenario Creation (`scenario.toml`)
- [x] F1. Define and freeze `scenario.toml` schema (v1) and validation errors (`schema_version`, strict keys, RFC3339 UTC checks, path normalization rules).
- [x] F2. Implement discovery scan (`workspace_root` child dirs) for new `scenario.toml`.
- [x] F3. Implement idempotent ingest with ingest marker/hash.
- [x] F4. Implement DEM canonicalization rules (copy/rename/no-op) and provenance capture.
- [x] F5. Register ingested DEM + scenario metadata (`surrounding_paths`, `time_interval`) in `scenario.db`.
- [x] F6. Add additive API endpoints for manual discovery trigger and ingest status reporting: `POST /api/v1/scenarios:discover`, `GET /api/v1/scenarios/discovery-status`, `POST /api/v1/scenarios/{scenario_id}:reingest`.
- [x] F7. Add tests: malformed toml, missing files, copy path, rename path, no-op path, re-ingest idempotency.
- [x] F8. Document operator workflow (create directory + `scenario.toml` + trigger discover). Artifact: `docs/SCENARIO_OPERATOR_WORKFLOW.md`.

Acceptance:
- A user can create a scenario without UI by adding directory + `scenario.toml`.
- Ingest produces canonical scenario artifacts and DB records.
- Re-running discovery does not duplicate scenario/products/files.

## 8. Contract/Test Additions

- [x] Contract tests for new horizon-set endpoints.
- [x] Integration tests: two scenarios share one `horizon_key` and one physical set.
- [x] Integration test: scenario detach preserves shared set if other refs exist.
- [ ] Integration test: GC deletes only unreferenced/unpinned sets.
- [ ] Regression test: existing `/api/v1` scenario/product/file flows remain valid.

## 9. Backward Compatibility

- Existing scenarios with local horizons remain supported.
- New shared-store references are additive.
- Materialization can be used for export/portability workflows.

## 10. Risks and Rollback

Risks:
- Incorrect key normalization causes false sharing or missed reuse.
- Concurrent build races around same key.
- Operational complexity around GC/pinning.

Rollback:
- Feature flag shared store resolution off.
- Continue writing scenario-local horizons only.
- Keep shared tables; mark unused until re-enabled.

## 11. Initial Implementation Recommendation

Start with:
1. Read-through cache behavior (`resolve` + shared attach, no materialize, no GC apply).
2. Manual/admin-only GC preview.
3. Add materialization after stability.

This minimizes early risk while immediately eliminating most duplicate horizon storage.
