# ADR.0038: Agent Reliability Policy for `raster.transform` and Internal Eval Pre-Filter

- Status: Accepted
- Date: 2026-03-28
- Owners: Lunar Analyst architecture team
- Related: `docs/ADR.0016.map_algebra_dsl.md`, `docs/ADR.0018.scripted_map_algebra.md`, `docs/ADR.0019.unified_tool_model.md`, `docs/ADR.0025.assistant_eval_pyramid_for_routing_functional_and_domain_quality.md`

## Context

Agent performance on raster authoring is bounded less by raw model intelligence and more by contract friction:

1. The most model-familiar syntax is Python/NumPy-like, but strict subsets create predictable failure modes (`and/or` vs elementwise logic, unsupported call spellings, temporal-binding ambiguity).
2. `raster.transform` is the correct governed execution surface for expressive raster logic, but guidance currently allows multiple equivalent input styles that increase model confusion.
3. Existing eval scoring captures turn outcomes, but does not separate "syntax/contract ineligible" attempts from "eligible but execution failed" attempts early enough for model-comparison debugging.

We need higher reliability without weakening safety boundaries.

## Decision

### 1) Canonical Agent Authoring Surface

`raster.transform` is the canonical agent-facing raster authoring surface for multi-step analytical transforms.

- `raster.calculate` remains supported as a compatibility/simple-expression path.
- Model-facing guidance, few-shots, and eval fixtures should bias to `raster.transform` unless a case explicitly requires expression-only behavior.

### 2) No Blind BoolOp Rewrite

We will **not** perform unconditional rewrites of `and/or/not` to elementwise array logic.

Rationale:
- Blind rewrite can change intended scalar semantics.
- Superficial AST rewriting is unsafe without reliable type evidence.

Policy:
- Keep strict parser behavior.
- Return explicit, repair-oriented error details when BoolOps are used in array contexts.

### 3) Minimal Safe NumPy Facade

To leverage model prior while preserving safety, we allow a minimal, sealed NumPy naming facade in the restricted runtime.

Initial target:
- Accept normalized spellings like `np.where` in addition to existing sealed calls.
- Keep imports disabled and builtins sealed.
- Reserve facade identifiers (for example `np`) so scripts cannot shadow them.

This is a parser/runtime allowlist expansion, not general Python execution.

### 4) Canonical Temporal Binding Contract for Agents

For model-facing contracts and examples, temporal requests use one canonical pattern:

- reserved `times` binding (with `start_utc`, `stop_utc`, `step_hours`)
- temporal inputs via `temporal_source`

Legacy signal-style bindings and top-level temporal fields remain runtime-compatible, but are treated as compatibility-only and are not the preferred generated pattern for agents/evals.

### 5) Repair-Oriented Error Payloads

`raster_transform_*` validation and contract errors should include deterministic repair hints, for example:

- nearest allowed function names on `unknown_function`
- explicit "use `&` / `|` with parentheses" guidance on BoolOp failures
- explicit "assign final value to `result`" guidance on missing-result failures
- temporal binding mismatch guidance (`times` vs top-level fields)

### 6) Internal Eval Pre-Filter Validator (Not a Tool)

Introduce a **non-public pre-filter validator** for benchmark/eval runs.

Purpose:
- classify agent outputs as contract-eligible before mutating execution
- separate syntax/contract failures from runtime/tool failures in benchmarking

Scope:
- parse/validate script
- validate raster/temporal input binding shape
- validate temporal-domain consistency
- build plan and enforce configured limits

Non-governance constraints:
- not listed in assistant tool catalog
- not exposed through MCP tool discovery
- not callable as a user-facing assistant tool
- intended for eval harness and offline analysis pipelines only

### 7) Model-Facing Examples as First-Class Contract Artifacts

Add short canonical examples ("copy-this templates") for:

- single-expression transform
- multi-statement transform with `result = ...`
- temporal transform with reserved `times` binding and reducer

These examples are normative model guidance artifacts and should be versioned with contract changes.

### 8) Eval Reliability Metrics

Extend assistant benchmarks to track at minimum:

- pre-filter eligibility rate
- first-attempt eligible-and-executed success rate
- correction-loop success after one repair turn
- category-level failure taxonomy (syntax, contract, planning-limit, runtime)

## Consequences

Positive:
- Better agent first-try success by aligning syntax with strong model priors where safe.
- Clear separation between model contract formation failures and backend execution failures.
- Reduced ambiguity for temporal transforms by teaching one canonical binding model.

Tradeoffs:
- Additional maintenance for normalized spellings and error-hint taxonomy.
- Eval harness complexity increases due to pre-filter stage and extra reporting dimensions.

## Out of Scope

- Replacing `raster.calculate` runtime compatibility in this ADR.
- Enabling unrestricted Python, imports, or arbitrary NumPy APIs in `raster.transform`.
- Automatic semantic rewriting that depends on full program type inference.
- Public exposure of pre-filter validation as an assistant/MCP tool.

## Implementation Plan

Implementation is delivered in additive vertical slices with explicit acceptance gates.

### Phase A: Internal Pre-Filter Validator

Goal:
- Add a non-public validator that classifies `raster.transform` requests as contract-eligible before execution.

Primary files:
- `backend/jobs/raster_transform.py`
- `backend/jobs/handlers.py` (internal helper wiring only, no public tool exposure)
- `backend/evals/assistant/run_benchmark.py`
- `backend/evals/assistant/benchmark_core.py`

Work items:
- [x] Add an internal validator entry point that performs:
  - parse/validate script;
  - validate input-binding shape (`relative_path`/`product_id`/`times`/`temporal_source`);
  - validate temporal-domain consistency (`times` binding vs top-level `time_*`);
  - build plan + enforce plan limits.
- [x] Return structured validator output with:
  - `eligible: bool`;
  - `failure_stage` (`parse_validate`, `binding_validate`, `build_plan`, `estimate_resources`);
  - canonical error payload (`code`, `message`, `details`);
  - planner summary when available.
- [x] Ensure validator does not write outputs, launch jobs, or mutate scenario state.
- [x] Add clear naming to emphasize internal usage (for example `prefilter` in symbols).

Acceptance:
- [x] Deterministic pre-filter result for invalid scripts/inputs without launching execution.
- [x] Zero artifact writes and zero DB mutation for validator calls.
- [x] Pre-filter callable from eval harness code paths.

### Phase B: Keep Validator Non-Public

Goal:
- Guarantee pre-filter cannot be called as a user-facing assistant/MCP tool.

Primary files:
- `backend/services/assistant/tool_registry.py`
- canonical catalog/discovery code in `backend/analyst_tools/*` as needed
- contract tests under `backend/tests/contract/*`

Work items:
- [x] Add contract tests that assert pre-filter symbols are absent from:
  - assistant tool list;
  - MCP tool discovery;
  - UI-facing tool discovery endpoints.
- [x] Add explicit guardrails/comments in code to prevent accidental registration.

Acceptance:
- [x] Discovery payloads do not expose any pre-filter action/tool.
- [x] Failing test coverage exists for accidental exposure regression.

### Phase C: Repair-Oriented Diagnostics

Goal:
- Improve correction success without weakening syntax safety.

Primary files:
- `backend/jobs/raster_transform.py`
- `backend/jobs/handlers.py` (error translation passthrough only)

Work items:
- [x] Add repair hints for common validation failures:
  - BoolOp misuse (`and`/`or`) -> elementwise guidance with parentheses;
  - missing `result` assignment;
  - unknown function with nearest allowed names;
  - temporal binding mismatch (`times` vs top-level fields).
- [x] Preserve stable error codes; add hints in `details` fields.
- [x] Ensure hints are deterministic and testable.

Acceptance:
- [x] Existing error code taxonomy remains stable.
- [x] New hint fields appear for targeted failure classes.
- [x] Unit tests verify hints for each targeted class.

### Phase D: Minimal Sealed NumPy Facade

Goal:
- Improve model familiarity while preserving sandbox constraints.

Primary files:
- `backend/jobs/raster_transform.py`
- docs/prompt artifacts for transform usage examples

Work items:
- [x] Add minimal facade support for selected aliases (initially `np.where`).
- [x] Keep imports disallowed and `__builtins__` sealed.
- [x] Reserve facade identifier(s) (for example `np`) to prevent user shadowing.
- [x] Do not add broad NumPy function exposure in this phase.

Acceptance:
- [x] Canonical and aliased spellings produce equivalent validated/evaluated output.
- [x] Shadowing/reserved-name violations return deterministic validation errors.
- [x] No import path or unrestricted function execution is introduced.

### Phase E: Canonical Temporal Contract Guidance

Goal:
- Use one temporal pattern in model-facing artifacts to reduce ambiguity.

Primary files:
- `docs/AGENT_PROMPT.md`
- `docs/rag_corpus/guidance_map_algebra_scripts.txt`
- any model-facing tool-description source used to generate assistant prompt context

Work items:
- [x] Update examples to use reserved `times` binding + `temporal_source`.
- [x] Mark legacy `signal` and top-level `time_*` as compatibility-only in guidance text.
- [x] Add exactly three short canonical templates:
  - single-expression script;
  - multi-statement script with `result`;
  - temporal script using `times` + reducer.

Acceptance:
- [x] All model-facing temporal examples follow the canonical `times` pattern.
- [x] Legacy compatibility remains in runtime but not promoted in primary examples.

### Phase F: Eval Harness Integration and Scoring

Goal:
- Integrate pre-filter classification into benchmark output and scoring.

Primary files:
- `backend/evals/assistant/run_benchmark.py`
- `backend/evals/assistant/benchmark_core.py`
- `backend/evals/assistant/leaderboard.py`
- `backend/evals/assistant/score.py`
- `backend/evals/assistant/README.md`

Work items:
- [x] Run pre-filter before mutating execution for applicable `raster.transform` cases.
- [x] Persist per-case pre-filter fields:
  - `prefilter_eligible`;
  - `prefilter_failure_stage`;
  - `prefilter_error_code`.
- [x] Add summary metrics:
  - pre-filter eligibility rate;
  - first-attempt eligible+executed success rate;
  - one-repair-loop recovery rate.
- [x] Add leaderboard/report columns for eligibility taxonomy.

Acceptance:
- [x] Benchmark artifacts include pre-filter diagnostics for applicable cases.
- [x] Score outputs include eligibility metrics in deterministic JSON fields.
- [x] Documentation explains how to interpret new metrics.

### Phase G: Regression Gates and CI

Goal:
- Prevent reliability regressions after rollout.

Primary files:
- benchmark/scoring CI configuration and related docs

Work items:
- [x] Add gates for minimum eligibility rate and minimum first-attempt eligible success.
- [x] Add non-exposure contract gate for pre-filter (assistant/MCP discovery).
- [x] Add parser/validator unit tests for canonical failure/hint classes.

Acceptance:
- [x] CI fails on pre-filter exposure regression.
- [x] CI fails on reliability metrics below configured thresholds.
- [x] Regression baseline snapshots are versioned.

## Implementation Checklist

- [x] Phase A complete: internal pre-filter validator implemented with deterministic output.
- [x] Phase B complete: pre-filter guaranteed non-public with discovery contract tests.
- [x] Phase C complete: repair-oriented error hints implemented and tested.
- [x] Phase D complete: minimal sealed NumPy facade implemented safely.
- [x] Phase E complete: canonical temporal guidance and templates published.
- [x] Phase F complete: eval harness emits eligibility taxonomy and metrics.
- [x] Phase G complete: CI gates enforce exposure and reliability thresholds.
- [x] Backward compatibility validated for existing `raster.transform` runtime inputs.
- [x] Rollback path validated (disable pre-filter stage in eval runner via config/flag if needed).

## Validation Requirements

- Pre-filter is unavailable from assistant tool discovery and MCP catalog.
- Benchmarks can report eligibility independently from runtime execution status.
- Parser diagnostics include actionable repair hints for top failure classes.
- Canonical temporal examples use reserved `times` binding only.

## Supersession and Deprecation Proposal

This ADR supersedes prior guidance in specific areas:

1. `docs/ADR.0016.map_algebra_dsl.md`
- Superseded scope: agent-primary authoring recommendation for expressive raster workflows.
- **Proposed status update:** `Deprecated (compatibility-only path for agent authoring)`.
- Note: runtime support for `raster.calculate` remains; deprecation is about preferred agent workflow/guidance, not immediate removal.

2. `docs/ADR.0018.scripted_map_algebra.md`
- Superseded scope: reliability strategy details that did not define an eval pre-filter stage or canonical single temporal guidance for model-facing artifacts.
- **Proposed status update:** keep active, mark as `Amended by ADR.0038` (not deprecated).

3. `docs/ADR.0025.assistant_eval_pyramid_for_routing_functional_and_domain_quality.md`
- Superseded scope: eval pipeline details for raster-transform benchmarking.
- **Proposed status update:** keep active, add "amended by ADR.0038" note for pre-filter eligibility taxonomy.
