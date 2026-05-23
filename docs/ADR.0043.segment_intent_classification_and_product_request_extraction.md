# ADR.0043: Segment Intent Classification and Structured Product Request Extraction

- Status: Accepted
- Date: 2026-04-06
- Owners: Lunar Analyst architecture team
- Supersedes: `docs/ADR.0042.non_deterministic_classification_and_product_graph.md`
- Related: `docs/DESIGN.md`, `docs/how-segments-are-classified.md`, `docs/ADR.0002.scenario_filesystem_and_catalog.md`, `docs/ADR.0011.ai_assistant_and_mcp.md`, `docs/ADR.0019.unified_tool_model.md`, `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`, `docs/ADR.0026.spacy_intent_unit_segmentation.md`, `docs/ADR.0027.intent_classification_contract.md`, `docs/ADR.0028.turn_planner_json_contract.md`, `docs/ADR.0029.per_segment_execution_state_and_merge_policy.md`, `docs/ADR.0033.assistant_observability_and_failure_taxonomy.md`, `docs/ADR.0035.typed_entity_memory_and_reference_resolution_v1.md`

## Context

The assistant currently uses a fixed turn pipeline:

1. prompt segmentation into prompt segments;
2. primary segment classification as `router_candidate`, `model_required`, or `clarification_or_policy_blocked`;
3. turn execution-plan construction;
4. deterministic execution and/or model continuation;
5. per-segment merge and finalization.

That pipeline is already integrated into the runtime, observability, and eval surfaces. The problem is that the current primary labels describe routing confidence rather than user intent.

This creates several shortcomings:

- the labels do not directly encode whether a segment should execute an app command, create a new product, or just be answered by the language model;
- product creation requests are mixed into the broad `model_required` bucket;
- the system has no first-class contract for extracting structured product requests from English;
- and prior discussion about "constraints" as a segment class mixes prompt-intent classification with prompt-context management.

`ADR.0042` narrowed the problem to canonical file-product generation inside `model_required` segments. That was a useful intermediate step, but the framing is no longer sufficient because the primary segment ontology itself is changing.

## Problem

The assistant needs segment classes that map directly to execution semantics:

- deterministic command execution for built-in app commands;
- structured extraction for product-creation requests;
- and generic language-model handling for ordinary questions or prose instructions.

In addition:

- the assistant must preserve deterministic control over known command execution;
- product creation requests need a typed extraction contract, not a free-form recipe prompt;
- canonical products need a closed-world dictionary that defines how they are recognized, named, estimated, and generated;
- and persistent user constraints should be handled explicitly in the UI rather than inferred unreliably from arbitrary segments.

## Decision

Adopt a new primary segment classification contract with three labels:

- `command`
- `create_product`
- `other`

Represent each segment as a structured object with:

- `text`
- `offsets`
- `class`

Where:

- `offsets` uses half-open character offsets: `[start, stop)`
- field names use Python/JSON-friendly snake_case
- all model-produced JSON is validated locally after receipt before use

Conditional fields:

- if `class == "command"`:
  - `command`
  - `args`
- if `class == "create_product"`:
  - `pixel_type`
  - `semantics`
  - `sources`
  - `product_type`

Persistent user constraints are not a segment class. They are moved to a dedicated constraints UI field whose contents are included in every assistant prompt as explicit persistent context.

Classification and extraction use a hybrid method:

1. deterministic first-pass matching for `command`;
2. one Ollama model call for unmatched segments, producing either `create_product` or `other`;
3. deterministic local validation and downgrade rules on the returned JSON;
4. dictionary-governed downstream product lookup, generation-function selection, prerequisite expansion, and execution.

Segment execution preserves prompt order. Classification determines how each segment is handled, but it does not reorder execution:

1. segments are classified independently;
2. segments are dispatched in the order they appear in the prompt;
3. `command` segments execute deterministically when reached;
4. `create_product` segments enter product planning when reached;
5. `other` segments are handled by the primary language model when reached;
6. later segments observe the updated state produced by earlier segments in the same turn.

This ADR retains the useful part of `ADR.0042`, namely model-assisted structured extraction for product requests plus deterministic recipe selection, but replaces the old `model_required`-centric framing with a new intent-centered segment ontology.

## Non-Goals

This ADR does not:

- replace the existing segmentation stage;
- replace the existing per-segment execution and merge pipeline in one step;
- make command execution model-driven;
- introduce arbitrary workflow graph planning for all actions outside the canonical product dictionary;
- infer persistent constraints from arbitrary user prose;
- or define the full implementation of every generation function.

## New Segment Contract

### Base Segment Object

Every segment has this base shape:

```json
{
  "text": "show slope",
  "offsets": {
    "start": 0,
    "stop": 10
  },
  "class": "command"
}
```

`offsets.start` is inclusive and `offsets.stop` is exclusive.

### `command`

Illustrative shape:

```json
{
  "text": "show slope",
  "offsets": {
    "start": 0,
    "stop": 10
  },
  "class": "command",
  "command": "layer_show",
  "args": [
    {
      "name": "layer",
      "value": "slope"
    }
  ]
}
```

Semantics:

- the segment intends to execute one built-in app command;
- `command` is one label from the app's command dictionary;
- `args` is a list of normalized argument/value pairs.

### `create_product`

Illustrative shape:

```json
{
  "text": "create a slope raster from the DEM",
  "offsets": {
    "start": 0,
    "stop": 34
  },
  "class": "create_product",
  "pixel_type": "float",
  "semantics": "Slope in degrees at each pixel.",
  "sources": [
    "primary_dem"
  ],
  "product_type": "slope_raster"
}
```

Semantics:

- the segment intends to create a new product;
- `pixel_type` is one of `boolean`, `byte`, `integer`, `float`;
- `semantics` is a short English description of what the product means;
- `sources` lists product references named or implied by the segment and may be empty;
- `product_type` is one label from the canonical product-type dictionary defined below.

### `other`

Illustrative shape:

```json
{
  "text": "What does slope tell me about landing safety here?",
  "offsets": {
    "start": 0,
    "stop": 48
  },
  "class": "other"
}
```

Semantics:

- the segment is intended for ordinary language-model response generation;
- it carries no deterministic execution or product-generation contract.

## Constraints Move To Explicit UI Context

Persistent user constraints are not represented as a segment class.

Instead, the assistant UI should expose a dedicated constraints text field. Its contents are included with every turn as explicit context. Examples:

- "Prefer byte rasters unless precision is necessary."
- "Assume south-pole stereographic display outputs."
- "Focus on areas near Shackleton crater."

Reasons:

- command intent and product-creation intent are execution semantics;
- persistent constraints are prompt-context management, not an execution route;
- a dedicated UI field is more explicit, controllable, inspectable, and observable than inferred memory extraction;
- and this avoids a weak classifier boundary between "constraint-like prose" and ordinary `other` segments.

## Classification Method

### 1. Segmentation

The existing segmentation stage remains in place. Segments continue to be produced before classification, with text and offsets anchored to the original prompt.

This ADR does not require replacing the segmenter. If the segmenter evolves later, the output still must provide stable segment text and half-open offsets.

### 2. Deterministic Command Classification

Each segment is first tested against the closed-world app command dictionary.

If a segment:

- matches exactly one supported command pattern;
- has normalizable arguments;
- satisfies required-slot validation;
- and passes any existing safety/policy checks for deterministic execution;

then it is classified as `command`.

Deterministic command matching is authoritative. The model must not override a successful deterministic command match.

### 3. One Ollama Call For Unmatched Segments

If a segment does not classify as `command`, the backend sends one model request to Ollama for classification/extraction. The model chooses between:

- `create_product`
- `other`

The purpose of this call is not recipe planning. Its purpose is to convert English into a normalized typed intent object when the segment is a product request.

### 4. Deterministic Validation

The backend validates the returned JSON against a local schema and local enum checks.

Validation includes:

- required fields present for the chosen class;
- allowed enum values only;
- valid offset bounds;
- `offsets.start <= offsets.stop`;
- `offsets` must lie within the original prompt;
- `sources` must be a string list;
- and any additional product-type-specific checks the implementation defines.

If validation fails:

- the result must not drive deterministic execution;
- the segment is downgraded safely, usually to `other`;
- and observability fields record the downgrade reason.

## Ollama Classification and Extraction Contract

### Model Use

The backend uses a locally hosted Ollama model for non-command segment classification/extraction.

Initial target model:

- `gemma4:e4b-it-q8_0`

The model identifier must remain configurable. The ADR accepts this model as the initial baseline, not as a permanent requirement.

In addition to selecting a base model, the implementation should create and version a task-specific Ollama model configuration for this classification/extraction task. In practice this means creating a repo-managed Ollama model definition or equivalent build artifact that wraps the chosen Gemma 4 base model with the intended system prompt, template behavior, and task defaults. The runtime should reference that task-specific model identity rather than relying only on an ad hoc raw base-model name in code.

#### Concrete Artifact Layout

The initial concrete artifact layout should be:

- Ollama model definition:
  - `models/segment-intent-classifier-gemma4-v1.mf`
- System prompt source:
  - `backend/services/assistant/prompts/segment_intent_classifier_system.txt`
- Few-shot examples:
  - `backend/services/assistant/prompts/segment_intent_classifier_fewshot.json`
- Local JSON schema for non-command output:
  - `backend/services/assistant/schemas/segment_intent_classifier.schema.json`
- Product-type dictionary implementation:
  - `backend/services/assistant/product_type_dictionary.py`
- Product-type dictionary validation tests:
  - `backend/tests/worker/test_product_type_dictionary.py`
- Segment-classification eval fixtures:
  - `backend/tests/fixtures/assistant_segmentation_classification/golden_cases_v2.jsonl`
- Ollama extraction eval fixtures:
  - `backend/tests/fixtures/assistant_segmentation_classification/non_command_extraction_cases.jsonl`
- Assistant eval output or comparison reports for this model:
  - `backend/evals/assistant/segment_intent_classifier_eval.jsonl`

The initial task-specific Ollama model identity should be:

- `segment-intent-classifier:gemma4-v1`

That identity should be created from the checked-in Modelfile rather than being hand-created interactively on a developer machine without a tracked source artifact.

### Ollama Backend Responsibilities

The backend Ollama adapter is responsible for:

- sending one non-command classification/extraction request per unmatched segment;
- providing the model with the segment text, original offsets, active scenario context, and persistent constraints context when relevant;
- requiring JSON output;
- attaching the local JSON schema to the request when supported by the adapter;
- parsing the response body;
- performing local schema and enum validation after receipt;
- and downgrading invalid outputs safely.

### Prompting Strategy

Use one system prompt for non-command segments. The system prompt should:

- explain that the task is to classify one already-segmented prompt segment;
- restrict output to `create_product` or `other`;
- define the exact JSON schema shape;
- define the allowed `pixel_type` values;
- define the allowed `product_type` values;
- instruct the model not to invent unsupported product types;
- instruct the model to prefer `other` if uncertain;
- and instruct the model to return JSON only.

Use a small fixed few-shot set covering:

- direct product requests;
- implied-source product requests;
- threshold or boolean-mask requests;
- and ordinary analytical questions that should be `other`.

This ADR chooses one prompt/schema family for non-command segments rather than separate model calls for different non-command subclasses.

The few-shot file should be machine-readable rather than embedded ad hoc in Python source. The initial expected format is a JSON array of objects with fields such as:

- `id`
- `segment_text`
- `constraints`
- `scenario_context`
- `expected_output`

This allows the same artifact to be used for:

- prompt assembly;
- local regression tests;
- and eval replay across task-model revisions.

### Model Build And Versioning Workflow

The initial workflow should be:

1. edit the checked-in prompt, few-shot, or Modelfile artifacts;
2. build the Ollama task model from `models/segment-intent-classifier-gemma4-v1.mf`;
3. register it locally as `segment-intent-classifier:gemma4-v1`;
4. run extraction and eval fixtures against that exact model id;
5. if prompt/template behavior changes materially, create a new versioned artifact and model id such as `segment-intent-classifier:gemma4-v2`.

Version changes should be driven by prompt/template/schema behavior, not by ephemeral local state. Eval records should capture:

- task-model id;
- base-model id;
- prompt artifact revision;
- schema revision;
- and few-shot artifact revision.

### Request Shape

Illustrative backend request payload to the Ollama adapter:

```json
{
  "model": "gemma4:e4b-it-q8_0",
  "format": "json",
  "system": "You classify one already-segmented prompt segment as create_product or other and return JSON matching the provided schema.",
  "prompt": {
    "segment_text": "create a hillshade from the primary DEM",
    "segment_offsets": {
      "start": 0,
      "stop": 37
    },
    "persistent_constraints": "Prefer byte rasters unless precision is required.",
    "scenario_context": {
      "active_scenario_id": "sc_123",
      "known_products": [
        "primary_dem"
      ]
    }
  },
  "json_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "oneOf": [
      {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "text": {
            "type": "string"
          },
          "offsets": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "start": {
                "type": "integer",
                "minimum": 0
              },
              "stop": {
                "type": "integer",
                "minimum": 0
              }
            },
            "required": [
              "start",
              "stop"
            ]
          },
          "class": {
            "const": "create_product"
          },
          "pixel_type": {
            "enum": [
              "boolean",
              "byte",
              "integer",
              "float"
            ]
          },
          "semantics": {
            "type": "string",
            "minLength": 1
          },
          "sources": {
            "type": "array",
            "items": {
              "type": "string",
              "minLength": 1
            }
          },
          "product_type": {
            "enum": [
              "dem",
              "hillshade_raster",
              "slope_raster",
              "aspect_raster",
              "ruggedness_raster",
              "tpi_raster",
              "roughness_raster",
              "threshold_mask",
              "combined_mask",
              "selection_mask",
              "boolean_raster",
              "region_labels",
              "region_sizes",
              "region_borders",
              "illumination_raster",
              "earth_visibility_raster",
              "duration_raster",
              "psr_raster",
              "sun_center_above_horizon",
              "sun_bottom_above_horizon",
              "earth_center_above_horizon",
              "station_over_horizon_deg"
            ]
          }
        },
        "required": [
          "text",
          "offsets",
          "class",
          "pixel_type",
          "semantics",
          "sources",
          "product_type"
        ]
      },
      {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "text": {
            "type": "string"
          },
          "offsets": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "start": {
                "type": "integer",
                "minimum": 0
              },
              "stop": {
                "type": "integer",
                "minimum": 0
              }
            },
            "required": [
              "start",
              "stop"
            ]
          },
          "class": {
            "const": "other"
          }
        },
        "required": [
          "text",
          "offsets",
          "class"
        ]
      }
    ]
  }
}
```

### Returned Object Shapes

Valid `create_product` response:

```json
{
  "text": "create a hillshade from the primary DEM",
  "offsets": {
    "start": 0,
    "stop": 37
  },
  "class": "create_product",
  "pixel_type": "byte",
  "semantics": "Shaded relief image derived from terrain elevation to visualize topographic variation.",
  "sources": [
    "primary_dem"
  ],
  "product_type": "hillshade_raster"
}
```

Valid `other` response:

```json
{
  "text": "Which terrain measure is more relevant for landing safety here?",
  "offsets": {
    "start": 0,
    "stop": 60
  },
  "class": "other"
}
```

### Post-Receipt Validation Rule

Even if Ollama is run in JSON mode with a supplied schema, the backend must still validate locally after receipt. Model output is never trusted solely because the transport requested JSON.

## Canonical Product-Type Dictionary

The `product_type` dictionary is a first-class contract.

Each dictionary entry must define:

- `product_type`: canonical label;
- `description`: English description of what the product means;
- `generation_functions`: one or more supported functions that can generate it from precursor products;
- `generation_time_estimator`: a function that estimates cost or duration as a function of the chosen generation-function arguments;
- `name_strategy`: either a fixed string or a function that generates a canonical product name from arguments, and that same strategy can be used for scenario lookup and reuse checks;
- `precursor_requirements`: the required precursor product types or explicit source categories;
- `default_pixel_type` or an equivalent rule for deriving pixel type when not explicitly specified;
- and any product-type-specific parameter constraints.

This dictionary is closed-world for dictionary-governed product planning:

- if a `product_type` is present in the dictionary, deterministic lookup and recipe planning are allowed;
- if it is absent, the runtime must not pretend a canonical deterministic recipe exists.

### Product-Type Labels

The canonical product-type labels are:

- `dem`
- `hillshade_raster`
- `slope_raster`
- `aspect_raster`
- `ruggedness_raster`
- `tpi_raster`
- `roughness_raster`
- `threshold_mask`
- `combined_mask`
- `selection_mask`
- `boolean_raster`
- `region_labels`
- `region_sizes`
- `region_borders`
- `illumination_raster`
- `earth_visibility_raster`
- `duration_raster`
- `psr_raster`
- `sun_center_above_horizon`
- `sun_bottom_above_horizon`
- `earth_center_above_horizon`
- `station_over_horizon_deg`

### Product-Type Semantics

- `dem`: raster of surface elevation in meters from the lunar reference sphere (radius = 1737.4 km)
- `hillshade_raster`: shaded relief image to visualize elevation changes
- `slope_raster`: slope raster expressed in degrees
- `aspect_raster`: downhill direction in degrees (0 = north, 90 = east, ...)
- `ruggedness_raster`: elevation difference between central and surrounding pixels
- `tpi_raster`: elevation difference between central and the mean of surrounding pixels
- `roughness_raster`: max elevation difference between central pixel and any surrounding pixel
- `threshold_mask`: boolean raster generated by comparing another raster with a fixed threshold; non-zero indicates true
- `combined_mask`: boolean raster generated by logical combination of other rasters
- `selection_mask`: boolean raster intended to indicate pixels with a particular property
- `boolean_raster`: generic boolean raster, zero vs non-zero
- `region_labels`: integer raster where equal values indicate membership in the same region
- `region_sizes`: integer raster where each pixel value is the size of the region containing that pixel
- `region_borders`: boolean raster where non-zero values represent region-edge pixels
- `illumination_raster`: pixel values indicate fraction of full sun at a specific time
- `earth_visibility_raster`: boolean raster indicating whether Earth is visible at a specific time
- `duration_raster`: float raster indicating duration of some property at each pixel
- `psr_raster`: boolean raster where non-zero indicates permanent shadow
- `sun_center_above_horizon`: angle in degrees of the sun's center above the horizon
- `sun_bottom_above_horizon`: angle in degrees of the sun's bottom limb above the horizon
- `earth_center_above_horizon`: angle in degrees of the Earth's center above the horizon
- `station_over_horizon_deg`: angle in degrees of a specific ground station above the horizon

### Required Dictionary Behavior

For each label above, the implementation must provide a dictionary entry with:

- an English description;
- one or more generation functions;
- a time-estimation function;
- and a name strategy.

Illustrative conceptual entry:

```yaml
product_type: slope_raster
description: Slope in degrees at each pixel derived from a DEM.
precursor_requirements:
  - dem
default_pixel_type: float
generation_functions:
  - function_name: raster.calculate.slope
    argument_contract:
      source_dem: product_ref
      units: degrees
generation_time_estimator:
  function_name: estimate_raster_runtime
  arguments:
    algorithm: slope
name_strategy:
  function_name: canonical_name_from_inputs
  arguments:
    pattern: "{source_name}_slope_deg"
```

The exact implementation data structure is not fixed by this ADR. "Dictionary" here means the authoritative catalog contract, not a mandated programming-language container.

## Dictionary-Governed Product Planning After Extraction

If a segment validates as `create_product`, the runtime performs dictionary-governed product planning:

1. resolve the `product_type` in the canonical dictionary;
2. use the `sources` list plus scenario context to resolve precursor products when possible;
3. select one supported generation function;
4. estimate cost/runtime using the dictionary's estimator;
5. generate the canonical target name using the name strategy;
6. check whether the product already exists or can be reused;
7. expand prerequisites according to planner policy when needed;
8. execute through governed tools/jobs only.

The model does not invent arbitrary execution recipes for known canonical products. It only extracts the normalized request object. Planning may start with direct single-step generation and later expand to bounded backward-chaining over the canonical dictionary when prerequisite products are missing.

## Validation and Downgrade Rules

### Deterministic Precedence

If deterministic command matching succeeds, the segment is `command` even if a later model call would have produced another class.

### Invalid Model Output

If the Ollama response:

- is not valid JSON;
- fails schema validation;
- includes unsupported enum values;
- produces out-of-bounds offsets;
- or otherwise fails deterministic validation;

then the result is downgraded to `other` unless an implementation-specific repair path is explicitly defined.

### Unsupported Product Types

If the model returns a `product_type` label that is not in the canonical dictionary, the segment must not enter dictionary-governed product planning. It is downgraded to `other` or handled by a separate explicitly non-canonical path if such a path exists.

### Source Ambiguity

If the extracted `sources` are ambiguous or unresolved, the planner may:

- ask for clarification;
- use deterministic reuse/discovery rules when there is exactly one safe interpretation;
- or treat the segment as not ready for deterministic execution.

This ADR does not require silent guessing.

## Execution-Planning Impact

The existing execution-plan pipeline remains, but the primary intent classes change.

- `command` segments enter deterministic command execution planning.
- `create_product` segments enter dictionary-governed product planning using the canonical dictionary.
- `other` segments are forwarded to ordinary language-model response generation.
- execution order follows the original prompt segment order, independent of class.

Per-segment execution state, merge policy, observability, and turn finalization remain governed by the existing assistant pipeline and related ADRs.

## API and Event Contract Changes

Implementations that expose classification payloads, execution plans, or observability events should migrate from the legacy labels:

- `router_candidate`
- `model_required`
- `clarification_or_policy_blocked`

to the new intent classes:

- `command`
- `create_product`
- `other`

Affected surfaces may include:

- planner/execution-plan payloads;
- assistant trace payloads;
- WebSocket stage metadata;
- generated JSON schemas;
- and eval fixture formats.

Compatibility strategy may be phased, but the new intent classes are the target canonical contract.

## Observability

The assistant should preserve existing stage events where practical:

- `prompt_segmentation_completed`
- `prompt_classification_completed`

Per-segment observability should additionally capture:

- `classification_method`: `deterministic_command` or `ollama_non_command`
- `validation_status`
- `downgrade_reason` when applicable
- `selected_product_type` for validated `create_product` segments
- `selected_generation_function` when deterministic planning proceeds
- `runtime_estimate` when available
- `canonical_name_candidate`

This supports replay, evals, regression triage, and user-facing debugging.

## Testing

Required test coverage includes:

- deterministic command-classification unit tests;
- non-command Ollama-adapter parsing and validation tests;
- downgrade tests for malformed model JSON;
- golden tests for segmentation plus new class outputs;
- product-request extraction tests across the full `product_type` label list;
- product dictionary validation tests ensuring each product type has required metadata;
- and contract tests for any updated planner/event schema surfaces.

Regression tests should include mixed prompts that contain:

- a command plus a product request;
- multiple product requests;
- ordinary analytical questions;
- and persistent constraints supplied via the UI constraints field.

## Alternatives Considered

### A. Keep `add_constraint` As A Segment Class

Rejected.

Reason:

- it mixes execution intent with persistent prompt-context management;
- the boundary between "constraint-like" text and ordinary prose is weak;
- and a dedicated persistent constraints field is more explicit and reliable.

### B. Fully Deterministic Classification For All Non-Command Segments

Rejected.

Reason:

- `create_product` requires semantic normalization into a typed object;
- lexical rules alone are too brittle for implied sources, product semantics, and product-type disambiguation.

### C. Fully Model-Based Classification Including Commands

Rejected.

Reason:

- built-in commands are a closed-world deterministic problem;
- deterministic command matching is safer, more replayable, and more observable.

### D. Keep `ADR.0042` Framing Without Changing Primary Labels

Rejected.

Reason:

- the old labels encode routing confidence rather than execution intent;
- and product extraction is now only one part of a broader classification-contract change.

## Consequences

### Benefits

- primary labels align with execution semantics;
- product creation becomes a first-class structured intent;
- command execution remains deterministic;
- persistent constraints become explicit UI-managed context;
- and canonical product planning stays dictionary-governed after extraction.

### Costs

- planner and event payloads need migration;
- eval fixtures need rework;
- the backend needs an Ollama adapter contract for non-command extraction;
- and the canonical product dictionary becomes a critical maintained artifact.

## Migration Plan

### Phase 1

- add new segment object types and validators behind a feature flag;
- add the persistent constraints UI field and include it in turn requests;
- define the canonical `product_type` dictionary contract.

### Phase 2

- implement deterministic command classification under the new labels;
- implement one Ollama non-command classification/extraction path;
- dual-write legacy and new classification metadata for comparison.

### Phase 3

- switch planner and execution logic to use `command`, `create_product`, and `other`;
- wire dictionary-governed product planning to the canonical dictionary;
- begin migrating the runtime from whole-turn dispatch to ordered per-segment dispatch.

### Phase 4

- remove legacy primary labels and compatibility shims once eval quality and telemetry confirm readiness.

## Detailed Implementation Plan

This section is the execution checklist for implementing this ADR. Items may be refined during implementation, but the phase structure and completion criteria should remain stable enough for tracking.

### Phase 0: ADR Alignment And Scope Lock

- [x] Confirm the canonical set of primary segment classes is exactly `command`, `create_product`, `other`.
- [x] Confirm persistent constraints are handled only through a dedicated UI field and are not emitted as a segment class.
- [x] Confirm the initial Ollama model baseline and configuration surface, including the default model id and where it is stored in config.
- [x] Confirm the initial command dictionary scope for deterministic matching.
- [x] Confirm the initial authoritative `product_type` label list and ownership for maintaining it.
- [ ] Confirm direct-cutover strategy and identify any code paths that still assume legacy labels or compatibility shims.

### Phase 1: Contract Definitions

- [x] Add backend types for the new base segment contract:
  - `text`
  - `offsets.start`
  - `offsets.stop`
  - `class`
- [x] Add backend types for `command` segments:
  - `command`
  - `args`
- [x] Add backend types for `create_product` segments:
  - `pixel_type`
  - `semantics`
  - `sources`
  - `product_type`
- [x] Define a discriminated-union JSON schema for non-command Ollama output.
- [x] Define a canonical local validation layer separate from model transport behavior.
- [x] Update any assistant contract modules that currently encode legacy labels as primary output.
- [x] Decide where the new types live so they are shared consistently by planner, observability, and tests.

### Phase 2: Product Dictionary Infrastructure

- [x] Create the authoritative `product_type` dictionary module.
- [x] For each `product_type`, add:
  - English description
  - precursor requirements
  - default pixel-type rule
  - one or more generation-function definitions
  - generation-time estimator hook
  - canonical name strategy
- [x] Implement dictionary validation on startup or test-time so missing metadata fails fast.
- [x] Add lookup helpers for:
  - label to dictionary entry
  - canonical name generation
  - scenario search/reuse lookup
  - generation-function enumeration
- [x] Add tests ensuring every declared `product_type` has a complete dictionary entry.

### Phase 3: Deterministic Command Classification

- [x] Refactor the existing classifier contract so deterministic command matching produces `class="command"` rather than legacy labels.
- [x] Preserve or improve required-slot validation for deterministic commands.
- [x] Preserve policy/safety checks that can block deterministic execution.
- [x] Ensure deterministic command matches are authoritative and bypass Ollama classification.
- [x] Define normalized command argument output shape.
- [x] Add unit tests for:
  - successful command matches
  - invalid/missing argument cases
  - ambiguous command cases
  - policy-blocked command cases if still represented in planner state

### Phase 4: Ollama Non-Command Classification/Extraction Path

- [x] Create a repo-managed Ollama model definition for this task using the chosen Gemma 4 base model.
- [x] Add `models/segment-intent-classifier-gemma4-v1.mf`.
- [x] Add `backend/services/assistant/prompts/segment_intent_classifier_system.txt`.
- [x] Add `backend/services/assistant/prompts/segment_intent_classifier_fewshot.json`.
- [x] Add `backend/services/assistant/schemas/segment_intent_classifier.schema.json`.
- [ ] Define a reproducible local build/update workflow for creating the task-specific Ollama model from the base model.
- [x] Default the runtime configuration to `segment-intent-classifier:gemma4-v1` for this task-specific path.
- [ ] Define the versioning strategy for the task-specific Ollama model so evals can be tied to a specific prompt/template revision.
- [ ] Document how developers rebuild or refresh the local task-specific Ollama model when prompts or schemas change.
- [x] Add or refactor a backend Ollama adapter dedicated to non-command classification/extraction.
- [x] Make model id configurable, with the agreed default baseline.
- [x] Implement one system prompt for unmatched segments.
- [x] Add a small fixed few-shot set for:
  - direct product requests
  - implied-source product requests
  - boolean/threshold product requests
  - non-product analytical questions
- [x] Implement JSON-mode request handling.
- [ ] Attach the discriminated-union JSON schema when supported by the Ollama adapter.
- [x] Parse responses into local typed objects.
- [x] Add local schema validation after receipt regardless of model JSON mode.
- [x] Add downgrade behavior to `other` when parsing or validation fails.
- [ ] Add timeout, retry, and failure logging behavior appropriate for local-model calls.

### Phase 5: Prompt Segmentation And Offset Normalization

- [x] Update segment contract code to expose half-open offsets consistently.
- [x] Verify segmentation output uses stable `[start, stop)` semantics end-to-end.
- [x] Ensure model-returned offsets are validated against original segment boundaries.
- [x] Decide whether model-returned offsets must exactly match the segment offsets or may be normalized by the backend.
- [x] Add regression tests for offset behavior, especially for multi-segment prompts.

### Phase 6: Constraints UI And Prompt Assembly

- [ ] Add a dedicated constraints text field to the assistant UI.
- [ ] Persist constraints in the appropriate frontend/session state.
- [x] Include constraints in every turn request payload.
- [ ] Define how constraints are rendered or summarized in the UI so users can see what persistent context is active.
- [x] Thread constraints through the backend turn-construction path.
- [x] Ensure constraints are available to Ollama non-command extraction as context.
- [x] Ensure constraints are available to the generic language-model path as context.
- [x] Add tests covering empty, present, and edited constraint states.

### Phase 7: Dictionary-Governed Product Planning

- [x] Replace the `ADR.0042`-style product-request attachment path with the new `create_product` segment contract.
- [x] Implement planner logic that takes validated `create_product` segments and resolves them via the product dictionary.
- [x] Resolve explicit and implicit `sources` against scenario context where safe.
- [x] Select one supported generation function per product request.
- [ ] Invoke the generation-time estimator and include its result in planning metadata.
- [x] Generate canonical product names using the dictionary's name strategy.
- [x] Check for existing reusable products before scheduling generation.
- [ ] Expand prerequisites according to planner policy when needed.
- [x] Route actual execution only through governed tools/jobs.
- [x] Add planner tests for:
  - direct source resolution
  - missing-source clarification cases
  - reusable existing product detection
  - prerequisite expansion

### Phase 7: Ordered Per-Segment Dispatch

- [x] Make the turn dispatcher execute segments in prompt order rather than choosing one whole-turn execution path.
- [x] Dispatch `command`, `create_product`, and `other` according to class without reordering.
- [x] Ensure later segments observe files, products, and runtime state produced by earlier segments in the same turn.
- [ ] Define stop/continue policy for blocked or failed required segments.
- [x] Add mixed-turn tests covering:
  - `other` followed by `create_product`
  - `create_product` followed by `other`
  - multiple interleaved `command` and `create_product` segments
  - multi-segment prompts where later segments depend on artifacts produced earlier in the same turn

### Phase 10: Bounded Backward-Chaining Product Planning

- [ ] Add a bounded backward-chaining planner for `create_product` requests within the canonical product dictionary.
- [ ] Allow the planner to search prerequisite expansions when direct sources are missing.
- [ ] Keep search bounded by dictionary edges, planner limits, and governed tool availability.
- [ ] Record the selected derivation path in planning metadata and observability.
- [ ] Add tests for multi-step canonical derivations where prerequisite products do not yet exist.
- [ ] Ensure non-canonical arbitrary workflow synthesis remains out of scope.

### Phase 8: Planner, Event, And Schema Migration

- [x] Update planner/execution-plan payloads to emit the new classes.
- [x] Update assistant event payloads and any stage metadata that expose classification results.
- [ ] Update generated JSON schemas and OpenAPI surfaces if they include classification objects.
- [ ] Decide whether to dual-write legacy fields temporarily for compatibility.
- [ ] If dual-write is used, document the compatibility mapping explicitly.
- [ ] Update any docs that still describe the legacy primary labels as canonical.

### Phase 9: Observability And Failure Taxonomy

- [x] Add per-segment observability fields for:
  - classification method
  - validation status
  - downgrade reason
  - selected product type
  - selected generation function
  - runtime estimate
  - canonical name candidate
- [ ] Add clear error codes for:
  - malformed model JSON
  - schema validation failure
  - unsupported product type
  - unresolved sources
  - missing product dictionary metadata
- [x] Ensure traces distinguish deterministic-classification failures from Ollama-call failures.
- [ ] Ensure observability remains usable for eval triage and replay.

### Phase 11: Tests And Evals

- [x] Update or replace golden segmentation/classification fixtures to the new labels.
- [x] Create `backend/tests/fixtures/assistant_segmentation_classification/golden_cases_v2.jsonl`.
- [x] Create `backend/tests/fixtures/assistant_segmentation_classification/non_command_extraction_cases.jsonl`.
- [ ] Add eval cases that compare the task-specific Ollama model behavior across prompt/template revisions.
- [ ] Record task-model eval results in `backend/evals/assistant/segment_intent_classifier_eval.jsonl`.
- [x] Add unit tests for discriminated-union JSON validation.
- [x] Add tests for malformed Ollama responses and downgrade behavior.
- [x] Add extraction tests spanning the full current `product_type` list.
- [x] Add mixed-turn tests containing:
  - commands
  - product requests
  - ordinary questions
  - persistent constraints
- [ ] Update eval scoring or fixtures that assume legacy labels.
- [ ] Add before/after regression cases for prompts that motivated this ADR.

### Suggested Definition Of Done For This ADR Implementation

- [x] The assistant classifies deterministic commands as `command` without model involvement.
- [x] Unmatched segments use one Ollama call that returns `create_product` or `other`.
- [x] Returned JSON is validated locally and malformed outputs are downgraded safely.
- [ ] The UI exposes explicit persistent constraints and includes them in every turn.
- [x] Every canonical `product_type` has a complete dictionary entry with required metadata.
- [x] Valid `create_product` segments enter dictionary-governed product planning and governed execution.
- [x] Segments execute in prompt order, independent of classification.
- [ ] Planner, schemas, tests, and observability surfaces are migrated to the new contract.

## Acceptance Criteria

This ADR is considered implemented when:

- known app commands classify deterministically as `command` with validated args;
- unmatched segments are classified by one Ollama call as `create_product` or `other`;
- returned JSON is always validated locally after receipt;
- invalid model JSON never directly drives deterministic execution;
- every canonical `product_type` has a complete dictionary entry with description, generation functions, runtime estimator, and name strategy;
- validated `create_product` segments enter dictionary-governed product planning;
- segment execution preserves prompt order;
- persistent user constraints are supplied through an explicit UI field rather than a segment class;
- and schemas, tests, and observability surfaces are updated to the new canonical contract.

## Deferred For Initial Testing

The following work is intentionally deferred and does not block initial testing of the new segment contract and prompt-order execution model:

- generation-time estimator integration and planner metadata population;
- bounded backward-chaining prerequisite search for `create_product`;
- dedicated frontend constraints UI;
- broader classification quality improvements beyond correctness of the new contract;
- full observability taxonomy expansion beyond the currently required fields;
- and broader eval-program expansion beyond the focused regression and worker coverage already added.

Before broader user-facing adoption, the implementation should still complete:

- assistant-facing OpenAPI and generated schema migration;
- basic correctness of blocked/failed error behavior on the new ordered segment paths;
- and removal of any remaining legacy classification/shim dependencies in runtime code.
