# ADR 0022: Hybrid Command Router with Deterministic Intent Triggers and Procedural Guidance

- Status: Accepted
- Date: 2026-03-11
- Owners: Architecture (Codex), Implementation (Gemini)
- Related: `docs/ADR.0011.ai_assistant_and_mcp.md`, `docs/ADR.0019.unified_tool_model.md`, `docs/ADR.0021.assistant_rag_wrapper_and_scenario_index.md`, `docs/hybrid-command-router.md`, `docs/DESIGN.md`

## Context

Assistant behavior for imperative map/scenario commands is currently unreliable when left entirely to model tool-loop behavior. We have observed these failure modes:

1. Correct user intent but wrong tool selection.
- Example: visibility intent (`show slope`) leads to read-only `product.list` instead of mutating `layer.update_state`.

2. Multi-step loops stall after read-only tools.
- Large read-only payloads inflate context and subsequent iterations return empty completions.

3. RAG guidance is advisory, not enforcing.
- Retrieved procedural text can improve probabilities, but does not guarantee tool selection or completion.

4. Cost/control concerns from provider fallback.
- Unintended cross-provider fallback (for example to OpenAI) can occur under empty/slow completion heuristics.

We need deterministic reliability for imperative intents while preserving model flexibility for open-ended analysis.

## Decision

Adopt a hybrid routing architecture:

1. Deterministic pre-router first for narrow imperative intents.
- Use a data-driven action registry (patterns, slot extractors, tool plans, guards).
- No monolithic switch statement in assistant service.

2. Model tool-loop second for non-matching or non-imperative prompts.
- Preserve existing tool-loop and RAG behavior for broad reasoning tasks.

3. Procedural guidance remains in RAG, but with deterministic trigger support.
- Procedural docs provide few-shot pattern->action examples.
- Deterministic triggers decide when to inject specific guidance docs.

4. Enforce mutation postconditions for deterministic actions.
- Commands that request state change must either mutate state or return explicit failure/clarification.

5. Keep execution provider orthogonal to router.
- Deterministic planner/executor runs before provider completion; provider choice should not affect imperative routing.

## Architecture

### A. Data-Driven Action Registry

Define actions declaratively (code-backed schema, config-like structure):

- `action_id`
- `priority`
- `patterns` (regex/templates)
- `deny_patterns`
- `required_slots`
- `tool_plan` (templated argument maps)
- `preconditions`
- `postconditions`
- `repair_strategies`

Registry coupling rule:
- `ActionSpec` must reference existing unified tool definitions by `implementation_name`/tool name from the tool registry.
- The deterministic executor must not duplicate tool contracts or validation logic.
- Argument validation remains owned by the existing tool schema layer.

Initial action set:

- `scenario.switch` -> `scenario.set_current`
- `layer.set_visible_by_name` -> `layer.update_state`
- `layer.list_visible` -> `layer.list_visible`

### B. Command Segmenter and Planner

- Split chained commands (`then`, `and then`, newline/`;`) into ordered segments.
- Plan each segment independently against registry.
- If all segments match, execute deterministic plan (`action_plan_fast_path`).
- If any segment is unmatched, route to model tool-loop (or hybrid partial mode in later phase).

### C. Deterministic Executor

- Execute planned tool calls sequentially.
- Normalize/validate arguments against tool schema.
- Preserve confirmation policy.
- Update runtime scenario context after `scenario.set_current`.
- Record structured execution trace.
- Use existing `ToolImplementations` execution surface only.
- Do not implement parallel compute or domain logic in router/executor code.

### D. Procedural Guidance Triggering

- Keep procedural few-shot docs in `docs/rag_corpus`.
- Add deterministic guidance selectors keyed by intent/channel.
- For matched deterministic intents, inject only relevant procedural snippets (small budget).
- Do not rely on broad lexical retrieval for critical imperative behavior.

### E. Model Tool-Loop Guardrails

When a prompt intent is mutating and deterministic routing was not used:

- Bias against read-only tool loops as terminal outcome.
- Add no-op detection: if no state mutation and no clarification, continue/recover.
- Compact large tool payloads before reinjecting into model context.

### F. Complexity Guard and Shadowing Prevention

Deterministic rules must bail out to model-loop when prompt complexity exceeds safe command scope.

Examples of complexity markers:
- conditional qualifiers: `if`, `when`, `unless`, `only if`
- causal/constraint clauses: `because`, `while`, `except`
- multi-objective requests with analysis requirements

Action specs must support `deny_patterns` and complexity predicates to prevent broad patterns (for example `show *`) from shadowing higher-order requests.

### G. Partial Deterministic Execution

If a chained prompt is only partially matched:

1. Execute matched deterministic segments in order.
2. Rebuild context with updated state and executed action trace.
3. Route the remaining unmatched segment(s) to model tool-loop.

This avoids all-or-nothing fallback and preserves deterministic wins in mixed prompts.

## Why Not Procedural Docs Alone

Procedural docs improve tool-choice likelihood but cannot guarantee:

- deterministic tool selection,
- completion under context pressure,
- postcondition satisfaction.

Therefore docs are supporting policy, not enforcement.

## Provider and Cost Controls

This ADR requires explicit fallback controls for cost-sensitive deployments:

- If remote fallback is undesired, disable cross-provider fallback in config.
- Keep command routing deterministic so imperative tasks do not depend on provider retries.

Operational recommendation:

- Set `command_provider`/`command_model` to local defaults unless remote use is intentional.
- Leave `slow_turn_fallback_provider/model` empty for no cross-provider fallback.

## Observability

Add/retain structured logs:

- planner result (`segments`, `matched_actions`, `unmatched_segments`)
- deterministic step execution (`action_id`, `tool`, `args_summary`)
- postcondition result (`pass/fail`)
- repair/clarification outcomes
- final planner mode (`action_plan_fast_path` vs `model_tool_loop`)
- execution origin tag per action (`deterministic` vs `model_reasoned`)

Do not log full large tool payloads.

## State, Ambiguity, and Response Policy

### State Conflict Handling

- Preconditions and postconditions must query live scenario/layer services at execution time.
- If live state differs from planner assumptions, executor re-resolves inputs or returns explicit recoverable failure.

### Ambiguity Handling

- Deterministic path performs first-pass disambiguation (exact/normalized name match).
- If multiple matches remain, return concise clarification (choices), not broad inventory dumps.
- Do not silently pick arbitrary candidates.

### User-Facing Response Consistency

- Deterministic actions return standard assistant responses (same turn UX).
- Response metadata should mark deterministic execution for traceability.
- UI may optionally show a deterministic badge (future enhancement).

## Consequences

Positive:

- Reliable handling for imperative commands.
- Better multi-intent execution sequencing.
- Reduced dependence on model compliance for state-changing operations.
- Cleaner separation between deterministic control and model reasoning.

Tradeoffs:

- Additional routing/execution code paths and tests.
- Need to curate action registry patterns over time.
- Some user phrasings may still miss deterministic matching until coverage expands.

## Non-Goals

- Replacing model tool-loop for analysis/exploration queries.
- Eliminating RAG procedural docs.
- Building a full task planner for long autonomous workflows.

## Implementation Plan

### Phase 1: Registry and Contracts

1. Define typed action models (`ActionSpec`, `PlannedAction`, `PlannedStep`, `CommandPlan`).
2. Validate registry at startup (schema/tool references/placeholders).
3. Enforce ActionSpec -> tool-registry reference validation (no duplicate schemas).
3. Add initial action specs (`scenario.switch`, `layer.set_visible_by_name`, `layer.list_visible`).

Acceptance:
- Registry validation passes.
- Unit tests cover pattern matching and slot extraction.

### Phase 2: Planner and Segmenter

1. Implement segmentation for chained imperative prompts.
2. Implement match/plan scoring with unmatched segment tracking.
3. Expose planner result in structured logs.

Acceptance:
- Multi-intent prompts produce ordered plans.
- Non-command prompts remain unmatched.

### Phase 3: Deterministic Executor

1. Execute plan steps sequentially via existing tool execution path.
2. Preserve confirmation gates.
3. Update runtime scenario context across steps.
4. Add mutation postconditions for visibility actions.
5. Add live-state precondition checks and re-resolution hooks.

Acceptance:
- `switch + turn on/off` chains execute in order.
- Confirmation-required actions pause correctly.

### Phase 4: Guidance Triggering

1. Add intent->guidance mapping for procedural snippets.
2. Inject focused few-shot docs for matched intents.
3. Keep snippet budget tight to avoid context bloat.

Acceptance:
- Matched layer-visibility intents inject only layer policy guidance.

### Phase 5: Tool-Loop Guardrails

1. For mutate intents in model-loop path, detect non-mutating dead-ends.
2. Prevent read-only-only completion as success when mutation requested.
3. Compact large tool outputs before reinjection.
4. Support partial deterministic execution followed by model-loop continuation.

Acceptance:
- Repro case (`show/turn on slope`) does not terminate after only `product.list`.

### Phase 6: Fallback/Cost Policy Hardening

1. Add explicit config guard to disallow cross-provider fallback.
2. Document recommended local-only settings.
3. Add tests validating no remote fallback when disabled.

Acceptance:
- Local-only mode performs no OpenAI calls.

### Phase 7: Rollout

1. Feature flag deterministic router (`backend.llm.hybrid_command_router_enabled`).
2. Start with shadow logging in test/staging.
3. Promote to active default after regression suite passes.
4. Add optional UI provenance marker for deterministic turns.

Acceptance:
- No regressions in assistant tool-loop contract tests.
- Deterministic intent success rate improves on known failure prompts.

## Testing Strategy

- Unit tests: segmenter, matcher, slot normalization, guidance selection.
- Worker tests: deterministic chains, confirmation flow, postcondition failures.
- Regression tests for known prompts:
  - `show slope`
  - `turn off slope layer`
  - `switch to test_scenario, then turn on slope`
- Cost-control tests: ensure no disallowed provider fallback attempts.

## Rollback

- Disable deterministic router via `backend.llm.hybrid_command_router_enabled = false`.
- Keep parser/model-tool-loop operational as baseline.
- Guidance docs remain harmless if deterministic path is disabled.
