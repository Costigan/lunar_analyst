# Phase 0.5 Step 1: Legacy Code/Asset Inventory

Date: 2026-02-14

## Scope
Inventory of legacy code/assets across:
- `D:\projects\lunar_analyst`
- `D:\projects\lunar_analyst_net`
- `D:\projects\new_horizon`
- `D:\projects\moonlayers`
- `D:\projects\lunarsiteeval`

This step is inventory-only. Classification/migration decisions are not included here.

Project-scope clarifications captured for later Phase 0.5 steps:
- Runtime/native calls will target `moonlib` inside `new_horizon`.
- Other `new_horizon` projects are reference material only (usage/examples), not wrapping targets.
- `moonlayers` AnyWidget `MoonMap` is intended to be reused as-is.
- `moonlayers/moonlayers/geotiff_server.py` is reference-only; rewrite is acceptable.

## Repo: `lunar_analyst`

### Top-level purpose
- Current Python-first backend/contracts workspace.

### Key code areas
- `backend/api/` (FastAPI app, routing, error handling, runtime job adapter)
- `backend/contracts/` (Pydantic contracts, WS envelope, decorators/types)
- `backend/jobs/handlers.py` (typed job handler signatures)
- `backend/services/interfaces/` (typed service interfaces)
- `backend/tests/contract/` (Stage 1 contract tests)
- `backend/tools/` (OpenAPI/schema exporters)

### Key docs/contracts
- `docs/PLAN.md`
- `docs/API_CONTRACT.md`
- `docs/contracts/openapi.v1.stage1.yaml`
- `docs/contracts/schemas/v1/*.schema.json`
- `docs/contracts/generated/v1/*`

## Repo: `lunar_analyst_net`

### Top-level areas
- `mapviewer/` (WinForms/.NET map client; `MapViewer.csproj`)
- `moonlayers/` (Python package + examples/tests for map/notebook workflows)
- `test_mapviewer/` (`test_mapviewer.csproj`, fixture-style test data)
- `docs/`, `old_docs/`, design/implementation markdowns

### Source inventory (excluding `bin/`, `obj/`, `.git/`, `.vs/`, `TestResults/`)
- `mapviewer`: 46 `*.cs`/`*.py` files
- `moonlayers`: 27 `*.cs`/`*.py` files
- `test_mapviewer`: 5 `*.cs`/`*.py` files

### Notable sample assets
- `moonlayers/data/pr_repositioning_hillshade.tif`
- `test_mapviewer/test_data/pr_repositioning_hillshade.compressed.tif`
- `test_mapviewer/test_data/A3_Named_regions_SP.geojson`
- `test_mapviewer/test_data/labels_lonlat.json`

## Repo: `new_horizon`

### Top-level areas
- `moonlib/` (core compute library; `moonlib.csproj`)
- `corelib/`, `corelib1/` (supporting numeric/compute code)
- `horizon_runner/` (runner executable)
- `CompareHorizons/` (comparison tooling)
- `tests/HorizonGen.Tests/` (test project)
- `analysis/`, `python/` (analysis/debug scripts)
- `data/` and many output directories (`output_*`, `horizons_2dems/`)

### Source inventory (excluding `bin/`, `obj/`, `.git/`, `.vs/`, `TestResults/`)
- `corelib1`: 90 `*.cs`/`*.py` files
- `moonlib`: 60 `*.cs`/`*.py` files
- `tests`: 28 `*.cs`/`*.py` files
- `corelib`: 27 `*.cs`/`*.py` files
- `analysis`: 16 `*.cs`/`*.py` files
- `CompareHorizons`: 14 `*.cs`/`*.py` files
- `python`: 13 `*.cs`/`*.py` files

### Notable sample assets (non-build outputs present in repo)
- Raster/example outputs: 888 `*.tif`
- Horizon binaries: 1604 `*.bin`
- Additional horizon/cache binaries: 1595 `*.cbin`
- Example paths:
  - `permanent_shadow_map.tif`
  - `horizons_2dems/horizon_00000_00000_000.bin`

### Integration note
- `moonlib` is the only planned runtime call surface from this repo.
- `corelib`, `corelib1`, `horizon_runner`, and `CompareHorizons` remain reference/example sources unless later phases explicitly expand scope.

## Repo: `moonlayers`

### Top-level areas
- `moonlayers/` (Python package; includes `MoonMap` widget and `geotiff_server.py`)
- `src/` (frontend/widget JS/TS source)
- `examples/` (marimo notebook examples, including `*.mo.py`)
- `tests/` (widget and layer tests)

### Source inventory (excluding `node_modules/`, `dist/`, `.git/`, caches)
- `examples`: 11 `*.py`/`*.ts`/`*.tsx`/`*.js` files
- `src`: 10 `*.py`/`*.ts`/`*.tsx`/`*.js` files
- `tests`: 6 `*.py`/`*.ts`/`*.tsx`/`*.js` files
- `moonlayers`: 5 `*.py`/`*.ts`/`*.tsx`/`*.js` files

### Notable files
- `moonlayers/moon_map.py` (defines `MoonMap(anywidget.AnyWidget)`)
- `moonlayers/geotiff_server.py` (embedded HTTP file server with CORS + Range support)
- `examples/geotiff_demo.mo.py`
- `examples/geotiff_http_demo.mo.py`
- `examples/south_pole_demo.mo.py`
- `examples/test_widget_ready.mo.py`

### Integration note
- `MoonMap` widget is a direct reuse candidate.
- `geotiff_server.py` behavior is useful reference; implementation can be replaced in FastAPI asset serving flow.

## Repo: `lunarsiteeval`

### Top-level areas
- `src/lunarsiteeval/` (Python package modules for dataset access/evaluation)
- `tests/` (pytest suite, GDAL/projection/pythonnet-related tests)

### Source inventory (excluding `.git/`, caches)
- `src`: 13 `*.py`/`*.ipynb` files
- `tests`: 6 `*.py`/`*.ipynb` files

### Notable files
- `src/lunarsiteeval/lunar_dataset.py` (dataset structure, GDAL-based raster/projection handling)
- `src/lunarsiteeval/projection_info.py` (projection metadata helpers)
- `src/lunarsiteeval/site_analyzer.py`
- `src/lunarsiteeval/evaluator.py`
- `tests/test_projection_info.py`
- `tests/test_lunar_dataset.py`
- `tests/test_pythonnet`

### Integration note
- This repo is a useful Python reference for dataset/projection handling patterns and tests.
- No direct wrapping commitment is implied by this inventory step.

## Notes
- `new_horizon` contains substantial checked-in output artifacts alongside source; later steps should separate fixture candidates from non-canonical outputs.
- `lunar_analyst_net` contains smaller, focused fixture candidates under `test_mapviewer/test_data/` and `moonlayers/data/`.
- `moonlayers` should be treated as an active integration source (widget + marimo examples), not just a legacy reference.
