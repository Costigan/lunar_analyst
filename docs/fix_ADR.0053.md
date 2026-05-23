# Fix Plan: ADR.0053 Deterministic Routing Implementation Gaps

## Problem Summary

The current implementation still violates the intended ADR.0053 flow in three key ways:

1. Classification still runs before full entity-aware deterministic recognition.
2. Deterministic recognition is split across multiple paths (command-regex path vs later typed promotion path).
3. Verb handling collapses too early to a single `canonical_operation`, instead of carrying verb-synonym operation candidates and resolving jointly with entity kinds.

This causes cases like `Zoom to Mons Mouton.` to hit semantic classification when they should be deterministically resolved first.

## Intended End-State

Per segment, processing must be:

1. Segment prompt.
2. Extract linguistic signals: verb phrase candidates, noun phrases, dependency/object hints.
3. Resolve entities (feature/layer/file/scenario/etc.) and ambiguity candidates.
4. Build verb operation candidates (synonym-set), not a single canonical operation.
5. Run one deterministic recognizer that evaluates rules using:
   - verb operation candidates,
   - resolved entity kinds/targets,
   - confidence/ambiguity state,
   - optional regex/syntax constraints.
6. If no deterministic route is valid, run semantic classifier/model fallback.

No separate command-vs-typed deterministic branches.

## Scope of Fix

### 1) Introduce a Unified Deterministic Recognizer

- Add a new recognizer module (or equivalent service methods) that is the single deterministic decision engine.
- Inputs:
  - segment text + offsets
  - linguistic extraction payload (verb phrase(s), object hints)
  - entity resolution payload (typed mentions + ambiguities)
  - prior-segment disambiguation memory
- Output:
  - `deterministic_result` with one of:
    - `planned_tool_steps`
    - `clarification_required`
    - `no_match`
  - decision trace (`matched_rule_id`, `reason`, `blocked_reason`)

### 2) Replace Early Canonicalization with Candidate-Based Verb Semantics

- Refactor verb normalization so it can emit operation candidates:
  - example: a phrase like `show` may map to multiple operation candidates depending on object role and entity type context.
- Keep current alias tables, but consume them as candidate generators.
- Final operation selection happens inside rule evaluation, not before it.

### 3) Move Entity Resolution Before Deterministic Recognition

- In `AssistantService` turn prep, reorder pipeline:
  - provisional/untyped segment labels only (or minimal structure),
  - entity resolution,
  - unified deterministic recognizer,
  - semantic fallback only for `no_match`.
- Remove semantic-classifier dependency for deterministic-covered traffic.

### 4) Remove Split Deterministic Branches

- Eliminate dual path semantics:
  - command-router-specific classification branch
  - separate typed-intent promotion branch
- Keep one deterministic recognizer that can produce either:
  - direct tool-step plans, or
  - intent-family mapped tool plans, but via one decision engine.

### 5) Cross-Segment Ambiguity Consistency

- Maintain turn-scoped memory:
  - resolved mention bindings (`kind + normalized_ref -> resolved_id`)
  - disambiguation selections
- Apply memory before requesting clarification on later segments.
- Clarification answers update this memory for subsequent segment processing.

### 6) Diagnostics and Transparency

- Update `scripts/show_prompt_plans.py` to reflect the new single recognizer stages:
  - show verb operation candidates,
  - show entity candidates/ambiguities,
  - show recognizer rule evaluation summary,
  - show explicit fallback reason when semantic classifier is used.
- Correct classifier-use reporting to be origin-based (`extractor_*` only).

## File-Level Change Plan

- `backend/services/assistant/assistant_service.py`
  - Reorder segment pipeline.
  - Replace dual deterministic promotion logic with unified recognizer invocation.
- `backend/services/assistant/prompt_classifier.py`
  - Convert to fallback classifier role only (for unmatched segments).
  - Remove deterministic gating logic based on `is_imperative_candidate`.
- `backend/services/assistant/entity_reference_resolver.py`
  - Keep early entity resolution and ambiguity metadata.
  - Preserve/extend prior-segment binding behavior.
- `backend/services/assistant/verb_normalizer.py` (or new module)
  - Emit operation candidates from synonym sets.
- `backend/services/assistant/action_router_config.py`
  - Ensure rule schema supports candidate-based verb matching and precedence.
- `config/assistant_action_router.yaml`
  - Keep verb aliases and typed rules, but enforce rule-order semantics for one recognizer.
- `scripts/show_prompt_plans.py`
  - Reflect unified recognizer stages and fallback boundaries.

## Test Plan

### Unit

- `test_prompt_classifier.py`
  - Ensure semantic classifier is not called for deterministic-recognizable segments.
  - Ensure semantic classifier is called only for recognizer `no_match`.
- New recognizer tests
  - Verify rule selection with verb candidate sets and entity kinds.
  - Verify tie/ambiguity handling.
- `test_verb_and_entity_resolution.py`
  - Verify verb candidate emission and entity-target selection.

### Worker/Integration

- `test_assistant_tool_loop.py`
  - `Zoom to Mons Mouton.` routes deterministically without semantic classifier.
  - `Show Mons Mouton.` resolves via joint verb/entity rule selection.
  - ambiguous `show` layer/file prompts force clarification consistently.
- Multi-segment tests
  - earlier disambiguation influences later segment resolution consistently.

## Acceptance Criteria

1. `Zoom to Mons Mouton.` does not invoke semantic classifier and deterministically plans `location.goto`.
2. Deterministic recognition uses one component and one rule-evaluation process.
3. Operation decision uses verb-candidate + entity-kind joint evaluation (not early fixed canonical op).
4. Semantic classifier is invoked only when deterministic recognizer returns `no_match`.
5. Diagnostics clearly show when/why fallback happened.

## Risks and Mitigations

- Risk: behavior drift for existing command regex cases.
  - Mitigation: migrate regex constraints into recognizer rule checks and keep regression corpus.
- Risk: complexity in refactor.
  - Mitigation: implement behind temporary feature flag, compare old/new recognizer decisions in logs during validation.
- Risk: ambiguity handling regressions.
  - Mitigation: add turn-level memory tests and clarification consistency fixtures.

## Rollout Steps

1. Land unified recognizer and pipeline reorder behind a feature flag.
2. Add decision-trace logging and run comparison tests.
3. Flip default to unified recognizer after regressions are clean.
4. Remove legacy split-path code.
