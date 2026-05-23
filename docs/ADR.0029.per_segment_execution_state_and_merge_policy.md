# ADR 0029: Per-Segment Execution State and Merge Policy for Hybrid Assistant Turns

- Status: Accepted
- Date: 2026-03-18
- Owners: Architecture (Codex), Implementation (TBD)
- Related: `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`, `docs/ADR.0023.deterministic_router_with_bounded_agent_substeps.md`, `docs/ADR.0026.spacy_intent_unit_segmentation.md`, `docs/ADR.0027.intent_classification_contract.md`, `docs/ADR.0028.turn_planner_json_contract.md`, `docs/ADR.0025.assistant_eval_pyramid_for_routing_functional_and_domain_quality.md`, `docs/DESIGN.md`

## Context

ADRs 0026-0028 define segmentation, classification, and planner/execution-plan contracts. We still need a formal runtime state and merge policy for mixed turns where deterministic and LLM-executed segments coexist.

Without a state/merge contract, the system risks:

1. duplicate work (LLM redoing deterministic actions),
2. stale context (LLM not seeing scenario/layer changes),
3. inconsistent user-facing status across segments,
4. weak replayability and difficult eval assertions.

We need deterministic rules for state transitions, handoff summaries, and final response composition.

## Decision

Adopt a per-segment execution state model (`turn_state`) and explicit merge policy for mixed hybrid turns.

1. Maintain segment-scoped runtime status and outputs for every segment.
2. Build a compact deterministic summary for LLM continuation, never raw large tool payloads.
3. Merge deterministic and LLM segment outcomes into one ordered response contract.
4. Persist a compact auditable state record for replay/eval.

## Execution State Contract

## A. Top-Level Turn State

```json
{
  "schema_version": "1.0",
  "turn_id": "string",
  "session_id": "string",
  "segments": [],
  "global_effects": {},
  "handoff_to_llm": {},
  "final_merge": {},
  "state_status": "in_progress"
}
```

## B. Segment State Entry

```json
{
  "segment_id": "s2",
  "execution_mode": "deterministic",
  "status": "completed",
  "started_at_utc": "ISO-8601",
  "completed_at_utc": "ISO-8601",
  "tool_calls": [],
  "artifacts": [],
  "state_effects": {},
  "error": null,
  "requires_user_input": false
}
```

`status` values:

- `pending`
- `running`
- `completed`
- `failed`
- `blocked`
- `skipped_dependency_failed`
- `skipped_already_satisfied`

## C. State Effects

Segment state effects capture normalized deltas only:

- `scenario_change` (`from_scenario_id`, `to_scenario_id`)
- `layer_visibility_changes` (layer IDs + visibility booleans)
- `created_artifact_refs` (`file_id`, `relative_path`, `artifact_kind`)
- `updated_policy_flags` (if applicable)

No full raster payloads or large inventories in `turn_state`.

## D. Error Object

```json
{
  "code": "string",
  "message": "string",
  "recoverable": true,
  "suggested_recovery": "string"
}
```

Use stable machine codes; keep message concise and user-safe.

## Deterministic-to-LLM Handoff Policy

## A. Handoff Payload

Before LLM continuation, construct `handoff_to_llm`:

```json
{
  "unresolved_segment_ids": ["s3"],
  "deterministic_summary": [],
  "active_scenario_id": "string",
  "active_scenario_directory": "string",
  "artifact_refs": [],
  "blocked_segments": []
}
```

`deterministic_summary` entries should include:

- `segment_id`
- `result_kind` (`state_change`, `artifact_created`, `read_only_info`, `failed`)
- compact `details`

## B. Handoff Constraints

1. Do not include full tool output blobs.
2. Do not include redundant schema dumps; use focused tool discovery flow.
3. Include only unresolved segment text for continuation prompt.
4. Include blocked segments so LLM does not fabricate completion for blocked operations.

## C. No Redo Rule

LLM continuation prompt must instruct:

- deterministic-completed segments are complete and should not be re-executed,
- blocked segments require clarification/recovery, not silent workaround.

## Merge Policy

## A. Merge Ordering

Final response preserves original segment order regardless of execution mode.

## B. Merge Entry Format

Each segment produces a user-facing entry:

- `segment_id`
- `text`
- `execution_mode`
- `status`
- `summary`
- `artifact_refs` (if any)
- `error_code` (if failed/blocked)

## C. Mixed-Turn Response Rules

1. Deterministic successes are reported as completed actions.
2. LLM sections focus on unresolved analytical/requested reasoning.
3. Failed/blocked segments are explicit with next step guidance.
4. Do not collapse failures into generic success prose.

## D. Completion Semantics

Turn-level success is not binary by default. Use aggregate status:

- `success`: all required segments completed.
- `partial_success`: at least one required segment completed, at least one failed/blocked.
- `failed`: no required segments completed or hard failure terminated turn.

## Dependency and Recovery Policy

1. If segment `B` depends on `A` and `A` fails, mark `B` as `skipped_dependency_failed`.
2. Independent segments may continue when policy allows.
3. Recovery suggestions must be tied to failure code and segment context.
4. Mutating failures cannot be silently transformed into read-only completions.

## Persistence and Replay

Persist compact `turn_state` with turn metadata.

Replay requirements:

1. Deterministic segment ordering and statuses are reproducible from state.
2. Merge output can be reconstructed from `turn_state` + messages.
3. Eval harness can assert per-segment expected status and postconditions.

## Observability

Structured events:

- `segment_state_changed`
- `deterministic_handoff_built`
- `merge_completed`
- `turn_aggregate_status_set`

Metrics:

- per-status segment rates
- partial success rate
- redo-attempt rate (LLM attempted completed deterministic segment)
- blocked-to-clarified conversion rate

## Testing Strategy

1. Unit tests
- state transition validity (no illegal transitions),
- merge ordering and status rendering,
- aggregate status computation.

2. Integration tests
- mixed prompt with deterministic + LLM segments merges correctly.
- dependency failures mark downstream segments correctly.
- blocked segments remain explicit through final response.

3. Eval tests
- assert per-segment outcomes and aggregate status.
- verify handoff payload excludes oversized tool output.

## Consequences

Positive:

- Consistent mixed-turn behavior and user-visible status.
- Better replay/debug/contract testing.
- Lower risk of LLM duplicate actions after deterministic execution.

Tradeoffs:

- Additional runtime state model and persistence logic.
- Need to maintain compacting rules as tool payloads evolve.

## Rollout

1. Feature flag: `backend.llm.segment_state_merge_policy_enabled`.
2. Shadow mode: compute `turn_state` and merged draft while current response path remains authoritative.
3. Promote after routing/eval parity is confirmed.
4. Roll back via feature flag disable.

## Detailed Implementation Plan

### Phase 1: State Model and Transition Engine

Goals:

1. Implement canonical `turn_state` and legal transition rules.

Target files:

- New module: `backend/services/assistant/turn_state_manager.py`
- `backend/services/assistant/models.py`
- `backend/tests/assistant/test_turn_state_transitions.py`

Tasks:

1. Define top-level and segment state objects.
2. Implement guarded transition API (reject illegal state jumps).
3. Add normalized state-effects and error payload schema.

Acceptance:

1. Unit tests validate allowed/forbidden transitions.
2. State objects serialize deterministically for persistence/replay.

Rollback:

- Keep state manager inactive behind feature flag.

### Phase 2: Deterministic Execution Recording

Goals:

1. Record deterministic segment/tool outputs into `turn_state`.

Target files:

- `backend/services/assistant/assistant_service.py`
- deterministic executor module (`backend/services/assistant/command_router.py` or equivalent)
- `backend/tests/assistant/test_turn_state_deterministic_recording.py`

Tasks:

1. Attach tool call summaries and state deltas to segment state.
2. Record artifact refs and authoritative scenario/layer deltas.
3. Record failures with stable codes and recoverability metadata.

Acceptance:

1. Integration tests confirm deterministic actions produce expected state deltas.
2. Failures are segment-scoped and do not corrupt global turn state.

Rollback:

- Disable state recording while retaining runtime execution.

### Phase 3: LLM Handoff Builder and No-Redo Enforcement

Goals:

1. Build compact handoff for unresolved segments.
2. Prevent LLM from re-executing completed deterministic segments.

Target files:

- `backend/services/assistant/turn_state_manager.py`
- `backend/services/assistant/assistant_service.py`
- `backend/tests/assistant/test_turn_state_handoff.py`

Tasks:

1. Implement `handoff_to_llm` payload construction with compaction limits.
2. Include blocked segments and deterministic summary in continuation context.
3. Add prompt-level no-redo instructions and runtime safeguard checks.

Acceptance:

1. Handoff payload excludes oversized raw tool blobs.
2. Replay/integration tests show no duplicate deterministic execution attempts.

Rollback:

- Disable merge policy flag and use legacy continuation context assembly.

### Phase 4: Final Merge and Aggregate Status Exposure

Goals:

1. Merge segment outputs by original order with explicit statuses.

Target files:

- `backend/services/assistant/assistant_service.py`
- `backend/contracts/assistant_events.py`
- `backend/tests/assistant/test_turn_merge_policy.py`
- `backend/tests/contract/test_assistant_events.py`

Tasks:

1. Implement merge formatter with per-segment entries.
2. Compute turn aggregate status using ADR 0032 semantics (when enabled).
3. Emit merge lifecycle telemetry events.

Acceptance:

1. Mixed-turn outputs preserve order and explicit failure/blocked entries.
2. Contract tests pass with additive metadata fields.

Rollback:

- Fallback to legacy response synthesis path.

## Verification Commands

1. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_turn_state_transitions.py -q"`
2. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_turn_state_deterministic_recording.py -q"`
3. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_turn_state_handoff.py -q"`
4. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_turn_merge_policy.py -q"`
5. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/contract -q"`

## Exit Criteria

1. Per-segment states and merged outcomes are replayable and deterministic.
2. No-redo policy holds across mixed-turn regression suite.
3. Blocked/dependency-skipped segments are surfaced consistently in output and metadata.

## Non-Goals

- Defining UI layout or frontend rendering details beyond response contract fields.
- Replacing assistant session persistence model.
- Expanding to autonomous multi-turn plan execution.
