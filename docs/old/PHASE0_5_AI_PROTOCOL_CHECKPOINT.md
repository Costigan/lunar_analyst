# Phase 0.5 AI Protocol Checkpoint

Date: 2026-02-14

## Prompt/Scope Evidence

- Legacy source references were explicitly included in Phase 0.5 artifacts:
  - `D:\projects\new_horizon`
  - `D:\projects\moonlayers`
  - `D:\projects\lunarsiteeval`
  - `D:\projects\lunar_analyst_net`
- User-directed scope decisions were captured:
  - Runtime native boundary calls `moonlib` only.
  - `MoonMap` is reused as-is.
  - `geotiff_server.py` is reference-only (rewrite allowed).
  - Fixture dataset selection deferred until user provides test data.

## Acceptance Criteria Traceability

- Inventory complete: `docs/PHASE0_5_LEGACY_INVENTORY.md`
- Component classification complete: `docs/PHASE0_5_COMPONENT_CLASSIFICATION.md`
- Data/model adapter requirements complete: `docs/PHASE0_5_DATA_MODEL_ADAPTERS.md`
- Legacy capability acceptance matrix complete: `docs/PHASE0_5_ACCEPTANCE_MATRIX.md`

## Regression-Test Evidence (Phase 0.5 Relevant)

- Added fixture discovery + baseline metadata validation scaffolding:
  - `backend/contracts/fixtures.py`
  - `backend/tools/fixture_discovery.py`
  - `backend/tests/contract/test_fixture_discovery.py`
- Local test run in `D:\projects\env_311`:
  - Command: `python -m pytest backend/tests/contract -q`
  - Result: `9 passed`

## Deferred Item Record

- Curating concrete DEM/lighting/vector legacy fixtures is deferred by explicit user instruction; this does not block framework-first contract/job-handler work.
