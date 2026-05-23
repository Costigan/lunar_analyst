# Lunar Analyst Map Service API

## Overview

This document describes the abstract map service API used by the Lunar Analyst UI. The current implementation wraps OpenLayers, but this API could be backed by any tile/raster server.

## Map

### Create Map

```
POST /map
```

Creates a new map instance.

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `target` | HTMLElement | yes | DOM element to render into |
| `projection` | string | yes | CRS code (e.g., `ESRI:103878`) |
| `center` | [number, number] | yes | Initial center in projection units |
| `zoom` | number | yes | Initial zoom level |
| `layers` | Layer[] | no | Initial layers to add |
| `maxTilesLoading` | number | no | Max concurrent tile loads (default: 64) |

### Get Map

```
GET /map
```

Returns the current map state.

### Set Target

```
PUT /map/target
```

Mounts/unmounts the map to a DOM element.

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `target` | HTMLElement \| undefined | yes | DOM element or null to unmount |

### Update Size

```
POST /map/size
```

Forces a size recalculation.

### Add Control

```
POST /map/controls
```

Adds a map control (e.g., scalebar).

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Control type: `scaleline` |
| `options` | object | no | Type-specific options |

**ScaleLine options:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `bar` | boolean | true | Render as bar vs line |
| `steps` | number | 4 | Number of tick marks |
| `minWidth` | number | 110 | Minimum pixel width |
| `units` | string | `"metric"` | Unit system |

---

## View

### Get View

```
GET /map/view
```

Returns current view state.

**Response:**
| Field | Type | Description |
|-------|------|-------------|
| `center` | [number, number] |
| `zoom` | number |
| `resolution` | number |
| `projection` | string |

### Set View

```
PUT /map/view
```

Sets view center, zoom, and/or resolution.

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `center` | [number, number] | no | New center |
| `zoom` | number | no | New zoom |
| `resolution` | number | no | New resolution |
| `animate` | boolean | no | Animate transition |
| `durationMs` | number | no | Animation duration |

### Fit Extent

```
POST /map/view/fit
```

Zooms/pan the view to fit an extent.

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `extent` | [minX, minY, maxX, maxY] | yes | Target extent in projection units |
| `paddingPx` | number | no | Viewport padding (default: 32) |
| `maxZoom` | number | no | Maximum zoom level |
| `animate` | boolean | no | Animate transition (default: true) |
| `durationMs` | number | no | Animation duration (default: 250) |

---

## Layers

### Add Layer

```
POST /map/layers
```

Adds a layer to the map.

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `layer_id` | string | yes | Unique layer identifier |
| `type` | string | yes | Layer type: `raster`, `vector`, `tile`, `wmts` |
| `source` | SourceSpec | yes | Data source |
| `style` | StyleSpec | no | Visual styling |
| `visible` | boolean | no | Initial visibility (default: true) |
| `opacity` | number | no | Opacity 0-1 (default: 1) |
| `zIndex` | number | no | Stacking order (default: append) |

### Remove Layer

```
DELETE /map/layers/{layer_id}
```

Removes a layer from the map.

### Reorder Layers

```
PUT /map/layers
```

Updates layer z-indices and active set.

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `layers` | Layer[] | yes | Complete layer state |

---

## Layer Properties

### Set Visibility

```
PATCH /map/layers/{layer_id}
```

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `visible` | boolean | yes | Show/hide layer |

### Set Opacity

```
PATCH /map/layers/{layer_id}
```

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `opacity` | number | yes | Opacity 0-1 |

### Set Z-Index

```
PATCH /map/layers/{layer_id}
```

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `zIndex` | number | yes | Stacking order |

### Set Style

```
PATCH /map/layers/{layer_id}
```

Updates layer styling.

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `style` | StyleSpec | yes | New style definition |

### Apply Tone Adjust

```
PATCH /map/layers/{layer_id}/tone
```

Applies brightness/contrast adjustments via prerender/postrender hooks.

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `brightness` | number | no | Brightness offset (-1 to inf) |
| `contrast` | number | no | Contrast multiplier (0 to inf) |

---

## Sources

### Raster Source (GeoTIFF)

Defines a single-band raster with colormap.

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"geotiff"` | Source type |
| `url` | string | URL to GeoTIFF file |
| `normalize` | boolean | Normalize values to 0-1 (default: false) |
| `interpolate` | boolean | Bilinear interpolation (default: false) |

### Vector Source (GeoJSON)

Defines a vector feature layer.

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"vector"` | Source type |
| `url` | string | URL to GeoJSON endpoint |
| `format` | `"geojson"` | Feature format |
| `wrapX` | boolean | Wrap horizontally (default: false) |

### Tile Source (XYZ)

Defines a tiled image layer.

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"xyz"` | Source type |
| `projection` | string | CRS code |
| `tileGrid` | TileGridSpec | Tile grid definition |
| `tileUrlFunction` | function | URL builder for [z, x, y] |
| `wrapX` | boolean | Wrap horizontally (default: false) |
| `interpolate` | boolean | Bilinear interpolation (default: false) |

### Tile Grid

| Field | Type | Description |
|-------|------|-------------|
| `origin` | [number, number] | Tile grid origin |
| `resolutions` | number[] | Resolution per zoom level |
| `extent` | [minX, minY, maxX, maxY] | Layer extent |
| `tileSize` | number | Tile dimensions (default: 256) |

---

## Styling

### Raster Style (WebGL Colormap)

| Field | Type | Description |
|-------|------|-------------|
| `colormap` | ColormapSpec | Color ramp definition |

### Colormap

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Colormap identifier |
| `stops` | ColorStop[] | Color ramp stops |

### Color Stop

| Field | Type | Description |
|-------|------|-------------|
| `value` | number | Data value (normalized 0-1) |
| `color` | [r, g, b, a] | RGBA color (0-255) |

### Vector Style

| Field | Type | Description |
|-------|------|-------------|
| `stroke` | StrokeSpec | Line/stroke styling |
| `fill` | FillSpec | Polygon fill styling |
| `image` | ImageSpec | Point marker styling |

### Stroke

| Field | Type | Description |
|-------|------|-------------|
| `color` | string | CSS color or hex |
| `width` | number | Line width in pixels |

### Fill

| Field | Type | Description |
|-------|------|-------------|
| `color` | string | CSS color or hex (with alpha) |

### Circle (Point Style)

| Field | Type | Description |
|-------|------|-------------|
| `radius` | number | Circle radius in pixels |
| `stroke` | StrokeSpec | Stroke styling |
| `fill` | FillSpec | Fill styling |

---

## Events

### Subscriptions

```
GET /map/events
```

Subscribe to map events via SSE/WebSocket.

**Event types:**

| Event | Description |
|-------|-------------|
| `change:size` | Map container resized |
| `tileloaderror` | Tile failed to load |
| `layer:change` | Layer source state changed |
| `prerender` | Before layer render |
| `postrender` | After layer render |

### Event Payloads

**change:size:**
```json
{ "type": "change:size", "size": [width, height] }
```

**tileloaderror:**
```json
{ "type": "tileloaderror", "layer_id": "string", "url": "string" }
```

**layer:change:**
```json
{ "type": "layer:change", "layer_id": "string", "state": "loading" | "ready" | "error" }
```

---

## Layer Types

| Type | Source | Rendering | Use Case |
|------|--------|-----------|----------|
| `raster` | GeoTIFF | WebGLTileLayer | DEMs, hillshade, analysis |
| `vector` | GeoJSON | VectorLayer | Features, annotations |
| `tile` | XYZ | TileLayer | Base imagery, overlays |
| `wmts` | WMTS | TileLayer | OGC WMTS services |

---

## Scenario Layers

Scenario layers represent raster/vector products from the scenario catalog.

**ScenarioLayer:**
| Field | Type | Description |
|-------|------|-------------|
| `layer_id` | string | Unique identifier |
| `source_file_id` | string | File ID in scenario catalog |
| `render_mode` | `"raster"` \| `"vector"` | Rendering mode |
| `visible` | boolean | Current visibility |
| `opacity` | number | Opacity 0-1 |
| `z_index` | number | Stacking order |
| `style` | object | Style/colormap parameters |

### Sync Scenario Layers

```
PUT /map/scenario-layers
```

Replaces all scenario layers with new state. Removes orphaned layers, adds new layers, updates changed layers.

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `layers` | ScenarioLayer[] | yes | Complete layer state |

---

## Trek Overlays

Moon Trek overlays are sourced from the NASA Lunar Trek catalog.

**TrekOverlayLayer:**
| Field | Type | Description |
|-------|------|-------------|
| `layer_id` | string | Unique identifier |
| `metadata` | TrekLayerMetadata | Trek catalog entry |
| `visible` | boolean | Current visibility |
| `opacity` | number | Opacity 0-1 |
| `z_index` | number | Stacking order |
| `style` | object | Tone adjustment parameters |

### Sync Trek Overlays

```
PUT /map/trek-overlays
```

Replaces all trek overlays with new state.

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `overlays` | TrekOverlayLayer[] | yes | Complete overlay state |

---

## Trek Layer Fallback Chain

Trek layers attempt loading in order:

1. **Feature service** (ArcGIS MapServer/FeatureServer) - vector features
2. **WMTS tiles** - raster tiles from capabilities
3. **Metadata footprint** - bounding box polygon fallback

---

## Projection

The map uses **ESRI:103878** (Lunar South Pole Stereographic) as the canonical CRS. All extents and coordinates are in this projection.

### Register Projection

```
POST /projections
```

Registers a CRS with proj4.

**Arguments:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | string | yes | CRS code (e.g., `ESRI:103878`) |
| `definition` | string | no | WKT or PROJ string |
| `units` | string | no | Units (default: `m`) |
| `extent` | number[] | no | Valid extent |

---

## Raster Display Policy

Per `ADR.0006`, map-facing display uses backend-warped `ESRI:103878` derivatives. Non-map notebook presentation can use native/source CRS.

Display derivatives store warped COGs at:
```
display/{product_id}/esri_103878/{source_stem}.{warp_hash}.cog.tif
```

---

## Error Handling

| Error Code | Description |
|-----------|-------------|
| `invalid_extent` | Extent has zero area |
| `invalid_projection` | Unknown CRS code |
| `layer_not_found` | Layer ID not in map |
| `source_error` | Raster source failed to load |
| `tile_error` | Tile failed to load |

---

## Configuration

### MapControllerConfig

| Field | Type | Default | Description |
|-------|------|--------|-------------|
| `projection` | Projection | required | Map CRS |
| `center` | [number, number] | required | Initial center |
| `zoom` | number | required | Initial zoom |
| `moonTrekLayerId` | string | `"LRO_WAC_Mosaic_SPole60_100mp"` | Base layer ID |
| `moonTrekMatrixSet` | string | `"default028mm"` | Tile matrix set |
| `moonTrekStyle` | string | `"default"` | Style variant |
| `extraZoomLevels` | number | 20 | Additional zoom levels |