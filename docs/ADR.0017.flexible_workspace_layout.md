# ADR 0017: Flexible Workspace Layout with FlexLayout-React

## Status
Accepted

## Context
The Lunar Analyst web application currently employs a fixed grid-based layout for its primary workspace. This layout, managed by `App.tsx` and `useWorkspaceLayout.ts`, defines static positions for several key panels:
- **Scenario Explorer** (Left)
- **Layer Manager** (Right)
- **Jobs Manager** (Right, stacked with Layer Manager)
- **Assistant Input** (Bottom-left)
- **Assistant Response** (Bottom-right)
- **Map Viewport** (Center)

Resizing these panels is currently handled via manual mouse event listeners and CSS grid variables. As the toolkit matures and the number of analysis tools grows (e.g., adding Timeline, Statistics, or multiple Map views), a more robust and user-configurable layout system is required to support varied analyst workflows.

## Decision
We will integrate `flexlayout-react` to manage the main workspace layout. This library will replace the custom grid and resizing logic, allowing all major panels to be moved, tabbed, docked, and resized by the user.

### Key Changes:
1.  **Component Factory:** All major panels will be registered in a centralized factory function that `flexlayout-react` uses to instantiate panel content.
2.  **Layout Persistence:** The current layout state (panel positions, sizes, and visible panel groupings) will be serialized to `JSON` and persisted in `localStorage` using a versioned storage key. Persisted layouts from older app schema versions may be discarded instead of migrated in this first iteration.
3.  **Single Map Invariant:** The workspace will support exactly one `MapViewport` panel. The map panel is non-closable, and layout restore/reset must always preserve one map pane.
4.  **Dockable Workspace Scope:** The Moon Trek layer list is part of the dockable workspace and will be represented as a normal dockable panel rather than a separate side-pane implementation.
5.  **Map Integration:** The `MapViewport` component will be updated to handle container resizes automatically via `ResizeObserver` and layout/panel activation events.
6.  **Theme Synchronization:** FlexLayout's styling will be integrated with the existing Blueprint.JS and custom theme CSS (Dark, Light, Ocean, etc.).
7.  **Panel Close/Reset Policy:** All standard panels other than the map may be closed by the user. A "Reset Layout" action restores the default set of standard panes.
8.  **State Preservation Policy:** Panel-local transient state will be treated selectively:
    - Unsent Assistant prompt text must survive panel close/reopen and remount.
    - Jobs Manager draft parameter state must survive panel close/reopen and remount.
    - Temporary UI state such as accordion expansion, selected run row, or similar ephemeral view state may reset.

## Rationale
- **User Agency:** Analysts can prioritize different tools (e.g., maximizing the Assistant for a prompt-heavy session or maximizing the Map for spatial exploration).
- **Reduced Complexity:** Replaces ~200 lines of manual resizing and visibility logic in `App.tsx` and `useWorkspaceLayout.ts` with a declarative, industry-standard library.
- **Scalability:** New panels can be added by simply updating the layout model and the component factory, without touching the core `App` shell or grid styles.
- **Standardization:** `flexlayout-react` is widely used in IDEs and data-heavy web applications, providing a familiar "docking" experience for professional users.
- **Risk Control:** Explicitly constraining the first iteration to a single map pane and discard-on-version-change persistence keeps the refactor bounded and reversible.

## Major Considerations

### 1. OpenLayers Map Resizing
OpenLayers requires an explicit call to `map.updateSize()` when its container dimensions change. Because `flexlayout-react` performs resizes via JS/DOM manipulations that don't always trigger a window `resize` event, the `MapViewport` must use a `ResizeObserver` to detect changes to its immediate parent container and notify the `MapController`. The map integration must also handle panel activation and restore flows so the single map pane remains valid after layout restore/reset.

### 2. Theming and CSS
FlexLayout uses its own CSS for tabs, splitters, and borders. We must map our existing theme variables (e.g., `--mm-shell-bg`, `--mm-shell-text`) to FlexLayout's CSS variable overrides to ensure a seamless visual experience across all themes.

### 3. Layout Reset
Users may occasionally create cluttered or unusable layouts. A "Reset Layout" action must be provided in the main toolbar or menu to restore the default configuration.

### 4. Persistence Contract
Persisted layout storage must be namespaced by an application layout schema version and a layout-mode key. In this ADR iteration, older persisted layouts may be deleted when the schema changes rather than migrated forward.

### 5. Panel State Ownership
Because several existing panels currently keep meaningful draft state inside component-local React state, the refactor must preserve selected state across panel close/reopen and remount. Assistant draft text and Jobs draft parameters must therefore be hoisted or otherwise persisted outside the panel instance lifecycle.

### 6. Out of Scope
- Multiple simultaneous map panes.
- Pop-out/external window support.
- Separate narrow/mobile layout mode support.

## Implementation Plan

### Phase 1: Dependency & Model Definition
1.  Add `flexlayout-react` to `package.json`.
2.  Create `src/layout/defaultModel.ts` defining the initial desktop configuration, including exactly one non-closable map panel and standard closable panels for Scenario Explorer, Layer Manager, Jobs Manager, Assistant Input, Assistant Response, and Moon Trek Layers.
3.  Create `src/layout/PanelFactory.tsx` to map panel IDs to existing panel components (passing necessary props/hooks).
4.  Define a versioned layout persistence key and reset-on-version-mismatch behavior.

### Phase 2: Core Refactor
1.  Modify `MapViewport.tsx` to include a `ResizeObserver` hook that calls `controller.getMap().updateSize()`.
2.  Update `App.tsx`:
    -   Replace manual `aside` and `section` tags with the `<Layout>` component.
    -   Wire the `PanelFactory` to the layout.
    -   Implement `onModelChange` to persist the new layout to `localStorage`.
    -   Enforce the single-map invariant during startup, restore, and reset.
3.  Hoist or persist Assistant unsent prompt state and Jobs draft parameter state so panel close/reopen and remount do not discard them.

### Phase 3: Styling & Polish
1.  Add a `flexlayout.css` override file to sync with Lunar Analyst themes.
2.  Add a "Reset Layout" button to the `Toolbar`.
3.  Ensure layout reset restores all standard panes and clears incompatible persisted layout state when needed.

## Consequences
- **Positive:** Greatly improved UX for professional analysts; cleaner code in `App.tsx`.
- **Negative:** Small increase in initial bundle size (~50kb); requires careful prop-drilling or Context usage for panels that previously relied on `App.tsx` local state.
- **Negative:** The first iteration intentionally excludes multi-map and pop-out workflows, which may require future follow-on ADRs if analyst demand proves strong.
