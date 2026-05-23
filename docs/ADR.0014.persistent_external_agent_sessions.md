# ADR.0014: Persistent External Agent Sessions and Context Caching

## Status
Accepted

## Context
The current external-agent integration for assistant turns is stateless and one-shot (`subprocess.run` per turn). For each turn, the backend relaunches the CLI, re-sends prompt context, and waits for process exit.

This causes:
1. High turn latency from repeated process startup.
2. Lost cache efficiency by repeatedly rebuilding stable context.
3. Repeated MCP/tool schema negotiation overhead.
4. UX mismatch with interactive assistant expectations.

Design review and implementation trial also surfaced key risk areas to handle explicitly:
1. Turn timeout must remain enforceable under blocking stdout reads.
2. One assistant session can issue overlapping turns; stream corruption must be prevented.
3. Process reuse must not ignore model/access-mode/cwd changes.
4. Compaction must keep backend and CLI state aligned.
5. Background cleanup and shutdown paths must reliably terminate child processes.

## Decision
Adopt a persistent interactive process model for **both** external MCP CLI providers:
1. `gemini_cli`
2. `codex_cli`

The model is authoritative for `execution_mode = "external_mcp_agent"` providers.

### 1) Protocol: Interactive stdin/stdout with JSON output
1. Backend launches each CLI in interactive session mode.
2. Turns are sent over `stdin` (not argv `--prompt`).
3. CLI output is consumed from `stdout` as JSON-framed events/results.
4. Backend forwards incremental text deltas to assistant WS events when present.
5. Backend must still support a final non-streaming completion path when only terminal JSON is emitted.

### 2) Process identity and reuse
1. Persistent process identity is keyed by session + launch context fingerprint, including:
   1. assistant `session_id`
   2. provider id
   3. model id
   4. access mode (`mcp_only` or `scenario_root`)
   5. effective working directory
2. If any fingerprint component changes, backend restarts process for that session.

### 3) Turn serialization and safety
1. At most one in-flight turn is allowed per persistent process.
2. Backend enforces per-process locking to prevent stdin/stdout interleaving.
3. Timeout enforcement must not rely on blocking character reads alone; read loop uses timeout-aware buffering.

### 4) Context and compaction alignment
1. Session compaction in Lunar Analyst may terminate external process state for that session.
2. Next turn recreates the process and seeds it from compacted backend history/system prompt.
3. External CLIs may also compact internally; backend remains source of truth for durable session history.

### 5) MCP lifecycle
1. MCP server registration/connection is established once per persistent process lifecycle.
2. Per-turn re-registration is avoided unless process restart occurs.
3. Failure during registration is surfaced as a turn error with stderr diagnostics.

### 6) Cleanup and shutdown
1. Idle cleanup runs periodically and terminates expired or dead processes.
2. Cleanup worker is singleton per backend process.
3. Backend shutdown explicitly terminates all managed external CLI children.

### 7) Configuration contract
1. Persistent mode is feature-flagged per external provider (`persistent = true|false`).
2. Persistent providers must use interactive prompt delivery via stdin.
3. JSON output mode is required for persistent mode.

## Non-Goals
1. Introducing a new brokered event bus for assistant WS streaming.
2. Changing non-external providers (`openai`, `anthropic`, `google`, `ollama`) to persistent local process orchestration.
3. Full cross-restart reattachment to arbitrary pre-existing external OS processes.

## Implementation Notes
1. Keep `AssistantProvider.complete(..., session_id, on_delta, ...)` as the provider contract for streaming/session-aware providers.
2. Prefer line-delimited JSON event framing where CLI supports it; otherwise use robust framed JSON parsing with explicit end-of-turn semantics.
3. Track stderr tail per process for actionable failure messages.
4. Preserve scenario-root path safety checks before launching any process in `scenario_root` mode.

## Validation Requirements
1. Unit tests for process manager:
   1. process reuse and restart on fingerprint change
   2. idle cleanup and explicit shutdown
   3. per-session turn serialization
   4. timeout behavior under stalled stdout
2. Assistant integration tests:
   1. external turn success path with streaming deltas
   2. compaction-triggered process reset
   3. access mode + scenario working directory propagation
3. Regression checks:
   1. provider registry compatibility with existing provider stubs/tests
   2. no startup failure when cleanup worker starts

## Consequences
1. Resource management complexity increases (long-lived child processes, locks, cleanup lifecycle).
2. Turn path reliability improves when implemented with strict framing, timeout control, and explicit shutdown.
3. Expected latency and token/caching efficiency improve for long-running assistant sessions.
