# Plan: Migrating MapViewer to a Browser-Based Architecture

## 1. Vision
Replace the C#-based WinForms desktop renderer with a high-performance, web-based map tool. This will unify the `mapviewer` and `moonlayers` (notebook) codebases, enabling a consistent experience across desktop, web, and research notebooks while leveraging modern web technologies for geospatial visualization.

## 2. Feasibility & Efficiency
- **Feasibility:** High. Libraries like `geotiff.js`, `OpenLayers`, and `MapLibre GL JS` provide mature support for Cloud Optimized GeoTIFFs (COGs), vector tiles, and high-performance rendering.
- **Efficiency:** Improved. Client-side rendering with `geotiff.js` uses HTTP Range requests to fetch only the required pixels for the current view, reducing memory overhead and server load compared to the existing C# GDAL windowed reads.

## 3. Recommended Technology Stack
- **Frontend Core:** **React** (for UI components) + **OpenLayers** (for GIS logic and parity with `moonlayers`).
- **High-Performance Overlays:** **Deck.gl** (WebGL-powered rendering for large vector datasets or complex analytics masks).
- **Raster Processing:** **geotiff.js** (client-side decoding and rescaling of GeoTIFFs).
- **Desktop Packaging:** **Tauri** (Provides a lightweight cross-platform wrapper with secure local filesystem access).
- **Backend Services:** **FastAPI (Python)** (To replace the C# backend, handling GDAL operations, Dask analytics, and project management).

## 4. Proposed Architecture
- **Client (Browser/Tauri):**
  - **Map Component:** OpenLayers manages view state, projections (Lunar South Pole), and layer stack.
  - **Raster Layer:** Custom OpenLayers source using `geotiff.js` to read local files via the Python HTTP server.
  - **UI Layer:** React-based ribbon, layer manager, and timeline (replicating the DevExpress look).
- **Backend (Python):**
  - **Asset Server:** Optimized version of `geotiff_server.py` for streaming local COGs.
  - **Project Service:** Manages the SQLite/SpatiaLite project database and metadata.
  - **Analytics Service:** Exposes Dask/xarray pipelines via REST endpoints.

## 5. Detailed Migration Steps

### Phase 1: Unified Asset Serving (Short-Term)
- [ ] **Enhance Python Server:** Update `geotiff_server.py` to handle more than just GeoTIFFs (e.g., GeoJSON, CSV, and Project DBs).
- [ ] **Implement COG-First Workflow:** Ensure analytics pipelines generate Cloud Optimized GeoTIFFs to maximize browser performance.

### Phase 2: Core Map Port (Medium-Term)
- [ ] **OpenLayers Projection Parity:** Ensure `ESRI:103878` (Lunar South Pole) is perfectly configured in the web environment using `proj4.js`.
- [ ] **Implement `geotiff.js` Renderer:** Port the C# `RasterRenderer` logic (grayscale rescaling, NoData handling) to a client-side WebGL shader or Canvas processor.
- [ ] **Vector Layer Port:** Replicate `VectorRenderer` using OpenLayers native GeoJSON/FlatGeobuf support.

### Phase 3: UI & Desktop Shell (Medium-Term)
- [ ] **React UI Shell:** Build a modern "Desktop-like" UI with a layer tree, opacity sliders, and toolbars.
- [ ] **Tauri Integration:** Wrap the React app in Tauri to allow the user to open local files and save project states directly to their disk.
- [ ] **REST/MCP Integration:** Replace the planned C# REST server with the FastAPI backend, allowing external Python scripts to control the browser map.

### Phase 4: Advanced Features (Long-Term)
- [ ] **Deck.gl Integration:** Use Deck.gl for "New Horizon" visibility masks and large crater catalogs.
- [ ] **Map Algebra Web UI:** Create an interactive "Builder" for map algebra operations that executes on the Python backend.

## 6. Comparison Table

| Feature | C# WinForms (Current) | Web-Based (Planned) |
| :--- | :--- | :--- |
| **Rendering** | SkiaSharp (CPU/GPU) | WebGL / WebGPU |
| **Raster Access** | GDAL (Local Reads) | `geotiff.js` (HTTP Range Requests) |
| **Vector Engine** | NetTopologySuite | OpenLayers / turf.js |
| **UI Framework** | DevExpress | React + Tailwind |
| **Distribution** | .NET Runtime | Browser or Tauri App |
| **Integration** | Custom REST API | Native HTTP / WebSockets |

## 7. Next Steps
1.  **Prototype:** Create a simple React + OpenLayers app that loads a single Lunar DEM via `geotiff.js` and `geotiff_server.py`.
2.  **Benchmark:** Compare memory usage and frame rates between the C# renderer and the WebGL-based browser renderer for a 2GB DEM.
3.  **Refactor `moonlayers`:** Begin extracting the core mapping logic from `moonlayers` into a shared library that both the notebook widget and the new Desktop app can use.
