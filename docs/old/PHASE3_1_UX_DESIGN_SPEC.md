# Phase 3.1 UX Design Spec: Combined Scenario Workspace View

Date: 2026-02-16  
Status: Approved baseline for implementation planning

## 1. Purpose

Define the user experience for a combined workspace view that keeps:

- Scenario Explorer
- OpenLayers map canvas
- Layer Manager

visible in one responsive interface for day-to-day scenario analysis.

## 2. Scope

In scope:

- Visual layout and panel structure.
- Information hierarchy and primary user flows.
- Responsive behavior (desktop and narrow widths).
- Accessibility and keyboard expectations.
- UX acceptance criteria for Phase 3.1 implementation.

Out of scope (deferred to later Phase 3.1 tasks):

- Detailed interaction contracts for selection/scoping logic.
- Drag/drop insertion algorithms.
- API loading sequence details.
- Automated test implementation.

## 3. Primary Users and Goals

Primary user:

- Analyst managing multiple scenarios and products while inspecting map context.

Goals:

- Quickly find scenario/products in a structured explorer.
- Add/remove and style layers without leaving map context.
- Keep map as primary visual workspace.
- Maintain awareness of active scenario and current layer stack.

## 4. Information Architecture

Top-level regions:

1. Header bar
2. Left workspace pane (Scenario Explorer)
3. Center map pane (OpenLayers map, dominant area)
4. Right controls pane (Layer Manager + Jobs)
5. Optional status/footer strip for transient messages

Hierarchy principle:

- Map-first: map gets most space.
- Explorer second: discovery/navigation.
- Controls third: operational adjustments.

## 5. Desktop Layout (>= 1280px)

Grid template:

- Columns: `320px | 1fr | 320px`
- Rows: `auto | 1fr`

Placement:

- Header spans all columns.
- Explorer pinned left, full working-height.
- Map centered, full working-height.
- Right pane stacks:
  - Layer Manager (top, default expanded)
  - Jobs panel (below, default expanded)

Behavior:

- Explorer and right pane independently scroll.
- Map remains full-height in center column.
- Panel widths are collapsable and resizable in a later task; fixed width for baseline.

## 6. Narrow Layout (< 1280px)

Mode:

- Two-pane priority mode with overlay drawers.

Behavior:

- Map remains primary full-width canvas.
- Explorer collapses to left slide-over drawer.
- Layer Manager/Jobs collapse to right slide-over drawer.
- Header contains explicit toggles: `Explorer` and `Layers/Jobs`.
- Drawers trap focus while open and close with `Esc`.

## 7. Scenario Explorer UX (Design Baseline)

Structure:

- Tree-grid style with scenario nodes and product children.
- Sticky header row for columns.

Baseline columns:

- `Name` (fixed, always visible)
- `Type`
- `Created`
- `Size`
- `Notes`

Visual states:

- Active scenario row highlighted.
- Expanded/collapsed chevrons for hierarchical rows.
- Empty state: clear guidance when no scenarios exist.

## 8. Layer Manager UX (Design Baseline)

Structure:

- Ordered list of layers with compact cards.
- Each card has always-visible:
  - visibility toggle
  - layer name
  - collapse/expand affordance

Expanded card controls:

- Opacity
- Brightness (raster)
- Contrast (raster)
- Colormap (raster)

Operational controls:

- Add layer from selected product.
- Remove layer.

## 9. Visual Design Direction

- Preserve current lunar-analyst visual language.
- Compact density to maximize map area.
- Small, readable typography for control panes.
- Clear selected/active state colors with sufficient contrast.
- Minimize visual noise over map backdrop.

## 10. Accessibility Baseline

- All actionable controls keyboard reachable.
- Visible focus outlines on explorer rows, map controls, and layer controls.
- Semantic labels for toggles/sliders/selects.
- `Esc` closes any open drawer in narrow mode.
- Color is not sole indicator of selection/visibility state.

## 11. UX Acceptance Criteria (for implementation phase)

*Status: All criteria satisfied via React/Blueprint JS 6 Migration (Phase 5).*

1. [PASS] On desktop, Explorer + Map + Layer Manager/Jobs are simultaneously visible without overlap.
2. [PASS] On narrow widths, map remains usable while Explorer and Layer/Jobs open as independent drawers.
3. [PASS] Layer card collapse/expand preserves visibility toggle and name in collapsed state.
4. [PASS] Active scenario is visually obvious in Explorer and reflected in workspace context indicators.
5. [PASS] Keyboard-only navigation can operate explorer selection and layer visibility/opacity controls.
6. [PASS] No pane blocks map zoom/pan controls unexpectedly.
7. [PASS] Explorer grid alignment is preserved across all tree levels (Restored in Phase 5).

## 12. Open Questions (to resolve in later Phase 3.1 tasks)

1. Preferred default width for explorer/right panes after first usability pass.
   No answer yet
2. Whether Jobs remains in right pane or moves to bottom dock for high-volume workflows.
   Answer: remain in right pane
3. Whether layer ordering uses drag handles only or also keyboard reordering actions.
   Answer: Both

