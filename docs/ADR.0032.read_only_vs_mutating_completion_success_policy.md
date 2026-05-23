# ADR 0032: Read-Only vs Mutating Completion Success Policy

- Status: Accepted
- Date: 2026-03-18
- Owners: Architecture (Codex), Implementation (TBD)
- Related: `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`, `docs/ADR.0027.intent_classification_contract.md`, `docs/ADR.0028.turn_planner_json_contract.md`, `docs/ADR.0029.per_segment_execution_state_and_merge_policy.md`, `docs/ADR.0031.assistant_performance_improvement_program.md`, `docs/DESIGN.md`

## Context

Assistant turns currently risk being marked successful when a mutating user request results only in read-only tool calls or descriptive prose. This creates false positives in both user experience and automated eval scoring.

We need explicit success semantics tied to requested prompt class and verifiable postconditions.

## Decision

Adopt a normative success policy that distinguishes read-only and mutating intents and requires postcondition evidence for mutating success.

## Intent Classes

1. `read_only`
- User asks for information, explanation, listing, comparison, or analysis without requested state mutation.

2. `mutating`
- User asks for state changes, job launches, file/script writes, scenario/layer changes, or artifact creation as a required outcome.

3. `mixed`
- Turn contains both read-only and mutating required segments.

## Segment-Level Success Rules

1. `read_only` segment is `completed` when:
- requested information is returned, and
- no blocking validation/runtime error occurred.

2. `mutating` segment is `completed` only when:
- required mutation tool call executed successfully, and
- postcondition evidence confirms the state/artifact change.

3. `mutating` segment is `failed` when:
- only read-only calls occurred, or
- mutation call failed, or
- postcondition check failed/missing.

4. `blocked` segment is not success.
- It is tracked as `blocked` with clarification/recovery guidance.

## Turn-Level Aggregate Status

Use these values:

- `success`: all required segments completed.
- `partial_success`: at least one required segment completed and at least one required segment failed/blocked/skipped due to dependency failure.
- `failed`: no required segment completed, or hard failure terminated required path.

Optional segments do not determine aggregate status.

## Required vs Optional Segments

Planner must mark each segment as:

- `required=true` (default)
- `required=false` (optional follow-up/explanatory segment)

Only required segments contribute to aggregate success decision.

## Postcondition Contract

Mutating segment completion requires intent-family-specific evidence:

1. Scenario/layer state change
- Re-read authoritative state and confirm expected value.

2. Job launch/compute mutation
- Confirm run accepted plus expected artifact registration/state transition.

3. File/script mutation
- Confirm file exists at allowed location and expected metadata/registration path succeeded.

Postcondition checks must run through authoritative backend services, not model claims.

## Prohibited Success Patterns

1. Mutating request + only `tools.search`/`tools.describe`/other read-only calls -> cannot be success.
2. Mutating request + assistant prose promise without state change evidence -> cannot be success.
3. Missing postcondition check for mutating segment -> cannot be success.

## Response and Metadata Contract

For each segment, persist and expose:

- `prompt_class`
- `required`
- `status`
- `postcondition_checked` (bool)
- `postcondition_passed` (bool or null for read-only)

Turn metadata must include:

- `aggregate_status`
- `required_segment_summary`
- `failed_required_segment_ids`

## Testing Strategy

1. Unit tests
- Aggregate status computation across mixed required/optional segments.
- Prohibited success pattern checks.

2. Integration tests
- Mutating prompts fail when only read-only tools are called.
- Mutating prompts pass only after postcondition evidence is present.
- Mixed prompt returns `partial_success` when analytical segment succeeds but mutation fails (or vice versa).

3. Eval alignment
- Eval harness must use this policy as the scoring ground truth for completion semantics.

## Observability

Emit:

- `segment_completion_evaluated`
- `postcondition_checked`
- `aggregate_status_computed`

Metrics:

- mutating false-success prevention count
- postcondition failure rate
- partial success rate by prompt class

## Consequences

Positive:

- Eliminates major false-positive completion cases.
- Aligns runtime semantics with user expectations.
- Improves trustworthiness of evaluation results.

Tradeoffs:

- Additional postcondition checks and metadata handling.
- More `partial_success` outcomes that require explicit UX messaging.

## Rollout

1. Feature flag: `backend.llm.success_semantics_policy_enabled`.
2. Shadow evaluation of aggregate status alongside current status.
3. Promote to authoritative status once regression suite passes.
4. Roll back by disabling flag.

## Detailed Implementation Plan

### Phase 1: Contract Types and Classification Mapping

Goals:

1. Introduce canonical status/intent types in runtime models.
2. Ensure segment prompt class from ADR 0027 flows into completion evaluator.

Target files:

- `backend/services/assistant/turn_state_manager.py`
- `backend/services/assistant/prompt_classifier.py`
- `backend/services/assistant/models.py` (or equivalent shared assistant models)
- `backend/tests/assistant/test_success_semantics_unit.py`

Tasks:

1. Add enums/constants:
- `prompt_class`: `read_only`, `mutating`, `mixed`
- `segment_status` values from ADR 0029
- `aggregate_status`: `success`, `partial_success`, `failed`

2. Mark each segment with `required` (default true).
3. Add compatibility mapping for legacy segments without prompt class.

Acceptance:

1. Unit tests confirm deterministic mapping from classifier output to prompt class.
2. Legacy turns without segment metadata still produce valid aggregate status.

Rollback:

- Keep compatibility mapper; disable policy flag to revert aggregate decision use.

### Phase 2: Postcondition Check Integrations

Goals:

1. Add intent-family-specific postcondition verifiers for mutating segments.

Target files:

- `backend/services/assistant/postconditions.py` (new)
- `backend/services/scenario_service.py`
- `backend/services/layer_service.py`
- `backend/services/job_service.py`
- `backend/services/product_service.py`
- `backend/tests/assistant/test_postconditions.py`

Tasks:

1. Implement verifier registry keyed by mutation family:
- scenario/layer updates
- job launch/artifact creation
- file/script write flows

2. Ensure verifiers query authoritative backend state/services.
3. Return machine-readable postcondition evidence payload.

Acceptance:

1. Mutating segment marked complete only when verifier passes.
2. Missing verifier coverage returns explicit `postcondition_failed` error path.

Rollback:

- Disable policy flag; retain postcondition telemetry for debugging.

### Phase 3: Aggregate Status Evaluator

Goals:

1. Make aggregate status computation policy-authoritative.

Target files:

- `backend/services/assistant/turn_state_manager.py`
- `backend/services/assistant/assistant_service.py`
- `backend/tests/assistant/test_aggregate_status.py`

Tasks:

1. Implement required/optional segment-aware aggregation logic.
2. Enforce prohibited success patterns from this ADR.
3. Emit per-segment fields:
- `postcondition_checked`
- `postcondition_passed`

Acceptance:

1. Mutating + read-only-only path cannot return aggregate `success`.
2. Mixed required outcomes produce `partial_success` as specified.

Rollback:

- Gate evaluator behind `backend.llm.success_semantics_policy_enabled`.

### Phase 4: Response/Metadata Exposure

Goals:

1. Expose status semantics in assistant responses/events for UI and eval harness.

Target files:

- `backend/contracts/assistant_events.py`
- `backend/services/assistant/assistant_service.py`
- `backend/tests/contract/test_assistant_events.py`

Tasks:

1. Add additive fields to response metadata:
- `prompt_class`, `required`, `postcondition_checked`, `postcondition_passed`, `aggregate_status`

2. Keep schema backward compatibility.

Acceptance:

1. Contract tests pass for additive schema updates.
2. UI/eval consumers can read aggregate and per-segment status from one payload.

Rollback:

- Keep fields optional; disable flag to stop authoritative use.

## Verification Commands

1. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_success_semantics_unit.py -q"`
2. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_postconditions.py -q"`
3. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_aggregate_status.py -q"`
4. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/contract -q"`

## Exit Criteria

1. No mutating false-success cases in blocking suites.
2. Postcondition checks present for all mutating intent families in active router coverage.
3. Aggregate status parity with expected outcomes on regression replay set.

## Non-Goals

- Defining model quality of scientific narrative beyond functional completion.
- Replacing existing confirmation policy.
