# Contracts Changelog

## 2026-04-05

- Classification: breaking (pre-release assistant contract rename)
- Summary:
  - Renamed assistant execution-plan lifecycle WS events from `turn_planner_*` to `turn_execution_plan_*`.
  - Renamed assistant metadata key from `planner_segments` to `execution_plan_segments`.
  - Renamed assistant validation error codes from `turn_planner_invalid_*` to `turn_execution_plan_invalid_*`.
  - Bumped `AssistantWsEnvelope.schema_version` from `1.0` to `1.1`.
- Artifacts:
  - `docs/contracts/generated/v1/assistant_ws_event_envelope.schema.json`
  - `backend/contracts/assistant_events.py`

## 2026-02-14

- Classification: additive
- Summary:
  - Established canonical contract artifact locations in `docs/contracts/README.md`.
  - Standardized local generation workflow for OpenAPI and JSON Schema exports.
  - Documented requirement that every contract change records compatibility class and touched artifacts.
- Artifacts:
  - `docs/contracts/README.md`
  - `docs/contracts/generated/v1/openapi.json`
  - `docs/contracts/generated/v1/*.schema.json`

- Classification: additive
- Summary:
  - Added typed `generate_hillshade` job contract from `JobHandlers`.
  - Added generated REST route `/api/v1/jobs/generate-hillshade` (via runtime adapter).
  - Added `GenerateHillshadeResult` JSON schema export target.
- Artifacts:
  - `docs/contracts/generated/v1/openapi.json`
  - `docs/contracts/generated/v1/generate_hillshade_result.schema.json`

## 2026-02-15

- Classification: breaking (pre-release prototype endpoint refinement)
- Summary:
  - Updated typed `generate_horizons` job contract to align with `MoonlibBridge.GenerateHorizons(scenarioRootDir, demPath, horizonsDir, overwriteHorizons, compressHorizons)`.
  - `POST /api/v1/jobs/generate-horizons` request body now uses explicit filesystem parameters and horizon options.
  - `GenerateHorizonsResult` now returns filesystem-oriented fields for the prototype worker flow.
- Artifacts:
  - `docs/contracts/generated/v1/openapi.json`
  - `docs/contracts/generated/v1/generate_horizons_result.schema.json`

## 2026-02-16

- Classification: additive
- Summary:
  - Added API route `GET /api/v1/products/{product_id}/files` to support client-side layer creation from product artifacts.
  - Added API route `GET /api/v1/jobs/handlers` to expose generated job-route metadata for dynamic web job launch UIs.
  - Added API route `POST /api/v1/lunar-analyst/bootstrap` to seed scenario/product/layer state for API-backed map hydration.
  - Added `ProductFile` response model for product file listing.
- Artifacts:
  - `docs/contracts/generated/v1/openapi.json`

## 2026-02-17

- Classification: additive
- Summary:
  - Added path-first explorer endpoint: `GET /api/v1/scenarios/{scenario_id}/explorer-nodes` with optional `include_hidden` flag.
  - Added path move/rename endpoint: `POST /api/v1/scenarios/{scenario_id}/paths:move`.
  - Added additive contract models: `ExplorerNode`, `ExplorerNodeType`, `MoveScenarioPathRequest`, and `MoveScenarioPathResponse`.
- Artifacts:
  - `docs/contracts/generated/v1/openapi.json`

## 2026-02-21

- Classification: additive
- Summary:
  - Extended `MarimoLaunchRequest` with optional `scenario_id` and `restart_if_running`.
  - Added map command endpoint `POST /api/v1/scenarios/{scenario_id}/map-commands/zoom-to-file`.
  - Added additive WS event `map_zoom_requested` for scenario-scoped viewport fit requests.
  - Added additive contract models `ZoomToFileMapCommandRequest` and `MapCommandQueuedResponse`.
- Artifacts:
  - `docs/contracts/generated/v1/openapi.json`
  - `docs/contracts/generated/v1/job_event.schema.json`
  - `docs/contracts/generated/v1/ws_event_envelope.schema.json`

- Classification: additive
- Summary:
  - Added notebook session endpoints: `POST /api/v1/notebook/sessions`, `GET /api/v1/notebook/sessions/{session_id}`.
  - Added Marimo runtime endpoints: `POST /api/v1/marimo/launch`, `GET /api/v1/marimo/status`, `POST /api/v1/marimo/stop`.
  - Added notebook WS endpoint: `WS /api/v1/notebook/events` (token-gated).
  - Added response/request models for notebook session and Marimo runtime contracts.
- Artifacts:
  - `docs/contracts/generated/v1/openapi.json`
