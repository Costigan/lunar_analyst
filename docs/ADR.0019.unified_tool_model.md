# ADR 0019: Unified Tool Model Across UI, Assistant, and Notebooks

- Status: Accepted
- Date: 2026-03-09
- Owners: Architecture (Codex), Product/Implementation (Gemini)
- Related: `AGENTS.md`, `docs/DESIGN.md`, `docs/ADR.0001.process_model.md`, `docs/ADR.0011.ai_assistant_and_mcp.md`, `docs/ADR.0012.python_net_native_bridge.md`, `docs/ADR.0013.notebook_integration_choice.md`, `docs/ADR.0016.map_algebra_dsl.md`, `docs/ADR.0018.scripted_map_algebra.md`

## Context
Lunar Analyst currently uses overlapping terms for adjacent concepts:

- `JobHandlers` in `backend/jobs/handlers.py` currently host most typed tool contracts and implementations.
- The web UI presents a Jobs-oriented workflow.
- Assistant/MCP exposes a separate user-facing tool vocabulary.
- Notebook/script authors primarily use `backend.notebook.notebook_helper`, which exposes some high-level governed operations and some purely local convenience functions.

This split is historically understandable, but it creates unnecessary conceptual duplication:

1. A user-facing capability such as generating PSR, running map algebra, or computing horizons is one thing conceptually, but it is described inconsistently across code and surfaces.
2. There is no single canonical capability catalog shared across UI, assistant/MCP, and notebook/script surfaces.
3. Notebook/script authors do not currently have a clean governed entry point for calling the same canonical capabilities that the UI and assistant use.
4. The term `helper` currently covers both legitimate low-level conveniences and some higher-level operations that are substantial enough to warrant governed tool treatment.

We want one conceptual model with these distinctions:

- **Tool Contract**: the stable, governed typed definition of a capability.
- **Tool**: the callable capability identity exposed across UI, assistant/MCP, and notebook/script surfaces.
- **Job**: one execution instance of a tool.
- **Helper**: a smaller local programming primitive that does not warrant independent governed discovery/confirmation/progress semantics.

We also want to preserve existing capabilities.
No current capability should be lost merely because terminology changes.

## Decision
We will adopt a unified **tool model** across the product.

### 1) Canonical Terms
1. **Tool Contract**
   A tool contract is the stable, governed typed definition for a capability, including request/response schema, visibility, and confirmation metadata.

2. **Tool**
   A tool is the callable capability identity derived from a tool contract and discoverable across surfaces.

3. **Job**
   A job is one invocation/execution of a tool.
   `job_id` and `job_*` event terminology are retained as runtime terms.

4. **Helper**
   A helper is a local convenience API for scripts/notebooks or internal code.
   A helper does not, by itself, imply:
   - UI discoverability
   - assistant/MCP exposure
   - confirmation policy
   - governed artifact registration contract
   - structured progress/cancellation contract

### 2) Tool Catalog is Canonical
There will be one canonical tool catalog for Lunar Analyst.

That catalog will be the source of truth for:
- UI discovery
- assistant/MCP discovery
- notebook/script governed invocation
- documentation

Each tool entry must include metadata sufficient to control exposure by surface rather than creating separate concept models.

The canonical tool catalog must also be the single source of truth for:
- request schema
- result schema
- confirmation policy metadata
- visibility metadata
- stable result-envelope typing

### 3) Visibility Metadata
Each canonical tool will carry visibility metadata:
- `public`
- `advanced`
- `system`
- `draft`

Meaning:
- `public`: normal user-facing tool; suitable for standard UI and assistant discovery
- `advanced`: real tool, but exposed only in advanced/disclosure-driven surfaces
- `system`: internal orchestration or system-facing tool; callable by infrastructure and explicit power-user/programmatic paths, but not part of the default user-facing catalog
- `draft`: contract exists for planning or implementation sequencing, but is not advertised as runnable in normal discovery

This visibility metadata replaces the current implicit split between "methods that exist", "predefined jobs", "assistant tools", and "helper-only capabilities".

### 4) Schema and Metadata Source of Truth
Tool schemas and metadata must not be duplicated across:
- typed handler/result models
- assistant tool registries
- UI discovery metadata
- notebook/script invocation wrappers

Target rule:
- request and result contracts are defined once from typed contract metadata, with Pydantic models as the preferred schema-bearing representation where applicable
- derived JSON schema for assistant/MCP/UI surfaces is generated from that typed contract source rather than maintained manually in parallel maps

This is required to prevent the current conceptual duplication from reappearing as schema duplication in code.

### 5) Existing Capabilities Become Canonical Tools
Existing handler-backed capabilities become entries in the canonical tool model.
They are not removed.

This means:
- current backend methods are treated as tool implementations behind tool contracts
- the UI should refer to capabilities as tools, not "job handlers"
- assistant/MCP should expose the same canonical tool identities where appropriate
- scripts/notebooks should be able to invoke the same canonical tools through a governed SDK/client layer

### 6) Confirmation Policy Lives in Tool Metadata
Confirmation/approval requirements are part of tool definition metadata, not assistant-only hardcoded policy wiring.

This means the canonical catalog should express at least:
- whether confirmation is required
- the governing action/policy type
- whether the requirement is unconditional or conditional

This allows:
- UI surfaces to show lock/approval indicators before launch
- assistant/MCP to enforce the same policy model
- notebook/script invocation layers to surface the same governance expectations

Assistant-specific policy code may still consume this metadata, but it should not be the original source of truth.

### 7) Scripts and Notebooks Can Call Canonical Tools
Canonical tools must be invocable from scripts and notebooks through a dedicated Python package named:

- `backend.analyst_tools`

This package is the governed Python-facing tool invocation surface.

It is intentionally distinct from:
- `backend.tools`, which currently contains CLI/support utilities such as OpenAPI export and MCP server launch
- `backend.notebook.notebook_helper`, which remains the place for local helper functions

### 8) Standardized Result Envelopes
Canonical tools should converge on standardized result-envelope typing.

At minimum:
- tool jobs should have a common job/result envelope for status, progress, and artifact references
- tools that materialize files or products should return standardized artifact/file reference types rather than ad hoc per-tool file payloads

Target examples:
- common file artifact reference for preview/download/open-in-map flows
- common raster artifact reference for map-facing outputs
- common table/image/plot artifact references where appropriate

This is necessary so UI and assistant surfaces can consistently offer actions such as preview, inspect, download, or add-to-map without tool-specific response parsing.

### 9) Helper Classification Rule
A capability should be a tool if it requires any of the following:
- stable request/response schema
- progress/cancellation handling
- governed artifact registration
- confirmation or policy enforcement
- UI discovery
- assistant/MCP discovery
- stable cross-surface naming

A capability should remain a helper if it is primarily:
- local path/data convenience
- small composition primitive
- low-level formatting or utility logic
- ergonomic script/notebook support that does not merit a governed public contract

Some current helpers may need reclassification.
In particular, `register_output_if_available` is a likely candidate to evolve into a system tool or a thin helper over a system tool because it interacts with governed artifact/catalog state rather than being purely local computation.

## Target Naming Scheme

### 1) Product Terminology
- **Tool Contract**: governed typed definition of a capability
- **Tool**: callable capability
- **Job**: one execution of a tool
- **Helper**: local convenience function

### 2) UI Terminology
The UI should use `tool` terminology for capability discovery and launch and `job` terminology for execution state.

Target naming:
- capability catalog/picker: **Tools**
- execution history/status: **Jobs**

This preserves the useful runtime meaning of `job` without calling the capability itself a job.

### 3) Backend Terminology
Target backend naming should converge toward:
- tool contract / tool definition
- tool implementation
- tool catalog
- tool invocation
- job execution

The historical `JobHandlers` name may be retained temporarily as a compatibility implementation detail, but it should not remain the dominant architectural term in new docs or user-facing code.

### 4) Python Package Naming
Governed cross-surface tool APIs should live under:
- `backend/analyst_tools/`

Intended contents:
- tool catalog access
- typed tool invocation client/helpers
- common result envelopes for governed tool calls
- notebook/script-friendly wrappers for invoking the same canonical tools used by UI and assistant/MCP

The existing package:
- `backend/tools/`

continues to mean CLI/support utilities for now and is not repurposed as the canonical tool runtime package.

### 5) Notebook/Script Naming
`backend.notebook.notebook_helper` remains the helper facade.

Target split:
- `backend.analyst_tools`: governed tools
- `backend.notebook.notebook_helper`: local helpers

Examples of likely tools:
- `generate_horizons`
- `generate_psr_raster`
- `generate_average_sun_fraction_raster`
- `raster.calculate`
- `raster.transform`

Examples of likely helpers:
- `safe_scenario_relative_path`
- `write_json`
- `bool_param`
- `directory_file_stats`
- `raster_let`

Some existing high-level notebook helper functions may move or gain tool-backed counterparts over time if they satisfy the tool criteria.

## Architecture Consequences

### 1) One Canonical Capability Catalog
We will no longer treat:
- assistant/MCP tools
- UI predefined jobs
- backend handler method sets

as three separate conceptual inventories.

Instead:
- one canonical tool catalog exists
- multiple surfaces derive filtered views from that catalog

### 2) Visibility Filtering Replaces Inventory Duplication
The same tool may be:
- visible in default UI
- visible only in advanced UI
- visible to assistant/MCP but not default UI
- callable by scripts/notebooks but hidden from general end users

That is a visibility/configuration choice, not a separate capability class.

### 3) Tool Contract Source of Truth
The current architecture rule that typed compute contracts live in `backend/jobs/handlers.py` remains valid during migration.

However, the conceptual interpretation changes:
- those typed contracts are tool contracts
- the runtime execution system still manages jobs

This ADR does **not** require an immediate one-step move of all implementations out of `backend/jobs/handlers.py`.

Longer term, maintainability takes priority over preserving a single large implementation file.
The current `handlers.py` concentration is acceptable only as a transitional state.

### 4) Assistant and UI Should Converge
Assistant/MCP-specific wrappers such as `jobs.run_predefined` are transitional compatibility surfaces.
Longer term, the assistant should consume the canonical tool catalog directly rather than maintaining a partially separate mental model.

### 5) Notebook/Script Governance Improves
Notebook/script authors gain the ability to invoke the same governed tools directly rather than only:
- calling handlers ad hoc
- relying on UI-only launch surfaces
- recreating capability logic in helpers

## Migration Plan

### Phase 0: Glossary and ADR Adoption
Goal:
- Establish the new conceptual model without breaking code.

Changes:
- Adopt this ADR.
- Update docs to define tool contract, tool, job, and helper consistently.
- Stop introducing new "job handler" terminology in user-facing docs unless discussing legacy implementation details.

Acceptance:
- New docs and design notes use the unified vocabulary.

### Phase 1: Canonical Tool Metadata
Goal:
- Introduce one canonical tool catalog with visibility metadata.

Primary files:
- `backend/jobs/handlers.py`
- `backend/api/job_runtime.py`
- `backend/services/assistant/tool_registry.py`
- contract/export tooling

Changes:
- Add explicit per-tool metadata including visibility.
- Add explicit per-tool metadata for confirmation policy and result-envelope typing.
- Classify existing tool-capable methods as `public`, `advanced`, `system`, or `draft`.
- Make UI/assistant discovery consume the same metadata source.
- Start removing manually duplicated schema declarations where generated schemas can be used instead.

Acceptance:
- One backend-derived catalog can explain what exists, why it is or is not visible on a given surface, and how it is governed.

### Phase 2: Introduce `backend.analyst_tools`
Goal:
- Provide a governed Python invocation package for canonical tools.

Primary files:
- `backend/analyst_tools/`
- notebook/script integration points
- tests/docs

Changes:
- Add a small SDK/client layer for listing tools and invoking them from scripts/notebooks.
- Provide tool-friendly envelopes for status/result access.
- Provide standardized artifact result types for file-producing tools.
- Prefer scenario-root-relative usage patterns for script ergonomics.
- Identify helpers that should become system tools or helper-over-tool adapters.

Acceptance:
- A notebook or script can invoke canonical tools without importing handler implementation classes directly.

### Phase 3: UI Terminology Migration
Goal:
- Rename user-facing capability terminology from job-oriented to tool-oriented.

Primary files:
- frontend job/tool launch panels
- relevant REST discovery endpoints if naming changes are needed
- docs/user guide

Changes:
- Rename capability discovery UI from Jobs-centric wording to Tools-centric wording.
- Keep history/status surfaces job-oriented where that remains useful.
- Preserve compatibility in backend route names initially if changing them would be disruptive.

Acceptance:
- Users see capabilities as tools and executions as jobs.

### Phase 4: Assistant/MCP Surface Simplification
Goal:
- Reduce wrapper/alias duplication and align assistant/MCP exposure with the canonical catalog.

Changes:
- Review `jobs.run_predefined`, `job.launch`, and related wrappers.
- Keep compatibility aliases where necessary, but make the canonical tool identity primary.
- Ensure confirmation, visibility, and result-shape metadata are derived from the unified tool model.

Acceptance:
- Assistant/MCP discovery reflects the canonical tool inventory rather than a partially separate registry concept.

### Phase 5: Internal Renaming Cleanup
Goal:
- Remove `JobHandlers` / `job handler` as dominant internal architectural terminology when safe.

Possible changes:
- rename `JobHandlers` to `ToolImplementations` or equivalent
- split implementations by domain where useful, for example:
  - `backend/analyst_tools/terrain.py`
  - `backend/analyst_tools/lighting.py`
  - `backend/analyst_tools/system.py`
  while preserving one generated canonical tool catalog
- update generator/export/discovery naming accordingly

Constraints:
- this is not required for the conceptual migration to succeed
- it should happen only after the canonical tool model is established and compatibility risk is understood

Acceptance:
- Internal naming matches the adopted architecture without forcing a disruptive big-bang refactor.

## Detailed Implementation Plan

This section defines the recommended execution sequence.
It is intentionally concrete so the migration can be performed as a set of small, reviewable changes rather than one large refactor.

### Work Package 1: Add Canonical Tool Metadata Model
Goal:
- Create one typed metadata model for canonical tool contracts and tools.

Primary files:
- `backend/contracts/`
- `backend/api/job_runtime.py`
- `backend/jobs/handlers.py`

Changes:
- Introduce typed metadata for:
  - canonical tool name
  - canonical tool contract identity
  - visibility (`public | advanced | system | draft`)
  - confirmation metadata
  - request model
  - result model
  - result-envelope/artifact typing
- Extend the contract/decorator path so the metadata is attached at tool-definition time rather than reconstructed separately later.
- Preserve compatibility with current handler discovery while enriching the discovered contract object.

Acceptance:
- Every governed tool-capable handler can be described from one typed metadata object.
 - Every governed tool-capable implementation can be described from one typed metadata object.

### Work Package 2: Remove Manual Schema Duplication
Goal:
- Make assistant/MCP/UI schemas derive from canonical typed contracts.

Primary files:
- `backend/services/assistant/tool_registry.py`
- schema export tooling
- contract tests

Changes:
- Replace or shrink manual schema maps where possible.
- Generate assistant/MCP parameter schemas from the typed request model source.
- Generate result metadata from the typed result model source.
- Keep compatibility wrappers only where a surface-specific alias is required.

Acceptance:
- Request/result schema drift between handler contracts and assistant/MCP exposure is no longer possible without failing generation/tests.

### Work Package 3: Introduce Canonical Tool Catalog Service
Goal:
- Provide one backend service/function that returns the canonical filtered tool catalog.

Primary files:
- `backend/api/job_runtime.py`
- `backend/api/dependencies.py`
- optionally `backend/services/`

Changes:
- Add one discovery path that returns canonical tool entries.
- Include visibility, confirmation metadata, request/result schema references, and draft/system classification.
- Make existing UI discovery and assistant discovery consume this path or a shared in-process implementation.

Acceptance:
- UI and assistant/MCP no longer maintain separate capability inventories.

### Work Package 3a: Enforce Tool Contract/Tool/Job Vocabulary in APIs
Goal:
- Align public API naming with `tool contract`, `tool`, and `job` semantics.

Primary files:
- `backend/contracts/models.py`
- `backend/api/routers/v1.py`
- `backend/api/dependencies.py`
- API docs/OpenAPI exports

Changes:
- Keep `job_id`, `job_*` runtime fields for execution payloads.
- Prefer `tool_name`/`tool` and `job` in catalog and invocation endpoints.
- Deprecate ambiguous `run`-first naming where equivalent `job` naming exists.
- Document compatibility aliases and planned removals.

Acceptance:
- API docs consistently distinguish tool contracts, tools, and jobs.

### Work Package 4: Standardize Confirmation Metadata
Goal:
- Move confirmation semantics into tool metadata.

Primary files:
- tool metadata definitions
- assistant policy integration
- UI discovery payloads

Changes:
- Replace assistant-only hardcoded confirmation classification as primary source of truth.
- Support:
  - unconditional confirmation
  - conditional confirmation
  - action type / policy type
- Surface this metadata to UI so lock/approval indicators can render before launch.

Acceptance:
- Confirmation behavior is defined once in canonical tool metadata and consumed consistently by assistant and UI.

### Work Package 5: Standardize Result Envelopes and Artifact References
Goal:
- Make file-producing tools return consistent artifact references.

Primary files:
- `backend/contracts/`
- `backend/jobs/handlers.py`
- artifact registration helpers
- UI/assistant renderers as needed

Changes:
- Define common result envelope types for:
  - run status/result
  - file artifact reference
  - raster artifact reference
  - plot/image/table artifact reference where appropriate
- Update high-value tools first:
  - `generate_horizons`
  - `generate_psr_raster`
  - `generate_average_sun_fraction_raster`
  - `raster.calculate`
  - `raster.transform`
- Preserve old fields during transition if current clients depend on them.

Acceptance:
- UI and assistant can offer preview/download/add-to-map flows using common result parsing.

### Work Package 6: Introduce `backend.analyst_tools`
Goal:
- Provide the governed Python-facing tool invocation surface.

Primary files:
- `backend/analyst_tools/__init__.py`
- `backend/analyst_tools/catalog.py`
- `backend/analyst_tools/client.py`
- `backend/analyst_tools/types.py`

Changes:
- Add Python APIs for:
  - listing canonical tools
  - retrieving tool metadata
  - invoking a tool
  - polling a run / reading result envelopes
- Make the package usable from:
  - headless notebook jobs
  - standalone scenario scripts
  - future notebook integrations

Acceptance:
- Scripts/notebooks can call governed tools without importing `JobHandlers` directly.

### Work Package 7: Reclassify and Trim Helpers
Goal:
- Leave only true helpers in `backend.notebook.notebook_helper`.

Primary files:
- `backend/notebook/notebook_helper.py`
- `backend/notebook/runtime.py`
- notebook examples/docs

Changes:
- Audit existing helper exports against the tool/helper litmus test.
- Keep purely local helpers as helpers.
- Promote governed state-mutating helpers to system tools or helper-over-tool adapters.
- In particular, evaluate:
  - `register_output_if_available`
  - high-level lightmap orchestration helpers

Acceptance:
- `notebook_helper` is a local convenience layer, not a second hidden tool catalog.

### Work Package 8: UI Terminology and Discovery Migration
Goal:
- Make the UI present capabilities as tools and executions as jobs.

Primary files:
- frontend tool/job launch panels
- UI strings
- user docs

Changes:
- Rename capability browsing and launch surfaces from Jobs-oriented wording to Tools-oriented wording.
- Retain execution-history wording as Jobs.
- Add advanced/system filtering behavior driven by visibility metadata.
- Remove user-facing strings containing "job handler".
- Ensure panel labels and help text use `tool` for capability and `job` for execution.

Acceptance:
- The UI vocabulary matches the canonical model without changing runtime concepts unnecessarily.

### Work Package 9: Assistant/MCP Compatibility Layer Cleanup
Goal:
- Reduce wrapper duplication while preserving compatibility.

Primary files:
- `backend/services/assistant/tool_registry.py`
- assistant tests
- MCP server integration

Changes:
- Keep existing compatibility names short term where needed.
- Make canonical tool identity primary in discovery metadata.
- Decide which wrappers remain:
  - permanent compatibility alias
  - deprecated alias
  - removable alias

Acceptance:
- Assistant/MCP uses the canonical catalog model with minimal duplication.

### Work Package 10: Domain-Based Internal Split
Goal:
- Prevent a long-term monolithic tool implementation file.

Primary files:
- `backend/analyst_tools/terrain.py`
- `backend/analyst_tools/lighting.py`
- `backend/analyst_tools/system.py`
- transitional compatibility layer in `backend/jobs/handlers.py`

Changes:
- Move implementations by domain while preserving one discovery/catalog source.
- Keep a compatibility aggregation layer if `backend/jobs/handlers.py` must continue to exist temporarily.
- Do not break existing run dispatch until compatibility coverage is verified.

Acceptance:
- Tool contracts and implementations are maintainable by domain and no longer concentrated in one oversized module.

### Work Package 11: Internal Terminology Retirement (`JobHandlers` / `job handler`)
Goal:
- Retire `JobHandlers` and `job handler` terms in code symbols, comments, and docs.

Primary files:
- `backend/jobs/handlers.py`
- `backend/api/job_runtime.py`
- `backend/api/dependencies.py`
- `backend/services/assistant/tool_registry.py`
- tests and docs

Changes:
- Rename symbols such as `JobHandlers`, `handler_name`, and `discover_job_handlers` to tool-implementation-oriented names where safe.
- Keep backward-compatible adapters only where required to avoid breaking external callers.
- Update tests and docs to use `tool contract`, `tool`, and `job`.

Acceptance:
- New code no longer introduces `JobHandlers` / `job handler` terminology.
- Existing internal terms are either migrated or explicitly marked compatibility-only.

## Recommended Delivery Order
1. Work Package 1
2. Work Package 2
3. Work Package 3
4. Work Package 4
5. Work Package 5
6. Work Package 6
7. Work Package 7
8. Work Package 8
9. Work Package 9
10. Work Package 10
11. Work Package 11

This ordering is deliberate:
- metadata and schema source-of-truth must come before catalog unification
- catalog unification should come before SDK/client introduction
- domain-based internal splitting should happen late, after external contracts stabilize
- terminology retirement should complete after compatibility boundaries are explicit

## Suggested Vertical Slices
To keep the migration reviewable, implement it as the following vertical slices:

1. Add canonical metadata to one simple existing tool such as `ping`.
2. Drive assistant schema generation for that tool from typed metadata.
3. Extend the same path to one file-producing tool such as `generate_psr_raster`.
4. Introduce standardized artifact result typing on that file-producing path.
5. Add `backend.analyst_tools` read-only catalog access.
6. Add `backend.analyst_tools` invocation for one public tool.
7. Migrate one UI discovery surface to the canonical catalog.
8. Migrate one notebook/script flow to `backend.analyst_tools`.
9. Expand classification to advanced/system tools.
10. Perform domain splits only after the prior slices are stable.
11. Retire internal `JobHandlers`/`job handler` terms once compatibility adapters are in place.

## Verification Plan
Each work package should include tests appropriate to the affected layer.

Required verification categories:
- contract schema generation tests
- assistant/MCP tool catalog tests
- UI discovery payload tests
- tool invocation compatibility tests
- standardized result-envelope tests
- notebook/script invocation tests
- backward-compatibility tests for retained aliases

Minimum regression checkpoints:
- existing assistant tool names still resolve where compatibility is promised
- existing job execution paths still function
- file-producing tools return standardized artifact references plus any required legacy fields during transition
- notebook helper flows continue working until explicitly migrated

## Compatibility and Non-Goals

### Compatibility
During migration:
- existing routes and wire payloads may continue to use `job_id` and related execution terms
- compatibility aliases may remain for assistant/MCP tools
- `backend/jobs/handlers.py` may remain in place temporarily

### Non-Goals
This ADR does not require, in one step:
- deleting the current job runtime model
- removing `job_id` or `job_*` event terminology
- forcing all helpers to become tools
- exposing `system` or `draft` tools in the normal UI
- immediately renaming every file/path from `jobs` to `tools`

## Risks
1. **Terminology churn without true convergence**
   If names change in docs/UI but inventories remain separate, confusion will increase rather than decrease.

2. **Over-promoting helpers into tools**
   If every convenience API becomes a tool, discoverability and governance surfaces become noisy.

3. **Breaking notebook/script workflows**
   If `backend.analyst_tools` is introduced without preserving current helper ergonomics, local authoring quality will regress.

4. **Premature internal renaming**
   A big-bang rename from `JobHandlers` to new internal names before metadata/catalog unification would create high churn with little architectural value.

5. **Schema drift**
   If typed models and assistant/UI schemas continue to be maintained separately, the unified tool model will fail at the code-contract layer.

## Follow-On Guidance
When evaluating a capability:

Make it a **tool** if it needs:
- stable contract
- governed invocation
- shared cross-surface identity
- progress/cancellation
- artifact registration
- confirmation or policy controls

Keep it a **helper** if it is mainly:
- local utility
- composition aid
- path/data convenience
- script ergonomics without independent governance needs

This ADR supersedes the user-facing use of "job handler" as the primary capability term.
Going forward, the canonical vocabulary is:
- **tool contract** for governed capability definition
- **tool** for callable capability
- **job** for one execution of a tool
