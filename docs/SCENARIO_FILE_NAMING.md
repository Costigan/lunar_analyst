# Scenario File Naming Specification

Status: Draft (implementation target)
Date: 2026-02-16
Related:
- `docs/ADR.0002.scenario_filesystem_and_catalog.md`
- `docs/PLAN.md`

## 1. Purpose
Define concrete, implementation-level file naming conventions for scenario-managed products and derivatives.

This document provides executable guidance for code and tests. The normative policy remains in ADR 0002.

## 2. Scope
Applies to files stored under a scenario root directory and registered in `product_files`.

Out of scope:
- API route naming
- product ID / file ID generation formats
- external source path naming before import

## 3. Canonical Tokens

### 3.1 Time token
- Format: `YYYY-MM-DDTHH-MM-SS`
- UTC required
- Example: `2026-02-16T14-37-09`

### 3.2 Hash token
- Lowercase hex
- Default length: 12 chars
- Algorithm choice is implementation-specific but must be stable for same normalized inputs

### 3.3 Slug token
- Lowercase `a-z0-9_-`
- No spaces
- Trim repeated separators

## 4. Reserved Names and Directories

Reserved root filenames:
- `scenario.db`
- `dem.tif`
- `hillshade.tif`

Reserved root directories:
- `display/`

Do not create products with names that collide with these reserved entries.

## 5. Product Family Naming Patterns

### 5.1 Primary DEM
- Required canonical path: `dem.tif`

### 5.2 Canonical hillshade (primary DEM default)
- Canonical path: `hillshade.tif`

### 5.3 Imported rasters (current behavior compatibility)
- COG import: `{source_stem}.cog.tif`
- Native bypass import: `{source_stem}.native.tif`
- Collision suffix: `{name}.{N}{ext}` where `N` starts at `1`

### 5.4 DEM-derived products
Pattern:
- `{derivative_kind}.{hash12}.tif`

Examples:
- `slope.7a2f8d0e4b11.tif`
- `aspect.10b8d4c9a1ef.tif`
- `roughness.95c0e2bb9f2d.tif`
- `tpi.7d22d185f090.tif`
- `tri.3f6b7b8e1f42.tif`

Notes:
- `derivative_kind` must be one of approved kinds.
- Hash is computed from normalized compute parameters.

### 5.5 Horizon directory sets
Directory pattern:
- `horizons/{set_slug}.{hash12}/`

Inside directory:
- Preserve native tile naming from generator, for example `horizon_00000_00000_000.bin`

### 5.6 Horizon profile outputs
Pattern:
- `horizon_profile.{observer_slug}.{hash12}.{ext}`

Examples:
- `horizon_profile.x12345_y67890.1324bc9d03ae.json`
- `horizon_profile.site_a.1324bc9d03ae.csv`

### 5.7 LOS/viewshed outputs
Pattern:
- `viewshed.{observer_slug}.{hash12}.tif`

Example:
- `viewshed.rim_site_01.f25a91bd8b64.tif`

### 5.8 Lightmap time-series
Directory pattern:
- `time_series/{series_slug}.{hash12}/`

Frame pattern:
- `frame.{timestamp_utc}.tif`

Examples:
- `time_series/sunlight_q1_2031.72cb6630b0f3/`
- `time_series/sunlight_q1_2031.72cb6630b0f3/frame.2031-01-01T00-00-00.tif`

### 5.9 Display-only derivatives (map delivery)
Pattern (current implementation):
- `display/{product_id}/esri_103878/{source_stem}.{warp_hash}.cog.tif`

This path family is cache-managed and should be treated as derived artifacts.

## 6. Collision and Overwrite Rules

- Default mode: non-destructive write.
- If target path exists and overwrite is not explicit:
  - allocate suffix `.1`, `.2`, ... before extension.
- Explicit overwrite must be operation-specific and opt-in.
- Metadata lineage must record whether output was overwritten or suffix-allocated.

## 7. Registration Rules

- Always persist `relative_path` (scenario-root relative) in `product_files`.
- Never persist absolute paths in `product_files`.
- Every generated file must map to one `file_id`.
- For multi-file products, each file receives its own `file_id`; product grouping is via `product_id`.

## 8. Legacy Import Interoperability

- Import adapters may preserve source stems where safe.
- Unsafe or non-conforming names must be normalized on import.
- Original source path/name must be retained in lineage metadata.

## 9. Implementation Guidance

- Route naming through shared helpers rather than ad hoc string formatting in handlers.
- Validate token formatting at helper boundaries.
- Keep helper outputs deterministic for identical normalized input parameters.

## 10. Conformance Checklist

- File path does not escape scenario root.
- Name does not collide with reserved root names unless intentionally writing canonical reserved files (`dem.tif`, `hillshade.tif`).
- Time and hash tokens follow section 3 rules.
- `product_files.relative_path` matches actual created path.
- Collision handling follows section 6.
