# ADR.0040: Lazy Initialization for Assistant Providers and RAG Indexes

- Status: Accepted
- Date: 2026-04-01
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0001.process_model.md`, `docs/ADR.0011.ai_assistant_and_mcp.md`, `docs/ADR.0021.assistant_rag_wrapper_and_scenario_index.md`, `docs/ADR.0031.assistant_performance_improvement_program.md`, `AGENTS.md`

## Context

The current backend service container is constructed as a single eager startup graph.

`build_service_container()` currently creates, in one pass:

- scenario/catalog services,
- product/layer services,
- notebook/job services,
- assistant policy/store/service,
- assistant provider registry,
- assistant RAG wrapper configuration,
- MCP server wiring.

This means unrelated API surfaces share startup fate. In particular:

1. Job and scenario endpoints depend on `get_services()`.
2. `get_services()` constructs the full `ServiceContainer`.
3. `ServiceContainer` construction always creates `AssistantProviderRegistry`.
4. `AssistantProviderRegistry` currently registers default providers immediately.
5. Default provider registration currently configures RAG wrappers immediately.
6. RAG wrapper configuration currently constructs the global `RagIndex` immediately.
7. `RagIndex` currently opens the SQLite database and applies schema/journal configuration during construction.

As a result, non-assistant flows can fail before they start if assistant-side startup dependencies are unavailable.

Observed failure mode that motivated this ADR:

- a job endpoint test for `generate_horizons` failed before reaching the job route because RAG index initialization attempted to open the global RAG SQLite database in WAL mode during service-container construction;
- the same test passed outside the sandbox, confirming the immediate failure was environmental, not a horizons-job bug;
- nevertheless, the architectural issue remains: unrelated job APIs are exposed to assistant/RAG startup failures.

This coupling conflicts with the broader runtime design intent described in `docs/DESIGN.md`:

- FastAPI should remain a robust control plane for scenario and job operations;
- optional startup work should not block readiness unnecessarily;
- assistant features are important, but they are not required for core scenario/job API operation.

## Problem Statement

Assistant provider setup and global RAG index setup are currently treated as mandatory container-construction dependencies, even for requests that never touch assistant APIs.

This causes three classes of problems:

1. **Blast-radius expansion**
   - Assistant or RAG storage/runtime failures can block unrelated scenario/job endpoints.

2. **Startup brittleness**
   - A single optional subsystem can prevent container creation and test bootstrap.

3. **Operational ambiguity**
   - It becomes difficult to distinguish "core backend is healthy" from "assistant augmentation is fully healthy."

We need assistant provider and RAG setup to become lazy and failure-isolated while preserving current assistant behavior for assistant requests.

## Decision

Adopt **lazy initialization** for assistant providers and RAG indexes.

### Decision Summary

1. Core backend service-container construction will remain eager for core control-plane services.
2. Assistant provider initialization will be deferred until first assistant-dependent use.
3. RAG wrapper and RAG index initialization will be deferred until assistant provider initialization runs.
4. Assistant initialization failures will be isolated to assistant features and must not block core job/scenario API startup.
5. Optional background warmup may still be used, but it must be non-blocking and failure-tolerant.

## What Becomes Lazy

The following behavior changes are intended:

### Eager at Container Build

These remain eager because they are core control-plane dependencies:

- `ScenarioService`
- `ProductService`
- `LayerService`
- `NotebookJobService`
- `JobService`
- shared stores/catalog DBs
- route registration
- MCP server skeleton construction, if it does not force assistant provider execution

### Lazy on First Assistant Use

These move behind a lazy boundary:

- assistant provider registry default-provider registration,
- provider model metadata loading,
- RAG wrapper configuration,
- global RAG index construction,
- startup RAG refresh registration and refresh execution.

### Allowed Optional Warmup

The backend may still perform background warmup for assistant subsystems after startup, but:

- warmup must not block API readiness,
- warmup failures must log warnings instead of aborting service initialization,
- warmup must be idempotent and concurrency-safe.

## Required Runtime Behavior

### Core Non-Assistant Requests

Requests that touch only:

- scenarios,
- products,
- layers,
- jobs,
- notebook execution,
- map delivery,

must succeed even when:

- no LLM provider is configured,
- the assistant store is degraded,
- the RAG database cannot be opened,
- the RAG index has never been built,
- assistant warmup has not yet completed.

### Assistant Requests

On the first assistant-dependent request, the backend should:

1. detect whether providers are initialized;
2. initialize them if not;
3. configure RAG wrappers if enabled;
4. proceed with the assistant flow if initialization succeeds.

If assistant initialization fails:

- the failure should be returned on the assistant request path only;
- the error should identify assistant/provider/RAG initialization failure clearly;
- core non-assistant APIs must remain available.

## Architecture Shape

### Current Shape

Current effective flow:

`get_services()` -> `build_service_container()` -> `AssistantProviderRegistry()` -> `_register_defaults()` -> `_configure_rag_wrappers()` -> `create_default_rag_index()` -> `RagIndex(...)`

This means provider and RAG side effects happen before the backend can serve any route that depends on the container.

### Target Shape

Target effective flow:

`get_services()` -> `build_service_container()` -> create assistant service with lazy provider handle -> serve core routes immediately

Then later:

- assistant route or assistant execution path calls `ensure_initialized()`
- provider registry performs one-time initialization
- RAG wrapper setup occurs inside that one-time path
- optional refresh runs lazily or in background

### Minimum Mechanism

At minimum, the provider registry or assistant service needs:

- an internal initialization state machine,
- a lock for one-time initialization,
- idempotent `ensure_initialized()` behavior,
- a clear degraded/error state for failed initialization attempts,
- optional retry semantics under explicit policy.

## Failure Policy

Lazy initialization is not enough by itself; the failure contract must also change.

### New Policy

Assistant/provider/RAG initialization failures are:

- **fatal for assistant execution in that request path**,
- **non-fatal for backend startup and non-assistant routes**.

### Logging Expectations

Initialization should emit structured logs that make it obvious whether the system is:

- not yet initialized,
- warming up,
- initialized successfully,
- initialized in degraded mode,
- failed initialization.

Examples of useful machine-readable conditions:

- `assistant_provider_init_started`
- `assistant_provider_init_succeeded`
- `assistant_provider_init_failed`
- `assistant_rag_init_failed`
- `assistant_rag_warmup_skipped`

### Retry Policy

The system should avoid repeated uncontrolled initialization attempts on every assistant request.

Preferred behavior:

- first failure is cached in memory for observability,
- subsequent assistant requests either:
  - fail fast with the cached initialization error, or
  - retry only after explicit backoff/administrative trigger.

This ADR does not require automatic retry-on-every-request.

## Consequences

### Positive

- Core job/scenario APIs stop depending on assistant/RAG runtime health at startup.
- Backend startup becomes more robust in degraded or partially configured environments.
- Test isolation improves because non-assistant tests no longer need assistant-side infrastructure to bootstrap.
- Assistant failures become easier to classify as assistant-specific rather than global backend failures.

### Tradeoffs

- Assistant initialization becomes stateful and lifecycle-aware.
- First assistant request may be slower because initialization is deferred.
- Concurrency handling becomes more important because multiple requests may race to initialize providers.
- Additional observability is required to avoid "why was my first assistant request slow?" ambiguity.

### Risks

- Incorrect locking can cause duplicate initialization or partial state.
- Lazy init can hide configuration failures until later, which may surprise operators if not logged clearly.
- Background warmup plus on-demand initialization must not race into conflicting side effects.

## Alternatives Considered

### Alternative A: Keep Eager Startup and Make RAG Failure Non-Fatal

This is the smallest resilience patch:

- keep eager provider construction,
- catch RAG init failures,
- log and continue without RAG wrappers.

Why not chosen as the primary decision:

- it narrows one failure mode, but the coupling remains;
- assistant/provider startup still executes for unrelated routes;
- future provider-side failures can still affect startup surface area unnecessarily.

This remains a valid short-term fallback or incremental step.

### Alternative B: Split the Entire Service Container into Core and Assistant Containers

This gives the strongest boundary:

- core routes use a core container,
- assistant routes use an assistant-specific container or resolver.

Why not chosen now:

- more invasive dependency rewiring,
- larger rollout surface,
- higher risk than needed for the immediate problem.

This may still become a later evolution if assistant lifecycle complexity grows.

### Alternative C: Disable RAG in Tests

This reduces immediate pain in some environments.

Why not chosen:

- test-only suppression does not solve production/runtime coupling,
- hides the architecture problem instead of fixing it.

## Implementation Plan

Implementation should be delivered in additive, reversible phases.

## Detailed Implementation Mini-Spec (Phase A/B)

This section turns the ADR decision into an implementation-ready plan for the first bounded slice: lazy provider initialization plus lazy assistant trigger points.

### Slice Goal

The first implementation slice must achieve the following:

- `build_service_container()` does not initialize assistant providers;
- `build_service_container()` does not initialize RAG wrappers or open the global RAG DB;
- first assistant execution initializes providers on demand;
- assistant initialization failure affects assistant requests only;
- non-assistant routes continue to operate when assistant initialization would fail.

### Slice Non-Goals

The following are explicitly out of scope for the first slice:

- splitting the unified service container into multiple container types;
- changing assistant tool contracts or user-visible assistant request/response schemas unless required for a clear error;
- changing RAG retrieval semantics, ranking, or index schema;
- redesigning background startup warmup beyond making it safe with lazy initialization;
- adding automatic retry-on-every-request behavior after initialization failure.

### Required Runtime Contract

After Phase A/B implementation:

1. Constructing `AssistantProviderRegistry` must perform no provider probing and no RAG DB access.
2. Constructing `AssistantService` must not implicitly initialize providers.
3. Non-assistant routes must not initialize providers indirectly.
4. The first assistant turn must call a one-time initialization path before provider/model selection.
5. If assistant initialization fails, the failure must be reported on the assistant request path only.
6. Once initialization fails, repeated assistant requests should fail fast from cached state unless an explicit retry mechanism is later added.

### Required State Model

`AssistantProviderRegistry` should gain an explicit initialization state machine.

Minimum required states:

- `uninitialized`
- `initializing`
- `ready`
- `failed`

Minimum required fields:

- `_init_state: str`
- `_init_lock: threading.Lock`
- `_init_error: Exception | None`
- `_defaults_registered: bool`

Optional but recommended fields for observability:

- `_init_started_at`
- `_init_completed_at`

State rules:

- constructor sets `uninitialized`;
- constructor must not call `_register_defaults()`;
- only `ensure_initialized()` may transition the registry out of `uninitialized`;
- `ready` must be idempotent;
- `failed` must cache the underlying error and fail fast on later assistant requests.

### Required Provider Registry API Additions

Primary file:

- `backend/services/assistant/provider_registry.py`

Required new methods:

- `ensure_initialized() -> None`
- `is_initialized() -> bool`
- `initialization_state() -> str`

Required exception type:

- `AssistantProviderInitializationError`

Expected behavior:

- `ensure_initialized()` returns immediately if state is `ready`;
- `ensure_initialized()` raises cached `AssistantProviderInitializationError` immediately if state is `failed`;
- initialization work runs under `_init_lock`;
- initialization work calls `_register_defaults()` exactly once in the success case;
- if `_register_defaults()` raises, state becomes `failed`, the underlying exception is cached, and a wrapped initialization error is raised.

Pseudo-contract:

```python
def ensure_initialized(self) -> None:
    if self._init_state == "ready":
        return
    if self._init_state == "failed" and self._init_error is not None:
        raise AssistantProviderInitializationError(...) from self._init_error
    with self._init_lock:
        if self._init_state == "ready":
            return
        if self._init_state == "failed" and self._init_error is not None:
            raise AssistantProviderInitializationError(...) from self._init_error
        self._init_state = "initializing"
        try:
            self._register_defaults()
            self._defaults_registered = True
            self._init_error = None
            self._init_state = "ready"
        except Exception as exc:
            self._init_error = exc
            self._init_state = "failed"
            raise AssistantProviderInitializationError(...) from exc
```

### Constructor Refactor Requirements

Current problem:

- `AssistantProviderRegistry.__init__` eagerly calls `_register_defaults()`.

Required change:

- remove eager `_register_defaults()` from the constructor;
- constructor may only do inert setup:
  - store config,
  - store workspace root,
  - initialize internal maps and caches,
  - initialize lock and state fields.

The constructor must not:

- resolve provider metadata from external backends;
- build RAG wrappers;
- construct `RagIndex`;
- access the RAG SQLite DB.

### Required Call-Site Updates

Any `AssistantProviderRegistry` method that assumes providers exist must ensure initialization first, unless that method is intentionally safe pre-init.

Expected methods to audit and update:

- `select_for_prompt(...)`
- `complete(...)`
- `catalog()`
- any provider/model listing surfaces used by assistant execution paths

Constraint:

- do not add `ensure_initialized()` to generic code paths used by non-assistant startup, readiness, or route bootstrap.

### Assistant Trigger Point Requirements

Primary file:

- `backend/services/assistant/assistant_service.py`

Required trigger behavior:

- call `self._providers.ensure_initialized()` at the assistant execution boundary before provider/model selection;
- do not initialize providers during session creation;
- do not initialize providers during generic service construction.

Preferred trigger location:

- near the start of `create_turn(...)`, before any provider-selection or model-tool-loop logic executes.

Failure behavior:

- catch `AssistantProviderInitializationError`;
- log the underlying failure clearly;
- return an assistant-scoped failure result for that request;
- do not propagate the exception in a way that crashes the backend process or invalidates the service container.

### Startup Warmup Requirements

Primary files:

- `backend/api/app.py`
- `backend/services/assistant/assistant_service.py`
- `backend/services/assistant/provider_registry.py`

For Phase A/B:

- startup warmup must not force assistant provider initialization as part of core service startup;
- `refresh_rag_indexes_on_startup()` must be safe when providers remain uninitialized;
- if providers are uninitialized, startup refresh should log and return rather than initializing synchronously.

Recommended short-term behavior:

- `refresh_rag_indexes_on_startup()` becomes a no-op pre-init with an informational log such as "assistant rag startup refresh skipped because providers are uninitialized."

This ADR does not require background warmup to trigger `ensure_initialized()` in Phase A/B.

### RAG-Specific Requirements

No separate RAG lifecycle refactor is required in Phase A/B if:

- `_configure_rag_wrappers()` remains inside `_register_defaults()`.

That automatically means:

- lazy provider initialization implies lazy RAG wrapper configuration;
- lazy provider initialization implies lazy global RAG DB access.

Failure policy for this slice:

- if RAG wrapper/index setup fails during lazy provider initialization, assistant initialization fails;
- non-assistant routes remain unaffected.

### Logging and Observability Requirements

Primary file:

- `backend/services/assistant/provider_registry.py`

Required logs:

- initialization start
- initialization success
- initialization failure
- startup RAG refresh skipped because providers are uninitialized

Recommended log messages:

- `assistant provider initialization started`
- `assistant provider initialization succeeded providers=%s`
- `assistant provider initialization failed error=%s`
- `assistant rag startup refresh skipped because providers are uninitialized`

Level guidance:

- `INFO` for start/success/skip
- `WARNING` for failure

### Primary Files for Phase A/B

Expected edit set:

- `backend/services/assistant/provider_registry.py`
- `backend/services/assistant/assistant_service.py`
- `backend/api/app.py` if startup refresh currently forces eager assistant work

Likely test files:

- `backend/tests/worker/test_assistant_tool_loop.py`
- new or existing provider-registry tests
- non-assistant route/service tests proving isolation

### Required Tests

Minimum regression coverage for this ADR slice:

1. Provider registry lazy-construction test
   - constructing `AssistantProviderRegistry` does not call `_register_defaults()` and does not touch the RAG DB.

2. Provider registry idempotent initialization test
   - repeated `ensure_initialized()` calls succeed and only register defaults once.

3. Provider registry cached-failure test
   - forced `_register_defaults()` failure sets state to `failed` and later `ensure_initialized()` calls fail fast.

4. Assistant lazy-init success test
   - first assistant turn initializes providers on demand and completes successfully.

5. Assistant lazy-init failure test
   - forced provider-init failure causes the assistant request to fail clearly without crashing the backend.

6. Non-assistant isolation test
   - core service-container construction succeeds even when provider initialization would fail if attempted.

7. Startup refresh pre-init no-op test
   - `refresh_rag_indexes_on_startup()` before provider initialization does not raise and emits a skip log.

### Regression Evidence Target

The motivating architecture regression should become a protected invariant:

- a job endpoint test or equivalent non-assistant bootstrap path must continue to work even when assistant/provider/RAG initialization is forced to fail.

This does not require using the exact sandbox-triggered WAL failure.
It is sufficient to inject a deterministic provider-init failure and prove:

- `build_service_container()` still succeeds;
- non-assistant route/service behavior still works.

### Implementation Order

Required implementation order:

1. Remove eager `_register_defaults()` call from `AssistantProviderRegistry.__init__`.
2. Add init state, lock, cached-error handling, and `ensure_initialized()`.
3. Update provider-registry assistant-facing methods to ensure initialization.
4. Add assistant-service trigger point before provider/model selection.
5. Make startup RAG refresh safe when providers are still uninitialized.
6. Add regression tests for lazy success, lazy failure, and non-assistant isolation.

### Phase A/B Acceptance Criteria

Phase A/B is complete when all of the following are true:

- `build_service_container()` performs no provider-default registration and no RAG DB access;
- non-assistant job/scenario routes can execute without assistant initialization;
- first assistant execution initializes providers on demand;
- assistant initialization failures are isolated to assistant request paths;
- regression tests cover lazy-init success, lazy-init failure, and non-assistant isolation.

### Phase A: Provider Registry Lazy Guard

Goal:
- prevent provider defaults and RAG setup from running in the constructor.

Primary files:
- `backend/services/assistant/provider_registry.py`
- `backend/services/assistant/assistant_service.py`

Work items:
- [ ] Move default provider registration out of `AssistantProviderRegistry.__init__`.
- [ ] Add `ensure_initialized()` with one-time lock protection.
- [ ] Track init state (`uninitialized`, `initializing`, `ready`, `failed`).
- [ ] Preserve current provider-selection and completion interfaces after initialization.

Acceptance:
- [ ] Constructing `AssistantProviderRegistry` performs no RAG DB access.
- [ ] Repeated `ensure_initialized()` calls are idempotent.

### Phase B: Assistant Service Trigger Point

Goal:
- ensure assistant requests initialize providers on demand.

Primary files:
- `backend/services/assistant/assistant_service.py`
- assistant API route wiring as needed

Work items:
- [ ] Call `ensure_initialized()` at the assistant execution boundary before provider/model selection.
- [ ] Return assistant-scoped initialization errors cleanly when initialization fails.
- [ ] Ensure non-assistant paths do not call `ensure_initialized()`.

Acceptance:
- [ ] First assistant turn initializes providers successfully in configured environments.
- [ ] Job/scenario routes do not initialize providers indirectly.

### Phase C: Lazy RAG Wrapper and Refresh Registration

Goal:
- keep RAG setup inside the lazy provider initialization path.

Primary files:
- `backend/services/assistant/provider_registry.py`
- `backend/services/assistant/rag_index.py`
- `backend/api/app.py`

Work items:
- [ ] Ensure RAG wrapper configuration runs only during provider initialization.
- [ ] Make startup RAG refresh non-blocking and no-op when providers are still uninitialized.
- [ ] If background warmup is kept, ensure it triggers lazy initialization safely and failure-tolerantly.

Acceptance:
- [ ] Backend startup does not open the global RAG DB unless assistant warmup explicitly does so.
- [ ] Assistant requests still receive RAG augmentation when enabled and available.

### Phase D: Failure Isolation and Observability

Goal:
- make degraded assistant startup understandable and safe.

Primary files:
- `backend/services/assistant/provider_registry.py`
- `backend/services/assistant/assistant_service.py`
- `backend/api/app.py`

Work items:
- [ ] Emit explicit structured logs for assistant initialization start/success/failure.
- [ ] Cache initialization failures in memory for fast repeated error reporting.
- [ ] Decide and implement retry policy (fail-fast cached vs explicit retry trigger).

Acceptance:
- [ ] Logs clearly distinguish core backend startup from assistant initialization.
- [ ] Assistant init failure does not crash job/scenario endpoints.

### Phase E: Regression Coverage

Goal:
- ensure the architecture boundary is testable and enforced.

Primary files:
- `backend/tests/worker/*`
- `backend/tests/contract/*`
- `backend/tests/integration/*`

Work items:
- [ ] Add tests proving `build_service_container()` does not initialize RAG eagerly.
- [ ] Add tests proving non-assistant job endpoints work when assistant/RAG initialization fails.
- [ ] Add tests proving first assistant request initializes providers lazily.
- [ ] Add tests proving assistant requests fail cleanly when lazy initialization fails.

Acceptance:
- [ ] A job endpoint test can run with forced assistant/RAG init failure and still pass.
- [ ] Assistant route tests cover lazy-init success and failure paths.

## Out of Scope

- Replacing the unified `ServiceContainer` with multiple dependency containers in this ADR.
- Changing assistant tool contracts or RAG retrieval semantics.
- Changing the global RAG index schema itself.
- Introducing user-visible configuration changes beyond what is needed to support lazy initialization.

## Rollback Plan

If lazy initialization introduces unacceptable regressions:

1. retain a guarded compatibility path that re-enables eager initialization by configuration or temporary code switch;
2. keep RAG failure isolation patches additive so assistant behavior can still degrade gracefully under eager mode;
3. roll back only the lifecycle trigger points first, not the observability additions.

This rollback should preserve any new logging and tests where possible, because they improve system diagnosability regardless of lifecycle model.

## Completion Definition

This ADR is complete when:

- provider and RAG initialization no longer occur during core service-container construction,
- non-assistant APIs remain available when assistant initialization fails,
- assistant requests initialize providers on demand,
- startup and assistant-init logs clearly expose lifecycle state,
- regression coverage exists for both lazy-init success and failure-isolation behavior.
