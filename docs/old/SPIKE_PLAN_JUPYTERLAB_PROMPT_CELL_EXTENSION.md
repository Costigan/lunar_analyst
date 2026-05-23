# Spike Plan: JupyterLab Prompt Cell Extension (1 Week)

## 1. Goal
Validate a JupyterLab-based "prompt cell" workflow using standard notebook cell types plus metadata and extension commands that generate executable code cells.

## 2. Out of Scope
- Core Jupyter notebook format changes.
- Organization-wide migration from marimo to JupyterLab.
- Production packaging/deployment changes (including Tauri bundling changes).

## 3. Allowed Files To Change
- `backend/notebook/jupyter_runner.py`
- `backend/api/routes/notebook_runtime.py`
- `backend/api/routes/llm_codegen.py`
- `backend/tests/integration/test_jupyter_runtime.py`
- `notebook_extensions/lunar_prompt_cell/package.json`
- `notebook_extensions/lunar_prompt_cell/src/index.ts`
- `notebook_extensions/lunar_prompt_cell/src/commands.ts`
- `docs/spikes/jupyter_prompt_cell_spike.md`

## 4. Constraints and Invariants
- Notebook runtime remains a separate process from FastAPI.
- Use canonical Jupyter cell types only (`code`, `markdown`, `raw`); prompt semantics live in metadata.
- LLM credentials remain server-side; extension calls FastAPI, not provider APIs directly.
- Notebook runtime does not directly mutate `scenario.db`; writes go through FastAPI APIs.

## 5. 5-Day Work Breakdown
1. Day 1: Add feature-flagged JupyterLab runtime launcher/stopper with health checks.
2. Day 2: Implement FastAPI `llm_codegen` endpoint returning structured generated cell payloads.
3. Day 3: Build extension command to mark/unmark a cell as prompt cell via metadata.
4. Day 4: Build extension command to execute prompt cell and insert generated code cells below it.
5. Day 5: Add logging/error handling, smoke tests, and spike findings report.

## 6. Acceptance Criteria
- Runtime lifecycle works (`start`, `health`, `stop`) for scenario-scoped sessions.
- Prompt-cell metadata can be toggled in notebook UI.
- Running a prompt cell inserts generated code cell(s) below source cell.
- Inserted cells can execute and render output.
- All LLM requests route through FastAPI API contract.
- Feature can be disabled cleanly by flag.

## 7. Required Tests
- Integration test for Jupyter runtime lifecycle.
- API contract test for `llm_codegen` success and error payloads.
- Extension smoke test for metadata toggle and cell insertion behavior.
- Manual verification evidence for end-to-end prompt -> generated cells -> executed result.

## 8. Risks
- JupyterLab extension build/tooling overhead in current repo structure.
- UX edge cases around selection/focus and repeated prompt execution.
- Additional effort needed for production-grade command palette/toolbar polish.

## 9. Rollback
- Keep Jupyter runtime and extension path feature-flagged.
- Disable flag to return to current notebook behavior.
- Notebook files remain standard `.ipynb`; metadata can be ignored safely if feature is removed.

