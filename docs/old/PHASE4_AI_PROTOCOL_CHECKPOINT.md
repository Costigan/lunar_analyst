# Phase 4 AI Protocol Checkpoint

## Scope
- Implement Marimo launch/attach lifecycle as a separate process integration.
- Add notebook session/auth flow for notebook-driven API use.
- Add notebook REST/WS helper client.
- Verify notebook event subscription and reconnect behavior.

## Invariants Checked
- FastAPI remains authoritative for scenario/layer/job state.
- Notebook workflow uses public API contracts only.
- No notebook shared-memory mutation path added.
- Worker/native compute boundary unchanged (JobHandlers-centered compute retained).

## Files Updated
- `backend/api/dependencies.py`
- `backend/api/app.py`
- `backend/api/routers/v1.py`
- `backend/contracts/models.py`
- `backend/notebook/__init__.py`
- `backend/notebook/client.py`
- `backend/tests/contract/test_phase4_marimo_integration.py`
- `docs/PHASE4_NOTEBOOK_WORKFLOW.md`
- `docs/PLAN.md`

## Verification Targets
- Notebook session/token gate for mutation endpoints.
- Notebook WS event stream requires token and supports reconnect.
- Marimo launch/attach/stop lifecycle routes behave as expected.
- Notebook loop (`generate/register/render`) validated through API integration test flow.
