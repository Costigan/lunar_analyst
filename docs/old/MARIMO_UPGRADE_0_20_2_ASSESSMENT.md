# Marimo Upgrade Assessment (to `0.20.2`)

Date: 2026-02-26

## Summary

Upgrading this repo to the latest marimo release (`0.20.2`, released 2026-02-22) appears to be a moderate dependency + validation task, not a large refactor.

## Current State (Observed)

- Repo pin: `moonlayers_pkg/requirements.txt` currently pins `marimo==0.17.2`.
- Local runtime in `D:\projects\env_311` is currently `marimo 0.17.3` (the backend launches marimo from that environment).
- Backend marimo integration is process-launch based (CLI), not a deep integration with marimo internals.

## What Needs to Change

1. Update `moonlayers_pkg/requirements.txt` from `marimo==0.17.2` to `marimo==0.20.2`.
2. Upgrade marimo in the actual runtime environment used by this repo (`D:\projects\env_311`).
3. Re-test any version-specific marimo UI/CSS customization docs:
   - `docs/LIBRO_EMULATION_1.md` (explicitly version-pinned to `0.17.3` selectors)
4. Optionally refresh notebook metadata headers (`__generated_with`) if notebooks are opened/saved in newer marimo:
   - Example files under `moonlayers_pkg/example_notebooks/`

## Why This Looks Low-to-Moderate Risk

- The backend primarily launches marimo via CLI in `backend/api/dependencies.py` using:
  - `python -m marimo edit --headless --port 2718 --token|--no-token`
- Those CLI options still exist in newer marimo (verified against upstream `0.20.2` / current `main`).
- Lunar Analyst backend/web contracts do not directly depend on marimo's internal AI server APIs.

## Main Risks

- Marimo UI/DOM changes may break CSS hacks/customization work that was pinned to `0.17.3`:
  - `docs/LIBRO_EMULATION_1.md`
- MoonLayers widget behavior regressions in notebook rendering / anywidget trait syncing need manual smoke testing.
- Existing backend marimo tests mostly validate process launch contract behavior, not end-to-end runtime compatibility with a real marimo instance.

## Validation Plan (Recommended)

1. Bump the pin in `moonlayers_pkg/requirements.txt`.
2. Upgrade marimo in `D:\projects\env_311`.
3. Run backend contract tests (expected to catch backend launch-contract regressions).
4. Manual smoke test:
   - Launch marimo via `POST /api/v1/marimo/launch` (or UI "Open in Marimo")
   - Open a scenario-scoped notebook
   - Verify MoonLayers widget renders in marimo
   - Verify notebook -> backend interactions still work (imports/helpers/map commands)
5. Re-check `docs/LIBRO_EMULATION_1.md` CSS selectors and update if needed.

## What Is Probably Not Needed

- Changes to FastAPI marimo launch endpoints (`/api/v1/marimo/launch`, `/status`, `/stop`)
- Changes to the React "Open in Marimo" flow
- API contract/schema changes for Lunar Analyst

## Notes

- No top-level/backend marimo pin or lockfile was found in this repo; the effective runtime version appears to come from the external environment (`D:\projects\env_311`).
- Several example notebooks contain `__generated_with = "0.17.x"` headers; these are informational unless notebooks are regenerated.
