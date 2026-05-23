# The Role of a 'Scenario'

A scenario is the fundamental unit of analysis and the primary
organizational boundary in Lunar Analyst. The tool is
"scenario-based," meaning that all mission analysis, data imports, and
computations are scoped to a specific study area.

- Authoritative Workspace: It serves as a self-contained environment
  where a user performs analysis on a specific lunar region.

- Coordinate Ground Truth: Each scenario is anchored by a primary
  Digital Elevation Map (DEM). This DEM defines the scenario's
  Coordinate Reference System (CRS), spatial bounds, and
  resolution. All other raster products within the scenario are
  expected to align with this grid.

- Lifecycle Hub: The FastAPI control plane manages the scenario
  lifecycle (creation, updates, discovery). It serves as the parent
  container for all Products (derived or imported data), Jobs (compute
  tasks like hillshade or horizon generation), and Layer States (how
  data is visualized).

# How Scenarios are Described and Represented

## Filesystem Representation

Scenarios are physically represented as self-contained directories
within a managed workspace.

- Naming: Each scenario uses a scenario_root slug (e.g.,
  shackleton-crater) as its directory name.
- Structure:
  - scenario.db: A local SpatiaLite database containing the scenario's
    specific metadata, product registry, and job history.
  - dem.tif: Canonical primary DEM filename.
  - hillshade.tif: Canonical default hillshade for the primary DEM.
  - Additional product/artifact files: placement is intentionally
    flexible, with type/provenance tracked in `scenario.db`.

# Database Representation

The system uses a two-tier database model to represent scenarios:

- Global Catalog (`scenario_catalog.db`): A workspace-wide database
  that tracks every discovered scenario. It stores identity
  (scenario_id, name), absolute paths, cached metadata (size_bytes,
  last_touched), and footprint geometry (the DEM's spatial polygon
  used for map-based selection).
- Local Scenario DB (`scenario.db`): Resides inside the scenario
  directory and contains the granular details of products and jobs
  specific to that analysis.

# API and Data Model

- Contract Resource: In the Stage 1 API contract, Scenario is a core
  resource. It is represented in JSON with fields for its root slug,
  primary DEM metadata (CRS, footprint), and administrative info
  (owner, created timestamps).

- ElevationMap Class: In the Python backend, the concept is
  represented by an ElevationMap class which encapsulates the spatial
  properties (pixel size, bounds, nodata values) that govern the
  analysis within that scenario.

# UI Representation

In the browser and notebook clients, scenarios are represented as:

- Map Footprints: Polygons on a global map that allow users to
  "click-select" a scenario for deep analysis.

- Contextual Scoping: When a scenario is selected, the UI filters
  available layers, products, and compute tools to only those relevant
  to that specific workspace.

# GUI Conventions for Scenario and Product Explorer

To facilitate discovery and analysis, the UI provides a centralized
explorer for navigating scenarios and their associated products.

## 1. The Scenario Explorer (Tree-Grid)

The primary interface is a multi-column tree-grid that provides a hierarchical view of the workspace.

- **Hierarchy:** Scenarios are the top-level nodes. Expanding a
  scenario reveals its products, which are further grouped by `kind`
  (e.g., Elevation, Lighting, Vectors).

- **Metadata Columns:**
    - **Name (Fixed):** The display name or ID of the scenario or product.
    - **Type:** The product `kind` and `subkind` (e.g., "Raster / Hillshade").
    - **Created:** UTC timestamp of creation or import.
    - **Size:** Formatted file size (e.g., "450 MB").
    - **Provenance:** Lineage summary (e.g., "Imported" or "Job:
        generate_horizons").

- **Column Visibility:** A header context menu allows users to show or
  hide metadata columns (excluding the Name column) to optimize screen
  real estate.

## 2. Drag-and-Drop Workflows

### Map Integration (Layer Addition)

Users can add products to the map by dragging them from the Explorer
onto the Map Canvas or the Layer Manager.

- **Canvas Drop:** Automatically creates a new `LayerState` and adds
  the product as the top-most layer.

- **Layer Manager Drop:** Allows the user to insert the product at a
  specific position in the layer stack (Z-index).

### Marimo Notebook Integration (Code Injection)

The Explorer supports dragging products or scenarios directly into Marimo notebook cells to facilitate "notebook-first" research.

- **Product Drop:** Inserts a Python code snippet referencing the
  specific product ID.

  - *Example:* `analyst.get_product("scn_shackleton",
    "prd_hillshade_01")`

- **Scenario Drop:** Inserts a reference to the scenario object.
  - *Example:* `scenario = analyst.get_scenario("scn_shackleton")`
