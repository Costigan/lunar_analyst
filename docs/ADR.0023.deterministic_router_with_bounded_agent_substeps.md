# ADR.0023: Deterministic Router with Bounded Agent Substeps

Status: Accepted  
Date: 2026-03-10  
Deciders: Lunar Analyst architecture team

## Context

ADR.0022 established a hybrid command architecture:
- Deterministic command routing for matched imperative intents.
- Agent/model tool-loop fallback for unmatched or partially matched prompts.

We are now moving deterministic router cases into a YAML action-spec file for maintainability. During that design, a follow-on question arose: can deterministic plans include constrained model reasoning as a sub-step, instead of only raw tool calls?

## Decision

Adopt a **deterministic outer orchestrator** that may run two step types:
1. `tool_call` (existing behavior)
2. `agent_call` (new, bounded sub-agent step)

The core routing rule remains unchanged:
- If deterministic action(s) match, run deterministic execution path.
- If no deterministic action matches, fallback to normal agent/model tool-loop.

`agent_call` is allowed only under strict controls:
- explicit per-step tool allowlist
- explicit structured output schema
- max iteration count
- max output tokens
- timeout
- deterministic validation of agent output before subsequent steps
- deterministic postcondition checks for state mutation outcomes

## Why

Some deterministic commands still need targeted interpretation (disambiguation, ranking, bounded extraction) where pure regex + static slots are insufficient. A bounded sub-agent step provides that capability without giving up deterministic control, observability, and safety.

## Consequences

### Positive
- Better reliability than pure agent loop for imperative workflows.
- Better capability than pure tool-only deterministic steps for ambiguous cases.
- Keeps strict mutation postconditions and execution traceability.

### Negative
- More complexity in step schema and executor.
- Additional validation and test surface.
- Requires clear guardrails to avoid unbounded model behavior.

## Changes Needed vs Current YAML Migration Plan

Yes, changes are needed.

The current YAML migration plan assumes `tool_plan` with tool-only steps. This ADR extends plan schema and executor semantics:
- replace `tool_plan` with ordered `steps` supporting typed step kinds, or keep `tool_plan` and add `agent_plan` (not recommended).
- add `agent_call` validation and runtime execution path.
- add deterministic validation contract for agent output.

Fallback behavior (no deterministic match -> agent loop) remains unchanged.

## YAML Schema Extension (Proposed)

Top-level action shape (simplified):

```yaml
actions:
  - action_id: "layer.resolve_then_toggle"
    priority: 80
    patterns:
      - '^\\s*show\\s+(?P<layer_query>.+?)\\s*$'
    steps:
      - kind: "agent_call"
        objective: "Resolve layer target from query `${layer_query}`."
        allowed_tools: ["layer.list_visible", "product.list"]
        output_schema:
          type: object
          required: ["layer_name", "visible"]
          properties:
            layer_name: { type: string }
            visible: { type: boolean }
        max_iterations: 2
        max_output_tokens: 512
        timeout_ms: 8000
      - kind: "tool_call"
        tool_name: "layer.update_state"
        arguments:
          layer_name: "${layer_name}"
          visible: "${visible}"
```

Rules:
- `kind=tool_call` requires `tool_name` and `arguments`.
- `kind=agent_call` requires all control fields above.
- Agent step outputs are merged into slot context only after schema validation.
- Unknown placeholders or disallowed tool names fail startup.

## Implementation Plan

1. YAML router file adoption
- Move current action specs from `_build_action_specs()` into YAML.
- Loader validates schema, regex compilation, tool references, placeholders.

2. Step model generalization
- Introduce `PlannedStep` union:
  - `PlannedToolStep`
  - `PlannedAgentStep`

3. Executor enhancement
- Deterministic executor handles steps sequentially:
  - `tool_call`: existing path
  - `agent_call`: invoke model with per-step guardrails, parse structured result, validate schema, merge slots
- Abort or repair deterministically on invalid agent step output.

4. Guardrails
- Enforce allowlisted tools during agent step execution.
- Enforce max iterations/tokens/timeouts.
- Enforce output schema validation.
- Log step-level provenance (`deterministic_tool`, `deterministic_agent_substep`).

5. Compatibility and rollout
- Feature flag: `backend.llm.deterministic_agent_substeps_enabled = false` initially.
- Ship YAML + tool-only behavior first.
- Enable substeps after tests and shadow telemetry pass.

6. Testing
- Unit:
  - YAML parsing/validation for both step kinds
  - agent step schema validation pass/fail
  - placeholder propagation after agent step
- Worker/integration:
  - mixed tool+agent deterministic plans
  - postcondition enforcement after sub-agent assisted mutation
  - fallback behavior unchanged when no deterministic match

7. Documentation
- Update `docs/DESIGN.md`:
  - explain deterministic orchestrator + bounded sub-agent pattern
  - clarify unchanged global fallback path
- Add examples in router spec docs for when to use `agent_call` vs pure tool steps.

## Rejected Alternatives

1. Keep deterministic plans tool-only forever  
Rejected because it limits practical robustness for ambiguous yet still imperative intents.

2. Let deterministic plan invoke unrestricted agent step  
Rejected due to reliability/safety regressions and loss of deterministic guarantees.

## References

- Existing: `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`
- Related pattern names: hierarchical agent orchestration, planner-controller with constrained sub-agents, neuro-symbolic control loop.
