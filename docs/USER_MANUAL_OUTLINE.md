# Lunar Analyst User Manual Outline

## 1. Introduction
*   Overview of Lunar Analyst
*   Mission & Scope: Lunar South Pole analysis
*   Platform Support: Linux (Host-native and Container)
*   Key Technologies: FastAPI, React, .NET 9.0 (moonlib)

## 2. Getting Started
*   System Requirements
*   Installation & Setup (Linux-only)
*   Launching the Application
*   The Concept of a "Scenario"

## 3. The Workspace Interface
*   **Activity Bar (Left):** Navigating primary surfaces
*   **Scenario Explorer:** Managing projects and files
*   **Layer Manager:** Controlling map overlays and styles
*   **Map View:** Interaction, navigation, and coordinate readouts
*   **Tools Panel:** Launching analysis jobs
*   **Assistant (Sidebar & Center):** Conversational analysis
*   **Jobs Manager:** Monitoring background tasks
*   **Messages Pane:** Logs and status history

## 4. Working with Scenarios
*   Creating a New Scenario (`scenario.toml` vs Manual Ingest)
*   Switching Between Scenarios
*   Understanding the Filesystem Structure
*   Primary DEM and CRS (ESRI:103878)
*   Importing Data (GeoTIFFs, GeoJSON, CSV)

## 5. Map Visualization & Styling
*   **Layer Controls:** Visibility, Opacity, Brightness, and Contrast
*   **Colormaps:** Scientific palettes for terrain and lighting
*   **Raster Inspection:** Pixel values and coordinate transformations
*   **NASA Moon Trek Integration:** Searching and adding global data

## 6. Analysis Tools & Jobs
*   **Terrain Analysis:**
    *   Slope, Aspect, and Hillshade generation
    *   PSR (Permanently Shadowed Region) detection
*   **Lighting & Horizons:**
    *   Horizon Generation (GPU-accelerated)
    *   Lightmap streaming and time-aggregated products
*   **Viewshed Analysis:**
    *   Line-of-Sight (LOS) visibility
    *   Single vs. Multi-observer viewsheds
*   **Mask & Region Operations:**
    *   Connected component labeling
    *   Filtering regions by size and connectivity metrics

## 7. Map Algebra & Scripted Transforms
*   **Map Algebra (`raster.calculate`):**
    *   Restricted DSL for fast raster expressions
    *   Supported operators and functions
*   **Raster Transform (`raster.transform`):**
    *   Vectorized NumPy-style scripting
    *   Multi-statement logic and intermediate variables
    *   Temporal signal integration (Sun/Earth visibility)

## 8. AI Assistant
*   Interacting with the Assistant
*   Capabilities: Discovery, Execution, and Artifact description
*   Deterministic Command Routing (Imperative intents)
*   Mutation Confirmation & Safety Policy
*   Rich Artifact Previews (Tables, Plots, Cards)

## 9. Notebooks & Python Scripting
*   **Marimo Notebooks:** Interactive, document-based analysis
*   **Python Scripts:** Automating workflows with `notebook_helper`
*   **Artifact Registration:** Ensuring script outputs appear in the UI
*   **Runtime Modes:** `osgeo` vs `moonlib` isolation

## 10. Advanced Configuration
*   Environment Variables & Secrets
*   Custom Colormap Definitions
*   MCP (Model Context Protocol) for External Agents
*   Backend configuration (`lunar_analyst.toml`)

## 11. Troubleshooting & Support
*   Common Error Codes
*   Log Inspection
*   Reporting Bugs
