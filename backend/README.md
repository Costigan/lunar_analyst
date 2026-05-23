# Typed Contract Scaffolding

This package is the typed source of truth for API/service contract design.

- `backend/contracts/models.py`: Pydantic contract models.
- `backend/contracts/decorators.py`: optional method-level contract registry.
- `backend/services/interfaces/*.py`: typed abstract interfaces.
- `backend/tools/export_contract_schemas.py`: export JSON Schemas from models.
- `backend/tools/export_openapi.py`: export OpenAPI from FastAPI app wiring.
- Operator workflow (scenario creation/reconciliation/forget): `docs/SCENARIO_OPERATOR_WORKFLOW.md`.

## OpenAPI/Schema Generation Triggers

Runtime generation:
- FastAPI generates OpenAPI lazily the first time `app.openapi()` is called.
- This is automatically triggered by requesting `/openapi.json` when the server is running.

Explicit file export:
- Run `python -m backend.tools.export_openapi` to write `docs/contracts/generated/v1/openapi.json`.
- Run `python -m backend.tools.export_contract_schemas` to write model schemas under `docs/contracts/generated/v1/`.

Local contract-test workflow:
- Run `python -m backend.tools.export_openapi` to refresh OpenAPI artifact.
- Run `python -m backend.tools.export_contract_schemas` to refresh JSON Schema artifacts.
- Run `pytest backend/tests/contract -q` to validate OpenAPI/WS/error-envelope contracts.
- Run `pytest backend/tests/integration/test_moonlib_bridge_real.py -q` to exercise real CLR + `moonlib` integration (auto-skips if native runtime/assembly import is unavailable).

CI integration is intentionally deferred for now.

## Browser App

- App route: `GET /lunar_analyst/`
- Map config: `GET /api/v1/lunar-analyst/config`
- Map bootstrap (scenario/product/layer seed): `POST /api/v1/lunar-analyst/bootstrap`
- Hillshade file endpoint (range-capable): `GET /api/v1/lunar-analyst/hillshade`

Configuration lives in `config/lunar_analyst.toml` under `[backend.lunar_analyst]`:

- `hillshade_path`: local path to a GeoTIFF/hillshade file.
- `hillshade_opacity`: startup overlay opacity (0.0-1.0).
- `moon_trek_capabilities_url`, `moon_trek_layer`, `moon_trek_tile_matrix_set`, `moon_trek_style`: Moon Trek WMTS settings.
- `[backend.trek].catalog_cache_ttl_seconds` and `feature_cache_ttl_seconds`: in-memory Moon Trek cache TTLs (seconds), used by `/api/v1/trek` catalog + feature proxy.

The frontend page is a React + OpenLayers app served from `backend/web/lunar_analyst/`.

## Phase 2 Backend Core Endpoints

- `POST /api/v1/scenarios`
- `GET /api/v1/scenarios`
- `GET /api/v1/scenarios/{scenario_id}`
- `POST /api/v1/scenarios/{scenario_id}/imports/geotiff`
- `POST /api/v1/products`
- `GET /api/v1/scenarios/{scenario_id}/products`
- `GET /api/v1/products/{product_id}`
- `GET /api/v1/products/{product_id}/files`
- `GET /api/v1/files/{file_id}` (range-capable)
- `POST /api/v1/layers`
- `PATCH /api/v1/layers/{layer_id}`
- `DELETE /api/v1/layers/{layer_id}`
- `GET /api/v1/scenarios/{scenario_id}/layers`
- `WS /api/v1/events`
- `GET /api/v1/jobs/handlers` (generated job route discovery for UI clients)
- `GET /api/v1/job-definitions` (notebook + native/system job catalog for Jobs Manager)

## Phase 4 Notebook + Marimo Endpoints

- `POST /api/v1/notebook/sessions`
- `GET /api/v1/notebook/sessions/{session_id}`
- `WS /api/v1/notebook/events` (requires notebook token)
- `POST /api/v1/marimo/launch` (launch or attach)
- `GET /api/v1/marimo/status`
- `POST /api/v1/marimo/stop`
- `POST /api/v1/scenarios/{scenario_id}/map-commands/zoom-to-file` (queue `map_zoom_requested` viewport event)
- `POST /api/v1/jobs/run-notebook-definition` (headless notebook job execution via worker subprocess)

Notebook-defined jobs:
- In-repo MoonLayers package workflow:
  - Install editable local package in `.venv`: `.venv/bin/python -m pip install -e ./moonlayers_pkg`
  - Fallback when editable install is unavailable in a locked-down/offline env: set `PYTHONPATH=/e/projects/lunar_analyst/moonlayers_pkg` for the shell/session running scripts.
  - Build frontend assets when widget JS/CSS changes: `cd moonlayers_pkg && npm install && npm run build`
  - Plain scripts (non-Marimo) can import `moonlayers` when using the repo-managed `.venv/bin/python`.
- Configure roots in `config/lunar_analyst.toml` under `[backend.notebook_jobs]`:
  - `search_roots = ["../path/to/notebook_jobs"]`
  - `python_executable = "/path/to/python3.11"` (optional override)
- Configured roots are scanned for explicit `*.job.json` files, and also for implicit top-level `*.py` scripts (no `.job.json` required). Hidden (`.*`), underscore-prefixed (`_*`), and `__init__.py` files are excluded from implicit discovery.
- When `GET /api/v1/job-definitions` includes a valid `scenario_id`, top-level `*.py` files in that scenario root are also exposed as implicit notebook jobs (no `.job.json` required). Hidden (`.*`), underscore-prefixed (`_*`), and `__init__.py` files are excluded.
- If `scenario_id` is missing or unknown, scenario-root implicit script discovery is not applied; configured-root discovery (explicit `*.job.json` + implicit top-level `*.py`) still applies.
- Runtime helper API is available via `backend.notebook.job_sdk.NotebookJobContext` (`report_progress`, `register_output`, `is_cancelled`).
- Script-mode helper API is available via `backend.notebook.runtime`:
  - `get_context()` loads context from `LUNAR_NOTEBOOK_CONTEXT_PATH`
  - `report_progress(...)`, `register_output(...)`, `is_cancelled()`
- Entry-point behavior:
  - If notebook module defines callable `run(context)`, runner calls it.
  - If no `run` is defined, module top-level script execution is still allowed (script mode).
- Runner import behavior:
  - During `run-notebook-definition`, backend prepends `<repo_root>/moonlayers_pkg` to `PYTHONPATH` for the subprocess when that directory exists.
  - This keeps scenario scripts/jobs aligned with the in-repo MoonLayers source during active development.

Marimo launch defaults:
- `backend.marimo.python_executable` (or env `LUNAR_ANALYST_MARIMO_PYTHON`) controls which Python runs `-m marimo`.
- If `backend.marimo.command` is provided, it is used as-is.
- `backend.marimo.use_token_auth` controls whether default launches use `--token` or `--no-token`.
- `backend.marimo.log_path` (optional) captures Marimo stdout/stderr to a file for tailing.
- `POST /api/v1/marimo/launch` supports optional `scenario_id` and `restart_if_running`:
  - `scenario_id` resolves launch `cwd` from scenario catalog metadata.
  - If Marimo is already running with a different `cwd`, launch returns `409` unless `restart_if_running=true`.
  - If Marimo is in `attach` mode, scenario-scoped launch requests are rejected with `409` until attach is stopped.
- FastAPI-managed Marimo launches prepend `<repo_root>` and `<repo_root>/moonlayers_pkg` to `PYTHONPATH` (then append existing `PYTHONPATH`) so interactive notebooks can import `backend.*` and `moonlayers` from scenario-scoped working directories.

Backend logging:
- `backend.log_level` in `config/lunar_analyst.toml` controls backend logger verbosity.
- `backend.logging.level` provides the same default level in the structured logging block.
- `backend.logging.loggers` supports per-logger overrides by logger name.
- Env `LUNAR_ANALYST_LOG_LEVEL` overrides config when set.

Notebook example:
- `backend/notebook/examples/import_geotiff_to_map.mo.py`

## Job Kind Definition Pattern (Example)

Define each valid job kind once as a typed handler method:

- Handler method:
  - `JobHandlers.generate_horizons(scenario_id, scenario_root_dir, dem_path, horizons_dir, overwrite_horizons, compress_horizons, mode) -> GenerateHorizonsResult`
  - `JobHandlers.generate_hillshade(scenario_id) -> GenerateHillshadeResult`

Runtime adapter flow:
- Startup discovers `JobHandlers` methods decorated with `@contract`.
- FastAPI routes are generated from handler signatures (`/api/v1/jobs/<handler-name>`).
- Generated endpoint wrappers are cached in-memory (`ROUTE_CACHE`) and reused for all requests.
- Requests dispatch to `JobService.run_typed_job(handler_name, args)` for orchestration.
- `JobService` does not require one method per job kind; handler signatures are the contract source.

This keeps allowed job types and parameters tied to a single signature source.

Prototype execution is implemented for typed handlers via `StubJobService.run_typed_job(...)`, including the native bridge-backed `generate_hillshade` flow.

## Native Bootstrap (Phase 1 Step 1)

- Module: `backend/worker/native_bootstrap.py`
- Purpose: bootstrap `pythonnet` using `coreclr`, enforce `.NET 9` (`net9.0`) assembly path expectations, and load the `moonlib` assembly.
- Dependency resolution: preloads managed dependencies discovered from `moonlib.deps.json` (prefers local output; falls back to NuGet cache) and applies Linux-native dependency and environment setup for `moonlib`, GDAL, PROJ, and CSPICE.
- Built-in smoke check: calls `moonlib.BridgeSmoke.AddOne(1.0)` and requires `2.0`.
- Built-in smoke check also validates SPICE init via `moonlib.BridgeSmoke.SpiceSmokeTest(1)` and requires `2`.
- Default app config file: `config/lunar_analyst.toml` (`[backend.native]` section)
- Env overrides:
  - `LUNAR_ANALYST_CONFIG_TOML`
  - `LUNAR_ANALYST_MOONLIB_DLL`
  - `LUNAR_ANALYST_DOTNET_RUNTIME_CONFIG`
- Test coverage:
  - `backend/tests/worker/test_native_bootstrap.py`

## Assistant RAG (ADR 0021)

RAG source corpus (git-managed):
- `docs/rag_corpus/`

Global RAG index:
- `<workspace_root>/.assistant/rag/global_rag.db`

Provider options:
- Unified backend RAG wrapping is applied to selected tool-loop providers in place (for example `ollama`, `openai`) when enabled.
- External CLI providers (`external_mcp_agent` mode, for example `codex_cli`, `gemini_cli`) are not wrapped in this implementation.

Config block:
- `[backend.llm.rag]`

Chunking directive (`.md` / `.txt`):
- If first line is exactly `RAG_CHUNKING: single`, the file is indexed as one chunk.

Ingest/refresh:
- Assistant tool: `scenario.rag_ingest`
- Typed handler: `ToolImplementations.assistant_rag_ingest`
- Startup auto-refresh runs in a background startup task when enabled.
