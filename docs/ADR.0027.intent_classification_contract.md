# ADR 0027: Intent Classification Contract for Segment-Level Hybrid Routing

- Status: Accepted
- Date: 2026-03-18
- Owners: Architecture (Codex), Implementation (TBD)
- Related: `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`, `docs/ADR.0023.deterministic_router_with_bounded_agent_substeps.md`, `docs/ADR.0026.spacy_intent_unit_segmentation.md`, `docs/ADR.0025.assistant_eval_pyramid_for_routing_functional_and_domain_quality.md`, `docs/DESIGN.md`

## Context

ADR 0026 introduces intent-unit segmentation, but routing still requires a stable contract for classifying each segment before planning/execution. Without a formal classifier contract, behavior can drift across models/providers and make regressions hard to detect.

Current risks:

1. Mixed prompts can be misrouted when deterministic and analytical clauses are adjacent.
2. Mutating requests may be treated as read-only exploration.
3. Ambiguous segments may be auto-executed instead of requesting clarification.
4. Evaluation lacks consistent labels for routing correctness.

We need deterministic, auditable segment labels and confidence policy that are provider-agnostic.

## Decision

Adopt a rule-first, model-assisted prompt classification contract at segment granularity.

1. Every segment must receive exactly one primary route label:
- `router_candidate`
- `model_required`
- `clarification_or_policy_blocked`

2. Classification output must include confidence and rationale metadata.

3. Deterministic routing is allowed only when:
- confidence is above configured threshold,
- no complexity guard is present,
- required slots can be resolved or repaired safely,
- policy checks do not require immediate clarification.

4. Classification is advisory to the planner; final execution still enforces tool schema, confirmation policy, and pre/postconditions.

## Classification Contract

## A. Input

- Ordered segment list from ADR 0026 with offsets and guard flags.
- Current runtime context (active scenario, known layer/product names, policy state).
- Action registry metadata (intent patterns, required slots, deny patterns).

## B. Output Schema

Each segment yields:

- `segment_id` (string)
- `label` (`router_candidate` | `model_required` | `clarification_or_policy_blocked`)
- `confidence` (0.0-1.0)
- `matched_action_ids` (ordered list)
- `missing_required_slots` (list)
- `blocking_reason_code` (nullable)
- `requires_clarification` (bool)
- `classification_origin` (`rule_only` | `rule_plus_model` | `fallback`)

## C. Label Semantics

1. `router_candidate`
- Segment appears imperative and maps to an allowlisted deterministic action pattern.
- No hard policy/safety block.
- May still require confirmation at execution time for mutating actions.

2. `model_required`
- Segment needs open-ended reasoning, ambiguous tradeoff analysis, or exceeded deterministic coverage.
- Includes segments with complexity guard triggers.

3. `clarification_or_policy_blocked`
- Segment cannot proceed without user input or violates policy constraints.
- Examples: ambiguous target among multiple exact candidates, out-of-root path request, forbidden mutation class.

## D. Confidence Policy

- `deterministic_min_confidence` default: `0.80`
- `clarification_band`: `[0.60, 0.80)` for deterministic-like matches with missing/ambiguous slots
- `<0.60` routes to `model_required` unless blocked by policy

Confidence is computed from weighted signals:

- Pattern/action match quality
- Slot extraction completeness
- Name resolution certainty
- Complexity guard absence/presence
- Conflict/overlap with alternative actions

## E. Conflict Resolution

When multiple deterministic actions match:

1. Prefer highest priority action from registry.
2. Prefer actions with full required-slot resolution.
3. If tie remains, set `requires_clarification=true` and label `clarification_or_policy_blocked`.

## F. Clarification Rules

The classifier must request clarification (not guess) when:

1. Multiple valid targets remain after normalization.
2. Required slots are missing and cannot be inferred safely.
3. Segment combines mutate + analytical conditional constraints beyond deterministic scope.

## Architecture

### A. Rule-First Strategy

- Start with deterministic registry matching and slot extraction.
- Apply deny patterns and complexity guards.
- Only use model-assisted tie-breaks if enabled; never bypass hard policy guards.

### B. Policy Boundary

Classifier does not execute tools.
Execution ownership remains in deterministic executor/model tool-loop flow.

### C. Integration with Planner

- Planner consumes labels and confidence to choose segment execution mode.
- `clarification_or_policy_blocked` segments are surfaced as explicit user-facing clarification tasks.

## Observability

Emit structured classification logs:

- `segment_id`
- `label`
- `confidence`
- `matched_action_ids`
- `blocking_reason_code`
- `requires_clarification`

Metrics:

- per-label rate
- deterministic precision/recall on eval set
- clarification rate
- blocked rate by reason code

## Testing Strategy

1. Unit tests
- Label assignment for canonical prompt fragments.
- Confidence threshold behavior at boundaries.
- Conflict resolution and tie-handling.
- Clarification trigger coverage.

2. Integration tests
- Mixed prompt classification feeds planner with expected per-segment labels.
- Mutating intent never downgraded to read-only success path.

3. Eval tests (ADR 0025 alignment)
- Gold-labeled segment classification dataset.
- Report confusion matrix by label and by action family.

## Consequences

Positive:

- Stable, auditable segment routing decisions.
- Better separation of concerns between segmentation, classification, and execution.
- Stronger eval coverage for routing regressions.

Tradeoffs:

- Additional policy/config surface to maintain.
- Ongoing tuning of confidence and action priorities.

## Rollout

1. Feature flag: `backend.llm.prompt_classification_contract_enabled`.
2. Shadow classification logging alongside existing behavior.
3. Activate planner consumption after classification quality thresholds pass.
4. Rollback by disabling flag and reverting to prior planner heuristics.

## Detailed Implementation Plan

### Phase 1: Classifier Models and Rule Engine Skeleton

Goals:

1. Define canonical classifier input/output models.
2. Implement rule-first classification pipeline skeleton.

Target files:

- New module: `backend/services/assistant/prompt_classifier.py`
- `backend/services/assistant/models.py`
- `backend/tests/assistant/test_prompt_classifier_models.py`

Tasks:

1. Define output fields in ADR contract (`label`, `confidence`, etc.).
2. Add strict validation for allowed labels and ranges.

Acceptance:

1. Unit tests validate schema and serialization.
2. Invalid labels/confidence values are rejected.

Rollback:

- Disable classifier flag; preserve models as additive contract.

### Phase 2: Matching, Confidence, and Conflict Resolution

Goals:

1. Implement deterministic registry matching and scoring.
2. Enforce conflict resolution and clarification rules.

Target files:

- `backend/services/assistant/prompt_classifier.py`
- `backend/services/assistant/command_router.py`
- `backend/tests/assistant/test_prompt_classifier_rules.py`
- `backend/tests/assistant/test_prompt_classifier_conflicts.py`

Tasks:

1. Consume action registry metadata and slot extraction output.
2. Compute confidence from weighted signals.
3. Implement tie-break precedence and blocked-on-ambiguity behavior.

Acceptance:

1. Canonical prompt fragments map to expected labels.
2. Conflicting matches produce deterministic blocked/clarification results.

Rollback:

- Keep rule engine in shadow-only mode.

### Phase 3: Planner Integration and Shadow Telemetry

Goals:

1. Feed classifier output into the execution-plan/planner input contract (ADR 0028).
2. Emit detailed classification telemetry without changing authoritative behavior initially.

Target files:

- `backend/services/assistant/turn_execution_plan.py`
- `backend/services/assistant/assistant_service.py`
- `backend/tests/assistant/test_prompt_classification_shadow.py`

Tasks:

1. Add compatibility mapping for unclassified segments.
2. Emit per-segment label/confidence/reason metrics.

Acceptance:

1. Shadow mode shows stable labels on replay prompts.
2. Execution-plan builder can consume classifier output in dry-run path.

Rollback:

- Disable flag and use prior planner heuristics.

### Phase 4: Active Classification Enforcement

Goals:

1. Make classifier output authoritative for execution mode selection.

Target files:

- `backend/services/assistant/turn_execution_plan.py`
- `backend/tests/assistant/test_prompt_classification_planner_integration.py`

Tasks:

1. Enforce threshold policy (`deterministic_min_confidence`, clarification band).
2. Ensure blocked segments surface explicit clarification requirements.

Acceptance:

1. Mixed prompt suite passes with expected per-segment route labels.
2. Mutating segments are not downgraded to read-only completion paths.

Rollback:

- Revert to shadow classification by feature flag.

## Verification Commands

1. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_prompt_classifier_models.py -q"`
2. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_prompt_classifier_rules.py -q"`
3. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_prompt_classifier_conflicts.py -q"`
4. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_prompt_classification_planner_integration.py -q"`

## Exit Criteria

1. Gold-labeled classification set meets configured precision/recall.
2. Clarification trigger rate is within expected bounds and policy-compliant.
3. No critical routing regressions in blocking mixed-turn suites.

## Non-Goals

- Replacing deterministic action registry definitions.
- Executing tools from classifier stage.
- Inferring scientific correctness of analytical conclusions.
