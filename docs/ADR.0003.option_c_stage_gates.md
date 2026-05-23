# ADR 0003: Adopt Option C API Contract Strategy and Stage Gates

- Status: Accepted
- Date: 2026-02-14
- Deciders: Lunar Analyst architecture/design owners
- Related: `docs/API_CONTRACT.md`, `docs/PLAN.md`, `docs/ADR.0001.process_model.md`, `docs/ADR.0002.scenario_filesystem_and_catalog.md`

## Context

Lunar Analyst has multiple API clients (browser, Marimo, automation). A contract strategy must protect integration stability without blocking early delivery. `docs/API_CONTRACT.md` recommends Option C (staged contract), but the plan item requires explicit adoption and clear stage gate criteria.

## Decision

Adopt **Option C: staged contract** as project policy.

- Stage 1 defines and freezes the minimum viable public contract in `/api/v1`.
- Stage 2 expands contract surface additively only, after Stage 1 exit gates are met.

## Stage Definitions

### Stage 1 (Core Contract Freeze)

Scope:
- Resource schemas: `Scenario`, `Product`, `LayerState`, `Job`, `JobEvent`
- Error envelope: `code`, `message`, `details`, `request_id`
- WS event envelope + event names:
  - `job_queued`
  - `job_started`
  - `job_progress`
  - `job_completed`
  - `job_failed`
  - `job_cancelled`
  - `layer_added`
  - `layer_updated`
  - `layer_removed`
- Versioning rule: additive-only in `/api/v1`; breaking changes require `/api/v2`

Policy:
- No undeclared required fields.
- No silent field renames/removals.
- Any Stage 1 contract change requires schema artifact update + changelog + contract tests.

### Stage 2 (Additive Expansion)

Scope examples:
- Optional resource fields
- Richer styling controls
- Batch layer operations
- Advanced filtering/query APIs
- Notebook convenience helpers

Policy:
- Stage 2 additions remain backward-compatible within `/api/v1`.
- New required semantics in existing endpoints are treated as breaking and must wait for `/api/v2`.

## Stage Gate Criteria

### Gate G0: Enter Stage 1 (Design Gate)

Required:
- Process boundaries and scenario storage decisions are ratified (ADR 0001, ADR 0002).
- Canonical schema locations are declared (OpenAPI + JSON schema files).
- Contract owner(s) and change-control rule are documented.

### Gate G1: Stage 1 Freeze Complete (Spec Gate)

Required:
- Stage 1 resource schemas, error envelope, and WS event set are declared for `/api/v1`.
- Versioning policy is documented and linked from plan/docs.
- Field semantics for IDs/timestamps/status enums are explicit.

### Gate G2: Stage 1 Implementation Complete (Runtime Gate)

Required:
- FastAPI handlers/serializers conform to Stage 1 schemas.
- WS publisher emits only Stage 1 event names with the Stage 1 envelope.
- Error responses use the Stage 1 error envelope consistently.

### Gate G3: Exit Stage 1 / Enter Stage 2 (Quality Gate)

Required:
- Contract tests pass in CI:
  - OpenAPI schema checks
  - WS event schema checks
  - Error envelope checks
- End-to-end "first working loop" is demonstrated:
  - create scenario
  - launch job
  - receive progress/completion events
  - register/render resulting layer
- No unresolved P0/P1 contract regressions.

## Consequences

Positive:
- Preserves delivery speed while preventing client drift.
- Creates objective transition criteria from early build-out to expansion.
- Keeps Marimo/browser/automation aligned on one public contract.

Tradeoffs:
- Requires strict discipline for schema governance and CI checks.
- Stage transitions require explicit verification artifacts.

## Out of Scope

- Full schema payload definitions (handled by Stage 1 schema-freeze tasks).
- Endpoint-by-endpoint implementation details (handled in backend tasks).
