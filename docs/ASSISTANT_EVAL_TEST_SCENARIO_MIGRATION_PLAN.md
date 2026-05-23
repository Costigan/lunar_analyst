# Assistant Eval Migration Plan: `mons-malapert` -> `test_scenario`

## 1. Goal

Standardize assistant eval tests to use `test_scenario` as the default baseline scenario instead of `mons-malapert`, with deterministic per-test setup and cleanup so tests do not depend on mutable scenario state.

This plan is additive and keeps a clear extension path for adding more scenario baselines later.

## 2. Scope

In scope:
- Backend assistant eval pytest suites:
  - `backend/tests/evals/test_assistant_domain.py`
  - `backend/tests/evals/test_assistant_functional.py`
  - `backend/tests/evals/conftest.py`
- Eval runner/default wiring:
  - `backend/evals/assistant/run_benchmark.py` (if default selector behavior is needed there)
  - `backend/evals/assistant/leaderboard.py` / `leaderboard_ui.py` (only if they hardcode or imply `mons-malapert`)
- New reusable test setup helpers for scenario file preparation and teardown.

Out of scope:
- Production runtime scenario selection behavior for normal app use.
- Changing non-eval tests unless they are directly impacted.
- Editing scenario science content itself (DEM/horizon generation logic, etc.).

## 3. Requirements

1. Default eval baseline scenario is `test_scenario`.
2. Each test runs against an isolated clone of that baseline (already the current pattern), plus deterministic per-test file setup.
3. Tests that need absent files must explicitly remove them in setup.
4. Tests that need present files must explicitly create/seed them in setup (not rely on baseline incidental contents).
5. Cleanup returns cloned scenario to a known post-test state (at least for files created/modified during test).
6. Design supports future multi-scenario runs without refactoring core fixtures.

## 4. Design Decisions

## 4.1 Single source of truth for baseline selector
- Introduce one central baseline selector constant/config in `conftest.py`, for example:
  - `DEFAULT_EVAL_SCENARIO_SELECTOR = "test_scenario"`
- All eval test calls to `isolated_scenario(...)` should use that constant (or omit explicit selector and rely on fixture default).

## 4.2 Deterministic fixture seeding
- Add helper utilities in `conftest.py` to guarantee required artifacts exist before test actions, e.g.:
  - `_ensure_raster_exists(...)`
  - `_ensure_csv_exists(...)`
  - `_ensure_geojson_exists(...)`
- Prefer deterministic creation from `dem.tif` or simple synthetic payloads where possible.
- Do not depend on pre-existing files in the baseline scenario except core seed files (e.g., `dem.tif`).

## 4.3 Per-test cleanup
- Add a per-test cleanup registry in fixture context to track generated/modified relative paths.
- Register finalizer to delete generated files and optionally restore modified files from local backups in clone scope.
- Keep cleanup within clone root only.

## 4.4 Future extension for additional scenarios
- Add optional case-level scenario mapping table in `conftest.py`, e.g.:
  - `CASE_SCENARIO_SELECTOR_OVERRIDES = {"future_case_id": "another_scenario"}`
- Resolver order:
  1. CLI `--scenario` (force override)
  2. Case override map
  3. Default selector (`test_scenario`)

## 5. Implementation Plan

## Phase A: Baseline selector switch

Files:
- `backend/tests/evals/conftest.py`
- `backend/tests/evals/test_assistant_domain.py`
- `backend/tests/evals/test_assistant_functional.py`

Changes:
1. Define `DEFAULT_EVAL_SCENARIO_SELECTOR = "test_scenario"` in `conftest.py`.
2. Update `isolated_scenario` resolution logic to use default selector when tests pass legacy `scn_mons-malapert`.
3. Replace hardcoded `"scn_mons-malapert"` in eval tests with the shared default selector reference (or remove selector argument and let fixture provide default).

Acceptance:
- Running eval suite without `--scenario` uses `test_scenario`.
- No references to `"scn_mons-malapert"` remain in eval test setup.

## Phase B: Deterministic per-test setup helpers

Files:
- `backend/tests/evals/conftest.py`
- `backend/tests/evals/test_assistant_domain.py`
- `backend/tests/evals/test_assistant_functional.py`

Changes:
1. Add helper APIs in `conftest.py` for test data setup:
  - ensure/remove relative path helpers
  - synthetic artifact creators (minimal valid raster/csv/geojson as needed)
2. Update tests that currently assert baseline file presence (e.g., `landing_sites_slope5.tif`, `dem_percentiles.csv`, `landing_sites_summary.csv`) to call setup helpers before assertions.
3. Keep test-specific setup logic explicit in each case to preserve readability.

Acceptance:
- Cases no longer fail due to missing baseline artifacts in clone.
- Tests document their own prerequisites in setup code.

## Phase C: Post-test cleanup guarantees

Files:
- `backend/tests/evals/conftest.py`

Changes:
1. Add cleanup tracker object attached to scenario clone fixture.
2. Record generated files/dirs and delete them at test teardown.
3. For files intentionally removed in setup, no restoration required unless test mutates shared clone baseline unexpectedly.
4. Keep existing clone root deletion finalizer as final safety net.

Acceptance:
- Re-running same case repeatedly in same session yields identical setup state.
- No artifact leakage across test cases.

## Phase D: Optional runner/UI defaults alignment

Files (if needed based on current behavior):
- `backend/evals/assistant/leaderboard_ui.py`
- `backend/evals/assistant/run_benchmark.py`
- `backend/evals/assistant/leaderboard.py`

Changes:
1. Ensure default scenario selector shown/used by UI is `test_scenario`.
2. Keep CLI `--scenario` override behavior unchanged.
3. Update human-readable run logging so it clearly reports resolved scenario selector/id.

Acceptance:
- UI-triggered runs default to `test_scenario` unless user overrides.

## 6. Test Plan

Required checks:
1. `pytest backend/tests/evals/test_assistant_domain.py -q`
2. `pytest backend/tests/evals/test_assistant_functional.py -q`
3. `python -m backend.evals.assistant.run_benchmark --suite all --provider openai --model gpt-5.4 --scenario test_scenario`
4. Repeat run to verify determinism and cleanup stability.

Validation focus:
- No precondition failures for missing artifacts.
- No cross-test contamination.
- Scenario resolution logs report `test_scenario`.

## 7. Risks and Mitigations

Risk: `test_scenario` lacks core assets expected by many cases.
- Mitigation: Setup helpers synthesize missing derived artifacts from `dem.tif`; fail fast with explicit error if even core DEM is absent.

Risk: Cleanup deletes required files accidentally.
- Mitigation: Track only files created by setup/test helpers; enforce scenario-root path safety checks.

Risk: Hidden dependencies on legacy `mons-malapert` contents.
- Mitigation: Convert implicit dependencies into explicit per-test setup in Phase B.

## 8. Rollback Strategy

Rollback steps:
1. Revert default selector constant to previous value.
2. Re-enable legacy hardcoded selector in eval tests.
3. Keep setup helper framework (safe to retain) but disable new required artifact seeding paths.

Rollback trigger:
- If migration causes broad functional regressions not resolved within one iteration.

## 9. Definition of Done

- [ ] Default eval baseline scenario is `test_scenario`.
- [ ] Eval tests do not depend on incidental pre-existing derived files.
- [ ] Deterministic setup + cleanup implemented for scenario artifacts.
- [ ] Suite runs stable across repeated executions.
- [ ] Extension hook exists for future per-case scenario overrides.

