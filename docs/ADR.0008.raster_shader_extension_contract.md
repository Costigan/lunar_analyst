# ADR 0008: Deferred Custom Shader Extension Contract for Raster Rendering

- Status: Accepted
- Date: 2026-02-16
- Deciders: Lunar Analyst architecture/design owners
- Related: `docs/ADR.0007.client_side_raster_styling_and_colormaps.md`, `docs/PLAN.md`, `backend/web/lunar_analyst/src/App.tsx`

## Context

Phase 3 implements raster styling through OpenLayers `WebGLTile` expressions and style variables (colormap, opacity, brightness, contrast). This satisfies current requirements.  
Future products may require advanced rendering not practical in expression-only style pipelines (multi-band transfer functions, data-dependent effects, domain-specific visual encodings).

We need an explicit extension contract now so future shader work is additive, controlled, and reversible.

## Decision

1. Keep expression/style-variable rendering as the default runtime path.
2. Define custom shader rendering as an opt-in capability behind explicit per-layer style metadata.
3. Require deterministic fallback to expression rendering when shader activation fails or is unsupported.
4. Treat shader programs as versioned artifacts with declared inputs and compatibility metadata.

## Extension Contract

`LayerState.style` may include an optional `shader` object:

```json
{
  "shader": {
    "enabled": false,
    "shader_id": "example_shader",
    "schema_version": "1",
    "uniforms": {},
    "requires": {
      "webgl2": true,
      "bands": 1
    }
  }
}
```

Contract rules:

- `enabled` defaults to `false`.
- `shader_id` must resolve to a known shader registry entry.
- `schema_version` is required for forward compatibility.
- `uniforms` is JSON-only data; binary payloads are not allowed.
- `requires` defines minimum client capabilities.

Activation rules:

1. `enabled == true`.
2. Shader registry contains `shader_id`.
3. Client capability check passes (`requires`).
4. Shader compile/link succeeds.

Fallback behavior:

- Any activation failure must log a structured warning and revert to expression rendering using current `colormap`/`brightness`/`contrast` style keys.
- Fallback is non-fatal and must preserve layer visibility.

## Consequences

Positive:

- Keeps current rendering stable while preserving a clean path for advanced shaders.
- Prevents silent failures by forcing explicit fallback semantics.
- Allows incremental shader rollout by scenario/product.

Tradeoffs:

- Adds style-schema governance burden.
- Requires capability probing and shader registry lifecycle management when implemented.

## Out of Scope

- Shader editor UI.
- Full shader registry implementation.
- Persisted per-user shader preference management.

## Follow-on Tasks

- Add JSON schema for `LayerState.style.shader`.
- Add capability-probe helper in web client.
- Add integration tests for shader activation and fallback behavior.


