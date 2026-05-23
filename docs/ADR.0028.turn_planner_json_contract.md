# ADR 0028: Turn Execution Plan JSON Contract for Mixed Deterministic and LLM Execution

- Status: Accepted
- Date: 2026-03-18
- Updated: 2026-04-05
- Owners: Architecture (Codex), Implementation (backend assistant runtime)
- Related: `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`, `docs/ADR.0023.deterministic_router_with_bounded_agent_substeps.md`, `docs/ADR.0026.spacy_intent_unit_segmentation.md`, `docs/ADR.0027.intent_classification_contract.md`, `docs/ADR.0029.per_segment_execution_state_and_merge_policy.md`, `docs/ADR.0033.assistant_observability_and_failure_taxonomy.md`, `docs/DESIGN.md`

## Context

Hybrid routing already supports:

1. deterministic execution for matched imperative segments,
2. model continuation for unmatched or higher-order segments,
3. explicit blocked/clarification outcomes for ambiguous cases.

The runtime needed a compact, versioned artifact that records the executable shape of a turn after segmentation/classification/routing have already happened.

The original ADR language used the term "planner" broadly. In the current implementation, that term must be interpreted narrowly:

- it is not a search-based planner,
- it does not generate alternatives,
- it does not score competing plans,
- it does not decide the authoritative deterministic step sequence.

Instead, it is a canonical execution-structure handoff artifact derived from prior segmentation/classification/routing results.

## Decision

Adopt a versioned turn execution plan JSON contract as the canonical per-turn execution-structure document for:

- ordered segment execution modes,
- compact step metadata,
- runtime seed state,
- blocked/clarification surfacing,
- replay/eval/merge metadata.

For implementation clarity, the internal code now names this artifact an execution plan:

- builder/module: `backend/services/assistant/turn_execution_plan.py`
- types: `TurnExecutionPlanBuilder`, `TurnExecutionPlanDocument`

Public contract and observability names now use execution-plan terminology consistently:

- WS events: `turn_execution_plan_built`, `turn_execution_plan_validation_failed`
- metadata key: `execution_plan_segments`
- error codes: `turn_execution_plan_invalid_*`

## Current Runtime Semantics

The authoritative execution flow is:

1. Segment the prompt into ordered prompt segments.
2. Classify each segment as `router_candidate`, `model_required`, or `clarification_or_policy_blocked`.
3. Build the execution-plan document from those classifications.
4. Execute deterministic segments through the existing `HybridCommandRouter` action plan.
5. Continue unmatched or unresolved work through the model tool-loop when applicable.
6. Build per-segment turn-state merge metadata and aggregate success metadata.

Important implementation boundary:

- The deterministic action sequence is still authored by the router/action plan.
- The execution-plan document mirrors the executable turn structure; it is not the runtime authority that replaces the router.

## Execution Plan Contract

## A. Top-Level Schema

```json
{
  "schema_version": "1.0",
  "turn_id": "string",
  "session_id": "string",
  "prompt_text": "string",
  "segments": [],
  "execution_policy": {},
  "runtime_state_seed": {},
  "execution_plan_status": "planned"
}
```

Required:

- `schema_version`
- `turn_id`
- `segments`
- `execution_policy`

## B. Segment Entry

```json
{
  "segment_id": "s1",
  "text": "Switch to Shackleton scenario",
  "start_char": 0,
  "end_char": 30,
  "classification": {
    "label": "router_candidate",
    "confidence": 0.94,
    "blocking_reason_code": null,
    "requires_clarification": false
  },
  "execution_mode": "deterministic",
  "dependencies": [],
  "planned_steps": [],
  "required_inputs": [],
  "expected_postconditions": [],
  "status": "pending"
}
```

`execution_mode` values:

- `deterministic`
- `llm`
- `blocked`

`status` values:

- `pending`
- `running`
- `completed`
- `failed`
- `blocked`
- `skipped_dependency_failed`

## C. Planned Step Entry

```json
{
  "step_id": "s1.step1",
  "kind": "tool_call",
  "tool_name": null,
  "action_id": "scenario.switch",
  "status": "pending"
}
```

Current implementation notes:

- `planned_steps` are compact and action-oriented.
- `action_id` is currently populated from classifier/router matches.
- `tool_name` is not currently resolved into the document.
- `dependencies`, `required_inputs`, and `expected_postconditions` exist in the schema but are not yet populated with non-empty values by the runtime.

## D. Execution Policy

```json
{
  "max_deterministic_steps": 12,
  "allow_partial_deterministic_execution": true,
  "allow_model_continuation": true,
  "stop_on_hard_failure": false,
  "mutating_requires_confirmation": true
}
```

## E. Runtime State Seed

Current seed contents are intentionally compact:

- `active_scenario_id`
- `active_scenario_directory`

Large inventories, tool payloads, and rich runtime state are excluded.

## Validation Rules

Current server-side validation enforces:

1. `schema_version` compatibility,
2. unique segment IDs,
3. ordered non-overlapping segment offsets,
4. valid `execution_mode` enum values,
5. `clarification_or_policy_blocked` classification must map to `execution_mode=blocked`.

Invalid documents fail fast with machine-readable errors:

- `turn_execution_plan_invalid_schema`
- `turn_execution_plan_invalid_dependency`

The broader validation set from the original proposal, including populated dependency DAG checks and registered tool-name validation in planned steps, is not implemented today.

## Execution and Continuation Semantics

The runtime behavior corresponding to the current code is:

1. Deterministic segments execute through `HybridCommandRouter` action plans.
2. If only part of the turn is deterministically matched, unmatched text is handed to model continuation as a follow-up prompt.
3. Per-segment turn state records deterministic completion, blocked segments, and model-completed LLM segments.
4. A compact handoff summary is built for observability/merge metadata.
5. Final assistant metadata includes `execution_plan_segments`, aggregate turn status, segment outcomes, and `turn_state_merge`.

Important constraint:

- The `handoff_to_llm` structure is currently runtime bookkeeping/metadata, not a fully authoritative continuation contract that the model loop consumes directly.

## Consequences

Positive:

- One versioned artifact records the mixed-turn execution structure.
- Replay/eval metadata is more stable than ad hoc runtime heuristics alone.
- Blocked/clarification segments remain explicit instead of being silently collapsed.

Tradeoffs:

- The term `planner` can still mislead readers unless interpreted narrowly.
- Some schema fields are intentionally present ahead of fuller population/validation.
- Deterministic authority remains split: router for execution, execution-plan document for structured turn metadata.

## Naming Guidance

When reading or extending this architecture:

- use "planner" only in the narrow sense of "the component that materializes the executable turn structure from prior routing/classification decisions";
- do not describe it as a search planner unless the implementation actually gains alternative generation, scoring, or selection logic;
- prefer "execution plan" or "orchestration artifact" when discussing the internal code path and runtime role.

## Implementation Mapping

Current implementation files:

- `backend/services/assistant/turn_execution_plan.py`
- `backend/services/assistant/assistant_service.py`
- `backend/services/assistant/turn_state_manager.py`
- `backend/tests/worker/test_turn_execution_plan.py`

## Verification Commands

1. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/worker/test_turn_execution_plan.py -q"`
2. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/worker/test_assistant_hybrid_metadata.py -q"`
3. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/worker/test_assistant_tool_loop.py -q"`
