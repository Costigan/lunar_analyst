# CesiumJS vs. OpenLayers for Lunar Analyst

This document evaluates the feasibility and trade-offs of replacing **OpenLayers** with **CesiumJS** as the primary mapping engine for the Lunar Analyst project, specifically for the `MoonLayers` widget and the upcoming web-based desktop viewer.

## Summary Recommendation

**Keep OpenLayers as the primary 2D mapping engine.** 

While CesiumJS offers superior 3D visualization, the current project requirements—focused on **2D lunar south pole analysis**, **custom polar stereographic projections**, and **direct COG rendering**—are significantly better served by OpenLayers. A transition to CesiumJS at this stage would introduce substantial engineering overhead for mapping 2D polar projections and integrating with `geotiff.js`.

---

## Comparative Analysis

| Feature | OpenLayers (Current) | CesiumJS (Proposed) |
| :--- | :--- | :--- |
| **2D Polar Mapping** | **Excellent.** Proj4js integration allows native support for `ESRI:103878`. | **Difficult.** Custom projections in 2D mode require complex `MapProjection` overrides. |
| **3D Visualization** | None (limited 2.5D via plugins). | **Best-in-class.** Native 3D globe with terrain and lighting. |
| **Lunar Ellipsoid** | Supported via Proj4 and custom view/extent settings. | Native support via `Ellipsoid.MOON`. |
| **COG (GeoTIFF.js)** | **Native support** via `WebGLTile` source. Highly performant. | No native support. Requires 3rd-party libs or tile-serving proxy. |
| **WMTS (Moon Trek)** | Robust. Handles non-standard grid extents and URNs (e.g., `EPSG::0`). | Standard support, but mapping custom URNs is less flexible than OL. |
| **Notebook Integration**| Mature (`anywidget`). Lightweight footprint. | Heavy footprint; can be resource-intensive in some notebook environments. |

---

## Detailed Trade-offs

### 1. The Projection Problem (South Polar Stereographic)
The Lunar South Pole mission focus requires `ESRI:103878` (Lunar South Pole Stereographic). 
- **OpenLayers** treats this as a first-class citizen. You define the Proj4 string, and the entire map (coordinates, scales, rotations) adapts.
- **CesiumJS** is fundamentally a 3D ECEF engine. While it has a 2D mode, it is largely designed for Geographic (EPSG:4326) or Web Mercator (EPSG:3857). Implementing a Polar Stereographic 2D view in Cesium requires custom math to transform every imagery request and mouse interaction, which is prone to edge-case errors at the poles.

### 2. Cloud Optimized GeoTIFF (COG) Support
Lunar Analyst relies on `geotiff.js` to render local DEMs and hillshades directly in the browser.
- **OpenLayers** has a dedicated `WebGLTile` source designed specifically for this workflow. It manages the tiling and GPU-accelerated rendering of raw GeoTIFF data seamlessly.
- **CesiumJS** lacks a native COG provider. To achieve the same "serverless" COG rendering, we would need to maintain a complex bridge between `geotiff.js` and Cesium's imagery provider API.

### 3. Moon Trek WMTS Integration
As seen in `backend/web/map_milestone/app.js`, Moon Trek uses several non-standard conventions:
- Using `EPSG::0` or `urn:ogc:def:crs:ESRI::103878` in `GetCapabilities`.
- Occasional `NaN` or invalid values in tile grid extents that require client-side patching.
- **OpenLayers**' flexible architecture makes these "hacks" easy to implement. Cesium's more rigid `ImageryProvider` architecture makes these corrections harder to inject.

---

## The Path to 3D

The desire for 3D visualization (shadowing, crater depth, landing approach) is valid. However, the 3D mode in Cesium uses a **global sphere/ellipsoid**, where "projections" like Polar Stereographic don't exist—you simply look at the south pole of the sphere.

### Proposed Hybrid Strategy
Instead of a total replacement, consider the following roadmap:

1.  **Phase 1-3 (Current):** Stabilize the **OpenLayers** stack for high-precision 2D analysis and notebook workflows.
2.  **Phase 4:** If 3D is required, implement a **CesiumJS Viewport** as an *optional* view mode.
    - The OpenLayers view remains the primary "Research/Measurement" tool.
    - The Cesium view provides the "Immersive/3D" context.
3.  **Tile Sourcing for 3D:**
    - **Imagery:** Continue using Moon Trek WMTS (Cesium can consume these as global imagery).
    - **Terrain:** Use NASA's quantized-mesh terrain tiles (available via Moon Trek or the Lunar LRO/LOLA datasets). Cesium can consume these natively.

## Conclusion

Replacing OpenLayers with CesiumJS today would sacrifice the project's primary strength: **precise, easy-to-use 2D polar mapping**. We should continue with OpenLayers for the unified web/notebook architecture and reserve CesiumJS for a future specialized 3D visualization module.
