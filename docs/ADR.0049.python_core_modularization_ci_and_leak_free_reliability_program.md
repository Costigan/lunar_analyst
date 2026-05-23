# ADR.0049: Python Core Modularization + CI and Leak-Free Reliability Program

- Status: Accepted (Implemented)
- Date: 2026-04-12
- Owners: Lunar Analyst architecture team
- Related: `docs/CODEX_CODE_REVIEW.md`, `docs/GEMINI_CODE_REVIEW.md`, `docs/DESIGN.md`, `AGENTS.md`, `docs/ADR.0012.python_net_native_bridge.md`, `docs/ADR.0019.unified_tool_model.md`, `docs/ADR.0034.assistant_eval_scoring_and_ci_gates.md`, `docs/ADR.0037.script_runtime_mode_isolation_for_osgeo_and_moonlib.md`, `docs/ADR.0040.lazy_assistant_provider_and_rag_initialization.md`

## Context

Recent architecture reviews identified a common risk pattern:

- Python control-plane code has become overly concentrated in large modules (`backend/api/dependencies.py`, `backend/jobs/handlers.py`, `backend/services/assistant/tool_registry.py`).
- Runtime lifecycle relies on global mutable state and import-order-sensitive native bootstrap behavior.
- Reliability checks are broad but not yet enforced by always-on CI gates.
- Teardown behavior for worker threads/processes is not yet guarded by dedicated leak detection checks.

The primary objective is to improve reliability and maintainability without breaking architecture invariants in `AGENTS.md`, especially:

- FastAPI as authoritative control plane.
- JobHandlers signatures as contract source-of-truth for typed jobs.
- Explicit CRS/path safety behavior.
- Separate native compute boundaries and cancellation/progress behavior.

## Decision

Adopt a phased implementation program with two principles:

1. Simplify the Python core first (modularization + dispatch decoupling) with no contract breakage.
2. Keep verification lightweight and local so architecture work stays fast and reversible.

This ADR is a delivery roadmap and tracking artifact for that program.

## Scope

In scope:

- Python control-plane modularization.
- Assistant tool dispatch refactor to registry-based execution.
- Local verification command bundle across Python, frontend, .NET, and contract exports.
- Teardown/leak detection hardening for worker/native lifecycle.
- Test fixture standardization and deterministic e2e smoke coverage.

Out of scope:

- Scientific algorithm rewrites in native `moonlib` unless required for regression fixes.
- Contract redesign of typed job handler signatures.
- UI redesign unrelated to reliability/maintainability goals.

## Non-Negotiable Constraints

- Keep `backend/jobs/handlers.py` method signatures authoritative for typed job contracts.
- Keep API/WS contracts backward compatible unless explicitly versioned.
- Preserve path normalization and scenario-root safety checks.
- Preserve cancellation/progress semantics for long-running jobs.
- Use repo-managed Python (`.venv/bin/python`) in all documented test commands.

## Phased Implementation Plan

## Phase 0: Baseline, Guardrails, and Measurement

Goal: establish a stable baseline and non-regression measurement before structural changes.

Implementation checklist:

- [ ] Capture baseline test pass/fail and duration for key suites.
- [ ] Capture baseline file-size/coupling metrics for target modules.
- [ ] Document known flaky/leak-prone tests with reproduction notes.
- [ ] Define and commit a lightweight local verification command bundle used by all later phases.

Required phase tests:

- [ ] `.venv/bin/python -m pytest backend/tests/contract -q`
- [ ] `.venv/bin/python -m pytest backend/tests/worker -q`
- [ ] `.venv/bin/python -m pytest backend/tests/integration -q`
- [ ] `npm run test`
- [ ] `dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal`
- [ ] `.venv/bin/python -m backend.tools.export_openapi`
- [ ] `.venv/bin/python -m backend.tools.export_contract_schemas`

Exit criteria:

- [ ] Baseline test report committed to docs/dev notes.
- [ ] Local verification bundle agreed and used as standard.

Rollback:

- No behavior changes expected; rollback by reverting baseline artifacts only.

## Phase 1: Modularize `backend/api/dependencies.py`

Goal: split the composition monolith into bounded modules without changing runtime behavior.

Implementation checklist:

- [ ] Extract scenario/product/layer service concerns into dedicated modules.
- [ ] Extract job runtime wiring and workspace message helpers into dedicated modules.
- [ ] Keep a thin composition root in `dependencies.py`.
- [ ] Preserve current service container public API and initialization semantics.

Required phase tests:

- [ ] Run Phase 0 local verification bundle.
- [ ] Add/update focused tests validating service-container startup/shutdown equivalence.
- [ ] Run targeted integration tests for native health and cancellation.

Exit criteria:

- [ ] No route/contract diffs except non-functional metadata ordering.
- [ ] Startup/shutdown behavior unchanged under existing tests.

Rollback:

- Revert module extraction commits; restore previous `dependencies.py` layout.

## Phase 2: Refactor `backend/jobs/handlers.py` to domain executors

Goal: reduce handler-file congestion while preserving handler signature contracts.

Implementation checklist:

- [ ] Reuse/extend existing executor modules first; only create new domain modules when no suitable bounded module exists.
- [ ] Create domain executor modules where needed (for example `terrain`, `lighting`, `map_algebra`, `notebook_jobs`).
- [ ] Keep handler functions as thin contract wrappers delegating to executors.
- [ ] Preserve response model shapes and error-code semantics.
- [ ] Keep job progress/cancel callbacks functionally identical.

Required phase tests:

- [ ] Run Phase 0 local verification bundle.
- [ ] Add/maintain regression tests for representative typed handlers per domain.
- [ ] Re-export and validate OpenAPI + JSON schemas.

Exit criteria:

- [ ] Handler signatures unchanged.
- [ ] Generated job routes unchanged.
- [ ] Existing worker/contract tests green.

Rollback:

- Revert executor extraction while retaining any safe test additions.

## Phase 3: Replace assistant tool `if/elif` dispatch with registry model

Goal: make tool onboarding additive and reduce central dispatcher risk.

Implementation checklist:

- [ ] Introduce registry abstraction mapping tool name -> handler callable + metadata.
- [ ] Migrate a first slice of tools behind compatibility shim.
- [ ] Keep existing tool names, contracts, and confirmation semantics unchanged.
- [ ] Remove duplicated dispatch branches after parity is proven.

Required phase tests:

- [ ] Run Phase 0 local verification bundle.
- [ ] Add parity tests that compare old and new dispatch outcomes for migrated tools.
- [ ] Run assistant eval smoke subset:
  - [ ] `.venv/bin/python -m pytest backend/tests/evals/test_assistant_functional.py -q`
  - [ ] `.venv/bin/python -m pytest backend/tests/evals/test_assistant_domain.py -q`

Exit criteria:

- [ ] Migrated tools execute through registry path by default.
- [ ] No regression in tool authorization/confirmation behavior.
- [ ] Assistant eval smoke subset remains green for migrated-tool scenarios.

Rollback:

- Revert to pre-phase commits using git if migrated registry path introduces regressions.

## Phase 4: Verification workflow hardening

Goal: enforce reliability checks continuously and block regressions during refactor.

Implementation checklist:

- [ ] Document one canonical local verification command bundle and expected runtime.
- [ ] Add optional CI workflow(s) for team visibility (non-blocking while architecture is in active flux).
- [ ] Publish concise troubleshooting docs for failed verification commands.
- [ ] Add fast-fail path for contract drift (OpenAPI/schema export mismatch).

Required phase tests:

- [ ] Run the canonical local verification bundle repeatedly across at least 5 phase PRs without unexplained failures.
- [ ] If optional CI is configured, verify one intentional failing branch reports failure as expected.

Exit criteria:

- [ ] Verification bundle is reliably run before phase completion and catches contract drift.
- [ ] Optional CI telemetry matches local verification outcomes.

Rollback:

- Revert verification-workflow changes with git if they introduce friction without reliability benefit.

## Phase 5: Teardown/leak detection hardening

Goal: guarantee clean shutdown and prevent hidden thread/process leaks.

Implementation checklist:

- [ ] Add test helpers/assertions for leaked subprocesses and non-daemon thread leftovers.
- [ ] Add shutdown regression tests after representative job types.
- [ ] Harden termination semantics for native worker and notebook job processes.
- [ ] Add diagnostics emitted on forced termination/timeouts.

Required phase tests:

- [ ] Run Phase 0 local verification bundle.
- [ ] Run new leak-focused tests in repeated loops (for example x5) to detect intermittent failures.
- [ ] Verify clean test process exit without timeout workarounds.

Exit criteria:

- [ ] No known hanging test flows in baseline verification suites.
- [ ] Leak checks enforced in local test runs.

Rollback:

- Revert only leak-check strictness toggles if blocking issue found; keep instrumentation in place.

## Phase 6: Global state reduction and native/bootstrap stabilization

Goal: remove fragile import-time side effects and reduce hidden coupling.

Implementation checklist:

- [ ] Replace module-level mutable singletons where practical with app-scoped context.
- [ ] Reduce global callback registries in runtime context wiring.
- [ ] Move native preflight behavior from import-time to explicit startup/warmup flow.
- [ ] Add explicit boot-order invariants to tests.

Required phase tests:

- [ ] Run Phase 0 local verification bundle.
- [ ] Run integration tests with and without native availability.
- [ ] Validate non-assistant and non-native API readiness under native bootstrap failure modes.

Exit criteria:

- [ ] Import order no longer required for stable startup.
- [ ] Native bootstrap failures do not degrade unrelated API paths.

Rollback:

- Revert to pre-phase commits using git if startup-order refactors introduce critical regressions.

## Cross-Phase Verification Policy

Each phase must complete the local verification bundle before moving to the next phase. No phase may be declared complete with failing required verification commands.

Core local verification commands (normative):

- `.venv/bin/python -m pytest backend/tests/contract -q`
- `.venv/bin/python -m pytest backend/tests/worker -q`
- `.venv/bin/python -m pytest backend/tests/integration -q`
- `npm run test`
- `dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal`
- `.venv/bin/python -m backend.tools.export_openapi`
- `.venv/bin/python -m backend.tools.export_contract_schemas`

## Progress Tracking Checklist

Program-level milestones:

- [x] Phase 0 complete
- [x] Phase 1 complete
- [x] Phase 2 complete
- [x] Phase 3 complete
- [x] Phase 4 complete
- [x] Phase 5 complete
- [x] Phase 6 complete

Reliability outcomes:

- [x] Core Python modules reduced to bounded ownership units.
- [x] Assistant tool onboarding no longer requires central `if/elif` edits.
- [x] Verification workflow is simple, repeatable, and consistently green.
- [x] Teardown/leak regressions are prevented by automated checks.
- [x] Startup/native initialization is deterministic and resilient.

## Risks and Mitigations

- Risk: behavioral drift during large file extraction.
  - Mitigation: phase-by-phase refactor with parity tests and route/schema diff checks.

- Risk: CI instability from environment-sensitive native tests.
  - Mitigation: keep optional CI non-blocking and use local verification bundle as the authoritative signal during active architecture refactors.

- Risk: over-scoping refactor beyond reliability goals.
  - Mitigation: enforce phase scope boundaries and require explicit ADR amendment for scope expansion.

## Adoption

If accepted, this ADR becomes the governing implementation plan for reliability/maintainability refactor work through ADR.0049 completion.

## Implementation Status (2026-04-13)

This ADR is fully implemented.

### Delivered

- Phase 0:
  - baseline metrics + baseline test report committed at:
    - `docs/dev_notes/adr0049_phase0_baseline_2026-04-12.md`
    - `.assistant/adr0049/baseline_metrics_2026-04-12.json`
  - canonical local verification bundle committed: `scripts/run_local_verification.sh`.
- Phase 1:
  - major `backend/api/dependencies.py` decomposition completed with extracted modules:
    - `backend/api/marimo_service.py`
    - `backend/api/runtime_state.py`
    - `backend/api/store_models.py`
    - `backend/api/notebook_session_service.py`
  - composition-root compatibility preserved.
- Phase 2:
  - handler logic extraction into domain executors:
    - `backend/jobs/executors/horizons.py`
    - `backend/jobs/executors/notebook.py`
    - `backend/jobs/executors/rag.py`
  - `ToolImplementations.*` signatures remain authoritative and unchanged for contracts.
- Phase 3:
  - registry-first assistant dispatch implemented in `backend/services/assistant/tool_registry.py`.
  - broad tool migration completed, including scenario/script, artifact, job/run, product, colormap, and layer tools.
  - helper decomposition completed with compatibility wrappers:
    - `backend/services/assistant/tool_layer_resolution.py`
    - `backend/services/assistant/tool_artifact_resolution.py`
    - `backend/services/assistant/tool_script_ops.py`
    - `backend/services/assistant/tool_scenario_matching.py`
    - `backend/services/assistant/tool_logs.py`
- Phase 4:
  - contract drift fast-fail added: `scripts/check_contract_drift.sh`.
  - verification bundle now includes drift check.
  - optional CI workflow added:
    - `.github/workflows/adr0049-verification.yml`
  - troubleshooting guide added:
    - `docs/dev_notes/adr0049_verification_troubleshooting.md`
- Phase 5:
  - leak-check test helpers added:
    - `backend/tests/support/leak_checks.py`
  - shutdown/leak regression tests added:
    - `backend/tests/worker/test_runtime_leak_checks.py`
- Phase 6:
  - explicit boot-order invariants added:
    - `backend/tests/integration/test_boot_order_invariants.py`
  - native health/non-probe API readiness under import-time preflight skip verified.

### Verification Evidence (Latest)

- `.venv/bin/python -m pytest backend/tests/contract backend/tests/integration backend/tests/worker -q`:
  - `553 passed, 3 skipped` (user-verified on 2026-04-13).
- Additional focused slices executed and passed (user-verified on 2026-04-13):
  - `backend/tests/worker/test_mcp_tool_registry.py`
  - `backend/tests/worker/test_tool_registry_artifact_paths.py`
  - `backend/tests/contract/test_phase6_assistant_api.py`
  - `backend/tests/contract/test_phase4_marimo_integration.py`
  - plus notebook/horizons/rag targeted slices referenced in this ADR timeline.
