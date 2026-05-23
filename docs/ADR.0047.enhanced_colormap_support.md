# ADR.0047: Enhanced Colormap Support and Raster Tone Semantics

- Status: Accepted
- Date: 2026-04-10
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0007.client_side_raster_styling_and_colormaps.md`, `docs/ADR.0008.raster_shader_extension_contract.md`, `AGENTS.md`

## Context

Lunar Analyst currently supports per-layer raster styling with:

- colormap selection,
- opacity,
- brightness,
- contrast,
- client-side OpenLayers WebGLTile expression rendering.

Colormap content is already merged from built-in and file-based sources, and layer style is persisted in scenario DB state. However, there are product and UX gaps:

- brightness/contrast behavior is not aligned with normal image-tone expectations across all layer types,
- colormap search precedence is underspecified,
- there is no default-colormap policy for newly created/imported layers based on filename patterns,
- assistant support for colormap-aware workflows is too implicit,
- advanced ideas are mixed with immediate deliverables.

## Problem

The current implementation blends together two different concepts under "brightness/contrast":

- data-domain remapping before colormap lookup, and
- display-tone adjustment after colorization.

Users expect "brightness" and "contrast" to behave like display-tone controls on the rendered image. Inconsistent semantics across raster and Trek overlays make layer controls feel unreliable.

Additionally:

- default colormap behavior at import/create time is ad hoc,
- there is no first-class rule set for mapping files to default colormaps,
- assistant tooling lacks explicit colormap operations,
- contour-line visualization is not represented as a first-class non-colormap style mode.

## Decision

### 1. Brightness and Contrast Semantics (In Scope)

Brightness and contrast controls will be defined as display-tone controls applied after colormap resolution (post-colormap RGB tone), and this behavior must be consistent for all map layer types that expose tone controls.

Implications:

- "Brightness" and "Contrast" labels refer only to post-colorization tone operations.
- Any pre-colormap scalar remapping remains a separate style concept and must not be surfaced as brightness/contrast.
- Existing layer style fields may be migrated in-place or reinterpreted, but runtime behavior must match the post-colormap contract.

Tone math contract (normative):

- Convert colormap output RGB channels to normalized `c` in `[0, 1]`.
- Apply post-colormap tone per channel:
  - `c_out = clamp(((c - 0.5) * contrast) + 0.5 + brightness, 0.0, 1.0)`
- `brightness` range: `[-1.0, 1.0]`, identity `0.0`.
- `contrast` range: `[0.0, 4.0]`, identity `1.0`.
- Alpha is unchanged by brightness/contrast.
- Frontend preview and backend bake must use the same formula.

### 2. Colormap Source Precedence (In Scope)

Colormaps are resolved with deterministic precedence order (highest to lowest):

1. scenario-local colormap file,
2. scenario-root colormap file,
3. app/deployment colormap file,
4. built-in colormaps.

Higher-precedence sources override lower-precedence sources by colormap `id`.

### 3. Default Colormap Rules by Filename Regex (In Scope)

A startup-loaded JSON rules file will define default-colormap assignment rules keyed by filename regex with first-match semantics.

Rules contract:

- Ordered list, evaluated top-to-bottom.
- First regex match wins.
- A rule maps to a known colormap id.
- If no rule matches, system default colormap is used.
- Match target is file basename without extension (file stem) only.

Application points:

- Applied on layer creation/import only.
- Persisted selected colormap is stored in scenario DB layer style.
- Once persisted, scenario DB style is authoritative.

Rules-file discovery and precedence:

- A colormap-rules file may exist in any directory where colormap files are resolved.
- Rules are merged using the same precedence order as colormaps (scenario-local > scenario-root > app > built-in).
- Higher-precedence rule entries are evaluated before lower-precedence entries.
- Expected initial deployment: only the built-in/app rules file is present.

### 4. Explicit Re-Apply Operation (In Scope)

Provide an explicit `Apply Default Colormap` user operation (context menu action in relevant layer/file UI surfaces) that re-runs regex default matching and updates the target layer style.

This operation is opt-in, single-target only (one selected file/layer per invocation), and does not automatically override existing persisted style values.

### 5. Assistant Colormap Operations (In Scope)

Add explicit assistant-level colormap operations for common workflows:

- list known colormaps,
- apply known colormap to a target layer,
- create simple colormaps (including discrete interval palettes),
- save scenario-scoped colormap definitions.

`layer.update_state.style` remains available but is not the primary assistant colormap contract.

### 6. Parameterized Colormap Controls (In Scope for Design, Partial Implementation)

Introduce the design contract for parameterized colormaps with real-time controls. Immediate required special case:

- two-color discrete threshold colormap must expose a live threshold slider in layer controls near brightness/contrast.

The same mechanism should support future simple runtime parameters for other colormap families.

### 7. Contour Lines as Non-Colormap Raster Style Mode (In Scope for Contract)

Contour-line visualization is recognized as a first-class raster style mode and not treated as a colormap.

- Contour mode parameters include at least interval, optional offset, line color, and line thickness.
- Contour mode is independent from colormap mode, though hybrid overlays are allowed as future enhancement.

Implementation may begin with expression-based approximation and later evolve under shader extension policy when needed.

### 8. Backend Colormap Bake to RGBA GeoTIFF (In Scope)

Add a backend operation that applies a selected colormap to a source GeoTIFF and writes a new RGBA GeoTIFF artifact for reproducible export/share workflows.

Output contract:

- Output filename suffix: `.rgba.tif`.
- Output bands: 4 bands (`R`, `G`, `B`, `A`) with `uint8` dtype.
- Output is written as a new scenario-managed file/product, not an in-place overwrite.
- Output is COG-compatible (tiled/compressed/overview-ready profile per project COG policy).
- Output CRS/projection matches the source file CRS/projection unless an explicit reprojection option is added in a future ADR.

Architecture contract:

- Implement as a backend job handler in `backend/jobs/handlers.py`.
- Persist lineage metadata including:
  - source file id/path,
  - colormap id,
  - colormap mode/parameters,
  - value-domain/classification settings used for the bake,
  - producer/version metadata.

Parity contract:

- Frontend interactive styling and backend bake must use the same canonical transfer-function schema.
- Do not maintain divergent, ad hoc colormap logic paths.
- Add parity tests on representative sample values to verify consistent RGBA outcomes between frontend and backend implementations.
- This parity requirement applies to colormap modes and contour style mode semantics; backend bake outputs must match frontend rendering intent for the same style definition.

Canonical transfer-function schema ownership:

- Backend owns the authoritative, versioned transfer-function schema contract.
- Frontend consumes this schema for interactive rendering behavior.
- Schema changes must remain versioned and backward-compatible or include explicit migration behavior.

## Scope

In scope now:

- fix brightness/contrast semantics so controls behave normally across supported layer types,
- implement deterministic colormap precedence,
- add startup-loaded filename-regex default-colormap rules,
- apply defaults on import/create,
- add explicit `Apply Default Colormap` operation,
- add explicit assistant colormap operations for create/apply/list/save,
- define parameterized colormap contract and deliver two-color threshold slider behavior,
- define contour-line style-mode contract.
- add backend `Export as RGBA GeoTIFF` colormap-bake operation.

Out of scope for this ADR implementation slice:

- viewport-wide colorblind simulation shader,
- bivariate / multidimensional LUT shader rendering,
- fully general histogram-overlay transfer-function editor UI,
- global shader-registry expansion beyond contracts already defined by ADR 0008.

## Data and Configuration Contracts

### Colormap Definition (Extended)

Existing colormap definitions remain valid. Extended fields are additive and optional.

```json
{
  "id": "hazard_3class",
  "name": "Hazard (3 Class)",
  "stops": [
    { "value": 0.0, "color": [30, 160, 70, 1] },
    { "value": 0.5, "color": [245, 190, 60, 1] },
    { "value": 1.0, "color": [215, 60, 45, 1] }
  ],
  "mode": "continuous",
  "parameters": []
}
```

Planned additive fields:

- `mode`: `continuous | discrete | threshold | cyclic`.
- `parameters`: optional parameter descriptors for runtime controls.

### Colormap Type JSON Examples

These examples are normative shape examples for the planned extended schema.

Continuous:

```json
{
  "id": "elevation_continuous",
  "name": "Elevation Continuous",
  "mode": "continuous",
  "stops": [
    { "value": 0.0, "color": [18, 42, 88, 1] },
    { "value": 0.5, "color": [138, 189, 106, 1] },
    { "value": 1.0, "color": [246, 232, 195, 1] }
  ]
}
```

Discrete:

```json
{
  "id": "hazard_discrete_3class",
  "name": "Hazard Discrete (3 Class)",
  "mode": "discrete",
  "stops": [
    { "value": 0.0, "color": [40, 167, 69, 1] },
    { "value": 0.3333, "color": [40, 167, 69, 1] },
    { "value": 0.3334, "color": [255, 193, 7, 1] },
    { "value": 0.6666, "color": [255, 193, 7, 1] },
    { "value": 0.6667, "color": [220, 53, 69, 1] },
    { "value": 1.0, "color": [220, 53, 69, 1] }
  ]
}
```

Threshold:

```json
{
  "id": "psr_threshold_blue",
  "name": "PSR Threshold (Blue)",
  "mode": "threshold",
  "parameters": [
    {
      "id": "threshold",
      "type": "number",
      "default": 0.78,
      "min": 0.0,
      "max": 1.0
    }
  ],
  "stops": [
    { "value": 0.0, "color": [70, 130, 180, 0] },
    { "value": 0.78, "color": [70, 130, 180, 0] },
    { "value": 0.7801, "color": [70, 130, 180, 1] },
    { "value": 1.0, "color": [70, 130, 180, 1] }
  ]
}
```

Cyclic:

```json
{
  "id": "aspect_phase_wheel",
  "name": "Aspect Phase Wheel",
  "mode": "cyclic",
  "cyclic": {
    "period": 360.0,
    "domain_min": 0.0,
    "domain_max": 360.0
  },
  "stops": [
    { "value": 0.0, "color": [255, 0, 0, 1] },
    { "value": 0.25, "color": [255, 255, 0, 1] },
    { "value": 0.5, "color": [0, 255, 255, 1] },
    { "value": 0.75, "color": [255, 0, 255, 1] },
    { "value": 1.0, "color": [255, 0, 0, 1] }
  ]
}
```

### Default Colormap Rule File

Startup-loaded JSON contract (path configurable):

```json
{
  "rules": [
    { "pattern": "(?i)hazard|risk|safe", "colormap": "hazard_3class" },
    { "pattern": "(?i)aspect", "colormap": "phase_wheel" },
    { "pattern": "(?i)slope", "colormap": "viridis" }
  ],
  "default": "gray"
}
```

Validation rules:

- invalid regex entries are skipped with structured warnings,
- unknown colormap ids are skipped with structured warnings,
- file load failures are non-fatal and fall back to normal default.

### Layer Style Authority

- Scenario DB `LayerState.style` remains source of truth after layer creation/import.
- Regex default rules are used only for initial selection (or explicit re-apply action).

## API and UI Contract Updates

- Colormap registry response should expose resolved source metadata and effective precedence.
- Layer create/import flows should set initial `style.colormap` using default rules.
- UI context menu adds `Apply Default Colormap`.
- UI adds an explicit `Export as RGBA GeoTIFF` action for eligible raster layers/files.
- Layer controls retain brightness/contrast and gain parameter control(s) for eligible parameterized colormaps (first case: threshold slider).
- Map-display delivery should avoid creating additional display-derivative files when source raster CRS is already compatible with the map-display CRS contract.

## Assistant Contract

Add explicit assistant tools (names illustrative):

- `colormap.list`
- `colormap.create_simple`
- `colormap.save_scenario`
- `layer.apply_colormap`

Behavioral expectations:

- Assistant can create simple discrete interval colormaps,
- assistant can apply known colormaps to existing layers,
- assistant should prefer explicit colormap tools over generic style-object mutation for these workflows.

## Contour-Line Contract

Contour mode is a raster style-mode contract with parameters, for example:

- `interval` (required),
- `offset` (optional),
- `line_color` (optional),
- `line_width_px` (optional).

This contract is additive and does not require immediate shader implementation.

Contour style JSON example:

```json
{
  "style_mode": "contour",
  "contour": {
    "interval": 5.0,
    "offset": 0.0,
    "line_color": [255, 255, 255, 1.0],
    "line_width_px": 1.5
  }
}
```

## Rollout Plan

### Phase 0: Guardrails and Compatibility

- [ ] Define migration behavior for existing brightness/contrast-styled layers.
- [ ] Add compatibility notes for persisted styles from prior builds.
- [ ] Add observability for style-application failures and fallback cases.

### Phase 1: Tone Semantics Fix

- [ ] Implement post-colormap brightness/contrast semantics for raster layers.
- [ ] Align tone behavior across raster and Trek overlays where controls are shared.
- [ ] Add regression tests verifying slider effects and cross-layer consistency.

### Phase 2: Colormap Resolution and Default Rules

- [ ] Implement and validate precedence: built-in < app < scenario-root < scenario-local.
- [ ] Add startup load + validation for filename-regex default-colormap rules.
- [ ] Apply defaults on layer import/create.
- [ ] Add `Apply Default Colormap` UI action.

### Phase 3: Assistant Operations

- [ ] Add explicit assistant colormap tools and schemas.
- [ ] Add tests proving assistant can create discrete colormaps and apply known colormaps.

### Phase 4: Parameterized Colormap Controls (Initial)

- [ ] Add parameterized colormap contract.
- [ ] Implement two-color threshold special case with live slider in layer controls.
- [ ] Ensure real-time updates without server-side recolor jobs.

### Phase 5: Contour Mode Contract Surface

- [ ] Add contour style-mode metadata/contract and validation.
- [ ] Implement initial rendering path or scoped no-op placeholder with clear unsupported signaling.

### Phase 6: Backend RGBA Bake Operation

- [ ] Add typed job handler implementation for colormap-to-RGBA bake.
- [ ] Add output path/naming policy with `.rgba.tif` suffix and deterministic conflict handling.
- [ ] Add lineage metadata capture for full reproducibility.
- [ ] Add UI action wiring for `Export as RGBA GeoTIFF`.
- [ ] Define initial supported colormap modes in bake path (`continuous`, `discrete`, `threshold`) and explicit unsupported signaling for others.
- [ ] Define contour bake parity contract against frontend contour rendering semantics.

### Phase 6b: Bake Parity Expansion (Follow-on, Same ADR Contract)

- [ ] Add backend bake support for `cyclic` colormaps using canonical cyclic parameters (`period`, domain mapping, wrap semantics).
- [ ] Add backend bake support for contour style mode using canonical contour parameters (`interval`, `offset`, `line_color`, `line_width_px`).
- [ ] Add parity tests for cyclic seam behavior and contour line placement/appearance equivalence versus frontend rendering.

## Testing and Verification Requirements

- Unit tests for colormap merge precedence and rule first-match behavior.
- Unit tests for default-rule regex validation/failure handling.
- Frontend tests for post-colormap tone behavior and threshold slider state updates.
- Integration tests for import/create applying expected default colormap.
- Assistant contract tests for create/apply/list colormap operations.
- Backend tests validating RGBA output dtype/band semantics and nodata/alpha behavior.
- Parity tests verifying frontend and backend colormap transfer outputs match for canonical sample inputs.
- Manual verification for `Apply Default Colormap` UX and no-surprise behavior with pre-existing styled layers.

## Risks and Mitigations

Risk: behavior change may surprise users who relied on previous pre-colormap tone behavior.
Mitigation: explicit release notes, migration notes, and optional compatibility toggle during rollout.

Risk: regex defaults could produce unexpected matches.
Mitigation: first-match deterministic semantics, ordered rule file, preview/logging, explicit re-apply action.

Risk: parameterized controls create UI complexity.
Mitigation: start with one constrained threshold case and expand only with validated patterns.

Risk: frontend preview and backend baked outputs drift over time.
Mitigation: one canonical transfer-function schema, shared fixtures, and parity tests as a release gate.

## Future Work (Explicitly Deferred)

- Viewport-wide colorblind simulation shader.
- Full histogram-overlay transfer-function editor UX.
- Bivariate/multidimensional LUT shader path (under ADR 0008 extension contract).
