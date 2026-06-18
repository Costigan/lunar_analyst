# Lunar Analyst Status and Remediation Plan

Date: 2026-06-18

## Executive Summary

Lunar Analyst is no longer just a prototype. The repository contains a substantial browser-first and notebook-first application with a FastAPI control plane, React/OpenLayers workspace UI, scenario-backed state, assistant/MCP tooling, Python job handlers, MoonLayers notebook support, and a serious native `.NET` horizon/lightmap engine.

Relative to the goals in `docs/DESIGN.md`, the project is directionally aligned and has many important foundations in place:

- The major runtime components described by the design exist in the repository.
- Scenario folders, API contracts, file-id asset serving, job discovery, assistant tools, RAG support, MCP exposure, notebook job execution, raster analytics, and native compute integration all have real implementation and tests.
- The project has strong architectural documentation and unusually broad test coverage across Python worker/contract/integration tests, frontend Vitest tests, MoonLayers tests, and native .NET tests.

The main concern is not absence of capability. It is accumulation of complexity. The system has a lot of technical debt, mostly from implementation concentration, runtime lifecycle coupling, partial worker isolation, and status drift between design documents and code reality. This debt is manageable, but it is already large enough that future feature work will get slower and riskier unless modularization and verification hardening become near-term priorities.

## Verification Snapshot

Tests were run on 2026-06-18 from the repository root.

Passing checks:

- `npm run test`: passed, 23 frontend test files and 88 tests.
- `timeout 600 dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal`: passed outside the sandbox, 131 tests. The first sandboxed attempt built but VSTest could not open its local socket (`SocketException (13): Permission denied`), so the successful run used elevated execution.

Backend remediation checks now pass when run outside this sandbox:

- `timeout 180 .venv/bin/python -m pytest backend/tests/contract/test_error_envelope.py backend/tests/worker/test_assistant_session_store.py backend/tests/worker/test_runtime_leak_checks.py backend/tests/worker/test_workspace_path_contract.py backend/tests/integration/test_boot_order_invariants.py -q`: passed, 13 tests.
- `timeout 180 .venv/bin/python -m pytest backend/tests/contract/test_phase4_5_shared_horizon_store.py::test_shared_horizon_resolve_reuse_inspect_and_detach -q`: passed, 1 test.
- `timeout 900 .venv/bin/python -m pytest backend/tests/contract -q`: passed, 110 tests.
- `timeout 900 .venv/bin/python -m pytest backend/tests/worker -q`: passed, 494 tests, 1 skipped.
- `timeout 600 .venv/bin/python -m pytest backend/tests/integration -q`: passed, 11 tests, 2 skipped.

Important environment note:

- FastAPI `TestClient` and AnyIO sync-worker execution can hang inside the managed sandbox because threadpool execution blocks there. The passing backend pytest results above were verified outside the sandbox. This appears to be a sandbox execution limitation, not an application failure.
- OpenAPI and contract-schema exports completed, but `scripts/check_contract_drift.sh` still reports generated contract drift. The generated OpenAPI now includes nomenclature and assistant bug-report surfaces that appear to predate this remediation work. That drift still needs review before the contract baseline can be called clean.

This verification snapshot changes the backend conclusion: the directly observed hangs and service-container failures have been remediated, but generated contract drift remains an unresolved release hygiene issue.

## Backend Remediation Plan

### Goal

Restore a trustworthy backend verification baseline without changing public API, job, assistant, or scenario contracts. The immediate target is:

- [x] Contract tests complete without hangs.
- [x] Worker tests complete without leak-check failures or process timeout.
- [x] Integration tests complete without native-health stalls.
- [x] Service-container startup and shutdown are deterministic in tests.
- [x] Failures produce visible diagnostics instead of silent hangs.

Out of scope for this remediation slice:

- [x] Do not refactor the full assistant service or job handler monoliths.
- [x] Do not change job handler signatures or generated route contracts.
- [x] Do not move `raster.calculate`, `raster.transform`, or `terrain.viewshed` to worker isolation.
- [x] Do not change native horizon/lightmap algorithms.

### Observed Backend Failures

1. Contract test hang:

- Command: `.venv/bin/python -m pytest backend/tests/contract -q`
- Stalled test: `backend/tests/contract/test_error_envelope.py::test_validation_error_uses_stage1_envelope`
- Single-test reproduction: `timeout 120 .venv/bin/python -m pytest backend/tests/contract/test_error_envelope.py::test_validation_error_uses_stage1_envelope -vv`
- Result: timed out without useful traceback.

Likely area:

- FastAPI app/test-client lifecycle, dependency/service-container startup, native preflight path, or exception handling path for validation errors.

2. Worker leak-check failures:

- Command: `.venv/bin/python -m pytest backend/tests/worker/test_runtime_leak_checks.py -vv`
- Failing tests:
  - `test_shutdown_services_no_non_daemon_thread_leak`
  - `test_notebook_runner_process_shutdown_is_detected`
- Failure: `sqlite3.OperationalError: unable to open database file` during `AssistantSessionStore` initialization inside `build_service_container()`.

Likely area:

- assistant session database path resolution, parent-directory creation, workspace/test temp root setup, or service-container defaults when no test-specific workspace root exists.

3. Integration test hang:

- Command: `.venv/bin/python -m pytest backend/tests/integration -q`
- Stalled test: `backend/tests/integration/test_boot_order_invariants.py::test_native_health_endpoint_remains_available_without_probe`
- Result: suite timed out after initial passes.

Likely area:

- `/health/native` behavior when native probe is disabled/unavailable, import-time preflight skip semantics, TestClient lifecycle, or native health route timeout handling.

### Phase 0: Freeze Reproductions and Add Diagnostics

Purpose: make the failures observable before changing behavior.

Tasks:

- [x] Add or use outer timeouts for the three known failing commands in local verification notes.
- [x] Run each failing test with `PYTHONFAULTHANDLER=1` and `pytest -vv -s` to determine whether the process is blocked in service construction, route handling, subprocess cleanup, SQLite open, or native bootstrap.
- [x] Add temporary local-only diagnostic commands to a dev note if needed, but do not commit noisy debug logging unless it is generally useful.
- [x] Capture the resolved assistant session database path during `build_service_container()` in the failing leak-check tests.
- [x] Capture the configured workspace root, assistant store path, and whether each parent directory exists.

Acceptance criteria:

- [x] Each current backend failure has a deterministic one-command reproduction.
- [x] Each reproduction identifies the last entered subsystem before stall/failure.
- [x] No production behavior changes are made in this phase.

Rollback:

- [ ] Revert any diagnostic-only edits.

### Phase 1: Fix Assistant Session Store Path Initialization

Purpose: make service-container construction reliable in tests and local runs.

Hypothesis:

`AssistantSessionStore` attempts `PRAGMA journal_mode=WAL` before the SQLite database path's parent directory is guaranteed to exist, or a test/runtime path resolves to a non-writable or missing workspace location.

Tasks:

- [x] Inspect `backend/services/assistant/session_store_sqlite.py` initialization and `backend/api/dependencies.py` assistant store path construction.
- [x] Ensure the parent directory for `assistant_sessions.db` is created before opening SQLite.
- [x] Ensure path creation uses existing workspace-root safety rules and does not create directories outside the configured workspace root.
- [x] Add focused test: store initialization creates a missing `.assistant/` parent under a temp workspace.
- [x] Add focused test: store initialization rejects or fails clearly for an out-of-root path.
- [x] Add focused test: `build_service_container()` can initialize with a temp workspace and then shut down cleanly.
- [x] Re-run `.venv/bin/python -m pytest backend/tests/worker/test_runtime_leak_checks.py -vv`.
- [x] Re-run `.venv/bin/python -m pytest backend/tests/worker/test_assistant_session_store.py -q`.
- [x] Re-run `.venv/bin/python -m pytest backend/tests/worker/test_workspace_path_contract.py -q`.

Acceptance criteria:

- [x] Both runtime leak checks pass.
- [x] SQLite path failures, if any remain, include the path and reason in a controlled exception or log.
- [x] No assistant session schema or persistence contract changes.

Rollback:

- [ ] Revert session-store path initialization changes and tests.

### Phase 2: Isolate the Contract Test Hang

Purpose: make validation-error contract tests terminate reliably.

Tasks:

- [x] Run `test_validation_error_uses_stage1_envelope` after Phase 1; if it passes, document the assistant-store path dependency as the root cause.
- [x] If it still hangs, instrument app construction.
- [x] If it still hangs, instrument TestClient entry.
- [x] If it still hangs, instrument request dispatch.
- [x] If it still hangs, instrument validation exception handling.
- [x] If it still hangs, instrument TestClient shutdown.
- [x] Inspect `backend/tests/contract/test_error_envelope.py`, `backend/api/app.py`, `backend/api/errors.py`, and dependency overrides used by the test.
- [x] Ensure validation-error handling does not trigger full native or assistant initialization.
- [ ] Add a regression test that constructs the app and sends the invalid request under a short in-test timeout or equivalent watchdog that fails with a useful message.
- [x] Re-run `.venv/bin/python -m pytest backend/tests/contract/test_error_envelope.py -vv`.
- [x] Re-run `.venv/bin/python -m pytest backend/tests/contract -q`.

Acceptance criteria:

- [x] The single error-envelope test completes.
- [x] The full contract suite completes without external timeout.
- [x] Validation errors still use the Stage 1 envelope.

Rollback:

- [ ] Revert error-envelope or lifecycle changes while keeping useful diagnostics if generally applicable.

### Phase 3: Fix Native Health Integration Stall

Purpose: make `/health/native` safe and bounded when the native probe is skipped or unavailable.

Tasks:

- [x] Inspect `backend/tests/integration/test_boot_order_invariants.py` and the native health route implementation.
- [x] Confirm the expected contract for `LUNAR_ANALYST_SKIP_IMPORT_TIME_NATIVE_PREFLIGHT=1`: the route should return a bounded, explicit status and must not block on native bootstrap.
- [x] Add a route-level timeout or non-blocking probe behavior if the route can currently wait indefinitely on native state.
- [x] Ensure native health checks do not initialize full assistant/RAG/session storage unless explicitly required.
- [x] Add or update test: skipped preflight returns quickly.
- [ ] Add or update test: missing native probe returns controlled degraded/unavailable status.
- [x] Add or update test: route still succeeds when native bootstrap is healthy.
- [x] Re-run `.venv/bin/python -m pytest backend/tests/integration/test_boot_order_invariants.py -vv`.
- [x] Re-run `.venv/bin/python -m pytest backend/tests/integration -q`.

Acceptance criteria:

- [x] `test_native_health_endpoint_remains_available_without_probe` completes.
- [x] Full integration suite completes without external timeout.
- [x] Native health behavior is explicit and bounded in degraded environments.

Rollback:

- [ ] Revert native health route changes and tests.

### Phase 4: Run Full Backend Verification and Close the Loop

Purpose: prove the fixes compose.

Commands:

- [x] `.venv/bin/python -m pytest backend/tests/contract -q`
- [x] `.venv/bin/python -m pytest backend/tests/worker -q`
- [x] `.venv/bin/python -m pytest backend/tests/integration -q`
- [x] `.venv/bin/python -m backend.tools.export_openapi`
- [x] `.venv/bin/python -m backend.tools.export_contract_schemas`
- [x] `scripts/check_contract_drift.sh`

Acceptance criteria:

- [x] All backend suites complete without outer timeout.
- [x] Any remaining failures are ordinary assertion failures with useful output, not silent hangs.
- [ ] No unexpected OpenAPI/schema drift, unless explicitly intended and reviewed.

Status note: `scripts/check_contract_drift.sh` was run and reported drift in `docs/contracts/generated/v1`. The drift is generated OpenAPI/schema output for nomenclature and assistant bug-report surfaces and needs review before this item can be closed.

Rollback:

- [ ] Revert the smallest phase that introduced contract drift or lifecycle regressions.

### Phase 5: Harden CI and Prevent Regression

Purpose: make these failures hard to reintroduce.

Tasks:

- [x] Update `.github/workflows/adr0049-verification.yml` after backend fixes are stable: keep `.NET 10.0.x`.
- [ ] Update `.github/workflows/adr0049-verification.yml` after backend fixes are stable: remove `continue-on-error: true` once the selected checks are reliable.
- [ ] Update `.github/workflows/adr0049-verification.yml` after backend fixes are stable: include the fixed backend lifecycle slices.
- [ ] Update `.github/workflows/adr0049-verification.yml` after backend fixes are stable: include contract drift checking.
- [ ] Add `docs/RUNNING_TESTS.md` troubleshooting note for SQLite assistant store path failures.
- [ ] Add `docs/RUNNING_TESTS.md` troubleshooting note for native-health degraded mode.
- [ ] Add `docs/RUNNING_TESTS.md` troubleshooting note for sandbox-specific VSTest socket denial.
- [ ] Consider a fast smoke target that includes the contract error-envelope test.
- [ ] Consider a fast smoke target that includes runtime leak checks.
- [ ] Consider a fast smoke target that includes the boot-order/native-health invariant test.
- [ ] Consider a fast smoke target that includes frontend tests.
- [ ] Consider a fast smoke target that includes native `HorizonGen.Tests`.

Acceptance criteria:

- [ ] CI catches service-container startup failures.
- [ ] CI catches backend route hangs through bounded test timeouts or route-level bounded behavior.
- [ ] Developers have a short, current command list for reproducing backend lifecycle failures locally.

Rollback:

- [ ] Restore optional CI while preserving local verification docs if CI proves unstable for infrastructure reasons.

### Recommended Work Order

- [ ] Fix assistant-store path initialization first because it is a direct failure and may unblock other service-container-dependent tests.
- [ ] Re-run the contract and integration reproductions before editing their code paths; avoid fixing symptoms that disappear after service-container startup is corrected.
- [ ] Fix native health route boundedness only if it still stalls after the assistant-store path fix.
- [ ] Only after backend suites complete should larger modularization work begin.

### Risks

- [ ] A quick fix that creates assistant store directories too broadly could violate workspace-root safety.
- [ ] Adding timeouts at the test layer without fixing route/service behavior could hide production hangs.
- [ ] Native health changes can accidentally initialize native/pythonnet paths inside FastAPI, conflicting with worker-only discipline.
- [ ] Global `SERVICES` state may cause order-dependent pass/fail behavior; tests should reset service state through a shared helper rather than ad hoc assignment where practical.

## Status Relative to Product Goals

### Browser-First Analysis Application

Status: substantially implemented, still maturing.

Evidence:

- The React/OpenLayers application exists under `backend/web/lunar_analyst/`.
- It includes workspace layout, map viewport, scenario explorer, layer manager, assistant panes, jobs pane, notebook tabs, image/text/CSV/Python editors, Moon Trek catalog surfaces, and many focused frontend tests under `backend/web/lunar_analyst/src/__tests__/`.
- Frontend commands are wired through root `package.json` and frontend `package.json`.

Remaining risk:

- The UI surface is broad, but no always-on end-to-end browser workflow appears to enforce the full loop from scenario selection to job launch to map artifact display.
- CSS and selected UI components are large, especially `backend/web/lunar_analyst/src/styles/app.css` and `JobsManagerPane.tsx`, which increases maintenance friction.

### Notebook-First and MoonLayers Workflows

Status: implemented and useful, with expected gaps.

Evidence:

- `moonlayers_pkg/` contains the in-repo MoonLayers package, frontend assets, notebook examples, scripts, and tests.
- Backend notebook runtime code exists under `backend/notebook/`.
- The design's notebook/script runtime isolation model is represented in runtime code and tests.

Remaining risk:

- Notebook execution has unavoidable process lifecycle complexity, especially around cancellation, logs, and native/GDAL import isolation.
- The design still lists MoonLayers gaps such as HTTP response caching, more protocols, and advanced measurement tools.

### FastAPI Control Plane and Scenario Model

Status: strong but too centralized.

Evidence:

- Backend API, contract, service, job, worker, notebook, MCP, and assistant modules are present.
- Contract tests and generated schemas exist under `backend/tests/contract/` and `docs/contracts/`.
- Scenario-root safety and CRS discipline are treated as explicit implementation concerns.

Remaining risk:

- `backend/api/dependencies.py` is about 5,000 lines and acts as a composition root, service container, lifecycle manager, repository/service holder, and runtime helper module.
- Tests frequently mutate the global `SERVICES` singleton directly, which is a sign that app-scoped lifecycle boundaries are still weak.

### Handler-Centered Compute and Job Contracts

Status: implemented, but under structural stress.

Evidence:

- `backend/jobs/handlers.py` is the main typed handler surface and includes worker-only tags for native/lighting/horizon workloads.
- Handler-backed jobs, generated job routes, and tests are present.
- Raster calculate, raster transform, viewshed, hillshade, horizon, PSR, and temporal lighting paths all have meaningful implementation.

Remaining risk:

- `backend/jobs/handlers.py` is about 5,800 lines. It mixes contract signatures, validation, orchestration, raster IO, native bridge calls, artifact registration, and UI/job result concerns.
- The design's "JobHandlers signatures are source of truth" rule is good, but the file has become a gravity point for too much actual implementation detail.

### Native Horizon and Lighting Compute

Status: substantial and actively validated, but scientifically sensitive.

Evidence:

- Native source and tests exist under `native/new_horizon/`.
- The .NET test surface includes horizon file/store/compressor tests, lightmap streaming tests, bridge tests, and native scenario regression tests.
- `docs/DESIGN.md` describes the current production path in detail, including quadtree horizon generation, subpatch interpolation, compressed horizon tiles, and streaming lightmap arrays.
- The current native test project passed locally with 131 tests.

Remaining risk:

- `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs` is about 5,000 lines and contains known fitting robustness work called out in the design.
- The design explicitly states that some chord-fitting failures currently degrade gracefully instead of using a final scientifically validated strategy.
- Native projects target `net10.0`, which is now the expected native target for this repository.

### Assistant, RAG, and MCP

Status: unusually advanced for an application of this size, but complexity is high.

Evidence:

- Assistant services, provider adapters, session persistence, RAG index/wrapper, deterministic routing, tool registry, evals, and MCP transports exist.
- There are many assistant-specific worker tests and eval fixtures.
- The design includes a mature hybrid routing and observability model.

Remaining risk:

- `backend/services/assistant/assistant_service.py` is about 7,300 lines.
- `backend/services/assistant/tool_registry.py` is about 2,400 lines and still has central tool-specific dispatch logic.
- The assistant architecture has many moving parts: deterministic recognition, model fallback, provider registry, confirmation policy, RAG, MCP, external CLI agents, session persistence, and eval scoring. This is powerful, but it needs strict module boundaries to remain maintainable.

## Technical Debt Assessment

Yes, there is a lot of technical debt. It is not primarily neglected-code debt. It is scale and integration debt from a system that has grown quickly while preserving many ambitious architecture invariants.

### Highest-Risk Debt

1. Large central modules

The largest maintainability risk is concentrated responsibility:

- `backend/services/assistant/assistant_service.py` - about 7,300 lines.
- `backend/jobs/handlers.py` - about 5,800 lines.
- `backend/api/dependencies.py` - about 5,000 lines.
- `backend/services/assistant/tool_registry.py` - about 2,400 lines.
- `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs` - about 5,000 lines.

These files are not just long. They sit on core architectural boundaries, so ordinary changes can require understanding too much of the system at once.

2. Runtime lifecycle coupling

The backend still relies on global mutable runtime state and import-order-sensitive native bootstrapping:

- `SERVICES` is a module-global service container in `backend/api/dependencies.py`.
- Tests often reset or replace `SERVICES` directly.
- Native bootstrap has an import-time skip flag used by tests: `LUNAR_ANALYST_SKIP_IMPORT_TIME_NATIVE_PREFLIGHT`.

This makes startup, shutdown, test isolation, and concurrency harder to reason about.

3. Incomplete worker isolation for heavy compute

The design correctly requires long-running native, `pythonnet`, `LightmapStreamingClient`, and GDAL-heavy jobs to route through the isolated worker protocol. Some handlers are tagged worker-only, but `docs/DESIGN.md` still identifies `raster.calculate`, `raster.transform`, and `terrain.viewshed` as worker-isolation gaps because they depend on backend scenario/catalog state that is not yet fully serializable.

That is a real architectural gap because those paths are exactly the kind that can stress FastAPI process stability.

4. Verification is broad but not yet strict enough

There are many tests, but the visible GitHub workflow is optional and marked `continue-on-error: true`. It runs useful slices, but not the full local verification bundle described in ADR.0049. ADR.0049 is marked "Accepted (Implemented)", yet its phase checklists remain mostly unchecked.

The latest local verification reduced this concern but did not eliminate it. Frontend, native, backend contract, backend worker, and backend integration suites now pass in the verified environment, but contract drift checking still reports generated OpenAPI/schema changes that need review. This means the project has good test assets and a healthier backend baseline than before, but automated enforcement still needs tightening.

5. Documentation/status drift

The docs are valuable, but there are signs of drift:

- ADR.0049 says implemented while retaining unchecked implementation checklists.
- `docs/DESIGN.md` is comprehensive but mixes implemented behavior, intended policy, known gaps, and future direction.
- Runtime guidance now consistently targets `.NET 10.0` for active native development and tests.
- Multiple review/status files exist with overlapping conclusions.

For agents and new developers, this makes it hard to distinguish contract from aspiration.

6. Repository hygiene and generated artifacts

The repo has good ignore rules, but tracked runtime/probe files remain:

- `backend/probe_backend.db`
- `backend/probe_backend.db-journal`

The working tree also contains local generated dependency/build artifacts such as `node_modules`, `.NET obj`, Python caches, and diagnostic outputs. Most are ignored, but their presence in the checkout increases noise during audits.

## Strengths That Reduce Risk

The debt is significant, but the project has strong counterweights:

- Architecture invariants are explicit and mostly sensible.
- The scenario-root and CRS safety story is well developed.
- Contract generation and contract tests exist.
- There is broad Python, frontend, native, and MoonLayers test coverage.
- Worker-only rules are written down and partially implemented.
- The assistant has eval infrastructure, not just ad hoc prompting.
- Native compute has reference-vs-production framing and regression tests.

This is a good foundation for paying down debt without pausing product work completely.

## Recommended Priorities

1. Make status truthful before adding more scope.

Update `docs/DESIGN.md` and ADR.0049 so they clearly separate:

- implemented and enforced
- implemented but lightly enforced
- accepted direction
- known gap
- future work

2. Modularize the Python core.

Start with behavior-preserving extractions:

- Split `backend/api/dependencies.py` into bounded service/container/lifecycle modules.
- Keep `JobHandlers` method signatures as the contract source, but move implementation bodies into domain executors.
- Replace central assistant tool dispatch branches with a registry map.

3. Close the worker isolation gap.

Make scenario/catalog state explicit and serializable enough for `raster.calculate`, `raster.transform`, and `terrain.viewshed` to run through the worker protocol by default.

4. Strengthen automated verification.

Turn the optional ADR0049 workflow into a stricter, staged gate once it is reliable:

- contract drift
- representative worker tests
- frontend tests
- native .NET tests
- leak/teardown checks

5. Treat native robustness as a parallel science/compute track.

Do not bury the `QuadTreeHorizonGenerator` fitting fallback work under general refactoring. Track it as a separate scientific validation risk with explicit acceptance data.

## Bottom Line

Lunar Analyst is a real, ambitious application with much of the intended architecture already present. It is aligned with its stated mission-analysis goals, but it has accumulated substantial technical debt in the places that matter most: central orchestration modules, lifecycle state, heavy-compute isolation, and verification enforcement.

The debt is not fatal. It is the predictable result of integrating browser UI, notebooks, geospatial raster processing, native GPU compute, and LLM agent workflows in one product. The project should now shift from adding surface area to making the core easier to change safely.
