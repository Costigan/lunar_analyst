# Libro-Style Prompt Cell Emulation in Marimo (No-Fork Plan v1)

## 1. Goal

Approximate Libro's "prompt cell" workflow in Marimo without forking Marimo:

1. User interacts with an in-notebook prompt UI.
2. An LLM proposes one or more notebook cells (markdown/python/sql).
3. User previews and approves.
4. Approved cells are inserted below the prompt cell.
5. Marimo reloads and displays the new cells.

This plan prefers additive, reversible changes inside the Lunar Analyst codebase and treats Marimo as a pinned third-party dependency.

## 2. Non-Goals (v1)

- No Marimo fork.
- No first-class new Marimo editor cell type.
- No custom Marimo frontend plugin/editor extension.
- No automatic execution of generated code by default.
- No direct notebook-side DB mutation (FastAPI remains authoritative).
- No native compute / `pythonnet` bridge changes.

## 3. Constraints and Invariants

These must hold throughout implementation:

- Use Python 3.11 environment (`D:\projects\env_311`) for local commands and tests.
- FastAPI remains the authoritative control plane for scenario state and notebook-facing APIs.
- No duplicate compute-contract layers; reuse existing backend job/API contracts where possible.
- Filesystem safety: normalize paths and restrict notebook writes to the target notebook file under an allowed root.
- Prefer no-fork Marimo customization (CSS + notebook helper + file rewrite).
- Changes should be small, testable, and reversible.

## 4. High-Level Architecture (No Fork)

### 4.1 Components

- `backend/api`:
  - New endpoint to transform a prompt into a structured "cell plan".
- `backend/notebook`:
  - Notebook helper UI for prompt interaction and preview.
  - Notebook mutation utility to insert generated cells into a Marimo `.py` notebook.
- `docs` / config:
  - Marimo custom CSS to narrow or hide built-in AI panel chrome.
- Tests:
  - Contract tests for API payload shape.
  - Worker/unit tests for notebook mutation and helper behavior.

### 4.2 Data Flow

1. Prompt cell UI captures prompt + context (scenario id/path, optional notebook context).
2. Prompt cell calls Lunar Analyst FastAPI endpoint.
3. Backend returns a typed `CellPlan` response (markdown/python/sql cells + metadata).
4. Prompt cell displays preview.
5. User approves insert.
6. Notebook mutator rewrites current Marimo notebook `.py` using Marimo parse/codegen helpers.
7. Marimo file watcher / reload refreshes the notebook.

## 5. Proposed Deliverables (v1)

- `docs/LIBRO_EMULATION_1.md` (this plan)
- New backend API route for prompt-to-cell generation (scaffold + stubbed provider)
- Structured backend contract models for generated cells
- Notebook helper `la_prompt_cell(...)` prototype
- Notebook mutator utility for safe cell insertion
- Unit/contract tests for mutation and API contract
- Optional CSS to shrink/hide Marimo LLM panel (version-pinned to Marimo `0.17.3`)

## 6. Implementation Phases and Task Breakdown

Each task is scoped to about one hour of implementation or review time.

### Phase 0: Design and Contract Freeze (Documentation + Interfaces)

#### Task 0.1: Define Prompt-to-Cell Contract

- Goal:
  - Define the backend response schema for generated notebook cells before UI implementation.
- Files allowed to change:
  - `backend/contracts/` (new model file if needed)
  - `backend/api/routers/v1.py` (route registration if contract references are exported)
  - `docs/LIBRO_EMULATION_1.md`
- Deliverables:
  - `PromptCellGenerateRequest`
  - `PromptCellGenerateResponse`
  - `GeneratedNotebookCell` (`kind`, `content`, optional metadata)
- Acceptance criteria:
  - Contract supports at least `markdown`, `python`, `sql`.
  - Contract supports preview-first UX (no insertion side effects in generate endpoint).
  - Contract includes room for safety/provenance metadata.
- Required tests:
  - Contract serialization/deserialization unit tests (if repo pattern exists).
  - OpenAPI export/contract snapshot update if schema is exposed.
- Risks:
  - Over-designing agent/tool fields before real usage.
- Rollback:
  - Keep only minimal fields (`kind`, `content`) and add metadata later.

#### Task 0.2: Define Notebook Mutation API (Internal Utility Interface)

- Goal:
  - Freeze the Python API for notebook insertion before wiring UI.
- Files allowed to change:
  - `backend/notebook/` (new module, likely `prompt_cells.py` and/or `notebook_mutation.py`)
  - `docs/LIBRO_EMULATION_1.md`
- Deliverables:
  - Function signatures for:
    - locate prompt cell anchor
    - insert generated cells after anchor
    - write notebook safely
- Acceptance criteria:
  - API is independent of Marimo private websocket internals.
  - API uses notebook file path + anchor marker, not runtime-only state.
- Required tests:
  - Signature-level unit tests not needed yet; test plan documented.
- Risks:
  - Binding to unstable Marimo internals.
- Rollback:
  - Fall back to append-only insertion if anchoring is unstable.

### Phase 1: Backend Prompt Generation Slice (Preview Only)

#### Task 1.1: Add Prompt Generation Route (Stubbed)

- Goal:
  - Create an API route that accepts prompt requests and returns deterministic mock/generated cell plans.
- Files allowed to change:
  - `backend/api/routers/lunar_analyst.py` and/or `backend/api/routers/v1.py`
  - `backend/api/app.py` (if router registration is needed)
  - `backend/contracts/...` (new/updated response models)
- Deliverables:
  - `POST` route for prompt-to-cell generation (preview only, no notebook writes)
  - Error envelope behavior consistent with existing APIs
- Acceptance criteria:
  - Endpoint returns valid `GeneratedNotebookCell` list.
  - Endpoint is deterministic for tests when provider is unavailable.
  - No direct filesystem mutation on generate.
- Required tests:
  - Contract test under `backend/tests/contract/` for route shape/status/error response.
- Risks:
  - Premature LLM provider integration complexity.
- Rollback:
  - Keep route stubbed with template responses while UI and mutation are built.

#### Task 1.2: Add Backend Prompt Planner Service (No External LLM Required)

- Goal:
  - Isolate prompt-to-cell planning logic behind a service boundary for future LLM integration.
- Files allowed to change:
  - `backend/services/` (new module, e.g. `prompt_cell_planner.py`)
  - Route wiring files from Task 1.1
- Deliverables:
  - Planner service that maps prompts to structured cells (template/mock implementation)
- Acceptance criteria:
  - Route is thin; planner logic is testable in isolation.
  - Planner can emit `markdown + python` bundle examples.
- Required tests:
  - Unit tests for planner in `backend/tests/worker/` or `backend/tests/integration/` (repo fit dependent)
- Risks:
  - Service shape too coupled to one provider.
- Rollback:
  - Inline planner logic in route temporarily, keep contract unchanged.

### Phase 2: Notebook Mutator (Core No-Fork Mechanism)

#### Task 2.1: Implement Notebook Anchor Marker Convention

- Goal:
  - Define and parse a stable marker embedded in the prompt cell source.
- Files allowed to change:
  - `backend/notebook/` (new module or `notebook_helper.py`)
  - `backend/tests/worker/test_notebook_helper.py` or new test file
- Deliverables:
  - Marker convention, e.g. `PROMPT_CELL_ID = "..."` or comment tag
  - Utility to extract/find marker in parsed cell source
- Acceptance criteria:
  - Anchor can be found after notebook roundtrip save/reload.
  - Marker syntax is valid Python and unobtrusive.
- Required tests:
  - Unit test for marker detection across representative cell source strings.
- Risks:
  - Marker collisions or accidental edits by user.
- Rollback:
  - Switch to append-only insertion until a stronger anchor scheme is added.

#### Task 2.2: Implement Marimo Notebook Parse/Insert/Codegen Utility

- Goal:
  - Insert generated cells into a Marimo `.py` notebook using Marimo conversion/codegen helpers.
- Files allowed to change:
  - `backend/notebook/` (new `marimo_prompt_mutator.py` or similar)
  - `backend/tests/worker/` (new tests)
- Deliverables:
  - Utility that:
    - reads notebook source
    - parses to IR / notebook structure
    - inserts cells after anchor
    - regenerates `.py`
- Acceptance criteria:
  - Generated output is a valid Marimo notebook file.
  - Existing notebook cells remain semantically intact.
  - Supports `markdown`, `python`, and optionally `sql`.
- Required tests:
  - Failing-before/passing-after regression test for insertion order
  - Roundtrip test with a sample `.mo.py` notebook fixture
  - Test for anchor-not-found fallback behavior
- Risks:
  - Marimo parser/codegen version changes
  - Formatting churn causing noisy diffs
- Rollback:
  - Switch to append-only insertion using simpler code path (still parse/codegen-based)

#### Task 2.3: Safe File Write + Conflict Detection

- Goal:
  - Prevent accidental overwrites when notebook changes between preview and insert.
- Files allowed to change:
  - `backend/notebook/` mutator module
  - `backend/tests/worker/` test file for mutator
- Deliverables:
  - Hash/checksum or source-version check before write
  - Atomic write strategy (temp file + replace) if feasible on Windows
- Acceptance criteria:
  - Insert fails gracefully on stale source mismatch.
  - Error message is actionable ("regenerate/retry").
- Required tests:
  - Conflict detection test
  - Atomic write success/failure path test (as practical)
- Risks:
  - Windows file locking behavior during active Marimo session
- Rollback:
  - Use simpler write path with explicit warning and retry loop

### Phase 3: Marimo Prompt Cell Helper (Preview + Insert UX)

#### Task 3.1: Add `la_prompt_cell()` Helper (Preview-Only UI)

- Goal:
  - Create a notebook helper that renders prompt UI and calls the backend generate endpoint.
- Files allowed to change:
  - `backend/notebook/notebook_helper.py`
  - `backend/notebook/client.py` (if API client method needed)
  - `backend/tests/worker/test_notebook_helper.py`
- Deliverables:
  - `la_prompt_cell(...)` helper with:
    - prompt input
    - generate button
    - preview area
    - error display
- Acceptance criteria:
  - Works in a Marimo notebook without custom frontend build.
  - Displays backend-generated markdown/code previews.
  - No insertion side effects yet.
- Required tests:
  - Unit tests for helper request payload construction (mock backend client)
  - Manual verification evidence in notebook example
- Risks:
  - Marimo UI state handling complexity for multi-step interactions
- Rollback:
  - Split into smaller helper functions (`build_ui`, `generate_preview`)

#### Task 3.2: Wire Approved Insert Action to Notebook Mutator

- Goal:
  - Add "Insert cells" action to `la_prompt_cell()` and call the mutator.
- Files allowed to change:
  - `backend/notebook/notebook_helper.py`
  - `backend/notebook/` mutator module
  - `backend/tests/worker/test_notebook_helper.py`
- Deliverables:
  - Approve/insert button
  - Provenance metadata/header comments in inserted cells
  - User-facing success/failure status
- Acceptance criteria:
  - Inserting approved preview writes new cells below the prompt cell anchor.
  - Generated Python cells default to safe mode (disabled/commented warning in v1).
- Required tests:
  - Helper + mutator integration unit test (mock backend response)
  - Regression test for repeated inserts from same prompt cell
- Risks:
  - Reload race/UX flicker
- Rollback:
  - Keep preview-only mode while mutator hardens

#### Task 3.3: Example Notebook and Manual Verification Workflow

- Goal:
  - Provide a simple example notebook demonstrating the prompt-cell flow.
- Files allowed to change:
  - `backend/notebook/examples/` (new example `.mo.py`)
  - `docs/HOW_TO_MANUALLY_TEST.md` or new doc note if needed
- Deliverables:
  - Example notebook with one prompt cell scaffold and sample usage
- Acceptance criteria:
  - Developer can run and verify end-to-end locally.
- Required tests:
  - Manual verification checklist only (documented)
- Risks:
  - Example drifts from helper API
- Rollback:
  - Keep example minimal and pinned to helper signature

### Phase 4: Marimo AI Panel UX Adjustment (No Fork, CSS)

#### Task 4.1: Add Version-Pinned CSS to Shrink or Hide Built-In AI Panel

- Goal:
  - Reduce UI conflict with custom prompt cell by shrinking/hiding Marimo's built-in AI panel.
- Files allowed to change:
  - Marimo config in repo (wherever team stores local/project marimo config)
  - `docs/` (selector notes and version pinning guidance)
  - Optional CSS file under project config/docs assets
- Deliverables:
  - CSS selectors for Marimo `0.17.3` panel chrome
  - Documented fallback to hide entirely if shrinking is unstable
- Acceptance criteria:
  - Custom prompt-cell UX is not visually blocked by Marimo AI panel.
  - Behavior is documented as version-sensitive.
- Required tests:
  - Manual smoke check only (UI)
- Risks:
  - Marimo DOM changes on upgrade break selectors
- Rollback:
  - Remove CSS and accept built-in panel coexistence

### Phase 5: Contract/Test Hardening and Observability

#### Task 5.1: Add Contract Tests for Prompt Generation Endpoint

- Goal:
  - Lock response shape and error handling before real LLM integration.
- Files allowed to change:
  - `backend/tests/contract/` (new test file)
  - Contract export tools only if schemas changed
- Deliverables:
  - Contract tests covering success/error payloads
- Acceptance criteria:
  - OpenAPI/contract checks pass for new endpoint
  - Invalid request inputs produce stable error responses
- Required tests:
  - `backend/tests/contract/...`
  - OpenAPI export + contract validation per repo conventions
- Risks:
  - Test brittleness if endpoint is still evolving rapidly
- Rollback:
  - Mark endpoint experimental and relax strict fields temporarily

#### Task 5.2: Add Logging and Provenance for Insert Operations

- Goal:
  - Provide observability and auditability for generated notebook insertions.
- Files allowed to change:
  - `backend/notebook/` helper/mutator modules
  - `backend/api` route/service modules (request IDs, structured logs)
- Deliverables:
  - Structured logs for generate + insert actions
  - Provenance header in inserted cells (prompt id, timestamp, source)
- Acceptance criteria:
  - Failures can be diagnosed from logs.
  - Inserted cells are traceable to a prompt operation.
- Required tests:
  - Unit tests for provenance formatting (if helper function added)
- Risks:
  - Logging sensitive prompt content unintentionally
- Rollback:
  - Log metadata only (IDs, counts, timings) and omit content

## 7. Concrete v1 File Targets (Recommended)

These are suggested paths consistent with the current repo layout.

- Backend API / contracts
  - `backend/api/routers/lunar_analyst.py`
  - `backend/api/routers/v1.py`
  - `backend/contracts/` (new file for prompt-cell schemas)
  - `backend/services/` (new planner service)
- Notebook helper / mutation
  - `backend/notebook/notebook_helper.py`
  - `backend/notebook/client.py`
  - `backend/notebook/` (new mutator module, e.g. `marimo_prompt_mutator.py`)
  - `backend/notebook/examples/` (new example notebook)
- Tests
  - `backend/tests/contract/` (new prompt-cell endpoint contract test)
  - `backend/tests/worker/` (new mutator/helper tests)
- Docs / config
  - `docs/LIBRO_EMULATION_1.md`
  - `docs/HOW_TO_MANUALLY_TEST.md` (optional update)
  - project Marimo CSS/config file (path depends on current team practice)

## 8. Recommended Task Order (Execution Sequence)

1. Phase 0 (contract + mutator interface)
2. Phase 1 (backend route + stub planner)
3. Phase 2 (mutator core + tests)
4. Phase 3.1 (preview UI helper)
5. Phase 3.2 (insert action wiring)
6. Phase 3.3 (example notebook/manual verification)
7. Phase 4 (CSS panel adjustment)
8. Phase 5 (contract hardening + observability)

This sequence minimizes risk by proving file mutation before UI polish.

## 9. Acceptance Criteria for the Full v1 Slice

- A Marimo notebook can render a custom prompt UI using `backend/notebook/notebook_helper.py`.
- Prompt UI can request a structured cell plan from FastAPI.
- User can preview proposed markdown/python cells before insertion.
- User can insert approved cells below the prompt cell without forking Marimo.
- Inserted cells preserve notebook validity and reload correctly in Marimo.
- New endpoint and notebook mutation behavior have tests.
- Any Marimo UI CSS customization is explicitly documented as version-pinned.

## 10. Risks, Tradeoffs, and Escalation Triggers

### 10.1 Known Tradeoffs (Accepted for No-Fork v1)

- Prompt cell is an emulation (normal Marimo code cell + UI), not a native editor cell type.
- Marimo panel customization via CSS is brittle across Marimo upgrades.
- Reload-based insertion is less seamless than a native frontend plugin.

### 10.2 Escalate / Re-evaluate If Any Occur

- Marimo codegen roundtrip corrupts notebooks or creates unstable diffs.
- Reload behavior causes unacceptable UX disruption.
- DOM/CSS hooks for Marimo panel become too fragile even with version pinning.
- Team requires true editor-native prompt cells (toolbar, keyboard, inline insertion without reload).

If any of the above happens, open a follow-up decision doc comparing:

- Continue no-fork with reduced UX goals
- Local patch maintenance of Marimo
- Full Marimo fork
- Revisit Libro/JupyterLab extension path

## 11. Rollback Plan (End-to-End)

Rollback should preserve notebook and backend stability at every step:

1. Disable prompt-cell insert action (preview-only mode remains).
2. Disable backend prompt endpoint registration.
3. Remove Marimo CSS override (restore default panel UI).
4. Keep tests/docs for future reattempt if low maintenance cost.

Because the plan is additive and no DB migrations are involved, rollback is primarily route/helper disablement.

## 12. Suggested First Implementation Ticket (Vertical Slice)

Title:
- "Add preview-only `la_prompt_cell()` with stubbed FastAPI prompt-to-cell endpoint"

Scope:
- Backend route + minimal contract + notebook helper preview UI only

Out of scope:
- Notebook mutation/insertion
- CSS panel changes
- Real LLM provider integration

Why first:
- Validates UX and contracts with minimal risk before file mutation complexity.

