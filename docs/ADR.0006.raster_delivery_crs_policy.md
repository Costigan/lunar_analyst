# ADR 0006: Raster Delivery CRS Policy for Web Map Clients vs Notebook Image Views

- Status: Accepted
- Date: 2026-02-16
- Deciders: Lunar Analyst architecture/design owners
- Related: `docs/ADR.0002.scenario_filesystem_and_catalog.md`, `docs/SCENARIO.md`, `docs/PLAN.md`

## Context

Lunar Analyst serves raster products to multiple client modes:
- OpenLayers-based map clients (browser milestone UI and AnyWidget map in Marimo), and
- general notebook image display in Marimo (non-map raster presentation).

Map clients require consistent layer alignment in the map CRS (south polar stereographic), while notebook image display often needs direct analysis artifacts without display-driven CRS mutation.

## Decision

1. Raster products delivered to OpenLayers map clients MUST be in map display CRS `ESRI:103878` (Lunar South Pole Stereographic).
2. If a source product is not already in `ESRI:103878`, the backend must produce and serve a warped display derivative for map usage.
3. Analysis artifacts remain in native/source CRS unless explicitly transformed for analysis purposes.
4. General Marimo image presentation (non-map display) may use native/source-CRS rasters without reprojection.
5. Warped map-display derivatives must be tracked as derived artifacts with lineage/metadata in scenario state.

## Rationale

- Ensures deterministic map overlay behavior across OpenLayers clients.
- Avoids client-side projection parsing/reprojection variability for lunar/custom CRS inputs.
- Preserves scientific/analysis fidelity by keeping primary analysis artifacts in native CRS.
- Keeps map rendering concerns separate from analysis data lifecycle.

## Consequences

Positive:
- Reliable layer alignment for web map experiences.
- Clear contract for when warping is required.
- Better reproducibility and auditability through explicit derived lineage.

Tradeoffs:
- Additional storage and compute cost for warped derivatives.
- Requires derivative lifecycle management (cache invalidation/refresh rules).

## Out of Scope

- Exact GDAL pipeline flags and performance tuning.
- Full cache key/eviction strategy details.
- Detailed API endpoint naming for map-display derivative access.

## Follow-on Tasks

- Implement server-side warp-to-`ESRI:103878` path for map-facing raster delivery.
- Register warped outputs in scenario metadata as display derivatives with lineage to source product.
- Ensure OpenLayers map endpoints/layer state reference warped derivative assets.
- Add tests for CRS policy: map endpoints serve `ESRI:103878` derivatives; non-map notebook image flows keep native CRS.
