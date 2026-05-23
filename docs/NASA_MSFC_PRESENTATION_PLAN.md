# NASA MSFC Presentation Plan: Lunar Analyst

## 1. Purpose and Audience
- **Audience:** NASA MSFC team with deep expertise in AI systems and lunar datasets.
- **Presentation goal:** Communicate (1) mission goals, (2) architecture and technical rigor, and (3) current status with credible next steps.
- **Tone:** Technical and evidence-oriented, avoiding marketing framing.

## 2. Core Message
- Lunar Analyst is evolving from a desktop-centered tool into a browser-first, API-driven platform for lunar south-pole analysis.
- The system is designed around scientific fidelity (CRS discipline, explicit data lineage, deterministic contracts) and operational usability (scenario-based workflows, assistant + tool execution).
- The project already has substantial implemented capability; remaining gaps are known, bounded, and tractable.

## 3. Suggested Slide Count and Flow
- Target: **14 slides** (+ optional backup slides)
- Narrative arc:
1. Why this exists (mission analysis problem)
2. What we built (platform + architecture)
3. What is working now (status)
4. What risks/gaps remain
5. What collaboration and validation we need next

## 4. Slide-by-Slide Plan

### Slide 1: Title and Context
- **Title:** Lunar Analyst: Browser-First Mission Analysis for Lunar South Pole Science
- **Subtitle:** Goals, Architecture, and Program Status
- **Key points:**
  - Mission context: site selection, hazard assessment, and illumination-driven planning.
  - Scope: scientist-facing workflows, not just engineering demos.
- **Visuals:**
  - `IMAGE PLACEHOLDER`: Lunar south-pole basemap/orthographic context image.

### Slide 2: Problem Statement
- **Title:** Mission Analysis Requires Multi-Scale, Multi-Modal Reasoning
- **Key points:**
  - Decisions span km-scale candidate regions down to local terrain/lighting constraints.
  - Data are heterogeneous: DEMs, derived rasters, vector layers, temporal signals.
  - Analysts need reproducible workflows, not ad hoc GIS operations.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Multi-scale analysis funnel (regional screening -> local hazard/illumination validation).

### Slide 3: Project Goals
- **Title:** Program Goals
- **Key points:**
  - High-fidelity lunar south-pole visualization and analytics.
  - Scenario-centered reproducibility (`primary_dem.tif` + `scenario.db`).
  - Notebook-first exploration plus production API workflows.
  - AI assistant support for domain users with guarded tool execution.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Four-goal quadrant (Fidelity, Reproducibility, Accessibility, Operational Safety).

### Slide 4: Architecture at a Glance
- **Title:** System Topology (Control Plane + Compute Plane + UX Clients)
- **Key points:**
  - FastAPI as authoritative control plane.
  - Separate compute worker for heavy `pythonnet`/`moonlib` workloads.
  - Marimo exploratory process communicates through APIs, not direct DB mutation.
  - React/OpenLayers client (Tauri-hostable) as primary user interface.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: End-to-end architecture block diagram with process boundaries.

### Slide 5: Scientific and Data Integrity Invariants
- **Title:** Non-Negotiable Invariants
- **Key points:**
  - CRS discipline: explicit CRS metadata, no silent reprojection.
  - Scenario-root file safety: normalized paths and out-of-root rejection.
  - File-id based serving and lineage-friendly artifact registration.
  - Structured progress/cancellation for long-running compute.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Invariant guardrails around data/control flows.

### Slide 6: Frontend and User Workflow
- **Title:** Workspace-Centric Analyst Experience
- **Key points:**
  - Dockable React workspace panels (Map, Layers, Tools, Assistant, Moon Trek).
  - Persistent layout and scenario selection.
  - Layer state and ordering management integrated with backend state.
- **Visuals:**
  - `IMAGE PLACEHOLDER`: Screenshot of current React workspace shell.
  - `IMAGE PLACEHOLDER`: Screenshot highlighting layer manager + tools panel.

### Slide 7: Compute and Native Performance Path
- **Title:** Heavy Compute Path (New Horizon + `moonlib`)
- **Key points:**
  - GPU-accelerated horizon generation (ILGPU/CUDA) with CPU reference modes.
  - Streaming lightmap pipeline with explicit stage parallelism.
  - Contracted job execution with cancellation/progress events.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Horizon/lightmap pipeline stages.
  - `IMAGE PLACEHOLDER`: Example horizon or illumination output visualization.

### Slide 8: Assistant + Tooling Architecture
- **Title:** Assistant as Controlled Orchestrator, Not Freeform Executor
- **Key points:**
  - Hybrid deterministic routing + model-loop for mixed prompts.
  - Tool contracts are explicit; mutating operations are confirmation-gated.
  - Typed artifacts (table/image/plot/cards) are produced by tools.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Assistant turn lifecycle (segment -> route -> tool/model -> postcondition).

### Slide 9: Raster Analytics Capability (Current)
- **Title:** Raster Compute Surface Available Today
- **Key points:**
  - `raster.calculate` map algebra with AST validation and bounded complexity.
  - `raster.transform` restricted script runtime with planner and memory limits.
  - Temporal contract support for horizon/lighting-derived signals.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Dataflow from inputs -> planner -> execution -> output GeoTIFF/layer publish.

### Slide 10: Integration with Lunar Data Ecosystem
- **Title:** Lunar Dataset Interoperability
- **Key points:**
  - Moon Trek catalog search and overlay support with layered fallback strategy.
  - COG-oriented raster handling and map-delivery CRS normalization.
  - Shared scenario folder as reproducible handoff artifact.
- **Visuals:**
  - `IMAGE PLACEHOLDER`: Example Moon Trek overlay + scenario layer composition.

### Slide 11: Current Status (What Is Implemented)
- **Title:** Status Snapshot: Implemented Capabilities
- **Key points:**
  - Browser-first UI operational with dockable workspace.
  - Job discovery/execution, notebook integration, and event streaming present.
  - Assistant/MCP stack implemented with safety controls and artifact rendering.
  - Native horizon/lightmap engine integrated through current bridge pathways.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Capability matrix (Implemented / Partial / Planned).

### Slide 12: Known Gaps and Technical Risks
- **Title:** Known Gaps (Explicit)
- **Key points:**
  - Some assistant/event delivery paths are process-local (no brokered bus).
  - Moon Trek cache currently in-memory (not persisted across restarts).
  - `raster.transform` execution isolation is not yet fully worker-only.
  - New Horizon fitting robustness has mitigation fallback; root-cause fix pending.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Risk register table (risk, impact, mitigation, owner/next action).

### Slide 13: Validation and Collaboration Opportunities with MSFC
- **Title:** Where MSFC Expertise Can Accelerate Validation
- **Key points:**
  - Scientific validation of terrain/illumination outputs and edge cases.
  - Dataset curation priorities and benchmark scenario design.
  - Evaluation methodology for assistant reliability on mission-relevant tasks.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Collaboration map (Lunar Analyst team responsibilities vs MSFC partner inputs).

### Slide 14: Near-Term Roadmap and Ask
- **Title:** Next 2-3 Milestones and Requested Feedback
- **Key points:**
  - Harden compute isolation and event delivery architecture.
  - Expand deterministic assistant coverage for mission workflows.
  - Close known robustness gaps and lock regression benchmarks.
  - Ask: align on validation criteria, reference datasets, and pilot workflow.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: 90-day roadmap timeline.

## 5. Optional Backup Slides
- Detailed assistant contract/event schema excerpt.
- CRS and raster delivery policy deep dive.
- Horizon engine math/accuracy tradeoff slide.
- Example end-to-end scenario lifecycle with artifact lineage.

## 6. Diagram/Image Asset Checklist (for later insertion)
- South-pole contextual basemap.
- Architecture topology diagram.
- Analyst workspace screenshots.
- Compute pipeline diagram.
- Assistant lifecycle diagram.
- Capability matrix and risk register graphics.
- Roadmap timeline graphic.

## 7. Presenter Notes Guidance (for drafting later)
- Keep each slide anchored to one technical claim and one evidence point.
- For status slides, distinguish implemented behavior vs target-state design.
- Use concrete terms (`implemented`, `partial`, `planned`) to avoid ambiguity.
