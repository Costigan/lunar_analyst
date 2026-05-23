# LLM Tool-Calling + Latency Plan

## 1. Goal
Implement both changes in one sequence:
1. Replace parser-gated execution with model-driven tool calling (agent loop).
2. Make assistant interaction fast enough for command UX on local and remote models.

This plan keeps existing confirmation policy and MCP tool contracts, and is intentionally additive.

## 2. Scope and Non-Goals
In scope:
1. Assistant backend loop and provider interfaces.
2. WS turn/event behavior needed for fast UI feedback.
3. Config and telemetry for latency tuning.

Out of scope:
1. Multi-user auth redesign.
2. New MCP authorization policy model (tracked separately).
3. Script argument inference (still assume no args for now).

## 3. Current State (What We Are Changing)
1. Assistant currently has parser-based command planning in `backend/services/assistant/assistant_service.py`.
2. Parser misses lead to text replies, even when tools could solve the request.
3. Latency is sensitive to local model warm/cold state and first-token delay.

## 4. Target Behavior
1. Parser remains only a fast-path optimization.
2. If fast-path does not confidently execute, model receives tool schemas and decides tool use.
3. One turn may execute multiple tool calls (bounded loop).
4. Mutating calls still require confirmation using existing policy flow.
5. UI receives immediate `assistant_turn_started` and incremental `assistant_delta` updates.
6. Local models are kept warm and tuned for short command responses by default.

## 5. Contracts That Must Hold
REST routes (unchanged paths):
1. `POST /api/v1/assistant/sessions/{session_id}/turns`
2. `POST /api/v1/assistant/sessions/{session_id}/confirmations/{confirmation_id}`
3. `GET /api/v1/assistant/providers`

WS event names (existing):
1. `assistant_turn_started`
2. `assistant_delta`
3. `assistant_tool_call_proposed`
4. `assistant_tool_call_started`
5. `assistant_tool_call_completed`
6. `assistant_confirmation_required`
7. `assistant_confirmation_resolved`
8. `assistant_scenario_changed`
9. `assistant_turn_completed`
10. `assistant_error`

Contract files to update only if schema changes:
1. `backend/contracts/assistant_models.py`
2. `backend/contracts/assistant_events.py`
3. `docs/contracts/generated/v1/assistant_turn.schema.json`
4. `docs/contracts/generated/v1/assistant_ws_event_envelope.schema.json`
5. `docs/contracts/generated/v1/openapi.json`

## 6. Implementation Phases

### Phase 1: Provider Tool-Call Contract (Backend Foundation)
Objective:
1. Make every provider return either text or tool calls (or both), without executing tools in provider code.

Files:
1. `backend/services/assistant/providers/base.py`
2. `backend/services/assistant/providers/ollama_provider.py`
3. `backend/services/assistant/providers/openai_provider.py`
4. `backend/services/assistant/providers/anthropic_provider.py`
5. `backend/services/assistant/providers/google_provider.py`
6. `backend/services/assistant/providers/subprocess_provider.py`
7. `backend/services/assistant/provider_registry.py`

Design details:
1. Standardize completion payload to include:
   1. `assistant_text`
   2. `tool_calls[]` with `call_id`, `name`, `arguments_json`
   3. `finish_reason`
2. Add provider capability flags for:
   1. native tool-calling support
   2. streaming support
3. Keep compatibility fallback for providers that return text-only.

Tests:
1. Add `backend/tests/worker/test_assistant_provider_tool_contract.py`.
2. Update provider-specific worker tests as needed.

Exit criteria:
1. A mocked provider can return multiple tool calls in one response.
2. Existing text-only turns still pass unchanged.

### Phase 2: Assistant Agent Loop (Model-Decided Tools)
Objective:
1. Move orchestration into a bounded loop in `AssistantService.create_turn`.

Files:
1. `backend/services/assistant/assistant_service.py`
2. `backend/services/assistant/context_builder.py`
3. `backend/services/assistant/tool_registry.py`
4. `backend/services/assistant/policy_service.py`

Design details:
1. Loop shape:
   1. build model prompt + tool schema,
   2. request provider completion,
   3. if tool calls returned, evaluate confirmation policy and execute approved calls,
   4. append tool results to conversation scratchpad,
   5. continue until final assistant text or limits hit.
2. Limits (config-backed):
   1. `max_tool_iterations_per_turn`
   2. `max_tool_calls_per_iteration`
3. Parser behavior:
   1. parser may fast-path obvious commands,
   2. parser miss must enter model tool loop (not text-only fallback).
4. Preserve current confirmation UX and session-scoped approvals.

Tests:
1. Extend `backend/tests/contract/test_phase6_assistant_api.py`.
2. Add `backend/tests/worker/test_assistant_tool_loop.py`.
3. Add `backend/tests/worker/test_assistant_confirmation_in_tool_loop.py`.
4. Add `backend/tests/worker/test_assistant_parser_fallback_to_tool_loop.py`.

Exit criteria:
1. Prompt like "switch to test_scenario" triggers tool use through model path when parser does not fast-path.
2. Multi-step turns (for example: list scenarios, then switch) work in one turn.

### Phase 3: Latency Fast Path (Keep It Responsive)
Objective:
1. Reduce time-to-first-feedback and average turn time, especially with local Ollama models.

Files:
1. `backend/services/assistant/assistant_service.py`
2. `backend/services/assistant/provider_registry.py`
3. `backend/services/assistant/providers/ollama_provider.py`
4. `backend/contracts/assistant_models.py` (usage metadata)
5. `backend/web/lunar_analyst/src/services/assistantWsClient.ts`
6. `backend/web/lunar_analyst/src/components/assistant/AssistantResponsePane.tsx`
7. `config/lunar_analyst.toml`

Design details:
1. Emit `assistant_turn_started` immediately on turn acceptance.
2. Stream `assistant_delta` during generation when provider supports it.
3. Add command-oriented response budget:
   1. low default `max_tokens` for command turns,
   2. larger budget for explicit analysis/explanation turns.
4. Add local model warm controls:
   1. `ollama_keep_alive`
   2. optional startup prewarm for selected local model.
5. Add slow-turn fallback policy:
   1. if first token exceeds threshold, retry on configured fast model/provider.

Tests:
1. Add `backend/tests/worker/test_assistant_fast_mode.py`.
2. Extend `backend/tests/contract/test_phase6_assistant_ws.py` for delta cadence and start event timing.
3. Extend `backend/web/lunar_analyst/src/__tests__/assistantWsClient.test.ts` for streamed deltas.

Exit criteria:
1. Warm-model command turns show immediate start event and visible partial output before completion.
2. Cold-start behavior is bounded by fallback policy rather than indefinite wait.

### Phase 4: Telemetry, Tuning, and Safe Rollout
Objective:
1. Make latency and loop behavior observable and tunable in production-like runs.

Files:
1. `backend/services/assistant/assistant_service.py`
2. `backend/contracts/assistant_models.py`
3. `docs/LLM_HAND_TESTING.md`
4. `docs/HOW_TO_MANUALLY_TEST.md`
5. `docs/DESIGN.md`
6. `CHANGELOG.md`

Design details:
1. Record per-turn metadata:
   1. `planner_mode` (`parser_fast_path`, `model_tool_loop`, `model_text_only`)
   2. `latency_ms_first_event`
   3. `latency_ms_total`
   4. `tool_call_count`
   5. `fallback_used`
2. Add hand-test matrix:
   1. parser fast-path,
   2. parser miss + model tool success,
   3. confirmation-required mutation,
   4. slow local model fallback,
   5. scenario switch with Explorer synchronization.

Tests:
1. Add `backend/tests/worker/test_assistant_latency_metadata.py`.
2. Keep all existing assistant contract tests passing.

Exit criteria:
1. Metrics are present on every turn.
2. Manual runbook can reproduce latency and behavior checks.

## 7. Config Additions
`config/lunar_analyst.toml` additions:

```toml
[backend.llm.performance]
max_tool_iterations_per_turn = 6
max_tool_calls_per_iteration = 4
command_max_output_tokens = 192
analysis_max_output_tokens = 1024
first_token_timeout_ms = 2500
slow_turn_fallback_provider = "local_ollama"
slow_turn_fallback_model = "qwen2.5-coder:7b-instruct-q4_K_M"
ollama_keep_alive = "15m"
prewarm_on_startup = true
```

## 8. Model Selection Guidance for Local UX
Default routing suggestion for current local inventory:
1. Command/control turns: `qwen2.5-coder:7b-instruct-q4_K_M` (fast path).
2. Deep analysis/code synthesis: `qwen3.5:35b-a3b` or `deepseek-coder-v2:16b` (on demand).
3. If GPU is busy, allow automatic fallback to remote configured provider.

## 9. Risks and Controls
1. Tool-loop runaway:
   1. hard iteration/call limits,
   2. explicit stop reason in final response.
2. Incorrect mutation:
   1. keep existing confirmation gating,
   2. preserve per-session approvals.
3. Latency regressions:
   1. keep parser fast-path,
   2. warm model + streaming + fallback.
4. Provider differences:
   1. normalize provider outputs through `providers/base.py` contract.

## 10. Definition of Done
1. Model can decide and execute appropriate tools when parser does not match.
2. Multi-tool turns complete with grounded assistant output.
3. Command UX is responsive with immediate WS feedback and improved first-token timing.
4. Assistant/MCP API and WS contract tests pass with added coverage.
5. Updated docs include architecture rationale and hand-testing instructions.
