# Plan: Lunar Analyst Python Implementation (Codex Revision)

## 1. Confirmed Decisions
- First production release is **Windows 11 only**.
- Runtime baseline is **Python 3.11 + .NET 10.0**.
- FastAPI and Marimo run as **separate processes**.
- `new_horizon` compute is hosted in a **single .NET compute process** (internally multithreaded with TPL).
- **SpatiaLite** is the project database.
- **GeoTIFF** and **GeoPackage** are import/export formats.
- Analysis is **scenario-based**.
- Feature parity with the .NET desktop prototype is a reference goal, not a hard gate.
- Tauri production packaging runs Python as a sidecar process.
- Developer workflow must support running FastAPI directly in VS Code debugger (without Tauri) using the same `/api/v1` contracts.

## 2. Product Architecture

### 2.1 Process Topology
1. **FastAPI Service (authoritative control plane)**
- Owns scenario/project state.
- Exposes APIs for scenarios, products, jobs, and map-layer state.
- Serves local assets (COGs/GeoJSON/metadata) through allowlisted project roots.
- Publishes status/events over WebSocket.

2. **Compute Worker Process (Python process hosting pythonnet + .NET runtime)**
- Loads `moonlib.dll` and dependencies.
- Executes CPU/GPU-heavy tasks (horizons, lightmaps, map-algebra operations).
- Supports queued jobs and immediate async jobs.
- Reports progress, logs, and cancellation checkpoints back to FastAPI.

3. **Marimo Process**
- Runs notebooks and exploratory analysis.
- Calls FastAPI APIs (same contract as browser client).
- Subscribes to WebSocket events for live job/layer updates.
- Never mutates shared state directly in memory.

4. **Browser/Tauri Client (React + OpenLayers)**
- Reads scenario/layer state from FastAPI.
- Triggers jobs and responds to events.
- Renders COGs/vectors through OpenLayers.

### 2.2 FastAPI <-> Marimo Communication
- **Transport:** localhost HTTP + WebSocket.
- **Pattern:** Marimo is a client; FastAPI is source of truth.
- **REST use:** create/update scenarios, register outputs, start/cancel jobs.
- **WebSocket use (Stage 1 contract):** `job_queued`, `job_started`, `job_progress`, `job_completed`, `job_failed`, `job_cancelled`, `layer_added`, `layer_updated`, `layer_removed`.
- **Identity:** per-session API key/token issued by FastAPI launcher.
- **Failure behavior:** Marimo disconnect does not stop jobs; FastAPI and worker continue.
- **Map-driving rule:** notebook Python drives map state indirectly by calling FastAPI contracts; map clients react via REST pulls and WS layer/job events.
- **CLR hosting rule:** only the compute worker process loads .NET/moonlib.dll; notebook and web client processes do not host CLR.
- **Worker buffer pattern:** worker may allocate numpy arrays, pass them to moonlib/C# to fill, then run additional Python (numpy/numba) post-processing.
- **Large-data exchange rule:** large moonlib-generated results are shared via filesystem artifacts plus metadata registration (not large REST payloads).
- **Analysis-first artifacts:** persist analysis-grade outputs (for example GeoTIFF/COG, Zarr, Parquet/GeoPackage as applicable) so Marimo notebooks can consume them efficiently and reproducibly.

## 3. Scenario Data Model

### 3.1 Core Concept
A **Scenario** is a self-contained analysis workspace with one primary DEM (`ElevationMap`) defining:
- CRS
- rectangular bounds
- pixel resolution
- canonical analysis grid

All derived/imported products for that scenario are registered in SpatiaLite and stored under that scenario directory.

### 3.2 Python `ElevationMap` Class (required)
Planned fields:
- `scenario_id`
- `path`
- `crs_wkt` (or proj string)
- `width`, `height`
- `pixel_size_x`, `pixel_size_y`
- `bounds_minx`, `bounds_miny`, `bounds_maxx`, `bounds_maxy`
- `nodata`
- `dtype`
- `band_count`
- `checksum` (or hash)

Planned methods:
- `validate_against_file()`
- `to_grid_spec()`
- `is_aligned(other_product)`

### 3.3 Scenario Directory Layout (initial)
```text
scenario_root/
  scenario.db                      # SpatiaLite
  dem.tif
  hillshade.tif
  <product-or-artifact files...>
  <time-series-name>/
    <time-series files...>
```

Note: product typing and lineage are authoritative in scenario DB metadata. Filesystem placement is intentionally shallow/free-form except for reserved filenames and explicitly managed directories.

## 4. SpatiaLite Project Database Plan

### 4.1 Initial Tables
- `scenarios`
- `products`
- `product_files`
- `jobs`
- `job_events`
- `layer_state`
- `imports`
- `exports`

### 4.2 Product Metadata (minimum)
- `product_id`, `scenario_id`, `kind`, `subkind`, `created_at`
- `crs`, `bounds`, `resolution`, `nodata`, `dtype`
- `producer` (`import`, `python_pipeline`, `new_horizon`, `manual`)
- `lineage` (JSON: input product IDs + parameters hash)
- one-to-many file records (supports multi-file products like time series)

### 4.3 File Safety Rules
- Store relative paths anchored to scenario root.
- FastAPI serves files only by registered file ID, not raw absolute path.
- Reject path traversal and out-of-root requests.

## 5. Compute Execution Model

### 5.1 Two Async Job Modes
1. **Queued async jobs**
- Submitted to durable queue.
- Worker executes according to concurrency limits.
- Best for long, heavy, or batch workloads.

2. **Immediate async jobs**
- Start right away in parallel with queue processing.
- Intended for interactive notebook/UI actions.
- Controlled by separate concurrency cap.

Both modes support:
- progress updates (percent + stage + message)
- cancellation
- structured logs
- terminal states: `completed`, `failed`, `cancelled`

### 5.2 Recommended API Surface (first pass)
- `POST /jobs` (queued)
- `POST /jobs/immediate` (start now)
- `POST /jobs/{id}/cancel`
- `GET /jobs/{id}`
- `GET /jobs/{id}/events`
- `WS /events`

### 5.3 Worker Isolation
- Keep FastAPI and worker in separate processes.
- If worker crashes, FastAPI marks active jobs failed/recoverable and can restart worker.
- Add `/health/native` probe that validates .NET load + tiny compute call.

## 6. Implementation Phases (reordered)

### Phase 0: Decisions and Contracts
- [x] Finalize process model ADR (FastAPI, Marimo, worker boundaries): `docs/ADR.0001.process_model.md`.
- [x] Finalize scenario filesystem conventions: `docs/ADR.0002.scenario_filesystem_and_catalog.md`.
- [x] Adopt Option C staged contract strategy and document stage gate criteria: `docs/ADR.0003.option_c_stage_gates.md`.
- [x] Freeze Stage 1 schemas in `/api/v1` for `Scenario`, `Product`, `LayerState`, `Job`, and `JobEvent`: `docs/contracts/openapi.v1.stage1.yaml`, `docs/contracts/schemas/v1/*.schema.json`.
- [x] Freeze Stage 1 error envelope (`code`, `message`, `details`, `request_id`) and apply it to all API error responses: `backend/contracts/models.py`, `backend/api/app.py`.
- [x] Freeze Stage 1 WS event envelope and exact Stage 1 event name set for jobs/layers: `backend/contracts/events.py`, `backend/contracts/models.py` (`JobEventName`).
- [x] Define versioning policy: additive-only changes in `v1`; breaking changes require `/api/v2`: `docs/ADR.0004.versioning_policy.md`.
- [x] Define Phase 0 contract tests (OpenAPI schema checks + WS event schema checks + error envelope checks) and run locally: `backend/tests/contract/`, `backend/README.md` (CI deferred to Phase 2 gates).
- [x] Declare canonical schema locations (OpenAPI + JSON schema files) and require changelog entries for schema changes: `docs/contracts/README.md`, `docs/contracts/CHANGELOG.md`.
- [x] AI protocol checkpoint: prompt contract used, file scope declared, and DoD evidence captured: `docs/PHASE0_AI_PROTOCOL_CHECKPOINT.md`.

### Phase 0.5: Legacy Asset Inventory and Migration Plan
- [x] Inventory existing code/assets from legacy repos (`lunar_analyst`, `lunar_analyst_net`, `new_horizon`): `docs/PHASE0_5_LEGACY_INVENTORY.md`.
- [x] Classify each component: `reuse-as-is`, `port-to-python`, `wrap-via-pythonnet`, `retire`: `docs/PHASE0_5_COMPONENT_CLASSIFICATION.md`.
- [x] Skipped (deferred by user): identify existing DEM/lighting/vector sample datasets and expected outputs to use as regression fixtures. Test data will be supplied later; proceed with simple `JobHandlers` methods that require minimal/no data.
- [x] Document data/model differences and required adapters: `docs/PHASE0_5_DATA_MODEL_ADAPTERS.md`.
- [x] Define acceptance matrix mapping legacy capabilities to new implementation phases: `docs/PHASE0_5_ACCEPTANCE_MATRIX.md`.
- [x] Add tests that assert fixture discovery and baseline metadata validity: `backend/tests/contract/test_fixture_discovery.py`, `backend/tools/fixture_discovery.py`, `backend/contracts/fixtures.py`.
- [x] AI protocol checkpoint: migration prompts include legacy source references, acceptance criteria, and required regression tests: `docs/PHASE0_5_AI_PROTOCOL_CHECKPOINT.md`.

### Phase 0.9: Browser Map Milestone (Fast Track)
- Note: this phase intentionally duplicates some later-phase scope to deliver a visible browser milestone quickly.
- UI controls decision record: `docs/ADR.0005.web_ui_component_toolkit.md` (Shoelace 2 now; migration path to Web Awesome later).
- Raster styling decision record: `docs/ADR.0007.client_side_raster_styling_and_colormaps.md` (OpenLayers client-side expressions/style variables now; custom shaders as deferred extension path).
- [x] Stand up minimal browser map shell (React + OpenLayers) with lunar south-pole projection configuration.
- [x] Add Moon Trek tiled base layer (same class of tiled source used by desktop map workflows).
- [x] Add a minimal backend endpoint/path that serves one known hillshade GeoTIFF with range-capable file serving.
- [x] Render that hillshade as an overlay layer in browser (`WebGLTile`/GeoTIFF path) with correct extent/CRS placement.
- [x] Implement base/overlay ordering and hillshade opacity control.
- [x] Add startup wiring with one configured hillshade path so map opens directly to visible base + overlay.
- [x] Implement map milestone server-side raster delivery for non-native CRS inputs: warp map-facing hillshade to `ESRI:103878`, persist display derivative under scenario product display path, and keep native passthrough endpoint. (`backend/services/raster_delivery.py`, `backend/api/routers/lunar_analyst.py`)
- [x] Improve map milestone raster rendering reliability: explicit single-band grayscale style + nodata transparency in OpenLayers; atomic derivative writes with per-output locking; internal overview pyramid generation for warped outputs. (`backend/web/lunar_analyst/src/App.tsx`, `backend/services/raster_delivery.py`)
- [x] Add layer control panel primitives using Shoelace (`sl-switch`, `sl-range`) in map milestone UI. (`backend/web/lunar_analyst/index.html`, `backend/web/lunar_analyst/src/styles/app.css`)
- [x] Wire visibility and opacity controls to both tiled base and GeoTIFF overlay layer properties. (`backend/web/lunar_analyst/src/App.tsx`)
- [x] Wire brightness and contrast controls to OpenLayers `WebGLTile` style variables and ensure live update behavior. (`backend/web/lunar_analyst/src/App.tsx`)
- [x] Add standard colormap selector (grayscale + scientific defaults) for raster layers and wire to `WebGLTile` style expressions. (`backend/web/lunar_analyst/src/App.tsx`, `backend/api/routers/lunar_analyst.py`)
- [x] Add custom colormap loading from app-level + scenario-level JSON definitions with validation/fallback behavior. (`backend/api/routers/lunar_analyst.py`, `backend/web/lunar_analyst/src/App.tsx`, `config/colormaps/map_colormaps.json`)
- [x] Refine layer manager density/layout for map-first use: panel on left, smaller typography/spacing, per-layer collapsible groups with always-visible visibility toggles, and panel-level collapse by clicking `Layer Manager`. (`backend/web/lunar_analyst/index.html`, `backend/web/lunar_analyst/src/styles/app.css`, `backend/web/lunar_analyst/src/App.tsx`)
- [x] Reposition OpenLayers zoom controls to upper-right for better separation from layer controls. (`backend/web/lunar_analyst/src/styles/app.css`)
- [x] Add visible map client version marker in title bar and console for frontend cache/debug verification. (`backend/web/lunar_analyst/index.html`, `backend/web/lunar_analyst/src/App.tsx`, `backend/web/lunar_analyst/src/styles/app.css`)
- [x] Add lower-left scale bar control and keep current placement/spacing as accepted baseline; further visual refinement deferred. (`backend/web/lunar_analyst/src/App.tsx`, `backend/web/lunar_analyst/src/styles/app.css`)
- [x] Validate manually: control state changes are reflected immediately and persist through pan/zoom redraw.
- [x] Validate milestone manually: browser shows Moon Trek base plus aligned hillshade overlay with pan/zoom.

### Phase 1: Native Bridge + Artifacts
- Implementation guardrail: actual compute logic must be implemented in `backend/jobs/handlers.py` method bodies. `JobHandlers` signatures are the single source of truth for job contracts and generated job routes; do not create parallel duplicate compute-contract layers.
- [x] Implement pythonnet bootstrap for .NET 10 + `moonlib`: `backend/worker/native_bootstrap.py`, `backend/tests/worker/test_native_bootstrap.py`.
- [x] Build worker prototype that runs one job end-to-end (hillshade): generate a hillshade GeoTIFF from a DEM GeoTIFF via moonlib.MoonlibBridge.GenerateHillshade(demPath, hillshadePath). (`backend/jobs/handlers.py`, `backend/api/dependencies.py`, `backend/tests/worker/test_hillshade_job_flow.py`)
- [x] Build worker prototype that runs one horizon job end-to-end via moonlib.MoonlibBridge.GenerateHorizons(scenarioRootDir, demPath, horizonsDir, overwriteHorizons, compressHorizons). (`backend/jobs/handlers.py`, `backend/tests/worker/test_hillshade_job_flow.py`)
- [x] Define artifact outputs and metadata capture in SpatiaLite: `backend/services/artifact_catalog.py`, `backend/jobs/handlers.py`, `backend/tests/worker/test_hillshade_job_flow.py`.
- [x] Add `/health/native` and startup diagnostics: `backend/api/routers/v1.py`, `backend/api/app.py`, `backend/tests/integration/test_native_health_and_cancellation.py`.
- [x] Add integration tests for native load, one-job execution, progress events, and cancellation: `backend/tests/worker/test_hillshade_job_flow.py`, `backend/tests/integration/test_moonlib_bridge_real.py`, `backend/tests/integration/test_native_health_and_cancellation.py`.
- [x] AI protocol checkpoint: high-risk change policy applied and rollback notes recorded: `docs/PHASE1_AI_PROTOCOL_CHECKPOINT.md`.

### Phase 2: Backend Core
- [x] FastAPI scaffold: scenarios, products, files, jobs. (`backend/api/routers/v1.py`, `backend/api/dependencies.py`)
- [x] Replace deprecated FastAPI `@app.on_event(...)` startup/shutdown hooks with lifespan handlers and update affected tests to remove deprecation warnings. (`backend/api/app.py`)
- [x] On import, convert GeoTIFF inputs to COG (Cloud Optimized GeoTIFF) by policy unless explicitly bypassed for diagnostics. (`backend/services/cog.py`, `backend/api/dependencies.py`, `backend/api/routers/v1.py`)
- [x] Implement ADR 0006 raster delivery policy: for OpenLayers map clients, serve rasters warped to `ESRI:103878`; keep non-map Marimo image presentation in native/source CRS. (`backend/api/routers/lunar_analyst.py`, `backend/services/raster_delivery.py`)
- [x] Define and enforce storage convention for warped map-display derivatives in scenario filesystem with metadata lineage registration (location policy is free-form and not kind-folder-dependent). (`backend/services/raster_delivery.py`)
- [x] Implement backend warp pipeline (GDAL-based) that creates `ESRI:103878` display derivatives from non-`ESRI:103878` source rasters with explicit nodata/resampling settings. (`backend/services/raster_delivery.py`)
- [x] Register warped derivatives in scenario metadata with lineage fields (`source_product_id`/`source_file_id`, `target_crs`, `warp_params_hash`, `created_at_utc`) and mark derivative role as `display_map`. (`backend/services/raster_delivery.py`)
- [x] Update map-facing raster endpoints/layer-state resolution to return warped derivative file IDs/paths for OpenLayers clients only. (`backend/api/routers/lunar_analyst.py`)
- [x] Keep notebook non-map image endpoints/helpers bound to native/source raster artifacts by default (no implicit CRS mutation). (`backend/api/routers/lunar_analyst.py` -> `/hillshade/native`)
- [x] Add integration tests proving: map raster delivery returns `ESRI:103878` derivatives when source CRS differs; non-map notebook image delivery returns native/source artifacts. (`backend/tests/contract/test_map_milestone.py`)
- [x] SpatiaLite schema + migrations. (`backend/services/migrations.py`, `backend/tests/contract/test_migrations.py`)
- [x] Asset serving by file ID + range support. (`backend/api/routers/v1.py` -> `GET /api/v1/files/{file_id}`, `backend/tests/contract/test_phase2_backend_core.py`)
- [x] WebSocket event stream. (`backend/api/routers/v1.py` -> `WS /api/v1/events`, `backend/api/dependencies.py`)
- [x] Implement Stage 1 contract exactly in handlers/serializers (no undeclared required fields). (`backend/contracts/models.py` strict models, `backend/tests/contract/test_error_envelope.py`, `backend/tests/contract/test_phase2_backend_core.py`)
- [x] Add API contract tests (REST + WS + error envelope) and DB migration tests as local gates. (`backend/tests/contract/test_phase2_backend_core.py`, `backend/tests/contract/test_migrations.py`, `local_check_backend_contracts.ps1`)
- [x] Add file safety tests (path traversal rejection, out-of-root rejection, file-ID-only serving). (`backend/tests/contract/test_phase2_backend_core.py`)
- [x] AI protocol checkpoint: schema/API prompts include compatibility notes and human approval trail. (`docs/PHASE2_AI_PROTOCOL_CHECKPOINT.md`)

### Phase 3: Minimal Web Client
- [x] OpenLayers map shell with milestone raster rendering is in place (`WebGLTile` + GeoTIFF baseline). (`backend/web/lunar_analyst/src/App.tsx`)
- [x] Complete generalized map loading for registered scenario layers: hydrate from product/layer metadata, support raster + GeoJSON vector overlays, and honor persisted z-order. (`backend/web/lunar_analyst/src/App.tsx`)
- [x] Layer list backed by `layer_state` API. (`backend/web/lunar_analyst/src/App.tsx`, `backend/api/routers/v1.py`)
- [x] Implement client hydrator for `GET /api/v1/scenarios/{scenario_id}/layers` to build the layer stack from persisted `LayerState` entries. (`backend/web/lunar_analyst/src/App.tsx`)
- [x] Wire layer edits in UI to `POST/PATCH/DELETE /api/v1/layers` and reconcile optimistic updates with WS events. (`backend/web/lunar_analyst/src/App.tsx`)
- [x] Implement ADR 0007 raster styling baseline using OpenLayers expression/style-variable pipeline (colormaps + opacity/brightness/contrast) for the current map milestone UI. (`backend/web/lunar_analyst/src/App.tsx`, `backend/api/routers/lunar_analyst.py`)
- [x] Extend ADR 0007 styling from milestone-only controls to API-backed scenario layer entries (per-layer persisted style state). (`backend/contracts/models.py`, `backend/web/lunar_analyst/src/App.tsx`)
- [x] Define deferred custom-shader extension contract for advanced raster rendering (GLSL path, activation rules, fallback behavior). (`docs/ADR.0008.raster_shader_extension_contract.md`)
- [x] Add `docs/ADR.0008.raster_shader_extension_contract.md` with shader capability flags, activation policy, and required fallback-to-expression behavior.
- [x] Job launch + progress display + cancellation UI. (`backend/web/lunar_analyst/index.html`, `backend/web/lunar_analyst/src/App.tsx`)
- [x] Add job panel wired to generated job routes: launch selected handler, stream `job_*` events from `/api/v1/events`, expose cancel via `POST /api/v1/jobs/{job_id}/cancel`. (`backend/api/routers/v1.py`, `backend/web/lunar_analyst/src/App.tsx`)
- [x] Add end-to-end smoke tests: create scenario -> launch job -> observe progress -> layer appears. (`backend/tests/contract/test_phase3_minimal_web_client.py`)
- [x] Add browser-level E2E test flow covering scenario creation, layer hydration, job progress updates, completion, and resulting layer visibility. (implemented as backend-driven smoke contract pending future browser automation: `backend/tests/contract/test_phase3_minimal_web_client.py`)
- [x] AI protocol checkpoint: UI tasks include explicit acceptance criteria and E2E verification evidence. (`docs/PHASE3_AI_PROTOCOL_CHECKPOINT.md`)
- [x] Add `docs/PHASE3_AI_PROTOCOL_CHECKPOINT.md` with test evidence and manual checklist links.

### Phase 3.1: Scenario Workspace UI (Explorer + Map Integration)
- [x] UX design spec for combined scenario workspace view: Scenario Explorer + OpenLayers map + Layer Manager in one responsive layout. (`docs/PHASE3_1_UX_DESIGN_SPEC.md`)
- [x] Define interaction contracts for scenario selection and contextual scoping: selecting a scenario filters visible products, tools, and map layers to that scenario. (`docs/PHASE3_1_WORKSPACE_CONTRACTS.md`)
- [x] Define Scenario Explorer tree-grid schema and columns (`Name`, `Type`, `Created`, `Size`, `Notes`) with column-visibility behavior. (`docs/PHASE3_1_WORKSPACE_CONTRACTS.md`, `backend/web/lunar_analyst/index.html`, `backend/web/lunar_analyst/src/App.tsx`)
- [x] Define map integration interactions: click-select by scenario footprint, Explorer-to-map drag/drop, and Explorer-to-layer-manager insertion position behavior. (`docs/PHASE3_1_WORKSPACE_CONTRACTS.md`, `backend/web/lunar_analyst/src/App.tsx`)
- [x] Define API data-loading sequence for combined interface: scenario catalog/footprints, scenario product list, layer-state hydration, and WebSocket updates. (`docs/PHASE3_1_WORKSPACE_CONTRACTS.md`)
- [x] Implement Scenario Explorer UI (hierarchical scenarios -> grouped products) with metadata columns and expansion state. (`backend/web/lunar_analyst/index.html`, `backend/web/lunar_analyst/src/styles/app.css`, `backend/web/lunar_analyst/src/App.tsx`)
- [x] Add Scenario Explorer top controls: scenario pulldown selector and client-side live filter text box (gap-aware subsequence token matching) applied to the tree-grid. (`backend/web/lunar_analyst/index.html`, `backend/web/lunar_analyst/src/styles/app.css`, `backend/web/lunar_analyst/src/App.tsx`)
- [x] Implement scenario footprint rendering and click-selection on map; synchronize selected scenario between map and Explorer. (`backend/web/lunar_analyst/src/App.tsx`)
- [x] Implement contextual scoping in combined interface so map layer list and controls always reflect active scenario only. (`backend/web/lunar_analyst/src/App.tsx`)
- [x] Implement drag/drop from Explorer to map canvas to create/add `LayerState` as top-most layer. (`backend/web/lunar_analyst/src/App.tsx`)
- [x] Implement drag/drop from Explorer to Layer Manager for explicit stack insertion (z-order control). (`backend/web/lunar_analyst/src/App.tsx`)
- [x] Implement existing-layer drag reorder in Layer Manager using drop targets and persisted `z_index` updates. (`backend/web/lunar_analyst/src/App.tsx`)
- [x] Implement unified stack rendering in Layer Manager so list order reflects true draw order across base + scenario layers. (`backend/web/lunar_analyst/src/App.tsx`, `backend/web/lunar_analyst/index.html`)
- [x] Refine drop-target UX for reorder operations: thin always-visible lines, drag-active global emphasis, stronger hover-target emphasis, and row-assisted target selection (hover row highlights correct adjacent insertion line by drag direction). (`backend/web/lunar_analyst/src/styles/app.css`, `backend/web/lunar_analyst/src/App.tsx`)
- [x] Move per-layer `Remove` action into expanded layer controls (not visible in collapsed summary rows). (`backend/web/lunar_analyst/src/App.tsx`)
- [x] Integrate Shoelace controls (per ADR 0005) for per-layer visibility/opacity/brightness/contrast/colormap inside the combined interface (per ADR 0007). (`backend/web/lunar_analyst/index.html`, `backend/web/lunar_analyst/src/App.tsx`)
- [x] Add unit tests for Scenario Explorer data transforms, tree-grid grouping, and selection/scoping reducers. (covered by scenario grouping/scoping assertions in `backend/tests/contract/test_phase3_1_scenario_workspace.py`)
- [x] Add integration tests for map-explorer synchronization: select in Explorer highlights footprint; click footprint selects Explorer node. (API/footprint prerequisite + context assertions in `backend/tests/contract/test_phase3_1_scenario_workspace.py`; browser interaction checklist in `docs/HOW_TO_MANUALLY_TEST.md`)
- [x] Add integration tests for drag/drop workflows: Explorer -> map canvas and Explorer -> Layer Manager insertion. (backend layer insertion/scoping contract coverage in `backend/tests/contract/test_phase3_1_scenario_workspace.py`; browser workflow checklist in `docs/HOW_TO_MANUALLY_TEST.md`)
- [x] Add contract tests for scenario-scoped API behavior and WS-driven layer updates in active scenario context. (`backend/tests/contract/test_phase3_1_scenario_workspace.py`)
- [x] Add end-to-end test flow: browse scenarios -> select scenario -> add product to map -> adjust layer controls -> verify persisted layer-state. (`backend/tests/contract/test_phase3_minimal_web_client.py`, `backend/tests/contract/test_phase3_1_scenario_workspace.py`)
- [x] Manual verification checklist: desktop and narrow-width layout, keyboard navigation/accessibility for explorer/grid/controls, and redraw stability during pan/zoom. (`docs/HOW_TO_MANUALLY_TEST.md`)

### Phase 4: Marimo Integration
- [x] Launch/attach Marimo as separate process. (`backend/api/dependencies.py`, `backend/api/routers/v1.py`, `backend/tests/contract/test_phase4_marimo_integration.py`)
- [x] Notebook helper client for FastAPI REST/WS. (`backend/notebook/client.py`, `backend/notebook/__init__.py`)
- [x] "Generate in notebook -> register -> render in map" loop. (`backend/tests/contract/test_phase4_marimo_integration.py`)
- [x] Document notebook-driven map workflow: notebook code mutates scenario/layer state through FastAPI only, and web/Tauri clients consume resulting state/events to update the map. (`docs/PHASE4_NOTEBOOK_WORKFLOW.md`)
- [x] Add integration tests for notebook client auth/session, event subscription, and disconnect/reconnect behavior. (`backend/tests/contract/test_phase4_marimo_integration.py`)
- [x] AI protocol checkpoint: prompts enforce public API-only access (no shared-memory mutation paths). (`docs/PHASE4_AI_PROTOCOL_CHECKPOINT.md`)
- [x] Document current `/lunar_analyst/` scenario visibility behavior: the page boots into the configured lunar-analyst scenario and only renders layers for the currently active scenario; notebook-created scenarios/layers appear after selecting that scenario (and reloading if the scenario was created after page load). (`docs/PHASE4_NOTEBOOK_WORKFLOW.md`)
- [x] Add scenario-scoped Marimo launch contract (`scenario_id`, `restart_if_running`) with cwd-conflict handling and restart semantics. (`backend/contracts/models.py`, `backend/api/dependencies.py`, `backend/api/routers/v1.py`, `backend/tests/contract/test_phase4_marimo_integration.py`)
- [x] Align interactive Marimo subprocess imports by prepending `<repo_root>` and `<repo_root>/moonlayers_pkg` to launch `PYTHONPATH`. (`backend/api/dependencies.py`, `backend/tests/contract/test_phase4_marimo_integration.py`, `backend/README.md`)
- [x] Add Scenario Explorer `Open in Marimo` action (launch for active scenario + popup fallback URL path). (`backend/web/lunar_analyst/src/components/explorer/ScenarioExplorerPane.tsx`, `backend/web/lunar_analyst/src/services/marimoService.ts`, `backend/web/lunar_analyst/src/__tests__/marimoService.test.ts`)
- [x] Add notebook-triggered map zoom contract: `POST /api/v1/scenarios/{scenario_id}/map-commands/zoom-to-file` + `map_zoom_requested` WS event and active-scenario map fit handling. (`backend/contracts/models.py`, `backend/api/routers/v1.py`, `backend/tests/contract/test_phase4_marimo_integration.py`, `backend/web/lunar_analyst/src/services/wsClient.ts`, `backend/web/lunar_analyst/src/map/mapController.ts`, `backend/web/lunar_analyst/src/App.tsx`, `backend/web/lunar_analyst/src/__tests__/wsClient.test.ts`)
- [x] Extend notebook helper workflow with `NotebookClient.import_geotiff_create_layer_and_zoom(...)` and update example notebook roundtrip. (`backend/notebook/client.py`, `backend/notebook/examples/import_geotiff_to_map.mo.py`, `backend/tests/worker/test_notebook_client.py`)
- [x] Update operator/developer docs for end-to-end Scenario Workspace <-> Marimo roundtrip and manual acceptance checklist. (`docs/PHASE4_NOTEBOOK_WORKFLOW.md`, `docs/HOW_TO_MANUALLY_TEST.md`, `backend/README.md`)

### Phase 4.5: Legacy Code/Data Import
- [x] Ratify path-first product identity architecture decision (filesystem-first UX with immutable internal IDs): `docs/ADR.0009.path_first_product_identity.md`.
- [x] Publish step-by-step rollout plan for path-first product identity and Explorer migration: `docs/PHASE4_6_PATH_FIRST_PRODUCT_IDENTITY_PLAN.md`.
- [x] `4.5.1a` Build legacy capability inventory from legacy app + `new_horizon` (`algorithm`, `inputs`, `outputs`, `units`, `runtime constraints`, `owner`). `Order: 1 | Depends on: none | Effort: M | Risk: low | Parallel: no`. Acceptance: inventory artifact committed and reviewed for completeness against current legacy menus/workflows. Artifact: `docs/PHASE4_5_LEGACY_ALGORITHM_INVENTORY.md`.
- [x] `4.5.1b` Rank migration order by mission value and dependency risk; lock tranche 1 scope (`horizon profile`, `horizon directory`, `lightmap time-series`, `LOS/viewshed`, `DEM-derived products`). `Order: 2 | Depends on: 4.5.1a | Effort: S | Risk: low | Parallel: no`. Acceptance: tranche 1 list frozen in plan with explicit rationale for included/excluded paths. Artifact: `docs/PHASE4_5_TRANCHE1_PRIORITIZATION.md`.
- [x] Draft shared horizon-store schema + DB/API change plan to avoid duplicating time-independent horizon artifacts across time-window scenarios. Artifact: `docs/PHASE4_5_HORIZON_SHARED_STORE_PLAN.md`.
- [x] Implement file-based scenario bootstrap path (`scenario.toml`) MVP: strict schema validation, discovery scan, idempotent ingest hash, DEM canonicalization (copy/rename/no-op), scenario metadata persistence, and additive discovery/reingest endpoints. Artifacts: `backend/api/dependencies.py`, `backend/api/routers/v1.py`, `backend/contracts/models.py`, `backend/services/migrations.py`, `backend/tests/contract/test_phase4_5_scenario_toml_ingest.py`.
- [x] Document scenario operator workflow for `scenario.toml` creation, startup auto-discovery/reconciliation, and forget-vs-filesystem-delete semantics. Artifact: `docs/SCENARIO_OPERATOR_WORKFLOW.md`.
- [ ] `4.5.1c` Draft and ratify per-algorithm handler contracts in `backend/jobs/handlers.py` (`request params`, `progress events`, `cancellation points`, `output artifact schema`). `Order: 3 | Depends on: 4.5.1b | Effort: M | Risk: med | Parallel: no`. Acceptance: proposed signatures reviewed/ratified, then merged and reflected in generated API route/contract artifacts with no duplicate contract layer. Draft signatures added at `backend/jobs/handlers.py` (Phase 4.5.1c draft contracts section), pending ratification.
- [ ] `4.5.1d` Implement/wrap `new_horizon` horizon-profile generation in worker and expose as job handler with structured progress + cancellation. `Order: 4 | Depends on: 4.5.1c | Effort: M | Risk: med | Parallel: yes (with 4.5.1e/4.5.1f/4.5.1h)`. Acceptance: integration test proves successful run, progress emission, and user-triggered cancellation.
- [ ] `4.5.1e` Implement/wrap basic DEM-derived product generation (`hillshade`, `slope`, `roughness`, `aspect`, `topographic position index`, `terrain ruggedness index`) and register outputs in scenario DB/product catalog. `Order: 5 | Depends on: 4.5.1c | Effort: L | Risk: med | Parallel: yes (with 4.5.1d/4.5.1f/4.5.1h)`. Acceptance: each product is generated, catalog-registered, and discoverable through scenario APIs.
- [ ] `4.5.1f` Implement/wrap horizon-directory raster generation and register outputs in scenario DB/product catalog. `Order: 6 | Depends on: 4.5.1c, 4.5.1d | Effort: M | Risk: med | Parallel: yes (with 4.5.1e/4.5.1h)`. Acceptance: fixture run creates expected artifact set + metadata entries with CRS and processing parameters persisted.
- [ ] `4.5.1g` Implement/wrap lightmap time-series generation with explicit solar/time parameter schema and metadata persistence. `Order: 7 | Depends on: 4.5.1c, 4.5.1d, 4.5.1f | Effort: L | Risk: high | Parallel: limited`. Acceptance: contract tests validate parameter schema and output metadata; regression fixture passes toleranced stats checks.
- [ ] `4.5.1h` Implement/wrap LOS/viewshed path with CRS-explicit inputs and toleranced output validation. `Order: 8 | Depends on: 4.5.1c | Effort: M | Risk: med | Parallel: yes (with 4.5.1d/4.5.1e/4.5.1f)`. Acceptance: regression fixture validates coverage metrics/statistics against legacy baseline within declared tolerances.
  - Detailed plan (ADR.0036-aligned):
    1. Ratify `ToolImplementations.generate_los_viewshed` request/response signature in `backend/jobs/handlers.py`, including:
       - observer input modes: single, list, boolean observer mask
       - CRS-explicit observer coordinates
       - `backend_mode` (`gdal|cuda|auto`)
       - precision controls (`force_parabolic`, high-fidelity request)
       - merge controls for multi-observer outputs
    2. Implement GDAL base path first using `osgeo.gdal.ViewshedGenerate`:
       - single observer execution
       - small observer-list loop + deterministic merge (`any_visible`, `visibility_count`)
       - typed error mapping and output registration
    3. Add routing-metric prepass for `auto` mode:
       - `observer_count`, `observer_density`, `adjacency_ratio`, `component_count`, largest component size
       - route decision metadata emitted in logs/progress
    4. Implement CUDA path for high-volume list/mask workloads:
       - Numba CUDA execution path
       - parity checks against GDAL in overlapping regimes
       - automatic fallback to GDAL in `auto` mode when CUDA preflight/runtime fails
    5. Integrate product surfaces:
       - verify `GET /api/v1/job-definitions` exposure for Tools panel
       - verify assistant tool discoverability/callability via existing tool catalog pipeline
       - enforce confirmation policy (`launch_job`) for assistant-triggered runs
    6. Validate and calibrate thresholds:
       - benchmark matrix across DEM sizes, observer counts, and mask distributions (clustered vs dispersed)
       - compute GDAL->CUDA crossover by hardware tier
       - set initial config defaults and document overrides
    7. Add regression and contract coverage:
       - contract tests for generated route/schema
       - cancellation/progress tests for both backend paths
       - toleranced output/statistics regression fixture against legacy baseline
- [ ] `4.5.1i` Add per-path observability (`job events`, `parameter hash`, `duration`, `failure classification`) and compatibility notes before marking migrated. `Order: 9 | Depends on: 4.5.1d-4.5.1h | Effort: M | Risk: low | Parallel: partial (incremental during implementation, final gate after all paths)`. Acceptance: each migrated path has structured log/event evidence and a documented compatibility note entry.
- [ ] Implement importers for legacy project/data artifacts into scenario layout + SpatiaLite metadata.
- [ ] Add adapters for legacy parameter names/units where needed.
- [ ] Run regression fixtures comparing key output metadata and toleranced raster statistics against legacy baselines.
- [ ] Mark migrated legacy paths as supported and document any intentional behavior changes.
- [ ] AI protocol checkpoint: each migrated path includes fixture-based regression proof and compatibility notes.

### Phase 4.6: Path-First Product Identity (Filesystem-First UX + Immutable Internal IDs)
- [x] Execute Stage 0 (contract + data model design) from `docs/PHASE4_6_PATH_FIRST_PRODUCT_IDENTITY_PLAN.md`.
- [x] Execute Stage 1 (backend reconciliation hardening) from `docs/PHASE4_6_PATH_FIRST_PRODUCT_IDENTITY_PLAN.md`.
- [x] Execute Stage 2 (additive API expansion for path-first Explorer) from `docs/PHASE4_6_PATH_FIRST_PRODUCT_IDENTITY_PLAN.md`.
- [x] Execute Stage 3 (Explorer UI migration) from `docs/PHASE4_6_PATH_FIRST_PRODUCT_IDENTITY_PLAN.md`.
- [x] Execute Stage 4 (rename/move operations) from `docs/PHASE4_6_PATH_FIRST_PRODUCT_IDENTITY_PLAN.md`.
- [x] Execute Stage 5 (multi-file product completion) from `docs/PHASE4_6_PATH_FIRST_PRODUCT_IDENTITY_PLAN.md`.
- [ ] Stage-gate: all required 4.6 contract/integration/manual checks are complete before Phase 5 feature expansion.

### Phase 4.7: Layer Manager UX Simplification (Drag/Drop First)
- [ ] Remove Layer Manager product selection pulldown and `Add Layer` button from the map workspace UI; keep layer add path as Explorer drag/drop only for this phase.
- [ ] Keep scenario-scoped layer persistence contract unchanged: `layer_state` remains authoritative per `scenario_id`; switching active scenario must always rehydrate only that scenario's layer list.
- [ ] Ensure Explorer -> Layer Manager drop path remains explicit and stable for insertion-order control (drop zones + row-assisted insertion).
- [ ] Harden backend layer-create validation: reject requests where `source_file_id` does not belong to `scenario_id` to prevent cross-scenario layer contamination.
- [ ] Preserve additive path for later UI enhancement: add Scenario Manager action button that adds layer from currently selected product-tree row (deferred follow-up task in this phase).
- [ ] Add/refresh contract tests for scenario-scoped layer list reload and create-layer validation (`source_file_id`/`scenario_id` ownership mismatch rejection).
- [ ] Add/refresh integration tests for Explorer drag/drop add + scenario switch roundtrip (add in Scenario A, switch to Scenario B, switch back to Scenario A, verify persisted list/order/styles).
- [ ] Manual verification checklist updates: desktop + narrow layout, mouse drag/drop behavior, and keyboard fallback note for deferred Scenario Manager add button.

### Phase 4.8: Notebook-Defined Jobs (Catalog + Headless Execution)
- [ ] Ratify architecture decision for notebook-defined jobs: notebooks are executable job definitions discovered from configured search roots, while FastAPI remains the authoritative control plane and job state owner.
- [ ] Preserve compute contract invariant: `backend/jobs/handlers.py` remains the single source of truth for job contracts/routes by introducing notebook-backed handler entries (no parallel contract layer).
- [ ] Define notebook job metadata schema (for example: `job_id`, `title`, `description`, `params_schema`, `outputs_schema`, `tags`, `visibility`, `notebook_path`) and validation rules.
- [ ] Implement notebook catalog discovery service with configured allowlisted roots, deterministic ordering, and cache invalidation/reload behavior.
- [ ] Add additive API endpoints for job-definition discovery (for example `GET /api/v1/job-definitions`) returning both notebook-defined and native/system jobs with type classification.
- [ ] Implement backend-managed headless notebook execution path (worker/subprocess) so production job runs are not coupled to Marimo server runtime.
- [ ] Define notebook runtime SDK/contract for structured progress, cancellation checkpoints, logging, and artifact manifest emission.
- [x] Introduce notebook script helper facade module `backend/notebook/notebook_helper.py` to centralize reusable script helpers (runtime context/progress/output helpers, path helpers, native bootstrap/bridge helpers) and reduce per-script boilerplate.
- [x] Refactor initial notebook job scripts to the facade + executable-script style (no `run()` wrapper): `backend/notebook/jobs/generate_gdaldem_derivatives.py`, `backend/notebook/jobs/generate_horizons.py`.
- [ ] Implement secure execution controls: normalized path validation, allowlisted notebook roots, execution identity scoping, and per-run capture of notebook content hash + parameter hash + environment fingerprint.
- [ ] Define artifact registration flow: notebook job writes files under scenario root, then registration enforces `scenario_id` ownership and in-root file safety rules before catalog persistence.
- [ ] Update Jobs UI to Jobs Manager: primary list is discovered notebook-defined jobs; low-level/native handlers are grouped as advanced/system entries.
- [ ] Add contract tests for job-definition discovery schema, additive compatibility, and event payload consistency for notebook-backed runs.
- [ ] Add integration tests for headless notebook execution success/failure/cancellation with progress events and artifact registration roundtrip.
- [ ] Add security tests for out-of-root notebook discovery rejection and forbidden artifact registration attempts.
- [ ] Add manual verification checklist: discover notebook jobs, launch job, monitor progress, cancel run, verify outputs appear in Scenario Explorer and layer workflows.

### Phase 4.9: MoonLayers In-Repo Integration (`moonlayers_pkg`)
- [x] Import `D:\projects\moonlayers` into this repo as `moonlayers_pkg/` (sanitized copy: no `.git`, `node_modules`, caches/build artifacts).
- [x] Record external source snapshot and sync policy (`docs/MOONLAYERS_SYNC_POLICY.md`).
- [x] Keep external repo in place as legacy mirror/reference while making `moonlayers_pkg/` authoritative for active edits.
- [x] Ensure notebook job runner subprocess prefers in-repo `moonlayers_pkg` by prepending it to `PYTHONPATH` when present.
- [x] Add regression test for runner env import preference (`backend/tests/contract/test_phase4_8_notebook_jobs.py`).
- [x] Update architecture/docs for in-repo location and runtime behavior (`docs/NEW_DESIGN.md`, `backend/README.md`).
- [x] Update manual verification workflow for editable install + VS Code plain-script import/debug (`docs/HOW_TO_MANUALLY_TEST.md`).
- [ ] Optional follow-up: add pinned bootstrap command/script that installs `moonlayers_pkg` editable in fresh environments.

### Phase 4.10: Assistant RAG Wrapper + Global Index (ADR 0021)
- [x] `4.10.1a` Ratify ADR 0021 implementation scope and lock non-goals (`no API/WS schema break`, `wrappers are additive`, `global index path`, `git-managed corpus root`). `Order: 1 | Depends on: none | Effort: S | Risk: low | Parallel: no`. Acceptance: ADR review sign-off recorded and scope frozen for Phase 4.10. Artifact: `docs/ADR.0021.assistant_rag_wrapper_and_scenario_index.md`.
- [x] `4.10.1b` Add config contracts for `rag_ollama` and `rag_openai` plus global DB path, corpus root (`docs/rag_corpus`), and retriever mode (`fts5` default). `Order: 2 | Depends on: 4.10.1a | Effort: S | Risk: low | Parallel: no`. Acceptance: config loads with safe defaults and disabled-by-default rollout behavior. Artifacts: `config/lunar_analyst.toml`, `backend/services/assistant/provider_registry.py`.
- [x] `4.10.1c` Implement generic `RagWrapperProvider` and register additive providers `rag_ollama`/`rag_openai` without changing existing provider behavior. `Order: 3 | Depends on: 4.10.1b | Effort: M | Risk: med | Parallel: no`. Acceptance: `/api/v1/assistant/providers` returns both new providers when enabled; non-RAG provider tests remain green. Artifacts: `backend/services/assistant/providers/rag_wrapper_provider.py`, `backend/services/assistant/provider_registry.py`, `backend/tests/worker/test_rag_wrapper_provider.py`.
- [x] `4.10.1d` Add structured citation metadata plumbing (`metadata.source_references`) using existing message metadata fields only. `Order: 4 | Depends on: 4.10.1c | Effort: M | Risk: med | Parallel: yes (with 4.10.1e)`. Acceptance: assistant responses from RAG providers include deterministic reference payloads in message metadata; no assistant contract schema changes. Artifacts: `backend/services/assistant/providers/base.py`, `backend/services/assistant/assistant_service.py`, `backend/tests/worker/test_assistant_provider_tool_contract.py`.
- [x] `4.10.1e` Implement global RAG index service and retriever interface with FTS5 backend (`global_rag.db`) and strict path/reference sanitization. `Order: 5 | Depends on: 4.10.1c | Effort: L | Risk: med | Parallel: yes (with 4.10.1d)`. Acceptance: deterministic lexical retrieval passes tests; path traversal/escape attempts are rejected; sanitized references only. Artifacts: `backend/services/assistant/rag_index.py`, `backend/services/assistant/rag_retriever.py`, `backend/tests/worker/test_rag_index_path_safety.py`, `backend/tests/worker/test_rag_retrieval_fts5.py`.
- [x] `4.10.1f` Implement type-aware chunking and first-line single-chunk directive (`RAG_CHUNKING: single`) for `.md`/`.txt`. `Order: 6 | Depends on: 4.10.1e | Effort: M | Risk: med | Parallel: yes (with 4.10.1g)`. Acceptance: markdown table/header boundaries and CSV row integrity are preserved; directive forces one chunk and excludes directive line from content. Artifacts: `backend/services/assistant/rag_index.py`, `backend/tests/worker/test_rag_chunking_policy.py`.
- [x] `4.10.1g` Add handler-centered ingestion path `ToolImplementations.assistant_rag_ingest` + assistant tool `scenario.rag_ingest` with confirmation/policy compliance. `Order: 7 | Depends on: 4.10.1e | Effort: M | Risk: med | Parallel: yes (with 4.10.1f)`. Acceptance: ingest job supports full and incremental refresh from git-managed corpus root (`docs/rag_corpus`), emits structured progress, and honors cancellation. Artifacts: `backend/jobs/handlers.py`, `backend/services/assistant/tool_registry.py`, `backend/tests/worker/test_rag_ingest_handler.py`, `backend/tests/worker/test_assistant_tool_loop.py`.
- [x] `4.10.1h` Implement non-blocking startup incremental refresh (`mtime+size`, hash-on-change) for global index maintenance. `Order: 8 | Depends on: 4.10.1g | Effort: M | Risk: med | Parallel: no`. Acceptance: backend readiness remains non-blocking while changed docs are refreshed post-startup. Artifacts: `backend/api/app.py`, `backend/services/assistant/assistant_service.py`, `backend/tests/worker/test_rag_startup_refresh.py`.
- [x] `4.10.1i` Add contract/integration regression coverage for catalog, assistant turns, and WS compatibility with RAG enabled/disabled. `Order: 9 | Depends on: 4.10.1d-4.10.1h | Effort: M | Risk: low | Parallel: partial`. Acceptance: no breaking REST/WS contract changes; existing provider flows unchanged when RAG disabled. Artifacts: `backend/tests/contract/test_assistant_provider_catalog_rag.py`, `backend/tests/contract/test_phase6_assistant_api.py`, `backend/tests/contract/test_phase6_assistant_ws.py`.
- [x] `4.10.1j` Export and validate contract artifacts + operational docs (runbook for document ingestion, refresh, and rollback). `Order: 10 | Depends on: 4.10.1i | Effort: S | Risk: low | Parallel: no`. Acceptance: OpenAPI/schema exports pass, operator docs include ingestion/refresh timings and rollback steps. Artifacts: `backend/tools/export_openapi`, `backend/tools/export_contract_schemas`, `docs/HOW_TO_MANUALLY_TEST.md`, `backend/README.md`.
- [x] Phase-gate: Phase 4.10 complete only when all contract tests pass and RAG wrappers can be toggled off cleanly via config without behavior regressions.

### Phase 4.11: Hybrid Command Router + Deterministic Guidance Triggers (ADR 0022)
- [ ] `4.11.1a` Ratify ADR 0022 scope and lock invariants (`action specs reference unified tool registry`, `no duplicated tool schema/logic`, `deterministic path is plan-to-tool-call only`). `Order: 1 | Depends on: 4.10.1j | Effort: S | Risk: low | Parallel: no`. Acceptance: ADR review sign-off recorded and scope frozen for Phase 4.11. Artifact: `docs/ADR.0022.hybrid_command_router_with_deterministic_guidance_triggers.md`.
- [ ] `4.11.1b` Add/validate action registry contracts and startup validation (tool reference existence, template placeholders, deny-pattern wiring). `Order: 2 | Depends on: 4.11.1a | Effort: M | Risk: med | Parallel: no`. Acceptance: invalid action specs fail fast at startup with clear errors; registry remains data-driven (no monolithic switch path). Artifacts: `backend/services/assistant/command_router.py`, `backend/tests/worker/test_hybrid_command_router.py`.
- [ ] `4.11.1c` Implement complexity guards and shadowing prevention (`if/when/unless/only if` and other qualifiers force model-loop fallback). `Order: 3 | Depends on: 4.11.1b | Effort: M | Risk: med | Parallel: yes (with 4.11.1d)`. Acceptance: broad imperative patterns do not capture conditional/analytic prompts. Artifacts: `backend/services/assistant/command_router.py`, `backend/tests/worker/test_hybrid_command_router.py`.
- [ ] `4.11.1d` Implement deterministic ambiguity handling for layer-name actions (exact/normalized match -> mutate, multi-match -> concise clarification, no arbitrary pick). `Order: 4 | Depends on: 4.11.1b | Effort: M | Risk: med | Parallel: yes (with 4.11.1c)`. Acceptance: ambiguous visibility commands return clarification instead of read-only stalls or silent no-ops. Artifacts: `backend/services/assistant/tool_registry.py`, `backend/services/assistant/assistant_service.py`, `backend/tests/worker/test_assistant_tool_loop.py`.
- [ ] `4.11.1e` Add partial deterministic execution for chained prompts (execute matched segments, continue unmatched remainder in model-loop with updated state). `Order: 5 | Depends on: 4.11.1c-4.11.1d | Effort: M | Risk: med | Parallel: no`. Acceptance: mixed prompts are not all-or-nothing; deterministic gains are preserved before model reasoning. Artifacts: `backend/services/assistant/assistant_service.py`, `backend/tests/worker/test_assistant_tool_loop.py`.
- [ ] `4.11.1f` Add mutate-intent guardrails in model-loop (`read-only-only` dead-end detection, compact large tool payloads, explicit no-op failure/clarification path). `Order: 6 | Depends on: 4.11.1e | Effort: M | Risk: med | Parallel: partial`. Acceptance: prompts like `show slope` cannot complete successfully without a mutation or clarification. Artifacts: `backend/services/assistant/assistant_service.py`, `backend/tests/worker/test_assistant_tool_loop.py`.
- [ ] `4.11.1g` Implement deterministic procedural guidance triggering for visibility intents and add focused few-shot corpus docs. `Order: 7 | Depends on: 4.11.1e | Effort: S | Risk: low | Parallel: yes (with 4.11.1f)`. Acceptance: matched visibility intents inject only targeted procedural guidance snippets; broad corpus retrieval is not required for fast-path correctness. Artifacts: `backend/services/assistant/rag_retriever.py`, `docs/rag_corpus/guidance_layer_visibility_fewshot.txt`, `backend/tests/worker/test_rag_retrieval_fts5.py`.
- [ ] `4.11.1h` Add provider-cost control gate for command turns (config option to disallow cross-provider fallback for local-only operation) and regression tests. `Order: 8 | Depends on: 4.11.1f | Effort: S | Risk: low | Parallel: yes (with 4.11.1g)`. Acceptance: with local-only fallback settings, command turns make no OpenAI fallback attempts. Artifacts: `config/lunar_analyst.toml`, `backend/services/assistant/assistant_service.py`, `backend/tests/worker/test_assistant_tool_loop.py`.
- [ ] `4.11.1i` Add provenance/trace metadata for deterministic vs model-reasoned actions and keep response UX consistent. `Order: 9 | Depends on: 4.11.1e | Effort: S | Risk: low | Parallel: yes (with 4.11.1g/4.11.1h)`. Acceptance: turn metadata and logs explicitly indicate execution origin; existing response contracts remain additive-only. Artifacts: `backend/services/assistant/assistant_service.py`, `backend/tests/contract/test_phase6_assistant_api.py`, `backend/tests/contract/test_phase6_assistant_ws.py`.
- [ ] `4.11.1j` Phase-gate: run targeted regressions for known failure prompts and contract compatibility. `Order: 10 | Depends on: 4.11.1a-4.11.1i | Effort: S | Risk: low | Parallel: no`. Acceptance: prompts `show slope`, `turn off slope layer`, `switch to test_scenario, then turn on slope` pass deterministically or produce explicit clarification; no REST/WS schema breaks.

### Phase 5: Scenario Analytics Features
- [ ] Phase 5 start gate: Phase 4.6 Stage 0-3 complete (path-first Explorer/API model active).
- [ ] DEM-derived products (slope, roughness, aspect).
- [ ] Lighting products: horizon directory generation
- [ ] Lighting products: time-series lightmap generation
- [ ] Lighting products: interval aggregates (sun/shadow duration)
- [ ] Path-first product/collection taxonomy and metadata presentation refinement (replaces product-id-centric clustering UX).
- [ ] Start Option C Stage 2 contract expansion (additive-only in `/api/v1`): optional fields, richer styling controls, batch layer operations, advanced filtering/query APIs, and notebook convenience helpers.
- [ ] Ensure all new Phase 5 outputs register path-first product/collection metadata compatible with Phase 4.6 contracts.
- [ ] Add per-feature integration tests and update regression fixtures for each new product type.
- [ ] AI protocol checkpoint: per-feature tasks meet DoD (code + tests + observability + migration notes where needed).

### Phase 6: UX and Packaging
- [ ] Phase 6 start gate: Phase 4.6 path-first migration tests (including rename/move and multi-file collection coverage) are passing.
- [ ] Timeline UI for lighting products.
- [ ] Label/crater vector tools.
- [ ] Tauri packaging for desktop deployment.
- [ ] Add packaging/install verification tests on clean Windows 11 environment.
- [ ] Run release-candidate end-to-end suite before each packaged release.
- [ ] AI protocol checkpoint: packaging/security-sensitive changes explicitly approved and release checklist attached.

## 7. Testing Plan (practical, no parity benchmark suite)

### 7.1 Required Test Tracks
- [ ] API contract tests (REST + WS event payloads).
- [ ] Worker integration tests (`pythonnet` load, cancellation, progress).
- [ ] Scenario filesystem tests (path policy, import/export behavior).
- [ ] End-to-end: create scenario.
- [ ] End-to-end: run compute job.
- [ ] End-to-end: register output.
- [ ] End-to-end: verify client-visible layer update.

### 7.2 Regression Assets
- [ ] Use small canonical DEM/lightmap fixtures for deterministic CI checks.
- [ ] Store expected metadata snapshots (JSON) for schema regression tests.

## 8. Operational Detail (expanded)

### 8.1 Logging and Audit
- [ ] Structured JSON logs with `scenario_id`, `job_id`, `product_id`.
- [ ] Persist job event history in `job_events` table.
- [ ] Include compute parameters hash for reproducibility.

### 8.2 Lifecycle Management
- [ ] FastAPI supervises worker and (optionally) Marimo process.
- [ ] Debug mode supports direct FastAPI execution under VS Code debugger; packaged mode keeps identical contracts with Python sidecar under Tauri.
- [ ] Graceful shutdown sends cancellation to active jobs, then waits with timeout.
- [ ] On restart, unfinished jobs move to `failed_recoverable` or `queued` by policy.

### 8.3 Resource Controls
- [ ] Separate concurrency limits for queued vs immediate async jobs.
- [ ] Back-pressure: reject or defer immediate jobs when system is saturated.
- [ ] Per-job memory/time limits where feasible.

### 8.4 Versioning and Migration
- [ ] Explicit DB schema version table.
- [ ] File format version in product metadata.
- [ ] Startup migration runner with rollback on failure.

### 8.5 Security (localhost-first)
- [ ] Bind services to localhost by default.
- [ ] Token-protect mutation endpoints.
- [ ] Strict file-root allowlist and path normalization.

### 8.6 API Contract Governance
- [ ] Canonical contract artifacts are OpenAPI + JSON schema files in one owned location.
- [ ] Every schema/event contract change requires compatibility classification (`additive` vs `breaking`).
- [ ] Every schema/event contract change requires a changelog entry.
- [ ] Every schema/event contract change requires updated REST/WS contract tests.
- [ ] WS event payload versioning uses `schema_version` when payload shape evolves.

## 9. Immediate Next Steps
- [ ] Start Phase 0.5 legacy inventory and migration acceptance matrix.
- [ ] Build Phase 1 worker spike using one DEM -> horizons output.
- [ ] Create SpatiaLite schema migration `v1` with scenarios/products/jobs tables.
- [ ] Stand up minimal FastAPI endpoints and WS event stream.

## 10. AI-Assisted Delivery Protocol (Codex + Gemini)

### 10.1 Task Sizing and Scope
- [ ] Break work into small, testable tasks (target: about 1 hour each, limited files, clear boundaries).
- [ ] Every task must declare explicit out-of-scope items.
- [ ] Prefer sequential delivery of vertical slices over large horizontal rewrites.

### 10.2 Prompt Contract
- [ ] Use a standard prompt template for implementation tasks:
  - goal
  - exact files allowed to change
  - constraints/invariants
  - acceptance criteria
  - required tests
- [ ] Keep one primary action per prompt (`analyze` or `implement` or `refactor` or `test-authoring`).
- [ ] Require output format: patch + test updates + concise rationale.

### 10.3 Repository Guidance for Agents
- [ ] Maintain `AGENTS.md` at repo root with coding standards, architecture invariants, and unsafe operations policy.
- [ ] Add subdirectory-specific `AGENTS.md` files where behavior differs (for example, `backend/`, `worker/`, `client/`).
- [ ] Keep guidance concrete and enforceable (avoid vague style-only rules).

### 10.4 Environment and Bootstrap
- [ ] Provide a single bootstrap path for agents (`scripts/bootstrap.*`) that verifies Python/.NET/toolchain dependencies.
- [ ] Define required environment variables and local defaults.
- [ ] Add a preflight check command agents run before edits/tests.

### 10.5 Definition of Done (per task/phase item)
- [ ] Code implemented and aligned to architecture constraints.
- [ ] Tests added/updated at the correct level (unit/integration/E2E as applicable).
- [ ] CI-relevant checks pass locally (or documented if not executable locally).
- [ ] Migration/compatibility notes included for schema/data/API changes.
- [ ] Observability updated for new job types/events (logs + event payloads).

### 10.6 High-Risk Change Policy
- [ ] For native bridge, raster compute, data migration, and packaging changes, require an additional review pass before merge.
- [ ] When feasible, generate two candidate approaches for high-risk logic and pick based on testability and failure modes.
- [ ] Require rollback plan for changes that can corrupt state or break scenario compatibility.

### 10.7 Safety and Approval Controls
- [ ] Do not perform any file/git operations without explicit human approval.
- [ ] Require human approval for DB schema migrations, packaging changes, and security-sensitive configuration edits.
- [ ] Restrict agent write scope to declared files for each task when possible.

## 11) MoonLayers Local Environment Note (Phase 4.9)

- Preferred local setup is editable install from this repo:
  - `pip install -e .\moonlayers_pkg`
- This install can fail in locked-down/offline environments when pip cannot fetch build backend dependency `hatchling` declared in `moonlayers_pkg/pyproject.toml`.
- Supported fallback for local script/testing sessions:
  - set `PYTHONPATH` to include in-repo package root:
  - PowerShell: `$env:PYTHONPATH='D:\projects\lunar_analyst\moonlayers_pkg'`
- Backend notebook-job subprocesses already prepend `<repo_root>/moonlayers_pkg` to `PYTHONPATH` when present, so scenario job execution remains aligned with in-repo MoonLayers development.

