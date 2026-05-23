# Marimo Token Caching Findings

Date: 2026-02-26

## Scope

This note summarizes what was learned while investigating whether marimo supports caching parts of LLM context ("token caching" / provider prompt caching), and what it would take to enable it for Lunar Analyst.

## Short Answer

- marimo does not currently appear to provide a built-in, provider-agnostic "token caching" feature or config knob.
- This was checked in:
  - local installed marimo `0.17.3`
  - upstream marimo latest release `0.20.2`
  - upstream `main` (as of 2026-02-25 / 2026-02-26 investigation)
- Enabling token caching would require either:
  - a marimo patch/fork to pass provider-specific cache hints/metadata, or
  - a FastAPI-side LLM gateway (preferred for Lunar Analyst) that implements prompt caching outside marimo.

## What Was Verified

### 1. marimo `0.17.3` (local environment)

In `0.17.3`, marimo's AI endpoints parse chat/completion requests and forward messages to provider-specific builders. The provider payload assembly did not include explicit prompt-cache fields/hints.

Observed characteristics:
- AI endpoints forward request messages on each request.
- OpenAI/Anthropic provider payload builders set model/tokens/tools/thinking settings, but no cache directives.
- No request schema/config field exposing token caching controls.

### 2. marimo `0.20.2` (latest release checked)

In newer marimo, the AI pipeline moved to `pydantic-ai` and `ui_messages` (AI SDK/Vercel-style messages), but there is still no obvious built-in token caching feature in marimo itself.

Observed characteristics:
- `ChatRequest` / `AiCompletionRequest` accept raw `ui_messages`.
- marimo forwards `ui_messages` to the provider layer.
- No explicit `cache_control`, `cached_tokens`, `prompt_cache`, or similar support found in marimo AI server code.
- `provider_metadata` is preserved on some message parts, which may provide an extension path for provider-specific cache hints.

## Important Distinction

"Token caching" here means provider-side prompt/context caching (reusing a stable prompt prefix or cached context on the model-provider side), not:

- marimo notebook cell execution caching
- browser HTTP caching
- backend result caching (COGs, derived rasters, etc.)

## What marimo Would Need to Support This Natively

To support token caching in a robust way, marimo would need all of the following:

1. A way to mark cacheable prompt/message segments
- Example concept: per-message or per-part cache hints (stable vs dynamic).

2. Request schema support
- `ui_messages`/message metadata would need a documented, supported way to carry cache directives.

3. Provider mapping logic
- marimo would need provider-specific translation for cache hints.
- This cannot be fully generic because provider APIs differ.

4. Config and feature flags
- Enable/disable per provider/model.
- Safe fallback when provider/model does not support caching.

5. Invalidation strategy
- Cache keys/hints must be invalidated when notebook context changes materially.
- Tool definitions, system prompt changes, scenario switch, or selected-cell context changes can all invalidate cached prefixes.

6. Observability
- Surface when cache hints were sent.
- Ideally report provider token usage details (including cached-token usage if exposed by the provider SDK/response).

## Practical Enablement Options for Lunar Analyst

## Option A (Preferred): FastAPI-side LLM Gateway / Prompt Planner

Implement token caching in Lunar Analyst's FastAPI control plane instead of patching marimo first.

Why this fits the architecture:
- FastAPI is already the control plane and integration boundary.
- Keeps marimo as a notebook UI/editor, reducing fork maintenance.
- Lets Lunar Analyst define stable vs dynamic context using scenario-aware knowledge.

Recommended split:
- Stable prefix (cache candidate):
  - scenario metadata
  - Lunar Analyst helper APIs/contracts
  - reusable coding rules/instructions
  - static notebook helper docs
- Dynamic suffix (non-cacheable):
  - current user request
  - selected cells / recent cells
  - latest tool outputs
  - recent conversation turns

Suggested cache key inputs (example):
- `scenario_id`
- `notebook_path`
- `model_id`
- `system_prompt_hash`
- `tools_schema_hash`
- `stable_context_hash`

## Option B: Patch/Fork marimo

Patch marimo to pass provider-specific cache hints through `ui_messages` / `provider_metadata`, then map them in provider adapters.

What this would involve:
- Define and document a metadata convention for cache hints.
- Preserve metadata through message conversion.
- Extend provider-layer request construction (or pydantic-ai integration points) to emit provider-specific cache directives.
- Add tests for:
  - metadata passthrough
  - provider payload generation
  - graceful fallback when unsupported

Tradeoff:
- More direct notebook UX integration, but higher maintenance burden and upstream drift risk.

## Option C: Upstream contribution to marimo

If the goal is long-term use without a fork, an upstream proposal could add:
- provider-agnostic cache metadata conventions
- provider-specific implementations behind capability flags
- usage reporting hooks

This is the cleanest ecosystem outcome, but not the fastest path for Lunar Analyst delivery.

## Why FastAPI-side Caching Is the Better First Step Here

- Aligns with Lunar Analyst architecture invariant: FastAPI is the authoritative control plane.
- Avoids coupling critical prompt/caching behavior to marimo release cadence.
- Lets Lunar Analyst apply scenario-aware invalidation rules (which marimo cannot know natively).
- Easier to instrument and test using existing backend contract/integration testing patterns.

## Expected Work to Enable Token Caching (Practical Estimate)

### FastAPI-side gateway path (preferred)

Moderate implementation task, roughly:
- prompt segmentation/planner service
- cache key strategy + storage
- provider adapter integration
- observability + tests
- UI integration path (if marimo chat is bypassed for certain actions)

### marimo patch path

Moderate-to-high effort due to:
- provider-specific behavior
- pydantic-ai integration constraints
- long-term maintenance against marimo upgrades

## Risks / Caveats

- Provider support differs by model/provider and may change.
- Cache hit rates can be poor without careful prompt segmentation.
- Incorrect invalidation can produce stale or misleading outputs.
- Cached token usage accounting may not be uniformly available across providers/SDKs.

## References Used During Investigation

- marimo local install (`0.17.3`) source under `D:\projects\env_311\Lib\site-packages\marimo\...`
- marimo upstream repo (inspected latest release `0.20.2` and `main`)
- Lunar Analyst context:
  - `docs/NEW_DESIGN.md`
  - backend marimo launch integration in `backend/api/dependencies.py`

