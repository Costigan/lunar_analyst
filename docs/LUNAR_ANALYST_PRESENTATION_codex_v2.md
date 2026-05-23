# Lunar Analyst Presentation Plan (Codex v2)

## 1. Purpose and Audience
- **Audience:** NASA MSFC experts in AI systems and lunar datasets.
- **Presentation goal:** Show how Lunar Analyst helps mission teams make better lunar south-pole decisions, then explain how the system is built to support that work.
- **Tone:** Mission-focused, practical, and technical without heavy software jargon.

## 2. Core Message
- Lunar Analyst helps scientists and mission planners turn terrain and lighting data into actionable site and operations decisions.
- The strongest way to explain value is through a realistic usage scenario.
- The underlying system design exists to make those analyst workflows reliable, repeatable, and auditable.

## 3. Suggested Slide Count and Flow
- Target: **10 slides**
- Narrative arc:
1. Brief overview of purpose and users
2. Concrete usage scenario (problem -> workflow -> output)
3. Implementation details that enable the scenario
4. Current status, risks, and collaboration asks

## 4. Slide-by-Slide Plan (10 Slides)

### Slide 1: Title and One-Slide Overview
- **Title:** Lunar Analyst for Lunar South Pole Mission Analysis
- **Subtitle:** Users, Workflow, and Technical Readiness
- **Key points:**
  - Lunar Analyst supports early mission design decisions.
  - It combines mapping, terrain/lighting analysis, and guided tooling in one workspace.
- **Visuals:**
  - `IMAGE PLACEHOLDER`: Lunar south-pole context map.

### Slide 2: Who Uses Lunar Analyst
- **Title:** Intended Users and Decision Context
- **Key points:**
  - Lunar scientists evaluating science opportunity and environmental constraints.
  - Mission planners comparing candidate sites, traverses, and timeline windows.
  - Analysis teams preparing evidence for mission reviews.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: User roles mapped to key decision types.

### Slide 3: Usage Scenario Setup
- **Title:** Scenario: Evaluating Candidate Landing Zones
- **Key points:**
  - Team starts with a south-pole scenario and a small set of candidate regions.
  - Goal: identify zones balancing safety, illumination access, and Earth visibility.
  - Inputs: DEM, reference layers, and mission constraints.
- **Visuals:**
  - `IMAGE PLACEHOLDER`: Candidate zones overlaid on south-pole map.

### Slide 4: Usage Scenario Workflow
- **Title:** How the Team Works Through the Scenario
- **Key points:**
  - Load scenario and relevant layers.
  - Generate terrain and lighting outputs.
  - Compare candidate regions with consistent map views and derived layers.
  - Iterate quickly with guided tool runs and map calculations.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Step-by-step workflow (load -> analyze -> compare -> refine).

### Slide 5: Usage Scenario Outcome
- **Title:** What the Team Produces
- **Key points:**
  - Ranked candidate regions with supporting evidence layers.
  - Saved outputs for review and repeat analysis.
  - Clear rationale for why one region is preferred.
- **Visuals:**
  - `IMAGE PLACEHOLDER`: Example output panel/map with selected region and supporting layers.

### Slide 6: Product Capabilities Behind the Scenario
- **Title:** Capabilities Used in the Workflow
- **Key points:**
  - Scenario-based organization of data and outputs.
  - Terrain/lighting analysis jobs with progress and cancellation.
  - Raster math and transforms for custom comparisons.
  - Moon Trek layer discovery and overlay support.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Capability map tied to scenario workflow steps.

### Slide 7: Assistant as a Workflow Accelerator
- **Title:** Assistant Support in Analyst Work
- **Key points:**
  - Helps users run analysis steps from plain-language requests.
  - Uses guarded execution for actions that change project state.
  - Returns structured outputs (tables, images, plots) for quick interpretation.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: User request -> assistant action -> analysis result.

### Slide 8: Implementation View (High Level)
- **Title:** System Design That Enables Reliability
- **Key points:**
  - Central backend manages scenarios, jobs, and shared state.
  - Dedicated compute path handles heavy terrain/lighting calculations.
  - Notebook workflows and interactive workspace use the same backend services.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: High-level architecture with control path and compute path.

### Slide 9: Current Readiness and Open Gaps
- **Title:** What Is Ready and What Needs Hardening
- **Key points:**
  - Core analyst workflow is usable today for scenario-driven analysis.
  - Known gaps are identified and actively managed (event delivery scale, cache persistence, compute isolation refinements, horizon robustness).
  - Near-term focus is reliability hardening and validation depth.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: Readiness and risk table with mitigation status.

### Slide 10: MSFC Collaboration Ask
- **Title:** Collaboration Opportunities with MSFC
- **Key points:**
  - Define acceptance criteria for terrain and illumination outputs.
  - Prioritize benchmark datasets and representative mission scenarios.
  - Evaluate assistant-supported workflows on real analyst tasks.
  - Align on pilot studies and success metrics.
- **Visuals:**
  - `DIAGRAM PLACEHOLDER`: 90-day collaboration timeline.

## 5. Diagram/Image Asset Checklist (for later insertion)
- Lunar south-pole context image.
- User-role decision map.
- Candidate-zone scenario map.
- Workflow diagram for scenario steps.
- Example analysis output screenshot.
- Capability-to-workflow mapping diagram.
- High-level architecture diagram.
- Readiness/risk table and collaboration timeline graphics.

## 6. Presenter Notes Guidance
- Keep explanations tied to analyst tasks and mission decisions first.
- Introduce technical internals only after scenario value is clear.
- Prefer plain language over software-engineering jargon where possible.
