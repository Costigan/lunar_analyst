# ADR 0001: Process Model Boundaries (FastAPI, Worker, Marimo)

- Status: Accepted
- Date: 2026-02-14
- Owners: Architecture (Codex), Implementation (Gemini)
- Related: `AGENTS.md`, `docs/PLAN.md`, `docs/API_CONTRACT.md`

## Context
Lunar Analyst is moving to a browser-first architecture and must combine:
- authoritative scenario/API state management,
- native heavy compute via `pythonnet` + `.NET 9` (`moonlib.dll`), and
- exploratory notebook workflows (Marimo).

A process model decision is required to reduce crash blast radius, avoid contract drift, and preserve data safety for scenario roots and file serving.

## Decision
We adopt a four-process topology with strict boundaries:

1. FastAPI Service (authoritative control plane)
- Owns scenario lifecycle, contract enforcement, job orchestration, and file-id based asset serving.
- Publishes system/job/layer events over WebSocket.

2. Compute Worker (separate Python process)
- Hosts `pythonnet` + .NET runtime + `moonlib.dll` for long-running heavy compute.
- Executes queued and immediate async jobs.
- Emits structured progress, logs, and cancellation checkpoints.

3. Marimo (separate process)
- Acts only as a client of FastAPI via REST/WS.
- Must not mutate scenario DB state directly.
- Disconnect/reconnect must not stop active jobs.

4. Browser/Tauri Client
- Consumes FastAPI contracts only (REST/WS).

## Operational Rules
- Worker and Marimo are supervised independently from FastAPI runtime concerns.
- If worker crashes, FastAPI marks active jobs failed/recoverable per policy and can restart worker.
- API contract is the only shared boundary between UI, Marimo, and orchestration.
- File serving remains file-id based with normalized, allowlisted scenario-root paths.
- CRS metadata is explicit and never silently reprojected.

## Rationale
- Process isolation reduces native-interop crash risk from `pythonnet` and GPU/native dependencies.
- A single authoritative control plane prevents state divergence between notebook and UI clients.
- REST/WS-only integration supports replayability, testing, and contract governance.
- Separation enables cancellation/progress guarantees without coupling user notebook execution to service availability.

## Consequences
Positive:
- Improved reliability and clearer ownership boundaries.
- Better contract testability (REST + WS event schemas).
- Lower integration drift risk as clients evolve.

Tradeoffs:
- More lifecycle/supervision complexity.
- Requires explicit event and auth/session handling between processes.

## Out of Scope
- Worker implementation details for specific algorithms.
- Packaging/deployment mechanics (Tauri).
- Stage 2 API shape expansion.

## Follow-on Tasks
- Freeze Stage 1 REST/WS schemas and error envelope in `/api/v1`.
- Add contract tests (OpenAPI + WS schema + error envelope checks) in CI.
- Add `/health/native` to validate .NET bridge load + tiny compute call.