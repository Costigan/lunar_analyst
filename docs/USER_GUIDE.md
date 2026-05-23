# Lunar Analyst User Guide

## Overview
Lunar Analyst is a high-fidelity lunar south-pole analysis toolkit designed for mission planning and terrain validation. It combines a FastAPI-driven backend with a React-based web interface and high-performance native compute engines. 

The application is **Scenario-centric**. A Scenario is a project directory containing a primary Digital Elevation Model (DEM), a local database (`scenario.db`), and all derived products (hillshades, horizons, etc.).

---

## Major Features

### 1. Scenario Management
The **Scenario Explorer** is your primary navigation tool for the lunar surface.
- **Selection:** Clicking a scenario loads its specific DEM, product catalog, and map state.
- **Auto-Discovery:** The backend monitors your configured scenarios root. New folders containing a `primary_dem.tif` are automatically ingested.
- **Refresh:** The explorer automatically updates after analysis jobs finish, ensuring new files are immediately visible.
- **Filtering:** Use the search bar in the Explorer to filter scenarios by name or metadata using strict token-substring matching.

### 2. Map Workspace & Layer Control
The main workspace provides a high-performance OpenLayers map optimized for the lunar south pole.
- **Layer Manager:** Control visibility, opacity, and rendering styles for both local rasters and NASA Moon Trek overlays.
- **Styling & Colormaps:** Apply specialized colormaps (e.g., Slope thresholds, PSR masks) via the layer's properties. The backend performs nearest-neighbor resampling to preserve scientific integrity.
- **NASA Moon Trek:** Search and add global NASA data products. The system handles the complex task of reprojecting global WMTS tiles into the south-polar stereographic space (`ESRI:103878`).
- **Vector Data:** GeoJSON files added to a scenario are normalized to the map CRS and can be toggled as overlays.

### 3. Jobs & Analysis
Heavy computations are handled by the **Jobs Manager** using a queued worker thread.
- **Job Types:**
  - **Horizon Generation:** GPU-accelerated calculation of 360° horizon profiles from terrain.
  - **Hillshades:** Standard and high-fidelity lighting simulations.
  - **PSR Analysis:** Generation of Permanently Shadowed Region rasters.
  - **Lightmap Reduction:** Temporal analysis of Sun/Earth visibility.
- **Parameters:** Jobs use a dedicated modal for parameter configuration. It includes typed inputs and validation to prevent runtime failures.
- **Execution:** Monitor live progress, elapsed time, and detailed log messages. Jobs can be cancelled at any time from the UI.

### 4. Assistant & AI Integration
The **Assistant** panels (Input and Response) provide a natural language interface to the toolkit.
- **Prose Commands:** Command the application using text (e.g., "Show me the slope map for Shackleton").
- **Tool Execution:** The assistant can launch jobs, update layer styles, and navigate scenarios.
- **Confirmation Gate:** Any action that modifies the filesystem or launches a job requires user confirmation in the Response panel.
- **MCP (Model Context Protocol):** Supports external agents like Codex or Gemini CLI to perform complex multi-step analysis directly against the Lunar Analyst API.

### 5. Notebook Analytics (Marimo)
For custom analysis, Lunar Analyst integrates with **Marimo** notebooks.
- **Interactive Widgets:** Use the `moonlayers_pkg` to embed interactive maps directly in your notebooks.
- **Local Data Access:** Notebooks can stream scenario GeoTIFFs via a local high-performance range-request server.
- **Automation:** Author Python scripts as "Notebook Jobs" that can be launched from the main UI, complete with progress tracking and automatic artifact registration.

---

## Coordinate Reference Systems (CRS)
Lunar Analyst is rigorous about spatial discipline:
- **Primary Projection:** `ESRI:103878` (Lunar South Pole Stereographic).
- **Fixed Frame:** All data is processed in the Moon Mean Earth (ME) fixed frame.
- **Warping:** The backend automatically warps source rasters to the display CRS for the map, while preserving original data for notebook-based analysis.
