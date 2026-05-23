# Marimo Integration Phase 2 Plan

Last updated: 2026-02-21
Owner: Lunar Analyst backend/web team
Status: Implemented (2026-02-21)

## 1. Goal

Implement a complete user workflow that connects the Scenario Workspace map UI and Marimo notebooks:

1. Open `/lunar_analyst/`, pick a scenario.
2. Launch/open Marimo from the selected scenario context.
3. Create/run notebooks in that scenario directory.
4. Generate outputs (for example GeoTIFF).
5. Add output to map layers from notebook code.
6. Trigger map zoom/focus to the new output.
7. Return to map UI and inspect result.

This document is intentionally self-contained so a new session can execute without rediscovery.

## 2. Architecture Constraints (Must Hold)

- FastAPI remains authoritative for scenario/job/layer state.
- Notebooks must mutate state through FastAPI APIs only (no direct DB writes).
- Scenario path safety rules remain enforced (normalized path, in-root validation).
- JobHandlers-centered compute rule remains intact (`backend/jobs/handlers.py` signatures define routed contracts).
- Additive API evolution only in `/api/v1` unless explicitly approved otherwise.
- Windows 11 / Python 3.11 / .NET 9 baseline remains unchanged.

## 3. Current State Snapshot

### 3.1 Implemented Today

- Notebook workflow contract exists in `docs/PHASE4_NOTEBOOK_WORKFLOW.md`.
- Marimo process control APIs exist:
  - `POST /api/v1/marimo/launch`
  - `GET /api/v1/marimo/status`
  - `POST /api/v1/marimo/stop`
  - Router: `backend/api/routers/v1.py`
- Marimo launch supports `cwd`, but defaults to repo root if omitted:
  - `cwd = request.cwd or str(_repo_root())` in `backend/api/dependencies.py`.
- Scenario Explorer already has selected scenario state and scenario directory metadata available in frontend:
  - `ScenarioSummary.directory` in `backend/web/lunar_analyst/src/services/scenarioService.ts`
  - Used by `backend/web/lunar_analyst/src/components/explorer/ScenarioExplorerPane.tsx`
- Notebook-to-map layer add is already possible via REST (`/api/v1/layers`) and WS layer events.

### 3.2 Gaps Against Requested Story

- No UI action in `/lunar_analyst/` to launch/open Marimo from selected scenario.
- No guaranteed behavior when Marimo is already running with a different `cwd`.
- Interactive Marimo launch does not currently inject repo import paths the same way headless notebook job runner does.
- No explicit notebook-triggerable map viewport command ("zoom to new layer") path.
- Manual test checklist does not currently validate end-to-end map <-> marimo roundtrip.

## 4. Scope

### 4.1 In Scope (Phase 2)

- Scenario-scoped Marimo launch/open action from web UI.
- Backend launch semantics for selected scenario directory with safe restart behavior.
- Interactive Marimo Python import consistency for `backend.*` and `moonlayers`.
- Notebook helper pattern for "register output -> create layer -> request map zoom".
- Map client support for backend-issued zoom requests.
- Contract/integration/manual tests for the full flow.
- Documentation updates for operator and developer workflow.

### 4.2 Out of Scope

- Replacing Marimo UI internals or customizing Marimo's own file browser UX beyond launch context.
- Generalized multi-user notebook collaboration/session arbitration.
- Tauri packaging changes.
- Any .NET/pythonnet bridge logic changes for compute kernels.

## 5. Proposed Design

### 5.1 Scenario-Scoped Marimo Launch Contract

Add additive fields to `MarimoLaunchRequest`:

- `scenario_id: str | None = None`
- `restart_if_running: bool = False`

Behavior:

- If `scenario_id` is supplied, backend resolves scenario directory and uses it as launch `cwd`.
- If Marimo is already running:
  - Same `cwd`: return current running status.
  - Different `cwd`:
    - `restart_if_running = true`: stop and relaunch in target scenario directory.
    - `restart_if_running = false`: return HTTP 409 with details (`current_cwd`, `requested_cwd`).
- If mode is `attach`, and `scenario_id` launch is requested, return clear conflict (cannot re-home attached external server).

Rationale:

- Meets scenario-specific launch intent.
- Avoids silent mismatch.
- Keeps behavior explicit and scriptable.

### 5.2 Launch Environment Consistency

For interactive Marimo subprocess launches, inject `PYTHONPATH` entries:

1. `<repo_root>`
2. `<repo_root>/moonlayers_pkg`
3. existing `PYTHONPATH` (append)

Apply only to FastAPI-managed Marimo launch mode.

Rationale:

- Notebook code in scenario cwd can still import `backend.*` and `moonlayers`.
- Aligns with existing headless job runner import behavior.

### 5.3 Web UI "Open in Marimo" Flow

Add action in Scenario Explorer controls:

- Button label: `Open in Marimo`
- Enabled only when an active scenario exists.

On click:

1. Call `POST /api/v1/marimo/launch` with:
   - `scenario_id = activeScenarioId`
   - `restart_if_running = true`
2. Read returned `base_url`.
3. Open Marimo in new tab/window (`window.open(base_url, "_blank", "noopener,noreferrer")`).

UI notes:

- Show success/failure feedback in existing status/log surface.
- Handle popup-blocker fallback (show clickable URL if window open fails).

### 5.4 Notebook-Triggered Map Zoom Contract

Introduce a lightweight map command endpoint and WS event.

API:

- `POST /api/v1/scenarios/{scenario_id}/map-commands/zoom-to-file`

Request body:

- `file_id: str`
- `padding_px: int | None` (optional default)
- `max_zoom: float | None` (optional)

Backend action:

- Resolve file path by `file_id`.
- For raster: use map-display derivative path (`ESRI:103878`) and compute extent from raster bounds.
- Emit WS event with scenario and extent:
  - Event name: `map_zoom_requested`
  - Data: `{"scenario_id","file_id","extent":[minx,miny,maxx,maxy],"padding_px","max_zoom"}`

Frontend action:

- Subscribe in existing `/api/v1/events` handler.
- If active scenario matches, call `view.fit(extent, options)`.

Rationale:

- Explicit, minimal command channel.
- Keeps map-view mutation under API/WS contract, not direct notebook <-> browser coupling.

### 5.5 Notebook Helper Utility

Add helper in `backend/notebook/client.py` (and optionally `backend/notebook/notebook_helper.py`) for:

- Register/import output.
- Create layer.
- Request map zoom via new endpoint.

This reduces per-notebook boilerplate for the pattern in story steps 8-9.

## 6. Implementation Slices (Small, Testable)

Each slice targets about 1 hour and can be merged independently.

### Slice A: Backend launch contract extension

Files:

- `backend/contracts/models.py`
- `backend/api/dependencies.py`
- `backend/api/routers/v1.py`
- `backend/tests/contract/test_phase4_marimo_integration.py`

Tasks:

- Add `scenario_id` and `restart_if_running` fields.
- Resolve scenario directory safely when `scenario_id` provided.
- Add running-cwd mismatch behavior and conflict response path.
- Keep backward compatibility for existing `command/cwd/attach_url` use.

Acceptance:

- Existing marimo tests still pass.
- New tests cover scenario launch and cwd mismatch/restart behavior.

### Slice B: Marimo launch environment injection

Files:

- `backend/api/dependencies.py`
- `backend/tests/contract/test_phase4_marimo_integration.py`
- `backend/README.md`

Tasks:

- Build env for launch-mode process with repo + moonlayers paths.
- Pass env to `subprocess.Popen`.
- Document behavior.

Acceptance:

- Test verifies injected `PYTHONPATH` entries.

### Slice C: Frontend Marimo action

Files:

- `backend/web/lunar_analyst/src/components/explorer/ScenarioExplorerPane.tsx`
- `backend/web/lunar_analyst/src/services/marimoService.ts` (new)
- `backend/web/lunar_analyst/src/App.tsx` (wire callbacks if needed)
- `backend/web/lunar_analyst/src/__tests__/...` (new/updated)

Tasks:

- Add service wrapper for marimo launch/status endpoints.
- Add "Open in Marimo" control in explorer.
- Launch with `scenario_id` + `restart_if_running=true`.
- Open returned base URL in new tab.
- Add user-visible error path.

Acceptance:

- UI action launches/opens Marimo for selected scenario.
- Works in both Blueprint and non-Blueprint branches.

### Slice D: Map zoom command backend

Files:

- `backend/contracts/models.py`
- `backend/api/routers/v1.py`
- `backend/api/dependencies.py` (event publish utilities if needed)
- `backend/services/raster_delivery.py` (reuse helper if needed)
- `backend/tests/contract/test_phase4_marimo_integration.py` or new `test_phase4_10_map_zoom_command.py`

Tasks:

- Add request model + endpoint for zoom-to-file command.
- Resolve extent in map CRS.
- Emit `map_zoom_requested` event.
- Update OpenAPI/schema artifacts as required.

Acceptance:

- Contract test validates request/response and event payload.
- Out-of-scope file IDs and scenario mismatch are rejected.

### Slice E: Map zoom command frontend

Files:

- `backend/web/lunar_analyst/src/App.tsx`
- `backend/web/lunar_analyst/src/map/mapController.ts`
- `backend/web/lunar_analyst/src/services/wsClient.ts`
- frontend tests

Tasks:

- Listen for `map_zoom_requested` in existing WS stream.
- Add `fitExtent(...)` method in map controller.
- Apply scenario scoping before fit.

Acceptance:

- Event causes map to zoom to requested extent in active scenario.
- No zoom action in inactive scenarios.

### Slice F: Notebook helper + example workflow

Files:

- `backend/notebook/client.py`
- `backend/notebook/notebook_helper.py` (optional convenience wrapper)
- `backend/notebook/examples/import_geotiff_to_map.mo.py`
- tests for helper behavior

Tasks:

- Add helper methods for layer add + map zoom request.
- Update example notebook showing complete roundtrip.

Acceptance:

- Example notebook can produce/import/add/zoom using documented calls.

### Slice G: Documentation and checklist closure

Files:

- `docs/PHASE4_NOTEBOOK_WORKFLOW.md`
- `docs/HOW_TO_MANUALLY_TEST.md`
- `docs/PLAN.md`
- `backend/README.md`

Tasks:

- Document new Marimo launch flow from Scenario Explorer.
- Document new notebook map zoom command contract.
- Add manual 10-step acceptance checklist.
- Add PLAN items/checkpoints for this phase.

Acceptance:

- New user story fully represented in workflow + manual test docs.

## 7. Proposed API Additions (Additive)

### 7.1 Extend `MarimoLaunchRequest`

- Add `scenario_id` (optional).
- Add `restart_if_running` (optional, default false).

### 7.2 Add map zoom command endpoint

- `POST /api/v1/scenarios/{scenario_id}/map-commands/zoom-to-file`

Response:

- `{ "status": "queued", "event": "map_zoom_requested" }`

### 7.3 WS event addition

- `map_zoom_requested`

Payload:

- `scenario_id`
- `file_id`
- `extent`
- `padding_px` (optional)
- `max_zoom` (optional)

Note:

- This is an additive event. Update contract schemas/changelog accordingly.

## 8. Security and Safety

- Validate `scenario_id` ownership and resolve scenario directory from catalog, not raw client path.
- Keep explicit out-of-root checks for any file lookup involved in zoom command.
- Reject zoom requests for file IDs not in requested scenario.
- Avoid leaking absolute filesystem paths in response payloads/events.
- Keep attach mode restrictions explicit to avoid unsafe process assumptions.

## 9. Test Plan

### 9.1 Backend contract/integration tests

- Marimo launch by `scenario_id` starts with expected cwd.
- Running-cwd mismatch returns conflict unless restart enabled.
- Restart path relaunches with target cwd.
- Marimo launch env contains repo and moonlayers paths.
- Zoom command endpoint emits `map_zoom_requested` with valid extent.
- Rejection tests for invalid scenario/file ownership.

### 9.2 Frontend tests

- Explorer button calls launch API with active scenario.
- Popup fallback message path tested (window open failure).
- WS `map_zoom_requested` triggers `fitExtent` only for active scenario.

### 9.3 Manual acceptance run (operator)

1. Open `/lunar_analyst/`.
2. Select scenario A.
3. Click `Open in Marimo`.
4. Confirm notebook browser is rooted at scenario A directory.
5. Create notebook in scenario A directory.
6. In notebook, generate GeoTIFF under scenario root.
7. Run helper cell to import/register/create layer.
8. Run helper cell to request map zoom to that file/layer.
9. Switch to map tab.
10. Verify layer is present and viewport is focused on output.
11. Repeat with scenario B to verify scenario scoping.

## 10. Rollout and Rollback

Rollout order:

1. Backend contract + tests.
2. Frontend Marimo launch control.
3. Map zoom command backend + frontend.
4. Notebook helper/example/docs.

Rollback strategy:

- If zoom command introduces instability, disable frontend handling first (feature flag or event ignore) while keeping launch flow.
- If scenario-scoped relaunch is disruptive, revert to current launch behavior but keep UI button hidden.
- Keep all changes additive to minimize migration risk.

## 11. Risks and Mitigations

- Risk: Restarting Marimo may interrupt active notebook sessions.
  - Mitigation: explicit UI copy before relaunch; optional prompt in future iteration.
- Risk: Popup blockers prevent automatic tab open.
  - Mitigation: show returned Marimo URL with manual-open button.
- Risk: Event contract drift for new map event.
  - Mitigation: update OpenAPI/schema artifacts + contract tests in same PR.
- Risk: Path handling bugs when scenario directories move.
  - Mitigation: always resolve via scenario service + workspace-root validation.

## 12. Open Questions to Resolve Before Coding

1. Should `Open in Marimo` always force restart when running with different cwd, or prompt user?
2. Should zoom command target `file_id` only, or also support `layer_id` directly?
3. Should map zoom command be one-shot ephemeral only, or persisted in DB/event log?
4. Do we need token auth enforcement for marimo launch endpoints in this phase?

## 13. Definition of Done for This Phase

- User can launch/open Marimo from selected scenario in web UI.
- Interactive notebook can import Lunar Analyst helper libs from scenario cwd.
- Notebook can add output to map and trigger map zoom via supported API/helper.
- Map updates and focus behavior are scenario-scoped and reproducible.
- Contract tests, integration tests, and manual checklist all pass.
- Documentation updated in workflow, plan, and manual test guides.
