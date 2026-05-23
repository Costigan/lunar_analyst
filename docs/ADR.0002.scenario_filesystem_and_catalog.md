# ADR 0002: Scenario Filesystem and Catalog Conventions

- Status: Accepted
- Date: 2026-02-14
- Owners: Architecture (Codex), Implementation (Gemini)
- Related: `docs/ADR.0001.process_model.md`, `docs/PLAN.md`, `AGENTS.md`

## Context
Phase 0 requires finalized scenario filesystem conventions that support:
- self-contained scenario storage,
- fast catalog/map browsing across many scenarios,
- safe file serving and path normalization,
- map rendering workflows where map CRS can differ from scenario CRS,
- efficient cached display artifacts for web clients.

## Decision

### 1) Workspace and Global Catalog
A managed workspace root contains scenario directories and one global SpatiaLite catalog DB:
- `{workspace_root}/scenario_catalog.db`

The global catalog tracks scenario discovery and lightweight metadata used for map/table UX.

### 2) Scenario Root Naming
`scenario_root` is a short filesystem-safe slug used as directory name:
- Regex: `^[a-z0-9][a-z0-9_-]{2,31}$`
- Length: 3-32
- Lowercase only
- No spaces or dots

### 3) Scenario Layout
Each scenario is self-contained and includes only `scenario.db` plus files:

```text
{workspace_root}/
  scenario_catalog.db
  {scenario_root}/
    scenario.db
    dem.tif
    hillshade.tif
    {product_or_artifact_files...}
    {time_series_name}/
      {time_series_files...}
```

Notes:
- The scenario root directory is intentionally free-form for product/artifact placement.
- Product type classification is authoritative in `scenario.db` metadata, not directory structure.
- Top-level typed folders such as `products/{kind}`, `lighting/`, `vectors/`, `imports/`, `exports/` are not required.
- Time-series outputs should be grouped in a directory named for the time-series set.
- Imported files are copied once into scenario-managed storage and registered as products (`producer='import'`).

### 4) Time Format
All timestamps are UTC and formatted as:
- `YYYY-MM-DDTHH-MM-SS`

The `Z` suffix is omitted by policy because UTC is implicit.

### 5) Primary DEM Convention
Primary DEM path is fixed to:
- `dem.tif`

### 5.1) Canonical Hillshade Convention
Canonical default hillshade for the primary DEM:
- `hillshade.tif`

### 6) Global Catalog Schema Requirements
Global catalog stores scenario metadata for map/table browsing, including:
- identity: `scenario_id`, `scenario_root`, `name`, `owner`
- location: absolute `directory`, relative `dem_path`
- metadata: `last_touched_utc` (cached), `size_bytes` (cached), `created_utc`, `updated_utc`
- geometry: DEM footprint polygon for map rendering/click-select

Map interaction uses polygon hit-tests against scenario footprint geometries.

### 7) CRS and Display Reprojection
- Scenario CRS is defined by the primary DEM and persisted.
- Map view CRS may differ from scenario CRS.
- Reprojection for display is allowed/expected and must be explicit.
- Reprojected sibling products may append `_reprojected` before extension.
- Reprojected and display-only outputs are tracked as derived products with lineage.

### 8) Display-Optimized Product Cache
System supports display-only derivatives for efficient serving when source analysis products are not directly displayable:
- colormap/stretch applied outputs
- reprojected outputs
- pyramid/tiled variants for large rasters

Display cache artifacts are managed in scenario filesystem (without requiring type-based directory trees) and registered in metadata with transform parameters hash.

### 8.1) Product File Naming Policy (Normative)
Scenario file naming policy is split into:
- this ADR section (normative rules and constraints),
- `docs/SCENARIO_FILE_NAMING.md` (detailed implementation templates and examples).

Normative rules:
- Reserved root filenames:
  - `scenario.db`
  - `dem.tif`
  - `hillshade.tif`
- Reserved root directories:
  - `display/` (display-only derivatives, cache-managed artifacts)
- Product classification is authoritative in metadata (`products`, `product_files`), not folder names.
- Paths persisted in DB must be scenario-root-relative.
- All generated filenames must be deterministic from stable inputs plus collision-safe handling where required.
- Time-bearing names must use UTC `YYYY-MM-DDTHH-MM-SS` tokens (no `Z` suffix).
- Hash-bearing names must use lowercase hex and fixed truncation length per naming spec.
- Collision behavior must be explicit and non-destructive (suffix allocation, never implicit overwrite unless explicitly requested by operation policy).
- Legacy-imported names may be preserved only when path safety and normalization rules are satisfied; canonical naming may be applied by import adapters.

Implementation authority:
- File-family templates, token definitions, and examples are defined in `docs/SCENARIO_FILE_NAMING.md`.
- Code paths generating files must conform to that document and should route through shared naming helpers.

### 9) Size Refresh Policy (`size_bytes`)
`size_bytes` refresh supports both startup/scheduled and on-demand modes:

1. Fast probe:
- compare cached directory mtime and sentinel mtimes (`scenario.db`, primary DEM)
- if unchanged, skip deep walk

2. Deep scan:
- if changed (or forced), walk scenario tree and recompute `size_bytes`
- update cached scan metadata

This is a performance optimization heuristic and not relied upon for correctness of scientific products.

### 10) File Safety Constraints
- Store file paths relative to scenario root in scenario DBs.
- Serve files by file-id mapping only.
- Normalize paths and reject out-of-root traversal.
- Reject absolute user-provided paths at API boundaries.

## Consequences
Positive:
- Consistent scenario portability and deterministic storage.
- Fast multi-scenario map/table UX from global catalog.
- Efficient display pipelines with explicit derived lineage.

Tradeoffs:
- Additional metadata maintenance for global catalog and display cache.
- Requires background refresh orchestration and invalidation rules.

## Out of Scope
- Full DDL definitions and migration scripts.
- Final REST/WS endpoint names for catalog APIs.
- Tile format and overviews implementation details.

## Follow-on Tasks
- Freeze global catalog and scenario DB schema specs.
- Add contract/integration tests for path safety and polygon hit-test selection.
- Implement startup scheduler + on-demand refresh for `size_bytes`.
- Define display-derivative cache key schema and eviction policy.
