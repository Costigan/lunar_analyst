# Phase 0.5 Step 4: Data/Model Differences and Adapter Plan

Date: 2026-02-14
Inputs:
- `docs/PHASE0_5_LEGACY_INVENTORY.md`
- `docs/PHASE0_5_COMPONENT_CLASSIFICATION.md`

## Scope
Document concrete data/model mismatches between legacy sources and the current Stage 1 backend contracts, and define required adapter behavior.

## Canonical Targets (Current Repo)
- API contracts: `backend/contracts/models.py`
- Scenario filesystem conventions: `docs/ADR.0002.scenario_filesystem_and_catalog.md`
- Typed job signatures: `backend/jobs/handlers.py`

## Differences and Required Adapters

| Area | Legacy pattern | Target pattern | Required adapter |
|---|---|---|---|
| Primary DEM naming | Legacy uses names like `dem.tif` or project-specific names | Canonical scenario DEM path is `dem.tif` | Import adapter normalizes DEM designation in metadata and records source filename/lineage details. |
| Scenario layout | `lunarsiteeval` expects many fixed subdirs (`other`, `sun`, `horizon`, etc.) | Scenario layout in ADR.0002 with managed product registry | Filesystem adapter maps legacy folder semantics to product `kind/subkind` and file records; no direct directory-coupled runtime assumptions. |
| Horizon artifacts | `new_horizon` contains `*.bin`/`*.cbin` with legacy naming conventions | Product-file records keyed by product/file IDs under scenario root | Horizon adapter registers binary artifacts as product files and stores parseable metadata (grid size, azimuth step, CRS linkage). |
| CRS metadata | Legacy code may infer from GDAL file opens or implicit workflow assumptions | Explicit CRS metadata persisted and surfaced in API contracts | CRS adapter extracts/proves CRS at import/registration time and rejects unknown CRS unless explicitly marked diagnostic. |
| Job submission shape | Legacy runners/scripts often use CLI args or script-local params | Signature-first typed methods in `JobHandlers` auto-published as API routes | Job adapter maps external/legacy parameter names to typed method args, with validation before enqueue/dispatch. |
| Time format | Legacy timestamps vary (`YYYYMMDDTHHMMSSZ`, file mtimes, etc.) | UTC `YYYY-MM-DDTHH-MM-SS` (no `Z`) | Time adapter normalizes persisted/display timestamps to canonical format and stores raw source timestamps only in lineage/details. |
| Raster serving | `moonlayers/geotiff_server.py` serves direct file paths over embedded HTTP | FastAPI file-ID mapped serving with root allowlist and traversal protection | Asset adapter resolves file IDs to validated scenario-root paths and supports HTTP range; never expose raw absolute paths. |
| Map rendering coupling | Legacy map code may own layer config directly in widget examples | Backend `layer_state` is source-of-truth; map clients consume REST/WS | Layer adapter converts notebook/widget operations into API calls and WS-observable state transitions. |
| Error handling | Mixed exceptions/messages across scripts/services | Stage 1 error envelope: `code`, `message`, `details`, `request_id` | Error adapter wraps validation/runtime failures into canonical envelope; preserve original exception text in `details`. |

## Adapter Implementation Boundaries

- `pythonnet` boundary:
  - Only `moonlib` is called as a runtime dependency.
  - Other `new_horizon` projects remain reference-only.
- Widget boundary:
  - `MoonMap` is reused as-is.
  - File serving behavior from `geotiff_server.py` is reimplemented behind FastAPI contracts.
- Notebook boundary:
  - Marimo code drives map changes through FastAPI only; no direct DB mutation from notebooks.

## Immediate Adapter Priorities

1. `job_param_adapter`:
   - Bridge legacy parameter names to typed `JobHandlers` args.
   - Emit clear validation errors in Stage 1 envelope.
2. `filesystem_product_adapter`:
   - Normalize imported files to scenario-root conventions.
   - Register all files by product/file ID mapping.
3. `crs_metadata_adapter`:
   - Validate/extract CRS on import.
   - Persist CRS + footprint for map hit-testing and layer registration.
4. `asset_serving_adapter`:
   - Implement secure file-ID serving with range support for raster clients.

## Out of Scope for This Step

- Implementing adapters in code.
- Selecting fixture datasets (explicitly deferred).
- Adding migration tooling for full legacy project import.
