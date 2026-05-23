# Review of `docs/PLAN.md` (by Codex)

## Executive Summary
The plan is directionally strong and aligned with the repository strategy (browser-based OpenLayers client, Python backend, reuse of `new_horizon` compute). The main gaps are around execution risk: process model, compute isolation, projection parity validation, and migration sequencing. Without those, the plan can stall in integration even if individual components work.

## Findings (ordered by severity)

### 1. Critical: Process model is underspecified and risks instability
- Reference: `lunar_analyst/docs/PLAN.md:21`, `lunar_analyst/docs/PLAN.md:52`
- Issue: "launch and manage a Marimo server instance" is stated, but there is no explicit decision on same-process vs separate-process lifecycle, ownership, restart behavior, or API boundary.
- Why this matters: You are combining FastAPI (request server), notebook execution (user code), and `pythonnet` (native bridge). In-process coupling greatly increases blast radius (kernel crash, native DLL crash, long-running notebook cell) and makes resource governance hard.
- Recommendation:
  - Use a **separate Marimo process** in production by default.
  - Keep FastAPI as the system-of-record service (project/layer state, job queue, assets).
  - Treat Marimo as a client of FastAPI (REST/WebSocket), not as the owner of state.
  - Allow optional in-process mode only for local debugging.

### 2. High: `pythonnet`/native dependency risk is not planned as a deployment workstream
- Reference: `lunar_analyst/docs/PLAN.md:31`, `lunar_analyst/docs/PLAN.md:47`
- Issue: The plan says "verify loading" but does not define runtime constraints for `moonlib.dll`, `cspice.dll`, ILGPU/CUDA dependencies, probing paths, version pinning, or fallback behavior.
- Why this matters: `pythonnet` integration is often the highest-risk part in mixed Python/.NET systems, especially with GPU/native dependencies.
- Recommendation:
  - Add a dedicated **Compatibility Matrix** milestone (Python version, .NET runtime version, CUDA/driver expectations, OS constraints).
  - Add a startup self-check endpoint (`/health/native`) that validates assembly load + one tiny horizon call.
  - Run compute in an isolated worker process (or subprocess pool) so native faults do not kill API process.

### 3. High: Scientific/projection parity acceptance criteria are missing
- Reference: `lunar_analyst/docs/PLAN.md:24`, `lunar_analyst/docs/PLAN.md:57`
- Issue: Plan targets feature parity but has no measurable parity tests for CRS math, raster sampling, timeline outputs, or lighting reproducibility vs existing C# app.
- Why this matters: The project is science/mission analysis; visual similarity is insufficient.
- Recommendation:
  - Define parity gates before Phase 3:
    - CRS round-trip tolerance (pixel and meter thresholds) for ESRI:103878.
    - Raster render parity tests (NoData, stretch, histogram windows).
    - Horizon/lightmap numeric regression against `new_horizon` reference outputs.

### 4. High: API contract is implied but not defined
- Reference: `lunar_analyst/docs/PLAN.md:17`, `lunar_analyst/docs/PLAN.md:53`
- Issue: `add_layer`/pipeline triggers are mentioned, but no canonical event/state model exists (layer IDs, provenance, ordering semantics, mutation rules).
- Why this matters: Frontend, Marimo notebooks, and backend will diverge quickly without a strict contract.
- Recommendation:
  - Define an explicit contract doc early:
    - Layer schema (id, source_uri, style, visibility, z-index, CRS, nodata, colormap).
    - Job schema (requested_by, parameters hash, artifact paths, status, logs).
    - Event stream (layer-added, layer-updated, job-finished, job-failed).

### 5. Medium: Migration sequencing increases rework risk
- Reference: `lunar_analyst/docs/PLAN.md:41`, `lunar_analyst/docs/PLAN.md:46`, `lunar_analyst/docs/PLAN.md:72`
- Issue: Current sequence starts broad UI work before de-risking hardest integration path (`pythonnet` + compute artifacts + asset publication).
- Why this matters: UI work may be redone once compute outputs and metadata contracts are finalized.
- Recommendation:
  - Reorder to:
    1. Native bridge spike + artifact publication contract.
    2. Asset/project services.
    3. Minimal map client consuming real artifacts.
    4. Rich UI shell and timeline.

### 6. Medium: Storage strategy is ambiguous between SpatiaLite and GeoPackage
- Reference: `lunar_analyst/docs/PLAN.md:19`, `lunar_analyst/docs/PLAN.md:68`
- Issue: "SpatiaLite / GeoPackage" is listed but not selected by role.
- Why this matters: Schema, tooling, and interoperability choices differ; ambiguity delays implementation and migration tooling.
- Recommendation:
  - Decide explicit roles:
    - Example: GeoPackage for interchange, SpatiaLite for local project DB and indexes.
  - Add schema versioning + migration plan from day one.

### 7. Medium: Security boundary for local file serving is missing
- Reference: `lunar_analyst/docs/PLAN.md:18`, `lunar_analyst/docs/PLAN.md:42`
- Issue: Asset streaming from local disk is planned, but path allowlisting/sandboxing is not.
- Why this matters: Desktop-local servers commonly drift into unsafe path exposure.
- Recommendation:
  - Restrict file serving to registered project roots and opaque file IDs.
  - Never expose arbitrary absolute path reads via API.

### 8. Medium: Test strategy is too thin for integration-heavy architecture
- Reference: `lunar_analyst/docs/PLAN.md` (overall)
- Issue: Plan has implementation tasks but no explicit test pyramid.
- Recommendation:
  - Add required test tracks:
    - Contract tests for API + websocket events.
    - Golden-file rendering tests for map styles.
    - End-to-end scenario tests: notebook generates artifact -> map updates.

### 9. Low: Minor source-path inaccuracy in "Next Steps"
- Reference: `lunar_analyst/docs/PLAN.md:74`
- Issue: It references `moonlayers/static/index.js`; current authoritative codebase emphasizes reusable modules under `moonlayers/src/`.
- Recommendation:
  - Extract from `moonlayers/src/*` modules and keep `static/` as build output only.

## Recommended Additions to the Plan

1. **Architecture Decision Record (ADR) for process model**
- Decide FastAPI + Marimo boundary, IPC mechanism, and lifecycle supervision.

2. **Native compute worker design**
- API process enqueues jobs; worker process handles `pythonnet` calls and publishes artifacts.

3. **Formal contracts before UI expansion**
- Layer/job/event schemas finalized before full React shell work.

4. **Parity benchmark suite**
- Define pass/fail thresholds versus `lunar_analyst_net` outputs.

5. **Operational concerns**
- Structured logging, job audit trail, artifact provenance, retry/cancel semantics.

## Direct answer to the Marimo process question
Recommended default: **separate process**.
- FastAPI process: authoritative state, APIs, asset service, job orchestration.
- Marimo process: interactive notebook UI and user code execution.
- Shared through explicit APIs/events, not in-memory objects.

Use same-process mode only as a dev convenience switch.

## Suggested revised phase ordering
1. Native bridge proof (`pythonnet` + `moonlib` + dependency self-check).
2. Artifact contract (what compute writes, how layers are registered).
3. Backend core (project registry, asset server, job queue, event bus).
4. Minimal OpenLayers client consuming real backend state.
5. Marimo integration via API/event contract.
6. Feature parity UI (timeline/labels), then packaging (Tauri).

## Open Questions
1. Is Windows-only acceptable for first production release (given `pythonnet` + native dependency complexity)?
2. Should long-running compute be synchronous API calls or queued jobs with progress + cancellation?
3. For project DB, do you want one-file portability (GeoPackage-first) or richer local SQL features (SpatiaLite-first)?
