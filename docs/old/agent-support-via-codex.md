# Plan: Agent Support via Codex CLI and Gemini CLI (SSE MCP Transport)

## 1. Goal
Add two new local assistant provider options:
- `local_codex_cli`
- `local_gemini_cli`

These should be first-class alternatives to existing providers and selectable from the Lunar Analyst assistant UX/API.

Primary integration method: use MCP over SSE so the CLI agents can call Lunar Analyst MCP tools directly, rather than relying on local stdin/stdout worker tool mediation.

## 2. Scope and Non-Goals

### In Scope
- Main app assistant provider refactor for cleaner provider extensibility.
- MCP transport expansion to include SSE.
- Codex CLI and Gemini CLI provider wiring that points to Lunar Analyst MCP SSE endpoints.
- Provider catalog and UI support for explicit provider and model selection.
- Confirmation/safety enforcement for mutating actions in external-agent MCP flows.
- Tests and docs updates for the new architecture.

### Out of Scope
- Marimo agent integration changes.
- JobHandlers contract/model changes.
- Tauri packaging/deployment changes.
- Replacing or removing existing providers (`local_ollama`, `openai`, `anthropic`, `google`, `local_subprocess`).

## 3. Decision Summary
This plan supersedes prior subprocess-stdio-only designs for Codex/Gemini support.

Key decisions:
1. Add SSE transport for MCP in the main app backend.
2. Refactor assistant provider plumbing to support multiple provider execution modes cleanly.
3. Keep current providers working unchanged while adding CLI providers.
4. Enforce the existing mutation confirmation policy for all agent-triggered mutating actions, including CLI-over-MCP paths.

## 4. Current-State Gaps (Why Refactor First)
Current code already has a provider abstraction, but adding new agent classes cleanly is blocked by a few structural issues:

1. Provider registration is hardcoded in one method and not factory-driven.
2. Frontend provider/model selection is biased to `local_ollama` and hardcoded model lists.
3. Frontend turn creation currently sends only `model_id`; it does not consistently send `provider_id`.
4. MCP transport currently supports HTTP JSON-RPC POST and stdio, but not SSE.
5. Assistant orchestration is optimized for backend-owned tool-loop providers; external MCP-driven agents need explicit handling boundaries.

## 5. Target Architecture

### 5.1 Provider Execution Modes
Introduce explicit execution modes in provider metadata/catalog:
- `tool_loop`: existing backend-mediated providers (current behavior).
- `external_mcp_agent`: CLI agents that connect to Lunar Analyst MCP over SSE.

This avoids special-case branching by provider ID and makes future agent additions additive.

### 5.2 MCP Transport
Keep existing MCP HTTP JSON-RPC endpoint and stdio transport for compatibility.
Add SSE MCP transport endpoints for external agent clients.

Transport requirements:
- Session/connection lifecycle support.
- JSON-RPC request/response correlation.
- Structured error envelopes.
- Optional auth token enforcement from existing MCP config.

### 5.3 Assistant Runtime Behavior
For `external_mcp_agent` providers:
- Assistant still creates/persists turns/messages/tool-call audit records.
- CLI agent performs planning/tool choice via MCP SSE.
- Backend remains source of truth for tool execution and confirmation rules.
- Provider access policy is explicit and config-driven:
  - `access_mode = "mcp_only"`: safe default. Launch CLI with an isolated temp working directory and mode-specific "safe" CLI flags.
  - `access_mode = "scenario_root"`: launch CLI inside the configured scenarios root, with mode-specific CLI flags that should restrict filesystem access to that root.

### 5.4 UX and Contracts
- Assistant UI must expose provider selection and provider-scoped model selection.
- Turn requests must include both `provider_id` and `model_id`.
- Provider catalog should include provider kind, execution mode, and available models.

## 6. Implementation Phases

### Phase 0: Refactor Foundation
- Refactor provider registry into factory-driven registration with typed provider config parsing.
- Extend provider catalog model to include execution mode and capability hints.
- Keep current provider behavior and defaults stable.

Acceptance:
- No regressions for existing providers/tests.
- Catalog includes execution metadata.

### Phase 1: MCP SSE Transport
- Add SSE transport module under `backend/mcp/transports`.
- Add API routes for MCP SSE handshake/message flow.
- Preserve current `/api/v1/mcp` POST behavior.

Acceptance:
- External client can initialize, list tools, call tools via SSE transport.
- Existing MCP HTTP and stdio tests still pass.

### Phase 2: Codex CLI and Gemini CLI Providers
- Add provider configs and registry wiring for:
  - `local_codex_cli`
  - `local_gemini_cli`
- Implement provider adapters that launch CLI processes configured to use Lunar Analyst MCP SSE endpoint.
- Add timeout, process cleanup, and stderr diagnostics.

Acceptance:
- `/api/v1/assistant/providers` lists enabled CLI providers.
- Turn execution works when either provider is selected explicitly.

### Phase 3: Frontend Provider/Model Refactor
- Add provider dropdown in Assistant Input pane.
- Filter model dropdown by selected provider.
- Send both `provider_id` and `model_id` in turn creation requests.
- Remove hardcoded model/provider assumptions.

Acceptance:
- User can switch providers in UI.
- Requests carry explicit provider+model.

### Phase 4: Safety/Hardening
- Ensure mutating MCP tool calls from external agents still require confirmation.
- Add transport-level observability: connection/session IDs, latency, failures.
- Add defensive limits: payload sizes, timeouts, retry/abort rules.

Acceptance:
- No mutation path bypasses confirmation policy.
- Failures are actionable in logs and API responses.

## 7. File-Level Change Plan

### Backend
- Update [provider_registry.py](/D:/projects/lunar_analyst/backend/services/assistant/provider_registry.py)
  - factory-driven registration
  - CLI provider entries
  - execution mode metadata in catalog
- Update [assistant_models.py](/D:/projects/lunar_analyst/backend/contracts/assistant_models.py)
  - provider metadata model extensions (execution mode/capability fields)
- Update [assistant_service.py](/D:/projects/lunar_analyst/backend/services/assistant/assistant_service.py)
  - explicit provider routing behavior by execution mode
  - safety policy application for external-agent paths
- Add SSE transport module(s) under:
  - `backend/mcp/transports/`
- Update [mcp.py](/D:/projects/lunar_analyst/backend/api/routers/mcp.py)
  - SSE route(s) and wiring
- Update [lunar_analyst.toml](/D:/projects/lunar_analyst/config/lunar_analyst.toml)
  - disabled-by-default `local_codex_cli` and `local_gemini_cli` blocks
  - SSE transport config knobs if needed

### Frontend
- Update [assistantService.ts](/D:/projects/lunar_analyst/backend/web/lunar_analyst/src/services/assistantService.ts)
  - include `provider_id` in turn create payload
- Update [AssistantInputPane.tsx](/D:/projects/lunar_analyst/backend/web/lunar_analyst/src/components/assistant/AssistantInputPane.tsx)
  - provider selector UI
  - provider-scoped model selector behavior
- Update [App.tsx](/D:/projects/lunar_analyst/backend/web/lunar_analyst/src/App.tsx)
  - maintain selected provider state
  - derive model options from selected provider catalog

### Docs
- Update [LLM_HAND_TESTING.md](/D:/projects/lunar_analyst/docs/LLM_HAND_TESTING.md)
  - add SSE MCP hand tests for CLI providers
- Update [DESIGN.md](/D:/projects/lunar_analyst/docs/DESIGN.md)
  - document MCP SSE transport and provider execution-mode model

## 8. Config Proposal (Draft)

```toml
[backend.llm.local_codex_cli]
enabled = false
model = "gpt-5-codex"
models = ["gpt-5-codex"]
command = ["codex", "exec"]
args = ["--model", "{model_id}"]
access_mode = "mcp_only" # safe default
mcp_only_args = ["--sandbox", "read-only", "--skip-git-repo-check"]
scenario_root_args = ["--sandbox", "workspace-write", "--skip-git-repo-check"]
scenario_root = "D:/lunar_analyst_scenarios"
mcp_sse_url = "http://127.0.0.1:8000/api/v1/mcp/sse"
mcp_server_name = "lunar_analyst"
timeout_seconds = 120

[backend.llm.local_gemini_cli]
enabled = false
model = "gemini-2.5-pro"
models = ["gemini-2.5-pro", "gemini-2.5-flash"]
command = ["gemini.cmd"]
args = ["--model", "{model_id}", "--prompt", "{prompt_text}", "--allowed-mcp-server-names", "{mcp_server_name}", "--output-format", "text"]
access_mode = "mcp_only" # safe default
mcp_only_args = ["--approval-mode", "plan"]
scenario_root_args = ["--approval-mode", "default", "--include-directories", "{scenario_root}"]
scenario_root = "D:/lunar_analyst_scenarios"
mcp_sse_url = "http://127.0.0.1:8000/api/v1/mcp/sse"
mcp_server_name = "lunar_analyst"
timeout_seconds = 120

[backend.mcp]
enabled = true
http_enabled = true
stdio_enabled = true
sse_enabled = true
http_auth_token_env = "LUNAR_ANALYST_MCP_TOKEN"
```

Note: exact CLI flags are CLI-version-specific; use `mcp_only_args`/`scenario_root_args` as the compatibility layer and keep `mcp_only` as default.
Implementation note:
- Codex CLI MCP endpoint wiring is injected per invocation via `codex exec -c mcp_servers.<name>...` overrides (no global config write required).
- Gemini CLI MCP endpoint wiring is prepared with `gemini mcp add ... --transport sse --scope project` in the provider working directory before each turn.

## 9. Test Plan

### Unit Tests
- provider registry factory/config parsing
- provider catalog execution-mode fields
- SSE transport request/response framing and error behavior
- CLI adapter timeout/process cleanup/error parsing

### Contract/API Tests
- assistant provider catalog includes CLI providers when enabled
- turn create payload accepts explicit `provider_id` and `model_id`
- MCP SSE initialize/tools/list/tools/call paths
- confirmation-required behavior on mutating tool calls from external-agent flow

### Frontend Tests
- provider selector renders/updates
- model list filters by provider
- submitted turn payload includes both `provider_id` and `model_id`

### Regression Tests
- existing assistant API, WS, MCP HTTP, and MCP stdio paths remain passing

## 10. Risks and Mitigations
1. Risk: SSE protocol mismatch with CLI expectations.
- Mitigation: phase-0 compatibility spike and fixture-based parser/transport tests.

2. Risk: mutation confirmation bypass through external MCP agent path.
- Mitigation: enforce confirmation at MCP tool-call boundary and test it explicitly.

3. Risk: Windows CLI process behavior (shim, orphan children, hangs).
- Mitigation: explicit launch strategy, timeout, and process-tree termination tests.

4. Risk: provider drift across CLI versions.
- Mitigation: log versions/flags used; keep adapters tolerant and feature-gated.

## 11. Rollback Strategy
- Keep both CLI providers disabled by default.
- Keep SSE transport gated by config.
- If issues occur, disable `local_codex_cli`, `local_gemini_cli`, and/or `sse_enabled` with no schema/data migration.

## 12. Execution Checklist
- [ ] Provider registry refactor complete with no existing-provider regressions.
- [ ] MCP SSE transport implemented and covered by tests.
- [ ] Codex/Gemini CLI providers wired via MCP SSE.
- [ ] UI supports explicit provider + model selection.
- [ ] Mutating-action confirmation enforced in external-agent path.
- [ ] Assistant/MCP contract and frontend tests pass.
- [ ] Main architecture docs and hand-test docs updated.

## 13. Notes
- Main app assistant support only; Marimo integration remains unchanged.
- Keep changes additive and reversible.
