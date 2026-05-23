# Hybrid Command Router and Plan Executor

## Problem Statement
The current assistant routing has two brittle extremes:

1. Parser fast-path handles only narrow single-intent commands.
2. Model tool-loop handles everything else, including imperative UI mutations.

This creates failures in realistic prompts:

- Multi-intent prompts are truncated by single-intent fast-path behavior.
  - Example: `Switch to test_scenario, then turn on slope.` can execute only the first action.
- Model loop may choose read-only tools (`layer.list_visible`) for mutating intents (`turn on slope`) and then stop.
- Tool-loop stalls after one tool call with empty completion, leaving user intent unfulfilled.

Result: non-deterministic behavior for imperative commands and poor reliability for scenario/layer operations.

## Solution Architecture
Implement a **hybrid command router** with a **data-driven deterministic plan executor**.

Design goals:

- Deterministic behavior for imperative state-changing intents.
- Multi-intent support with ordered execution.
- No large switch statement.
- Strict schema validation and postcondition checks.
- Controlled use of LLM for disambiguation only.

### High-Level Flow
1. **Intent segmentation**: Split prompt into ordered action candidates.
2. **Action planning**: Convert segments into typed action specs via a registry-driven matcher.
3. **Deterministic execution**: Run action graph sequentially with preconditions/postconditions.
4. **Repair/disambiguation**: If resolution fails, run bounded clarifier/resolver step.
5. **Fallback**: If no deterministic action plan is viable, use model tool-loop.

### Data-Driven Deterministic Core
Use an **Action Registry** (declarative specs), not a switch statement.

Each action spec contains:

- `action_id` (e.g., `scenario.switch`, `layer.set_visible_by_name`)
- `intent_patterns` (regex/templates)
- `slot_extractors` (named captures or extractor functions)
- `required_slots`
- `tool_plan` (ordered tool templates with slot interpolation)
- `preconditions`
- `postconditions`
- `repair_strategies`
- `priority`
- `supports_chaining`

Execution is generic: registry lookup -> instantiate plan -> validate -> run.

### Key Components
- `CommandSegmenter`: splits prompts by conjunctions/order words while preserving quoted strings.
- `ActionPlanner`: registry-based match + slot binding + ambiguity score.
- `ActionExecutor`: generic runner over tool templates, validation, and postcondition checks.
- `EntityResolver`: deterministic resolution for scenario/layer names using catalog data.
- `RepairEngine`: retries with alternate resolution or asks targeted clarification.
- `ExecutionLedger`: structured trace of actions, tool calls, outcomes, and repairs.

## Detailed Implementation Plan

### Phase 1: Core Contracts and Registry
1. Add `backend/services/assistant/command_router/` package.
2. Define pydantic models:
   - `PlannedAction`, `PlannedStep`, `ActionResult`, `RepairAttempt`, `ExecutionTrace`.
3. Define registry schema (`action_specs.py`) and loader.
4. Implement startup validation:
   - unique `action_id`
   - valid tool names
   - slot references resolve
   - pre/postcondition function references valid.

Deliverable: validated, data-driven action registry with no execution logic yet.

### Phase 2: Intent Segmentation and Planning
1. Implement `CommandSegmenter.segment(prompt)`:
   - split on connectors (`then`, `and then`, commas in imperative context)
   - preserve quoted substrings
   - return ordered segments.
2. Implement `ActionPlanner.plan(prompt, context)`:
   - match segments against registry patterns
   - extract slots
   - compute confidence and ambiguity
   - produce ordered `PlannedAction[]`.
3. Add planner tests:
   - single intent
   - multi-intent chain
   - negative tests (non-command prompts).

Deliverable: reliable multi-action plan extraction.

### Phase 3: Deterministic Executor
1. Implement `ActionExecutor.execute(plan, context)`:
   - evaluate action preconditions
   - instantiate tool steps from templates
   - run existing `execute_tool` path with strict arg validation
   - evaluate postconditions
   - emit structured trace logs.
2. Add shared tool helpers:
   - `resolve_scenario_ref`
   - `resolve_layer_name` (all layers, not only visible)
   - `verify_layer_visibility`.
3. Add failure policies:
   - hard fail for non-recoverable errors
   - bounded repair for resolvable ambiguity.

Deliverable: deterministic multi-step execution engine.

### Phase 4: Repair and Clarification
1. Implement `RepairEngine` with bounded strategies per action spec:
   - alternate name matching strategy
   - nearest candidate retry
   - tool-assisted inventory refresh.
2. If still ambiguous, return concise clarification question with options.
3. Ensure assistant message includes what succeeded and what needs clarification.

Deliverable: robust recovery without silent no-ops.

### Phase 5: Assistant Service Integration
1. Replace single `tool_fast_path` with `action_plan_fast_path`:
   - if planner returns executable actions, run deterministic executor.
   - if no deterministic match, continue existing model tool-loop.
2. Preserve explicit tool-call mode and existing confirmations.
3. Integrate execution trace into turn usage metadata.
4. Add telemetry fields:
   - `turn_handling_mode=action_plan_fast_path|model_tool_loop`
   - `action_count`, `repair_count`, `postcondition_failures`.

Deliverable: hybrid router live behind feature flag.

### Phase 6: Action Specs for Initial Coverage
Implement initial registry entries:

1. `scenario.switch`
   - prompts: `switch/use/set scenario ...`
   - tool: `scenario.set_current`
2. `layer.set_visible_by_name`
   - prompts: `turn on/off`, `show/hide ... layer`
   - tools: `product.list`/layer inventory resolver + `layer.update_state`
   - postcondition: verify target `visible` value
3. `layer.list_visible`
   - prompts: explicit visible-layer questions only
   - tool: `layer.list_visible`
4. `scenario.import_geotiff`
5. `scenario.move_path`

Deliverable: deterministic handling of highest-impact imperative intents.

### Phase 7: Testing Strategy
1. Unit tests:
   - registry validation
   - segmenter behavior
   - action planner matching and slot extraction
   - resolver ambiguity handling
   - postcondition checks.
2. Worker tests:
   - deterministic action chain execution
   - repair paths
   - no-op prevention.
3. Contract tests:
   - multi-intent prompt executes all intended actions in order
   - mutation prompts never end after read-only listing when mutation requested.
4. Regression fixtures for known failures:
   - `Turn on slope`
   - `Switch to X, then turn off hillshade`
   - ambiguous layer names.

### Phase 8: Rollout and Safety
1. Add config flag: `backend.llm.hybrid_command_router_enabled`.
2. Shadow mode first:
   - plan actions and log traces while still executing current flow.
3. Compare outcomes and promote to active mode when stable.
4. Keep kill-switch to revert to current model tool-loop.

## Logging and Observability
Add structured logs:

- `assistant action planner result` (segments, matched actions, confidence)
- `assistant action execute start/result` (action_id, step_id, tool, args_summary)
- `assistant action postcondition` (pass/fail)
- `assistant action repair` (strategy, attempt count, result)

Do not log large artifact payloads.

## Data-Driven Answer to the Switch-Statement Concern
Yes, deterministic routing will be data-driven.

- The deterministic portion is implemented as a validated action registry + generic executor.
- New behavior is added by adding action specs and reusable resolver/precondition functions.
- No monolithic per-intent switch in assistant service.

## Out of Scope (Initial Iteration)
- Full natural language planner replacement.
- Autonomous long-horizon task planning beyond bounded imperative command chains.
- Free-form domain Q&A behavior changes.
