# Prompt Handling and Execution Dispatch in Lunar Analyst

This document describes the lifecycle of a user prompt within the Lunar Analyst assistant, detailing how raw text is segmented, classified, planned, and ultimately dispatched to either deterministic execution paths or LLM-driven reasoning loops.

## 1. Overview of the Hybrid Architecture

Lunar Analyst employs a **Hybrid Command Routing Policy** (ADR 0022) to handle user intents. Instead of relying solely on an LLM to interpret and execute every request, the system uses a neuro-symbolic approach:
- **Narrow, imperative intents** (e.g., "show layer X", "calculate slope", "zoom to Shackleton") are identified and executed deterministically.
- **Open-ended or complex requests** are handed off to an LLM in a bounded "model loop" for reasoning and tool-calling.

This guarantees reliability, speed, and exactness for known analytical and UI operations while preserving the flexibility of an AI assistant for complex tasks.

## 2. The Intent-Unit Reliability Pipeline

Prompt ingestion follows a fixed, sequential pipeline (established in ADRs 0026-0034) before any tool or model is invoked:

1. **Segmentation (`PromptSegmenter`)**:
   - The raw prompt is split into manageable units (segments) using a spaCy NLP model for sentence boundaries, followed by deterministic clause-splitting heuristics.
   - Segments are flagged for complexity (e.g., presence of "if" or "unless" guards) which can disqualify them from deterministic routing.

2. **Classification (`PromptClassifier`)**:
   - Each segment is classified into a specific category based on its semantic intent or syntactical structure.
   - Classification uses rule-first matching (regex, heuristics) against the action router, with fallback to bounded LLM classification if configured.

3. **Entity Reference Resolution (`EntityReferenceResolver`)**:
   - Before planning, ambiguous entity references within the segments (e.g., "that layer", "Shackleton Crater") are resolved to concrete database IDs or file paths (ADRs 0035, 0051).
   - Uses exact-match first, bounded fuzzy fallback, and same-turn pronoun binding.

4. **Execution Plan Construction (`TurnExecutionPlan`)**:
   - A versioned execution plan is materialized from the classifications and resolved entities.
   - The planner assigns an **Execution Mode** to each segment, determining exactly which code path will handle it.

5. **Execution and Dispatch**:
   - **Partial Deterministic Execution**: Segments marked for deterministic execution are run first.
   - Unmatched or complex segments are then handed off to the LLM (`model_loop`) with updated context reflecting the results of the deterministic steps.

6. **State Merge and Success Semantics**:
   - Per-segment runtime states are tracked and merged.
   - Overall turn status (`success`, `partial_success`, `failed`) is computed based on mutation evidence and postcondition validation.

## 3. Segment Classification

During the Classification phase, segments are assigned a class that dictates how they will be planned:

- `command`: The segment explicitly matches a known router pattern (e.g., defined in `config/assistant_action_router.yaml`) for UI or state manipulation.
- `create_product`: The segment requests the generation of a known analytical product (e.g., slope, hillshade, viewshed).
- `intent_family`: The segment matches a high-level semantic operation (e.g., `search`, `goto`, `identify`) that has a dedicated parameter-mapping logic.
- `unknown` / `unclassified`: The segment cannot be confidently mapped to a deterministic path.

## 4. Execution Modes (Dispatch Paths)

The execution plan assigns a specific `mode` to each segment based on its classification and the success of the planning phase. These modes represent the actual execution paths.

### 4.1 `deterministic_command`
- **Trigger**: Segment classified as `command`.
- **Handling**: Bypasses the LLM entirely. The `HybridCommandRouter` maps the segment directly to specific tool calls (or bounded sub-agent steps) based on hardcoded YAML specifications.
- **Example**: "Turn off the slope layer." -> Maps directly to `layer.set_visibility(visible=False)`.

### 4.2 `deterministic_create_product`
- **Trigger**: Segment classified as `create_product`.
- **Handling**: Processed by the `CreateProductPlanner`. The planner builds a reliable recipe (sequence of tools) to generate the requested dataset.
- **Reuse**: The planner checks if the requested product already exists in the scenario. If so, it short-circuits execution and reuses the existing product.
- **Example**: "Generate a hillshade for the primary DEM." -> Maps to `raster.transform` or `raster.calculate` with predefined parameters.

### 4.3 `deterministic_intent_family`
- **Trigger**: Segment classified as `intent_family`.
- **Handling**: Processed by the `IntentToToolPlanner`. The planner maps the resolved entities and intent properties to a specific deterministic tool sequence.
- **Example**: "Find Shackleton Crater." -> Maps to nomenclature search and `map.set_extent` tools based on the resolved feature coordinates.

### 4.4 `model_loop` (LLM Fallback)
- **Trigger**: 
  - Segment was unclassified or deemed too complex (e.g., conditional logic).
  - A deterministic planner failed (e.g., `CreateProductPlanner` blocked due to missing input files).
  - Explicit non-deterministic segment class requiring reasoning.
- **Handling**: The segment (along with context from prior deterministic steps) is sent to the LLM provider. The LLM enters an iterative ReAct-style loop, deciding which tools to call, observing the results, and formulating a final response.
- **Example**: "What is the average slope in this area?" -> Handed to the LLM to query raster stats and synthesize a natural language answer.

#### 4.4.1 Model Loop Prompt Construction
To control token growth while providing maximum relevance, the prompt for the model loop is constructed dynamically:

1. **System Instructions**: Base persona, available tool schemas, and procedural guidance.
2. **Active Identity**: Explicit `scenario_id` and `scenario_directory` for the active scenario.
3. **Segment Handoff**: The raw text of the segment being processed, plus any "unresolved" remainders from the turn.
4. **Deterministic Side-Effects**: A summary of any deterministic segments already executed in the current turn (e.g., "Successfully switched to scenario X").
5. **Resolved Entity References**: Compact, high-confidence identifiers (Feature IDs, File IDs, Layer IDs) resolved by the `EntityReferenceResolver`. This prevents the model from having to "guess" names it already identified earlier in the pipeline.
6. **Compacted Inventory**: Aggressive compaction of large lists (e.g., `product.list`, `layer.list_visible`) to provide a summary of available data without exhausting the context window.
7. **RAG Context**: Relevant document chunks (procedural or domain knowledge) retrieved from the global RAG index based on the segment text.
8. **Artifact References**: Instead of full data payloads, the prompt includes `file_id`, generated relative paths, and key statistics for any relevant artifacts.
9. **Procedural Guidance Triggers**: Deterministic snippets of guidance (few-shot examples) triggered by the segment's intent classification.
10. **Session Summary**: A compact distillation of prior turns in the session to maintain long-term coherence.

## 5. Bounded Sub-Agent Steps

While deterministic command routing usually triggers single tools, it can also dispatch to **Bounded Agent Substeps** (ADR 0023).
- If enabled (`deterministic_agent_substeps_enabled`), a deterministic plan can include an `agent_call`.
- These calls are constrained to read-only tools and operate under strict token/iteration limits to perform scoped data gathering before returning control to the deterministic flow.

## 6. Observability and Telemetry

The execution mode and status of every segment are preserved in the assistant's turn metadata.
- **Execution Origin**: Traces whether an action was `deterministic` or `model_reasoned`.
- **Latency**: Metrics are emitted for each stage (`latency_segmentation_ms`, `latency_classification_ms`, `latency_execution_plan_ms`).
- **Telemetry Codes**: Machine-readable codes record the exact reason a planner succeeded, failed, or fell back to the model loop, aiding in continuous evaluation and regression testing.
