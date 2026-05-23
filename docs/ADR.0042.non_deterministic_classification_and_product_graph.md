# ADR.0042: Canonical File Product Dictionary and Deterministic Recipe Templates

- Status: Accepted (classification superseded by ADR.0043; retained as product-generation guidance)
- Date: 2026-04-05
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0002.scenario_filesystem_and_catalog.md`, `docs/ADR.0011.ai_assistant_and_mcp.md`, `docs/ADR.0019.unified_tool_model.md`, `docs/ADR.0028.turn_planner_json_contract.md`, `docs/ADR.0029.per_segment_execution_state_and_merge_policy.md`, `docs/ADR.0033.assistant_observability_and_failure_taxonomy.md`, `docs/ADR.0035.typed_entity_memory_and_reference_resolution_v1.md`, `docs/ADR.0043.segment_intent_classification_and_product_request_extraction.md`

## Context

The assistant pipeline already supports segmented prompt handling, deterministic command execution paths, and model-guided continuation.

The unresolved reliability problem is narrower:

- robust creation of known canonical file products, especially raster products;
- deterministic prerequisite handling for those products;
- and replayable/evaluable behavior across model and prompt changes.

The team later replaced ADR 0042's segment-classification framing with `ADR.0043`.

This document now keeps only the product-generation ideas that still matter:

- structured product intent extraction;
- closed-world canonical product vocabulary;
- deterministic recipe selection and prerequisite expansion;
- execution through existing governed tools/jobs;
- explicit open-ended fallback for non-canonical requests.

## Relationship to ADR 0043

`ADR.0043` is authoritative for segment classification and extraction contract shape (`command`, `create_product`, `other`).

This ADR does not define or override segment classes. It assumes upstream classification/extraction has already produced a validated product intent payload and focuses only on canonical product generation semantics.

## Problem

When users ask for products such as:

- slope rasters;
- hillshade rasters;
- threshold masks;
- combined masks;
- lighting and visibility rasters;

the system must translate intent into reliable, auditable execution.

If recipe planning stays prompt-driven for known products, behavior drifts and prerequisite handling becomes inconsistent. That weakens eval repeatability and operator trust.

## Decision

For canonical file products, adopt deterministic product-generation contracts:

1. Use structured product intent (from ADR 0043 pipeline) as recipe-planning input.
2. Maintain a closed-world canonical product dictionary.
3. Maintain deterministic recipe templates per canonical product type.
4. Expand prerequisites recursively but narrowly and deterministically.
5. Execute only through existing governed tool/job contracts.
6. Keep import/publish/layer/UI actions outside recipe templates.
7. Route unsupported or non-canonical requests to open-ended script/notebook or other governed fallback paths.

## Architecture

### 1. Input Contract for Product Planning

Input to this layer is a validated product intent object produced upstream (see ADR 0043). This layer treats that object as data, not as free-form prose.

Minimum expected fields:

- canonical or candidate `product_type`;
- product-defining parameters (for example operator/threshold);
- source references or source product hints when present.

If upstream cannot produce valid product intent, this layer returns structured failure (`product_request_unparseable`) instead of guessing a recipe.

### 2. Canonical Product Dictionary

Maintain a closed-world dictionary of canonical file product types.

Illustrative entries:

- `dem`
- `hillshade_raster`
- `slope_raster`
- `aspect_raster`
- `threshold_mask`
- `combined_mask`
- `horizon_set`
- `illumination_raster`
- `earth_visibility_duration_raster`
- `combined_sun_earth_max_contiguous_duration_raster`
- `psr_raster`

Closed-world rule:

- in dictionary: deterministic recipe path allowed;
- not in dictionary: deterministic canonical path not allowed.

### 3. Deterministic Recipe Templates

Each canonical product type maps to one or more deterministic templates.

Each template defines:

- produced `product_type`;
- prerequisite product types;
- governed execution reference (`implementation_name`);
- required product-defining parameters;
- parameter mapping into governed tool arguments;
- conservative reuse keys.

Illustrative template:

```yaml
product_type: slope_raster
requires:
  - dem
execution_ref:
  implementation_name: raster.calculate
parameter_mapping:
  expression_template: "slope({dem})"
reuse_keys:
  - scenario_id
  - source_product_id
  - crs
  - resolution
  - parameter_hash
```

Illustrative threshold-mask template:

```yaml
product_type: threshold_mask
requires:
  - source_raster
execution_ref:
  implementation_name: raster.calculate
required_parameters:
  - operator
  - threshold
parameter_mapping:
  expression_template: "({source_raster}) {operator} {threshold}"
reuse_keys:
  - scenario_id
  - source_product_id
  - parameter_hash
```

### 4. Limited Prerequisite Expansion

Planning remains intentionally narrow:

- choose recipe for requested canonical product;
- ensure prerequisites exist or generate them via canonical templates;
- execute in dependency order.

Not allowed in this layer:

- arbitrary workflow graph search;
- broad multi-goal optimization;
- unconstrained synthesis over open operation sets.

### 5. Contract Authority Boundaries

Recipe catalog remains thin.

Authority separation:

- tool arg schemas, confirmations, execution semantics: governed tools/jobs;
- product/file persistence and metadata: scenario files + `scenario.db`;
- this ADR layer: product vocabulary, prerequisite relationships, recipe parameter mapping, and reuse logic.

No parallel tool-contract system may be introduced here.

### 6. Reuse Keys

Reuse checks are conservative and deterministic.

Illustrative keys:

- `scenario_id`
- `source_product_id`
- `crs`
- `resolution`
- `parameter_hash`

Goal: avoid wrong reuse while allowing replayable reuse of clearly equivalent prior outputs.

### 7. Open-Ended Fallback

When canonical planning cannot proceed, route to governed open-ended paths (for example script/notebook generation) rather than inventing unsupported canonical recipes.

Fallback triggers include:

- unknown product type;
- missing required parameters;
- unsupported prerequisite chain;
- no supported recipe variant.

### 8. Product-Handling Actions Are Separate

Actions such as import/add-layer/show/move/update-visibility are intentionally outside recipe templates and should remain in deterministic tool/action handling layers.

## Failure Handling

Canonical planning failures must be explicit and machine-readable.

Illustrative reason codes:

- `product_request_unparseable`
- `unknown_canonical_product_type`
- `missing_required_product_parameter`
- `missing_prerequisite_product`
- `no_supported_recipe`
- `execution_ref_unavailable`

Failure payload should include:

- original request segment text;
- normalized product type (if available);
- missing parameters/prerequisites;
- failing recipe id or execution reference;
- machine reason code.

## Consequences

### Positive

- Canonical product generation becomes stable and testable.
- Recipe behavior is explicit and replayable.
- Model drift has less impact on known-product paths.
- Non-canonical requests remain possible via open-ended governed execution.

### Tradeoffs

- Requires maintaining canonical product dictionary and recipe catalog.
- Requires eval coverage for extraction-to-recipe handoff quality.
- Some requests intentionally fall back rather than forcing weak deterministic behavior.

## Out of Scope

- Segment classification ontology and prompt-level class labels (ADR 0043).
- Persistent constraints modeling and UI behavior (ADR 0043).
- General workflow synthesis over arbitrary operations.
- Replacing governed execution/tool contracts.
- Folding import/publish/layer actions into recipe templates.

## Detailed Implementation Plan

Line references below are from the current tree as of 2026-04-09 and are intended as implementation anchors.

### Phase A: Catalog and Recipe Contract Hardening

- [x] Extend canonical dictionary contract in `backend/services/assistant/product_type_dictionary.py`:
  - change `ProductTypeSpec` at line 18 to add recipe linkage fields (`canonical_recipe_ids`, `reuse_keys`, `required_parameters`);
  - change `validate_product_type_dictionary()` at line 279 to enforce those new fields for deterministic product types.
- [x] Write new module `backend/services/assistant/canonical_recipe_catalog.py` with:
  - `RecipeTemplateSpec` dataclass;
  - `load_recipe_catalog()`;
  - `validate_recipe_catalog()`;
  - `recipe_ids_for_product_type(product_type: str)`.
- [x] Keep ADR 0043 boundary explicit:
  - no segment-class changes here; input remains `create_product` payload from `SegmentIntentExtractor.classify_or_other()` (`backend/services/assistant/segment_intent_extractor.py`, line 53).

### Phase B: Deterministic Planner Refactor (Core ADR 0042 Work)

- [x] Refactor `CreateProductPlanner` in `backend/services/assistant/create_product_planner.py`:
  - change `plan()` at line 93 to become dictionary/recipe-driven rather than hardcoded `SUPPORTED_PRODUCT_TYPES`;
  - change `_plan_dem_derived()` at line 132 and `_plan_threshold_mask()` at line 169 to compile from selected recipe templates;
  - change `_find_existing_output()` at line 300 to use recipe `reuse_keys` metadata instead of output-path-only matching.
- [x] Add new planner functions in `create_product_planner.py`:
  - `select_recipe_for_classification(...)`;
  - `expand_prerequisites(...)`;
  - `compile_recipe_step_to_tool_call(...)`;
  - `resolve_required_parameters(...)`;
  - `compute_reuse_key_fingerprint(...)`;
  - `find_reusable_product(...)`;
  - `build_structured_block(reason_code: str, ...)`.
- [x] Preserve narrow scope:
  - do not add arbitrary graph search;
  - keep prerequisite expansion bounded and only over canonical product dependencies.

### Phase C: Assistant Execution Integration

- [x] Update ordered segment execution wiring in `backend/services/assistant/assistant_service.py`:
  - change create-product dispatch block at lines 1676-1743 to support multi-step recipe execution outcomes (prerequisites + target);
  - change `_scenario_product_inventory()` at line 1781 to collect metadata required by `reuse_keys` checks (existing references, file paths, and recipe-relevant attrs);
  - add `self._execute_create_product_recipe(...)` and `self._execute_recipe_step(...)` helper functions near the current create-product branch.
- [x] Extend execution plan metadata in `backend/services/assistant/turn_execution_plan.py`:
  - change `ExecutionPlanSegmentRecord` (line 20) to include optional product-planning metadata (`requested_product_type`, `selected_recipe_id`, `prerequisite_count`);
  - change `TurnExecutionPlanBuilder.build()` (line 48) to populate those fields for `create_product` segments.
- [x] Ensure turn-state merge/handoff can expose recipe context:
  - change `TurnStateManager.build_merge()` in `backend/services/assistant/turn_state_manager.py` (line 106);
  - add `recipe_summary` and `prerequisite_outcomes` in merge payloads for eval traceability.

### Phase D: Failure Taxonomy and Telemetry

- [x] Extend telemetry reason-code vocabulary in `backend/services/assistant/telemetry_codes.py`:
  - add constants for `product_request_unparseable`, `unknown_canonical_product_type`, `missing_required_product_parameter`, `missing_prerequisite_product`, `no_supported_recipe`, `execution_ref_unavailable`.
- [x] Emit structured create-product failure metadata from:
  - `CreateProductPlanner.build_structured_block(...)` (new);
  - `assistant_service.py` blocked path around lines 1706-1733.
- [x] Keep machine-readable codes stable for eval assertions and UI rendering.
  - Status: validated in worker + contract runs.

### Phase E: Tests and Eval Coverage (Must Add Before Declaring Complete)

- [x] Update/expand unit tests for planner/catalog:
  - extend `backend/tests/worker/test_product_type_dictionary.py` (`test_product_type_dictionary_is_complete`, line 6);
  - add new `backend/tests/worker/test_canonical_recipe_catalog.py` with catalog validation and recipe lookup tests;
  - add new `backend/tests/worker/test_create_product_planner.py` with prerequisite expansion, reuse-key matching, and structured block reasons.
- [x] Update assistant integration tests:
  - extend `backend/tests/worker/test_assistant_hybrid_metadata.py` create-product tests at lines 171, 211, 246, 272 to cover deterministic recipe selection, prerequisite execution order, and fallback correctness.
- [x] Update execution-plan tests:
  - extend `backend/tests/worker/test_turn_execution_plan.py` (`test_turn_execution_plan_builds_ordered_execution_modes`, line 14) to assert new product-planning metadata fields.
- [x] Add contract/eval fixtures for canonical routing and fallback:
  - extend `backend/tests/fixtures/assistant_segmentation_classification/golden_cases_v2.jsonl`;
  - add create-product eval cases for unknown product types and missing parameters.

### Phase F: Rollout, Guardrails, and Rollback

- [x] Add a feature flag for recipe-catalog execution path under `[backend.llm]` (for example `create_product_recipe_catalog_enabled`) with default off until eval quality gates pass.
- [x] Run required checks:
  - `.venv/bin/python -m pytest backend/tests/worker -q`
  - `.venv/bin/python -m pytest backend/tests/contract -q`
  - Status: complete (`worker`: 409 passed, 1 skipped; `contract`: 102 passed).
- [x] Rollback plan:
  - revert to current `CreateProductPlanner.plan()` behavior (single-step hardcoded path) by disabling the feature flag;
  - keep ADR 0043 classification behavior unchanged during rollback.
  - Status: implemented via `backend.llm.create_product_recipe_catalog_enabled=false` (default in repo configs).

If future evals show this narrow architecture is insufficient, add complexity incrementally, but keep deterministic known-product behavior as the baseline for canonical products.
