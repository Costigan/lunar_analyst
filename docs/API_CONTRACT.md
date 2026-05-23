# API Contract Discussion for Lunar Analyst

## Why this topic exists
The plan references interactions like `add_layer`, job submission, notebook-triggered processing, and live map updates. Without a clear API contract, each client (React app, Marimo notebooks, automation scripts) can implement slightly different assumptions, which causes integration drift.

A contract does not mean overdesign. It means agreeing on:
- what resources exist
- payload shapes
- event names
- error semantics
- versioning behavior

## Options

### Option A: Loose, ad-hoc endpoints
Pros:
- Fast to start.
- Minimal upfront documentation.

Cons:
- Drift between clients is likely.
- Breaking changes are frequent.
- Harder to test reliably.
- Event payload inconsistencies are common.

### Option B: Strict, fully specified contract early
Pros:
- Stable integration across UI + Marimo + automation.
- Easier contract testing.
- Better long-term maintainability.

Cons:
- Higher initial design effort.
- Risk of specifying fields too early.

### Option C: Staged contract (recommended)
Pros:
- Keeps early momentum while preventing drift.
- Defines only stable core resources now.
- Allows additive growth with versioning.

Cons:
- Requires discipline around change control.

## Recommended approach: Staged contract

Adoption status: accepted in docs/ADR.0003.option_c_stage_gates.md.
Stage 1 schema freeze artifacts: `docs/contracts/openapi.v1.stage1.yaml` and `docs/contracts/schemas/v1/*.schema.json`.


### Stage 1 (define now)
Define versioned schemas for:
- `Scenario`
- `Product`
- `LayerState`
- `Job`
- `JobEvent`

Define event names now:
- `job_queued`
- `job_started`
- `job_progress`
- `job_completed`
- `job_failed`
- `job_cancelled`
- `layer_added`
- `layer_updated`
- `layer_removed`

Stage 1 WS envelope freeze artifacts:
- `backend/contracts/events.py` (`WsEnvelope`, `STAGE1_WS_EVENT_NAMES`)
- `docs/contracts/generated/v1/ws_event_envelope.schema.json` (from export script)

Define error envelope now:
- `code`
- `message`
- `details`
- `request_id`

### Stage 2 (add after first working loop)
Add optional fields and advanced endpoints:
- richer styling controls
- batch layer operations
- advanced filtering/query APIs
- notebook convenience helpers

## Practical contract shape (example)

### Job create request
```json
{
  "scenario_id": "scn_001",
  "job_type": "generate_horizons",
  "mode": "queued",
  "params": {
    "dem_product_id": "prd_dem_01",
    "azimuth_step_deg": 0.25
  }
}
```

### Job status response
```json
{
  "job_id": "job_123",
  "status": "running",
  "progress": {
    "percent": 42.5,
    "stage": "raycast",
    "message": "Processing tile 51/120"
  },
  "started_at": "2026-02-14T20:00:00Z",
  "updated_at": "2026-02-14T20:01:35Z"
}
```

### Event payload (WebSocket)
```json
{
  "event": "job_progress",
  "job_id": "job_123",
  "scenario_id": "scn_001",
  "timestamp": "2026-02-14T20:01:35Z",
  "data": {
    "percent": 42.5,
    "stage": "raycast"
  }
}
```

## Versioning policy
- Use explicit API versioning in path: `/api/v1/...`.
- Only additive changes within a version.
- Breaking changes require `/api/v2/...`.
- WS events include `schema_version` if payload evolves.
- Policy status: accepted in `docs/ADR.0004.versioning_policy.md`.

## Contract governance
- Canonical published contract artifacts:
  - `docs/contracts/generated/v1/openapi.json`
  - `docs/contracts/generated/v1/*.schema.json`
- Canonical location declaration: `docs/contracts/README.md`
- Add CI contract tests to validate:
  - response payload shape
  - required fields
  - WS event payload structure
- Require changelog entries for schema changes in `docs/contracts/CHANGELOG.md`.

## Specific guidance for FastAPI <-> Marimo
- Marimo must use the same public contract as browser clients.
- Avoid private in-memory shortcuts between FastAPI and Marimo.
- Benefits:
  - replayable workflows
  - easier debugging
  - less coupling

## Signature-First Job Contracts
- Job kinds should be defined as typed Python handler signatures (for example, `generate_horizons(...)`).
- FastAPI job submission endpoints can be generated at startup from discovered handler signatures.
- Generated endpoint wrappers should be cached after startup and reused per request.
- Orchestration remains in the job service layer; compute remains in handler implementations.
- Strong rule: implement actual calculations in `backend/jobs/handlers.py` method bodies (for example, `JobHandlers.generate_hillshade`). Do not split equivalent compute contracts into separate parallel layers that duplicate handler definitions.

## Summary
The API contract issue is fundamentally about controlling integration risk across multiple clients and processes. A staged, versioned contract gives you speed now and stability later, without freezing design too early.
