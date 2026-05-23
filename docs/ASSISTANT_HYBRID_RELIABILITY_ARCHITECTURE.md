# Assistant Hybrid Reliability Architecture

## Purpose
This document describes the current Lunar Analyst assistant runtime architecture for processing user prompts with deterministic reliability and model-based reasoning in the same turn.

It focuses on:
- prompt segmentation into prompt segments,
- segment classification and planning,
- deterministic and model execution flow,
- argument repair, success semantics, and observability.

## Scope and Invariants
- Assistant orchestration runs in FastAPI service code under `backend/services/assistant/`.
- Tool execution remains centralized through `backend/services/assistant/tool_registry.py` and existing backend services.
- Mutating actions still obey confirmation policy and filesystem/root safety invariants.
- Compute ownership remains in established job handlers/tool implementations.

## End-to-End Turn Flow
For each turn (`create_turn`), processing is:

1. Prompt intake and context resolution.
- Determine active scenario context.
- Create user message and turn record.

2. Prompt segmentation.
- Implemented by `PromptSegmenter` (`backend/services/assistant/prompt_segmenter.py`).
- Produces ordered prompt segments with:
  - `segment_id`
  - `text`
  - `start_char`/`end_char`
  - `is_imperative_candidate`
  - `has_complexity_guard`
  - `segmentation_confidence`

3. Prompt classification.
- Implemented by `PromptClassifier` (`backend/services/assistant/prompt_classifier.py`).
- Labels each segment as:
  - `router_candidate`
  - `model_required`
  - `clarification_or_policy_blocked`
- Uses router matching, complexity guards, and confidence thresholds.

4. Turn execution-plan construction.
- Implemented by `TurnExecutionPlanBuilder` (`backend/services/assistant/turn_execution_plan.py`).
- Builds a `schema_version=1.0` planner/execution-plan document with per-segment execution mode:
  - `deterministic`
  - `llm`
  - `blocked`
- Performs planner validation before use.
- This artifact is derived from prior classification/routing results; it is not a search planner.

5. Per-segment execution state initialization.
- Implemented by `TurnStateManager` (`backend/services/assistant/turn_state_manager.py`).
- Creates turn-local state for segment statuses and later merge output.

6. Deterministic execution path.
- Existing `HybridCommandRouter` action plan remains the authoritative deterministic executor and executes deterministic segments first.
- Tool calls are emitted/recorded with standard proposal/start/completion events.
- Per-segment deterministic completions are recorded in turn state.

7. Model continuation for unresolved segments.
- If unmatched/LLM-required segments remain, model tool-loop is invoked with updated context.
- Deterministic outcomes are retained and unresolved segments are completed in model path.

8. Argument repair before tool invocation.
- Implemented by `ToolArgumentRepairer` (`backend/services/assistant/tool_argument_repair.py`).
- Applies bounded repairs (alias mapping, defaults, enum/path normalization).
- Blocks unsafe path traversal and produces clarification-required outcomes when needed.

9. Merge and success finalization.
- Per-segment merge is generated from `TurnStateManager`.
- Success semantics are computed by `compute_success_semantics` (`backend/services/assistant/success_semantics.py`):
  - per-segment prompt class and postcondition fields
  - aggregate status: `success`, `partial_success`, `failed`
- Final assistant message metadata includes `execution_plan_segments`, segment outcomes, aggregate status, and merged segment summary.

10. Turn completion and persistence.
- Turn usage includes latency breakdown values.
- Turn/messages/tool-calls persist via assistant session store.

## Prompt Segmentation Details
Segmentation is a two-stage approach:

1. Sentence boundary detection.
- Uses the configured spaCy model (`prompt_segmentation_model`, default `en_core_web_sm`).
- spaCy and the configured model are required for the segmenter to initialize.

2. Clause-level split for orchestration connectors.
- Splits on connectors such as `then`, `and then`, `also`, `next`, `after that`.
- Suppresses aggressive splitting when complexity markers are present (`if`, `unless`, `compare`, etc.).

Post-processing:
- merge tiny fragments,
- preserve original order and offsets,
- compute confidence and imperative/complexity signals per segment.

## Routing and Execution Behavior
- Deterministic-first is default: matched imperative units execute before model reasoning.
- Mixed prompts are handled in one turn:
  - deterministic segments execute,
  - unresolved segments continue through model loop,
  - final response merges both result types in segment order.
- `blocked` segments are not silently executed; they remain explicit in metadata/merge output.

## Observability and Failure Taxonomy
Canonical event names are defined in `backend/contracts/assistant_events.py`.
Canonical failure/error code constants are defined in `backend/services/assistant/telemetry_codes.py`.

Observed telemetry includes:
- segmentation/classification/planner lifecycle events,
- per-turn latency breakdown fields,
- tool execution failures with machine-readable error codes,
- merge and final status events.

## Configuration Variables
Primary assistant settings are under `[backend.llm]` in `config/lunar_analyst.toml`.

Most relevant to this architecture:
- `hybrid_command_router_enabled`
- `legacy_parser_enabled`
- `deterministic_agent_substeps_enabled`
- `action_router_spec_path`
- `system_prompt_path`
- `prompt_segmentation_model`
- `require_confirmation_for_mutations`
- `session_store_backend`
- `session_store_path`
- `session_store_legacy_json_path`
- `max_context_tokens`
- `default_max_output_tokens`

Related sections:
- `[backend.llm.performance]` for model loop limits/fallback behavior.
- `[backend.llm.rag]` for channel-aware retrieval/index behavior.
- `[backend.llm.evals]` for benchmark default provider/model.

## Evaluation and Scoring
The evaluation contract and scoring policy are documented in:
- `docs/ASSISTANT_EVAL_SPEC.md`

Current scorer implementation:
- `backend/evals/assistant/score.py`

Scoring includes:
- weighted component scores,
- mandatory-fail safety overrides,
- suite-level gate evaluation.

## Key Implementation Files
- `backend/services/assistant/assistant_service.py`
- `backend/services/assistant/command_router.py`
- `backend/services/assistant/prompt_segmenter.py`
- `backend/services/assistant/prompt_classifier.py`
- `backend/services/assistant/turn_execution_plan.py`
- `backend/services/assistant/turn_state_manager.py`
- `backend/services/assistant/tool_argument_repair.py`
- `backend/services/assistant/success_semantics.py`
- `backend/services/assistant/telemetry_codes.py`
- `backend/contracts/assistant_events.py`
- `backend/evals/assistant/score.py`
