# ADR 0030: Tool Argument Repair Policy for Assistant Execution Reliability

- Status: Accepted
- Date: 2026-03-18
- Owners: Architecture (Codex), Implementation (TBD)
- Related: `docs/ADR.0019.unified_tool_model.md`, `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`, `docs/ADR.0023.deterministic_router_with_bounded_agent_substeps.md`, `docs/ADR.0027.intent_classification_contract.md`, `docs/ADR.0028.turn_planner_json_contract.md`, `docs/ADR.0029.per_segment_execution_state_and_merge_policy.md`, `docs/DESIGN.md`

## Context

Assistant turns frequently fail from minor argument mismatches rather than intent failure. Common cases include:

1. missing default-able fields (`overwrite_mode`, `publish_layer.enabled`),
2. path forms that need normalization to scenario-relative format,
3. temporal requests missing one bound (`time_stop_utc` or `time_step_hours`),
4. alias/legacy field names (`handler_name` vs canonical `implementation_name`).

Today, many of these fail hard and force unnecessary user retries. At the same time, unrestricted auto-repair risks hidden behavior changes and unsafe mutations.

We need a policy that improves robustness while preserving safety, auditability, and contract clarity.

## Decision

Adopt a bounded, explicit tool argument repair policy with:

1. allowlisted deterministic repairs only,
2. per-tool repair rules and limits,
3. repair provenance logging,
4. mandatory clarification when ambiguity or safety risk remains.

Repair is an execution-time adapter layer and must not replace schema validation ownership in tool contracts.

## Repair Contract

## A. Repair Stages

For each planned tool call:

1. Validate raw arguments against tool schema.
2. If invalid and repair is enabled, apply allowlisted repair rules.
3. Re-validate repaired arguments.
4. If still invalid or unsafe, return explicit recoverable failure and request clarification.

Only one repair pass per call in v1.

## B. Repair Outcome Schema

Each call records:

```json
{
  "repair_attempted": true,
  "repair_applied": true,
  "repair_rules": ["normalize_path", "default_overwrite_mode"],
  "repair_status": "revalidated",
  "repair_warning_codes": []
}
```

`repair_status` values:

- `not_needed`
- `revalidated`
- `failed_unrepairable`
- `blocked_requires_clarification`

## C. Allowed v1 Repair Categories

1. Canonical field alias mapping
- Map known compatibility aliases to canonical fields where semantics are equivalent.

2. Default injection for explicit policy-backed defaults
- Example: set `overwrite_mode="ask"` when omitted and tool supports it.

3. Path normalization
- Normalize separators, trim whitespace, convert absolute scenario-internal paths to scenario-relative form.
- Enforce scenario-root allowlist before and after normalization.

4. Temporal parameter completion from explicit local context
- Only when unambiguous and policy-approved.
- Example: infer missing `time_stop_utc` from provided start + explicit duration parameter if both present.

5. Enum normalization
- Case-fold and map known synonyms when one canonical match exists.

## D. Forbidden Repairs (v1)

1. Guessing among multiple candidate targets (layer/product/scenario) without clarification.
2. Fabricating required scientific parameters absent from prompt/context.
3. Expanding tool scope (adding mutating side effects not requested).
4. Converting a blocked policy action into a permitted one.
5. Multi-hop speculative repairs (more than one semantic assumption).

## Safety and Clarification Rules

Repair must stop and request clarification when:

1. Multiple valid interpretations remain after normalization.
2. Any path is out-of-root or traversal-like after normalization.
3. Mutation semantics would materially change due to repair.
4. Temporal request requires assumptions not explicitly grounded in prompt/runtime state.

Clarification responses must include:

- missing/ambiguous fields,
- acceptable options/examples,
- preserved user intent summary.

## Tool-Level Policy Configuration

Add configuration surface for per-tool repair control:

```toml
[backend.assistant.argument_repair]
enabled = true
max_repairs_per_call = 1

[backend.assistant.argument_repair.tools.raster_calculate]
allow_path_normalization = true
allow_default_overwrite_mode = true
allow_temporal_completion = true
```

Defaults should remain conservative.

## Architecture

1. Placement
- Repair layer runs after planner step creation and before final tool invocation.

2. Ownership
- Tool schema remains source of truth.
- Repair does not duplicate full validation logic; it adapts inputs, then calls schema validator again.

3. Integration with Segment State
- Repair outcomes are persisted in per-segment `tool_calls` metadata (ADR 0029).
- Final merge can mention repaired fields when user-visible behavior changed.

## Observability

Structured logs for each repair attempt:

- `tool_name`
- `repair_attempted`
- `repair_applied`
- `repair_rules`
- `repair_status`
- `clarification_required`

Metrics:

- repair attempt rate
- repair success rate
- post-repair execution success rate
- clarification-after-repair rate
- unsafe repair blocked count

## Testing Strategy

1. Unit tests
- each allowed rule transforms expected invalid input to valid canonical input.
- forbidden cases produce `blocked_requires_clarification`.

2. Integration tests
- end-to-end prompts that previously failed due to minor argument issues now succeed with recorded repair provenance.
- ambiguous repairs remain blocked and ask for clarification.

3. Regression tests
- ensure repairs do not bypass confirmation gates for mutating tools.
- ensure out-of-root path requests are never auto-repaired into execution.

## Consequences

Positive:

- Reduced avoidable tool-call failures.
- Better user experience for near-correct requests.
- Clear audit trail for automatic argument adjustments.

Tradeoffs:

- Added policy and configuration complexity.
- Ongoing maintenance as tool surfaces evolve.

## Rollout

1. Feature flag: `backend.assistant.argument_repair_enabled`.
2. Shadow mode: evaluate repair candidates and log hypothetical changes without execution mutation.
3. Enable for low-risk read-only tools first.
4. Expand to mutating tools after confirmation and safety regression pass.
5. Roll back quickly by disabling feature flag.

## Detailed Implementation Plan

### Phase 1: Repair Engine Skeleton and Policy Config

Goals:

1. Implement repair engine framework with policy-driven enablement.

Target files:

- New module: `backend/services/assistant/tool_argument_repair.py`
- `backend/config/settings.py`
- `config/lunar_analyst.toml`
- `backend/tests/assistant/test_argument_repair_config.py`

Tasks:

1. Add config model for global and per-tool repair rules.
2. Implement repair pipeline stages:
- pre-validate,
- repair pass,
- re-validate,
- outcome classification.

Acceptance:

1. Repair engine returns `not_needed` when args are already valid.
2. Unknown tool policy uses conservative defaults.

Rollback:

- Disable `backend.assistant.argument_repair_enabled`.

### Phase 2: Allowed Rule Implementations

Goals:

1. Implement v1 allowlisted repair rules from this ADR.

Target files:

- `backend/services/assistant/tool_argument_repair.py`
- `backend/services/assistant/path_utils.py` (or existing path normalization utility)
- `backend/tests/assistant/test_argument_repair_rules.py`

Tasks:

1. Implement alias mapping.
2. Implement default injection for explicit policy defaults.
3. Implement path normalization with in-root checks.
4. Implement bounded temporal completion.
5. Implement enum normalization.

Acceptance:

1. Unit tests pass for each allowed rule and composition behavior.
2. Revalidation is mandatory after every applied repair set.

Rollback:

- Keep engine but disable individual rule switches per tool.

### Phase 3: Forbidden Rule Guards and Clarification Path

Goals:

1. Block unsafe/ambiguous repairs with explicit clarification output.

Target files:

- `backend/services/assistant/tool_argument_repair.py`
- `backend/services/assistant/assistant_service.py`
- `backend/tests/assistant/test_argument_repair_forbidden_cases.py`

Tasks:

1. Implement guards for ambiguity, out-of-root paths, and semantic scope expansion.
2. Return structured `blocked_requires_clarification` outcomes.
3. Ensure downstream execution does not proceed after blocked repair.

Acceptance:

1. Forbidden-case tests confirm no tool execution occurs.
2. Clarification payload contains missing fields/options and intent summary.

Rollback:

- Disable repair engine for affected tool categories.

### Phase 4: Tool Boundary Integration and Telemetry

Goals:

1. Integrate repair engine into tool invocation path and telemetry.

Target files:

- tool execution boundary module (`backend/services/assistant/tool_execution.py` or equivalent)
- `backend/services/assistant/turn_state_manager.py`
- `backend/tests/assistant/test_argument_repair_integration.py`

Tasks:

1. Apply repair before tool invocation when schema validation fails.
2. Attach repair outcomes to per-segment tool call metadata.
3. Emit repair telemetry/events and counters.

Acceptance:

1. Integration tests show previously failing near-valid calls now succeed.
2. Confirmation gates remain enforced for mutating calls after repair.

Rollback:

- Revert invocation pipeline to strict validation-only mode.

## Verification Commands

1. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_argument_repair_config.py -q"`
2. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_argument_repair_rules.py -q"`
3. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_argument_repair_forbidden_cases.py -q"`
4. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_argument_repair_integration.py -q"`

## Exit Criteria

1. Repair success rate improves targeted failure classes without increasing safety violations.
2. Forbidden repair cases consistently block and request clarification.
3. No confirmation-policy bypass incidents in repair-enabled paths.

## Non-Goals

- Replacing planner/classifier responsibilities.
- Correcting scientific logic errors in user intent.
- Introducing best-effort fuzzy execution that hides ambiguity from users.
