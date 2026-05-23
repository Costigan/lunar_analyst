# ADR.0025: Assistant Evaluation Pyramid for Routing, Functional Task Success, and Domain QA

Status: Accepted (Amended by ADR.0038 for raster-transform pre-filter eligibility taxonomy)  
Date: 2026-03-12  
Deciders: Lunar Analyst architecture team

## Context

`docs/ASSISTANT_EVAL_SPEC.md` defines a v1 benchmark focused on:
- routing mode (`tool_call` vs `clarify` vs `respond`);
- tool selection and argument shape;
- repair/clarification behavior;
- unsafe-call blocking.

That benchmark is effective for deterministic routing regression and tool-call contract hygiene, but it is not sufficient for model selection or improvement on analyst-critical outcomes:
- producing correct raster/vector outputs via script authoring/execution;
- producing correct raster/vector outputs via map algebra DSL;
- manipulating created products (for example visibility/opacity);
- producing tables/plots with valid structure;
- answering lunar environment and mission-design questions with factual rigor.

## Goals

1. Preserve fast deterministic routing/safety regression coverage.
2. Add execution-based validation of artifact-producing workflows.
3. Add state-transition validation for product manipulation workflows.
4. Add rubric-based domain QA evaluation with provenance requirements.
5. Enable side-by-side provider/model comparison on metrics aligned to analyst value.

## Non-Goals

- Replacing existing `benchmark.xlsx` routing benchmark.
- Requiring pixel-perfect equality for all raster/vector outputs in first iteration.
- Fully automating scientific truth adjudication without human review.

## Decision

Adopt a three-tier assistant evaluation architecture:

1. **Tier 1: Routing Contract Benchmark (existing v1)**
- Keep `backend/evals/assistant/benchmark.xlsx` + `run_benchmark.py` + `score.py` as a fast gate.
- Continue enforcing mode/tool/args/safety gates.

2. **Tier 2: Functional Integration Evals (new)**
- Execute assistant turns end-to-end.
- Validate produced artifacts and side effects (files, metadata, layer/product state, table/plot contracts).
- Score task success against structured per-case expectations.

3. **Tier 3: Domain QA Evals (new)**
- Evaluate long-form lunar environment and mission-design answers with rubric scoring.
- Require retrieval/source provenance checks for retrieval-dependent prompts.
- Use LLM-as-judge plus periodic human calibration set.

This yields an evaluation pyramid: fast deterministic checks at the base, slower behavioral correctness in the middle, and expert-level domain quality at the top.

## Rationale

- Routing correctness is necessary but not sufficient for analyst outcomes.
- Execution-based testing is needed where output validity is the real objective.
- Domain QA quality requires semantic assessment that exact-match scoring cannot capture.
- Tiering preserves CI speed while adding mission-relevant signal for model improvement decisions.

## External References

The approach builds on established techniques:

1. **Test Pyramid / layered test strategy**
- Martin Fowler, "The Practical Test Pyramid":  
  https://martinfowler.com/articles/practical-test-pyramid.html

2. **Execution-based evaluation for code/task correctness**
- HumanEval (functional correctness via test execution):  
  https://arxiv.org/abs/2107.03374
- SWE-bench (realistic task-level software evaluation):  
  https://arxiv.org/abs/2310.06770

3. **LLM-as-judge for open-ended response quality**
- MT-Bench / LLM-as-a-judge framing:  
  https://arxiv.org/abs/2306.05685
- G-Eval rubric-based judging:  
  https://arxiv.org/abs/2303.16634

4. **RAG evaluation concepts (faithfulness/relevance)**
- RAGAS framework:  
  https://arxiv.org/abs/2309.15217

5. **Oracle-problem mitigation via relation/property-based checks**
- Metamorphic testing overview (when exact oracles are hard):  
  https://www.researchgate.net/publication/322261865_Metamorphic_Testing_A_Review_of_Challenges_and_Opportunities

## Architecture and Data Model Changes

Add new eval assets under `backend/evals/assistant/`:

- `functional_benchmark.xlsx` (or `.csv`): Tier 2 case definitions.
- `domain_benchmark.xlsx` (or `.csv`): Tier 3 case definitions.
- `run_functional_benchmark.py`: functional runner.
- `score_functional.py`: functional scorer.
- `run_domain_benchmark.py`: domain runner.
- `score_domain.py`: domain scorer (rubric + provenance checks).

Keep existing:
- `benchmark.xlsx`, `run_benchmark.py`, `score.py` for Tier 1.

### Tier 2 Case Schema (proposed)

Core fields:
- `id`, `category`, `prompt`, `scenario_id`, `inject_scenario_context`
- `expected_mode`, `expected_primary_tool`
- `execution_required` (bool)
- `postconditions_json` (JSON object)

`postconditions_json` supports:
- artifact checks:
  - `must_create_files`: list of scenario-relative paths/patterns
  - `raster_constraints`: CRS, width/height/band count, nodata/alpha expectations, COG validity
  - `vector_constraints`: geometry type/count bounds, CRS, attribute schema
  - `table_constraints`: required columns, minimum rows
  - `plot_constraints`: artifact type, file existence, format
- numerical checks:
  - stats bounds/tolerances (min/max/mean/std)
  - nodata-ratio tolerance (for example +/- 1% vs baseline)
  - optional golden-reference comparison with tolerance
  - pixel/hash equality only for deterministic transforms where environment variance is controlled
- state checks:
  - layer visibility/opacity/order/style expectations
  - product registration expectations

### Tier 3 Case Schema (proposed)

Core fields:
- `id`, `category`, `prompt`, `scenario_id`, `inject_scenario_context`
- `golden_points_json` (bullet facts expected in strong answers)
- `must_cite_channels` (for example `domain`, `procedural`)
- `judge_rubric_json` (criteria, scale, weights, pass threshold)

## Detailed Implementation Plan

### Phase 0: Baseline and Governance

1. Keep Tier 1 unchanged as release gate.
2. Add `docs/ASSISTANT_EVAL_SPEC_V2.md` to define Tier 2/3 schemas and metrics.
3. Add benchmark data governance:
- append-only ids;
- versioned case files;
- tagged calibration subsets for stable comparisons.
- deterministic runner config for reproducibility:
  - fixed seeds where supported;
  - pinned fixture versions;
  - explicit timeout and transient-retry policy.

### Phase 1: Tier 2 Functional Runner

1. Implement `run_functional_benchmark.py`:
- create isolated assistant session per case;
- execute turn with scenario context;
- resolve confirmations using configured policy;
- wait for tool/job completion when `execution_required=true`;
- capture emitted artifacts, tool calls, assistant metadata, runtime durations.

2. Implement verification engine:
- reusable validators for raster/vector/table/plot/state postconditions;
- scenario-root-safe file resolution only;
- structured pass/fail evidence per assertion.

3. Implement `score_functional.py` metrics:
- `task_success_rate`
- `artifact_validity_rate`
- `state_transition_accuracy`
- `numeric_tolerance_pass_rate`
- `first_try_task_success_rate`
- `unsafe_policy_compliance_rate`

4. Define initial functional gate targets (proposed):
- `task_success_rate >= 0.75`
- `artifact_validity_rate >= 0.90`
- `unsafe_policy_compliance_rate = 1.00`

5. Add runtime failure taxonomy and evidence capture:
- collect tool/job stderr and normalized failure codes per case;
- minimum failure categories:
  - `runtime_import_error`
  - `runtime_syntax_error`
  - `tool_argument_validation_error`
  - `job_execution_error`
  - `postcondition_assertion_failed`
- include categories in score outputs and model-comparison reports.

### Phase 1.5: Fixture Isolation and State Hygiene

1. Ensure each Tier 2 case runs against a pristine scenario fixture:
- preferred: per-case scenario clone in an isolated temp directory under allowed roots;
- fallback: snapshot/restore of scenario DB and managed artifact paths.

2. Enforce teardown regardless of case outcome:
- restore/delete fixture on pass, fail, timeout, or interruption.

3. Add pollution guards:
- pre/post checks that only expected paths and DB rows changed;
- mark case as infra-failure if cleanup/restore fails.

### Phase 2: Tier 2 Case Authoring for Analyst Priorities

Author initial 40-60 functional cases across:
1. script-based raster/vector generation;
2. DSL-based raster/vector generation;
3. product manipulation/display flows;
4. table/plot generation.

Case design rules:
- include both direct and under-specified prompts (clarification expected);
- include negative/safety variants;
- include semantically equivalent DSL variants (metamorphic pairs) to avoid brittle string matching.

### Phase 3: Tier 3 Domain QA Runner + Judge

1. Implement `run_domain_benchmark.py`:
- run prompts and capture final responses plus metadata/source references.

2. Implement `score_domain.py`:
- deterministic checks:
  - required provenance presence and channel match;
  - refusal/safety policy for unsafe scientific requests.
- judge-based checks:
  - rubric dimensions: factual accuracy, faithfulness to sources, scientific rigor, uncertainty handling, actionability;
  - weighted score and per-dimension thresholds.

3. Add judge reliability controls:
- fixed judge prompt template and temperature;
- dual-judge option (primary + tie-break);
- calibration subset scored by humans each release cycle.

### Phase 4: Reporting and Model Comparison

1. Add unified report generator (`eval_report.py`) that merges Tier 1/2/3 results.
2. Emit:
- per-model scorecards;
- confidence intervals for key metrics;
- regression deltas vs baseline model/version.
3. Add CI tiers:
- PR gate: Tier 1 required, Tier 2 smoke subset optional/required by touched area.
- nightly: full Tier 2 + Tier 3.

### Phase 5: Rollout and Safety

1. Start with non-blocking informational runs for Tier 2/3.
2. Promote to blocking gates after 2-3 stable cycles and calibrated thresholds.
3. Keep rollback path:
- if Tier 2/3 instability is high, keep Tier 1 as sole hard gate and continue collecting Tier 2/3 telemetry.

## Acceptance Criteria

1. Tier 1 remains green with no safety regression.
2. Tier 2 validates at least one successful end-to-end case in each priority area:
- script product generation;
- DSL product generation;
- product manipulation;
- table/plot generation.
3. Tier 3 includes at least 25 curated lunar science/mission-design prompts with rubric scoring and provenance checks.
4. Unified per-model report supports ranking candidate models for Lunar Analyst workloads.

## Risks and Mitigations

- **Risk:** Judge drift/bias.  
  **Mitigation:** fixed prompts, dual-judge option, human calibration set.

- **Risk:** Functional eval flakiness due to runtime/environment variance.  
  **Mitigation:** isolated per-case fixtures, semantic raster tolerances (not raw hash), deterministic seeds where possible, retry policy for transient infra failures.

- **Risk:** Cross-case scenario-state pollution causing non-repeatable results.  
  **Mitigation:** Phase 1.5 fixture isolation, guaranteed teardown, and pollution guards.

- **Risk:** Overfitting to benchmark phrasing.  
  **Mitigation:** paraphrase variants, metamorphic case pairs, holdout set not used for prompt tuning.

- **Risk:** Runtime cost growth.  
  **Mitigation:** tiered schedule (PR smoke vs nightly full), caching fixture setup, bounded timeouts.

## Consequences

Positive:
- Model selection aligns with mission-relevant outcomes, not only routing form.
- Faster detection of regressions in artifact quality and domain-answer quality.
- Better signal for improving prompts/router/tool contracts.

Negative:
- More complex benchmark infrastructure.
- Longer runtime for full evaluation cycles.

## Related

- `docs/ASSISTANT_EVAL_SPEC.md`
- `docs/ADR.0011.ai_assistant_and_mcp.md`
- `docs/ADR.0015.rich_assistant_outputs_with_marimo_components.md`
- `docs/ADR.0016.map_algebra_dsl.md`
- `docs/ADR.0018.scripted_map_algebra.md`
- `docs/ADR.0019.unified_tool_model.md`
- `docs/ADR.0021.assistant_rag_wrapper_and_scenario_index.md`
- `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`
- `docs/ADR.0023.deterministic_router_with_bounded_agent_substeps.md`
