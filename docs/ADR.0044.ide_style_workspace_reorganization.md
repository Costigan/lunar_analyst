# ADR 0044: Activity-Bar Workspace Reorganization

## Status
Accepted

## Context
The Lunar Analyst web application currently uses a flexible layout managed by `flexlayout-react` (introduced in ADR 0017). While this provides user agency through dockable panels, the current default arrangement and styling follow a traditional "dashboard" aesthetic.

As the application moves toward a more complex, multi-tool environment involving notebooks, job launch surfaces, map interaction, and assistant sessions, a more structured workspace with a left-side Activity Bar is desired to improve analyst workflow ergonomics.

## Decision
We will reorganize the Lunar Analyst workspace around a left-side Activity Bar and `flexlayout-react` border zones. The goal is a more structured analyst workspace while preserving the existing dockable-panel model and the ability for users to rearrange panels after startup.

### 1. Functional Zones
- **Activity Bar (Left Border Tab Strip):** A narrow vertical strip (48px) on the far left. Icons in this bar toggle the visibility of the Primary Sidebar.
- **Primary Sidebar (Left Border):** Contains "Explorer" style tools:
    - **Scenario Explorer**
    - **Layer Manager**
    - **Moon Trek Layers**
    - **Tools**
- **Secondary Sidebar (Right Border):** Dedicated to Assistant interactions:
    - **Assistant Input**
    - **Assistant Response** for compact response viewing while keeping the map visible
- **View Group (Central Row):** The primary work area for persistent views:
    - **Map Viewport** (Persistent, non-closable)
    - **Notebooks** (Marimo-backed interactive documents, opened as new tabs)
    - **Expanded Assistant Response** tabs when a response needs more space than the right sidebar provides
- **Panel (Bottom Border):** Dedicated to outputs and background tasks:
    - **Jobs Manager** (job status, progress, logs, and background output)

### 2. Implementation Details
- **FlexLayout Borders:** The existing layout model will be migrated to use `borders` for the Left, Right, and Bottom functional areas. This allows these zones to be collapsed or expanded while preserving the central View Group.
- **Notebook Component:** A new `Notebook` component will be added to the `PanelFactory`. It will render a Marimo notebook session within an `<iframe>`, resolving the `base_url` from the `marimo_service`.
- **Assistant Response Placement:** The assistant response surface will support two presentation modes:
    - a compact docked mode in the right sidebar for keeping map effects visible during interaction
    - an expanded mode in the central View Group for responses with rich content or longer analysis
  The same underlying assistant session should be viewable in either location without creating duplicate sessions.
- **Workspace Styling (CSS):**
    - The `app.css` will be updated to refine border colors, tab styles, and background gradients to support a clean activity-bar workspace feel.
    - Explicit support for both **Dark** and **Light** skins will be maintained, with dedicated theme variables for the Activity Bar, sidebars, editor area, and bottom panel.
- **Layout Persistence:** Existing saved layouts do not need migration support. The default layout can change freely at this stage of the product.

### 3. Notebook Lifecycle
- Notebooks can be opened directly from the application via an "Open in Notebook" action.
- Each notebook will open in a new tab within the central View Group.
- Notebook tabs are closable, but the underlying Marimo session may persist until explicitly stopped or the backend session expires.
- This ADR is concerned with the workspace/UI behavior for notebook opening and presentation. It does not require changes to the backend notebook execution API.

## Rationale
- **Structured Navigation:** A dedicated Activity Bar gives the workspace a clear, consistent way to switch between primary tool surfaces.
- **Optimal Space Utilization:** Collapsible sidebars and bottom panels maximize the screen real estate for the Map and Notebooks.
- **Map-Aware Assistant Workflow:** Assistant responses can stay compact beside the map or expand into the main work area depending on the task.
- **Multi-Document Support:** The central View Group allows users to switch between spatial analysis (Map), notebook work, and expanded assistant outputs using a standard tabbing metaphor.
- **Maintainability:** Using `flexlayout-react` borders is more idiomatic than the previous manual row/column nesting for sidebars.

## Consequences
- **Positive:** More ergonomic and structured workspace; better support for diverse tool types.
- **Positive:** The Tools surface becomes easier to discover as a first-class activity.
- **Negative:** Higher CSS maintenance to ensure the activity-bar workspace looks correct across both dark and light skins.
- **Negative:** Assistant-output placement logic becomes more complex because the same response surface must work in both compact and expanded modes.

## Major Considerations

### FlexLayout Border Icons
We will map Blueprint JS icons to the `icon` property of `flexlayout-react` tab nodes to populate the Activity Bar.

### Tools as a Primary Activity
Tools are a first-class workspace surface and must be directly reachable from the Activity Bar via the Primary Sidebar. This ADR does not collapse tool launching into the Jobs Manager.

### Assistant Output Dual Placement
Assistant output should support both compact and expanded presentation without splitting the conversation model. Compact mode is optimized for keeping the map visible; expanded mode is optimized for dense textual or rich rendered output.

### Notebook Persistence
Unlike the Map, Notebook tabs are dynamic. The application must track open notebook sessions to ensure they are restored correctly across page reloads if possible.

### Theme Contrast
The activity-bar workspace should use subtly different background shades for the sidebar vs. the editor area. We will introduce new theme variables (e.g., `--mm-sidebar-bg`, `--mm-activity-bar-bg`) where necessary to achieve this depth while maintaining dark and light skin parity.

### Dockability
The default arrangement is prescribed by this ADR, but users may continue rearranging docked panels after startup. The default layout should be optimized for analyst workflows without removing the existing dockable-workspace flexibility.

### Screen Size Support
Mobile is not a target, but the default layout must remain usable on typical laptop screens. Sidebar and panel defaults should not leave the map or active document area unusably constrained at common desktop widths.

## Acceptance Criteria
- The default workspace presents an Activity Bar on the left and uses left, right, and bottom border zones for primary navigation and secondary surfaces.
- The Primary Sidebar includes Scenario Explorer, Layer Manager, Moon Trek Layers, and Tools.
- The Map remains exactly one panel and remains non-closable.
- Users can open notebooks directly into central tabs without changing the backend notebook execution API.
- Assistant input is available in the right sidebar, and assistant output can be viewed either in the right sidebar or as an expanded central tab.
- Both dark and light skins are supported with intentional styling for the Activity Bar, sidebars, central editor/view area, and bottom panel.
- The default layout is usable on typical laptop-size screens, with sidebars and bottom panel collapsible to prioritize the map or active document.
- Users may still rearrange docked panels after startup, and Reset Layout restores the new default arrangement.

## Implementation Plan

### Phase 1: Layout Model & Schema Migration
- [ ] **Schema Handling:** Update the default workspace layout definition as needed for the new arrangement. Backward-compatibility migration is not required at this stage.
- [ ] **Border Definition:** Define the `borders` array in `DEFAULT_WORKSPACE_LAYOUT_JSON` with `left`, `right`, and `bottom` locations.
- [ ] **Panel Relocation:** Move `scenarioExplorer`, `layerManager`, `moonTrek`, and `tools` into the left-side activity/sidebar flow; move `assistantInput` and compact `assistantResponse` to the `right` border; move `jobsManager` to the `bottom` border.
- [ ] **Tab Configuration:** Add `icon` properties to `WORKSPACE_PANELS` mapping to Blueprint JS icons (e.g., `folder-open`, `layers`, `chat`).
- [ ] **Global Configuration:** Set `borderTabWidth: 48` and appropriate sidebar/panel sizes in the FlexLayout global config to implement the Activity Bar and surrounding workspace zones.

### Phase 2: Component Preparation
- [ ] **Notebook Pane:** Create `backend/web/lunar_analyst/src/components/notebook/NotebookPane.tsx` using an `<iframe>` to host the Marimo URL.
- [ ] **Panel Factory Update:** Add `WORKSPACE_COMPONENTS.notebook` to `PanelFactory.tsx`.
- [ ] **Expanded Assistant Pane:** Add an expanded assistant-response component or panel mode that can open in the central View Group while reusing the active assistant session.
- [ ] **Workspace Integration:** Update `AppLayout.tsx` to handle notebook tabs, assistant-output placement, and the necessary URLs/state passed into the factory.

### Phase 3: Activity-Bar Styling
- [ ] **Theme Variables:** Define activity-bar workspace CSS variables in `app.css` (e.g., `--mm-activity-bar-bg`, `--mm-sidebar-bg`, `--mm-editor-bg`).
- [ ] **Activity Bar Styling:** Customize `.flexlayout__border_left` to have a darker, distinct background from the main editor.
- [ ] **Tab Styling:** Update `.flexlayout__tab_button` and `.flexlayout__border_button` to use clear active indicators for the Activity Bar, sidebar tabs, and central views.
- [ ] **Light/Dark Parity:** Ensure both dark and light skins have intentional, legible contrast across the activity bar, sidebars, central work area, and bottom panel.

### Phase 4: Functional Wiring & Polish
- [ ] **Notebook Launch:** Add an "Open in Notebook" action to the relevant application UI so notebooks open directly into the workspace.
- [ ] **Assistant Expansion Action:** Add a visible affordance to move assistant output between compact sidebar mode and expanded central-tab mode.
- [ ] **Layout Action Logic:** Update `handleLayoutAction` in `AppLayout.tsx` so the Map remains non-closable, the default border behavior is preserved, and user rearrangement still works.
- [ ] **Toolbar Cleanup:** Remove redundant top-level "Show X" buttons that are replaced by the Activity Bar.
- [ ] **Reset Logic:** Verify that "Reset Layout" correctly re-populates the new border-based default arrangement.
- [ ] **Laptop Verification:** Validate that the default layout remains usable on laptop-class screen widths, including map-first usage with collapsed secondary panels.
