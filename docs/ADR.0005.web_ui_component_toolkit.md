# ADR 0005: Web UI Component Toolkit for Layer Controls

- Status: Superseded by [[ADR 0010: Adoption of Blueprint JS 6 for Application Shell and Desktop Controls]]
- Date: 2026-02-15
- Deciders: Lunar Analyst architecture/design owners
- Related: `docs/NEW_DESIGN.md`, `docs/PLAN.md`, `backend/web/lunar_analyst/`, `docs/ADR.0010.blueprint_ui_toolkit.md`

## **NOTE: This ADR has been superseded.**
The primary UI toolkit direction for the application shell and desktop controls has moved to **Blueprint JS 6**. Shoelace remains in use only for specialized map-control-only components where already stable.

## Context

The browser map milestone needs immediate user controls for:
- layer visibility,
- opacity/transparency,
- brightness, and
- contrast.

Controls must work in the current browser map stack (OpenLayers + vanilla JavaScript) and remain compatible with a Vite-based frontend path. The toolkit should be framework-agnostic, accessible, and suitable for incremental expansion.

## Decision

Adopt **Shoelace 2.20.1** (`@shoelace-style/shoelace`) as the current UI web component toolkit for layer controls, with a planned migration path to **Web Awesome 3** (`@awesome.me/webawesome`) after map milestone stabilization.

Scope of this ADR:
- control primitives (`sl-switch`, `sl-range`, related form controls) for map layer UX,
- bundler-based import usage in vanilla JS,
- no immediate design-system-wide rewrite.

## Rationale

- Framework-agnostic web components align with current vanilla JS map code and future React/Tauri web client plans.
- Shoelace 2 is stable and widely used, suitable for low-risk near-term delivery.
- The project lineage continues as Web Awesome 3; adopting Shoelace 2 now preserves a practical migration path instead of forcing immediate beta adoption.
- OpenLayers `WebGLTile` style variables natively support dynamic brightness/contrast updates, so the control toolkit can focus on input UX while map rendering remains in OpenLayers.

## Alternatives Considered

### Web Awesome 3 now
- Pros: active development line.
- Cons: currently beta package line; adds migration churn while map rendering issues are still being stabilized.

### Spectrum Web Components
- Pros: mature, accessible component set.
- Cons: stronger Adobe Spectrum design coupling than needed for a focused map control panel.

### FAST
- Pros: strong web component foundation.
- Cons: better suited to building custom components than quickly shipping a polished ready-made control panel.

### Vaadin components
- Pros: robust component ecosystem.
- Cons: broader enterprise framework orientation than required for this lightweight map UI layer.

## Consequences

Positive:
- Faster delivery of usable map controls.
- Accessible, consistent controls without committing to a framework lock-in.
- Clear near-term path for visibility/opacity/brightness/contrast controls.

Tradeoffs:
- Future migration effort to Web Awesome naming/package conventions is expected.
- Two-lineage awareness (Shoelace 2 now, Web Awesome later) must be documented and managed.

## Out of Scope

- Implementing the controls in code.
- Resolving current GeoTIFF rendering visibility issues.
- Defining a full application-wide design system.

## Follow-on Tasks

- Add layer control panel in map milestone UI using `sl-switch`/`sl-range`.
- Wire controls to OpenLayers layer properties (`visible`, `opacity`) and `WebGLTile` style variables (`brightness`, `contrast`).
- Add manual verification checklist for control behavior and cross-browser rendering.
- Reassess migration timing to Web Awesome after GeoTIFF and layer-state workflows are stable.

## Evidence (Web Research)

- Shoelace repository note on transition to Web Awesome:
  - https://github.com/shoelace-style/shoelace
- Shoelace npm package status/version:
  - https://www.npmjs.com/package/%40shoelace-style/shoelace
- Web Awesome repository and docs:
  - https://github.com/shoelace-style/webawesome
  - https://webawesome.com/docs/components/slider/
- OpenLayers WebGLTile style variables (brightness/contrast, updateStyleVariables):
  - https://openlayers.org/en/latest/apidoc/module-ol_layer_WebGLTile.html
- Spectrum Web Components slider docs:
  - https://opensource.adobe.com/spectrum-web-components/components/slider
- FAST docs:
  - https://fast.design/

