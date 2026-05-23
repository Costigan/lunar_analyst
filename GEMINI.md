# GEMINI.md

## Lunar Analyst (Quick Model Context)

Lunar Analyst is a Linux-only lunar south-pole analysis toolkit moving from a legacy .NET desktop app to a browser-first architecture.

### Primary Goal
- Scenario-based lunar terrain/lighting analysis with high-fidelity map visualization and notebook-driven workflows.

### Core Stack
- FastAPI backend (authoritative control plane)
- React + OpenLayers web client (and Tauri-hostable)
- Python worker process for heavy compute (`pythonnet` + .NET `moonlib`)
- Marimo/notebook workflows through backend APIs (not direct DB mutation)

### Key Invariants
- Runtime baseline: Python 3.11 + .NET 9.0.
- Scenario is the source of truth on disk (`primary_dem.tif`, `scenario.db`).
- CRS discipline: explicit CRS handling; no silent reprojection.
- Filesystem safety: normalized in-root paths; reject traversal.
- Long jobs: support cancellation + structured progress events.
- Compute contract rule: real job logic belongs in `backend/jobs/handlers.py` methods (no parallel duplicate contract layer).

### Practical Guidance
- Prefer helpers in `backend/notebook/notebook_helper.py` for scripts/notebooks.
- Keep algorithm-specific math in small transform functions; keep orchestration in helpers.
- Register outputs via backend/runtime helpers so products appear in app workflows.
- Add focused tests for changed behavior (`backend/tests/...`).
