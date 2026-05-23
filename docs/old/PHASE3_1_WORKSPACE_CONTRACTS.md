# Phase 3.1 Workspace Interaction and Data Contracts

Date: 2026-02-16  
Status: Implemented baseline

## 1. Scenario Selection and Context Scoping

Active scenario is the single context key for the combined workspace.

- Explorer row selection sets `activeScenarioId`.
- Map footprint click sets `activeScenarioId`.
- All layer controls and product-add actions operate on `activeScenarioId` only.
- WS event handling applies updates only when `event.scenario_id == activeScenarioId`.

Expected behavior:

1. Selecting scenario `S1` in Explorer updates map highlight and right-pane content to `S1`.
2. Clicking footprint for `S2` updates Explorer highlight and right-pane content to `S2`.
3. Layer list and layer edits never mix entries across scenarios.

## 2. Scenario Explorer Tree-Grid Schema

Columns:

- `Name` (always visible)
- `Type`
- `Created`
- `Size`
- `Notes`

Hierarchy:

1. Scenario rows
2. Group rows by `kind/subkind`
3. Product rows

Column visibility behavior:

- User can toggle visibility of `Type`, `Created`, `Size`, `Notes`.
- `Name` is fixed-visible.
- Visibility toggles apply to header and row cells using `data-col` attributes.

## 3. Map Integration Interactions

Implemented interactions:

1. Click-select by footprint:
- Scenario footprints render as a dedicated vector layer.
- Clicking a footprint activates its scenario in Explorer and right pane.

2. Explorer -> map drag/drop:
- Product rows are draggable.
- Dropping on map creates a new `LayerState` as top-most layer.

3. Explorer -> Layer Manager drag/drop insertion:
- Layer Manager exposes explicit drop zones.
- Dropping on a zone inserts layer at that stack position.
- Z-order is normalized after insert (`z_index` reassigned in deterministic steps).

## 4. API Data Loading Sequence

Startup sequence:

1. `GET /api/v1/lunar-analyst/config`
2. `GET /api/v1/lunar-analyst/colormaps`
3. `POST /api/v1/lunar-analyst/bootstrap`
4. `GET /api/v1/scenarios`
5. `GET /api/v1/scenarios/{activeScenarioId}/products`
6. `GET /api/v1/scenarios/{activeScenarioId}/layers`
7. `GET /api/v1/jobs/handlers`
8. `WS /api/v1/events` subscription

Active-scenario switch sequence:

1. Set `activeScenarioId`
2. `GET /api/v1/scenarios/{activeScenarioId}/products`
3. `GET /api/v1/scenarios/{activeScenarioId}/layers`
4. Re-render Explorer + footprint highlight

## 5. Test Mapping

- Scenario-scoped API and context events:
  - `backend/tests/contract/test_phase3_1_scenario_workspace.py`
- Existing Phase 3 smoke and layer-state coverage:
  - `backend/tests/contract/test_phase3_minimal_web_client.py`


