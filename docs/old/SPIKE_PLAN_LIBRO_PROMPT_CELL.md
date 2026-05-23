# Spike Plan: Libro Prompt Cell Pilot (1 Week)

## 1. Goal
Validate whether Libro can deliver the target workflow out of the box: no visible LLM side panel and first-class prompt cells that generate executable notebook cells.

## 2. Out of Scope
- Production packaging/deployment changes (including Tauri bundling changes).
- Replacing existing marimo paths globally.
- Any `pythonnet` bridge boundary changes.

## 3. Allowed Files To Change
- `backend/notebook/libro_runner.py`
- `backend/api/routes/notebook_runtime.py`
- `backend/services/notebook_gateway.py`
- `backend/tests/integration/test_libro_runtime.py`
- `frontend/src/features/notebook/NotebookLaunchButton.tsx`
- `docs/spikes/libro_pilot.md`

## 4. Constraints and Invariants
- Notebook runtime remains a separate process from FastAPI.
- Notebook runtime does not directly mutate `scenario.db`; writes go through FastAPI APIs.
- Testing on Windows 11
- Preserve scenario-root path safety and file-id/path mapping contracts.

## 5. 5-Day Work Breakdown
1. Day 1: Add feature-flagged Libro runtime launcher/stopper with health checks and per-scenario session wiring.
2. Day 2: Add FastAPI gateway/proxy routes for session boot, status, and URL handoff.
3. Day 3: Add minimal frontend launch path in notebook area.
4. Day 4: Run prompt-cell workflow validation (prompt -> generated code cells -> executed output).
5. Day 5: Add observability and failure-path logging; write fit/gap report.

## 6. Acceptance Criteria
- FastAPI can start, monitor, and stop Libro runtime per scenario.
- UI can open Libro session from the app workflow.
- Prompt cell can generate one or more code cells.
- Generated cells execute and produce output in session.
- No direct DB mutation from notebook process in tested path.
- Logs include startup, auth/session, prompt execution, and failure reasons.

## 7. Required Tests
- Integration test for runtime lifecycle (`start -> healthy -> stop`).
- Integration test for unauthorized session access rejection.
- Manual verification evidence for prompt-cell generation and execution.
- Manual verification evidence that notebook actions use FastAPI for project writes.

## 8. Risks
- Libro integration maturity and API/documentation drift.
- Embedding/session-management friction with existing FastAPI/Tauri flow.
- LLM provider behavior variance across environments.

## 9. Rollback
- Keep all Libro entry points behind a feature flag.
- Disable flag to revert to current notebook path immediately.
- No DB migrations or contract-breaking backend changes in this spike.

