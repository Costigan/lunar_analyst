# Lunar Analyst Presentation Plan (Codex)

## 1. Purpose and Audience
- **Audience:** NASA MSFC experts in AI systems and lunar datasets.
- **Presentation goal:** Explain what Lunar Analyst is for, who it is for, what mission tasks it supports, and the current technical status.
- **Tone:** Technical, operational, and evidence-based.

## 2. Core Message
- Lunar Analyst is a mission-analysis platform for lunar south-pole science and operations.
- It enables scientists and mission analysts to move from data ingestion to defensible site/traverse/observation decisions.
- The system combines high-fidelity terrain/lighting analytics, reproducible scenario workflows, and assistant-guided tool execution.

## 3. Suggested Slide Count and Flow
- Target: **10 slides**
- Narrative arc:
1. Users and mission needs
2. Product goals and task coverage
3. How the system supports those tasks
4. Current readiness, risks, and collaboration asks

## 4. Slide-by-Slide Plan (10 Slides)

### Slide 1: Title and Mission Focus
- **Title:** Lunar Analyst: Mission Analysis for Lunar South Pole Operations
- **Subtitle:** Goals, Users, Workflows, and Current Status
- **Key points:**
  - Focus is mission decision support, not generic geospatial tooling.
  - Emphasis on scientific validity and operational usability.
- **Visuals:**
  - `IMAGE PLACEHOLDER`: Lunar south-pole context map with candidate regions.

### Slide 2: Intended Users
- **Title:** Who Lunar Analyst Is For
- **Key points:**
  - Lunar scientists evaluating observation opportunities and constraints.
  - Mission planners assessing landing zones, traverses, and activity windows.
  - Analysis/operations teams needing reproducible scenario-based workflows.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: User personas mapped to mission decisions.

### Slide 3: Mission Tasks Supported
- **Title:** What Tasks the App Helps Solve
- **Key points:**
  - Candidate site screening and comparative evaluation.
  - Terrain and hazard assessment from DEM-derived products.
  - Illumination and Earth-visibility analysis over time.
  - Layer fusion and evidence packaging for review decisions.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Task pipeline from screening -> validation -> recommendation.

### Slide 4: Product Goals
- **Title:** Product Goals and Success Criteria
- **Key points:**
  - Scientific fidelity: CRS rigor, explicit geospatial contracts, robust compute paths.
  - Reproducibility: scenario-root artifacts and lineage-oriented outputs.
  - Analyst efficiency: integrated map/tools/assistant workflow.
  - Operational safety: confirmation-gated mutations and auditable actions.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Goal pillars with measurable outcomes.

### Slide 5: End-to-End Analyst Workflow
- **Title:** Typical Analyst Workflow in Lunar Analyst
- **Key points:**
  - Select scenario and curate relevant layers.
  - Run terrain/lighting jobs and inspect outputs.
  - Iterate through map algebra or scripted transforms.
  - Publish layers/artifacts and capture rationale for mission review.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: End-to-end workflow swimlane.
  - `IMAGE PLACEHOLDER`: Workspace screenshot showing map + tools + layers.

### Slide 6: Core Capabilities (Current)
- **Title:** Capabilities Available Today
- **Key points:**
  - Scenario-based data management and file-backed artifact serving.
  - Native-backed horizon/lightmap compute with progress/cancellation.
  - Raster analytics via `raster.calculate` and `raster.transform`.
  - Moon Trek layer discovery and overlay integration.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Capability matrix (Implemented / Partial / Planned).

### Slide 7: Assistant in Analyst Workflows
- **Title:** Assistant as an Analysis Copilot
- **Key points:**
  - Converts analyst intent into bounded tool workflows.
  - Uses deterministic routing where possible and model reasoning where needed.
  - Enforces mutation confirmation and returns typed output artifacts.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Assistant request -> tool execution -> artifact result lifecycle.

### Slide 8: Architecture That Supports the Mission Tasks
- **Title:** System Architecture (Task-Aligned View)
- **Key points:**
  - FastAPI control plane for scenario/job/tool contracts.
  - Compute worker path for heavy lunar analytics workloads.
  - Marimo/notebook workflows for exploratory analysis through the same APIs.
  - React/OpenLayers workspace for operational analyst interaction.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Architecture diagram annotated by mission task ownership.

### Slide 9: Current Readiness and Known Gaps
- **Title:** Readiness Snapshot and Active Gaps
- **Key points:**
  - Core workflows are implemented and usable for analyst operations.
  - Remaining gaps are explicit (event delivery scope, cache persistence, compute isolation refinements, horizon fitting robustness).
  - Risk management approach: bounded mitigations + regression-driven closure.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Readiness/risk table with mitigation status.

### Slide 10: MSFC Collaboration Opportunities and Ask
- **Title:** Collaboration Ask: Validation and Mission Relevance
- **Key points:**
  - Co-define acceptance criteria for terrain/illumination outputs.
  - Prioritize representative datasets and benchmark scenarios.
  - Evaluate assistant reliability on real mission analysis tasks.
  - Align on pilot use cases and success metrics.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: 90-day collaboration plan with milestones.

## 5. Diagram/Image Asset Checklist (for later insertion)
- Lunar south-pole context map with candidate regions.
- User persona-to-task diagram.
- End-to-end workflow swimlane.
- Workspace screenshots (map, layers, tools, assistant).
- Capability matrix and readiness/risk table.
- Architecture diagram annotated by task.
- Collaboration roadmap timeline.

## 6. Presenter Notes Guidance
- Lead each slide with user task impact, then supporting technical evidence.
- Keep distinction clear between `implemented`, `partial`, and `planned`.
- Avoid platform-history framing unless needed for specific technical context.
