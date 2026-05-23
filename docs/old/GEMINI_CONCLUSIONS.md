# React Migration Audit Conclusions

This document summarizes the technical root causes for the bugs identified in `docs/CURRENT_BUGS.md` based on an audit of the React codebase in `backend/web/map_milestone/src`.

Status: Historical snapshot before subsequent bug-fix work. See `docs/CURRENT_BUGS.md` for current status.

## 1. Startup Flicker
- **Root Cause:** Fragmented state authority and redundant fetching.
- **Evidence:**
    - `App.tsx` (lines 142-167) maintains `scenarioLayers` state and updates it via WebSocket events.
    - `LayerManagerPane.tsx` (lines 101-120) maintains its own `layers` state and fetches it independently on mount/update.
- **Impact:** Desynchronized state updates and multiple overlapping network/render cycles during initialization.

## 2. Duplicate Layer Creation
- **Root Cause:** Event bubbling in drop handlers and hardcoded titles.
- **Evidence:**
    - `LayerManagerPane.tsx` (lines 191-213) handles drop in `renderDropZone` but lacks `event.stopPropagation()`.
    - `LayerManagerPane.tsx` (lines 218-225) handles drop on the container, leading to a second call if the first one bubbles.
    - `LayerManagerPane.tsx` (line 178) hardcodes the title: `title: "Product ${payload.product_id.slice(0, 8)}"`.
- **Impact:** Two layers created for a single drop; poor default naming.

## 3. Layer Panel IA Mismatch
- **Root Cause:** Disconnected components for filtering and reordering.
- **Evidence:**
    - `LayerManagerPane.tsx` (lines 242-255) renders a `FilteredList` separate from the actual layer stack.
    - `LayerManagerPane.tsx` (lines 257-285) renders the reorderable `LayerCard` list, which does not consume `filterText`.
- **Impact:** Redundant UI elements; filtering does not affect the actual layer cards.

## 4. Black Nodata Corners
- **Root Cause:** Incomplete style initialization and strict masking logic.
- **Evidence:**
    - `LayerManagerPane.tsx` (lines 182-184) creates layers without `nodataCutoff`.
    - `map/rasterStyle.ts` (lines 61-70) only generates a transparency mask if `nodataCutoff` is present in the style object.
- **Impact:** Warped GeoTIFF edges (value 0) render as black instead of transparent.

## 5. Colormap Persistence
- **Root Cause:** Desync between UI registry and Map Controller registry.
- **Evidence:**
    - `map/mapController.ts` (lines 36-58) `FALLBACK_COLORMAPS` only contains `gray` and `viridis`.
    - `LayerCard.tsx` (lines 145-155) allows selection of `magma`, `inferno`, and `plasma`.
- **Impact:** Selecting advanced colormaps fails to resolve in the map, falling back to grayscale.

## 6. Slow Slider Interactions
- **Root Cause:** Synchronous API "round-trips" on every input event.
- **Evidence:**
    - `LayerCard.tsx` (lines 103, 118, 133) uses `onChange` to immediately call `onPatch`.
    - `LayerManagerPane.tsx` (lines 151-154) `patchLayerAndRefresh` performs a network `PATCH` followed by a full `loadLayers()` refresh.
- **Impact:** UI freezes and slider lag as the browser waits for network/state cycles on every pixel moved.
