# ADR.0051: Entity Reference Resolution for Segment Processing

- Status: Accepted
- Date: 2026-04-13
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0011.ai_assistant_and_mcp.md`, `docs/ADR.0035.typed_entity_memory_and_reference_resolution_v1.md`, `docs/ADR.0048.semantic_intent_family_extraction_and_property_mapping.md`, `docs/ADR.0050.local_lunar_nomenclature_and_feature_navigation.md`

## Context

Prompt segmentation and intent-family classification are now first-class assistant behaviors. The assistant can map some segments to deterministic tools, but entity references inside segments are still mostly free text (for example feature names, scenario references, layer/file names, and pronouns like "it").

This creates avoidable ambiguity:

- deterministic paths may receive unresolved or weakly resolved references,
- model-loop paths may miss available local context that could be injected safely,
- same-turn pronouns across segments ("Find Shackleton Crater and zoom to it") are not guaranteed to bind deterministically.

## Problem

1. **Entity Strings Are Under-Specified**: Segment text often contains references to known entities, but those are not normalized early into stable identities.
2. **Ambiguity Is Not Uniformly Managed**: Different execution paths may resolve names differently or too late.
3. **Limited Observability**: We do not have a consistent trace of "what reference was resolved, how, and why."

## Decision

Introduce a **minimal, deterministic, bounded Entity Reference Resolver** in the segment-processing pipeline.

### Decision Summary

1. Add a resolver step that runs per segment and emits structured entity references.
2. Resolve high-value domain kinds in v1:
   - `feature`
   - `scenario`
   - `layer`
   - `file`
   - `colormap`
3. Define an explicit expansion set for later phases:
   - `product` (derived artifacts by `product_id`/type)
   - `job` (run/job references for logs, status, cancel)
   - `notebook` (named scripts/notebooks)
   - `tool` (explicit tool-name references in command prompts)
   - `coordinate` (point/extent references)
   - `time_window` (date/range references for temporal operations)
   - `marker_or_pin` (user pins/waypoints)
   - `dataset_or_source` (catalog/source dataset references)
   - `recipe` (canonical compute recipe references)
4. Use exact-match-first with bounded fuzzy fallback and explicit ambiguity handling.
5. Support same-turn pronoun binding (`it`, `that`, `there`) only to entities resolved earlier in the same turn, with strict tie-breaking.
6. Keep behavior deterministic: same input/state must produce the same resolution output.
7. Expose resolution traces in turn metadata and logs for debugging and regression tests.
8. Make deterministic dispatch depend on both:
   - verb/object action pattern, and
   - resolved entity kind(s) (and ambiguity state).
9. Add a canonical verb-normalization step so deterministic dispatch is robust to synonym phrasing.
10. Keep verb alias configuration in a single source of truth consumed by router/planner paths.

## Non-Goals

- general-purpose noun/POS tagging,
- open-ended NER/coreference research pipeline,
- unbounded cross-session memory resolution,
- autonomous disambiguation beyond configured confidence/ambiguity thresholds.

## Resolver Contract (Normative)

Resolver input:

- segment text,
- segment intent classification,
- active scenario context,
- bounded recent-turn entity state.

Resolver output:

```json
{
  "mentions": [
    {
      "kind": "feature",
      "mention_text": "Shackleton Crater",
      "normalized_ref": "shackleton crater",
      "strategy": "exact",
      "resolved_id": "feature:1234",
      "confidence": 1.0,
      "candidates": []
    }
  ],
  "ambiguities": [],
  "errors": []
}
```

Rules:

1. `strategy` is one of `exact|alias|fuzzy|pronoun_from_turn_state`.
2. Fuzzy candidate list is capped (v1: max 3).
3. If ambiguity remains after deterministic ranking, mark ambiguity and require clarification.
4. Resolver must not mutate global state; only emits structured references and same-turn bindings.

## Pipeline Order (Normative)

Per-segment processing order is fixed:

1. segment classification,
2. canonical verb normalization,
3. entity reference resolution,
4. verb + entity-kind compatibility gating,
5. deterministic dispatch or clarification/model-loop fallback.

Implementations must not reorder these stages without ADR revision.

## Execution-Path Usage

1. **Deterministic intent-family path**
   - Prefer `resolved_id` over raw text when building tool arguments.
   - Normalize action verbs to canonical operations before deterministic dispatch checks.
   - Deterministic dispatch eligibility requires compatible verb/object pattern + resolved entity kind(s).
   - If required reference remains ambiguous, return clarification instead of executing.

2. **Model-loop path**
   - Inject only compact high-confidence references relevant to the segment.
   - Do not inject full candidate catalogs; include top bounded candidates only.

3. **Same-turn pronouns**
   - Bind pronouns only to entities resolved in prior segments of the same turn (or explicit configured short window).
   - If multiple compatible candidates exist, require clarification.

## Deterministic Dispatch Gating (Normative)

Deterministic dispatch must be gated by both syntactic intent and semantic entity typing.

Required checks:

1. The segment matches a supported verb/object pattern (for example `goto`, `find`, `identify`, `set_current`, `show/hide`, `apply_colormap`).
2. Resolver returns entity kind(s) compatible with that pattern (for example `goto` + `feature`; `set_current` + `scenario`; `apply_colormap` + `layer` and/or `colormap`).
3. No unresolved ambiguity for required entities.

If any required check fails, deterministic dispatch must not execute mutating/navigation steps directly; it must either:

- request clarification, or
- route through model-loop with bounded resolver context.

## Verb Normalization (Normative)

Deterministic dispatch must use canonical verb normalization before final action selection.

Rules:

1. Normalize candidate action phrasing to a canonical verb/operation label (for example `goto`, `search`, `show`, `hide`, `set_current`, `apply`, `identify`, `nearby`).
2. Maintain an explicit alias map (synonym/phrase -> canonical operation), including common multi-word verbs.
3. Apply normalization before verb+entity-kind compatibility checks.
4. If multiple canonical operations remain plausible after normalization, treat as ambiguous and require clarification.
5. Unknown/unsupported verbs must not execute deterministic mutation/navigation actions; route to clarification or model-loop.

Configuration source of truth:

- Canonical verb alias mapping must be maintained in one shared configuration source.
- Deterministic router/planner code paths must consume canonical operation labels from that same source.

Illustrative alias set (v1, non-exhaustive):

- `goto`: `go to`, `zoom to`, `center on`, `navigate to`
- `search`: `find`, `look up`, `search for`
- `show`: `turn on`, `display`, `reveal`
- `hide`: `turn off`, `conceal`
- `set_current`: `switch to`, `use`, `select`
- `apply`: `set`, `use` (style context only)

## Determinism and Bounds

The resolver is constrained by policy constants (v1 defaults):

- allowed entity kinds: fixed set above,
- fuzzy candidate cap: 3,
- per-kind lookup limits: fixed,
- pronoun binding window: current turn prior segments only,
- no probabilistic model calls in resolver path.

Ambiguity thresholds (v1 defaults):

- exact match confidence: `1.0` (eligible for deterministic dispatch),
- fuzzy dispatch minimum confidence: `0.90`,
- clarification required when top-two fuzzy candidates are within `0.05` score delta,
- clarification required when pronoun binding has more than one compatible candidate at equal precedence.

## Observability

For each segment, persist and log:

- mention text and kind,
- strategy used,
- candidates considered (bounded),
- selected entity and confidence,
- ambiguity/clarification reason codes.

Recommended reason codes:

- `entity_exact_match`
- `entity_fuzzy_match`
- `entity_pronoun_bound_last_feature`
- `entity_ambiguous_requires_clarification`
- `entity_no_match`

## Risks and Mitigations

- **Risk**: Wrong silent binding.
  - **Mitigation**: exact-first policy, bounded fuzzy, explicit ambiguity gate.
- **Risk**: Complexity creep into NLP platform work.
  - **Mitigation**: strict non-goals; fixed entity kinds and bounded behavior.
- **Risk**: Prompt-context bloat in model-loop mode.
  - **Mitigation**: compact injection format and relevance filtering.

## Implementation Plan

### Phase 1: Resolver Core
- [ ] Add resolver module and typed output models.
- [ ] Implement per-kind exact/fuzzy lookup adapters for v1 kinds.
- [ ] Implement same-turn pronoun binding with deterministic precedence.

### Phase 2: Assistant Integration
- [ ] Run resolver after segment classification, before deterministic/model dispatch.
- [ ] Wire deterministic tool argument building to prefer resolved identities.
- [ ] Add deterministic dispatch gating by verb/object pattern + compatible entity kind(s).
- [ ] Add canonical verb-normalization layer and alias map for deterministic paths.
- [ ] Wire model-loop context builder to inject compact resolved references.

### Phase 3: Observability and Tooling
- [ ] Persist resolver trace in turn metadata.
- [ ] Add structured logs and reason codes.
- [ ] Update `scripts/show_prompt_segmentation.py` and `scripts/show_prompt_plans.py` to display resolver output and ambiguity decisions.

### Phase 4: Tests
- [ ] Add worker tests for exact/fuzzy precedence and ambiguity gating.
- [ ] Add tests for same-turn pronoun resolution across segments.
- [ ] Add contract tests that confirm deterministic path blocks on unresolved ambiguity.
- [ ] Add synonym/alias tests that prove canonical verb normalization drives deterministic dispatch decisions.

### Phase 5: Entity-Kind Expansion
- [ ] Add expansion entity kinds incrementally (`product`, `job`, `notebook`, `tool`, `coordinate`, `time_window`, `marker_or_pin`, `dataset_or_source`, `recipe`).
- [ ] Add per-kind matching policy (exact aliases, bounded fuzzy, ambiguity thresholds) and tests before enabling each kind by default.

### Phase 6: Readiness Metrics
- [ ] Add evaluation metrics and a small goldens corpus for deterministic-dispatch precision/recall by family.
- [ ] Track:
  - wrong-binding regression count,
  - clarification rate for ambiguous references,
  - deterministic-dispatch precision for segments eligible under gating.

## Definition of Done

- [ ] Resolver output is present in segment metadata for processed turns.
- [ ] Deterministic paths consume resolved identities when available.
- [ ] Deterministic dispatch rejects segments whose resolved entity kinds are incompatible with the selected verb/object pattern.
- [ ] Deterministic dispatch uses canonical verb normalization and passes synonym regression cases.
- [ ] Same-turn pronoun case (`Find Shackleton Crater and zoom to it`) resolves correctly or asks clarification when ambiguous.
- [ ] Model-loop prompt augmentation uses compact bounded entity references.
- [ ] `scripts/show_prompt_segmentation.py` and `scripts/show_prompt_plans.py` show resolver traces.
- [ ] `docs/DESIGN.md` includes a cross-reference to ADR 0051.
- [ ] Readiness metrics are emitted and reviewed for rollout.
