# Phase 4 Notebook Workflow

## Goal
Support notebook-first analysis where notebook code drives scenario/product/layer changes through FastAPI contracts only, while map clients react through existing REST + WebSocket state/event flows.

## Workflow Contract
1. Notebook starts a session:
   - `POST /api/v1/notebook/sessions`
   - Receives `api_token`.
2. Notebook mutates state via FastAPI:
   - `POST /api/v1/scenarios`
   - `POST /api/v1/scenarios/{scenario_id}/imports/geotiff` or `POST /api/v1/products`
   - `POST /api/v1/layers`
3. Map clients update:
   - Pull current state via `GET /api/v1/scenarios/{scenario_id}/layers`
   - Subscribe to `WS /api/v1/events` or `WS /api/v1/notebook/events`.
4. No direct DB writes from notebook code:
   - `scenario.db` is mutated only by FastAPI services.
   - Notebook client remains a REST/WS consumer.

## Scenario Workspace -> Marimo Launch Flow
1. In `/lunar_analyst/`, select a scenario in Scenario Explorer.
2. Click `Open in Marimo`.
3. Frontend calls `POST /api/v1/marimo/launch` with:
   - `scenario_id=<active scenario id>`
   - `restart_if_running=true`
4. Backend launch behavior:
   - resolves `cwd` from selected scenario directory (catalog-backed path only),
   - returns running status if already running in that same directory,
   - returns `409` on cwd mismatch unless restart is requested,
   - rejects scenario-scoped relaunch while in attach mode (`409`).
5. Frontend opens returned `base_url`; if popup is blocked, UI shows fallback link.

## Map Visibility Notes (Current `/lunar_analyst/` Behavior)
- The `/lunar_analyst/` page initializes into the configured lunar-analyst scenario on load.
- The map and layer panel render layers for the currently active scenario only.
- If notebook code creates/imports/layers into another scenario (for example `scn_marimo_demo`), those layers are visible after selecting that scenario in the Scenario Explorer.
- If the scenario was created after the page was opened, reload `/lunar_analyst/` first so the scenario appears in the explorer catalog.

## Session/Auth Policy
- Optional token enforcement is controlled by:
  - env: `LUNAR_ANALYST_REQUIRE_SESSION_TOKEN`
  - config: `[backend.notebook].require_session_token`
- When enabled, mutation endpoints require header `x-lunar-session-token`.
- Notebook WebSocket endpoint (`/api/v1/notebook/events`) always requires a valid token.

## Marimo Process Integration
- Launch or attach control:
  - `POST /api/v1/marimo/launch`
  - `GET /api/v1/marimo/status`
  - `POST /api/v1/marimo/stop`
- Modes:
  - `attach`: connect to externally managed Marimo URL.
  - `launch`: FastAPI-managed subprocess.
- Launch env for FastAPI-managed subprocess:
  - prepends `<repo_root>` and `<repo_root>/moonlayers_pkg` to `PYTHONPATH`,
  - appends existing `PYTHONPATH` afterwards.

## Notebook Helper
- `backend/notebook/client.py` provides `NotebookClient`:
  - opens session
  - performs REST calls with session token
  - subscribes to notebook events over WebSocket
  - supports `import_geotiff_create_layer_and_zoom(...)` helper for one-call roundtrip:
    - import/register output
    - create map layer
    - request map viewport fit for the new file

## Notebook-Triggered Map Zoom Contract
- Endpoint: `POST /api/v1/scenarios/{scenario_id}/map-commands/zoom-to-file`
- Request:
  - `file_id` (required)
  - `padding_px` (optional)
  - `max_zoom` (optional)
- Response:
  - `{ "status": "queued", "event": "map_zoom_requested" }`
- WS event emitted on `/api/v1/events`:
  - `event="map_zoom_requested"`
  - payload data includes `scenario_id`, `file_id`, `extent`, `padding_px`, optional `max_zoom`.
- Frontend handling:
  - applies zoom only when event `scenario_id` equals the active scenario.

