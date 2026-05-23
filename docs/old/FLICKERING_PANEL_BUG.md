# Bug Report: Right Panel Flickering (React Map Milestone)

## Problem Statement
After migrating the Map Milestone UI to React, the right panel (containing the Layer Manager and Jobs Manager) exhibited persistent, high-frequency flickering upon map rendering and scenario loading.

## Root Cause Analysis

### 1. The `<details>` Infinite Toggle Loop
The primary driver of the flickering was an infinite render loop within the `LayerCard` component. 

- **The Logic:** The component used a standard HTML `<details>` element with its `open` attribute controlled by React state (`expanded`). It also had an `onToggle` event listener to sync manual user clicks back to React state.
- **The Trigger:** When `LayerManagerPane` received new layers from the server, it would programmatically set `expanded` to `true` for new layers.
- **The Loop:** 
    1. React sets `open={true}` on the DOM element.
    2. The browser's native `<details>` behavior triggers a `toggle` event because the state changed.
    3. The `onToggle` listener fired and blindly toggled the state: `setExpanded(!expanded)`.
    4. This flipped the state back to `false`, causing a re-render.
    5. The re-render closed the element, triggering *another* `toggle` event.
    6. This flipped the state back to `true`, restarting the cycle.
- **The Symptom:** React eventually throttles this loop, but not before causing dozens of renders per second, which manifested as a visible flicker of the panel's contents.

### 2. Referential Instability
Even without the loop, the `RightPane` (wrapped in `React.memo`) was re-rendering unnecessarily because its props were not referentially stable.
- `refreshScenarioLayers` was being re-created on every `activeScenarioId` change.
- `scenarioLayers` was being set to a new array object on every WebSocket event, even if the data content was identical to the current state.

### 3. CSS Compositor Churn
Redundant style definitions existed in both the legacy `styles.css` and the new `app.css`. Overlapping rules for `.workspace-pane` and `.layer-group` caused the browser's layout engine to perform unnecessary recalculations and repaints during the React render bursts.

---

## The Solution

### 1. Conditional Toggle Guard
In `LayerCard.tsx`, the `onToggle` handler was modified to check the actual state of the DOM against the React state before triggering an update:
```typescript
onToggle={(event) => {
  const isNowOpen = (event.target as HTMLDetailsElement).open;
  if (isNowOpen !== expanded) {
    onToggleExpanded(layer.layer_id);
  }
}}
```
This ensures that programmatic updates to the `open` prop (which occur during rendering) do not trigger a recursive state update.

### 2. State & Callback Stabilization
- **Ref-based Identity:** In `App.tsx`, `refreshScenarioLayers` now uses a `useRef` for the `activeScenarioId`. This allows the function's identity to remain constant (stable) across the entire application lifecycle while still accessing the latest scenario context.
- **Shallow Equality Checks:** The `refreshScenarioLayers` function now performs a deep comparison of layer IDs, z-indexes, and styles before calling `setScenarioLayers`. If the data is unchanged, the state is not updated, preventing downstream re-renders.
- **Prop Memoization:** The `RightPane` props are now consolidated into a `useMemo` block to ensure that the `React.memo` guard on the component can effectively skip renders.

### 3. CSS Consolidation
- Legacy styles that were superseded by React components were removed from `styles.css`.
- Essential layouts were moved to `app.css` and optimized to prevent layout shifts.

### 4. Production Cleanup
All debug instrumentation (render counters and `useWhyDidYouUpdate` hooks) used to diagnose the loop have been removed, resulting in a clean, high-performance production build.
