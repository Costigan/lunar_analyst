# ADR 0007: Client-Side Raster Colormaps and Style Controls for OpenLayers

- Status: Accepted
- Date: 2026-02-16
- Deciders: Lunar Analyst architecture/design owners
- Related: `docs/ADR.0005.web_ui_component_toolkit.md`, `docs/ADR.0006.raster_delivery_crs_policy.md`, `docs/PLAN.md`, `backend/web/lunar_analyst/`

## Context

Map users need interactive control over how raster values are rendered in OpenLayers, including:
- colormap selection,
- opacity,
- brightness,
- contrast,
- and future user-defined colormaps.

The current map stack uses OpenLayers `WebGLTile` with GeoTIFF/COG sources and Shoelace-based UI controls. We need a low-risk implementation path that works now, while preserving an option for advanced rendering later.

## Decision

1. Implement raster display styling primarily in the map client using OpenLayers `WebGLTile` style expressions and style variables.
2. Provide a standard built-in colormap set and expose selection in the layer UI.
3. Support user-defined colormaps loaded from both:
- application-level storage (shared defaults), and
- scenario-level storage (scenario-specific overrides/additions).
4. Expose per-layer controls for `opacity`, `brightness`, and `contrast` in the layer manager/control panel.
5. Keep custom shader implementations as a planned extension path for advanced rendering, not part of the initial implementation scope.

## Rationale

- OpenLayers already supports GPU-backed per-pixel styling and dynamic variable updates, enabling interactive UX without server-side recoloring.
- Client-side controls avoid generating extra server-side derivative rasters for each style change.
- Scenario-level colormap definitions support reproducible, portable visualization setups per analysis workspace.
- Deferring custom GLSL keeps near-term delivery simpler while preserving a technically valid path for future advanced effects.

## Alternatives Considered

### Server-side colorized raster derivatives
- Pros: deterministic rendered outputs, thin client logic.
- Cons: high storage/compute churn for style experimentation; slower interaction for exploratory work.

### Immediate custom shader pipeline
- Pros: maximum rendering flexibility.
- Cons: higher complexity, harder testing/debugging, larger initial implementation risk.

### Fixed grayscale-only rendering
- Pros: minimal implementation effort.
- Cons: insufficient for diverse scientific products and user workflows.

## Consequences

Positive:
- Fast interactive styling workflow in map UI.
- Clear user controls for common display needs.
- Extensible colormap model (standard + custom definitions).

Tradeoffs:
- Requires client-side validation and fallback behavior for malformed colormap definitions.
- Browser/GPU differences may require compatibility testing.

## Out of Scope

- Full custom GLSL shader authoring UI.
- Advanced transfer-function editor (histogram-driven curves, multidimensional LUTs).
- Persisting style presets across users/sessions beyond scenario/app storage policy.

## Follow-on Tasks

- Add colormap selector + opacity/brightness/contrast controls in map layer UI.
- Define JSON schema and storage resolution order for colormap definitions (app-level then scenario-level overrides).
- Wire controls to `WebGLTile` style variables with live updates.
- Add tests/manual checks for style updates across pan/zoom and layer toggles.
- Define a Phase 2+ extension point for custom shader-based renderers.

## Evidence

- OpenLayers `WebGLTile` API and style variables:
  - https://openlayers.org/en/latest/apidoc/module-ol_layer_WebGLTile-WebGLTileLayer.html
- OpenLayers expressions/style model:
  - https://openlayers.org/en/latest/apidoc/module-ol_expr_expression.html
- OpenLayers GeoTIFF source:
  - https://openlayers.org/en/latest/apidoc/module-ol_source_GeoTIFF.html

