# ADR 0015: Rich Assistant Outputs via Typed Artifact Contracts

- Status: Accepted
- Date: 2026-03-06
- Owners: Architecture (Codex), Implementation (Gemini)
- Related: `docs/ADR.0011.ai_assistant_and_mcp.md`, `docs/ADR.0013.notebook_integration_choice.md`, `docs/ADR.0006.raster_delivery_crs_policy.md`, `backend/contracts/assistant_models.py`, `backend/contracts/assistant_events.py`, `backend/services/assistant/tool_registry.py`, `backend/web/lunar_analyst/src/components/assistant/AssistantResponsePane.tsx`

## Context
The Lunar Analyst assistant currently provides text-only responses. Assistant messages persist a single `content` string, the React response pane renders that string directly, and WebSocket streaming carries only `text_delta` fragments during a turn.

That is sufficient for conversational responses, but it is a poor fit for analysis workflows where the user needs to inspect actual results such as:

- a raster preview or histogram
- a sample of a CSV table
- a rendered plot
- a compact artifact card pointing at a generated file

Lunar Analyst already has strong primitives for structured tool execution and file-safe asset delivery:

- The assistant and MCP integrations are built on a unified tool registry and typed tool results.
- FastAPI is the authoritative control plane.
- File assets are served by `file_id`, not raw arbitrary paths.
- The assistant event stream is intentionally lightweight and currently text-oriented.

The earlier version of this ADR proposed adopting Marimo frontend components as the primary architectural mechanism. That overreached in two ways:

1. It assumed a reusable Marimo React renderer boundary that is not currently present in the main React app.
2. It understated the contract and persistence impact across assistant REST responses, WebSocket events, and on-disk session history.

The actual architectural decision needed here is not "use Marimo components". It is "how rich assistant outputs are typed, persisted, transferred, and rendered safely".

## Decision
We will introduce rich assistant outputs as typed artifact manifests attached to tool results and assistant messages.

The core rules are:

1. Tools are the primary producers of rich outputs.
2. Assistant messages may include a prose summary plus zero or more typed outputs.
3. Large binary payloads are transferred by `file_id` references through existing FastAPI file serving, not inline chat text or WebSocket payloads.
4. WebSocket streaming remains lightweight. Rich outputs are announced by metadata and then fetched through normal assistant/message APIs or file endpoints.
5. Frontend rendering is driven by Lunar Analyst-owned MIME/output contracts. Reuse of Marimo renderers is optional implementation detail, not the architectural center of the design.

## Contract

### 1. Assistant Output Model
Add a new typed output model to assistant contracts and persist it on assistant messages and tool calls.

Illustrative shape:

```python
class AssistantOutput(StrictModel):
    output_id: str
    kind: Literal["image", "table", "plot", "artifact_card", "map_view"]
    mime_type: str
    storage: Literal["inline", "file"]
    title: str | None = None
    caption: str | None = None
    file_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

`AssistantMessage` gains:

```python
outputs: list[AssistantOutput] = Field(default_factory=list)
```

`AssistantToolCall` gains:

```python
outputs: list[AssistantOutput] = Field(default_factory=list)
```

`data` is structured JSON, not arbitrary HTML. The contract is typed by `kind`, `mime_type`, and `storage`.

### 2. Supported Output Kinds in v1
Initial rich outputs are read-only and bounded:

- `image`
  - MIME types: `image/png`, `image/jpeg`, `image/svg+xml`
  - Typical use: raster thumbnails, histograms, rendered plots
- `table`
  - MIME type: `application/vnd.lunar-analyst.table+json`
  - Typical use: sampled CSV/table preview
- `plot`
  - MIME types:
    - `application/vnd.vegalite.v5+json`
    - `application/vnd.plotly.v1+json`
    - fallback image MIME types above
  - Typical use: declarative plot spec or rendered image fallback
- `artifact_card`
  - MIME type: `application/vnd.lunar-analyst.artifact-card+json`
  - Typical use: compact metadata card for a generated file/product

`map_view` is reserved for a later phase and is not part of v1.

### 3. Storage and Transfer Rules
Rich outputs use one of two transport modes:

- `storage="inline"`
  - Only for small structured payloads
  - Typical examples: table sample JSON, small plot specs
- `storage="file"`
  - For images and larger renderables
  - Payload references a backend-served `file_id`

Normative rules:

- Do not inline large binary payloads in assistant messages.
- Do not stream binary content over assistant WebSocket events.
- Prefer `file_id` references for PNG, JPEG, SVG, or any payload above a conservative size threshold.
- Continue to enforce file-id-based serving and scenario-root safety constraints.

### 4. Representation by Output Type

#### Images
Images are represented as:

```json
{
  "kind": "image",
  "mime_type": "image/png",
  "storage": "file",
  "file_id": "file_123",
  "metadata": {
    "width": 512,
    "height": 512,
    "alt": "Slope histogram preview"
  }
}
```

Small images may be inline only when bounded and encoded as structured data in `data`, for example a base64 field. File-backed transfer is preferred.

#### Tables
Tables are represented as structured JSON, not CSV text:

```json
{
  "kind": "table",
  "mime_type": "application/vnd.lunar-analyst.table+json",
  "storage": "inline",
  "data": {
    "columns": [
      {"key": "crater_id", "label": "Crater", "dtype": "string"},
      {"key": "slope_deg", "label": "Slope (deg)", "dtype": "number"}
    ],
    "rows": [
      {"crater_id": "A1", "slope_deg": 12.4},
      {"crater_id": "A2", "slope_deg": 8.9}
    ],
    "row_count": 2500,
    "truncated": true,
    "source_file_id": "file_table_csv"
  }
}
```

Supported column dtypes in v1:

- `string`
- `integer`
- `number`
- `boolean`
- `datetime`

#### Plots
Plots may be represented either as declarative specs or rendered image fallbacks:

```json
{
  "kind": "plot",
  "mime_type": "application/vnd.vegalite.v5+json",
  "storage": "inline",
  "data": {
    "spec": {}
  }
}
```

or

```json
{
  "kind": "plot",
  "mime_type": "image/png",
  "storage": "file",
  "file_id": "file_plot_png"
}
```

Rendered image fallback is required for robustness when the frontend does not support a given declarative plot type.

## Tool Interface and Producer Model
Rich outputs are primarily produced through the tool interface.

Tool results should follow this shape:

```json
{
  "summary_text": "Preview generated for slope_stats.csv.",
  "artifacts": [
    {
      "output_id": "out_1",
      "kind": "table",
      "mime_type": "application/vnd.lunar-analyst.table+json",
      "storage": "inline",
      "data": {}
    },
    {
      "output_id": "out_2",
      "kind": "image",
      "mime_type": "image/png",
      "storage": "file",
      "file_id": "file_preview_png"
    }
  ]
}
```

Normative rules:

- Tools generate outputs or references to already-generated artifacts.
- The LLM does not author raw render payloads directly.
- The assistant may summarize tool results in prose, but renderable outputs come from backend-owned tool execution.
- MCP tool responses and built-in assistant tool responses use the same output contract.

This keeps the tool registry as the authoritative integration layer established by ADR 0011.

## Agent Guidance and Tool-Use Patterns
Rich outputs are only useful if assistant providers and external MCP agents understand how to obtain them.

This ADR therefore requires explicit agent guidance updates in addition to contract changes.

### 1. Guidance Source of Truth
Agent guidance must reflect the live tool surface exposed by the backend, not a hardcoded static assumption set.

Normative rules:

- Built-in assistant providers must be prompted against the current tool catalog exposed by the backend.
- External MCP agents must rely on MCP `tools/list` results and tool schemas as the primary source of truth.
- Feature-gated tools enabled or disabled by `.toml` configuration must appear or disappear from the live tool catalog accordingly.
- Agent instructions must not assume a tool is available if it is absent from the tool catalog.

### 2. Guidance Content
Assistant system prompts and tool guidance should teach the following patterns:

- When the user asks to inspect an existing artifact, prefer `artifact.describe_*` tools that can return typed outputs.
- When the user asks for a derived product, prefer an existing typed job/tool first.
- If no typed job exists and script authoring is enabled, the agent may use `scenario.write_script` plus `scenario.run_script` to create and execute a scenario-scoped Python script.
- After a script or notebook run produces artifacts, the agent should follow with artifact inspection tools so the response can include typed outputs rather than prose alone.
- Mutating tools remain confirmation-gated according to existing assistant policy.

### 3. Script Authoring and Execution Pattern
The write-and-run script workflow is a valid rich-output producer path and must be documented for agents.

Expected pattern:

1. Discover context with read-only tools such as scenario, product, and file listing tools.
2. If needed, write a scenario-scoped Python script with `scenario.write_script`.
3. Execute it with `scenario.run_script`.
4. Inspect resulting products/files with `artifact.describe_geotiff`, `artifact.describe_table`, `artifact.describe_plot`, or related artifact tools.
5. Return a prose summary plus typed outputs derived from those artifact inspections.

Normative rules:

- Script-created outputs must still flow back into the assistant through typed artifact descriptors, not ad hoc textual descriptions alone.
- The script path must remain scenario-scoped and subject to existing confirmation and overwrite policies.
- This workflow is optional and must only be used when the relevant write/run tools are available in the current tool catalog.

## Frontend Rendering Model
The assistant response pane will render:

- assistant prose content
- attached outputs from `AssistantMessage.outputs`
- optionally tool-call-local outputs when useful for transparency/debugging

Rendering is dispatched by `kind` and `mime_type` using Lunar Analyst-owned components.

Expected v1 renderers:

- `ImageOutput`
- `TableOutput`
- `PlotOutput`
- `ArtifactCardOutput`

Reusing Marimo rendering code is allowed only when it cleanly fits the main React app and does not weaken the contract. The frontend contract must not depend on Marimo-specific runtime state, notebook process state, or arbitrary notebook MIME bundles.

## Eventing and Persistence

### 1. Persistence
Because assistant sessions are persisted on disk, rich outputs require explicit persistence updates:

- message storage schema
- tool call storage schema
- legacy JSON import/export compatibility
- exported contract schemas

This must be treated as a real contract change, not a UI-only enhancement.

### 2. WebSocket Events
Assistant WebSocket events remain lightweight.

v1 rules:

- Keep text token/delta streaming for prose.
- Do not stream large output payloads over WebSocket.
- When rich outputs become available, emit metadata only.
- The frontend then refreshes messages or fetches referenced files as needed.

Two acceptable approaches:

1. Reuse existing `assistant_turn_completed` and message refresh flow.
2. Add a small explicit event such as `assistant_outputs_ready` carrying `message_id`, `turn_id`, and output metadata.

If a new event is added, the assistant event schema version must be updated deliberately.

## Security and Safety
Rich outputs must preserve existing filesystem and serving safety guarantees.

Normative rules:

- Prefer typed JSON contracts over raw `text/html`.
- Do not allow arbitrary unsanitized HTML as a primary rendering surface.
- All file-backed assets must be served through validated `file_id` mappings.
- Output generation must not bypass scenario-root and file serving policies.
- Output payloads must be size-bounded and truncation-aware.

## CRS and Map Considerations
Map-like outputs are more constrained than generic images or tables.

For v1:

- raster previews may be static images
- map snippets are out of scope

If a future phase adds `map_view`, it must define:

- a dedicated `application/vnd.lunar-analyst.map+json` schema
- exact CRS declaration requirements
- whether raster sources require map-display derivatives in `ESRI:103878`
- how layer sources are referenced by `file_id` or product identity

This future work must respect ADR 0006 raster delivery policy and the main React map architecture.

## Phased Rollout

### Phase 1: Contract and Persistence
- Add `AssistantOutput` to backend and frontend contracts.
- Persist `outputs` on assistant messages and tool calls.
- Update assistant schema exports and tests.
- Keep frontend rendering simple at first.

### Phase 2: Tool-Produced v1 Outputs
- Extend `artifact.describe_geotiff` to return:
  - summary text
  - image preview or histogram artifact
  - artifact card metadata
- Extend `artifact.describe_table` to return:
  - bounded table sample JSON
  - artifact card metadata
- Extend `artifact.describe_plot` to return:
  - file-backed image or supported plot spec
  - artifact card metadata
- Update assistant provider guidance and MCP-facing tool documentation so agents understand:
  - renderable outputs come from tool results
  - artifact describe tools are the preferred inspection path
  - script write/run plus artifact inspection is the fallback pattern when enabled

### Phase 3: Frontend Renderers
- Refactor `AssistantResponsePane.tsx` to render outputs alongside text.
- Add bounded renderers for image, table, plot, and artifact card outputs.
- Preserve a robust fallback for unsupported MIME types.

### Phase 4: Optional Renderer Reuse
- Evaluate targeted reuse of Marimo or other renderer code only where it reduces duplication without changing the contract model.

## Consequences
Positive:

- Assistant turns can show actual artifacts instead of only prose summaries.
- The design aligns with the existing tool registry and MCP architecture.
- Large payload transfer stays compatible with file-id-based serving and path safety.
- The rendering contract becomes explicit and testable.

Tradeoffs:

- Assistant persistence and API contracts become more complex.
- Contract tests, schema exports, and frontend/client types all need updates.
- Some output kinds will require backend preview generation work.
- Declarative plot rendering may need image fallback paths for compatibility.

## Out of Scope
- Full Marimo cell execution inside the assistant
- Persistence of interactive widget state across sessions
- Arbitrary notebook MIME bundle replay
- Arbitrary raw HTML rendering
- Editable outputs
- General-purpose embedded map snippets in v1
