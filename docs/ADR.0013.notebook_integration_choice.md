# ADR.0013: Notebook Integration Choice (Marimo vs. Jupyter)

## Status
Accepted

## Context
Lunar Analyst requires an interactive analytics environment to complement its fixed-layout GIS map interface. This environment must support exploratory data analysis, custom Python scripting, and visualization of lunar geospatial products.

The integration needed to support:
1.  **Scenario Scoping:** Seamlessly switching the notebook's working directory to the active scenario root.
2.  **Reactive UI:** A modern, cell-based interface that feels cohesive with the React-based map workspace.
3.  **Headless Execution:** The ability to run notebooks as background "jobs" with progress tracking and automatic artifact registration.
4.  **Deep Map Integration:** A way for notebook code to trigger actions in the main map UI (e.g., adding a layer or zooming to a result).

## Decision
We chose **Marimo** as the authoritative notebook and analytics integration for Lunar Analyst.

### 1. Rationale for Marimo
-   **Reactive Execution:** Unlike Jupyter's linear/out-of-order execution, Marimo's reactive graph ensures that code cells stay in sync, reducing hidden state bugs during complex lunar analysis.
-   **Pure Python Format:** Marimo notebooks are stored as standard `.py` files. This simplifies version control, enables easy discovery as headless jobs, and allows scripts to be executed without a specialized notebook server.
-   **Scenario-Scoped CWD:** Marimo's architecture allows for easy re-homing. When a user clicks "Open in Marimo" from a scenario, the backend restarts the Marimo server with the scenario's root directory as the current working directory (`CWD`).

### 2. Implementation Strategy
-   **Interactive Bridge:** The `MarimoService` manages the lifecycle of the Marimo process (launch, status, stop). It injects repository import paths (e.g., `moonlayers_pkg`) so notebooks can immediately access the project's internal libraries.
-   **Notebook SDK & Helpers:** A specialized `backend.notebook.notebook_helper` provides high-level primitives for:
    -   Accessing scenario-relative paths and the primary DEM.
    -   Reporting progress to the main Jobs Manager.
    -   Registering new GeoTIFFs or tables as scenario artifacts.
-   **Map Command Channel:** A lightweight command API (`/map-commands/zoom-to-file`) allows notebooks to emit WebSocket events that the main React app listens to, enabling "Zoom to Result" behaviors triggered from code.
-   **Headless Job Runner:** Marimo notebooks are discovered in scenario roots and configured "job roots." They are executed using `marimo run`, which executes the notebook as a script while capturing its output and progress via the `NotebookJobContext`.

### 3. MoonLayers Integration
The `moonlayers_pkg` provides a specialized `anywidget` for Marimo. This widget allows notebooks to embed high-performance OpenLayers maps that can stream local scenario COGs (Cloud Optimized GeoTIFFs) through a dedicated range-request server, bypassing browser-side data limits.

## Consequences
-   **Developer Experience:** Users can author complex analysis in an interactive notebook and then "publish" it as a reusable job handler without changing the file format.
-   **Cohesion:** The "Open in Marimo" flow provides a tight link between the visual explorer and the code environment.
-   **Stability:** Isolated subprocesses for Marimo and headless runs ensure that notebook errors do not crash the backend.
-   **Complexity:** Managing the lifecycle of a secondary web server (Marimo) alongside the FastAPI backend adds networking and process-control overhead (e.g., port management and session tokens).
-   **Learning Curve:** Users familiar with Jupyter may need to adjust to Marimo's reactive execution model and file format.
