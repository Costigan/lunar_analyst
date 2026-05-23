# Lunar Analyst

Lunar Analyst is a Linux-only toolkit for lunar south pole mission analysis, combining:

- a FastAPI backend (scenario/catalog/control plane),
- a browser UI (React + OpenLayers),
- a separate Python compute path for native `.NET`/`moonlib` work via `pythonnet`,
- and notebook-first workflows (Marimo + headless notebook jobs).

It is designed around scenario folders on disk and high-fidelity terrain/lighting analysis.

## Status

This repository is an active transition from a legacy native app toward a browser-first + notebook-first workflow.

Key implemented capabilities include:

- scenario discovery and catalog-backed management
- typed job handlers with generated API routes/contracts
- native horizon and hillshade workflows
- lightmap streaming + native temporal reduction (v2 bridge path)
- notebook-defined jobs (including Marimo examples)
- PSR (permanent shadow / PSR-style) raster generation via native `moonlib` mapops

## Architecture (High Level)

Runtime topology:

1. `FastAPI` service (`backend.api.app`)
2. compute worker code path (`backend/worker/*`) for `pythonnet` + `moonlib`
3. Marimo process (interactive notebooks)
4. browser/Tauri client (`backend/web/lunar_analyst`)

Important invariant:

- Native `.NET`/GDAL work should not be loaded casually into the API process if it risks DLL conflicts; use isolated subprocess/worker patterns where needed.

## Platform / Requirements

- Host-native Linux is the maintained baseline runtime.
- Host-native Pop!_OS is the active day-to-day development baseline, with Ubuntu container parity for deployment-style validation.
- Python `3.11`
- .NET `9.0`
- Node.js / npm (for frontend builds/tests)

## Repository Layout

- `backend/` FastAPI app, contracts, jobs, notebook runtime, worker bridge code
- `backend/web/lunar_analyst/` React frontend
- `native/new_horizon/moonlib/` .NET native analysis library
- `docs/` design docs, generated OpenAPI/JSON schemas, planning notes
- `config/lunar_analyst.toml` local app/runtime configuration
- `scenarios/` sample/in-repo scenarios (plus external workspace scenarios)
- `moonlayers_pkg/` in-repo MoonLayers package/frontend assets

## Scenario Model

A scenario is a folder on disk with analysis inputs and outputs. In practice, common files/dirs include:

- DEM GeoTIFF (often `dem.tif` or `primary_dem.tif`)
- `lighting/horizons/` precomputed horizon tiles
- `scenario.db` (SpatiaLite/metadata)

The backend workspace root is configured in `config/lunar_analyst.toml`:

- `[backend].workspace_root = "/e/lunar_analyst_scenarios"`

Assistant session state and the global RAG index are workspace-root-relative by default:

- `<workspace_root>/.assistant/assistant_sessions.db`
- `<workspace_root>/.assistant/rag/global_rag.db`

## Quick Start (Local Dev)

### 1) Bootstrap the repo-managed development environment

Linux:

```bash
./scripts/bootstrap.sh
```

The canonical Python dependency manifests are:

- `requirements.in`
- `requirements.txt`

`requirements.in` is the human-edited dependency source. Regenerate `requirements.txt` with `./scripts/compile_requirements.sh` after changing it. That script treats the checked-in `requirements.txt` as current unless `requirements.in` is clearly newer by more than a small timestamp epsilon; use `--force` to regenerate unconditionally.

On Linux, `GDAL` is installed separately by `./scripts/bootstrap.sh` as `GDAL==$(gdal-config --version)` so the Python bindings match the system `libgdal`. The bootstrap script then installs from the checked-in `requirements.txt`, installs `moonlayers_pkg` editable, builds frontend assets, installs the required spaCy model, and runs `scripts/verify_env.py`. It only regenerates `requirements.txt` automatically if it is missing; use `./scripts/bootstrap.sh --refresh-requirements` to force a refresh on Linux.

For the full setup flow and available bootstrap flags, see `docs/DEVELOPER_SETUP.md`.

### 2) Build `moonlib` (x64 Debug)

```powershell
dotnet build native/new_horizon/moonlib/moonlib.csproj -c Debug -p:Platform=x64 -v minimal
```

The default config points native bootstrap at:

- `native/new_horizon/moonlib/bin/x64/Debug/net9.0/moonlib.dll`

### 3) Start backend (Host-Native Linux)

For the current Pop!_OS host-native baseline:

```bash
./scripts/run-host-dev.sh
```

### 4) Build frontend assets (when UI code changes)

From repo root:

```bash
npm run build:map
```

Or directly:

```bash
cd backend/web/lunar_analyst
npm run build
```

## Common Developer Commands

### Python tests (backend)

```bash
.venv/bin/python -m pytest backend/tests/worker/test_hillshade_job_flow.py -q
.venv/bin/python -m pytest backend/tests/contract -q
```

### .NET tests (native)

```bash
dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj --filter LightmapArrayStreamingBridgeTests -v minimal
```

### Frontend tests (Vitest)

From repo root:

```bash
npm run test -- src/__tests__/filterMatch.test.ts
```

Or from frontend directory:

```bash
cd backend/web/lunar_analyst
npm run test -- src/__tests__/filterMatch.test.ts
```

### Export contracts

```bash
.venv/bin/python -m backend.tools.export_openapi
.venv/bin/python -m backend.tools.export_contract_schemas
```

Generated files land in:

- `docs/contracts/generated/v1/openapi.json`
- `docs/contracts/generated/v1/*.schema.json`

## Testing Guide

For detailed automated and manual testing instructions (including assistant benchmark eval runs and scoring), see:

- `docs/HOW_TO_TEST.md`

## Jobs and Notebook Workflows

The Jobs Manager UI lists:

- typed system jobs (from `backend/jobs/handlers.py`)
- notebook jobs (discovered from configured notebook job roots / scenario scripts)

Examples:

- horizon generation
- hillshade
- PSR raster (`generate_psr_raster`)
- native temporal analytics (average sun fraction, Earth-above-terrain duration, combined Sun+Earth contiguous duration)

Notebook examples live in:

- `backend/notebook/examples/`

Notable examples:

- `backend/notebook/examples/psr_raster_native_mapops.mo.py`
- `backend/notebook/examples/lightmap_temporal_analytics_examples_index.mo.py`

## Lightmap Temporal Analytics (v2 Bridge)

The v2 lightmap bridge path supports:

- chunked signal streaming (`SignalStream`) for custom Python reducers
- native built-in temporal reductions (`NativeReduce`)
- `uint8` `sun_fraction` streaming
- `float32` explicit angle signals (Sun/Earth center margin)

This enables both:

- notebook-first custom reductions (LLM-authored Python)
- efficient native reductions for common scenarios

## PSR Raster Generation

PSR (permanent shadow / PSR-style) raster generation is exposed as a typed job:

- API route: `/api/v1/jobs/generate-psr-raster`
- handler: `JobHandlers.generate_psr_raster(...)`

If `dem_path`, `horizons_dir`, or `output_path` are left blank in the UI, defaults are inferred from the selected scenario:

- DEM: scenario resolver path (fallbacks include `primary_dem.tif` and `dem.tif`)
- Horizons: `lighting/horizons`
- Output: `lighting/psr.tif`

## Configuration

Primary config:

- `config/lunar_analyst.toml`
- `config/lunar_analyst.devcontainer.toml`
- `config/lunar_analyst.container.toml`

Phase A container alignment:

- Dev/container runtime state is expected under the configured `workspace_root`, not under the repo checkout.
- `config/lunar_analyst.devcontainer.toml` targets bind-mounted repo development with container-shaped paths.
- `config/lunar_analyst.container.toml` targets immutable-image runtime behavior with a mounted workspace volume.

## Container Dev Workflow

Phase B adds a Docker-based development workflow that complements host-native Pop!_OS development.

For the host-native Linux development loop, use:

```bash
./scripts/run-host-dev.sh
```

Default host-native script behavior:

- config: `config/lunar_analyst.toml`
- workspace root: `${LUNAR_ANALYST_WORKSPACE_ROOT:-/e/lunar_analyst_scenarios}`
- host/port: `${LUNAR_ANALYST_HOST:-127.0.0.1}:${LUNAR_ANALYST_PORT:-8000}`

Build the Phase A base image and the Phase B dev image:

```bash
./scripts/docker-build.sh
```

Start the bind-mounted dev container and open a shell:

```bash
./scripts/docker-run-dev.sh
```

This drops you directly into an interactive shell inside a one-off dev container.
You should see a prompt like `lunar@<container-id>:/workspace/lunar_analyst$`.
When you exit that shell, the container is removed.

Run the Phase B smoke checks:

```bash
./scripts/docker-smoke.sh
```

Or, from inside the interactive dev shell:

```bash
/usr/local/bin/docker-smoke.sh
```

Tear down the dev compose resources explicitly when needed:

```bash
./scripts/docker-down.sh
```

By default the dev compose workflow bind-mounts:

- the repo checkout to `/workspace/lunar_analyst`
- `${LUNAR_ANALYST_HOST_WORKSPACE_ROOT:-/e/lunar_analyst_scenarios}` to `/var/lib/lunar-analyst/workspace`
- container writes run as your host UID/GID so bind-mounted files remain editable on the host

Override `LUNAR_ANALYST_HOST_WORKSPACE_ROOT` before `docker compose` if your host workspace lives elsewhere.

## Container Runtime Workflow

Phase C adds the immutable runtime image used for local production-style checks and NRP deployment packaging.

Build the base, dev, and runtime images:

```bash
./scripts/docker-build.sh
```

Run the runtime image with only the workspace mounted:

```bash
./scripts/docker-run-runtime.sh
```

Default runtime behavior:

- image config path: `/opt/lunar-analyst/config/lunar_analyst.container.toml`
- mounted workspace root: `${LUNAR_ANALYST_RUNTIME_WORKSPACE_ROOT:-${LUNAR_ANALYST_HOST_WORKSPACE_ROOT:-/e/lunar_analyst_scenarios}}` -> `/var/lib/lunar-analyst/workspace`
- no bind-mounted git checkout
- built frontend assets are served by FastAPI from `backend/web/lunar_analyst/dist`

Run the Phase C runtime smoke path:

```bash
./scripts/docker-runtime-smoke.sh
```

This validates:

- immutable-image backend startup
- frontend asset serving
- scenario/catalog persistence across restart
- global RAG DB persistence under `.assistant/rag/global_rag.db`
- a representative raster job writing under the scenario root

First-slice NRP manifests now live under `deploy/nrp/`.
Use `deploy/nrp/namespace-notes.md` as the apply/secret/persistence guide.

Important sections:

- `[backend]` workspace root, logging
- `[backend.native]` moonlib + runtime bootstrap settings
- `[backend.native.dll_resolver]` strict native DLL loading (GDAL/PROJ/sqlite)
- `[backend.marimo]` Marimo auto-start and Python executable
- `[backend.notebook_jobs]` job discovery roots
- `[backend.scenario_discovery]` startup scenario discovery/reconciliation

## Troubleshooting

### `Native DLL 'sqlite3.dll' is already loaded ...`

Cause:

- `moonlib`/GDAL native bootstrap is being attempted in a process that already loaded a different `sqlite3.dll` (often the Python process).

Fix:

- Use isolated subprocess/worker execution for native jobs (the PSR job already does this).
- Ensure you use a single consistent native build root (`x64 Debug` vs `Debug` mixed outputs can cause issues).

### `MoonlibBridge` missing a method after C# changes

Cause:

- Python code reloaded, but `moonlib.dll` was not rebuilt.

Fix:

```powershell
dotnet build native/new_horizon/moonlib/moonlib.csproj -c Debug -p:Platform=x64 -v minimal
```

Then restart the backend.

### Frontend changes not appearing

If you are serving built assets (not a frontend dev server), rebuild:

```powershell
npm run build:map
```

Then hard refresh the browser (`Ctrl+F5`).

## Further Reading

- `AGENTS.md` project architecture invariants and collaboration protocol
- `backend/README.md` backend contracts and API details
- `docs/EXTENDING_LIGHTMAP.md` design/implementation notes for lightmap bridge v2 extensions
- `docs/contracts/README.md` generated contract artifacts
