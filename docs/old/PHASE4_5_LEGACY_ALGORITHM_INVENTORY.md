# Phase 4.5.1a: Legacy Algorithm Capability Inventory

Date: 2026-02-16

## Scope
First-cut algorithm inventory for Phase 4.5 migration planning, focused on legacy capabilities referenced by:
- `docs/NEW_DESIGN.md`
- `docs/PHASE0_5_LEGACY_INVENTORY.md`
- `docs/PHASE0_5_ACCEPTANCE_MATRIX.md`
- `native/new_horizon/moonlib/*`
- `backend/jobs/handlers.py` (current callable surface)

This inventory is intentionally contract-agnostic. It captures what exists and what is callable today versus reference-only.

## Capability Inventory

| Algorithm capability | Primary legacy source | Inputs (current shape) | Outputs | Units/CRS | Runtime constraints | Current callable state | Owner |
|---|---|---|---|---|---|---|---|
| Hillshade raster generation | `native/new_horizon/moonlib/MoonlibBridge.cs` (`GenerateHillshade`) | DEM GeoTIFF path, output GeoTIFF path | `hillshade.tif` | Raster values; CRS inherited from DEM metadata | Windows 11, Python 3.11, .NET 9, pythonnet bootstrap, GDAL/native deps available | Callable now via `JobHandlers.generate_hillshade` | Backend worker + native `moonlib` |
| Horizon directory generation (tiled horizon binaries) | `native/new_horizon/moonlib/MoonlibBridge.cs` (`GenerateHorizons`), `moonlib/horizon/*` | Scenario root dir, DEM path, output dir, overwrite flag, compression flag | `horizon_*.bin` / `horizon_*.cbin` files | 1440 azimuth samples (0.25 deg bins), elevation-angle style horizon data, tied to DEM CRS | Same as above; substantial IO footprint; long-running compute path | Callable now via `JobHandlers.generate_horizons` | Backend worker + native `moonlib` |
| Horizon profile at point/patch (in-memory horizon angles) | `moonlib/horizon/QuadTreeHorizonGenerator.cs`, `ReferenceHorizonGenerator.cs` | DEM list, tile/pixel location, observer elevation | Horizon angles array/profile | 1440 bins, degrees/radians depending on API, tied to DEM CRS | GPU/CPU path selection and DEM pyramid prep complexity | Reference-only from Python today (not exposed via `MoonlibBridge`) | Native `moonlib` |
| Lightmap/shadow time-slice generation from horizons + sun vectors | `moonlib/pipeline/LightmapPipeline.cs`, `moonlib/LightmapGenerator.cs` | Horizon files, DEM metadata, time/sun vectors, output path(s) | Light/shadow rasters, patch/time outputs | Solar geometry + raster grid CRS must be explicit | Long-running pipeline; high memory/throughput; cancellation/progress required | Reference-only from Python today | Native `moonlib` + backend worker integration |
| Permanent shadow map (PSR-style aggregate) | `moonlib/mapops/MapOperations.cs` (`GeneratePermanentShadowMap`) | DEM path, horizon directory, output raster path | Permanent shadow raster | Binary-like shadow map semantics; CRS inherited from DEM | Requires precomputed horizons + ephemeris path; expensive batch processing | Reference-only from Python today | Native `moonlib` + backend worker integration |
| LOS/viewshed derivations from horizon/ray emulation | `moonlib/horizon/ReferenceHorizonGenerator.cs` and ray emulators | Observer origin, DEM(s), max range/tiling controls | Visibility/horizon-derived coverage outputs | Angular horizon quantities mapped to raster/grid visibility outcomes | Algorithm options and tolerances not yet normalized in API contracts | Reference-only from Python today | Native `moonlib` + backend contracts owner |
| DEM-derived products: slope | Legacy analytics roadmap (`docs/PLAN.md`, `docs/NEW_DESIGN.md`) plus existing DEM processing patterns | DEM path, optional window/scale parameters, output path | `slope.tif` | Degrees or rise/run; CRS same as DEM | Must define canonical unit in contract | Not yet implemented as handler | Backend worker |
| DEM-derived products: aspect | Legacy analytics roadmap (`docs/PLAN.md`, `docs/NEW_DESIGN.md`) plus existing DEM processing patterns | DEM path, optional nodata handling, output path | `aspect.tif` | Degrees [0,360) convention to be fixed in contract | Must define nodata and wrap semantics | Not yet implemented as handler | Backend worker |
| DEM-derived products: roughness | Legacy analytics roadmap (`docs/PLAN.md`) | DEM path, neighborhood/window parameters | `roughness.tif` | Elevation-delta style units (to be fixed) | Requires fixed neighborhood contract | Not yet implemented as handler | Backend worker |
| DEM-derived products: TPI | User-added Phase 4.5 migration task in `docs/PLAN.md` | DEM path, neighborhood radius/shape parameters | `tpi.tif` | Elevation-relative index | Strong dependence on neighborhood specification | Not yet implemented as handler | Backend worker |
| DEM-derived products: TRI | User-added Phase 4.5 migration task in `docs/PLAN.md` | DEM path, neighborhood/window parameters | `tri.tif` | Ruggedness index | Method variant must be pinned in contract | Not yet implemented as handler | Backend worker |

## Coverage Notes

- Current Python-callable native bridge surface is limited to:
  - `GenerateHillshade`
  - `GenerateHorizons`
- Other legacy/native capabilities above are real in source but not yet exported through stable `JobHandlers` contracts.
- This is sufficient to proceed with `4.5.1b` prioritization and then `4.5.1c` signature drafting.

## Completeness Review Basis

Completeness was reviewed against currently documented legacy workflows/capabilities in `docs/NEW_DESIGN.md` and Phase 0.5 inventory/matrix docs, plus source inspection under `native/new_horizon/moonlib`. If additional mapviewer-only analysis paths are required, they should be appended before `4.5.1c` ratification.
