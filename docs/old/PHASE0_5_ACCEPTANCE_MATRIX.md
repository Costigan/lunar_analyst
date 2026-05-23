# Phase 0.5 Step 5: Legacy Capability Acceptance Matrix

Date: 2026-02-14
Inputs:
- `docs/PHASE0_5_COMPONENT_CLASSIFICATION.md`
- `docs/PHASE0_5_DATA_MODEL_ADAPTERS.md`

## Scope
Map legacy capabilities to the phase where each is accepted into the new implementation, including minimum acceptance evidence.

## Acceptance Matrix

| Legacy capability | Legacy source | New target | Planned phase | Minimum acceptance evidence |
|---|---|---|---|---|
| Horizon generation compute | `new_horizon/moonlib` | Worker job via `pythonnet` | Phase 1 | Integration test: native load + one horizon job request reaches terminal state with progress and cancellation checkpoints. |
| Horizon/lighting artifact registration | `new_horizon` output conventions | FastAPI product/file registration | Phase 2 | API test: generated artifacts registered as `Product` + file records with CRS/footprint metadata. |
| Secure raster file serving | `moonlayers/geotiff_server.py` behavior (reference) | FastAPI file-ID asset endpoints | Phase 2 | Security tests: path traversal/out-of-root rejected; range request works by file ID only. |
| Web map raster rendering | `mapviewer` + `moonlayers` patterns | React/OpenLayers `WebGLTile` layers | Phase 3 | E2E smoke: registered raster appears in map with layer controls and correct visibility updates. |
| Notebook-driven map interaction | `moonlayers` marimo examples | Marimo client using FastAPI REST/WS | Phase 4 | Integration test: notebook helper mutates layer/job state through API and map updates via WS events. |
| `MoonMap` AnyWidget usage | `D:\projects\moonlayers\moonlayers\moon_map.py` | Reused widget in marimo workflows | Phase 4 | Manual verification + integration example showing `MoonMap` bound to backend-driven layers/events. |
| Legacy parameter naming/unit compatibility | `new_horizon` scripts and runners | Adapter layer in typed job interface | Phase 4.5 | Tests for parameter alias mapping and explicit conversion behavior with clear validation failures. |
| Legacy project/data import | Legacy scenario/data artifacts | Import pipeline to scenario layout + SpatiaLite | Phase 4.5 | Fixture migration tests proving normalized paths, metadata integrity, and repeatable import behavior. |
| Dataset/projection helper logic | `lunarsiteeval` (`lunar_dataset`, `projection_info`) | Python adapters/services | Phase 4.5+ | Unit tests for CRS extraction, projection metadata, and scenario alignment checks. |
| Advanced analytics helpers | `lunarsiteeval` evaluator/analyzer modules | New analytics feature set | Phase 5+ | Feature-specific tests + documented intentional behavior differences where parity is not exact. |

## Non-Accepted Runtime Paths

- `mapviewer` WinForms runtime is not accepted into new runtime topology (retired).
- `new_horizon` utility executables (`horizon_runner`, `CompareHorizons`) are not runtime dependencies.
- `moonlayers/geotiff_server.py` is not accepted as deployed serving path; FastAPI file-ID serving is required.

## Exit Criteria for Phase 0.5

1. Inventory document exists and is current.
2. Component classification is explicit.
3. Data/model adapter requirements are documented.
4. Acceptance matrix maps legacy capabilities to phases and objective evidence.
5. Fixture-data-dependent work is either complete or explicitly deferred with owner decision.
