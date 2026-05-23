# Migration Plan: Blueprint JS 6 for Lunar Analyst (Revised)

This document updates the Blueprint migration strategy for `backend/web/lunar_analyst` with architecture/ADR alignment, Blueprint 6 compatibility, risk gates, and rollback controls.

## 1. Decision and Scope

### 1.1 Decision
- Adopt **Blueprint JS 6** for the React application shell and desktop-oriented controls.
- Keep migration **incremental and reversible** behind a UI feature flag.

### 1.2 ADR Alignment Requirement
- `docs/ADR.0005.web_ui_component_toolkit.md` currently records Shoelace/Web Awesome direction for map controls.
- Before broad implementation, create an ADR addendum (or superseding ADR) that states one of:
  - Blueprint becomes the primary React UI toolkit, while Shoelace remains only where already embedded and stable.
  - Shoelace remains map-control-only, Blueprint covers shell/Explorer/Jobs/Layer management.
- Until ADR alignment is merged, this plan is design guidance, not an execution authorization.

### 1.3 Non-Goals
- No backend contract changes.
- No map rendering pipeline rewrite.
- No changes to JobHandlers-centered compute contracts.

## 2. Blueprint 6 Technical Baseline

- Packages:
  - `@blueprintjs/core`
  - `@blueprintjs/icons`
  - `@blueprintjs/select`
- Theme namespace:
  - Use `bp6-dark` (not `bp5-dark`).
- Context menu APIs:
  - Use Blueprint 6 APIs from `@blueprintjs/core`.
  - Do not add `@blueprintjs/popover2` as a migration dependency.
- Styling strategy:
  - Import Blueprint CSS globally in `src/main.tsx`.
  - Layer current app layout CSS on top of Blueprint primitives.

## 3. Risk Register

### 3.1 Highest Risk: Explorer Tree-Grid
- `FilteredTreeTable.tsx` is a tree-grid with:
  - custom multi-column row layout,
  - gap-aware filtering/highlighting,
  - drag payload contract to map/layer manager.
- A direct swap to Blueprint `Tree` can regress grid semantics and keyboard behavior.

### 3.2 Secondary Risk: Interaction Regressions
- Drag/drop insertion affordances in `LayerManagerPane.tsx`.
- Debounced layer style updates in `LayerCard.tsx`.
- Current narrow-layout behavior at `NARROW_BREAKPOINT`.

## 4. Rollout Strategy (Flagged and Gated)

### 4.1 Phase 0: Preconditions
1. ADR reconciliation is merged.
2. Add feature flag:
   - `VITE_USE_BLUEPRINT_UI=true|false`.
3. Add app-level toggle point so legacy UI path remains available.

Acceptance gate:
- [ ] Legacy UI remains default.
- [ ] Blueprint dependencies compile with no runtime warnings.

### 4.2 Phase 1: Shell and Primitive Wiring
Target files:
- `backend/web/lunar_analyst/src/main.tsx`
- `backend/web/lunar_analyst/src/App.tsx`
- `backend/web/lunar_analyst/src/components/Toolbar.tsx`
- `backend/web/lunar_analyst/src/styles/app.css`

Tasks:
1. Import Blueprint CSS.
2. Apply `bp6-dark` at root container for flagged path.
3. Migrate top toolbar buttons/labels to Blueprint primitives.
4. Preserve existing pane resize math and grid behavior.

Acceptance gate:
- [ ] `VITE_USE_BLUEPRINT_UI=false` keeps current UI unchanged.
- [ ] `VITE_USE_BLUEPRINT_UI=true` renders Blueprint shell and preserves map resize/layout behavior.

### 4.3 Phase 2: Explorer Spike (Hard Gate)
Target files:
- `backend/web/lunar_analyst/src/components/explorer/FilteredTreeTable.tsx`
- `backend/web/lunar_analyst/src/components/explorer/ScenarioExplorerPane.tsx`

Tasks:
1. Build a spike using Blueprint components for Explorer rows.
2. Preserve existing row model (`ExplorerTreeRow`) and drag payload shape.
3. Keep explicit multi-column grid layout in row label/content; do not collapse to single-line tree text.
4. Validate keyboard activation and filtering highlight parity.

Go/No-Go rule:
- If parity is not reached quickly, retain custom tree-grid internals and apply Blueprint only around shell/inputs.

Acceptance gate:
- [ ] No loss of column visibility behavior.
- [ ] No loss of drag-to-map/layer payload behavior.
- [ ] No loss of gap-aware token filter behavior.

### 4.4 Phase 3: Layer and Jobs Controls
Target files:
- `backend/web/lunar_analyst/src/components/layers/LayerCard.tsx`
- `backend/web/lunar_analyst/src/components/layers/LayerManagerPane.tsx`
- `backend/web/lunar_analyst/src/components/jobs/JobsManagerPane.tsx`
- `backend/web/lunar_analyst/src/components/common/PatternCombobox.tsx`

Tasks:
1. Replace basic HTML inputs/buttons with Blueprint equivalents where behavior is unchanged.
2. Keep debounce and optimistic patch logic unchanged.
3. Migrate `PatternCombobox` to Blueprint `Suggest` only if custom filter logic is preserved.

Acceptance gate:
- [ ] Layer style controls still update map immediately.
- [ ] Drag/drop reorder and insertion still behave identically.
- [ ] Jobs launch/cancel flow unchanged.

### 4.5 Phase 4: Context Menus and Tooltips
Tasks:
1. Add right-click menus for layer rows and explorer nodes.
2. Add concise tooltips for advanced controls.
3. Keep menu actions behind existing API pathways (no new backend coupling).

Acceptance gate:
- [ ] Context menus work in both desktop and narrow layouts.
- [ ] No accidental action triggering during drag operations.

### 4.6 Phase 5: Cutover and Cleanup
Tasks:
1. Flip default to Blueprint after all gates pass.
2. Remove dead legacy UI code in a separate cleanup PR.
3. Keep rollback flag for one release cycle.

Acceptance gate:
- [ ] Blueprint is default.
- [ ] Legacy path removable with no contract/UI regressions.

## 5. Testing and Verification Plan

### 5.1 Unit/Vitest
- Keep existing tests green:
  - `backend/web/lunar_analyst/src/__tests__/filterMatch.test.ts`
  - `backend/web/lunar_analyst/src/__tests__/treeVisibility.test.ts`
  - `backend/web/lunar_analyst/src/__tests__/layerManager.test.ts`
  - `backend/web/lunar_analyst/src/__tests__/layerOrder.test.ts`
  - `backend/web/lunar_analyst/src/__tests__/dragPayload.test.ts`
  - `backend/web/lunar_analyst/src/__tests__/jobsManager.test.ts`
- Add targeted UI behavior tests for any rewritten component logic (especially combobox selection and tree row activation).

### 5.2 Integration/Contract Safety
- Confirm no backend API/WS contract changes are introduced.
- Re-run scenario-scoped layer and drag/drop workflow checks from existing contract coverage.

### 5.3 Manual Checks
- Desktop and narrow layout.
- Explorer selection/filtering/expand-collapse.
- Explorer drag to map and layer manager.
- Layer reorder and style controls (opacity, brightness, contrast, colormap).
- Jobs launch/progress/cancel panel.

## 6. Rollback Plan

If migration introduces regressions:
1. Set `VITE_USE_BLUEPRINT_UI=false` to revert to legacy UI immediately.
2. Revert only Blueprint-touching frontend commits; leave backend untouched.
3. Re-open with smaller scope (component-level migration, not full-surface migration).

## 7. Updated Acceptance Criteria

- [ ] ADR alignment is explicit and merged.
- [ ] Blueprint 6 setup is correct (`bp6-dark`, no `@blueprintjs/popover2` dependency).
- [ ] Explorer spike proves parity or documents fallback decision.
- [ ] Existing drag/drop, filtering, and scenario scoping behavior is preserved.
- [ ] Existing Vitest suite remains green.
- [ ] Flagged rollback path is validated before default cutover.
