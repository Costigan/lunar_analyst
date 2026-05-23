# Phase 3 AI Protocol Checkpoint

Date: 2026-02-16  
Tester: `mshirley`

## Scope

Phase 3 "Minimal Web Client":

- API-backed layer hydration and management for map overlays.
- OpenLayers rendering of registered raster/vector layers.
- Per-layer styling controls (opacity/brightness/contrast/colormap) persisted through `layer_state`.
- Job launch/progress/cancel UI wired to generated job routes and WS events.

## Acceptance Criteria Evidence

Implemented:

- Map bootstrap + scenario/layer hydration: `POST /api/v1/lunar-analyst/bootstrap`, `GET /api/v1/scenarios/{scenario_id}/layers`.
- Layer CRUD wiring: `POST/PATCH/DELETE /api/v1/layers`.
- Product file discovery for layer creation: `GET /api/v1/products/{product_id}/files`.
- Job route discovery for UI launcher: `GET /api/v1/jobs/handlers`.
- WS event consumption for job/layer updates: `WS /api/v1/events`.

Primary files:

- `backend/web/lunar_analyst/src/App.tsx`
- `backend/web/lunar_analyst/index.html`
- `backend/api/routers/v1.py`
- `backend/api/routers/lunar_analyst.py`
- `backend/contracts/models.py`

## Automated Test Evidence

Executed:

- `python -m pytest backend/tests/contract/test_phase3_minimal_web_client.py -q`
- `python -m pytest backend/tests/contract -q`

Results:

- Phase 3 smoke/contract tests: passed (`2 passed`)
- Full contract suite: passed (`26 passed`)

## Manual Verification Checklist Link

Checklist updated in:

- `docs/HOW_TO_MANUALLY_TEST.md` (Phase 3 section)


