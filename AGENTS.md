# Lunar Analyst: AI Agent Protocol & Project Guidance

This document defines the roles, architecture invariants, and delivery protocol for AI agents (Gemini and Codex) working on Lunar Analyst.

## 1. Project Overview
Lunar Analyst is a project-based toolkit for lunar south pole mission analysis. The active product is a browser-first, notebook-first stack built on React + OpenLayers + FastAPI + Tauri, with MoonLayers for interactive analytics and legacy desktop viewer code kept outside the active runtime in this repository.

- **Primary Goal:** High-fidelity visualization and notebook-first analytics (`moonlayers`) for lunar terrain and lighting.
- **Reference Frame:** ESRI:103878 (Lunar South Pole Stereographic).
- **Target OS:** Linux only.
- **Source of Truth:** Scenario folder on disk (`primary_dem.tif`, `scenario.db`) and FastAPI-owned scenario state.
- **Agent Support:** A large language model will aid human users, who are lunar scientists but not mapping or mission planning experts, in analyzing landing sites and evaluating locations where their desired scientific observations can be done.

### 1.1 Current Development Focus

- Improve agent performance via improving guidance in the startup message and RAG documents

## 2. Architecture Invariants (Must Hold)

- **Runtime Baseline:** Python 3.11 + .NET 9.0 via `pythonnet`.
- **Process Topology:**
  1. **FastAPI Service:** Authoritative control plane; owns scenario lifecycle, API contracts, and asset serving.
  2. **Compute Worker:** Separate Python process hosting `pythonnet` + `moonlib` for heavy compute (horizon, lightmap).
  3. **Marimo Process:** Separate exploratory process; communicates only through FastAPI (REST/WS), not direct DB mutation.
  4. **Browser/Tauri Client:** React + OpenLayers frontend consuming API/WS contracts.
- **Scenario Model:** Self-contained scenario folder with `primary_dem.tif` and `scenario.db` (SpatiaLite).
- **CRS Discipline:** Persist CRS metadata and never silently reproject; all map/analysis paths must explicitly declare CRS.
- **Data Safety:** Normalize paths, serve by file-id mapping only, and reject out-of-root traversal.
- **Cancellation & Progress:** Long-running compute must support cancellation and emit structured progress events.
- **JobHandlers-Centered Compute (Strong Rule):** Implement actual calculation logic in `backend/jobs/handlers.py` methods (for example, `JobHandlers.generate_hillshade`). Handler signatures are the single source of truth for job contracts and generated API routes. Do not introduce separate parallel compute-contract layers that duplicate handler definitions.
- **Worker-Only Long Compute:** Long-running native, `pythonnet`/`.NET`, `MoonlibBridge`, `LightmapStreamingClient`, and GDAL-heavy typed handlers must be tagged/routed as worker-only and use the shared isolated worker protocol for progress, cancellation, and results. `LUNAR_ANALYST_NATIVE_INLINE_HANDLERS` is only a local development/debug escape hatch, not a production path or a reason to add inline FastAPI compute.
- **Moonlib Python Entry Surface:** Production Python app/worker code must invoke native moonlib functionality through `MoonlibBridge` only. Direct use of other moonlib runtime types is disallowed except narrowly-scoped bootstrap/runtime loading seams and test code.

## 3. Delivery Protocol

### 3.1 Task Sizing
- Keep tasks small and testable (~1 hour).
- Include explicit out-of-scope statements.
- Prefer vertical slices over broad horizontal rewrites.

### 3.2 Prompt Contract (Per Task)
- State goal and exact files allowed to change.
- List constraints/invariants and acceptance criteria.
- List required tests (unit/integration/e2e as applicable).
- List risks and rollback approach for high-risk changes.

### 3.3 Definition of Done (DoD)
- [ ] Code follows style and architecture invariants.
- [ ] Tests added/updated and passing for touched behavior.
- [ ] API/WS contracts updated and validated when schemas change.
- [ ] DB migrations (if any) are versioned, idempotent, and documented.
- [ ] Observability updated (logs + event payloads + error paths).
- [ ] Manual verification evidence captured for UI/compute workflows.

## 4. Safety & High-Risk Policy

- **No Secrets:** Never commit keys, tokens, or sensitive config.
- **Human Approval Required:**
  - DB schema migrations.
  - Packaging/deployment changes (Tauri).
  - Security-sensitive config/auth changes.
  - `pythonnet` bridge logic affecting native compute boundaries.
- **Rollback Plan Required:** Native compute changes, data migrations, contract-breaking API/WS updates.
- **Filesystem Safety:** Enforce normalized paths + scenario root allowlist checks.

## 5. Testing & Verification Expectations

- **Bootstrap First:** Run `./scripts/bootstrap.sh` before implementation when environment state is uncertain.
- **Python Environment (Required):** Use the repo-managed `.venv/bin/python`. Do not use system/default Python for this repo.
- **Python:** `pytest` for unit/integration.
- **.NET:** `dotnet test` for C# components.
- **Contract Coverage:** REST schema checks, WS event schema checks, worker cancellation/progress behavior.
- **Regression Evidence:** For bug fixes, include a test that fails before and passes after.

### 5.1 Local Python Commands (.venv)
- Activate env: `.venv/bin/python --version`
- Export OpenAPI: `.venv/bin/python -m backend.tools.export_openapi`
- Export contract schemas: `.venv/bin/python -m backend.tools.export_contract_schemas`
- Run contract tests: `.venv/bin/python -m pytest backend/tests/contract -q`

## 6. Collaboration Rules for Agents

- Finish discussions with the user and wait for explicit direction before making changes
- Do not expand scope without explicitly stating it.
- Prefer additive, reversible changes over wide refactors.
- Record assumptions when data/science interpretation is uncertain.
- If invariants conflict with requested work, stop and escalate with options.
