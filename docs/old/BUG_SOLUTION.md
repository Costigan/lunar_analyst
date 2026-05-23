# BUG SOLUTION: Phase 0.9 Browser Map Blank Blue Background

## Problem Summary
The Phase 0.9 map milestone page loaded without errors, but displayed only a blue background with no visible Moon Trek WMTS tiles. The WMTS capabilities were fetched successfully, tile URLs were generated correctly, and direct tile fetches returned HTTP 200, yet OpenLayers did not render any tiles.

## Root Causes Identified

### 1. WMTS Source `optionsFromCapabilities` Creates Invalid Tile Grid
OpenLayers' `optionsFromCapabilities()` function created a WMTS tile grid with `NaN` values in `fullTileRange`. This caused OpenLayers to reject all tiles as "out of range" during the render cycle, even though the layer appeared to render (prerender/postrender events fired).

**Evidence:**
```
Full tile range at z=1: {minX: NaN, maxX: NaN, minY: NaN, maxY: NaN}
```

### 2. View Resolution Mismatch
When the View's `fit()` method was called, it calculated an intermediate resolution (e.g., 3373.55) that didn't match any tile grid resolution. Without `constrainResolution: true`, OpenLayers couldn't properly snap to a valid tile zoom level.

**Evidence:**
```
View after fit - resolution: 3373.550724637681
Tile grid resolutions: [8561.95, 4280.98, 2140.49, 1070.24, 535.12]
```

### 3. Moon Trek's Non-Standard CRS (`EPSG::0`)
Moon Trek uses a non-standard CRS identifier `urn:ogc:def:crs:EPSG::0` for their south polar stereographic projection. This must be explicitly registered as equivalent to `ESRI:103878` for OpenLayers to properly handle the projection.

## Solution

### Replaced WMTS Source with XYZ Source + Custom TileGrid

Instead of relying on OpenLayers' `optionsFromCapabilities()` which produced invalid tile grids, the solution manually constructs an XYZ source with:

1. **Custom TileGrid** with explicit origin, resolutions, and extent parsed from WMTS capabilities
2. **Custom tileUrlFunction** that maps OpenLayers tile coordinates to WMTS REST URL format
3. **Proper CRS registration** with all Moon Trek CRS aliases marked as equivalent

### Key Code Changes

**Projection Registration:**
```javascript
// Register all CRS variants as equivalent
const projection = registerProjection("ESRI:103878", proj4def, extent);
const urnEsri = registerProjection("urn:ogc:def:crs:ESRI::103878", proj4def, extent);
const epsg0 = registerProjection("EPSG::0", proj4def, extent);
const urnEpsg0 = registerProjection("urn:ogc:def:crs:EPSG::0", proj4def, extent);
addEquivalentProjections([projection, urnEsri, epsg0, urnEpsg0]);
```

**TileGrid Construction:**
```javascript
const tileGrid = new TileGrid({
  origin: [-1095930, 1095930],  // From WMTS capabilities TopLeftCorner
  resolutions: resolutions,     // Parsed from TileMatrix ScaleDenominators
  extent: [-931100, -931100, 931100, 931100],
  tileSize: 256,
});
```

**Custom Tile URL Function:**
```javascript
tileUrlFunction: (tileCoord) => {
  const z = tileCoord[0];
  const x = tileCoord[1];
  const y = tileCoord[2];
  const matrixId = matrixIds[z];
  return `${baseUrl}/${style}/${matrixSet}/${matrixId}/${y}/${x}.png`;
}
```

**View Configuration:**
```javascript
view: new View({
  projection: wmtsProjection,
  resolutions: extendedResolutions,  // Extended beyond WMTS levels for deep zoom
  constrainResolution: true,         // Force snapping to valid resolutions
  center: [0, 0],
  zoom: 0,
})
```

## Files Modified
- `backend/web/map_milestone/app.js` - Complete rewrite of tile layer creation
- `backend/web/map_milestone/index.html` - Cleaned up import map
- `config/lunar_analyst.toml` - Updated hillshade path

## Lessons Learned

1. **Don't trust `optionsFromCapabilities()` for non-standard projections** - Moon Trek's unusual CRS setup causes OpenLayers' automatic parsing to produce invalid tile grids.

2. **Always verify tile grid `fullTileRange`** - If this contains `NaN`, tiles will never be requested regardless of other settings.

3. **Use `constrainResolution: true`** when working with fixed tile grid resolutions to prevent resolution mismatches after view operations like `fit()`.

4. **XYZ source with custom tileUrlFunction is more reliable** than WMTS source for non-standard WMTS services, as it gives full control over URL construction.

## Verification
After the fix, the browser console shows successful tile requests:
```
XYZ tileUrlFunction: [1, 0, 0] -> https://trek.nasa.gov/.../1/0/0.png
Tile load start: [1, 0, 0]
```

And the map displays the Moon Trek tiled base layer correctly.
