# Consolidated System Design: Lunar Analyst Suite

## 1. Context & Objectives
The Lunar Analyst project is a project-based toolkit for lunar south pole mission analysis. it couples high-fidelity desktop visualization with notebook-first analytics and high-performance computation engines. The goal is to support the full mission lifecycle—from global site exploration (km-scale) to landing hazard analysis (cm-scale) and lighting validation.

## 2. System Architecture Overview
The project is composed of four primary pillars sharing a common filesystem-based project structure:

1.  **Desktop MapViewer** (`lunar_analyst/mapviewer`): .NET 9 + SkiaSharp desktop GIS for immersive exploration.
2.  **MoonLayers Widget** (`moonlayers/`): anywidget + OpenLayers widget for Marimo/Jupyter environments.
3.  **New Horizon Engine** (`new_horizon/`): GPU-accelerated C# engine for precision terrain visibility/horizons.
4.  **Analytics Backend**: Dask/xarray pipelines for large-scale raster processing and a high-performance C# Map Algebra library.

### Repository Structure Note
- **Authoritative MoonLayers**: The library in the root `moonlayers/` directory is the intended version. The directory `lunar_analyst/moonlayers/` contains an obsolete version and should be ignored for new development.

---

## 3. Pillar Details

### 3.1 Desktop MapViewer (WinForms, .NET 9)
- **Rendering Architecture**: The `ILayer` interface unifies Raster, Vector, Tile, and Label layers. A renderer registry in `MapControl` routes layers to specialized renderers (`RasterRenderer`, `VectorRenderer`, `TileRenderer`).
- **SkiaSharp Core**: Bypasses standard GIS controls to render directly to a SkiaSharp canvas. `RenderCache` manages GPU/CPU textures to maintain 60 FPS performance during pan/zoom.
- **Data Support**:
    - **Rasters**: GDAL-backed windowed reads (typically 2048x2048 blocks) to support multi-gigabyte GeoTIFFs without memory exhaustion.
    - **Vectors**: `NetTopologySuite` (NTS) for GeoJSON parsing and spatial math; SkiaSharp for stroke/fill styling.
    - **Tiles**: `BruTile` integration for WMTS/PMTiles, featuring a multi-tier cache (GPU/RAM -> SQLite-managed Disk -> Web).
- **UI Shell**: DevExpress ribbon/dock layout hosting the MapControl, Layer Manager, Timeline, and properties panels. Layer state adapters synchronize UI controls with layer model properties.
- **Extensibility**: Lightweight REST API for headless control from Python; file-watchers for hot-reloading assets modified by backend pipelines.

### 3.2 MoonLayers Widget (Notebook Widget)
- **Frontend Stack**: Vite-built ES modules using OpenLayers, `proj4`, `geotiff.js`, and `ol-layerswitcher`.
- **Backend Integration**: `moonlayers/geotiff_server.py` provides a per-process HTTP server streaming local COGs via range requests, bypassing browser Data URL limits.
- **NASA Trek Integration**: Built-in boolean search engine for the Moon Trek catalog (800+ layers); auto-configures `TileGrid` from WMTS GetCapabilities metadata.
- **Implementation**: Uses `sync_geotiffs` and `_widget_ready` signals to resolve asynchronous initialization race conditions in notebook environments.

### 3.3 New Horizon Engine (C#, ILGPU/CUDA)
- **Objective**: Generate 0.25° angular resolution (1440 bins) horizon profiles from 2D DEM height-fields.
- **Technology**: `ILGPU` enables high-performance CUDA kernels. Features a multi-level Min-Max quadtree (pyramid) for efficient hierarchical ray-casting.
- **Mathematical Rigor**: 4th-order polynomial ray representation; rigorous geodetic corrections for lunar spherical geometry and polar map projection distortions.
- **Modes**: Support for "Reference" (CPU/Double) and "QuadTree" (GPU/Fast) modes to ensure scientific ground-truth validation.

### 3.4 Analytics & Map Algebra
- **Python Pipeline**: Orchestrated in Marimo using `xarray`, `Dask`, `rasterio/rioxarray`, and `GeoPandas`. 
    - **Pattern**: Builds lazy Dask graphs; uses `.persist()` on expensive intermediates like slope/roughness.
    - **Caching**: Hashes parameter combinations to avoid re-running expensive calculations; emits outputs as COG, Zarr, or GeoPackage.
- **C# Map Algebra**: A foundational library implementing a taxonomy of geospatial operators (Local, Focal, Zonal, Global) optimized for C#/.NET 9, acting as a high-performance alternative to Python for critical loops.

---

## 4. Integration & Workflows

- **Project Lifecycle**: Define Project DEM/Projection -> Preprocess/Align ancillary rasters -> Generate derived layers via Python/C# pipelines -> Register outputs in SQLite/SpatiaLite -> Visualize in MapViewer/MoonLayers.
- **Data Sharing**: Both desktop and notebook clients point at the same project directory. Clients use timestamp and size checks to monitor for updates.
- **Coordinate Reference Systems**: Primary projection is ESRI:103878 (Lunar South Pole Stereographic). All components respect the Moon Mean Earth fixed frame. GDAL/Proj are used for all coordinate transformations.

---

## 5. Current State & Known Gaps

- **MapViewer**: Phase 1b complete. Core layer types render and BruTile caching is active. Gaps include a disk cache eviction policy, WMTS capabilities discovery UI, and a full REST API implementation.
- **MoonLayers**: Production-ready for notebooks. Lacks HTTP response caching, additional layer protocols (XYZ/WMS), and advanced measurement tools.
- **Analytics**: Design is clear; implementations for automated viewshed/insolation generators and cache management tools are in progress.
- **New Horizon**: Standalone engine is complete; needs closer integration with the visual MapViewer and the main Python pipeline as a service.

## 6. Design Principles
- **Performance First**: GPU-first rendering and compute (SkiaSharp, ILGPU).
- **Project-Centric**: Unified filesystem structure for all artifacts.
- **Offline Readiness**: Robust disk caching for tiles and terrain data.
- **Scientific Accuracy**: Double-precision geodetic math for all polar transformations.
