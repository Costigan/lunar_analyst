# ADR 0033: Assistant Observability and Failure Taxonomy

- Status: Accepted
- Date: 2026-03-18
- Owners: Architecture (Codex), Implementation (TBD)
- Related: `docs/ADR.0025.assistant_eval_pyramid_for_routing_functional_and_domain_quality.md`, `docs/ADR.0028.turn_planner_json_contract.md`, `docs/ADR.0029.per_segment_execution_state_and_merge_policy.md`, `docs/ADR.0030.tool_argument_repair_policy.md`, `docs/ADR.0031.assistant_performance_improvement_program.md`, `docs/ADR.0032.read_only_vs_mutating_completion_success_policy.md`, `docs/DESIGN.md`

## Context

Assistant reliability improvements require consistent telemetry and machine-readable error semantics across segmentation, planning, tool execution, and response merge. Existing logs and errors are useful but not fully standardized for KPI tracking, alerting, and replay triage.

## Decision

Adopt a canonical observability contract and failure taxonomy for assistant turns, segments, and tool calls.

This ADR defines:

1. required correlation identifiers,
2. event schema families,
3. failure code taxonomy,
4. latency breakdown metrics,
5. redaction and logging safety rules.

## Correlation Contract

All assistant telemetry events must include:

- `session_id`
- `turn_id`
- `segment_id` (nullable for turn-level events)
- `tool_call_id` (nullable)
- `provider_request_id` (nullable)
- `timestamp_utc`
- `schema_version`

## Event Families

Required event families:

1. Routing
- `prompt_segmentation_completed`
- `prompt_classification_completed`
- `turn_execution_plan_built`
- `turn_execution_plan_validation_failed`

2. Execution
- `segment_execution_started`
- `tool_call_started`
- `tool_call_finished`
- `tool_call_repair_applied`
- `segment_execution_finished`

3. Handoff/Merge
- `deterministic_handoff_built`
- `model_continuation_started`
- `turn_merge_completed`
- `turn_status_finalized`

4. Safety/Policy
- `confirmation_requested`
- `confirmation_decided`
- `policy_blocked`

## Failure Taxonomy

Use stable machine codes grouped by domain:

1. Routing/Planning
- `routing_no_match`
- `segmentation_invalid`
- `classification_conflict`
- `turn_execution_plan_invalid_schema`
- `turn_execution_plan_invalid_dependency`

2. Validation/Repair
- `tool_args_schema_invalid`
- `tool_args_unrepairable`
- `tool_args_repair_blocked`

3. Policy/Safety
- `policy_confirmation_required`
- `policy_out_of_root_path`
- `policy_forbidden_action`
- `policy_ambiguous_target`

4. Tool/Runtime
- `tool_execution_failed`
- `tool_timeout`
- `job_launch_failed`
- `artifact_registration_failed`
- `postcondition_failed`

5. Provider/Model
- `provider_empty_completion`
- `provider_timeout`
- `provider_transport_error`
- `model_no_actionable_output`

6. Turn Synthesis
- `merge_failed`
- `turn_status_compute_failed`

Every failure event must include:

- `error_code`
- `recoverable` (bool)
- `severity` (`info` | `warning` | `error`)

## Latency Contract

Capture durations (ms) at minimum:

- `latency_segmentation_ms`
- `latency_classification_ms`
- `latency_execution_plan_ms`
- `latency_retrieval_ms`
- `latency_model_ms`
- `latency_tool_total_ms`
- `latency_merge_ms`
- `latency_turn_total_ms`

Store both per-turn values and aggregate histograms (P50/P95/P99).

## Metrics Contract

Required counters/gauges:

- turn count by aggregate status
- segment count by status and prompt class
- failure count by `error_code`
- repair attempt/success rate
- confirmation request/approve/deny rates
- mutating false-success prevention count

Label limits:

- avoid high-cardinality free-text labels,
- use enumerated codes/IDs only.

## Redaction and Data Safety

1. Do not log full large tool payloads.
2. Do not log secrets/tokens/raw credentials.
3. Prompt text logging should be configurable and redacted/sampled in production.
4. Paths in logs must be normalized and bounded to safe summaries when possible.

## Testing Strategy

1. Unit tests
- error code mapping coverage,
- required field presence in event schemas.

2. Integration tests
- known failure paths emit expected event + error code sequence.
- latency fields present for successful and failed turns.

3. Contract tests
- telemetry payload schema snapshots for stability.

## Consequences

Positive:

- Faster diagnosis and root-cause isolation.
- Stable inputs for KPI dashboards and alerting.
- Better replay/eval triage automation.

Tradeoffs:

- Added telemetry surface and maintenance.
- Need to manage volume and cardinality carefully.

## Rollout

1. Feature flag: `backend.llm.observability_contract_enabled`.
2. Emit new events in parallel with legacy logs.
3. Switch dashboards/alerts to new taxonomy.
4. Deprecate legacy ad hoc codes after parity verification.

## Detailed Implementation Plan

### Phase 1: Canonical Code Registry and Shared Types

Goals:

1. Centralize error codes and event-name constants.
2. Prevent code drift across modules.

Target files:

- New file: `backend/services/assistant/telemetry_codes.py`
- `backend/services/assistant/*`
- `backend/tests/assistant/test_telemetry_codes.py`

Tasks:

1. Define canonical enums/constants for:
- event families,
- error codes,
- severity values.

2. Add helper validators to reject unknown codes in emission path.

Acceptance:

1. Unit tests fail on unknown/error typo code usage.
2. No duplicate ad hoc error strings in touched assistant modules.

Rollback:

- Keep registry additive; disable observability flag to stop new emissions.

### Phase 2: Event Schema and Correlation Wiring

Goals:

1. Ensure required correlation fields are attached to all assistant telemetry events.

Target files:

- `backend/contracts/assistant_events.py`
- `backend/services/assistant/assistant_service.py`
- `backend/services/assistant/turn_state_manager.py`
- `backend/tests/contract/test_assistant_events.py`
- `backend/tests/assistant/test_telemetry_correlation.py`

Tasks:

1. Add correlation envelope builder:
- `session_id`, `turn_id`, `segment_id`, `tool_call_id`, `provider_request_id`, `timestamp_utc`, `schema_version`

2. Update event emitters to use envelope consistently.
3. Keep schema changes additive and backward compatible.

Acceptance:

1. Contract tests validate required field presence for all event families.
2. Integration tests show traceable event chains across one mixed turn.

Rollback:

- Keep old emitters in compatibility path behind flag.

### Phase 3: Failure Mapping and Latency Timers

Goals:

1. Map known runtime failures to canonical taxonomy.
2. Emit stage-level latency breakdowns.

Target files:

- `backend/services/assistant/assistant_service.py`
- `backend/services/assistant/command_router.py`
- `backend/services/assistant/turn_execution_plan.py`
- `backend/services/assistant/tool_execution.py` (or equivalent tool boundary)
- `backend/tests/assistant/test_failure_code_mapping.py`
- `backend/tests/assistant/test_latency_fields.py`

Tasks:

1. Add exception-to-error-code mapping layer.
2. Add timers for required latency buckets.
3. Ensure failure events include `recoverable` and `severity`.

Acceptance:

1. Injected failure scenarios emit expected canonical codes.
2. Successful and failed turns both include latency fields.

Rollback:

- Use legacy code mapping when flag disabled.

### Phase 4: Metrics Export and Redaction Guards

Goals:

1. Export stable counters/histograms.
2. Enforce logging redaction policy.

Target files:

- `backend/services/assistant/telemetry.py`
- `backend/services/assistant/logging_utils.py` (new or existing)
- `backend/tests/assistant/test_redaction_policy.py`

Tasks:

1. Implement metric emitters for required counters/histograms.
2. Add payload compaction/redaction helpers:
- truncate large tool payloads,
- redact sensitive keys,
- normalize path summaries.

Acceptance:

1. Metrics emitted with bounded label cardinality.
2. Redaction tests confirm sensitive/materially large payloads are not logged verbatim.

Rollback:

- Metrics/report path can be report-only; disable contract flag for authoritative dependency.

## Verification Commands

1. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_telemetry_codes.py -q"`
2. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_telemetry_correlation.py -q"`
3. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_failure_code_mapping.py -q"`
4. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_latency_fields.py -q"`
5. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_redaction_policy.py -q"`
6. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/contract -q"`

## Exit Criteria

1. 100% of emitted failure events use canonical error codes.
2. 100% of turn-finalized events include required latency fields.
3. Dashboards/alerts run on canonical taxonomy with no critical gaps vs legacy telemetry.

## Non-Goals

- Selecting a specific external observability vendor.
- Storing full prompt/tool payload histories in high-volume telemetry streams.
