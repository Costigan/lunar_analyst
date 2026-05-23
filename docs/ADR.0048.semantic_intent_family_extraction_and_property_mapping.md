# ADR.0048: Semantic Intent Family Extraction and Property Mapping

- Status: Accepted
- Date: 2026-04-11
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0011.ai_assistant_and_mcp.md`, `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`, `docs/ADR.0026.spacy_intent_unit_segmentation.md`, `docs/ADR.0027.intent_classification_contract.md`, `docs/ADR.0030.tool_argument_repair_policy.md`, `docs/ADR.0032.read_only_vs_mutating_completion_success_policy.md`, `docs/ADR.0033.assistant_observability_and_failure_taxonomy.md`, `docs/ADR.0043.segment_intent_classification_and_product_request_extraction.md`, `docs/ADR.0047.enhanced_colormap_support.md`, `AGENTS.md`

## Context

Lunar Analyst assistant execution currently combines:

- deterministic regex router actions for selected imperative prompts,
- segment classification with extraction for selected non-command flows,
- model tool-loop fallback for everything else.

This approach is effective for narrow high-confidence commands, but it becomes brittle as domain tasks diversify across landing-site analysis workflows (terrain products, style manipulation, job control, scenario management, route planning, mission-constraint reasoning, and evidence packaging).

Prompt understanding is still concentrated in lexical/pattern coverage. That creates reliability and maintenance costs:

- frequent wording variants require new regex additions,
- semantically clear intents can miss deterministic handling,
- intent-specific properties are reconstructed late in the pipeline instead of extracted explicitly,
- per-family behavior can drift when success semantics and argument normalization are updated incrementally.

## Problem

The assistant lacks a unified semantic intent-family layer that:

1. classifies a segment into a domain intent family,
2. extracts typed properties required to execute or answer the request,
3. maps validated intents deterministically to tool actions or evidence-backed response behavior.

Without this layer, expanding to additional mission-analysis workflows tends to produce ad hoc one-off handling and duplicated routing logic.

## Decision

Adopt a generalized, schema-first **Intent Family Extraction and Property Mapping** architecture by extending the existing `segment_intent_extractor` contract and pipeline integration.

### Decision Summary

1. Add a versioned multi-family intent extraction schema.
2. Extract both `intent_family` and `intent_properties` per segment.
3. Validate extraction output strictly; downgrade safely when invalid/unavailable.
4. Introduce deterministic intent-to-tool planners that convert validated properties into canonical tool calls or clarification outcomes.
5. Keep regex router and model-loop paths as complements, not replacements.
6. Roll out family coverage in phases; do not attempt full-domain implementation in one pass.

## Scope

In scope for this ADR:

- define generalized family taxonomy and extraction contract,
- define reusable property extraction and validation model,
- define deterministic intent-to-tool planner architecture,
- define phased rollout across selected initial families,
- define shared testing, observability, and rollback standards.

Out of scope for this ADR slice:

- implementing all identified families end-to-end now,
- removing regex routing,
- replacing model-loop reasoning for open-ended scientific discussion.

## Intent Family Taxonomy (Initial Target Set)

The taxonomy is additive and versioned. Families may be implemented incrementally.

1. `create_product`
- Generate or derive products/artifacts (rasters/tables/plots/route artifacts).

2. `layer_style_update`
- Apply/manipulate colormaps and style parameters (opacity, tone, contour mode, thresholds).

3. `layer_visibility_update`
- Show/hide/toggle/reorder layer visibility state.

4. `artifact_inspection`
- Describe/preview/stats/readout for produced artifacts.

5. `scenario_context_management`
- Switch/list/select scenario context and scenario-scoped resource targeting.

6. `compute_job_control`
- Launch, cancel, status, logs, and follow-up control for long-running jobs.

7. `programmatic_workflow_authoring`
- Write/edit/run scripts or notebooks when typed tools are insufficient or explicitly requested.

8. `lunar_environment_reasoning`
- Domain-grounded explanatory questions about illumination, terrain, shadowing, communications visibility, and mission implications.

9. `surface_route_planning`
- Route-specific planning over terrain/constraints/cost surfaces, including alternatives and constraint validation.

10. `evidence_packaging`
- Export/share reproducible outputs with provenance for mission review.

## Intent Extraction Contract

### Canonical Shape (Normative)

```json
{
  "class": "intent_family",
  "intent_family": "layer_style_update",
  "text": "Apply the magma colormap to the slope layer.",
  "offsets": { "start": 0, "stop": 44 },
  "intent_properties": {
    "operation": "apply",
    "target": { "layer_ref": "slope" },
    "style": { "kind": "colormap", "colormap_ref": "magma" }
  },
  "confidence": 0.92,
  "validation_status": "validated",
  "downgrade_reason": null
}
```

### Contract Rules

- `intent_family` must be in the configured family enum set.
- `intent_properties` must validate against the family-specific schema.
- Unknown family or invalid properties downgrade to `other` with machine-readable reason.
- Extraction output must be stable under schema versioning (additive evolution preferred).

### Family-Specific Property Schemas

Each family declares a strict schema. Examples:

- `layer_style_update`: operation, target refs, style payload.
- `compute_job_control`: action (`launch|cancel|status|logs`), target job ref, optional log parameters.
- `programmatic_workflow_authoring`: mode (`write|edit|run|write_and_run`), path/ref, runtime mode.
- `surface_route_planning`: origin/target refs, objective weights, traversability constraints, vehicle limits.

## Family Property Contracts (Normative)

This section defines required and optional `intent_properties` fields by family.

### 1) `create_product`

Required:

- `operation` (`create|generate|derive`)
- `product_type`

Optional:

- `inputs` (named source refs),
- `output` (`relative_path`, `publish_layer`),
- `parameters` (family/tool specific),
- `constraints` (dtype, nodata, resampling, time window).

Example:

```json
{
  "operation": "generate",
  "product_type": "slope_raster",
  "inputs": { "dem": "primary_dem.tif" },
  "output": { "relative_path": "slope.tif", "publish_layer": true }
}
```

### 2) `layer_style_update`

Required:

- `operation` (`apply|set|change|update`)
- `target.layer_ref`
- `style.kind`

Optional (by kind):

- `style.colormap_ref`,
- `style.opacity`,
- `style.brightness`,
- `style.contrast`,
- `style.contour` (`interval`, `offset`, `line_color`, `line_width_px`),
- `style.parameters` (for parameterized colormaps).

Example:

```json
{
  "operation": "apply",
  "target": { "layer_ref": "slope" },
  "style": { "kind": "colormap", "colormap_ref": "magma" }
}
```

### 3) `layer_visibility_update`

Required:

- `operation` (`show|hide|toggle|set`)
- `target.layer_ref`

Optional:

- `visible` (required only when `operation=set`),
- `z_index` (optional reorder intent).

Example:

```json
{
  "operation": "show",
  "target": { "layer_ref": "psr" }
}
```

### 4) `artifact_inspection`

Required:

- `operation` (`describe|preview|stats|readout`)
- `target` (`file_id` or `relative_path` or product/layer ref).

Optional:

- `readout` (`x`, `y`, CRS/readout mode),
- `stats` options (band, percentiles, masked behavior).

Example:

```json
{
  "operation": "stats",
  "target": { "relative_path": "slope.tif" }
}
```

### 5) `scenario_context_management`

Required:

- `operation` (`set_current|list|select`)

Optional:

- `scenario_ref`,
- `resource_ref` (file/product/layer follow-on selection).

Example:

```json
{
  "operation": "set_current",
  "scenario_ref": "mons-malapert"
}
```

### 6) `compute_job_control`

Required:

- `operation` (`launch|cancel|status|logs`)

Conditional required:

- for `launch`: `implementation_name` and/or `job_definition_ref`,
- for `cancel|status|logs`: `job_ref` (`job_id` or alias resolution input).

Optional:

- `params` (launch payload),
- `log_options` (`head_lines`, `tail_lines`, `stream`).

Example:

```json
{
  "operation": "logs",
  "job_ref": { "job_id": "job_abc123" },
  "log_options": { "tail_lines": 80, "stream": "combined" }
}
```

### 7) `programmatic_workflow_authoring`

Required:

- `operation` (`write|edit|run|write_and_run`)

Conditional required:

- for `write|edit|write_and_run`: `path_ref` and/or `content_spec`,
- for `run`: `path_ref`.

Optional:

- `runtime_mode` (`osgeo|moonlib`),
- `overwrite_policy`,
- `execution_expectations` (expected outputs/artifacts).

Example:

```json
{
  "operation": "write_and_run",
  "path_ref": { "relative_path": "analyze_site.py" },
  "runtime_mode": "osgeo"
}
```

### 8) `lunar_environment_reasoning`

Required:

- `question_type` (`fact_query|interpretation|mission_impact|method_guidance`)

Optional:

- `region_ref`,
- `time_window`,
- `phenomena` (`illumination`, `psr`, `earth_visibility`, `terrain`, `thermal`),
- `evidence_preference` (`artifact_backed|required_sources`).

Example:

```json
{
  "question_type": "mission_impact",
  "phenomena": ["illumination", "earth_visibility"],
  "region_ref": "candidate_site_a"
}
```

### 9) `surface_route_planning`

Required:

- `operation` (`plan|compare|validate`)
- `origin_ref`
- `destination_ref`

Optional:

- `vehicle_profile`,
- `constraints` (max slope, roughness, shadow/lighting constraints, communication visibility),
- `objective_weights` (`safety`, `distance`, `energy`, `science_value`),
- `alternatives` (requested count).

Example:

```json
{
  "operation": "plan",
  "origin_ref": "lander_site",
  "destination_ref": "sample_region_1",
  "constraints": { "max_slope_deg": 12.0 },
  "objective_weights": { "safety": 0.6, "distance": 0.4 }
}
```

### 10) `evidence_packaging`

Required:

- `operation` (`assemble|export|summarize`)

Optional:

- `scope` (scenario/run/site candidate),
- `artifacts` (requested inclusions),
- `output_format` (`bundle|report|manifest`),
- `provenance_required` (boolean, default true).

Example:

```json
{
  "operation": "export",
  "scope": { "scenario_ref": "mons-malapert" },
  "output_format": "bundle",
  "provenance_required": true
}
```

## Architecture and Flow

### Existing

`segment -> (router OR extractor OR model) -> execution plan -> tool loop`

### Target

1. Segment produced (existing).
2. Deterministic regex router tries fast-path match (existing).
3. If unmatched, semantic extractor classifies into family + properties.
4. Family schema validation runs.
5. Intent-to-tool planner resolves references and builds canonical tool/action plan.
6. Confirmation/safety/postcondition policies execute via existing framework.
7. If unresolved ambiguity or policy block: clarification-required outcome.
8. If extraction unavailable/invalid: safe downgrade to existing fallback paths.

## Intent-to-Tool Planner Model

Each family has an intent-to-tool planner module with shared interface:

- `validate_properties(...)`
- `resolve_references(...)`
- `build_action_plan(...)`
- `postcondition_spec(...)`

Mapper output is deterministic and tool-contract-native; no duplicate compute logic is introduced.

## Local LLM Usage

Use the local LLM-backed extractor (current Ollama pattern) as the semantic parser, with strict boundaries:

- extraction only, not direct mutation execution,
- schema-validated structured output,
- deterministic mapping after extraction,
- explicit fallback when local extractor is unavailable.

This allows broad language coverage while preserving deterministic execution guarantees.

## Detailed Implementation Plan (Tracked Checklist)

### Phase 0: General Contract Foundation

- [x] Add versioned multi-family extraction schema definitions (`intent_family` + family-specific `intent_properties`).
- [x] Add typed extraction result models and parser updates to carry `intent_family`, `intent_properties`, `confidence`, and downgrade metadata.
- [x] Add family-aware telemetry codes/events for extraction lifecycle (attempted, validated, downgraded, unavailable).
- [x] Add startup schema validation guard so invalid extraction schemas fail fast during initialization.
- [x] Add compatibility tests proving existing `create_product` / `other` behavior still passes unchanged.
- [x] Add feature flags for semantic intent-to-tool planning (global + per-family toggles).

Phase 0 completion gate:

- [x] Schema validation passes at startup.
- [x] Existing extraction behavior is backward-compatible in tests.

### Phase 1: First Implemented Families (Reliability Priority)

Target families:

1. `layer_style_update` (initial subtype: colormap apply),
2. `layer_visibility_update`,
3. `compute_job_control` (status/log/cancel first, launch follow-on if stable).

- [x] Extend extractor prompt/few-shot/schema coverage for the three families above.
- [x] Implement planner modules with shared interface (`validate_properties`, `resolve_references`, `build_action_plan`, `postcondition_spec`).
- [x] Wire deterministic tool mapping:
  - `layer_style_update` -> `layer.apply_colormap` / `layer.update_state` as applicable,
  - `layer_visibility_update` -> `layer.update_state`,
  - `compute_job_control` -> `runs.get_status` / `runs.get_logs` / `runs.cancel` (and optionally launch path).
- [x] Add resolver behavior for layer refs and job refs with explicit ambiguity outcomes.
- [x] Extend mutation/postcondition accounting to include newly mapped mutating tools.
- [x] Add integration tests for representative phrasing variants and ambiguity clarification flows.

Phase 1 completion gate:

- [x] Deterministic completion succeeds for representative prompt variants.
- [x] Ambiguous refs produce clarification-required outcomes.
- [x] No regression in existing command-router tests.

### Phase 2: Authoring and Inspection Families

Target families:

- `artifact_inspection`,
- `programmatic_workflow_authoring`,
- expanded `create_product` property extraction.

- [x] Add extraction schemas and few-shot examples for artifact inspection operations (`describe|preview|stats|readout`).
- [x] Add extraction schemas and few-shot examples for script/workflow authoring operations (`write|edit|run|write_and_run`).
- [x] Add planner logic that preserves script guardrails (typed tools preferred, scenario-relative paths, runtime-mode policy).
- [x] Expand `create_product` property extraction for richer input/output parameter capture.
- [x] Add contract tests ensuring these families produce deterministic plans when properties are complete.
- [x] Add eval coverage showing reduction in fallback-to-model rate for these families.

Phase 2 completion gate:

- [x] Eval demonstrates lower fallback-to-model for implemented families versus baseline.
- [x] Script and mutation safety guardrails remain enforced in all passing cases.

### Phase 3: Domain-Reasoning and Planning Families

Target families:

- `lunar_environment_reasoning`,
- `surface_route_planning`,
- `evidence_packaging`.

- [x] Define constrained V1 property schemas for each family (start minimal and executable).
- [x] Implement family planners that support mixed outcomes (tool plans + evidence-backed narrative responses).
- [x] Add provenance/evidence requirements to planner outputs where domain claims are produced.
- [x] Add uncertainty-signaling rules for underconstrained or ambiguous domain prompts.
- [x] Add family-specific eval suites with scenario-grounded prompts and expected behavior checks.
- [x] Iterate schema/mapping based on eval failures before broad enablement.

Phase 3 completion gate:

- [x] Family-specific eval suites exist and pass agreed thresholds.
- [x] Domain responses include explicit uncertainty and evidence provenance where required.

### Cross-Phase Operational Tasks

- [x] Maintain per-family rollout toggles for staged enablement and targeted rollback.
- [x] Publish per-family readiness dashboards from telemetry (validation rate, mapping success, clarification rate, fallback rate).
- [x] Record release notes for each family enablement milestone and known limits.
- [x] Keep regression replay suite updated for newly enabled families.

## Testing and Verification Requirements

- Unit tests:
  - family schema validation,
  - extractor downgrade/error behavior,
  - planner property validation and resolver ambiguity handling.
- Integration tests:
  - segment -> family extraction -> mapped tool execution for implemented families.
- Contract tests:
  - execution-plan labels/modes for each implemented family.
- Eval tests:
  - phrasing diversity per family,
  - fallback rate and clarification rate tracking,
  - mutation postcondition correctness.

## Observability Requirements

Add family-aware metrics and logs:

- extraction attempts by family,
- validation pass/fail by family,
- deterministic mapping success rate,
- clarification-required rate,
- fallback-to-model rate,
- postcondition pass/fail by action type.

All new fields are additive and should align with existing telemetry conventions.

## Risks and Mitigations

Risk: over-scoped rollout creates instability.
Mitigation: phased family rollout with feature flags and per-family gates.

Risk: local extractor unavailability harms reliability.
Mitigation: strict downgrade/fallback paths with explicit telemetry and health checks.

Risk: family overlap leads to inconsistent routing.
Mitigation: explicit family precedence policy and conflict-resolution rules.

Risk: silent semantic drift in extracted properties.
Mitigation: schema pinning, contract tests, and eval regression baselines.

## Rollback Plan

- Feature-flag semantic intent-to-tool planning by family.
- On regression:
  1. disable affected family planner,
  2. retain regex router + model-loop behavior,
  3. keep telemetry for diagnosis and targeted re-enable.

No DB migration rollback is required for baseline contract adoption.

## Definition of Done for Each New Family

A family is considered production-ready only when:

- extraction schema and planner contracts are versioned and documented,
- unit/integration/eval coverage exists,
- postcondition policy is defined for mutating actions,
- clarification/fallback behaviors are explicit,
- rollout flag and rollback path are validated.

## Notes on ADR 0047 Alignment

This ADR generalizes the approach used for colormap-intent reliability under ADR 0047 into a reusable multi-family architecture. Colormap manipulation remains an early implementation target inside `layer_style_update`, not the full scope.
