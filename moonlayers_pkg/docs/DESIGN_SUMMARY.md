# MoonLayers Detailed Design Summary

## 1. Overview
MoonLayers is an interactive lunar mapping library designed for Marimo and Jupyter notebook environments. It provides a high-level Python API to an OpenLayers-based map, specifically optimized for lunar data visualization, with a focus on the Moon's South Pole. The project leverages `anywidget` for seamless Python-JavaScript integration.

## 2. Architecture

### 2.1 Python-JavaScript Split
- **Python Layer (`moonlayers/moon_map.py`)**: Provides the user-facing API. It manages widget state via Traitlets, implements a boolean search engine for the NASA Moon Trek catalog, and hosts an integrated HTTP server for local GeoTIFFs.
- **JavaScript Layer (`src/`)**: Orchestrates the OpenLayers map. It handles projection registration, WMTS/GeoTIFF/GeoJSON layer creation, interactive controls, and bidirectional event communication.

### 2.2 Communication Model
- **State Synchronization**: Uses `anywidget`'s traitlet syncing for configuration (projections, layers, view state).
- **Command Channel (`_command`)**: A specialized trait used to send imperative commands from Python to JavaScript (e.g., `set_view`, `export_png`).
- **Event Channel (`_event`)**: A trait used to send events from JavaScript to Python (e.g., feature clicks, extent changes, export completion).
- **Command Queueing**: To handle the inherent latency in notebook widget initialization, the Python side implements a queue that buffers commands until the JavaScript side signals it is ready (`_widget_ready`).

## 3. Core Components

### 3.1 Projection System (`src/projection.js`)
- **Lunar Specifics**: Sets the Moon's radius to **1,737,400 meters** for accurate geodesic measurements and scale.
- **ESRI:103878**: The primary projection for the Moon's South Pole (Stereographic).
- **Equivalency Mapping**: Automatically maps various URN and non-standard codes (like Moon Trek's `EPSG::0`) to registered projections to ensure compatibility with diverse WMTS services.
- **Custom Proj4 Support**: Allows users to provide their own Proj4 strings for specialized mapping needs.

### 3.2 Integrated GeoTIFF Server (`moonlayers/geotiff_server.py`)
- **The Problem**: Browser limitations on Blob and Data URLs prevent concurrent HTTP Range requests, which are essential for Cloud Optimized GeoTIFF (COG) tile streaming.
- **The Solution**: An embedded, threaded Python HTTP server (singleton pattern) that serves local files over localhost.
- **Features**:
  - Zero-configuration: Starts automatically on the first `add_geotiff` call.
  - Range Request Support: Fully implements HTTP Range headers for efficient tile fetching.
  - Security: Binds only to `127.0.0.1`.

### 3.3 Layer Management

#### 3.3.1 NASA Moon Trek Integration (`src/trek-layers.js`)
- **Catalog Search**: Python-side boolean parser (`AND`, `OR`, `NOT`, grouping) allows searching over 800+ NASA layers.
- **WMTS Capabilities**: Frontend parses GetCapabilities XML to auto-configure tile grids.
- **Feature Layers**: Supports ArcGIS MapServer REST API for vector data, with bounding-box-based lazy loading.
- **GeoTIFF Support**: Locates and loads remote COGs from NASA servers.

#### 3.3.2 GeoTIFF Rendering (`src/layers.js`)
- **WebGL Rendering**: Uses `WebGLTileLayer` for high-performance raster rendering.
- **Dynamic Styling**: Auto-detects band counts to apply grayscale, RGB, or RGBA styles. Supports custom min/max normalization and nodata transparency via WebGL expressions.

#### 3.3.3 GeoJSON & Vector Overlays
- Supports arbitrary GeoJSON with customizable styling (stroke, fill, icons).
- Interactive features include hover highlighting and click popups with property inspection.

## 4. UI/UX Features
- **Search Panel**: A collapsible UI for discovering and adding layers from the Trek catalog.
- **Layer Switcher**: Integrated `ol-layerswitcher` for visibility and opacity control.
- **Layer Ordering**: Custom-built UI controls (↑↓) injected into the layer switcher to manage the Z-order of layers.
- **Measurements**: Support for both planar and geodesic (Moon-radius-aware) distance and area calculations.
- **Export**: PNG and PDF export functionality, utilizing a composition of multiple canvas layers.

## 5. Implementation Nuances

### 5.1 Widget Readiness and Latency
In Marimo, the first load of a widget can have significant sync latency. The design handles this by:
1.  **Readiness Flag**: `_widget_ready` signal from JS to Python.
2.  **Command Buffering**: Python stores actions in `_command_queue` until the flag is true.
3.  **Wait Method**: `wait_until_ready()` provides an optional way for users to block until the frontend is confirmed active.

### 5.2 NASA Moon Trek Quirks
- **NaN Extents**: A fix in `src/wmts.js` handles cases where Moon Trek capabilities return invalid extents, reconstructing them from tile matrix definitions.
- **Equivalent Projections**: Multiple aliases for polar projections are registered to ensure OpenLayers matches the correct TileGrid.

### 5.3 GeoTIFF "White Box" Fix
Standard rendering without a WebGL style often results in a white rectangle for single-band data. MoonLayers solves this by generating appropriate WebGL style expressions that map raw pixel values to color ramps or grayscale.

## 6. Future Directions
- **3D Visualization**: Potential integration with Cesium or similar for lunar globes.
- **Vector Tiles**: Support for MVT layers for large-scale vector data.
- **Advanced Styling**: Predefined color ramps (viridis, jet) for scientific rasters.
- **North Pole Support**: Expansion of projections to include the lunar North Pole.
