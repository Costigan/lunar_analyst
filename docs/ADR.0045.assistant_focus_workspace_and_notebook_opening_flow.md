# ADR.0045: Assistant Focus Workspace and Notebook Opening Flow

- Status: Accepted
- Date: 2026-04-07
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0013.notebook_integration_choice.md`, `docs/ADR.0017.flexible_workspace_layout.md`, `docs/ADR.0044.ide_style_workspace_reorganization.md`, `AGENTS.md`

## Context

The current workspace already has:

- an activity-bar-based dockable shell,
- a right-sidebar assistant split into separate input and response panels,
- center-tab notebook support backed by Marimo iframes,
- a scenario explorer with scenario and filter controls,
- a light skin that is functionally complete but visually weak in several places.

Recent UI feedback identified four concrete problems:

1. Assistant control rows do not remain usable when the assistant pane becomes narrow.
2. The current light skin uses weak label contrast and unattractive accent colors.
3. The scenario explorer file-list header row has a vertical alignment bug.
4. The assistant and notebook workflows need a more focused center-workspace experience.

This ADR defines the implementation plan for those changes while preserving the existing architecture:

- FastAPI remains the control plane.
- The current assistant session model remains authoritative.
- Marimo remains the notebook integration.
- Notebook tabs continue to share space with the map in the center workspace.

## Problem

The current UI is structurally close to the desired workflow, but several behaviors are incomplete:

- Assistant controls are arranged as a single dense row that degrades badly at narrower widths.
- The light skin lacks a disciplined token set for productivity-oriented contrast and accents.
- The current assistant experience is split across compact right-sidebar panes only, which is suboptimal for longer interactions and rich outputs.
- The current `Open in Marimo` action opens the Marimo shell instead of directly opening a notebook document.
- The current explorer recognizes Marimo notebooks heuristically, but the product needs a clearer opening policy for generic `*.py` files.

## Decision

We will implement a focused assistant and notebook workspace flow in incremental phases.

### 1. Assistant Session Model

The right sidebar assistant and the center-workspace assistant will use the same active assistant session.

We explicitly reject creating separate "sidebar assistant" and "main assistant" sessions because:

- it would fragment conversation history,
- it would make context continuity harder for users,
- it would introduce avoidable session-management complexity in the frontend and backend.

The right sidebar becomes the compact assistant surface. The center `Assistant` activity becomes the focused assistant surface for the same session.

### 2. Assistant Workspace Placement

We will add an `Assistant` activity to the Activity Bar and support a center tab containing:

- a large chronological chat transcript,
- the existing rich assistant output rendering behavior,
- an assistant input pane docked at the bottom,
- a vertical splitter between transcript and input.

This center assistant tab is a different presentation of the existing assistant session, not a different assistant runtime.

### 3. Responsive Assistant Controls

Assistant titlebar controls will no longer rely on a single-row layout.

At narrower widths, controls will reorganize into a wrapped or stacked layout with these priorities:

- prompt entry and send action must remain usable,
- provider/model/session selectors must remain legible,
- compact and secondary actions may wrap onto additional rows,
- no selector may collapse to unreadable text solely because the pane is narrow.

### 4. Light Theme Direction

We will replace the current ad hoc light-theme accent treatment with a restrained productivity-oriented light palette:

- neutral surfaces for most workspace chrome,
- darker low-emphasis text tokens than the current skin,
- a blue-centered accent used sparingly for active states and interactive emphasis,
- stronger contrast for metadata labels such as `Scenario` and `Filter`.

This direction is informed by current public design-system guidance that favors neutral surfaces and selective accent usage for dense productivity UIs.

Reference material:

- Atlassian Design System color foundations and token usage: `https://atlassian.design/foundations/color`
- Carbon light-theme color usage: `https://v10.carbondesignsystem.com/guidelines/color/usage/`

This ADR does not lock exact final hex values, but it does lock the design direction and acceptance criteria for contrast and usage.

### 5. Moon Trek Activity Rename

The Activity Bar entry and corresponding panel label currently called `Moon Trek Layers` will be renamed to `Map Layers`.

This is a UI naming change only. It does not rename backend APIs or internal Trek service concepts.

### 6. Notebook Opening Policy

We will change notebook opening behavior from "open Marimo home/shell" to "open a notebook document directly."

Two notebook entry paths will be supported:

- `Open in Marimo` from scenario explorer controls creates a new uniquely named notebook for the active scenario and opens it directly in a center tab.
- `Open as Notebook` on eligible scenario files attempts to open a specific file directly in a notebook tab.

### 7. Python File Handling Policy

Not every Python file under a scenario is a valid Marimo notebook. Therefore:

- the UI must not assume every `*.py` file is a notebook,
- the app may offer `Open as Notebook` for `*.py` files,
- the open attempt becomes the deciding action,
- successful opens may be cached as notebook-capable,
- failed opens may be cached as non-notebook-capable,
- cached classification is advisory and may be invalidated if the file changes.

This keeps the UX flexible without baking in brittle file-type assumptions.

### 8. Iframe Policy

Notebook tabs will continue to use iframes in the center workspace unless a later ADR replaces that approach.

This preserves the current Marimo integration boundary and keeps this work additive and reversible.

## Detailed Implementation Plan

## Phase 0: Guardrails and UX Contract

- [ ] Confirm the touched frontend files and backend endpoints for this workstream.
- [ ] Add a short implementation note in the working task describing out-of-scope items for each phase.
- [ ] Preserve the single-map invariant and existing dockable workspace behavior.
- [ ] Preserve assistant rich-output rendering behavior across sidebar and center assistant views.
- [ ] Preserve the current backend-owned assistant session lifecycle and Marimo process ownership.

## Phase 1: Light Theme Token Cleanup and Readability Fixes

- [ ] Introduce or normalize light-theme CSS variables for:
  - [ ] workspace surfaces,
  - [ ] panel chrome,
  - [ ] muted labels,
  - [ ] normal text,
  - [ ] active accents,
  - [ ] focus/selection styling.
- [ ] Replace the current light-theme accent usage with a restrained blue-centered palette and stronger neutral hierarchy.
- [ ] Darken low-contrast light-theme labels used in explorer controls, including `Scenario` and `Filter`.
- [ ] Audit other light-theme metadata labels in the same component family for the same contrast issue.
- [ ] Verify hover, selected, active, and keyboard-focus states remain visually distinct in light theme.
- [ ] Add or update frontend tests that assert the correct light-theme class or token hooks where practical.

## Phase 2: Scenario Explorer Layout and Table Polish

- [ ] Fix the file-list header alignment bug so `Name`, `Type`, `Created`, `Size`, and `Notes` headers do not overlap the first data row.
- [ ] Verify column-header positioning at common desktop sizes and after toggling visible columns.
- [ ] Review sticky-header, padding, line-height, and row-height interactions in the explorer table CSS.
- [ ] Rename the `Moon Trek Layers` activity/panel label to `Map Layers`.
- [ ] Update any tests or snapshots that assert the old label.

## Phase 3: Responsive Assistant Sidebar Controls

- [ ] Refactor the assistant titlebar control layout so selectors and buttons wrap or stack when width is constrained.
- [ ] Ensure provider, model, thinking, access-mode, and session selectors remain readable at narrow widths.
- [ ] Prevent select text clipping that makes the current combobox values unreadable.
- [ ] Keep `New Session`, `Compact`, and `Send` accessible without horizontal overflow.
- [ ] Verify behavior at representative widths for:
  - [ ] wide desktop sidebar,
  - [ ] narrow sidebar,
  - [ ] center-tab assistant layout.
- [ ] Add frontend tests for responsive assistant control rendering where practical.

## Phase 4: Focused Assistant Center Activity

- [ ] Add a new `Assistant` activity-bar entry and corresponding workspace component.
- [ ] Implement a center-tab assistant view that contains:
  - [ ] scrollable message history on top,
  - [ ] assistant input pane on bottom,
  - [ ] draggable splitter between them.
- [ ] Reuse the existing active assistant session state instead of creating a separate session model.
- [ ] Reuse the existing rich response rendering components or factor them into shared assistant-presenter building blocks.
- [ ] Ensure the center assistant tab can be selected, re-opened, and restored by workspace layout persistence.
- [ ] Keep the existing compact right-sidebar assistant workflow available.
- [ ] Add a clear affordance to move focus between compact sidebar usage and center-tab usage if needed.
- [ ] Update workspace layout tests to cover the new assistant activity and default placement.

## Phase 5: Notebook Open/Create Backend Contract

- [ ] Review the current Marimo API surface and identify the minimal additive contract needed for direct notebook opening.
- [ ] Add a backend endpoint or extend an existing endpoint so the frontend can request:
  - [ ] launch-or-reuse Marimo for a scenario,
  - [ ] create a uniquely named notebook file for that scenario,
  - [ ] return the direct notebook URL or notebook-open metadata needed by the iframe tab.
- [ ] Ensure unique notebook naming is deterministic and collision-safe.
- [ ] Keep scenario-root path safety checks and normalized-path enforcement intact.
- [ ] Keep Marimo process ownership in the backend service layer.
- [ ] Define structured error responses for:
  - [ ] notebook creation failure,
  - [ ] invalid scenario root,
  - [ ] invalid notebook target path,
  - [ ] failed notebook-open attempt for a non-notebook Python file.
- [ ] Add backend tests for the new or extended notebook-opening contract.

## Phase 6: Notebook Open/Create Frontend Flow

- [ ] Change `Open in Marimo` so it creates a new unique notebook and opens it as a center tab instead of opening the Marimo home shell in a new browser tab.
- [ ] Keep a fallback action available for external opening only when direct in-app open fails.
- [ ] Add a scenario-file context action for `Open as Notebook` on eligible files.
- [ ] Route successful notebook opens into center tabs that reuse the existing `NotebookPane` iframe model.
- [ ] Preserve notebook tab titles using meaningful relative paths or generated notebook names.
- [ ] Persist open notebook tab metadata in workspace state as appropriate.
- [ ] Update frontend tests for notebook tab creation and Marimo URL handling.

## Phase 7: Python File Classification Cache

- [ ] Add a lightweight frontend or backend-backed cache for notebook-open capability by file path plus file-change signal.
- [ ] Mark successful `*.py` notebook opens as notebook-capable.
- [ ] Mark failed `*.py` notebook opens as not notebook-capable, with an invalidation rule when the file changes.
- [ ] Ensure the cache is advisory only and never bypasses server-side validation.
- [ ] Decide whether the cache belongs in browser state, scenario metadata, or another backend-owned store, and document that decision before implementation.

## Phase 8: Verification and Rollout

- [ ] Run frontend unit tests for touched assistant, explorer, workspace, and Marimo service behavior.
- [ ] Run backend tests for touched Marimo/notebook routes and path-validation behavior.
- [ ] Perform manual verification in both dark and light skins.
- [ ] Perform manual verification at typical laptop widths and larger desktop widths.
- [ ] Capture manual verification evidence for:
  - [ ] responsive assistant controls,
  - [ ] readable light-theme labels,
  - [ ] corrected explorer header alignment,
  - [ ] `Map Layers` rename,
  - [ ] center assistant activity behavior,
  - [ ] unique notebook creation,
  - [ ] opening a known Marimo notebook file,
  - [ ] failed opening of a non-notebook `*.py` file.

## Acceptance Criteria

- The assistant selectors and controls remain legible and usable when the assistant pane is narrow.
- The light skin uses darker readable labels for `Scenario`, `Filter`, and similar metadata labels.
- The light skin uses a more disciplined accent strategy based on neutral surfaces plus restrained blue emphasis.
- The scenario explorer file-list header row no longer overlaps the first data row.
- The activity/panel label presented to users is `Map Layers`.
- Users can open a focused center `Assistant` tab that shares the same assistant session as the compact sidebar assistant.
- The center `Assistant` tab shows chat history above and input below with a splitter between them.
- `Open in Marimo` creates a uniquely named notebook and opens it directly in a center tab.
- Users can attempt `Open as Notebook` on `*.py` files from the scenario explorer.
- Non-notebook Python files fail gracefully without corrupting session or layout state.
- Notebook tabs continue to use the current iframe integration model.

## Out of Scope

- Replacing Marimo with a different notebook technology.
- Replacing iframe-based notebook rendering.
- Redesigning assistant backend execution, session storage, or provider orchestration.
- Renaming backend Moon Trek APIs or changing Trek data-model terminology internally.
- Mobile-specific workspace redesign.

## Risks and Rollback

### Primary Risks

- Responsive assistant controls may become visually correct but semantically awkward if control priority is not defined carefully.
- The new center assistant activity may duplicate logic unless the existing assistant panes are properly refactored into shared building blocks.
- Notebook create/open flow may require backend contract changes that surface edge cases around path validation and Marimo process reuse.
- Python-file notebook capability caching may become stale if invalidation is too weak.

### Rollback Strategy

- Keep the changes phased and additive.
- If the center assistant activity proves unstable, retain the right-sidebar assistant as the primary supported workflow.
- If direct notebook creation/opening is unstable, temporarily preserve an external-open fallback while the backend contract is corrected.
- If notebook-capability caching proves brittle, disable the cache and rely on per-open validation until a better invalidation strategy is implemented.

## Notes for Implementation

This ADR intentionally separates:

- small CSS and labeling fixes,
- assistant workspace composition,
- notebook contract changes.

That separation is required to keep the work vertically sliceable, testable, and reversible.
