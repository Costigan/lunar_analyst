# ADR 0026: spaCy-Based Prompt Segmentation for Hybrid Assistant Routing

- Status: Accepted
- Date: 2026-03-18
- Owners: Architecture (Codex), Implementation (TBD)
- Related: `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`, `docs/ADR.0023.deterministic_router_with_bounded_agent_substeps.md`, `docs/ADR.0025.assistant_eval_pyramid_for_routing_functional_and_domain_quality.md`, `docs/DESIGN.md`

## Context

The assistant currently supports hybrid execution: deterministic routing for narrow imperative intents and model tool-loop handling for open-ended analysis. Mixed prompts often contain both classes in one turn.

Examples:

1. `Switch to Shackleton scenario. Turn on slope and hillshade. Then recommend top candidate landing zones.`
2. `Show illumination map, and if coverage is low near the rim, suggest alternatives.`

For mixed prompts, we need stable segmentation into ordered prompt segments before prompt classification and execution planning. Current ad hoc splitting logic is brittle around conjunctions, abbreviations, and conditionals, and can over-fragment or incorrectly route complex clauses.

We need a deterministic, testable segmenter that:

- preserves user order and text offsets,
- supports partial deterministic execution with model continuation,
- is fast enough for interactive turns in a low user-count deployment,
- degrades safely when confidence is low.

## Decision

Adopt a spaCy-based segmenter as the canonical implementation for assistant prompt segmentation, with layered deterministic clause rules and no non-spaCy fallback path.

1. Use spaCy for sentence boundaries plus dependency/POS features.
- Base model: `en_core_web_sm` (default).
- Keep model choice configurable for future tuning.

2. Build prompt segments in two passes.
- Pass A: sentence segmentation with span offsets.
- Pass B: rule-based clause splitting on orchestration connectors (`then`, `also`, `and then`, newline/semicolon separators) only when syntactic cues indicate separate imperative actions.

3. Apply complexity guards to prevent unsafe deterministic splitting.
- If a unit contains conditional/comparative markers (`if`, `unless`, `only if`, `except`, `while`, `compare`, `tradeoff`), keep the clause intact and mark as model-preferred.

4. Emit structured segment metadata for downstream planning.
- Required fields: `segment_id`, `text`, `start_char`, `end_char`, `is_imperative_candidate`, `has_complexity_guard`, `segmentation_confidence`.

5. Treat spaCy availability and model installation as required runtime prerequisites.
- If spaCy or the configured model cannot be loaded, assistant startup/runtime initialization should fail clearly rather than silently falling back to a different sentence splitter.

## Why spaCy

1. Better clause-level features than regex-only splitters.
- Dependency labels and POS tags allow safer decisions on whether conjunctions represent separate commands.

2. Good operational fit.
- Python-native, stable ecosystem, straightforward integration in FastAPI backend.

3. Latency acceptable for this product profile.
- Expected per-turn segmentation overhead remains in millisecond to low-hundreds-of-milliseconds range on typical workstation CPUs; this is generally smaller than model and tool latency.

## Rejected Alternatives

1. Regex-only segmentation.
- Too brittle for mixed imperative + analytical phrasing and high risk of false deterministic matches.

2. LLM-only segmentation.
- Non-deterministic behavior, extra token/latency cost, and difficult-to-test boundary decisions.

3. Lightweight sentence splitters only (`blingfire`, `pysbd`, `syntok`) without parsing.
- Useful for first-pass boundaries, but insufficient alone for robust clause-level prompt segments.

## Architecture

### A. Segmenter Placement

- Implement in assistant routing layer before deterministic action matching.
- Segmenter output feeds planner in ADR 0022 flow.

### B. Processing Pipeline

1. Normalize prompt text and preserve original char offsets.
2. Protect spans that should not be split (quoted strings, JSON-like argument blocks, file paths/timestamps).
3. Run spaCy sentence segmentation.
4. Apply clause split rules on candidate connectors with parse-based checks.
5. Run complexity guard to suppress over-segmentation.
6. Emit ordered prompt segments with metadata and confidence.
7. Hand units to deterministic planner/classifier.

### C. Execution Contract with Hybrid Router

- Deterministic-matched units execute first in order.
- Unmatched or guarded units are forwarded as remainder to model tool-loop with deterministic execution trace/state summary.
- Per-unit provenance is logged as `execution_origin` (`deterministic` or `model_reasoned`).

## Latency and Reliability Targets

1. P50 segmenter latency: <= 25 ms for typical single-turn prompts.
2. P95 segmenter latency: <= 120 ms for longer multi-sentence prompts.
3. spaCy model availability is required for supported runtime environments.

These are internal engineering targets and can be revised from telemetry.

## Observability

Add structured logs and metrics:

- `segment_count`
- `segment_lengths`
- `segmentation_confidence`
- `complexity_guard_hits`
- `spacy_model_name`
- per-segment route decision (`router_candidate` vs `model_required`)

Do not log sensitive full prompt text in high-verbosity operational logs.

## Testing Strategy

1. Unit tests (deterministic)
- Prompt -> expected ordered segments and offsets.
- Clause split correctness on coordination patterns.
- Guard behavior on conditional/comparative language.
- Protected span behavior (quotes/JSON/path/time tokens).

2. Routing integration tests
- Mixed prompts execute deterministic units first and route remainder to model path.
- Failed deterministic unit blocks dependent units and surfaces recoverable status.

3. Eval-suite additions (ADR 0025 alignment)
- Add segmentation-focused benchmark set with expected per-unit routing labels.
- Track segmentation precision/recall against a gold-labeled prompt set.

## Consequences

Positive:

- Higher reliability for mixed prompts without forcing all-or-nothing model routing.
- Better testability and regression detection at segmentation boundary.
- Cleaner separation between segmentation, prompt classification, and execution.

Tradeoffs:

- New dependency and model artifact management.
- Need for tuning domain-specific split/guard rules over time.
- Slight added latency per turn.

## Rollout

1. Feature flag: `backend.llm.prompt_segmentation_enabled`.
2. Shadow mode first: compute segments and log decisions while existing behavior remains authoritative.
3. Promote to active mode after segmentation benchmark and routing regression thresholds pass.
4. Keep immediate rollback via feature flag disable.

## Detailed Implementation Plan

### Phase 1: Dependency and Configuration Wiring

Goals:

1. Add spaCy dependency and model configuration as required runtime prerequisites.
2. Ensure startup/config path supports selecting model and thresholds.

Target files:

- `backend/pyproject.toml` or `requirements*.txt`
- `backend/config/settings.py` (or equivalent)
- `config/lunar_analyst.toml`
- `backend/tests/assistant/test_segmentation_config.py`

Tasks:

1. Add configurable fields:
- `backend.llm.prompt_segmentation_enabled`
- `backend.llm.segmenter_model`
- `backend.llm.segmenter_confidence_threshold`

2. Add startup validation for missing model.

Acceptance:

1. Missing spaCy model yields clear startup/runtime error message.
2. Supported environments install the configured model before assistant startup.

Rollback:

- Restore prior prompt-segmentation implementation if this requirement must be relaxed.

### Phase 2: Segmenter Module and Contracts

Goals:

1. Implement deterministic segmenter with offset-safe output schema.

Target files:

- New module: `backend/services/assistant/prompt_segmenter.py`
- `backend/services/assistant/models.py` (segment DTO)
- `backend/tests/assistant/test_prompt_segmenter.py`

Tasks:

1. Implement normalization + protected-span preprocessing.
2. Implement sentence pass + clause split pass.
3. Emit required metadata fields and confidence score.

Acceptance:

1. Unit tests pass for offsets, order, and protected-span behavior.
2. Segmenter output schema stable and serializable.

Rollback:

- Keep module present, bypassed by feature flag.

### Phase 3: Router Integration in Shadow Mode

Goals:

1. Produce segmentation artifacts/telemetry without changing authoritative routing.

Target files:

- `backend/services/assistant/command_router.py`
- `backend/services/assistant/assistant_service.py`
- `backend/tests/assistant/test_segmentation_shadow_mode.py`

Tasks:

1. Call segmenter before deterministic matcher when flag enabled.
2. Emit shadow logs/metrics for segments and confidence.
3. Preserve existing routing path as authoritative.

Acceptance:

1. Integration tests confirm no routing behavior changes in shadow mode.
2. Telemetry includes `segment_count`, confidence, fallback flags.

Rollback:

- Disable feature flag.

### Phase 4: Active Routing Cutover

Goals:

1. Use segmenter output as authoritative planner input.

Target files:

- `backend/services/assistant/command_router.py`
- `backend/tests/assistant/test_segmentation_routing_integration.py`

Tasks:

1. Enable full-prompt fallback on low-confidence segmentation.
2. Enforce complexity guards before deterministic execution planning.

Acceptance:

1. Mixed-prompt routing regression suite passes.
2. No hard failures from segmentation errors (fallback always available).

Rollback:

- Revert to shadow mode by disabling active flag behavior.

## Verification Commands

1. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_prompt_segmenter.py -q"`
2. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_segmentation_shadow_mode.py -q"`
3. `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/assistant/test_segmentation_routing_integration.py -q"`

## Exit Criteria

1. Segmentation benchmark meets P50/P95 targets.
2. Gold segmentation label set passes configured precision/recall threshold.
3. Mixed-turn regression suite shows no critical routing regressions.

## Non-Goals

- Replacing deterministic action registry or tool contracts.
- Performing semantic prompt classification inside segmentation module.
- Solving long-horizon autonomous planning beyond single-turn intent-unit decomposition.
