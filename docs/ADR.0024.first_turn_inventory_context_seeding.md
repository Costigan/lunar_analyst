# ADR.0024: First-Turn Inventory Context Seeding

Status: Accepted  
Date: 2026-03-10  
Deciders: Lunar Analyst architecture team

## Context

Current assistant behavior does not pre-seed scenario inventory context on new sessions.  
For many imperative geospatial prompts, the model’s first action is often an exploratory read (`product.list`), which can:
- consume a full iteration;
- inject a large payload into context;
- increase empty-completion risk on local models.

This gap is visible even with deterministic routing improvements (ADR 0022/0023), for prompts not matched by deterministic actions.

## Decision

Add **first-turn inventory context seeding** for new assistant sessions and scenario changes:
- before first model completion in a turn (for a given session+scenario), inject a compact inventory snapshot into model context;
- snapshot includes compact summaries of:
  - `product.list` (scenario-scoped, truncated/compacted);
  - `layer.list_visible` (scenario-scoped, compact);
- do not expose raw large payloads; use the same compacting policy as model replay.

This is a context-management behavior, not a user-visible tool call.

## Scope

In scope:
- Session+scenario-scoped seeding marker (seed once per scenario per session, then refresh only when needed).
- Provider-independent context injection.
- Logging for seed applied/skipped.

Out of scope:
- Full inventory refresh policy for long sessions.
- Provider-specific prompt-cache internals.

## Rationale

- Reduces first-turn tool-loop drift into inventory-discovery calls.
- Improves reliability for imperative mutation workflows.
- Works even when provider-side prefix caching is unavailable (for example Ollama).

## Provider Cache Clarification

This ADR does **not** require provider-native prefix cache support:
- OpenAI/Google may benefit from stable-prefix cache keys.
- Ollama may still benefit from better first-turn behavior despite no provider-side cache key support.

## Implementation Plan

1. Add seed builder in assistant service/context builder:
- produce compact inventory seed text/object from scenario-scoped services.

2. Add per-session runtime tracking:
- `seeded_scenarios: set[str]` in runtime state.
- apply seed on first model-loop turn for unseeded scenario.

3. Inject seed into conversation/system augmentation:
- prepend deterministic compact seed block before first completion.

4. Add observability:
- log `assistant context seed applied` with session/scenario/item counts/char budget.
- log `assistant context seed skipped` with reason (`already_seeded`, `no_scenario`, `seed_error`).

5. Add tests:
- first turn includes seed for scenario.
- second turn same scenario skips seed.
- scenario switch triggers seed for new scenario.
- seed payload is compact/truncated and bounded.

6. Documentation:
- update `docs/DESIGN.md` assistant context strategy subsection.

## Risks

- Seed staleness if scenario changes after first seed.
- Token overhead if budgets are not tightly bounded.

Mitigation:
- compact/truncate hard caps;
- optional reseed trigger on explicit user request or state-changing tool results.
