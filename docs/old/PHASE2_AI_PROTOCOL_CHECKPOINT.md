# Phase 2 AI Protocol Checkpoint

## Scope
- Completed backend-core Phase 2 items for scenario/product/file/layer/job scaffolding, schema migrations, file-ID serving, WebSocket event stream, and import-to-COG policy.

## Prompt Contract Evidence
- Goal was constrained to Phase 2 completion only.
- Changes remained in backend API/services/contracts/tests/docs/CI wiring.
- Out-of-scope items (Phase 3+ UI/E2E feature work) were not implemented.

## Compatibility Notes
- Lifespan migration replaced deprecated FastAPI startup hook behavior without changing route contracts.
- Stage 1 envelope and strict-schema behavior remain enforced by contract tests.
- New endpoints are additive under `/api/v1`:
  - `POST /api/v1/scenarios/{scenario_id}/imports/geotiff`
  - `GET /api/v1/files/{file_id}`
  - `POST /api/v1/layers`
  - `PATCH /api/v1/layers/{layer_id}`
  - `DELETE /api/v1/layers/{layer_id}`
  - `GET /api/v1/scenarios/{scenario_id}/layers`
  - `WS /api/v1/events`

## Human-Approval Trail (High-Risk / Policy-Relevant)
- DB migration scope was explicitly requested by user while asking to complete Phase 2.
- Migration implementation is additive (`CREATE TABLE IF NOT EXISTS`) and versioned via `schema_migrations`.
- No destructive migration or data-dropping SQL was introduced.

## Verification
- `python -m pytest backend/tests/contract -q` -> passed.
- `python -m pytest backend/tests/integration backend/tests/worker -q` -> passed.
- Local gate wrapper added: `local_check_backend_contracts.ps1`.
