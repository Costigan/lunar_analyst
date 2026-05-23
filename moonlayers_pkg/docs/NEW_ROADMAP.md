# Lunar Analyst Project Roadmap

## 1. Vision & Objectives
Deliver a high-performance, project-based toolkit for lunar south pole mission analysis. The system unifies high-fidelity visualization with notebook-first analytics (MoonLayers + Dask/xarray Backend), supporting reproducible terrain analysis pipelines and sophisticated planetary science scenarios. 

**Strategic Pivot:** We are transitioning the desktop visualization from a native .NET WinForms application (`mapviewer`) to a browser-based architecture (using React, OpenLayers, and Tauri). This unifies the mapping engine across all clients (Notebook and Desktop) and leverages client-side COG rendering via `geotiff.js`.

---

## 2. Current State (February 2026)
### MapViewer (.NET 9 + SkiaSharp)
- **Phase 1a/b Complete**: Core infrastructure, Raster (GeoTIFF via GDAL), Vector (GeoJSON via NTS), and WMTS/Tile layers are fully implemented.
- **BruTile Integration**: Multi-tier caching (GPU/RAM -> Disk -> Web) is active.
- **Label System**: High-performance rendering for large crater/label catalogs (CSV + JSON sidecars).
- **Projections**: Default support for ESRI:103878 (Lunar South Pole Stereographic).

### MoonLayers (Python + Anywidget + OpenLayers)
- **Core Map**: OpenLayers-based interactive widget for Marimo/Jupyter.
- **Data Integration**: Integrated GeoTIFF server for local COGs; Moon Trek catalog search (800+ layers).
- **Ready for Production**: GeoTIFF race conditions fixed; projection parity with desktop.

### New Horizon & Backend
- **New Horizon**: GPU-accelerated (ILGPU) horizon profile generation from DEMs is standalone and high-performance. It serves as the reference engine for visibility and lighting.
- **Analytics Design**: xarray/Dask-based pipeline architecture finalized for slope, roughness, and illumination.
- **Map Algebra**: Conceptual design for a high-performance C# library to handle complex geospatial operations (Focal, Zonal, Global) on rasters and horizon profiles.

---

## 3. Phase 1c: Optimization & Discovery (Short-Term)
*Focus: Hardening the existing desktop experience and streamlining layer discovery.*

- [ ] **BruTile Disk Cache Persistence**: Finalize SQLite metadata tracking and LRU eviction policy for the tile cache.
- [ ] **WMTS Capabilities UX**: 
  - Implementation of a "Layer Discovery" dialog in MapViewer.
  - Fetch and parse `WMTSCapabilities.xml` from Moon Trek/QuickMap.
  - Allow users to select and add layers directly from the UI.
- [ ] **MoonLayers Packaging**: Formalize the build process (`npm run build` + `python -m build`) and document the `sync_geotiffs` usage.
- [ ] **Label Layer Editor**: Simple UI in MapViewer to toggle visibility and basic styling for label/crater layers.

---

## 4. Phase 1e: Browser Migration Kickoff (Short-Term)
*Focus: Transitioning MapViewer logic to the web.*

- [ ] **Prototype Web Renderer**: Create a standalone React + OpenLayers app that renders local COGs via `geotiff.js`.
- [ ] **Tauri Shell**: Set up the Tauri project to wrap the web map and provide local file system access.
- [ ] **Shared Mapping Library**: Extract OpenLayers logic from `moonlayers` into a reusable package for both the widget and the new Desktop app.
- [ ] **C# Feature Audit**: Ensure all `mapviewer` features (Projections, Layer Manager, Opacity) are parity-tracked for the web implementation.

---

## 5. Phase 1f: Integration & Control (Short-Term)
*Focus: Bridging the Desktop and Python worlds.*

- [ ] **REST API for MapViewer**: 
  - Implement a lightweight REST server within MapViewer.
  - Expose endpoints: `add_layer`, `remove_layer`, `set_view(lat, lon, zoom)`, `set_opacity`, `get_screenshot`.
  - Enable "MapViewer as Matplotlib" workflow from Python.
- [ ] **Live Asset Refresh (File Watcher)**:
  - Implement a service to monitor GeoJSON and GeoTIFF files.
  - Trigger reloads in MapViewer when backend pipelines update assets (using timestamp/size checks).
- [ ] **Reprojection Services**:
  - Implement on-the-fly reprojection for vectors if they differ from the project CRS.
  - Add GDAL-backed raster reprojection with disk caching.

---

## 5. Phase 2: Analytics Pipeline & Site Tool (Medium-Term)
*Focus: Implementing the Dask-powered backend described in `lunar_analyst.backend.md`.*

- [ ] **Pipeline Core**:
  - Codify the `rioxarray` + `Dask` templates for DEM alignment and clipping.
  - Implement parameter hashing for idempotent recompute and Zarr/NetCDF caching.
- [ ] **Derivative Generators**:
  - Implement Slope, Aspect, and Roughness calculators as reusable Python modules.
  - Implement boolean constraint masking (e.g., `slope < 10°` AND `roughness < threshold`).
- [ ] **Project Catalog Schema**:
  - Finalize the SpatiaLite/GeoPackage schema for project metadata and layer registry.
  - Ensure compatibility between Python-generated catalogs and MapViewer.
- [ ] **Analytics Overlays**:
  - Create standardized legends and color ramps for analytics outputs in MapViewer.

---

## 6. Phase 3: Horizons, Lighting & Synthesis (Medium-Term)
*Focus: Leveraging New Horizon for complex lighting scenarios.*

- [ ] **New Horizon Service**:
  - Wrap `new_horizon` logic into a reusable service or CLI for the Python pipeline.
  - Generate 0.25° resolution horizon profiles for candidate sites.
- [ ] **Advanced Lighting Pipeline**:
  - Generate time-series lighting maps and PSR (Permanently Shadowed Region) masks.
  - Generate "Average Sun" and "Shadow Depth" maps for mission duration analysis.
- [ ] **Synthetic Imagery**:
  - Use horizon profiles and solar geometry to generate accurate synthetic views for site validation.

---

## 7. Phase 4: Advanced Scenarios & Map Algebra (Long-Term)
*Focus: Completing the scenario list and formalizing the operator library.*

- [ ] **Map Algebra Library**:
  - Implement the C# Map Algebra library referenced in `MAP_ALGEBRA.md`.
  - Support high-performance Focal, Zonal, and Global operations.
- [ ] **Crater Detection & Analysis**:
  - Integrate automated crater marking and d/D statistics generation.
- [ ] **Path Planning**:
  - Multi-constraint path planning (slope, energy, comms) using the analytics engine.
- [ ] **MCP Interface**:
  - Implement Model Context Protocol (MCP) to allow LLMs to directly drive MapViewer and Analytics.
- [ ] **Radio Analysis**:
  - Framework error rate (FER) and line-of-sight analysis using terrain and horizons.

---

## 8. Cross-Cutting Concerns
- **CI/CD**: Automate builds and tests for MapViewer (.NET), MoonLayers (JS/Python), and new_horizon (C#/ILGPU).
- **Documentation**: Maintain a unified API reference across C# and Python components.
- **Performance**: Monitor GPU VRAM usage on the target hardware (NVIDIA 5090 Mobile) to ensure coexistence with LLMs.
