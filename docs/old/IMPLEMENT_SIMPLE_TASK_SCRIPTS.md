# Plan: Implicit Scenario Script Jobs (No Auto `.job.json` Files)

## Goal
Allow any top-level `*.py` file in a selected scenario root to appear and run as a notebook job in Jobs Manager, without creating a `.job.json` file. Keep explicit `.job.json` support unchanged.

## User Workflow Target
1. User iterates on a script in Marimo and saves `my_script.py` in scenario root.
2. User opens map GUI and selects that scenario.
3. Jobs Manager shows `my_script.py` as runnable immediately.
4. User runs it via existing notebook execution path.
5. Later, user may add a mature explicit `.job.json` definition.

## Constraints
- Do not auto-create any `.job.json` files.
- Keep existing `run(context)` support and script-mode runtime helper support.
- Keep path safety and subprocess isolation.
- Keep `GET /api/v1/job-definitions` fallback behavior:
  - no/invalid `scenario_id` => configured `search_roots` only
  - valid `scenario_id` => configured roots + scenario-local discovery

## Discovery Rules
- Scenario implicit discovery applies only when `scenario_id` resolves to an existing scenario.
- Scan only top-level files under `<scenario_root>` (non-recursive).
- Include files with `.py` suffix.
- Exclude known non-job/system files:
  - hidden names (prefix `.`)
  - names beginning with `_`
  - optionally `__init__.py`
- For each implicit script, synthesize in-memory metadata:
  - `job_id`: deterministic from filename stem (for example `script-<stem>`)
  - `title`: filename (or stem title-cased)
  - `notebook_path`: script path
  - `visibility`: `default`
  - `tags`: include `scenario-script`, `implicit`

## Precedence and Collision Policy
- Explicit `.job.json` definitions have precedence over implicit script definitions.
- If implicit/explicit collide on `job_id`, keep explicit and drop implicit.
- If multiple explicit definitions collide, keep current duplicate-error behavior.

## Implementation Steps
1. Update notebook discovery integration in `backend/api/dependencies.py`:
   - Extend `_discover_notebook_jobs(scenario_id)` to:
     - keep existing configured-root `.job.json` discovery
     - add implicit in-memory `DiscoveredNotebookJob` records from scenario-root `*.py`
   - Apply explicit-over-implicit merge logic.
2. Keep `backend/notebook/job_catalog.py` unchanged for explicit `.job.json` parsing/safety.
3. Ensure returned `JobDefinition` metadata indicates implicit entries clearly:
   - `job_type=notebook`
   - `handler_name=run_notebook_definition`
   - `route_path=/api/v1/jobs/run-notebook-definition`
   - tags include `implicit` for synthesized entries.
4. Confirm execution path is unchanged:
   - implicit jobs execute through existing subprocess runner using resolved script path.
5. Update docs:
   - `backend/README.md`: document implicit scenario-root script discovery behavior.
   - `docs/NEW_DESIGN.md`: add note about implicit script jobs (scenario-scoped).

## Tests
1. Contract test: implicit discovery appears with valid `scenario_id`.
2. Contract test: implicit discovery does not appear when `scenario_id` missing/invalid.
3. Contract test: explicit `.job.json` and implicit script collision => explicit wins.
4. Contract test: implicit-discovered script executes successfully via `/api/v1/jobs/run-notebook-definition`.
5. Regression test: existing explicit `.job.json` behavior remains unchanged.

## Out of Scope
- Recursive scenario script discovery.
- Auto-generation of `.job.json` files.
- New UI controls beyond existing Jobs Manager list behavior.
- New auth model changes.

## Risks and Mitigations
- Risk: accidental exposure of utility scripts as jobs.
  - Mitigation: top-level only + exclude hidden/underscore-prefixed files.
- Risk: ambiguous job IDs from filename collisions.
  - Mitigation: deterministic naming and explicit-over-implicit precedence.
- Risk: breaking existing discovery contract shape.
  - Mitigation: additive behavior only; preserve response schema.

## Acceptance Criteria
- A new top-level scenario script appears in Jobs Manager without `.job.json`.
- It runs through existing notebook subprocess pipeline and can register outputs.
- Explicit `.job.json` continues to work and overrides implicit metadata on collision.
- No files are auto-created by backend during discovery.
