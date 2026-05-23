# Phase 4.6 Plan: Path-First Product Identity Rollout

Date: 2026-02-17  
Owner: Architecture + Backend + Web Client

## Current Status (2026-02-17)

- Implementation status: Stage 0-5 feature work is landed.
- Verification status: stage-gate is not yet closed.
- Current blocker: Windows file-handle/permission behavior during rename/move flows is causing integration failures in `backend/tests/contract/test_phase4_6_path_first_identity.py`.

## Goal

Shift Scenario Explorer and product organization to a path-first model so users interact with filesystem-like names/folders while preserving immutable internal IDs for backend integrity.

## Scope

In scope:
- Explorer rendering model (file/folder-first).
- Scenario reconciliation logic and canonical artifact registration behavior.
- Additive API/contract expansion to support path-first views.
- Rename/move semantics for products and product collections.

Out of scope:
- Breaking `/api/v1` contract changes.
- Replacing all internal IDs with paths.
- Packaging/deployment changes.

## Explorer Node Contract (Implemented)

Path-first Explorer payload is served by:
- `GET /api/v1/scenarios/{scenario_id}/explorer-nodes`
- Query option: `include_hidden` (default `false`)

Node shape (additive `/api/v1`):

```json
{
  "node_type": "scenario|folder|file|collection",
  "name": "hillshade.tif",
  "relative_path": "lighting/hillshade.tif",
  "parent_relative_path": "lighting",
  "is_renderable": true,
  "is_hidden_default": false,
  "product_id": "prd_xxx",
  "file_id": "fil_xxx",
  "kind": "lighting",
  "subkind": "hillshade",
  "created_at_utc": "2026-02-17T01-02-03",
  "size_bytes": 12345,
  "child_count": null
}
```

## Stage 0: Contract + Data Model Design

- [x] Write Explorer node contract draft in docs (file/folder tree payload shape).
- [x] Define canonical key rules in docs.
- [x] Single-file key is scenario-relative file path (example: `dem.tif`).
- [x] Multi-file key is scenario-relative directory path (example: `lightmaps/series_a/`).
- [x] Define default visibility policy in docs.
- [x] Visible: renderable map assets.
- [x] Hidden by default: `scenario.db`, `scenario.toml`, `display/`, `.cog.` derivatives.
- [x] Define rename/move transaction contract in docs (success/failure/rollback behavior).
- [x] Add `/api/v1` additive-only compatibility note and link ADR 0004.
- [x] Stage 0 acceptance: ADR and contract notes ratified and linked from plan.

## Stage 1: Backend Reconciliation Hardening

- [x] Extend reconcile logic to upsert canonical single-file products (`dem.tif`, `hillshade.tif`).
- [x] Add initial directory-level reconcile support for multi-file collections.
- [x] Rebuild minimal `scenario.db` when missing (scenario + canonical product/file records).
- [x] Add deterministic path normalization for Windows (`\` to `/`, case-safe compare).
- [x] Add collision detection for canonical keys and relative paths.
- [x] Add integration tests for add/remove/rename detection.
- [x] Add integration tests for missing `scenario.db` reconstruction.
- [x] Add tests that display-only artifacts are excluded from default Explorer view.
- [ ] Run contract test subset touching scenarios/products/files. (Now blocked by Windows `PermissionError` during file move in Phase 4.6 contract tests; see verification record below.)
- [ ] Stage 1 acceptance: reconcile tests pass and existing product/layer APIs show no regressions.

## Stage 2: Additive API Expansion for Path-First Explorer

- [x] Add path-first Explorer API payload (new endpoint or additive fields only).
- [x] Include `node_type` (`scenario|folder|file|collection`).
- [x] Include `name`.
- [x] Include `relative_path`.
- [x] Include `is_renderable`.
- [x] Include `is_hidden_default`.
- [x] Include optional internal links (`product_id`, `file_id`) for map actions.
- [x] Keep existing `/api/v1` endpoints unchanged for backward compatibility.
- [x] Export OpenAPI + schema artifacts for additive changes.
- [x] Add contract tests for payload schema stability.
- [x] Add changelog entry with compatibility classification.
- [ ] Stage 2 acceptance: additive `/api/v1` contract updates pass tests and legacy clients still run.

## Stage 3: Explorer UI Migration

- [x] Switch Explorer tree-grid data source to path-first node payload.
- [x] Render filesystem names in Name column (file/folder/collection labels).
- [x] Keep metadata columns contextual (`Type`, `Created`, `Size`, `Notes`).
- [x] Keep live gap-aware filter across name/path/type metadata.
- [x] Keep scenario pulldown + scoped filtering behavior.
- [x] Keep drag/drop add-to-map using internal IDs behind the scenes.
- [x] Add UI toggle to include hidden/system/display artifacts (default off).
- [ ] Add manual checks for default-visible expectations (`dem.tif`, `hillshade.tif` shown).
- [ ] Add manual checks for hidden defaults (`scenario.db`, `scenario.toml`, `display/*`, `.cog.*` hidden).
- [ ] Stage 3 acceptance: Explorer behavior matches filesystem mental model without regressions in scoping/drag-drop.

## Stage 4: Rename/Move Operations

- [x] Design additive `/api/v1` rename/move endpoint(s) and payload schema.
- [x] Implement single-file rename operation.
- [x] Implement collection-directory rename operation.
- [x] Update filesystem paths and DB path metadata in one coordinated operation.
- [x] Add rollback path for partial failures.
- [x] Emit WS layer/product update events after rename/move success.
- [x] Add integration tests for success cases.
- [x] Add integration tests for rollback on simulated failures.
- [x] Verify layer references remain valid (immutable internal IDs unchanged).
- [ ] Stage 4 acceptance: rename/move is coherent, recoverable, and observable. (Rollback path test passes; success-path move test currently failing with `500` from `POST /paths:move` under Windows permission conditions.)

## Stage 5: Multi-File Product Completion

- [x] Define finalized directory conventions for horizons and lightmap series.
- [x] Implement collection node aggregation fields (count, size, time range where applicable).
- [x] Add collection-aware filtering behavior in Explorer.
- [x] Add collection add-to-map affordances where supported.
- [x] Add regression tests for collection discovery.
- [x] Add regression tests for collection rename/move.
- [x] Add regression tests for Explorer collection rendering.
- [ ] Stage 5 acceptance: multi-file products are first-class folder-like objects in Explorer.

## Required Tests

- [x] Contract: additive schema validation for Explorer payloads. (Covered by `backend/tests/contract/test_phase4_6_path_first_identity.py::test_explorer_nodes_default_view_hides_system_and_display_artifacts` and `...::test_explorer_nodes_include_hidden_shows_system_and_display_artifacts`.)
- [ ] Contract: WS payload compatibility for rename/move and reconcile updates. (Partially covered; `layer_updated` assertion exists but success path currently failing before assertion.)
- [ ] Integration: filesystem reconcile add/remove/rename/rebuild DB. (Rename case currently failing with `PermissionError` on file replace.)
- [ ] Integration: rename transaction success/failure rollback. (Rollback test passes; success-path move test failing.)
- [ ] Manual/UI: default hidden-artifact policy.
- [ ] Manual/UI: live filter on file/folder names.
- [ ] Manual/UI: drag/drop stability and persisted layer state.
- [ ] Manual/UI: scenario scoping behavior unchanged.

## Verification Record (2026-02-17)

- Automated command used:
  - `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/contract/test_phase4_6_path_first_identity.py -q --basetemp D:\projects\lunar_analyst\.tmp\pytest_run_20260217_1"`
- Result:
  - `5 collected, 3 passed, 2 failed`
  - Failed:
    - `test_reconcile_detects_filesystem_rename_as_remove_and_add` (`PermissionError` on `Path.replace` for `hillshade.tif`)
    - `test_move_path_endpoint_moves_file_updates_records_and_emits_layer_event` (`POST /api/v1/scenarios/{scenario_id}/paths:move` returned `500`)
- Manual checklist source:
  - `docs/HOW_TO_MANUALLY_TEST.md` (Phase 4.6 section; includes hidden policy, filter behavior, drag/drop, and move/rename smoke).

## Closure Checklist

- [ ] Fix Windows rename/move file-handle issue affecting `hillshade.tif` move operations.
- [ ] Re-run `backend/tests/contract/test_phase4_6_path_first_identity.py` and require all tests passing.
- [ ] Execute and record Phase 4.6 manual checks from `docs/HOW_TO_MANUALLY_TEST.md`.
- [ ] Mark Phase 4.6 stage-gate complete in `docs/PLAN.md` after automated and manual checks pass.

## Risks and Controls

- [x] Risk tracked: path ambiguity on Windows case-insensitive filesystem.
- [x] Control implemented: canonical path normalization + uniqueness checks + collision tests.
- [x] Risk tracked: partial rename failure.
- [x] Control implemented: staged operation with rollback + structured error envelope.
- [x] Risk tracked: contract drift.
- [x] Control implemented: additive-only `/api/v1` changes + changelog + contract tests.

## Rollback Approach

- [x] Keep legacy product-centric Explorer code path behind a feature flag during migration.
- [x] Define explicit fallback procedure to restore legacy Explorer endpoint/model.
- [x] Preserve backend reconciliation improvements when UI fallback is activated.
- [x] Block destructive data migrations until path-first endpoints and tests are stable.

## Execution Order

- [x] Stage 0 complete (design/contracts)
- [x] Stage 1 complete (reconciliation hardening)
- [x] Stage 2 complete (additive API expansion)
- [x] Stage 3 complete (Explorer UI migration)
- [x] Stage 4 complete (rename/move)
- [x] Stage 5 complete (multi-file completion)
