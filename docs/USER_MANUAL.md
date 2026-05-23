# Lunar Analyst User Manual

## 1. Introduction
Lunar Analyst is a specialized toolkit for high-fidelity lunar south pole mission analysis. It provides an integrated environment for terrain validation, lighting analysis, and landing site characterization. By combining a FastAPI control plane with high-performance native compute engines, Lunar Analyst enables rigorous scientific workflows in an interactive, web-first interface.

### Key Capabilities
- **Scenario-Based Analysis:** Organize your work into discrete projects (Scenarios) that manage their own terrain data, local databases, and derived analysis products.
- **High-Performance Visualization:** Interactive, GPU-accelerated map viewing optimized for the lunar south pole (ESRI:103878).
- **Advanced Terrain & Lighting:** Compute horizons, viewsheds, and time-aggregated lightmaps using optimized native kernels.
- **AI-Driven Workflows:** Command the system and perform complex analysis using a built-in AI assistant and the Model Context Protocol (MCP).
- **Notebook Integration:** Perform custom data science workflows using Marimo notebooks directly within the application workspace.

---

## 2. Getting Started

### System Baseline
- **Operating System:** Host-native Linux (Pop!_OS or Ubuntu) is the required environment.
- **Hardware:** An NVIDIA GPU is strongly recommended for accelerated horizon and viewshed calculations.
- **Runtime:** Python 3.11+ and .NET 9.0.

### Launching the Application
The application is typically launched via a host-native script or Docker Compose:
- **Host Launch:** `scripts/run-host-dev.sh`
- **Container Launch:** `docker compose -f docker/compose.dev.yml up`

Once running, the web interface is accessible via `http://localhost:3000` (or the configured host port).

### The Concept of a \"Scenario\"
The **Scenario** is the foundational unit of Lunar Analyst. Every analysis task is performed within the context of a scenario. A scenario directory contains:
- `primary_dem.tif`: The canonical Digital Elevation Model (DEM) for the region.
- `scenario.db`: A local SQLite/SpatiaLite database for cataloging products, layers, and job history.
- `analysis/`: Sub-directories for derived products like hillshades and viewsheds.
- `lighting/`: Specialized storage for generated horizons and lightmaps.

---

## 3. The Workspace Interface

Lunar Analyst uses a persistent, IDE-style workspace designed for high-density analysis.

### Sidebars (Activity Bar)
- **Scenario Explorer:** Navigate your workspace root, switch scenarios, and manage files.
- **Layer Manager:** Control the visibility, ordering, and styling of map layers.
- **Map Layers:** Search the global NASA Moon Trek catalog and add overlays.
- **Tools:** Configure and launch predefined analysis jobs (e.g., Viewsheds, Horizons).
- **Assistant:** A focused workspace for conversational analysis and complex planning.

### Map View (Center)
The central Map View is optimized for the lunar south pole (`ESRI:103878`).
- **Navigation:** Standard pan and zoom controls.
- **Readout:** Real-time display of coordinates (Longitude/Latitude and Easting/Northing) and pixel-value inspection at the cursor position.

### Sidebars & Panels
- **Assistant Sidebar (Right):** A compact interface for quick conversational commands.
- **Jobs Manager (Bottom):** Monitor background task progress, view logs, and cancel long-running jobs.
- **Messages Pane (Bottom):** A chronological transcript of system messages, logs, and status updates.

---

## 4. Working with Scenarios

### The Scenario Model
A **Scenario** is a self-contained project directory. It serves as the single source of truth for all spatial data and metadata within its bounds.
- **Primary DEM:** The base elevation data used for all derived terrain and lighting products.
- **SpatiaLite Catalog:** Metadata for all products and files is stored in `scenario.db`, ensuring that even complex projects remain discoverable.

### Switching Scenarios
Use the Scenario Explorer to switch projects. Changing the active scenario automatically updates the Map View extent, the Layer Manager, and the Assistant's context to match the new project.

---

## 5. Visualization & Styling

### Colormaps and Styling
Lunar Analyst supports real-time, GPU-accelerated styling for single-band rasters:
- **Colormaps:** Choose from scientific palettes like `Viridis`, `Magma`, `Plasma`, or `Cividis`.
- **Image Adjustments:** Interactively tune Brightness and Contrast without regenerating data.
- **Contour Export:** Export styled rasters as RGBA GeoTIFFs using the `export_colormap_rgba_geotiff` tool.

### NASA Moon Trek Integration
Search over 800+ global lunar layers directly from the UI. Added Trek layers behave like local scenario layers and can be reordered, styled, or removed in the Layer Manager.

---

## 6. Analysis Tools

### Terrain Analysis
- **Viewshed (`terrain.viewshed`):** Calculate visibility from single or multiple observers. Supports a high-performance CUDA backend for large observer sets.
- **Connectivity Metrics:** Analyze observer masks for \"connected\" visibility regions using `terrain.mask_connectivity_metrics`.
- **Horizon Generation:** Generate angular horizon profiles (0.25° resolution) for every pixel in a DEM.

### Lighting Analysis
- **PSR Detection (`lighting.psr`):** Automatically identify Permanently Shadowed Regions based on computed horizons.
- **Lightmap Aggregation:** Compute time-averaged lighting or duration rasters (e.g., \"average sun fraction\" over a lunar month) using high-level streaming workers.

---

## 7. Map Algebra & Raster Transforms

### Map Algebra (`raster.calculate`)
A restricted DSL for fast, single-expression raster math.
- **Example:** `(dem > 2000) & (slope < 15)`
- **Temporal Signals:** Bind `lighting_raster`, `sun_above_horizon`, or `earth_above_horizon` to perform time-aware calculations.

### Raster Transforms (`raster.transform`)
A powerful NumPy-style scripting environment for multi-statement logic.
- **Capabilities:** Vectorized arithmetic, intermediate variables, and advanced reducers (`min`, `max`, `avg`, `std`).
- **Temporal Integration:** Use the reserved `times` binding to stream horizon-derived lighting data for complex temporal analysis.

---

## 8. AI Assistant

The Assistant is a collaborative \"peer programmer\" for your analysis.
- **Discovery:** Ask \"What can you do?\" to see available tools and capabilities.
- **Execution:** Prose commands like \"Switch to the Shackleton scenario\" or \"Calculate a viewshed at 0, 0\" trigger deterministic actions.
- **Safety:** All mutating actions (writing files, changing layer state) require explicit user confirmation.
- **Artifacts:** The assistant renders rich previews of its outputs, including tables, plots, and raster statistics.

---

## 9. Notebooks & Scripting

### Marimo Notebooks
Create interactive notebooks (`.py` files) within your scenario root. Notebooks have full access to the Lunar Analyst API and can render interactive maps using the MoonLayers widget.

### Python Scripting
Automate workflows using the `backend.notebook.notebook_helper` library.
- **Runtime Isolation:** Scripts run in either `osgeo` (standard GDAL/PROJ) or `moonlib` (.NET bridge) modes to prevent dependency conflicts.
- **Artifact Registration:** Ensure your script outputs appear in the UI by using `register_output_if_available()`.

---

## 10. Advanced Configuration

### Secrets & Environment
- Store API keys (OpenAI, Anthropic, Google) in a user-scoped `.env` file or use `direnv`.
- **MCP:** Use the Model Context Protocol to connect external agents (like Codex or Gemini CLI) to your active Lunar Analyst session.

### Troubleshooting
- Check the **Jobs Manager** for detailed task logs and error codes.
- Inspect the **Messages Pane** for real-time system alerts.
- Use the `/bug` command to report issues directly to the development team.
