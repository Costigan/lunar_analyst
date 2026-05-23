# ADR.0054: Deterministic Segment Routing Simplification and Assistant Flag Reduction

- Status: Accepted
- Date: 2026-04-19
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`, `docs/ADR.0043.segment_intent_classification_and_product_request_extraction.md`, `docs/ADR.0048.semantic_intent_family_extraction_and_property_mapping.md`, `docs/ADR.0051.entity_reference_resolution_for_segment_processing.md`, `docs/ADR.0053.entity_kind_aware_deterministic_routing_and_domain_entity_context.md`, `docs/list-of-toggles.md`, `AGENTS.md`

## Context

Assistant routing has accumulated multiple rollout-era feature flags and legacy branches:

- hybrid router enable/disable,
- legacy parser fallback,
- semantic intent-classifier fallback via a smaller LLM,
- optional domain-entity context injection,
- optional product-recipe planning path,
- optional storage backend selection (SQLite vs JSON),
- optional provider fallback and startup prewarm behavior.

This increases implementation complexity, expands test matrix size, and leaves dead or low-value paths in active runtime code.

The target architecture is a single deterministic recognizer pipeline for segment routing, with fallback to the primary LLM only when deterministic rules do not match.

## Problem

1. Routing behavior is not constrained to one authoritative path.
2. A secondary, smaller LLM is still used for segment intent fallback classification.
3. Several config flags now represent fixed policy decisions rather than true runtime variability.
4. Optional JSON session storage backend and fallback/perf toggles add code and test burden without expected operational value.

## Decision

Adopt a simplified assistant routing contract and remove rollout-era toggles whose values are now policy-fixed.

### Normative Routing Pipeline

Per prompt segment, processing order is:

1. Divide prompt into segments.
2. Extract linguistic signals: verb phrase candidates, noun phrases, dependency/object hints.
3. Resolve entities (feature/layer/file/scenario/etc.) and ambiguity candidates.
4. Build verb operation candidates (synonym-set), not a single canonical operation.
5. Run one deterministic recognizer over:
   - verb operation candidates,
   - resolved entity kinds/targets,
   - confidence/ambiguity state,
   - optional regex/syntax constraints.
   First matching rule routes the segment.
6. If no rule matches, route the segment to the primary LLM.

### Classifier Policy

- The secondary/smaller LLM segment intent-classifier path is removed from runtime routing.
- Unmatched deterministic segments go directly to primary-LLM fallback.

### Required Always-On Behaviors

The following behaviors become mandatory, not optional:

- deterministic routing path,
- entity-kind-aware deterministic recognition,
- domain entity context injection for primary-LLM fallback,
- product recipe catalog path for create-product handling,
- SQLite assistant session store backend,
- same-provider fallback policy (`allow_cross_provider_fallback = false`),
- no provider prewarm on startup (`prewarm_on_startup = false`).

## Toggle and Flag Changes

### Removed Config Keys

The following keys are removed from active config contract and runtime wiring:

- `backend.llm.hybrid_command_router_enabled`
- `backend.llm.legacy_parser_enabled`
- `backend.llm.prompt_segmentation_model`
- `backend.llm.deterministic_agent_substeps_enabled`
- `backend.llm.create_product_recipe_catalog_enabled`
- `backend.llm.session_store_backend`
- `backend.llm.routing.entity_kind_routing_enabled`
- `backend.llm.routing.domain_entity_context_enabled`
- `backend.llm.routing.semantic_classifier_fallback_enabled`
- `backend.llm.segment_intent_classifier.provider`
- `backend.llm.segment_intent_classifier.model`
- `backend.llm.segment_intent_classifier.timeout_seconds`
- `backend.llm.performance.allow_cross_provider_fallback`
- `backend.llm.performance.prewarm_on_startup`

### Removed/Deprecated Runtime Surfaces

- Secondary segment intent-classifier prompts/schema assets and invocation path.
- Legacy parser fallback execution branch.
- JSON-vs-SQLite backend selection branch for assistant sessions.

## Scope

In scope:

- assistant routing and classification simplification,
- removal of listed config keys and dead branches,
- test/documentation updates to align with fixed policy.

Out of scope:

- provider catalog redesign,
- RAG architecture changes,
- MCP transport policy changes,
- non-assistant feature toggles (for example viewshed backend selection).

## Consequences

### Positive

- Smaller runtime decision surface and lower cognitive load.
- Reduced test matrix and fewer combinatorial feature-flag paths.
- More predictable routing and explainability.
- Lower maintenance burden from dead-code and stale config branches.

### Tradeoffs

- Fewer rollback levers via config-only toggles.
- Rollback for removed behavior requires code revert/release rollback rather than config flip. This is intentional for this phase while the system has no external users and architecture simplification is prioritized.

## Implementation Plan

1. Remove key parsing/wiring and delete dead branches in assistant dependency construction and service paths.
2. Remove secondary classifier invocation path and associated prompt/schema/config plumbing.
3. Collapse session store backend to SQLite-only construction and migration behavior.
4. Remove `allow_cross_provider_fallback` and `prewarm_on_startup` config handling; hard-code policy.
5. Remove all eliminated keys from all maintained config variants (`config/lunar_analyst.toml`, `config/lunar_analyst.devcontainer.toml`, `config/lunar_analyst.container.toml`) and related docs/examples.
6. Enforce strict handling for removed keys: if present in config, treat as invalid config (same class as unknown/malformed keys) and fail configuration validation.
7. Update tests that depended on removed flags or alternate backends.
8. Update `docs/DESIGN.md`, `docs/list-of-toggles.md`, and config examples.

## Test Impact Assessment

### Tests to Update

- `backend/tests/worker/test_assistant_tool_loop.py`
  - Remove flag-driven setup permutations for removed keys.
  - Re-baseline expectations to deterministic recognizer + primary LLM fallback only.
- `backend/tests/worker/test_assistant_hybrid_metadata.py`
  - Remove metadata/behavior assertions tied to legacy parser and semantic fallback toggles.
- `backend/tests/worker/test_deterministic_recognizer.py`
  - Re-baseline for always-on entity-kind routing path.
- `backend/tests/worker/test_prompt_classifier.py`
  - Remove semantic fallback toggle pathways and align with deterministic-first contract.
- `backend/tests/contract/test_phase6_assistant_api.py`
  - Remove config fixtures that rely on `backend.llm.enabled`/legacy backend-selection behavior if no longer accepted.
- `backend/tests/contract/test_phase6_assistant_ws.py`
  - Same as above for assistant config fixtures.
- `backend/tests/contract/test_phase6_mcp_http.py`
  - Update config fixture setup if removed keys are present.
- `backend/tests/contract/test_phase6_mcp_sse.py`
  - Update config fixture setup if removed keys are present.

### Tests to Eliminate

- Tests whose only purpose is validating removed toggles or removed branches, including:
  - legacy parser toggle behavior,
  - semantic classifier fallback toggle behavior,
  - session backend selection (`json` vs `sqlite`) behavior,
  - provider fallback/prewarm toggle behavior (`allow_cross_provider_fallback`, `prewarm_on_startup`),
  - deterministic agent-substep toggle behavior if `agent_call` deterministic gating is removed.

Concretely, remove or rewrite test cases in:

- `backend/tests/worker/test_assistant_tool_loop.py` that assert behavior changes exclusively by flipping removed flags.
- `backend/tests/worker/test_assistant_policy_service.py` only if coverage is solely tied to removed config toggles rather than mutation policy behavior itself.

## Acceptance Criteria

- Deterministic segment routing executes through one recognizer path with first-match rule semantics.
- No runtime path calls a secondary/smaller LLM for intent classification.
- Unmatched segments are routed to primary LLM fallback.
- Domain entity context is always injected in primary fallback path.
- Product recipe path is always enabled for create-product handling.
- Assistant session persistence is SQLite-only.
- No runtime references remain to removed config keys.
- Removed keys are absent from repo-maintained `.toml` files.
- Removed keys are rejected as invalid config (unknown/malformed-key class), not silently ignored.
- Updated tests pass for touched assistant behavior.

## Risks and Rollback

### Risks

- Hidden dependencies in tests or eval tooling on removed flags.
- Behavior drift in edge-case segments previously handled by semantic fallback classifier.

### Rollback Strategy

- Short-term rollback: revert ADR.0054 implementation commit(s) and redeploy previous release.
- Operational mitigation during rollout: increase logging/tracing for deterministic no-match segments and primary-fallback outcomes.

## Notes

- This ADR intentionally converts several historical feature toggles into fixed architectural policy.
- If future requirements demand restoring variability for any removed axis, introduce a new ADR with narrow, explicit justification rather than reintroducing broad rollout flags.
