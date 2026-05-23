# Filter Components Plan (Map UI)

## Goal
Reuse one filtered-selection pattern across the map UI for:
1. Scenario selection
2. Product selection (existing Scenario Explorer pattern)
3. Layer selection in Layer Manager
4. Task selection in Jobs Manager (future Task Manager naming)

## Scope
- [DONE] Migrated to React/Blueprint JS 6 in `backend/web/lunar_analyst`.
- Preservation of token-based filtering and gap-aware visibility.

## Component Set

### A) Filtered List Component
- Purpose: flat list filtering and single/multi selection.
- Status: Implemented in React as `FilteredList.tsx` using Blueprint `MenuItem`.
  - Gap-aware subsequence token matching.
  - Keyboard navigation (`ArrowUp`, `ArrowDown`, `Enter`, `Escape`).
  - Single-select and multi-select modes.
- Primary UI targets:
  - Layer Manager layer list selection.
  - Task list selection (Jobs Manager evolution).

### B) Filtered Tree/Table Component
- Purpose: tree in first column with table columns for metadata.
- Core behavior:
  - Parent/child visibility rules under active filter.
  - Expand/collapse state control.
  - Row selection and activation.
  - Column visibility support.
- Primary UI target:
  - Scenario Explorer (replace current ad hoc tree-grid rendering logic).

### C) Pattern Combobox Component
- Purpose: type pattern, show matches below input, commit selected value into textbox.
- Core behavior:
  - Filter-as-you-type popup list.
  - Commit-on-select fills input with selected item label.
  - Keyboard navigation and click-outside close.
- Primary UI targets:
  - Scenario selector.
  - Task/job definition selector.
  - Optional layer quick-jump selector.

## Architecture Approach
1. Keep `backend/web/map_milestone/app.js` as orchestrator for API calls and map state.
2. Move filtering/selection display behavior into reusable components under `backend/web/map_milestone/components/`.
3. Use ES module custom elements compatible with current vanilla JS + Shoelace setup.
4. Keep shared filter semantics centralized in one utility module to ensure consistent behavior.

## File-Level Task List

1. `backend/web/map_milestone/components/filter_match.js`
- Add shared filter helpers:
  - tokenization
  - subsequence matching
  - all-token match
- Export pure functions used by all three components and app integrations.

2. `backend/web/map_milestone/components/filtered-list.js`
- Implement reusable filtered-list custom element.
- Support props/state: items, filter text, selected item(s), selection mode.
- Emit events for select/change/activate.

3. `backend/web/map_milestone/components/filtered-tree-table.js`
- Implement reusable filtered tree/table custom element.
- Support rows, hierarchy, expanded state, selected row(s), and visible columns.
- Emit row select/toggle/activate events.

4. `backend/web/map_milestone/components/pattern-combobox.js`
- Implement reusable pattern combobox custom element.
- Support items, input value, popup open/close, committed selection.
- Emit value/commit events.

5. `backend/web/map_milestone/components/index.js`
- Export/register component modules from one entrypoint for simple app imports.

6. `backend/web/map_milestone/app.js`
- Replace local filter helper implementations with imports from `filter_match.js`.
- Integrate `filtered-tree-table` for Scenario Explorer rendering.
- Integrate `pattern-combobox` for scenario selector and job/task selector.
- Integrate `filtered-list` for layer selection and task list selection.
- Keep map/layer/job API flow unchanged.

7. `backend/web/map_milestone/index.html`
- Add mount points/custom element tags for new reusable components.
- Remove or simplify legacy control markup replaced by components.

8. `backend/web/map_milestone/styles.css`
- Add shared component styles and tokens.
- Preserve current desktop and narrow/mobile drawer behavior.
- Add focus/active/selection styles for accessibility consistency.

9. `docs/HOW_TO_MANUALLY_TEST.md`
- Add manual checks for:
  - filtering correctness
  - keyboard navigation
  - selection/commit behavior
  - scenario switching and state refresh
  - no regression in drag/drop and job launch/cancel flows

## Delivery Order
1. Shared filter utilities (`filter_match.js`).
2. Pattern combobox (scenario + job selectors).
3. Filtered tree/table (Scenario Explorer).
4. Filtered list (Layer Manager + Task list).
5. Styling/accessibility polish and regression verification.

## Acceptance Criteria
1. One shared filtering behavior is used across list, tree/table, and combobox.
2. Scenario, product, layer, and task selection each use one of the reusable components.
3. Existing map/layer/job behaviors remain functional with no API contract changes.
4. Keyboard and mouse interactions are supported for all three components.
5. Manual verification checklist updated and completed for changed UI paths.
