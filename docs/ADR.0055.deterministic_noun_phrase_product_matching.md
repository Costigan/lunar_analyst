# ADR.0055: Deterministic Noun-Phrase Product-Type Matching for Recipe-Backed Create-Product Segments

- Status: Accepted
- Date: 2026-04-19
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0043.segment_intent_classification_and_product_request_extraction.md`, `docs/ADR.0054.deterministic_segment_routing_and_flag_reduction.md`, `backend/services/assistant/prompt_classifier.py`, `backend/services/assistant/create_product_planner.py`, `backend/services/assistant/product_type_dictionary.py`, `backend/services/assistant/canonical_recipe_catalog.py`

## Context

The deterministic ordered-segment path can already execute create-product segments without a language-model call when classification produces:

- `segment_class = "create_product"`
- `product_type = <canonical type>`

However, current create-product classification is a narrow heuristic in `PromptClassifier._heuristic_create_product`:

- requires a leading create verb (`create/generate/make/build`), and
- relies on a short hardcoded phrase list.

This causes many prompts that clearly refer to known products (for example noun-phrase requests such as "need a hillshade" or "use a slope map") to fall through to `other`, which then requires LLM fallback.

## Problem

1. Deterministic create-product routing depends on brittle imperative-prefix checks.
2. Noun-phrase mentions of known product types are not first-class deterministic signals.
3. Segments for product types with deterministic recipes should not require LLM classification.
4. We need a closed-world, auditable matching rule that avoids over-matching and preserves safety.

## Decision

Add a deterministic noun-phrase product matcher in `PromptClassifier` for recipe-backed product types.

The matcher runs before `fallback_other` and produces `create_product` classifications when:

1. a segment contains a noun-phrase candidate that matches a canonical product type alias, and
2. that product type has at least one deterministic recipe in the recipe catalog.

When these conditions hold, the segment is classified as `create_product` and executed through `CreateProductPlanner` in deterministic mode, with no LLM classification call.

## Scope

In scope:

- deterministic matching in classifier for known recipe-backed product types,
- explicit alias dictionary and normalization rules,
- ambiguity handling and downgrade behavior,
- tests for noun-phrase-driven deterministic classification.

Out of scope:

- adding new product recipes,
- replacing downstream `CreateProductPlanner` prerequisite/parameter checks,
- changing non-create-product routing,
- model-side semantic extraction redesign.

## Normative Design

### 1. Recipe-Backed Type Gate (Hard Requirement)

Only product types with configured recipes are eligible for deterministic noun-phrase matching.

Normative check:

- `recipe_ids_for_product_type(product_type)` must be non-empty.

If a product type exists in `PRODUCT_TYPE_DICT` but has no recipe, noun-phrase matching must not emit `create_product` for that type.

### 2. Alias Index Construction

At module load (or cached lazy init), build `RECIPE_BACKED_PRODUCT_ALIAS_INDEX`:

- key: normalized alias phrase
- value: canonical `product_type`

Alias sources per product type:

1. explicit alias phrases declared on the product type spec (source of truth),
2. canonical product type tokens (`slope_raster` -> `slope raster`),
3. default output filename stem aliases (`hillshade.tif` -> `hillshade`) where meaningful.

Alias ownership is product-type-level, not recipe-level. The canonical declaration location is `product_type_dictionary.py` alongside `ProductTypeSpec` definitions. `PromptClassifier` consumes these aliases to build the deterministic lookup index.

The alias map remains deterministic, explicit, and version-controlled in code.

### 3. Segment Normalization

Normalize segment text before matching:

- lowercase,
- replace `_` and `-` with space,
- strip punctuation to spaces,
- collapse repeated whitespace.

No fuzzy embeddings, no edit-distance matching, no probabilistic scoring.

### 4. Noun-Phrase Candidate Extraction

Extract noun-phrase candidates using deterministic span rules over normalized text:

1. generate contiguous n-grams (1..5 tokens),
2. retain spans whose head token is in a controlled product-head vocabulary (`raster`, `map`, `mask`, `hillshade`, `slope`, `aspect`, `tpi`, `roughness`, `ruggedness`, `illumination`, `visibility`, `shadow`, `psr`),
3. include direct token-head matches (for single-word product nouns like `hillshade`, `aspect`).

This is intentionally deterministic and lightweight; it does not require model inference.

### 5. Match Resolution

For each candidate phrase:

1. normalize candidate phrase with the same normalization pipeline,
2. exact-lookup in alias index,
3. accumulate matched product types.

Resolution rules:

- 0 matches: no create-product classification from noun-phrase matcher.
- 1 matched product type: emit `create_product` for that type.
- >1 distinct matched product types: do not auto-classify; return `other` (LLM fallback) to avoid wrong deterministic execution.

### 6. Create-Verb Compatibility

The existing create-verb-prefix heuristic remains valid but is no longer required.

Classification order for non-command segments becomes:

1. deterministic noun-phrase product matching (recipe-backed gate),
2. legacy create-verb heuristic (kept for compatibility),
3. `fallback_other`.

### 7. Deterministic Metadata Contract

When noun-phrase matching emits `create_product`, set:

- `classification_origin = "deterministic_noun_phrase_product_match"`
- `intent_properties.operation = "create"`
- `intent_properties.product_type = <canonical type>`

`pixel_type` and `semantics` come from `PRODUCT_TYPE_DICT[product_type]`, same as existing heuristic behavior.

`sources` extraction keeps existing deterministic behavior (for example `primary_dem`, `dem`, slope-threshold hints).

## Examples (Normative)

These segments must classify as `create_product` without LLM classification:

- "Need a hillshade for this scenario" -> `hillshade_raster`
- "Use a slope map from the primary dem" -> `slope_raster`
- "Make mask where slope <= 5" -> `threshold_mask`
- "Generate permanent shadow mask" -> `psr_raster`

These segments must not deterministically force a create-product type:

- "Show slope layer" (command route should win)
- "Compare hillshade and slope" (multi-product ambiguity)
- "Need roughness" when `roughness_raster` has no recipe (until a recipe exists)

## Implementation Plan

1. Extend product-type definitions in `product_type_dictionary.py` so each product type can declare optional noun-phrase aliases (for example via `ProductTypeSpec`).
2. Add alias-index and normalization helpers to `prompt_classifier.py` (or a tightly scoped helper module under `backend/services/assistant/`) that load aliases from product types, then apply recipe-backed gating.
3. Add deterministic noun-phrase matcher function returning optional structured create-product payload.
4. Integrate matcher into `_extract_non_command` before `fallback_other`.
5. Keep existing heuristic path temporarily for backward compatibility.
6. Add/adjust unit tests in `backend/tests/worker/test_prompt_classifier.py`:
   - noun-phrase positive matches,
   - recipe-backed gate behavior,
   - alias source-of-truth coverage from product-type definitions,
   - ambiguity downgrade to `other`,
   - command precedence over create-product inference.
7. Add planner integration test(s) confirming noun-phrase-triggered `create_product` can produce deterministic recipe plans for recipe-backed types.

## Acceptance Criteria

- Segments containing known product noun phrases for recipe-backed types are classified as `create_product` deterministically.
- Those segments execute through deterministic create-product planning without an LLM classification call.
- Product types without recipes are not auto-routed to deterministic create-product execution.
- Ambiguous multi-type noun-phrase segments are not forced deterministically and remain eligible for LLM fallback.
- Existing deterministic command routing precedence is unchanged.

## Risks and Mitigations

Risk: over-matching ordinary prose as create-product intent.

Mitigations:

- recipe-backed gating,
- exact alias matching only,
- ambiguity downgrade to `other`,
- command-first routing retained.

Risk: synonym drift and maintenance burden.

Mitigation:

- explicit alias map co-located with classifier tests; additions require test updates.

## Rollback

Rollback is a code revert of the noun-phrase matcher integration:

1. remove matcher call in `_extract_non_command`,
2. keep legacy create-verb heuristic path,
3. revert associated tests.

No data migration or API contract migration is required.
