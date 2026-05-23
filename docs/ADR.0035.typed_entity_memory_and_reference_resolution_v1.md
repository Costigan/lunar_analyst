# ADR 0035: Typed Entity Memory and Reference Resolution (V1)

- Status: Proposed (partially realized by ADR.0051 and ADR.0053)
- Date: 2026-03-24
- Owners: Assistant Architecture
- Related:
  - `docs/ADR.0031.assistant_performance_improvement_program.md`
  - `docs/ADR.0028.turn_planner_json_contract.md`
  - `docs/ADR.0029.per_segment_execution_state_and_merge_policy.md`
  - `docs/ADR.0030.tool_argument_repair_policy.md`
  - `docs/ADR.0032.read_only_vs_mutating_completion_success_policy.md`
  - `docs/ADR.0033.assistant_observability_and_failure_taxonomy.md`

## Context

The assistant currently handles many user intents through deterministic routing plus model-tool loop, but referential language such as `this`, `that`, `it`, `this layer`, `this file`, `this raster`, and `this colormap` is not handled by a shared deterministic resolver.

This creates failure modes such as:

1. choosing `layer.update_state` for non-existent layers when the user intended "create layer from the file just produced",
2. ambiguous referent binding across turns,
3. inconsistent behavior across tools and prompt classes.

We need a practical, deterministic reference-resolution layer that is broad enough to cover common typed entities, but we do not want broad open-ended coreference reasoning in deterministic path logic.

## Decision

Adopt **Option #2**: implement a **typed entity memory + deterministic reference resolver (V1)** and use it as a pre-tool binding stage for deterministic/mutating actions.

Explicitly out of scope for deterministic path:

- Option #3 style broad linguistic/coreference reasoning.
- Attempting to fully solve arbitrary English reference resolution.

## Scope (V1)

In scope:

1. typed working-set memory for high-value entities:
   - `scenario`, `layer`, `file`, `raster`, `product`, `colormap`, `artifact` (table/plot/image), `job`.
2. deterministic binding for demonstratives/pronouns:
   - `this`, `that`, `it`, `these`, plus typed forms (`this layer`, `this file`, etc.).
3. schema-aware binding:
   - resolver only binds entities compatible with target tool argument requirements.
4. explicit blocked/clarification outcomes when binding is not unique.
5. traceability metadata for each binding decision.

Out of scope:

1. unconstrained discourse-level coreference.
2. probabilistic binding for mutating actions when deterministic uniqueness is absent.

## Entity Memory Model

Maintain per-session, recency-ordered typed entities:

1. canonical id (`layer_id`, `file_id`, `product_id`, etc.),
2. display name and alias tokens (title, filename stem, normalized variants),
3. source (`tool_result`, `tool_args`, `user_text`, `assistant_text`, optional `ui_selection`),
4. scenario scope and timestamp,
5. confidence/quality marker (exact id vs inferred mention).

Memory is updated after each turn from:

1. executed tool calls and results,
2. explicit textual mentions that resolve uniquely to known entities.

## Resolution Policy (Deterministic)

For each segment requiring binding:

1. Determine expected entity type(s) from target tool schema and route intent.
2. Collect candidate entities of compatible type and scenario scope.
3. Rank by deterministic policy:
   - same-segment explicit mention > same-turn prior segment > last tool-result entity > recent mention.
4. Bind only when exactly one top candidate remains after normalization and filtering.
5. If zero or multiple viable candidates:
   - do not execute mutation,
   - emit clarification-required outcome with top candidate options.

## Safety and Execution Semantics

1. Mutating actions with unresolved references are blocked and require clarification.
2. Deterministic path must never silently guess among multiple candidates.
3. Confirmation policy for destructive/high-cost actions remains unchanged and applies after successful binding.
4. Low-cost reversible actions may auto-execute once binding is unique and valid.

## Tooling Implications

1. Add assistant tool for layer creation from existing scenario file:
   - recommended name: `layer.create`.
2. Keep `layer.update_state` semantics strict (existing layers only).
3. Route "add this as a layer" style intents to `layer.create` when referent resolves to file/raster.
4. Optionally support explicit `create_if_missing` only as an explicit argument (not implicit fallback).

## UI/UX Visibility (Working Set)

Expose typed memory to users as a Working Set list:

1. show recent entities by type with source and recency,
2. support pin/unpin priority,
3. show pre-execution reference preview for mutating actions,
4. when blocked, provide clarification choices from current working set.

## Observability

Emit structured events/metadata:

1. resolver candidates (redacted identifiers where necessary),
2. selected binding and reason,
3. blocked reason codes (`ref_no_candidate`, `ref_ambiguous`, `ref_type_mismatch`),
4. resolver latency and stage counters.

## Testing Plan

Required coverage:

1. unit tests for normalization and candidate ranking by type.
2. deterministic integration tests:
   - "Generate slope map ... then Add this as a layer" -> `layer.create`.
   - ambiguous "this layer" -> clarification required.
   - typed phrases (`this file`, `this raster`, `this colormap`) bind correctly when unique.
3. regression cases for previous failure paths in assistant tool loop.

## Consequences

Positive:

1. safer and more predictable action binding,
2. better mixed-turn reliability for follow-up commands,
3. clearer user trust via visible working set and explicit clarification.

Tradeoffs:

1. added orchestration complexity and state management,
2. more tests and telemetry to maintain,
3. deterministic resolver will intentionally decline some linguistically valid but ambiguous references.

## Rollout

1. shadow mode logging of candidate resolution without action binding changes.
2. enable binding for `layer`/`file` first.
3. expand to `colormap`, `product`, `artifact`, `job` after eval gates pass.
4. keep feature-gated rollback path during rollout.

## Detailed Implementation Plan (Normative)

### 1. Tool Contract: `layer.create`

Add assistant tool `layer.create` for creating a scenario layer from an existing scenario file/product.

Required behavior:

1. Create new layer when target source exists and no equivalent layer exists.
2. If equivalent layer exists:
   - default: return existing layer with `status=exists` and do not create duplicate;
   - optional explicit override `on_existing` controls behavior.

Proposed request schema:

1. required:
   - `scenario_id: string`
2. source selector (exactly one required):
   - `source_file_id: string`
   - `product_id: string` (requires file resolution policy)
   - `relative_path: string` (scenario-relative)
3. optional display controls:
   - `title: string`
   - `visible: boolean` (default `true`)
   - `opacity: number` (default `1.0`)
   - `z_index: integer` (default top+1)
   - `render_mode: raster|vector` (default inferred from source)
   - `style: object` (default inferred baseline style)
4. optional existing-layer policy:
   - `on_existing: "return_existing" | "update_existing" | "error"` (default `return_existing`)

Proposed response schema:

1. `status: "created" | "exists" | "updated"`
2. `layer: { layer_id, scenario_id, source_file_id, title, visible, opacity, z_index, render_mode, style }`
3. `created: boolean`

### 2. Deterministic Resolver Algorithm

Resolver stage runs after segment classification/planner, before tool argument execution.

Inputs:

1. segment text,
2. target tool name + schema,
3. session typed-memory store,
4. active scenario id.

Algorithm:

1. Detect referential tokens: `this`, `that`, `it`, `these`, plus typed noun phrases.
2. Determine required target type set from tool schema:
   - example: `layer.create` expects `file|product`;
   - `layer.update_state` expects `layer`.
3. Build candidate list from typed memory, filtered by:
   - type compatibility,
   - active scenario (unless tool permits cross-scenario refs),
   - freshness window (default last 25 entities / last 10 turns).
4. Score candidates deterministically:
   - exact id mention: +1000
   - exact normalized alias mention: +900
   - same-turn previous segment mention: +700
   - last tool result entity: +600
   - recency decay: `-10 * age_rank`
   - pinned entity bonus: +200
5. Select:
   - no candidates => `blocked_requires_clarification`
   - single top candidate and margin >= `resolver_min_margin` => bind
   - otherwise => `blocked_requires_clarification`

Configurable thresholds:

1. `resolver_min_margin` default `80`
2. `resolver_max_candidates_for_prompt` default `5`

### 3. Error Codes and Observability Schema

Add resolver/follow-up codes under assistant telemetry taxonomy:

1. `ref_no_candidate`
2. `ref_ambiguous`
3. `ref_type_mismatch`
4. `ref_bound`
5. `layer_create_source_not_found`
6. `layer_create_ambiguous_source`

Per-turn metadata additions:

1. `resolver_bindings`: list of
   - `segment_id`
   - `token`
   - `expected_types`
   - `selected_entity_id`
   - `selected_entity_type`
   - `decision`
   - `reason_code`
2. `resolver_latency_ms`

Event payload additions (assistant WS):

1. `reference_resolution_completed`
   - `segment_count`
   - `bindings_count`
   - `blocked_count`
   - `latency_ms`
2. `reference_resolution_blocked`
   - `segment_id`
   - `reason_code`
   - `candidate_labels` (max N, redacted ids when needed)

### 4. Feature Flags and Config

Add config keys:

1. `backend.llm.reference_resolution_enabled = true`
2. `backend.llm.typed_memory_enabled = true`
3. `backend.llm.working_set_ui_enabled = true`
4. `backend.llm.resolver_min_margin = 80`
5. `backend.llm.resolver_history_turns = 10`
6. `backend.llm.resolver_max_candidates_for_prompt = 5`

Rollout defaults:

1. enable in shadow mode first:
   - decisions logged, no mutation binding changes.
2. promote to enforcing mode for `layer`/`file` intents after eval gates.

### 5. Working Set UI Contract (V1)

Expose new read endpoint (or assistant session detail field) for typed working set:

1. `GET /api/v1/assistant/sessions/{session_id}/working-set`

Response shape:

1. `session_id`
2. `active_scenario_id`
3. `items`: list of
   - `entity_id`
   - `entity_type`
   - `label`
   - `aliases`
   - `source`
   - `created_at_utc`
   - `last_seen_at_utc`
   - `pinned`
   - `confidence`

Optional mutation endpoints:

1. `POST /api/v1/assistant/sessions/{session_id}/working-set/{entity_id}:pin`
2. `POST /api/v1/assistant/sessions/{session_id}/working-set/{entity_id}:unpin`

UI behavior:

1. show recent typed items grouped by type,
2. show source + recency,
3. allow pin/unpin,
4. show pre-execution resolution banner for mutating turns:
   - `this -> file:slope.tif`.

### 6. Tests (Required for Done)

Backend unit tests:

1. resolver candidate scoring and margin behavior,
2. type filtering and scenario scoping,
3. ambiguity and no-candidate blocks.

Backend integration tests:

1. `Generate slope map ...` + `Add this as a layer ...` resolves to `layer.create`.
2. `this layer` ambiguous between two layers => clarification required, no mutation.
3. `this file` with unique last artifact => binds and succeeds.

Frontend tests:

1. Working-set rendering and pin state changes.
2. Resolution preview banner behavior.
3. Blocked clarification prompt display for `ref_ambiguous`/`ref_no_candidate`.
