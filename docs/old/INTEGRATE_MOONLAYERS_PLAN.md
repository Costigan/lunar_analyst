# Integrate `moonlayers` Into `lunar_analyst` (Development Home Move)

## Goal
Make `moonlayers` a first-class package developed inside this repository while leaving `D:\projects\moonlayers` in place. The external repo becomes a legacy mirror/reference; active development happens in `D:\projects\lunar_analyst`.

Developer workflow requirement: plain (non-Marimo) Python scripts must remain first-class, including VS Code run/debug support with `D:\projects\env_311\Scripts\python.exe`.

## Scope
- In scope:
  - Vendor `moonlayers` source into this repo as a local package.
  - Wire backend/job runtime to use the in-repo package reliably.
  - Add tests and manual checks proving notebook + map workflows still work.
  - Update docs for developer workflow and migration expectations.
- Out of scope:
  - Immediate deletion or archival of `D:\projects\moonlayers`.
  - Rewriting `moonlayers` architecture.
  - Publishing/releasing to PyPI.

## Constraints and Invariants
- Keep Python 3.11 baseline for this repo workflows.
- Preserve notebook-first UX (Marimo scripts/jobs).
- Keep FastAPI as authoritative control plane; notebooks/jobs communicate via API.
- No hidden path hacks required for normal development (prefer explicit local editable install).
- Changes must be reversible if integration causes regressions.

## Proposed Target Layout
- Add top-level folder: `moonlayers_pkg/`
- Inside it, preserve package project shape:
  - `moonlayers_pkg/pyproject.toml`
  - `moonlayers_pkg/moonlayers/...`
  - `moonlayers_pkg/src/...` (frontend sources)
  - `moonlayers_pkg/tests/...`
  - `moonlayers_pkg/package.json`, `vite.config.js`, etc.
- Keep `lunar_analyst` backend/app code separate from `moonlayers_pkg`.

Rationale:
- Clear ownership boundary.
- Easy editable install: `pip install -e .\moonlayers_pkg`
- Allows optional future subtree split without repo surgery.

## Phase Plan

### Phase A: Import and Freeze Baseline
1. Copy `D:\projects\moonlayers` into `moonlayers_pkg/` (excluding `.git`, `node_modules`, caches, build artifacts).
2. Record source commit hash from external repo in a note within this repo (`docs/INTEGRATE_MOONLAYERS_PLAN.md` update or a dedicated sync note).
3. Run baseline smoke checks from `env_311`:
   - `python -c "import moonlayers; print(moonlayers.__version__)"`
   - run a minimal widget import test.

Acceptance:
- `moonlayers` imports from in-repo path when installed editable.
- No dependency on `_moonlayers.pth` required.

### Phase B: Development Environment Wiring
1. Add/update bootstrap docs/scripts to install local package:
   - `pip install -e .\moonlayers_pkg`
2. Ensure frontend build steps are explicit:
   - `cd moonlayers_pkg && npm install && npm run build`
3. Add a single source-of-truth developer section in `docs/HOW_TO_MANUALLY_TEST.md` and/or `backend/README.md`.
4. Add/verify VS Code interpreter guidance for plain scripts:
   - interpreter must be `D:\projects\env_311\Scripts\python.exe`
   - a normal `.py` script in this repo can `import moonlayers` without extra path edits.

Acceptance:
- Fresh environment can build and import `moonlayers` without referencing `D:\projects\moonlayers`.
- VS Code can run/debug a plain `.py` script in this repo that imports `moonlayers` using `env_311`.

### Phase C: Runtime and Job Integration Validation
1. Validate notebook-task execution path can import and use `moonlayers` in worker/subprocess contexts.
2. Verify Scenario Manager + Jobs Manager flow with a script that:
   - writes a product artifact
   - optionally uses `MoonMap`/related helpers where applicable
   - posts product registration back to FastAPI.
3. Add/adjust contract tests around job execution environment assumptions (import paths, subprocess behavior).

Acceptance:
- Jobs launched by backend succeed using in-repo `moonlayers` package.
- No regression in current phase 4.8 script discovery/execution behavior.

### Phase D: Documentation and Team Workflow Cutover
1. Update:
   - `docs/NEW_DESIGN.md` (state `moonlayers` now co-developed in-repo)
   - `docs/HOW_TO_MANUALLY_TEST.md` (local setup/build/test flow)
   - `docs/PLAN.md` (add milestone/checklist item and completion criteria)
2. Add a short “external repo sync policy” note:
   - external `D:\projects\moonlayers` is reference-only unless explicitly syncing.
   - define one-way sync direction and cadence (if any).

Acceptance:
- Docs consistently point contributors to in-repo workflow.

## Testing Plan
- Unit/contract:
  - Existing backend contract tests still pass.
  - Add at least one test asserting notebook/script job can `import moonlayers`.
- Manual:
  1. Start backend and web GUI.
  2. Select scenario.
  3. Run a scenario-root script that imports `moonlayers` and produces/registers an output product.
  4. Confirm product appears in Scenario Explorer and can be layered in map UI.
  5. In VS Code, run/debug a plain `.py` script (outside Marimo) that imports `moonlayers` and logs `moonlayers.__file__`; verify it resolves to the in-repo `moonlayers_pkg` path.

## Risks and Mitigations
- Risk: duplicate package sources (`D:\projects\moonlayers` and in-repo copy) cause ambiguous imports.
  - Mitigation: explicitly document and verify `sys.path`/import origin during test steps.
- Risk: frontend asset build drift for `moonlayers` static bundle.
  - Mitigation: add deterministic build command and include generated assets policy.
- Risk: dependency conflicts between `lunar_analyst` and `moonlayers`.
  - Mitigation: pin/align key dependency versions in docs and bootstrap scripts.

## Rollback Plan
1. Re-point environment to external `D:\projects\moonlayers` (restore `.pth`/editable install).
2. Revert in-repo `moonlayers_pkg` wiring commits.
3. Keep docs note describing rollback state and known limitations.

## Implementation Checklist
- [x] Create `moonlayers_pkg/` by copying external project (sanitized).
- [x] Ensure editable install from `moonlayers_pkg` works in `env_311`.
- [x] Verify VS Code plain-script run/debug (`env_311`) can import `moonlayers` from in-repo package path.
- [x] Verify notebook job runtime imports in-repo `moonlayers`.
- [x] Add/adjust automated tests for import/runtime path.
- [x] Update `docs/NEW_DESIGN.md`.
- [x] Update `docs/HOW_TO_MANUALLY_TEST.md`.
- [x] Update `docs/PLAN.md` milestone/checklist.
- [x] Add external-repo sync policy note.

## Notes for Execution
- Keep changes incremental and reviewable (small commits by phase).
- Prefer additive adjustments to scripts/docs over broad refactors.
- Do not remove external repo assumptions until all acceptance checks pass.

## Execution Status (2026-02-17)
- Imported `D:\projects\moonlayers` into `moonlayers_pkg/` with sanitized copy rules.
- External source snapshot commit: `8b88ab41db5c5e4bc0872474b4c8a79dc144d177`.
- Runtime wired so notebook job subprocesses prepend `<repo_root>/moonlayers_pkg` to `PYTHONPATH` when present.
- Docs updated: `docs/NEW_DESIGN.md`, `docs/HOW_TO_MANUALLY_TEST.md`, `backend/README.md`, `docs/PLAN.md`.
- External mirror policy documented in `docs/MOONLAYERS_SYNC_POLICY.md`.
