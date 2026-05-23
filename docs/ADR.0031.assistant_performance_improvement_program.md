# ADR 0031: Assistant Performance Improvement Program (Meta ADR)

- Status: Accepted
- Date: 2026-03-18
- Owners: Architecture (Codex), Implementation (TBD)
- Related: `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`, `docs/ADR.0023.deterministic_router_with_bounded_agent_substeps.md`, `docs/ADR.0025.assistant_eval_pyramid_for_routing_functional_and_domain_quality.md`, `docs/ADR.0026.spacy_intent_unit_segmentation.md`, `docs/ADR.0027.intent_classification_contract.md`, `docs/ADR.0028.turn_planner_json_contract.md`, `docs/ADR.0029.per_segment_execution_state_and_merge_policy.md`, `docs/ADR.0030.tool_argument_repair_policy.md`, `docs/DESIGN.md`

## Context

Assistant reliability for lunar mission analysis depends on multiple coordinated improvements across routing, execution, retrieval, observability, and evaluation. The project now has detailed ADRs for core runtime mechanics (segmentation, classification, planning, execution state, argument repair), but lacks one top-level architecture decision that defines the full program and how parts fit together.

Without a meta decision:

1. implementation sequencing can drift,
2. partial delivery may optimize one layer while regressing another,
3. acceptance criteria for "agent performance improved" remain ambiguous.

## Decision

Adopt a unified Assistant Performance Improvement Program with a deterministic-first hybrid execution model and behavior-first evaluation gates.

This ADR is the umbrella contract. It defines end-to-end goals, boundaries, rollout stages, and acceptance metrics, while deferring detailed design to subordinate ADRs.

## Program Goals

1. Increase deterministic reliability for imperative/mutating user intents.
2. Preserve model flexibility for scientific reasoning and open-ended analysis.
3. Improve turn completion quality via bounded repair and explicit blocked/clarification handling.
4. Make failures diagnosable and reproducible through structured state and telemetry.
5. Promote evaluation from text-quality checks to behavior/postcondition correctness.

## Scope

In scope:

- Segment-level mixed routing and execution orchestration.
- Tool-call robustness improvements that preserve safety policy.
- Evaluation and observability enhancements needed for measurable improvement.

Out of scope:

- Replacing unified tool contracts or moving compute ownership outside existing handler/tool implementation surfaces.
- Full autonomous multi-turn planning.
- Scientific model validation of lunar domain conclusions beyond tool/postcondition correctness.

## Program Architecture (Layered)

1. Input decomposition and routing preparation
- Prompt segmentation and prompt classification.
- Details delegated to ADR 0026 and ADR 0027.

2. Turn orchestration
- Versioned planner/execution-plan contract for mixed deterministic + LLM execution.
- Details delegated to ADR 0028.

3. Runtime state and response synthesis
- Per-segment status tracking, deterministic-to-LLM handoff, final merge policy.
- Details delegated to ADR 0029.

4. Execution robustness
- Bounded argument repair with explicit safety and clarification boundaries.
- Details delegated to ADR 0030.

5. Follow-on governance layers (to be specified in additional ADRs)
- Mutating-vs-read-only success semantics.
- Observability and failure taxonomy.
- RAG channel routing and provenance/versioning.
- Eval scoring model, CI gates, golden/replay harness.
- Prompt contract hardening for hybrid behavior.

## Success Metrics

Program-level KPIs (tracked by provider/model and by suite):

1. Deterministic intent completion rate.
2. Mixed-turn completion rate (`success` + `partial_success` breakdown).
3. Postcondition pass rate for functional tasks.
4. Clarification quality rate (blocked prompts resolved in next turn without regression).
5. Regression rate on golden replay set.
6. P50/P95 end-to-end turn latency with segmenter/planner overhead separated.

Quality gates should be defined by follow-on eval/observability ADRs and enforced before default-on rollout.

## Safety and Invariants

All improvements must preserve existing project invariants:

1. Mutation confirmation policy remains enforced.
2. Filesystem path normalization and scenario-root allowlist checks remain mandatory.
3. Compute logic ownership remains in job handlers/tool implementation contracts; no parallel duplicate compute contract layer.
4. Deterministic routing must not silently bypass blocked/ambiguous states.

## Rollout Strategy

1. Stage A: Shadow mode
- Generate segment/classification/planner/state/repair artifacts without changing authoritative behavior.

2. Stage B: Controlled activation
- Enable deterministic-first mixed routing for targeted intent families and low-risk tools.

3. Stage C: Expansion
- Expand action coverage, repair coverage, and evaluation suites.

4. Stage D: Default-on
- Promote to default after KPI gates pass and no critical safety regressions.

Every stage must be feature-flagged and independently reversible.

## Detailed Implementation Plan

This section is implementation-normative for sequencing, acceptance, and rollback checkpoints.

### Workstream 0: Baseline and Feature Flags (Week 1)

Goals:

1. Establish baseline metrics and current eval snapshot.
2. Add top-level feature flags with no behavior change.

Target files:

- `backend/config/settings.py` (or equivalent config model)
- `config/lunar_analyst.toml`
- `backend/services/assistant/*` (flag wiring only)
- `backend/tests/assistant/*` (baseline assertions)

Tasks:

1. Add flags:
- `backend.llm.prompt_segmentation_enabled`
- `backend.llm.prompt_classification_contract_enabled`
- `backend.llm.turn_execution_plan_contract_enabled`
- `backend.llm.segment_state_merge_policy_enabled`
- `backend.assistant.argument_repair_enabled`
- `backend.llm.success_semantics_policy_enabled`
- `backend.llm.observability_contract_enabled`

2. Add startup logging of effective flag values.
3. Capture baseline eval run artifact and store under repo-defined eval outputs.

Acceptance:

1. No user-visible behavior changes when all new flags are disabled.
2. Baseline eval report produced and archived.

Rollback checkpoint:

- Revert config additions only; no runtime contract changes active yet.

### Workstream 1: Segmentation + Classification Shadow Mode (Week 1-2)

Goals:

1. Generate segment/classification artifacts without changing routing behavior.

Primary ADRs:

- ADR 0026
- ADR 0027

Target files:

- `backend/services/assistant/command_router.py`
- New modules:
- `backend/services/assistant/prompt_segmenter.py`
- `backend/services/assistant/prompt_classifier.py`
- `backend/tests/assistant/test_prompt_segmenter.py`
- `backend/tests/assistant/test_prompt_classifier.py`

Tasks:

1. Implement spaCy-backed segmenter with confidence and guard flags.
2. Implement classifier contract output schema and conflict resolution.
3. Emit shadow telemetry events only; do not alter authoritative routing.

Acceptance:

1. Segment/classification unit tests pass with offset and label fixtures.
2. Shadow logs available for mixed prompts.

Rollback checkpoint:

- Disable flags; keep modules present but inactive.

### Workstream 2: Planner Contract + Deterministic Execution Binding (Week 2-3)

Goals:

1. Introduce versioned planner/execution-plan JSON and validation.
2. Route deterministic-capable segments through execution-plan-aware deterministic execution.

Primary ADR:

- ADR 0028

Target files:

- New module: `backend/services/assistant/turn_execution_plan.py`
- `backend/services/assistant/assistant_service.py` (or orchestration entrypoint)
- `backend/tests/worker/test_turn_execution_plan.py`
- `backend/tests/assistant/test_hybrid_planner_execution.py`

Tasks:

1. Build planner/execution-plan schema models and validation rules.
2. Connect the execution-plan builder to the existing deterministic executor path.
3. Persist compact planner metadata in turn records.

Acceptance:

1. Mixed prompt integration tests show deterministic-first ordering.
2. Validation failures return machine-readable planner errors.

Rollback checkpoint:

- Disable planner flag and fall back to pre-planner orchestration.

### Workstream 3: Per-Segment State + Merge Contract (Week 3-4)

Goals:

1. Track per-segment runtime state.
2. Implement deterministic-to-LLM handoff and final merge contract.

Primary ADR:

- ADR 0029

Target files:

- New module: `backend/services/assistant/turn_state_manager.py`
- `backend/services/assistant/assistant_service.py`
- `backend/contracts/assistant_events.py` (additive schema fields only)
- `backend/tests/assistant/test_turn_state_merge.py`

Tasks:

1. Add state transitions and dependency-skip handling.
2. Build compact LLM handoff summary.
3. Produce merged response entries by original segment order.

Acceptance:

1. Integration tests verify no-redo behavior and ordered merge.
2. Segment-level status visible in turn metadata/events.

Rollback checkpoint:

- Disable merge/state flag and use legacy response assembly.

### Workstream 4: Argument Repair Policy (Week 4)

Goals:

1. Reduce avoidable tool-call failures via bounded repair rules.

Primary ADR:

- ADR 0030

Target files:

- New module: `backend/services/assistant/tool_argument_repair.py`
- Tool invocation boundary in `backend/services/assistant/*`
- `backend/tests/assistant/test_tool_argument_repair.py`

Tasks:

1. Implement allowlisted repair rules and one-pass revalidation.
2. Add repair outcome metadata to tool-call records.
3. Add clarification path for unrepairable/unsafe cases.

Acceptance:

1. Existing failing fixtures from minor arg errors now pass.
2. Forbidden repair cases remain blocked.

Rollback checkpoint:

- Disable repair flag and restore strict schema-fail behavior.

### Workstream 5: Success Semantics + Observability + Eval Gates (Week 5-6)

Goals:

1. Make completion status policy authoritative.
2. Standardize telemetry/error taxonomy.
3. Enforce scoring-based CI quality gates.

Primary ADRs:

- ADR 0032
- ADR 0033
- ADR 0034

Target files:

- `backend/services/assistant/*` (status evaluation + telemetry emission)
- `backend/contracts/assistant_events.py`
- `backend/tests/assistant/*`
- `backend/tests/contract/*` (if event schema changes)
- `docs/ASSISTANT_EVAL_SPEC.md` (align case schema and scoring)
- CI workflow files under `.github/workflows/*` or equivalent

Tasks:

1. Implement required/optional segment-aware aggregate status logic.
2. Emit canonical failure codes and latency fields.
3. Implement weighted scoring runner and blocking suite gates.

Acceptance:

1. Safety suite blocks on critical policy violations.
2. Mutating false-success patterns no longer pass.
3. CI reports suite scores and baseline deltas.

Rollback checkpoint:

- Disable each policy flag independently; CI gates can be downgraded to report-only mode.

## Required Verification Commands

Use environment-required Python:

1. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant -q"`
2. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/contract -q"`
3. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest -q"` (before enabling blocking gates)

If schemas/events change:

4. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m backend.tools.export_contract_schemas"`

## Program-Level Exit Criteria

Promotion to default-on requires:

1. All blocking eval suites passing at configured thresholds.
2. No critical safety regression for two consecutive release-candidate runs.
3. P95 end-to-end latency increase within accepted budget.
4. Documented rollback drill completed successfully.

## Risks and Mitigations

1. Risk: routing regressions from segmentation/classification drift.
- Mitigation: shadow mode + gold-labeled classification set before activation.

2. Risk: telemetry cardinality/volume explosion.
- Mitigation: code-based label allowlist and sampling/redaction policy.

3. Risk: repair rules accidentally alter mutation semantics.
- Mitigation: forbidden-repair enforcement + mandatory clarification boundaries.

4. Risk: CI instability from flaky model/provider behavior.
- Mitigation: deterministic fixture-heavy suites and controlled retry/quarantine policy.

## Dependency and ADR Governance

This ADR is normative at program level and defers implementation details to subordinate ADRs:

1. ADR 0026: segmentation.
2. ADR 0027: classification.
3. ADR 0028: planner/execution-plan contract.
4. ADR 0029: execution state and merge.
5. ADR 0030: argument repair.

Follow-on ADRs should cite this document as umbrella context.

## Testing Strategy (Program-Level)

1. Require behavior-first assertions (tool family, postconditions, artifact validity, safety policy adherence).
2. Maintain stratified suites for deterministic intents, mixed prompts, analytical prompts, ambiguity handling, and recovery paths.
3. Include replayable regressions for previously failed real prompts.

Detailed scoring and CI gate policy is delegated to follow-on ADRs.

## Consequences

Positive:

- Single coherent architecture narrative for the full improvement effort.
- Clear dependency map and rollout order.
- Measurable definition of performance improvement beyond subjective response quality.

Tradeoffs:

- Additional process overhead to maintain a layered ADR set.
- Requires disciplined telemetry/eval investment to realize gains.

## Rollback

- Disable individual stage feature flags to return to prior behavior.
- If needed, disable deterministic-first enhancements globally and fall back to established model tool-loop baseline.
- Preserve logs/state artifacts for root-cause analysis before re-enabling.

## Non-Goals

- Replacing assistant provider abstractions.
- Re-architecting backend process topology.
- Delivering all follow-on governance ADRs in this single document.
