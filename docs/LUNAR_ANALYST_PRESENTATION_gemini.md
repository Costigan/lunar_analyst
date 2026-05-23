# Presentation Plan: Lunar Analyst Project Overview

**Target Audience:** NASA MSFC AI and Lunar Dataset Experts  
**Goal:** Technical overview of the Lunar Analyst toolkit architecture, capabilities, and development status.

---

## Slide 1: Title Slide
- **Title:** Lunar Analyst: A Modern Toolkit for South Pole Mission Analysis
- **Subtitle:** Transitioning from Legacy Desktop to a Browser-First, AI-Assisted Architecture
- **Presenter:** [Your Name/Team]
- **Context:** Prepared for NASA MSFC AI & Lunar Data Teams

---

## Slide 2: Motivation & Project Goals
- **The Challenge:** High-fidelity lunar south pole analysis (terrain, lighting, Earth/Sun visibility) is computationally expensive and traditionally siloed in desktop apps.
- **The Vision:** 
  - Centralized "Scenario-as-a-Workspace" model.
  - High-performance native compute paired with flexible Python/Notebook workflows.
  - Seamless transition from exploratory analysis (Marimo) to production visualization (OpenLayers).
- **Key Target:** Lunar South Pole (ESRI:103878).
- **[IMAGE PLACEHOLDER]:** Side-by-side comparison of a legacy .NET UI vs. the new modern browser-based Map View with a hillshade overlay.

---

## Slide 3: System Architecture: The Four-Process Model
- **Topology:**
  1. **FastAPI Backend:** Authoritative control plane; owns scenario lifecycle, API contracts, and asset serving.
  2. **Compute Worker:** Dedicated Python process hosting `pythonnet` + `.NET 9` `moonlib.dll` for heavy compute (CPU/GPU).
  3. **Marimo Notebooks:** Interactive exploration; uses the same REST/WS contracts as the UI.
  4. **React Client:** OpenLayers-based GIS visualization (Desktop-class performance in the browser).
- **[DIAGRAM PLACEHOLDER]:** Process topology diagram showing communication paths (HTTP/WS) between API, Worker, Marimo, and Client.

---

## Slide 4: Data Model & The "Scenario" Concept
- **Self-Contained Workspaces:** Everything for an analysis lives in a folder (`primary_dem.tif`, `scenario.db`, products).
- **SpatiaLite Metadata:** Native spatial SQL for product lineage and metadata.
- **CRS Discipline:** Strict adherence to Lunar South Pole Stereographic; no silent reprojections.
- **Asset Pipeline:** Automatic COG (Cloud Optimized GeoTIFF) conversion for efficient web delivery.
- **[IMAGE PLACEHOLDER]:** Screenshot of the "Scenario Explorer" UI showing a tree view of DEMs, hillshades, and temporal products.

---

## Slide 5: The Compute Engine (Native Bridge)
- **Hybrid Execution:** Why we use `pythonnet` to bridge Python's ecosystem with optimized `.NET 9` analysis libraries.
- **Job Management:** 
  - Queued vs. Immediate async jobs.
  - Structured progress events and cancellation support.
- **Key Native Tasks:** Horizon generation, Hillshade, PSR (Permanent Shadow Region) detection.
- **[DIAGRAM PLACEHOLDER]:** Sequence diagram of a job launch: UI -> API -> Worker -> .NET Bridge -> Filesystem -> UI Notification.

---

## Slide 6: Temporal Analytics & Lightmaps
- **Lightmap Bridge (v2):** Streaming large temporal datasets between native code and Python.
- **Reducers:** 
  - Native reducers for speed (Average Sun Fraction, Earth-above-terrain).
  - Python/Numpy/Numba reducers for flexibility (Notebook-authored custom logic).
- **[IMAGE PLACEHOLDER]:** A temporal "Average Sun" heatmap overlay on the lunar south pole terrain.

---

## Slide 7: Notebook-First Workflow (Marimo)
- **Beyond the UI:** Marimo as the "Expert Interface."
- **Headless Execution:** Notebooks can be registered as system jobs and run in the background.
- **Map-Driving:** Notebooks can command the main Map UI (e.g., "Zoom to this newly created product").
- **[IMAGE PLACEHOLDER]:** A Marimo notebook screen showing a code snippet generating a custom slope analysis and the resulting layer appearing in the background map.

---

## Slide 8: AI-Assisted Analysis
- **Strategic AI Integration:** Not just a chatbot, but a tool-using agent.
- **Capabilities:**
  - **RAG (Retrieval-Augmented Generation):** Grounded in project documentation and scenario metadata.
  - **Deterministic Routing:** Fast-path for common GIS commands (e.g., "Show me the slope layer").
  - **Tool Loop:** AI can write/run scripts and inspect results (ADR 0015 rich outputs).
- **[IMAGE PLACEHOLDER]:** The "Assistant" pane showing a prose response alongside a generated histogram of terrain slopes.

---

## Slide 9: Project Status & Roadmap
- **What's Implemented:**
  - Scenario & Catalog core.
  - Native Hillshade/Horizon/PSR jobs.
  - OpenLayers Map Milestone (Layer Manager, Colormaps).
  - Marimo/API integration.
- **Next Steps (Phase 5-6):**
  - Advanced temporal timeline UI.
  - Full legacy capability parity (Viewsheds, LOS).
  - Tauri-based production packaging.
- **[IMAGE PLACEHOLDER]:** High-level roadmap chart showing Phases 1 through 6.

---

## Slide 10: Conclusion & Q&A
- **Summary:** A scalable, Windows-first platform for the next generation of lunar exploration.
- **Contact Info:** [Your Contact Details]
- **Open for Discussion:** Integration with MSFC datasets, AI-driven mission planning, etc.
