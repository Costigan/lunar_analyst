# Plan: Lunar Analyst Python Implementation

## 1. Vision & Goals
Transition the Lunar Analyst desktop application from its current C# (.NET 9 WinForms) architecture to a modern, browser-based system with a primary Python backend. This pivot unifies the mapping engine across both interactive notebooks (Marimo) and the standalone analysis tool, while maintaining access to high-performance C# compute kernels via `pythonnet`.

### Key Objectives:
- **Python-First:** Use Python for the core backend, project management, and analytics pipelines.
- **Browser-Based UI:** Replace SkiaSharp/WinForms with OpenLayers and React.
- **C# Integration:** Reuse `new_horizon` (moonlib) for precision lighting/horizons via `pythonnet`.
- **Marimo Integration:** Embed or co-host a Marimo server for "notebook-first" research.

---

## 2. System Architecture

### 2.1 Backend (Python / FastAPI)
- **API Server:** A FastAPI application acting as the central coordinator.
  - **Asset Service:** Streams local COGs (Cloud Optimized GeoTIFFs) using HTTP Range requests.
  - **Project Service:** Manages the SQLite/SpatiaLite project database and layer registry.
  - **Compute Service:** Wraps `pythonnet` calls to C# DLLs for horizon and lighting generation.
- **Marimo Integration:** The backend will launch and manage a Marimo server instance. Notebooks will communicate with the main application state via a shared REST API or WebSockets.

### 2.2 Frontend (OpenLayers / React)
- **Map Core:** A standalone version of the `MoonMap` component (from `moonlayers`).
- **UI Shell:**
  - **Layer Manager:** Tree view for toggling visibility, opacity, and ordering of layers (WMTS, GeoTIFF, GeoJSON).
  - **Timeline Control:** Interactive slider to drive lighting simulations and "New Horizon" time-series maps.
  - **Analytics Panel:** UI for triggering Dask/xarray pipelines (Slope, Roughness, Viewshed).

### 2.3 C# Compute Bridge (`pythonnet`)
- **Assembly Loading:** Dynamically load `moonlib.dll` and its dependencies (e.g., `cspice.dll`, `ILGPU`).
- **Wrapper API:** A Pythonic interface for:
  - `QuadTreeHorizonGenerator`: Generating horizon profiles from DEMs.
  - `LightmapPipeline`: Synthesizing lighting products for specific timestamps.
  - `MapAlgebra`: High-performance focal/zonal operations.

---

## 3. Implementation Phases

### Phase 1: Foundation (Short-Term)
- [ ] **FastAPI Scaffold:** Set up the backend structure with `geotiff_server.py` integration.
- [ ] **React Map Viewer:** Create a standalone React app using OpenLayers that replicates basic `MoonMap` functionality.
- [ ] **Project Registry:** Implement the SpatiaLite schema for tracking layers and project metadata.

### Phase 2: C# Integration (Short-Term)
- [ ] **PythonNet Setup:** Verify loading of `moonlib` in the `env_311` environment.
- [ ] **Horizon Service:** Create a Python service that consumes a DEM, calls C# to generate horizons, and saves the output (e.g., as `.bin` or `.tif`).
- [ ] **Lighting Service:** Expose time-series lighting generation to the backend API.

### Phase 3: Notebook & Analytics (Medium-Term)
- [ ] **Marimo Hosting:** Integrate the Marimo server into the backend lifecycle.
- [ ] **Direct Layer Injection:** Allow Marimo notebooks to "push" a generated GeoTIFF or GeoJSON to the standalone map viewer via an `add_layer` API call.
- [ ] **Dask Pipelines:** Codify the standard terrain analysis (Slope, Roughness, PSR) as reusable Python modules.

### Phase 4: Feature Parity & UX (Long-Term)
- [ ] **Timeline UI:** Implement the C# "Timeline" equivalent in React for scrubbing through lighting data.
- [ ] **Label/Crater System:** Port the C# label rendering logic to OpenLayers (using Vector layers + WebGL).
- [ ] **Tauri Packaging:** (Optional) Wrap the system in Tauri for a native desktop feel with local filesystem access.

---

## 4. Technology Stack Summary
- **Frontend:** React, OpenLayers, Tailwind CSS, `geotiff.js`.
- **Backend:** Python 3.11+, FastAPI, Uvicorn.
- **Compute:** `pythonnet` (C# Bridge), Dask, xarray, rioxarray, GDAL.
- **Interactive:** Marimo.
- **Database:** SQLite + SpatiaLite / GeoPackage.

---

## 5. Next Steps
1.  **Prototype C# Bridge:** Create a small Python script to call `ReferenceHorizonGenerator` from `moonlib.dll` to confirm the `pythonnet` configuration.
2.  **Shared Mapping Lib:** Extract the OpenLayers logic from `moonlayers/static/index.js` into a reusable library.
3.  **FastAPI Entry Point:** Implement the basic file-serving and project-listing endpoints.
