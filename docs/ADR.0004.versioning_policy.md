# ADR 0004: API and Event Versioning Policy

- Status: Accepted
- Date: 2026-02-14
- Deciders: Lunar Analyst architecture/design owners
- Related: `docs/PLAN.md`, `docs/API_CONTRACT.md`, `docs/ADR.0003.option_c_stage_gates.md`

## Context

Lunar Analyst has multiple clients (web/Tauri, Marimo notebook helpers, automation). A stable versioning policy is required to prevent contract drift and to define when additive changes are allowed versus when a new API version is required.

## Decision

### 1) REST API versioning

- REST APIs are versioned in the path (for example, `/api/v1/...`).
- Within `v1`, changes are additive-only.
- Breaking changes require a new major path version (`/api/v2/...`).

Breaking changes include:
- Removing or renaming fields in responses.
- Changing field types or enum meanings incompatibly.
- Making previously optional fields required in existing endpoints.
- Changing endpoint semantics in a way that breaks existing clients.

### 2) WebSocket event versioning

- Stage 1 WebSocket envelope is fixed at `schema_version = "1.0"`.
- Stage 1 event name set is fixed to:
  - `job_queued`
  - `job_started`
  - `job_progress`
  - `job_completed`
  - `job_failed`
  - `job_cancelled`
  - `layer_added`
  - `layer_updated`
  - `layer_removed`
- Additive event payload fields are allowed in the same schema version if they do not break existing consumers.
- Breaking payload or event-set changes require a new schema version and, if needed, a new API major version.

### 3) Change control requirements

Every contract change requires:
- Compatibility classification (`additive` or `breaking`).
- Changelog entry.
- Updated generated schemas/OpenAPI artifacts.
- Updated contract tests (REST/WS/error envelope).

## Consequences

Positive:
- Predictable compatibility behavior across clients.
- Safer additive evolution in early phases.
- Clear trigger for introducing `/api/v2`.

Tradeoffs:
- Requires strict governance and review discipline.
- Some desired changes must wait for a major-version boundary.

## Out of Scope

- Deprecation window policy details.
- Backward-compatibility shims between major versions.
