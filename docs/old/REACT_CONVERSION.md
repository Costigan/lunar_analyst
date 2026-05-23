# React Conversion Plan (Map Milestone UI)

## Goal
Migrate `backend/web/map_milestone` from vanilla JS DOM rendering to React, while preserving:
1. Existing `/api/v1` REST/WS contracts
2. OpenLayers rendering behavior
3. Scenario-scoped state invariants
4. Drag/drop workflows and job workflows

## Progress Checklist
- [x] Phase 0: Scaffolding and dual-run safety
- [x] Phase 1: Service layer extraction
- [x] Phase 2: React shell + OpenLayers bridge
- [x] Phase 3: Scenario Explorer conversion
- [x] Phase 4: Layer Manager conversion
- [x] Phase 5: Jobs Manager conversion
- [x] Phase 6: Cutover and legacy decommission
- [x] Stabilization: map parity regressions (GeoTIFF placement, panel flicker) closed

## Status (2026-02-18)
1. React conversion is complete and is the only active map milestone UI implementation.
2. Legacy vanilla implementation files were removed (`app.js`, `styles.css`, and legacy custom-element UI path).
3. `/map-milestone/` serves React index assets (`dist/index.react.html` when built, otherwise source entrypoint via `index.html`).

## Current Baseline
Current map page is implemented in:
1. `backend/web/map_milestone/index.html`
2. `backend/web/map_milestone/index.react.html`
3. `backend/web/map_milestone/src/main.tsx`
4. `backend/web/map_milestone/src/App.tsx`
5. `backend/web/map_milestone/src/styles/app.css`
6. `backend/web/map_milestone/src/components/*`

## Current Map UX Spec

### Window Layout
1. Top toolbar with left and right control groups.
2. Three-pane workspace:
   1. Left pane: Scenario Explorer (to be renamed; see requested adjustments).
   2. Center pane: map viewport (OpenLayers).
   3. Right pane: Layer Manager and Jobs Manager (Task Manager direction).
3. On narrow widths, side panes currently behave as slide-in drawers.

### Toolbar Behavior
1. Left toolbar contains the button currently labeled `Explorer` plus title/version.
2. Right toolbar contains active scenario/status and button labeled `Layers/Jobs`.
3. Current behavior hides these two side-panel buttons at larger widths.

### Explorer Pane Behavior
1. Scenario selector uses pattern-combobox matching.
2. Explorer filter uses tokenized gap-aware subsequence matching.
3. Tree/table rows preserve parent context under filtering.
4. Explorer rows are draggable for layer/map drop targets.
5. Scenario-scoped view: active scenario selection determines layer/task scope.

### Layer Manager Behavior
1. Layer list reflects scenario-scoped `layer_state`.
2. Each layer card supports:
   1. visibility
   2. opacity
   3. raster controls (brightness/contrast/colormap)
   4. diagnostics (`range`, `nodata`, normalization mode)
   5. remove action
3. Drag/drop supports:
   1. reorder existing layers
   2. insert product drops from explorer at explicit positions
4. Layer card expansion state persists across panel rerenders.

### Jobs/Task Manager Behavior
1. Job/task definition picker uses pattern-combobox.
2. Filtered task list supports quick selection.
3. JSON params editor + launch/cancel actions.
4. Active status and log update from WS events.

### Raster Rendering UX Contract
1. Single-band raster colormap pipeline:
   1. raw-value normalization via `valueMin/valueMax` when available
   2. fallback behavior when range unavailable
2. Nodata handling:
   1. nodata value rendered transparent
   2. under-range warped edge artifacts treated as transparent
3. Colormap changes should update raster without collapsing layer card state.

### Interaction and State Contracts
1. Scenario switch rehydrates scenario-scoped layers/tasks/explorer state.
2. WS layer/job events reconcile UI state without manual refresh.
3. UI remains server-authoritative for persisted layer/task state.

## Requested Baseline Adjustments (Must Be Included In React Migration)
1. Rename left toolbar button label from `Explorer` to `Sessions`.
2. Keep both side-panel buttons always visible:
   1. `Sessions` button (left)
   2. `Layers/Jobs` button (right)
   This applies at all window sizes, not only narrow layouts.
3. Make both side panels user-resizable:
   1. left panel width resizable
   2. right panel width resizable
   3. define min/max widths and preserve usability at narrow widths
4. Treat the above as baseline UX requirements for parity-plus migration, not optional follow-up enhancements.

## Non-Goals (Initial Migration)
1. No backend schema/API contract changes
2. No job handler contract changes
3. No OpenLayers library replacement
4. No visual redesign beyond parity and regression fixes

## Architecture Target
1. React owns UI composition and panel state.
2. OpenLayers remains imperative in a dedicated integration layer.
3. Data/side effects move to typed API and WS services.
4. Filtering components become React components/hooks (or wrappers around existing custom elements as an interim step).

## Constraints and Invariants
1. Preserve scenario isolation: layer/task/explorer state always scoped to active `scenario_id`.
2. Preserve file safety behavior and file-id-based raster/vector loading.
3. Preserve map CRS handling and raster delivery behavior.
4. Preserve cancellation/progress UX semantics for jobs.

## Migration Strategy (Phased)

### Phase 0: Scaffolding and Dual-Run Safety
1. Add React build/runtime in `backend/web/map_milestone/` with Vite.
2. Keep existing page functional behind a migration-time fallback entrypoint during migration.
3. Use temporary dual-entry routing during migration cutover.
4. Establish shared CSS variables and import existing stylesheet to avoid visual regression.

### Phase 1: Service Layer Extraction (No UI Rewrite Yet)
1. Extract API and WS logic from `app.js` into modules:
   1. `services/apiClient.ts`
   2. `services/scenarioService.ts`
   3. `services/layerService.ts`
   4. `services/jobService.ts`
   5. `services/mapMilestoneService.ts`
2. Extract pure map-independent utilities:
   1. filter matching/highlighting
   2. raster style expression builders
   3. layer ordering helpers
3. Add tests for extracted pure functions.

### Phase 2: React Shell + OpenLayers Bridge
1. Create React app entry:
   1. `src/main.tsx`
   2. `src/App.tsx`
2. Build `MapViewport` component that:
   1. Initializes one OL map instance via `useEffect`
   2. Exposes imperative actions via refs/services
   3. Never re-creates map instance on normal React rerenders
3. Port toolbar and pane layout into React components with parity markup/classes.
4. Implement requested baseline adjustments in shell layout:
   1. `Explorer` -> `Sessions` button rename
   2. always-visible side-panel buttons
   3. resizable left/right panels

### Phase 3: Scenario Explorer Conversion
1. Convert explorer controls to React:
   1. scenario pattern select
   2. filter input
   3. hidden/system toggle
   4. column toggles
2. Convert filtered tree-table to React component:
   1. preserve gap-aware subsequence filtering
   2. preserve expansion state and drag source behavior
3. Preserve Explorer -> map/layer drag payload contract.

### Phase 4: Layer Manager Conversion
1. Convert layer panel cards to React:
   1. visibility/opacity
   2. brightness/contrast/colormap
   3. diagnostics block
   4. remove action
2. Preserve drag/drop reorder and insertion zones.
3. Preserve expanded-card state and selection/focus behavior.
4. Keep layer-source replacement logic for raster style min/max/nodata changes.

### Phase 5: Task/Jobs Manager Conversion
1. Convert task selector/filter/list and job params editor to React.
2. Preserve launch/cancel behavior and WS-driven progress updates.
3. Preserve notebook/system grouping semantics.

### Phase 6: Cutover and Legacy Decommission
1. Switch `/map-milestone/` default route to React build.
2. Complete stabilization pass for parity regressions.
3. Remove legacy `app.js` and `styles.css` after acceptance and soak period.

## File-Level Implementation Steps

1. Add frontend package/build files:
1. `backend/web/map_milestone/package.json`
2. `backend/web/map_milestone/vite.config.ts`
3. `backend/web/map_milestone/tsconfig.json`
4. `backend/web/map_milestone/index.react.html` (or replace current index with built bundle injection)

2. Add React source tree:
1. `backend/web/map_milestone/src/main.tsx`
2. `backend/web/map_milestone/src/App.tsx`
3. `backend/web/map_milestone/src/styles/app.css`
4. `backend/web/map_milestone/src/hooks/useActiveScenario.ts`
5. `backend/web/map_milestone/src/hooks/useScenarioCatalog.ts`
6. `backend/web/map_milestone/src/hooks/useScenarioLayers.ts`
7. `backend/web/map_milestone/src/hooks/useJobDefinitions.ts`
8. `backend/web/map_milestone/src/hooks/useWsEvents.ts`

3. Add OpenLayers integration layer:
1. `backend/web/map_milestone/src/map/MapViewport.tsx`
2. `backend/web/map_milestone/src/map/mapController.ts`
3. `backend/web/map_milestone/src/map/rasterStyle.ts`
4. `backend/web/map_milestone/src/map/projection.ts`

4. Add UI component tree:
1. `backend/web/map_milestone/src/components/Toolbar.tsx`
2. `backend/web/map_milestone/src/components/explorer/ScenarioExplorerPane.tsx`
3. `backend/web/map_milestone/src/components/explorer/FilteredTreeTable.tsx`
4. `backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx`
5. `backend/web/map_milestone/src/components/layers/LayerCard.tsx`
6. `backend/web/map_milestone/src/components/jobs/JobsManagerPane.tsx`
7. `backend/web/map_milestone/src/components/common/PatternCombobox.tsx`
8. `backend/web/map_milestone/src/components/common/FilteredList.tsx`

5. Add shared services/utilities:
1. `backend/web/map_milestone/src/services/apiClient.ts`
2. `backend/web/map_milestone/src/services/wsClient.ts`
3. `backend/web/map_milestone/src/services/scenarioService.ts`
4. `backend/web/map_milestone/src/services/layerService.ts`
5. `backend/web/map_milestone/src/services/jobService.ts`
6. `backend/web/map_milestone/src/utils/filterMatch.ts`
7. `backend/web/map_milestone/src/utils/dragPayload.ts`

6. Update backend static serving for built assets:
1. `backend/api/app.py` and `backend/api/routers/map_milestone.py` (serve React build artifacts)

## Detailed Execution Steps
1. Bootstrap React toolchain and ensure build output can be served by FastAPI static path.
2. Port only layout shell and static controls first; keep legacy behavior behind feature flag.
3. Move API calls into services and verify parity by snapshotting payloads in browser network panel.
4. Integrate OL map controller and verify:
   1. base layer render
   2. raster layer render
   3. vector layer render
5. Port Explorer with drag source behavior and scenario switching.
6. Port Layer Manager with reorder and insertion targeting.
7. Port Jobs Manager with WS updates.
8. Run parity checklist and fix regressions.
9. Flip default route, complete soak cycle, and remove legacy entrypoint.

## Testing Plan
1. Unit tests:
1. filter matching/highlighting
2. tree visibility and expansion logic
3. layer reorder insertion index math
4. raster style expression generation

2. Integration tests:
1. scenario switch rehydrates scenario-scoped layers
2. explorer drag/drop creates layer in expected scenario
3. layer style edits persist and survive reload
4. task selection and job launch/cancel workflow
5. WS event reconciliation updates UI state

3. Regression/contract checks:
1. Existing `backend/tests/contract/test_map_milestone.py`
2. Existing `backend/tests/contract/test_phase3_1_scenario_workspace.py`
3. Add React-specific smoke for rendered controls and key flows

## Manual Verification Checklist (React Cutover)
1. Desktop and narrow layout parity
2. `Sessions` and `Layers/Jobs` buttons remain visible at all widths
3. Side panels are resizable from drag handles and remain usable after resize
4. Scenario selection from combobox and map footprint click
5. Explorer filtering + drag/drop to map and layer manager
6. Layer reorder by drag/drop and persistence
7. Raster colormap and diagnostics behavior
8. Job launch/progress/cancel
9. WS multi-tab reconciliation

## Risks and Mitigations
1. Risk: OpenLayers re-creation on React rerender causing performance/state loss.
1. Mitigation: isolate OL instance in stable controller/ref lifecycle.
2. Risk: drag/drop regressions in tree/layer manager.
1. Mitigation: keep payload contract unchanged and add focused integration tests.
3. Risk: subtle scenario-scoping regressions.
1. Mitigation: preserve `scenario_id` as explicit dependency in hooks and reducer state.
4. Risk: migration churn in one large PR.
1. Mitigation: phase-gated PRs with explicit rollback commits.

## Rollback Plan
1. Revert to a pre-cutover commit/tag that still contains legacy implementation if emergency rollback is required.
2. Rebuild frontend assets and redeploy backend static bundle.
3. Re-run parity checklist before re-cutover.

## Acceptance Criteria
1. React map page matches current feature behavior with no contract changes.
2. Requested baseline adjustments are complete:
   1. `Sessions` rename
   2. always-visible side-panel buttons
   3. resizable side panels
3. Existing map milestone contract tests remain passing.
4. Manual parity checklist passes on Windows 11 desktop and narrow layout.
5. Legacy fallback route is no longer required after stabilization closeout.
