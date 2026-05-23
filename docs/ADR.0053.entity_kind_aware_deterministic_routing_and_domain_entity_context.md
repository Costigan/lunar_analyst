# ADR.0053: Entity-Kind-Aware Deterministic Routing and Domain Entity Context for LLM Fallback

- Status: Accepted
- Date: 2026-04-17
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`, `docs/ADR.0026.spacy_intent_unit_segmentation.md`, `docs/ADR.0048.semantic_intent_family_extraction_and_property_mapping.md`, `docs/ADR.0050.local_lunar_nomenclature_and_feature_navigation.md`, `docs/ADR.0051.entity_reference_resolution_for_segment_processing.md`

## Context

The current hybrid pipeline supports deterministic routing for some command patterns and semantic intent-family mapping for others. In practice, routing quality for short imperative prompts depends on **what noun phrase the verb applies to**.

Examples:

- `show mons mouton` should navigate to a feature.
- `show the slope layer` should update layer visibility.
- `show slope.tif` should show a layer mapped from a file (or import/create then show).
- `show the slope` is ambiguous (`layer` vs `file` vs product-like reference) and should clarify.

Today, these cases can be inconsistently handled because deterministic matching does not always incorporate typed entity resolution and object-role interpretation as first-class routing inputs.

## Problem

1. Deterministic routing is primarily pattern-driven and does not always condition on resolved entity kind(s).
2. Verb handling is split across configuration and downstream logic, which weakens explainability and testability.
3. Primary LLM/tool-loop prompts for `other` segments do not consistently receive compact, structured entity-resolution context.
4. We rely on a small intent-classification LLM for traffic that can be handled deterministically with stronger symbolic routing.

## Decision

Adopt an **entity-kind-aware deterministic router flow** with bounded grammar/parse signals and explicit primary-LLM context injection for `other`/model-loop segments.

### Normative Pipeline

Per segment, processing order is:

1. **Segmentation** (unchanged).
2. **spaCy extraction** of candidate verb + noun phrases + lightweight dependency roles (including direct-object signal).
3. **Entity resolution** for noun phrases (feature, layer, file, scenario, etc.).
4. **Verb canonicalization** using shared alias map (`goto`, `show`, `hide`, ...).
5. **Entity-object targeting**: resolve likely target object(s), preferring direct-object signal when available.
6. **Typed deterministic routing** by rule matrix using canonical verb + target entity kind + ambiguity/confidence state.
7. If no deterministic route is valid, execute the **primary LLM/tool-loop path** with `DOMAIN_ENTITY_CONTEXT` + unchanged user query.

## Architectural Rules

1. Dependency parse signals (direct object, head) are **advisory**, not hard requirements.
2. Deterministic routing must require:
   - canonical verb,
   - compatible target entity kind,
   - non-ambiguous required entity reference(s).
3. Ambiguous references must trigger clarification, not silent execution.
4. Case-insensitive matching remains required end-to-end.
5. Variant-attempt entity lookup is preferred over destructive normalization for multi-word names.
6. Semantic intent-classifier LLM remains as controlled fallback during rollout, behind config flag.

## Verb/Entity Routing Semantics (Initial)

Illustrative initial mappings:

- `goto` + `feature` -> `location.goto`
- `show` + `layer` -> `layer.update_state(visible=true)`
- `hide` + `layer` -> `layer.update_state(visible=false)`
- `show` + `file` -> `layer.show_or_add_from_file` behavior (compose existing tools)
- `show` + ambiguous(`layer|file`) -> clarification
- `show` + `feature` -> `location.goto` (navigation interpretation)

These mappings are policy and should live in versioned config.

## Deterministic Rule Configuration

Introduce typed rule entries in action-router config (YAML) before introducing a new DSL.

Rule schema extension (conceptual):

- `required_verbs`: canonical operation list
- `required_entity_kinds`: target kind list
- `min_confidence`: threshold for deterministic execution
- `allow_ambiguity`: default `false`
- `resolution_strategy`: named strategy for tool-arg projection
- `tool_plan`: one or more tool steps, templated by resolved entity metadata

Regex `patterns` may remain as supplemental constraints, but final selection is typed-rule-based.

## Primary LLM Context Contract (`other`/model-loop segments)

When deterministic route is not selected (for example `other` segments or segments explicitly deferred to model loop), the assistant prompt for the **primary LLM** must include:

- `<DOMAIN_ENTITY_CONTEXT>` with bounded structured entities:
  - `mention_text`, `kind`, `resolved_id`, `confidence`, `reason_code`
  - top candidates when ambiguous (capped)
- `<USER_QUERY>` containing the original segment text unchanged

### Required Prompt Instruction

Add to main system prompt:

`User prompts may include <DOMAIN_ENTITY_CONTEXT>. Use it for precise grounded reasoning. Do not invent or contradict provided entity details.`

This instruction is for the **primary LLM system prompt**, not the small intent-classifier prompt.

## Data Model Additions

Add/extend typed models for:

- extracted segment linguistic signals (`verb_candidates`, `noun_phrases`, `direct_object_candidate`)
- routed target entity (`target_kind`, `target_mention`, `target_resolved_id`)
- deterministic routing decision trace (`matched_rule_id`, `blocked_reason`, `clarification_reason`)
- primary-LLM context payload (`domain_entity_context`)

## Phased Implementation Plan

### Phase 0: Baseline Instrumentation (No Behavior Change)

Scope:

- Add observability for verb/object/entity signals in current pipeline.

Exact files:

- `backend/services/assistant/assistant_service.py`
- `backend/services/assistant/entity_reference_resolver.py`
- `backend/services/assistant/verb_normalizer.py`
- `backend/services/assistant/schemas/` (event payload schema updates as needed)

Acceptance tests:

- `backend/tests/worker/test_assistant_hybrid_metadata.py`
- Add assertions that segment metadata includes canonical verb, direct-object candidate, and resolved entity summary.

Pass criteria:

- Existing deterministic/model routing behavior unchanged.
- New metadata present for representative prompts.

### Phase 1: Typed Target Selection and Routing Matrix (Deterministic Only)

Scope:

- Implement target selection from resolved noun phrases.
- Add entity-kind-aware routing checks for existing operations (`goto`, `show`, `hide`).
- Keep semantic intent-classifier LLM as fallback.

Exact files:

- `backend/services/assistant/assistant_service.py`
- `backend/services/assistant/intent_to_tool_planner.py`
- `backend/services/assistant/entity_reference_resolver.py`
- `backend/services/assistant/command_router.py`
- `backend/services/assistant/action_router_config.py`
- `config/assistant_action_router.yaml`

Acceptance tests:

- `backend/tests/worker/test_intent_to_tool_planner.py` (new/extended)
- `backend/tests/worker/test_assistant_tool_loop.py` (new deterministic dispatch cases)
- `backend/tests/worker/test_feature_resolution_variants.py` (retain/extend)

Required cases:

- `show mons mouton` -> deterministic `location.goto(feature_id=...)`
- `show the slope layer` -> deterministic `layer.update_state(visible=true)`
- `show slope.tif` -> deterministic file->layer behavior path (existing-layer case)
- `show the slope` -> clarification when unresolved ambiguity remains

### Phase 2: File-to-Layer Visibility Workflow

Scope:

- Deterministic workflow for file references:
  - resolve existing corresponding layer, else import/register layer, then set visible.

Exact files:

- `backend/services/assistant/intent_to_tool_planner.py`
- `backend/services/assistant/tool_registry.py`
- `backend/api/dependencies.py` (if helper/wiring needed)
- `backend/contracts/models.py` (if any contract shape additions)

Acceptance tests:

- `backend/tests/worker/test_assistant_tool_loop.py`
- `backend/tests/integration/` add workflow-level test for file->layer show path.

Required cases:

- `show slope.tif` with pre-existing layer -> no duplicate layer creation
- `show slope.tif` without layer -> import/register then visible=true
- clear error/clarification when file does not exist in scenario root

### Phase 3: Primary LLM Context Injection for `other`/model-loop Segments

Scope:

- Inject `<DOMAIN_ENTITY_CONTEXT>` in the primary LLM/tool-loop prompt path for `other` and other model-loop segments.
- Add prompt template and bounded entity serialization policy.

Exact files:

- `backend/services/assistant/assistant_service.py`
- `backend/services/assistant/prompts/` (system prompt text update)
- `backend/services/assistant/session_store*.py` (if storing compact context traces)

Acceptance tests:

- `backend/tests/worker/test_assistant_tool_loop.py`
- `backend/tests/worker/test_segmentation_non_command_extraction_fixtures.py` (if fixtures include prompt text)

Required assertions:

- Primary-model prompt contains `<DOMAIN_ENTITY_CONTEXT>` and `<USER_QUERY>` wrappers.
- Query text remains unchanged in `<USER_QUERY>`.
- Entity payload is bounded and includes confidence + ambiguity candidates when relevant.
- Small intent-classifier prompt payload does not include the primary-model wrapper contract.

### Phase 4: Classifier Dependency Reduction

Scope:

- Route covered verb/entity patterns without small intent-classifier LLM.
- Keep classifier as opt-in fallback for uncovered families.

Exact files:

- `backend/services/assistant/assistant_service.py`
- `backend/services/assistant/prompt_classifier.py`
- `backend/services/assistant/segment_intent_extractor.py`
- `config/*.toml` and/or `config/assistant_action_router.yaml` feature flags

Acceptance tests:

- `backend/tests/worker/test_assistant_tool_loop.py`
- `backend/tests/worker/test_segmentation_classification_invariants.py`
- `backend/tests/contract/` routing/metadata contract tests as needed

Required outcomes:

- Covered deterministic cases execute correctly with classifier disabled.
- Uncovered cases still reach safe fallback behavior.
- No regression in clarification for ambiguous prompts.

## Rollout Controls

Feature flags (suggested):

- `assistant.entity_kind_routing_enabled`
- `assistant.domain_entity_context_enabled`
- `assistant.semantic_classifier_fallback_enabled`

Default rollout:

1. Enable Phase 0 + 1 in dev.
2. Enable Phase 2 behind flag for targeted testing.
3. Enable Phase 3 in dev and evaluate prompt quality/cost.
4. Reduce classifier dependency in staged environments after eval gates.

## Acceptance Test Matrix (End-State)

Core deterministic tests:

- `show mons mouton` -> `location.goto`
- `goto mons mouton` -> `location.goto`
- `show the slope layer` -> layer visible true
- `hide the slope layer` -> layer visible false
- `show slope.tif` -> show existing layer OR add+show
- `show the slope` -> clarification with candidates

Ambiguity and safety:

- equal-confidence layer/file matches -> clarification
- unresolved feature-like noun phrase -> clarification or bounded fallback
- out-of-root file references rejected

Primary-LLM context quality:

- `<DOMAIN_ENTITY_CONTEXT>` present in primary LLM/tool-loop path for `other`/deferred segments
- includes resolved and ambiguous entities with bounded candidates
- no contradiction between entity context and user query text

## Risks and Mitigations

- Risk: Overfitting deterministic rules to narrow phrasing.
  - Mitigation: typed routing matrix + regression corpus + bounded fallback.
- Risk: False confidence on noisy parse signals.
  - Mitigation: treat dependency roles as hints; require entity confidence/compatibility.
- Risk: Tool-plan complexity for file->layer behavior.
  - Mitigation: explicit tested workflow helper and idempotent layer lookup.
- Risk: Prompt token growth from entity context.
  - Mitigation: strict caps and relevance filtering.

## Out of Scope

- Full grammar parser or general semantic parser replacement.
- Cross-turn long-horizon entity memory redesign.
- New external database schema for alias expansion (handled separately).

## Consequences

Positive:

- Higher deterministic hit rate for common mission-analysis prompts.
- Fewer model calls for routable operational commands.
- Better transparency via explicit routing traces and bounded entity context.

Negative:

- More routing configuration and planner complexity.
- Additional integration tests required to maintain confidence.
