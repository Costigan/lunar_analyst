# ADR.0039: Linux Port Plan (Pop!_OS First, Ubuntu Container Second)

- Status: Accepted (later narrowed by ADR.0046 to Linux-only baseline)
- Date: 2026-04-01
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0001.process_model.md`, `docs/ADR.0012.python_net_native_bridge.md`, `AGENTS.md`

## Context

Lunar Analyst is currently Windows-first. The codebase and runtime configuration include Windows-specific assumptions across:

- backend runtime defaults and docs (`D:/...` paths, `.bat`/PowerShell launch flow),
- native bootstrap (`WinDLL`, `.dll` naming and preload logic),
- native library interop (`cspice.dll` binding),
- native build/test setup (Windows Forms project in solution, test asset paths pinned to `win-x64`),
- Python GDAL runtime expectations (`osgeo` import required in runtime helper paths).

Direct Linux probing in the existing environment showed:

- `/e/projects/env_311` currently runs Python `3.12.3` (not project baseline `3.11`),
- `osgeo` is missing,
- `dotnet` is not installed on host,
- API can import/start in degraded mode, with expected warnings for native bootstrap and GDAL runtime setup.

We need a staged plan that first achieves practical Linux host operation on Pop!_OS, then packages the same runtime in an Ubuntu container.

## Decision

Adopt a two-stage port strategy:

1. **Stage A (Host Port):** make Pop!_OS a supported development/runtime target.
2. **Stage B (Containerization):** build Ubuntu container images only after Stage A runtime parity is stable.

Track work as explicit checklist items in this ADR. Native compute parity is treated as a separate sub-stage with higher risk and explicit rollback points.

## Scope

In scope:

- Linux host execution for backend, jobs, frontend build/test, and notebook paths.
- Native bridge and moonlib portability assessment and implementation work.
- Ubuntu container image and runtime profile.
- CI updates for Linux lanes.

Out of scope:

- Tauri packaging/deployment changes.
- Security/auth architecture changes beyond what is required to run existing local workflows.
- Broad refactors unrelated to platform portability.

## Implementation Plan (Checklist)

### Phase 0: Baseline and Acceptance Contract

- [x] Complete Linux portability assessment of current repo/runtime state.
- [ ] Define formal Linux acceptance criteria for Stage A:
  - backend startup health endpoints,
  - scenario discovery/create/list flow,
  - raster map-delivery path,
  - notebook/script execution in `osgeo` mode,
  - selected assistant/tool flows.
- [ ] Define native-compute acceptance criteria for Stage A-Native:
  - `pythonnet` bootstrap success,
  - moonlib smoke checks,
  - horizon/lightmap representative job pass.

Acceptance evidence:

- [ ] Add a short test matrix section to `docs/HOW_TO_TEST.md` for Linux host.

### Phase 1: Linux Runtime Profiles and Launch Paths

- [ ] Add Linux app config profile (`config/lunar_analyst.linux.toml`) with:
  - Linux `workspace_root`,
  - Linux Python executable paths,
  - Linux-native defaults for optional provider command paths.
- [ ] Add Linux launch script (`scripts/run_backend.sh`) that replaces Windows-only startup helpers for Linux.
- [ ] Preserve Windows launch/config path unchanged (`analyst.ps1`, `lunar_analyst.bat` remain valid).
- [ ] Update docs to show OS-specific startup commands.

Acceptance:

- [ ] Backend starts on Pop!_OS using Linux config profile without manual code edits.

### Phase 2: Python Environment Parity (Pop!_OS)

- [ ] Decide and document interpreter baseline for Linux:
  - either enforce Python `3.11`,
  - or formally approve `3.12` and update compatibility statement/tests.
- [ ] Install and verify missing Python runtime dependencies (minimum: `osgeo`).
- [ ] Add/refresh reproducible backend dependency manifest(s) for Linux setup.
- [ ] Add quick runtime probe command to verify required imports (`fastapi`, `rasterio`, `osgeo`, `pythonnet`, etc.).

Acceptance:

- [ ] `configure_gdal_runtime()` succeeds on Linux with configured environment.
- [ ] Core backend test subset passes in Linux venv.

### Phase 3: Remove Hardcoded Windows Defaults in Runtime Code

- [ ] Replace `D:/...` fallback defaults with config/env-derived defaults in:
  - `backend/jobs/raster_transform.py`,
  - `backend/notebook/notebook_helper.py`,
  - any remaining runtime-path code used in active flows.
- [ ] Keep compatibility behavior for explicit Windows configuration values.
- [ ] Add tests asserting platform-neutral fallback behavior.

Acceptance:

- [ ] No active runtime path in backend defaults to a Windows drive path when config/env is absent.

### Phase 4: Native Bootstrap Cross-Platform Refactor

- [ ] Split native loader behavior by platform strategy (Windows vs Linux).
- [ ] Add Linux-native shared library resolution (`.so`) where Windows currently assumes `.dll`.
- [ ] Keep strict import-order/consistency guarantees where possible, with Linux equivalents.
- [ ] Maintain existing Windows behavior (no regression in current platform).

Acceptance:

- [ ] `bootstrap_pythonnet()` works on Linux when moonlib artifacts are present.
- [ ] Existing bootstrap unit tests pass; add Linux-specific resolver tests.

Risk and rollback:

- Risk: bootstrap regressions can block both native and GDAL paths.
- Rollback: keep feature flag/config guard to fall back to current Windows-centric path while Linux path matures.

### Phase 5: moonlib/CSPICE/Linux Native Artifacts

- [ ] Produce Linux moonlib build artifacts (`dotnet build` on Linux).
- [ ] Resolve CSPICE interop portability:
  - support Linux library naming/loading (`libcspice.so`) or runtime resolver mapping.
- [ ] Validate native smoke checks (`BridgeSmoke` and SPICE smoke path) on Linux.
- [ ] Audit `System.Drawing` usage in active runtime paths and mitigate Linux runtime issues if encountered.

Acceptance:

- [ ] Linux native smoke checks pass end-to-end in backend process/worker path.
- [ ] Representative native compute job(s) pass on Linux.

Risk and rollback:

- Risk: native dependency ABI/runtime mismatches.
- Rollback: keep non-native job paths operational and gate native job availability by capability checks.

### Phase 6: .NET Solution/Test Portability Cleanup

- [x] Ensure Linux build/test pipelines do not require Windows-only projects (`CompareHorizons` removed from the active native solution).
- [ ] Update test project asset-copy paths to avoid hardcoded `win-x64` assumptions.
- [ ] Add Linux-native `dotnet test` subset for moonlib and relevant tests.

Acceptance:

- [ ] Linux CI job can build/test required native components without manual project exclusion edits.

### Phase 7: Ubuntu Containerization (Post Host-Parity)

- [ ] Add Dockerfile(s) for backend runtime with Linux dependencies.
- [ ] Add build stage for native artifacts when required.
- [ ] Add runtime image with:
  - Python env and required libs,
  - GDAL/PROJ runtime data,
  - optional native artifacts for moonlib path.
- [ ] Externalize scenario workspace via volume mount and environment config.
- [ ] Add health checks for API and native health endpoints.

Acceptance:

- [ ] Container runs backend and passes Stage A acceptance checks.
- [ ] Native-enabled container variant passes Stage A-Native checks.

### Phase 8: CI and Documentation Completion

- [ ] Add Linux host CI lane (non-native core first).
- [ ] Add Linux native CI lane after native parity is stable.
- [ ] Keep Windows CI as control baseline during transition.
- [ ] Update `README.md`, `docs/DEVELOPER_GUIDE.md`, and `docs/HOW_TO_TEST.md` with Linux and container workflows.

Acceptance:

- [ ] CI matrix includes Windows + Linux with documented expected pass sets.

## Operational Policy During Port

- Linux support should be introduced as additive, reversible changes.
- Keep Windows behavior as a maintained baseline until Linux parity is declared accepted.
- Native-compute changes must include explicit rollback steps and smoke-test evidence.
- Do not change job contract authority: handler signatures in `backend/jobs/handlers.py` remain the source of truth.

## Completion Definition

This ADR is complete when all checklist items are either:

- checked (`[x]`) with merged implementation and passing evidence, or
- explicitly deferred with rationale and a follow-up tracking issue.
