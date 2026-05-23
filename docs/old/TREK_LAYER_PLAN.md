# Trek Layers In Main App: Implementation Plan

## Goal
Add Moon Trek catalog search and multi-layer add/remove controls to the main Lunar Analyst page, equivalent to MoonLayers notebook behavior.

## Scope
- In scope:
  - Trek catalog fetch/search API in backend.
  - New frontend panel for Trek search and add/remove.
  - Support for multiple active Trek overlays on the map.
  - Per-overlay visible/opacity/remove controls in UI.
- Out of scope:
  - Persisting active Trek overlays to scenario DB.
  - Assistant MCP operations for Trek catalog.

## Phase 1: Backend Trek Catalog API
- Files:
  - `backend/services/trek_catalog_service.py` (new)
  - `backend/api/routers/trek.py` (new)
  - `backend/api/app.py` (include router)
- Endpoints:
  - `GET /api/v1/trek/layers`
  - `GET /api/v1/trek/layers:search?pattern=...`
- Behavior:
  - Fetch Trek South Pole catalog from Trek service.
  - Cache results in-process with TTL.
  - Support boolean text search (`AND`, `OR`, `NOT`, `-`, parentheses) matching MoonLayers semantics.

## Phase 2: Frontend Trek Services + Types
- Files:
  - `backend/web/lunar_analyst/src/services/trekService.ts` (new)
- Contracts:
  - `listTrekLayers()`
  - `searchTrekLayers(pattern: string)`
  - `type TrekLayerMetadata`

## Phase 3: Map Support For Multiple Trek Overlays
- Files:
  - `backend/web/lunar_analyst/src/map/trekLayerFactory.ts` (new)
  - `backend/web/lunar_analyst/src/map/mapController.ts`
  - `backend/web/lunar_analyst/src/map/MapViewport.tsx`
- Behavior:
  - Build Trek overlay layer from catalog metadata.
  - Track active Trek overlay layers by ID.
  - Sync overlays from React state.
  - Support per-overlay visibility and opacity updates.

## Phase 4: UX Panel + Integration
- Files:
  - `backend/web/lunar_analyst/src/components/trek/TrekLayerCatalogPane.tsx` (new)
  - `backend/web/lunar_analyst/src/App.tsx`
  - `backend/web/lunar_analyst/src/styles/app.css`
- UX:
  - Add `Trek Layer Catalog` panel in right pane.
  - Search box with boolean pattern help text.
  - Results table/list with add/remove action.
  - Active overlays section with visible/opacity/remove controls.

## Phase 5: Tests
- Backend:
  - `backend/tests/worker/test_trek_catalog_service.py` (new)
- Frontend:
  - `backend/web/lunar_analyst/src/__tests__/trekService.test.ts` (new)
- Existing test suites must remain green.

## Acceptance Criteria
- User can search Trek layers and add more than one overlay at once.
- Added overlays render on map and can be toggled/opacity-adjusted/removed.
- Same Trek layer is not duplicated if added repeatedly.
- Base `Moon Trek Base` behavior remains unchanged.
