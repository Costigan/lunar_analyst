# Codex Architecture and Test Review

## Scope
This review is based on `docs/DESIGN.md` plus a codebase pass over the backend control plane, job/runtime layer, assistant layer, frontend test surface, and representative tests.

## Executive Assessment
The project has a strong architectural direction and many good reliability-oriented design choices:

- The runtime topology is clear and coherent (FastAPI control plane, separate native compute path, notebook runtime, browser client).
- Contract-driven job discovery from handler signatures is a strong pattern that reduces route drift.
- CRS and filesystem-safety concerns are treated as first-class constraints in both docs and implementation.
- The test footprint is broad (Python contract/worker/integration + frontend unit tests + native .NET tests), and contract exports are explicitly managed.

The main risks now are less about missing concepts and more about concentration of responsibility and runtime state complexity. The architecture is correct in intent but is accumulating implementation coupling that will hurt reliability and change velocity unless actively reduced.

## Architecture Findings

### 1) Control-plane composition is too centralized
`backend/api/dependencies.py` is currently ~5900 lines and combines:

- dependency injection container creation
- scenario/product/layer repositories and services
- job queue execution behavior
- marimo process management
- workspace message logging
- path and config resolution helpers
- assistant store/provider bootstrapping

This concentration creates a single high-risk change surface and makes lifecycle behavior harder to reason about (startup/shutdown, test isolation, failure handling).

### 2) Job handler layer is carrying too much mixed responsibility
`backend/jobs/handlers.py` is ~5600 lines and includes:

- request/response models
- low-level raster compute orchestration
- artifact registration/publishing behavior
- notebook job invocation behavior
- assistant-adjacent tool implementations

The “JobHandlers as contract source of truth” rule is good, but this file now blends contracts + orchestration + domain logic + side effects at a scale that increases regression risk.

### 3) Global mutable runtime state introduces coupling and test fragility
There are several global/singleton patterns in core runtime paths:

- global `SERVICES` container in `backend/api/dependencies.py`
- global callback registry in `backend/jobs/runtime_context.py`
- global route/tool caches in `backend/api/job_runtime.py`

These patterns simplify wiring, but they also raise the chance of cross-test contamination, hidden ordering dependencies, and subtle behavior under concurrency.

### 4) Native/bootstrap lifecycle is still brittle
`backend/api/app.py` runs a best-effort native preflight at import time. Tests explicitly disable this (`LUNAR_ANALYST_SKIP_IMPORT_TIME_NATIVE_PREFLIGHT`) in `backend/tests/conftest.py`, which is a signal that import order/bootstrap timing remains sensitive.

This is a reliability smell: behavior depends on process import context and environment flags, which is hard to reason about long term.

### 5) Event/job durability model is intentionally lightweight but has reliability tradeoffs
In-memory bounded event buffers and in-memory job state are pragmatic for now, but they imply:

- no durable resume after process restart
- possible event loss due to bounded retention
- harder post-mortem analysis for long-running compute failures

That may be acceptable for the current stage, but it should be treated as an explicit reliability limit, not an implicit one.

## Test Posture Findings

### Strengths
- Large Python test footprint with focused worker and contract tests.
- OpenAPI and schema export workflow is documented and tested.
- Integration tests exist for native health/cancellation and real moonlib bridge behavior (with environment-aware skipping).
- Frontend has fast Vitest coverage across service and utility logic.

### Gaps/Risks
- CI automation appears not yet wired in-repo (`.github/workflows` absent), while docs still rely on manual run sequences.
- Several important integration tests are skip-capable based on environment availability, reducing guaranteed signal in constrained environments.
- A representative worker test run (`backend/tests/worker/test_hillshade_job_flow.py`) completed assertions but did not terminate cleanly without a timeout in this environment, suggesting teardown/thread/process lifecycle leakage worth investigating.
- Test strategy is strong at unit/contract level but still relatively thin on deterministic, always-on end-to-end API + worker + native + frontend interaction checks.

## Top Focus Areas (Priority Order)

1. **Simplify the Python core first (`dependencies.py`, `handlers.py`, tool dispatch).**
   - Decompose `backend/api/dependencies.py` into bounded modules with a thin composition root.
   - Refactor `backend/jobs/handlers.py` into contract-first wrappers plus domain executors.
   - Replace hardcoded assistant tool `if/elif` dispatch in `backend/services/assistant/tool_registry.py` with a registry map.

2. **Automate the reliability pipeline (CI/CD) immediately after core simplification starts.**
   - Add always-on CI for Python tests, frontend tests, .NET tests, and contract export/validation.
   - Treat CI as the merge gate for refactor work to prevent hidden regressions.

3. **Harden teardown and leak detection as a first-class reliability goal.**
   - Add explicit checks for leaked worker threads/processes and hung shutdown paths.
   - Add regression tests for clean job worker/native/bootstrap teardown after representative runs.

4. **Reduce global mutable runtime state and make lifecycle explicit.**
   - Prefer app-scoped container/context over module globals.
   - Replace global callback registration with injected runtime context objects.
   - Make startup/shutdown deterministic and idempotent.

5. **Stabilize native/bootstrap sequencing and remove import-time side effects.**
   - Move bootstrap preflight to explicit startup health/warmup phases.
   - Keep non-native APIs isolated from native bootstrap failures.
   - Assert boot-order invariants in tests without environment-toggle dependence.

6. **Standardize shared test infrastructure and expand always-on e2e coverage.**
   - Move repeated service/scenario/raster setup into shared `backend/tests/conftest.py` fixtures.
   - Add a minimal always-on e2e smoke slice (scenario create -> job run -> event stream -> artifact read).

## Suggested Near-Term Plan (Small, Reversible)

1. Create `backend/api/services/` modules and move non-HTTP helper logic out of `dependencies.py` without behavior changes.
2. Extract one vertical handler domain (for example hillshade/horizons) from `handlers.py` into executor modules and keep handler signatures unchanged.
3. Introduce a registry-backed assistant tool dispatcher and migrate a small initial slice of tools behind compatibility shims.
4. Add CI workflow gates that run:
   - `.venv/bin/python -m pytest backend/tests/contract backend/tests/worker -q`
   - `npm run test`
   - `dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal`
5. Add teardown/leak regression tests (thread/process leak assertions and clean shutdown checks) for representative job flows.
6. Add shared pytest fixtures for service-container lifecycle/scenario setup and migrate a first batch of tests.

## Overall Verdict
Architecture direction is strong and deliberate, especially around contracts, CRS discipline, and scenario-root safety. The largest reliability/maintainability gains now will come from reducing implementation concentration and lifecycle coupling, then locking that down with always-on automated verification.

## Addendum: Response to `GEMINI_CODE_REVIEW.md`

### Points I Agree With
- **Monolithic core files are a primary maintainability risk.**
  - I agree with the emphasis on `backend/api/dependencies.py` and `backend/jobs/handlers.py` as major architectural congestion points.
- **Dependency wiring is too centralized.**
  - I agree that the current composition pattern behaves like a “God Object” factory and increases coupling between unrelated capabilities.
- **Tool dispatch in `tool_registry.py` should move off hardcoded branching.**
  - I agree. `backend/services/assistant/tool_registry.py` is large (~2k lines) and `execute_tool` is currently branch-heavy, which does not scale well.
- **Native bootstrap/import ordering is fragile.**
  - I agree. The use of `LUNAR_ANALYST_SKIP_IMPORT_TIME_NATIVE_PREFLIGHT` in tests is a direct indicator that startup order coupling is still present.
- **Test fixture reuse is weak at the shared `conftest.py` level.**
  - I agree that common setup patterns are distributed across files and should be consolidated where practical.
- **Top remediation themes are directionally correct.**
  - I agree with modularizing core files, moving to registry-based tool dispatch, hardening bootstrap sequencing, and standardizing test infrastructure.

### Points I Partly Agree With (with caveats)
- **“Many tests are integration-heavy.”**
  - Partly agree. Some worker/contract tests do rebuild substantial runtime state, but the suite also has many focused unit-style tests. The bigger issue is lifecycle determinism and teardown hygiene, not just integration-vs-unit mix.
- **Risk level framing for tool dispatch as “medium.”**
  - Partly agree. For current scale it is medium, but it will become high if assistant/tool surface keeps expanding without dispatch refactor.

### Points I Disagree With (or rank differently)
- **Priority 5 focused on `QuadTreeHorizonGenerator.cs` “Needs Work” comments as a top reliability focus for this review scope.**
  - I disagree with elevating this to a top cross-system priority in this specific architecture/test maintainability review.
  - Reason: the highest current reliability/maintainability leverage is in Python control-plane modularity, lifecycle/state management, and test automation hardening. Native algorithm quality work is important, but I would rank it as a separate track unless there is active evidence of user-facing regressions tied to those code paths.
