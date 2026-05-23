# Consolidated System Design: Lunar Analyst Suite

## 1. Context & Objectives
The Lunar Analyst project is a project-based toolkit for lunar south pole mission analysis. It is web-first and notebook-first, with FastAPI as the control plane and native `.NET` compute services for heavy terrain/lighting workloads. The goal is to support the full mission lifecycle, from global site exploration (km-scale) to landing hazard analysis (cm-scale) and lighting validation.

### Platform Support Posture
- **Current Development Baseline**: Host-native Linux is the only supported day-to-day development environment for the web/backend/notebook/native stack.
- **Container Parity Targets**: Ubuntu-based dev/runtime containers are supported for parity validation and deployment packaging.
- **Windows Posture**: Windows is no longer supported for Lunar Analyst development and runtime execution.
- **Porting Constraint**: Linux/container work remains responsible for preserving validated native-compute behavior and explicit acceptance criteria for `moonlib`.

### ADR 0041 Implementation Status
- **Current State**: `docs/ADR.0041.parallel_popos_and_ubuntu_container_development.md` has been implemented through the C4 boundary as the current stopping point for Linux/container work.
- **Completed Repo Surface**: Host-native Pop!_OS development support, shared base image, dev container workflow, runtime image workflow, and first-slice NRP manifest package are in place.
- **Deferred Follow-On Work**: Remaining steps after C4 are intentionally paused until the application is more mature and the operational deployment shape is stable enough to justify additional validation and hardening.

## 2. System Architecture Overview
The active system is composed of runtime components sharing a common filesystem-based scenario structure:

1.  **Web Client** (`backend/web/lunar_analyst/`): React + OpenLayers browser UI (Tauri-hostable).
2.  **FastAPI Control Plane** (`backend/api/`, `backend/jobs/`): authoritative API/contracts/job orchestration.
3.  **Notebook/Script Runtime** (`backend/notebook/`): headless scenario script and notebook execution with cancellation/progress/log streaming.
4.  **Native Compute Engine** (`native/new_horizon/`): `moonlib` C#/.NET terrain and lighting compute, plus native runner/test projects used for validation.
5.  **Assistant + MCP Gateway** (`backend/services/assistant/`, `backend/mcp/`): tool-driven assistant execution and MCP exposure.

Legacy desktop viewer code exists externally and is not part of the active Lunar Analyst runtime in this repository.

### Repository Structure Note
- **Authoritative MoonLayers Source**: Active MoonLayers development is inside this repo at `moonlayers_pkg/`.
- **External Mirror Policy**: Any external mirrors are historical only. Day-to-day edits must be made in the in-repo `moonlayers_pkg/` source tree.

### Native Third-Party Runtime Layout
- **CSPICE Runtime Payloads**: Platform-specific CSPICE shared libraries are stored under `native/third_party/cspice/`.
- **Linux Location**: `native/third_party/cspice/linux-x64/libcspice.so`
- **Bootstrap Contract**: Native bootstrap and .NET interop should resolve CSPICE from the Linux third-party runtime root rather than from ad hoc build-output copies.

### Native Dependency Setup: CSPICE
The `moonlib` native compute path depends on NAIF CSPICE. The upstream NAIF Linux build scripts produce static archives (`cspice.a`, `csupport.a`) but do not produce a shared object automatically, so Linux setup requires an extra shared-link step.

#### Linux: Download, Build, and Install `libcspice.so`
1. Create a temporary build folder and unpack the NAIF CSPICE toolkit:

```bash
mkdir -p /tmp/cspice-build
cd /tmp/cspice-build
# Place the NAIF CSPICE toolkit archive here, then unpack it.
uncompress cspice.tar.Z
tar xf cspice.tar
cd cspice
```

2. Run the stock NAIF build:

```bash
/bin/csh -f makeall.csh
```

This produces the static libraries:
- `lib/cspice.a`
- `lib/csupport.a`

3. Link the shared object manually from those archives:

```bash
mkdir -p /tmp/cspice-build/shared
gcc -shared \
  -Wl,-soname,libcspice.so \
  -Wl,--whole-archive /tmp/cspice-build/cspice/lib/cspice.a /tmp/cspice-build/cspice/lib/csupport.a \
  -Wl,--no-whole-archive \
  -lm \
  -o /tmp/cspice-build/shared/libcspice.so
```

4. Verify the output:

```bash
file /tmp/cspice-build/shared/libcspice.so
ldd /tmp/cspice-build/shared/libcspice.so
readelf -d /tmp/cspice-build/shared/libcspice.so | rg SONAME
nm -D /tmp/cspice-build/shared/libcspice.so | rg "furnsh_c|spkezr_c|str2et_c"
```

Expected results:
- `file` reports an ELF shared object
- `ldd` resolves only standard system libraries such as `libm` and `libc`
- `readelf` shows `Library soname: [libcspice.so]`
- `nm -D` shows exported CSPICE entry points such as `furnsh_c`

5. Copy the shared object into the repo’s Linux third-party runtime location:

```bash
mkdir -p native/third_party/cspice/linux-x64
cp /tmp/cspice-build/shared/libcspice.so native/third_party/cspice/linux-x64/libcspice.so
```

#### Notes and Constraints
- **Do not rely on static-only Linux output**: `cspice.a` alone is not sufficient for the current .NET P/Invoke path.
- **Keep filenames platform-native**:
  - Linux: `libcspice.so`
- **Do not treat build-output copies as authoritative**: the long-term contract is that reusable runtime payloads live under `native/third_party/cspice/<platform>/`.
- **Rebuild when toolkit version changes**: if CSPICE sources/toolkit revision change, rebuild the shared object and replace the checked-in runtime payload.

---

### Secret Management

The current runtime does not use a general-purpose secret store inside the application. Instead, the config files declare the names of environment variables that must be resolved by the surrounding host or container runtime. Secrets must not be committed to the repo, baked into container images, or written into the mounted scenario/workspace storage.

#### Secret Inventory
- **Remote LLM provider credentials**:
  - `OPENAI_API_KEY`
  - `ANTHROPIC_API_KEY`
  - `GOOGLE_API_KEY`
- **MCP bearer token**:
  - `LUNAR_ANALYST_MCP_TOKEN`
  - Used when MCP HTTP/SSE auth is enabled and when external CLI agents connect back to Lunar Analyst's MCP endpoint.
- **Session-scoped runtime tokens**:
  - Notebook session tokens and optional Marimo tokens are generated at runtime by the service layer.
  - These are operational credentials, not operator-provisioned bootstrap secrets, and should not be pre-populated in checked-in config.

#### Secret-Handling Rules
- Config TOML files may name secret environment variables via fields such as `api_key_env`, `mcp_auth_token_env`, and `http_auth_token_env`, but the secret values themselves must come from the execution environment.
- Secret values must not be stored under `workspace_root`, `.assistant/`, scenario folders, Docker images, or Kubernetes `ConfigMap` objects.
- For local/container development, inject secrets at process or container launch time; for Kubernetes, inject them from `Secret` objects by `env.valueFrom.secretKeyRef`.

#### Context A: Host OS Development on Pop!_OS
- **Current state**: there is no repo-standardized secret injection mechanism yet for host-native development.
- **Recommended standard**:
  - Store developer secrets in a user-owned file outside the repo, for example `~/.config/lunar-analyst/host-secrets.env`, with mode `0600`.
  - Use `direnv` from the repo root to load that file into the shell automatically when entering the checkout.
  - Keep only non-secret wiring in the repo, for example an `.envrc` that runs `source_env "$HOME/.config/lunar-analyst/host-secrets.env"` and exports non-secret defaults such as `LUNAR_ANALYST_CONFIG_TOML`.
- **Why this is the recommended default**:
  - It keeps secrets out of git and out of the scenario workspace.
  - It works with the existing host-native launch flow (`scripts/run-host-dev.sh`) because the backend already reads secrets from environment variables.
  - It is simple, Linux-native, and does not require changing application code.
- **Operational rule**: do not put long-lived secrets in `~/.bashrc`, `.profile`, or repo-local `.env` files under version control.

#### Context B: Development Container
- **Current repo behavior**:
  - `docker/compose.dev.yml` and `config/lunar_analyst.devcontainer.toml` define the container shape and name the secret env vars used by the backend.
  - The dev image does not bake provider credentials into the image.
  - The compose file does not currently provision secret values by itself.
- **Required handling**:
  - Inject secrets from the developer environment at container launch, for example from an uncommitted compose override, `docker compose --env-file`, or exported host variables.
  - Treat the bind-mounted repo and mounted workspace as non-secret storage.
- **Design intent**: the dev container is an execution boundary, not a secret store.

#### Context C: Runtime Container on Pop!_OS
- **Current repo behavior**:
  - `docker/Dockerfile.runtime` bakes application code and non-secret config into the immutable image.
  - `scripts/docker-run-runtime.sh` mounts only the writable workspace root and does not inject secret values by default.
- **Required handling**:
  - Supply provider keys and MCP tokens at `docker run` time with `-e` or `--env-file`.
  - Keep secret material in the container environment only; never copy it into the image or mounted workspace.
- **Operational implication**: local production-style runtime validation currently depends on operator-supplied environment injection.

#### Context D: Runtime Container on Kubernetes in the National Research Platform
- **Current repo behavior**:
  - `deploy/nrp/runtime-configmap.yaml` carries non-secret runtime configuration only.
  - `deploy/nrp/runtime-deployment.yaml` injects `OPENAI_API_KEY` and `LUNAR_ANALYST_MCP_TOKEN` via `secretKeyRef` from the `lunar-analyst-secrets` `Secret`.
  - `deploy/nrp/namespace-notes.md` documents the bootstrap command for creating that `Secret`.
- **Required handling**:
  - Keep provider/API credentials in Kubernetes `Secret` objects, not in `ConfigMap`, image layers, or PVC contents.
  - Mount only the workspace PVC as persistent application state; secrets remain outside that storage boundary.
- **Future-hardening direction**:
  - If NRP operations mature beyond manual `kubectl create secret`, move to a managed secret workflow such as External Secrets or Sealed Secrets, but the runtime contract should remain env-based at the pod boundary.

#### Current Design Gap
- The only material gap in the current four-context story is Context A: host-native Pop!_OS development does not yet have a standardized, documented developer workflow in the repo.
- The recommended resolution is to standardize on `direnv` plus a user-scoped `~/.config/lunar-analyst/host-secrets.env` file and document that as the canonical host-development pattern.

## 3. Component Details

### ADR 0050+ Cross-Reference
- **Local Lunar Nomenclature Navigation**: See `docs/ADR.0050.local_lunar_nomenclature_and_feature_navigation.md` for the global `scenario_catalog.db` nomenclature tables (`lunar_features`, `lunar_features_fts`, `lunar_features_rtree`) and the `resolve_exact` / `search_fuzzy` / `nearby` navigation flow used by API, assistant, and workspace search UI.
- **Entity Reference Resolution**: See `docs/ADR.0051.entity_reference_resolution_for_segment_processing.md` for deterministic, bounded verb normalization + entity reference resolution (feature/scenario/layer/file/colormap), same-turn pronoun binding, and verb+entity-kind gating used by assistant segment dispatch.
- **Entity-Kind-Aware Deterministic Routing and Domain Entity Context**: See `docs/ADR.0053.entity_kind_aware_deterministic_routing_and_domain_entity_context.md` for the unified deterministic recognizer with entity-kind-aware typed rule matrix, verb operation candidates, cross-segment entity binding memory, and primary-LLM `<DOMAIN_ENTITY_CONTEXT>` injection.
- **Deterministic Segment Routing Simplification**: See `docs/ADR.0054.deterministic_segment_routing_and_flag_reduction.md` for removal of secondary segment intent classification, deterministic-first routing with primary-LLM fallback for unmatched segments, and removal of obsolete assistant toggles.
- **Deterministic Noun-Phrase Product Matching**: See `docs/ADR.0055.deterministic_noun_phrase_product_matching.md` for product-type-owned alias matching, create-intent gating, and propagation of candidate product-type hints into primary-LLM handoff context.

### 3.1 Web Application (React + OpenLayers + FastAPI)
- **Current Primary UI**: The active Lunar Analyst application is browser-first (and Tauri-hostable) with a React/OpenLayers frontend backed by FastAPI APIs and WebSocket events.
- **Frontend Stack**: React + Vite + OpenLayers with `proj4`, `geotiff.js`, and **Blueprint JS 6** controls for layer and job interactions.
- **Workspace UX**: The React client now uses a persistent dockable workspace shell (`flexlayout-react`) with a left activity bar, a center editor/viewer region, a right assistant sidebar, and a bottom messages region.
- **Workspace Layout Contract**:
    - Exactly one Map panel is allowed.
    - The Map panel is non-closable.
    - Left-activity surfaces include Scenario Explorer, Layer Manager, Map Layers, Tools, Jobs, and Assistant.
    - The center region hosts the Map plus scenario-scoped notebook/editor/viewer tabs.
    - The bottom region hosts `Messages`.
    - Other standard panels are closable and can be re-docked.
    - `Reset Layout` restores the standard desktop panel arrangement.
    - Layout state is persisted in browser storage with a schema-versioned key; incompatible older saved layouts are discarded.
- **Scenario-Scoped Center Tabs**:
    - Center tabs may belong to different scenarios at the same time.
    - Selecting a scenario-scoped center tab switches the active scenario to match that tab so explorer contents, assistant context, and tool defaults stay coherent.
- **Scenario Selection Persistence**: The active scenario selection is persisted in browser storage and restored across frontend/server restarts; explicit URL scenario selection still overrides saved state.
- **Layer Controls and Ordering**: Visibility, opacity, brightness, contrast, and colormap controls are persisted through API-backed layer state; reordering uses drag/drop with insertion-line targeting.
- **Assistant Workspace UX**:
    - The assistant can be used in two coordinated forms that share the same active assistant session:
        - a compact right-sidebar assistant input/output pair for lightweight interaction while working on the map;
        - a focused center `Assistant` workspace tab with the transcript above, input below, and a resizable splitter between them.
    - Assistant prompt drafts are preserved across panel close/reopen and remount.
- **Tools Workspace UX**: Tools is a dockable panel within the main workspace. Tool-argument drafts are preserved across panel close/reopen and remount, while transient UI state such as row selection can reset.
- **Messages Workspace UX**:
    - The bottom `Messages` pane is a transcript-style message/log surface rather than a second jobs manager.
    - Background job progress is shown compactly in the title bar, while message history accumulates in the pane body and is persisted to scenario-scoped log storage.
- **Moon Trek Catalog UX**: Moon Trek layer discovery is exposed through a dedicated dockable workspace panel; search controls remain fixed while the results list fills remaining panel height and scrolls.
- **Moon Trek Overlay Behavior**: Added Moon Trek overlays are treated as normal map overlays in Layer Manager: reorderable with scenario layers, removable through standard layer controls, and adjustable for opacity/brightness/contrast.
- **Moon Trek Base Layer Startup Policy**: Base-layer initialization no longer blocks on live WMTS `GetCapabilities`; the client uses a deterministic south-pole tile-grid profile for the configured base product/style/matrix-set and treats Trek metadata as slow-changing.
- **Startup Responsiveness Policy**: FastAPI startup is kept non-blocking for readiness by using lazy service-container initialization plus background warmup tasks for optional startup actions (Marimo auto-start and scenario auto-discovery).
- **Assistant Initialization Isolation (ADR 0040, target architecture)**:
    - Core scenario/product/layer/job services remain eager at container build.
    - Assistant provider registration, model metadata loading, RAG wrapper setup, and global RAG index construction are lazy and assistant-triggered.
    - Assistant/RAG initialization failures must not block non-assistant API readiness or scenario/job routes.
    - Optional assistant warmup is allowed only as non-blocking, failure-tolerant background work.
- **Scenario Catalog Hydration Policy**: Scenario product/file catalogs are hydrated lazily per scenario from `scenario.db` and memoized in process memory; full filesystem reconcile is no longer performed during service-container construction.
- **Job Discovery and Execution**: Jobs are listed from `GET /api/v1/job-definitions`, including typed handler-backed jobs, notebook-defined jobs, and scenario-local runnable script/notebook entries discovered under `.notebook_jobs`.
- **Worker-Only Compute Rule (ADR 0056)**: Typed handlers that are long-running, host `pythonnet`/`.NET`, instantiate `MoonlibBridge`, use `LightmapStreamingClient` for native lighting work, or run heavy native/GDAL loops must be marked worker-only when their inputs are serializable for the worker protocol.
- **Moonlib Python Entry Surface Rule**: Production Python application/worker compute paths must invoke native moonlib functionality through `MoonlibBridge` only. Direct use of other moonlib runtime types from production Python code is disallowed; bootstrap/runtime loading seams (for example native import/bootstrap smoke checks) and test code are the only approved exceptions.
    - Worker-only handlers are identified by the `worker-only` tool tag and, for compatibility, `JobService.WORKER_ONLY_HANDLER_NAMES`.
    - FastAPI must route those handlers through the isolated worker protocol by default, even for `mode=immediate`; immediate mode waits for the isolated worker result rather than executing the handler body inside FastAPI.
    - `LUNAR_ANALYST_NATIVE_INLINE_HANDLERS` is only a local development/debug escape hatch for diagnosing worker behavior. It is not a supported production mode and must not be used to justify new inline native or heavy compute paths.
    - Current worker-only handlers include horizon generation, native lightmap reductions, PSR raster generation, and the draft lightmap timeseries surface.
    - Remaining raster/map-algebra surfaces that still depend on backend scenario-service state, including general `raster.calculate`, `raster.transform`, and `terrain.viewshed`, are tracked as worker-isolation gaps until their worker contexts can carry all required scenario/catalog state explicitly.
- **Scenario Explorer File Opening Contract**:
    - Explorer file rows now support explicit `Open` behavior from the context menu and double-click.
    - File opening is extension- and capability-aware:
        - image files open in an embedded image viewer tab;
        - `.csv` files open in an editable table tab;
        - `.txt` files open in a text editor tab;
        - `.py` files try notebook opening first and fall back to the Python editor when they are not recognized as Marimo notebooks.
    - Single-click row selection remains distinct from file opening.
- **Scenario File Editing/Viewing Contract**:
    - Text-like scenario files are edited through API-backed, scenario-root-safe read/write routes rather than direct client filesystem access.
    - Viewer/editor tabs are scenario-scoped center tabs and persist their scenario identity in workspace layout state.
- **Image Inspection Contract**:
    - Image viewer tabs expose `Fit to Pane` and `Original Size` display modes while preserving aspect ratio and scrollability.
    - The client does not parse GeoTIFF or other image georeferencing locally.
    - Backend APIs supply image metadata and per-pixel readouts:
        - `GET /api/v1/scenarios/{scenario_id}/image-metadata`
        - `GET /api/v1/scenarios/{scenario_id}/image-readout`
    - Readout responses can include pixel coordinates, projection name, projected easting/northing, and longitude/latitude when backend conversion is possible.
- **Unified Tool Model Contract (ADR 0019)**: Canonical identifiers are `implementation_name` and `job_id` across job APIs, assistant tools, and MCP surfaces. Compatibility aliases (`handler_name`, `run_id`) are legacy-only and should not be used for new flows.
- **Tool Implementation Discovery Contract**: Canonical implementation introspection is exposed at `GET /api/v1/jobs/implementations`; compatibility alias `GET /api/v1/jobs/handlers` returns the same payload for legacy clients.
- **Notebook/Script Runtime Contract**: Headless runs support structured progress and cancellation; scenario scripts are authored as executable Python scripts using shared helpers from `backend.notebook.notebook_helper`.
- **Notebook and Python Authoring UX**:
    - Scenario Explorer provides `New Notebook` and `New Python File` actions in the scenario root.
    - New notebooks open directly into scenario-scoped Marimo tabs rather than the generic Marimo landing page.
    - New Python files open into a syntax-highlighted editor with `Save`, `Lint`, and `Run Script`.
- **Runtime Mode Isolation (ADR 0037, Operational Invariant)**:
    - Script/notebook runs use explicit `runtime_mode`: `osgeo` (default) or `moonlib`.
    - Resolution precedence is `request parameter > job params > script pragma (# lunar_runtime: ...) > default`.
    - `osgeo` mode pins GDAL/PROJ to Python `osgeo` paths and rejects `moonlib`/`pythonnet` imports.
    - `moonlib` mode bootstraps native runtime first and avoids `osgeo` environment override.
- **Notebook Job Process Control**: Active notebook runner subprocesses are tracked in backend state to support explicit cancellation, service-shutdown cleanup, and signal-driven termination (`SIGINT`/`SIGTERM`) so long-running jobs do not block process shutdown.
- **Notebook Live Log Contract**: Per-run stdout/stderr/combined logs are available via `GET /api/v1/jobs/{job_id}/logs`, and the Jobs Manager polls these logs during queued/running states.
- **Job Error Contract Improvements**: Native DEM dimension validation errors from horizon generation are normalized to structured API `422` responses (`invalid_dem_dimensions`) with actionable detail fields for UI display.
- **Tools UI Launch Feedback**: Job launch failures are surfaced directly in the Tools pane, and horizon notebook templates default `compress_horizons=true` for new launches.
- **Runtime Import Preference**: Notebook job subprocesses prepend `<repo_root>/moonlayers_pkg` to `PYTHONPATH` when present, keeping job execution aligned with in-repo MoonLayers development.
- **Raster Delivery Policy (Map vs Notebook Image)**: Per `docs/ADR.0006.raster_delivery_crs_policy.md`, map-facing OpenLayers delivery uses backend-warped `ESRI:103878` display derivatives, while non-map notebook image presentation can use native/source CRS outputs.
- **Display Derivative Storage**: Warped map-display rasters are stored under scenario-managed display paths, for example `display/{product_id}/esri_103878/{source_stem}.{warp_hash}.cog.tif`, with lineage metadata in scenario DB.
- **Raster Display Validity Policy**:
    - Warped display derivatives synthesize an explicit alpha band when reprojection creates invalid output coverage, preserving valid data values (including byte `0`) without relying on nodata/value collisions.
    - Raster stats (`GET /api/v1/lunar-analyst/files/{file_id}/raster-stats`) include `alpha_band`, and min/max calculations are alpha-aware when that mask is present.
    - Frontend layer-style hydration applies `alphaBand` masking and clears stale `nodataCutoff` when nodata is absent; deferred `GDAL_NODATA` metadata access is patched to return `null` instead of aborting GeoTIFF source initialization.
- **Backend Core Integration**: FastAPI provides scenario/product/layer scaffolding, file-id-based asset serving (`GET /api/v1/files/{file_id}` with range support), and Stage 1 job/layer event streaming (`WS /api/v1/events`).
- **Moon Trek Backend APIs**:
    - `GET /api/v1/trek/layers` and `GET /api/v1/trek/layers:search` expose catalog list/search.
    - `GET /api/v1/trek/layers/{product_label}/features` proxies feature retrieval for browser-safe loading.
- **Moon Trek Load Fallback Chain**:
    - Try ArcGIS feature service (`MapServer`/`FeatureServer`) via backend proxy.
    - If unavailable, download Trek archive stream and parse vector payloads (including nested shapefile layouts) server-side.
    - If feature loading fails, try WMTS tiles.
    - If WMTS fails, render metadata-derived footprint as final fallback.
- **Moon Trek Cache Controls**: Trek catalog and feature-proxy caches are in-memory with long TTL defaults and are configurable under `[backend.trek]` (`catalog_cache_ttl_seconds`, `feature_cache_ttl_seconds`).
- **Assistant UX Panels**:
    - **Assistant Input**: dockable panel for session create/select/compact, prose request entry, confirmation controls, and model/access selection.
    - **Assistant Output**: dockable panel showing the chronological assistant/system/user message log for the active assistant session.
- **Assistant API Surface**: FastAPI now serves assistant endpoints under `/api/v1/assistant` for session lifecycle, message retrieval, turn execution, policy updates, confirmation decisions, provider catalog lookup, and context compaction.
- **Assistant Event Stream**: Assistant turn/tool/confirmation events stream on `WS /api/v1/assistant/sessions/{session_id}/events` using `backend/contracts/assistant_events.py` (`schema_version = "1.1"`).
- **Assistant Provider Catalog Startup Use**: Frontend assistant startup requests provider catalog (`GET /api/v1/assistant/providers`) to populate model choices before interactive turns.
- **Assistant External Agent Access Mode UX**: For external CLI providers, the assistant input exposes a per-turn access-mode toggle (`mcp_only` vs `scenario_root`) that is sent with turn requests and enforced by provider launch policy.
- **Assistant Rich Output Contract**: Assistant messages can now carry typed render outputs (`table`, `image`, `plot`, `artifact_card`) alongside prose, allowing the response pane to render previews without scraping assistant text.
- **Assistant Stage Event Contract**: The assistant WS stream includes stage-level lifecycle events for hybrid execution (`prompt_segmentation_completed`, `prompt_classification_completed`, `turn_execution_plan_built`, `turn_execution_plan_validation_failed`, `segment_execution_started`, `segment_execution_finished`, `deterministic_handoff_built`, `turn_merge_completed`, `turn_status_finalized`) to support UI/runtime observability and eval traceability.

### 3.2 Assistant + MCP Gateway
- **Single Assistant Mode**: One assistant supports both chat and command execution. It can:
    - Describe Lunar Analyst capabilities (`capabilities.describe`).
    - Execute read-only and mutating tools from prose-triggered intents.
    - Describe produced artifacts (GeoTIFF, table/CSV, plot/image).
    - Set current scenario from prose with fuzzy matching, ambiguity prompts, and scenario-change audit events.
- **Tool Execution Model**: Tools are defined in `backend/services/assistant/tool_registry.py` and execute through existing backend services (`scenario_service`, `product_service`, `layer_service`, `job_service`) to preserve current control-plane invariants.
  - **Tool Schema Discovery Policy**:
      - The assistant may use `tools.search` (keyword lookup) and `tools.describe` (exact-name schema expansion) to fetch focused tool contracts instead of relying on full-catalog schema injection on every turn.
      - This keeps model context bounded as the tool catalog grows while preserving access to precise tool arguments when needed.
- **Hybrid Command Routing Policy (ADRs 0022, 0053)**:
    - A data-driven deterministic command router (`backend/services/assistant/command_router.py`) and a unified deterministic recognizer (`backend/services/assistant/deterministic_recognizer.py`) together handle imperative intents before model tool-loop.
    - Router action specs are loaded from YAML (`config/assistant_action_router.yaml`) at startup with fail-fast validation (regex compile, tool-name references, placeholder bindings, step schema).
    - Router action specs map to existing unified tool names and execute through the existing tool execution surface (no duplicated tool contracts or compute logic).
    - **Entity-kind-aware routing (ADR 0053)**: the unified deterministic recognizer evaluates a typed rule matrix using canonical verb operation candidates + resolved target entity kind (feature, layer, file, scenario) + confidence/ambiguity state, rather than relying on regex pattern matching alone. Routing for `goto/show/hide` + entity kind is explicit: `show`/`goto` + `feature` routes to `location.goto`; `show`/`hide` + `layer` routes to layer visibility update; ambiguous `show` with unresolved layer/file reference triggers clarification; `show` + file resolves to find-or-import-then-show.
    - Regex/intent matching includes complexity guards (`if/when/unless/only if/...`) and deny patterns to avoid shadowing higher-order analytical prompts.
    - ADR.0054 makes this behavior baseline runtime policy (not feature-gated): deterministic recognizer evaluates each segment first; unmatched segments fall back to the primary LLM.
    - Removed assistant-routing keys are treated as invalid configuration inputs by the config validator.
- **Deterministic Sub-Agent Steps (ADR 0023)**:
    - Deterministic action plans can include bounded `agent_call` steps in addition to `tool_call` steps.
    - `agent_call` steps are constrained by per-step tool allowlists, output JSON schema validation, iteration/token/time limits, and deterministic post-step slot binding.
    - `agent_call` allowlists are restricted to read-only tools; mutating tools are rejected during startup spec validation.
    - This behavior is runtime policy in the current branch and is not toggled by a dedicated assistant-routing flag.
- **Partial Deterministic Execution Policy**:
    - For multi-segment prompts, matched segments execute deterministically first.
    - Unmatched remainder is handed off to model tool-loop in the same turn with updated runtime state.
    - This preserves deterministic reliability for known imperative segments without forcing all-or-nothing fallback.
- **Intent-Unit Hybrid Reliability Pipeline (ADRs 0026-0034)**:
    - Prompt ingestion now runs a fixed assistant pipeline in order: prompt segmentation -> prompt classification -> turn execution-plan construction (planner contract) -> deterministic execution and/or model continuation -> per-segment state merge -> success-semantics finalization.
    - **Segmentation (`backend/services/assistant/prompt_segmenter.py`)**:
        - Uses the configured spaCy model for sentence boundaries, then applies deterministic clause-splitting heuristics within those sentence spans.
        - Emits ordered prompt segments with offsets, confidence, imperative-candidate flag, and complexity-guard flag.
    - **Provisional Classification (`backend/services/assistant/prompt_classifier.py`)**:
        - Performs deterministic first-pass segment labeling before unified deterministic recognizer promotion.
        - Segment classes are `command`, `create_product`, and `other`.
        - `create_product` requires positive create intent plus product-type matching; noun phrases alone are insufficient.
        - Candidate product types are still extracted for all segments and attached as structured hints even when final class is `other`.
    - **Entity Resolution and Verb Operation Candidates (`backend/services/assistant/entity_reference_resolver.py`, `backend/services/assistant/verb_normalizer.py`)**:
        - Entity resolution runs before unified deterministic recognition, supplying typed mention metadata (`kind`, `resolved_id`, `confidence`, `reason_code`) and ambiguity candidates for noun phrases in the segment.
        - Verb normalization emits operation **candidate sets** (`operation_candidates`, `matched_aliases_by_operation`) from synonym tables rather than forcing a single early canonical operation; final operation selection occurs inside recognizer rule evaluation.
        - Direct-object dependency signal (`direct_object_candidate`) is used to disambiguate target selection when multiple resolved entities are present.
        - Cross-segment binding memory (`_apply_prior_mention_bindings`) preserves resolved mention bindings from earlier segments in the same turn, reducing clarification requests for follow-up segments.
    - **Unified Deterministic Recognizer (`backend/services/assistant/deterministic_recognizer.py`)**:
        - Single decision engine replacing the prior split command-router/typed-intent-promotion branches.
        - Evaluates deterministic regex constraints and entity-kind typed rules in one stage using verb operation candidates, resolved entity kinds/targets, confidence/ambiguity state, and optional syntax constraints.
        - Produces one of: `planned_tool_steps`, `clarification_required`, or `no_match`; includes a decision trace (`matched_rule_id`, `reason`, `blocked_reason`).
        - `no_match` outcomes route directly to primary-LLM handling for that segment.
    - **Primary LLM Domain Entity Context (`backend/services/assistant/assistant_service.py`, `backend/services/assistant/system_prompt.txt`)**:
        - For segments that reach the model-loop path (deterministic `no_match` or explicit model-required), the primary LLM prompt is injected with a `<DOMAIN_ENTITY_CONTEXT>` block and a `<USER_QUERY>` wrapper containing the original segment text unchanged.
        - The context block carries bounded structured entity mentions (`mention_text`, `kind`, `resolved_id`, `confidence`, `reason_code`) plus capped top ambiguity candidates when applicable.
        - The context block also carries per-segment classification metadata and `candidate_product_types` extracted deterministically from product-type aliases, so model fallback can use noun-phrase grounding without forcing deterministic create-product execution.
        - System prompt instructs the primary LLM to use provided domain-entity context for grounded reasoning and avoid invention or contradiction of provided entity details.
        - `<DOMAIN_ENTITY_CONTEXT>` injection applies to the primary LLM path by default.
    - **Execution Plan Builder (`backend/services/assistant/turn_execution_plan.py`)**:
        - Builds a versioned execution-plan document (`schema_version=1.0`) with segment execution modes and compact step metadata.
        - Materializes executable turn structure from prior segmentation/classification results; it is not a search-based planner.
        - Validates ordering, schema shape, and mode consistency.
    - **Execution State and Merge (`backend/services/assistant/turn_state_manager.py`)**:
        - Tracks per-segment runtime state.
        - Produces deterministic handoff context for unresolved model segments.
        - Produces merged segment outcomes for UI/eval traceability.
    - **Argument Repair (`backend/services/assistant/tool_argument_repair.py`)**:
        - Applies bounded deterministic repairs before tool invocation (alias mapping, safe defaults, enum normalization, path normalization).
        - Blocks unsafe path traversal (`..`) and returns clarification-required outcomes.
    - **Success Semantics (`backend/services/assistant/success_semantics.py`)**:
        - Computes turn aggregate status (`success`, `partial_success`, `failed`) and per-segment outcome metadata.
        - Mutating success requires executed mutation evidence and postcondition pass.
    - **Observability Taxonomy (`backend/services/assistant/telemetry_codes.py`)**:
        - Uses canonical event families and machine-readable error codes for routing/execution-plan/tool/safety/merge paths.
        - Emits stage latency metrics in turn usage metadata (`latency_segmentation_ms`, `latency_classification_ms`, `latency_execution_plan_ms`, etc.).
    - This architecture is now the default behavior on this branch (no feature-gate toggles for these layers).
- **Typed Entity Memory + Reference Resolution (ADR 0035, planned V1)**:
    - Introduces a typed per-session working set (`scenario`, `layer`, `product`, `file`, `tool_output`) used for deterministic reference binding in follow-up turns.
    - Adds a resolver stage between planning and tool execution to map ambiguous references (for example "that layer", "the last output") to concrete ids with score/rank telemetry.
    - Design includes pin/unpin semantics for user-controlled working-set persistence and explicit clarification-required outcomes when resolution confidence is below threshold.
    - Candidate API surface includes session working-set read/pin/unpin endpoints under `/api/v1/assistant/sessions/{session_id}/working-set...`.
- **Unified Tool Model Contract**:
    - `implementation_name` and `job_id` are the normative identifiers across assistant/tool/MCP contracts.
    - `handler_name` and `run_id` are accepted only as compatibility aliases for legacy callers.
- **Artifact Production Policy**:
    - Tools, not freeform assistant prose, are the primary producers of typed render outputs.
    - `artifact.describe_geotiff` is metadata-only.
    - `artifact.preview_geotiff` generates a preview PNG under scenario-managed `.assistant_previews/` and returns a file-backed artifact reference.
    - `artifact.stats_geotiff` returns numeric raster statistics (valid counts, min/max/mean/std, percentiles, per-band stats) without embedding raster payloads.
    - `artifact.describe_plot` prefers file-backed outputs when the referenced file can be resolved or registered to a scenario `file_id`; inline base64 is a fallback only when no file-backed identity is available.
- **Script Authoring Tooling**:
    - `scenario.write_run_script` is the preferred mutation path for “write script then run it” workflows.
    - Scenario Python jobs run with the active scenario root as the working directory, so scripts should use scenario-relative paths by default unless the user requests a different location.
- **Response Rendering vs Model Context Contract**:
    - Rich artifact payloads are persisted for UI rendering and logged in raw form for observability.
    - The assistant model loop receives compact tool summaries plus artifact references (`file_id`, generated relative paths, key stats), not full inline render payloads, to control token growth.
    - Large inventory/list payloads are compacted aggressively before model replay (for example `product.list`, `product.files`, `layer.list_visible`) to reduce context bloat and iterative stall risk.
- **Raster Calculation Tool**: `raster.calculate` is exposed as a first-class assistant/MCP tool (confirmation-gated as `launch_job`) and routes to typed `ToolImplementations.raster_calculate` execution.
    - Overwrite behavior is explicit via `overwrite_mode`:
        - `ask` (default): requires user confirmation if output exists.
        - `always`: overwrite existing output.
        - `never`: fail when output exists.
    - One-call publish behavior supports `publish_layer.enabled=true` to register and show a layer directly from the same calculation call.
    - For selection/highlight masks where non-selected pixels should be transparent, set `publish_layer.transparent_background=true` to publish byte-mask outputs with `NODATA=0`.
    - Region/mask DSL operations now include:
        - `label_regions(mask[, cleanup_mode[, cleanup_iterations]])`;
        - `region_sizes(mask[, cleanup_mode[, cleanup_iterations]])`;
        - `filter_regions_by_size(mask, threshold, comparator[, cleanup_mode[, cleanup_iterations]])` with comparators `>=` and `<=`, using cleanup-aware seed selection projected back to original connected components to preserve kept-region shapes.
- **Scripted Raster Transform Tool**: `raster.transform` is exposed as a first-class assistant/MCP tool (confirmation-gated as `launch_job`) and routes to typed `ToolImplementations.raster_transform` execution.
    - Overwrite behavior is explicit via `overwrite_mode` (`ask` | `always` | `never`), with legacy `overwrite` accepted as a compatibility alias and normalized to canonical behavior.
    - Temporal requests prefer a reserved `times` input binding plus horizon-derived `temporal_source` bindings, while legacy `signal` plus top-level `time_*` fields remain as a compatibility path.
    - DSL reliability policy includes:
        - repair-oriented validation hints (missing `result`, elementwise BoolOp guidance, unknown-function suggestions, temporal binding mismatch hints);
        - sealed NumPy alias support for `np.where` only, and `import numpy as np` is accepted as a no-op compatibility line (other imports and non-allowlisted `np.<fn>` calls remain disallowed);
        - reserved facade identifiers (for example `np`) cannot be used as input or assignment names.
    - Internal eval prefilter support exists for `raster.transform` classification (contract eligibility vs execution failure) and is intentionally non-public (not exposed as assistant/MCP tool).
- **Mutation Safety Policy**: Mutating tool categories map to action types (`launch_job`, `import_file`, `move_path`, `update_layer_state`) and require confirmation by default. Session policy supports "allow once", "always allow this action type", and "deny once".
- **Scenario-Switch Auto-Zoom Reliability**: Assistant-driven scenario switches now ignore placeholder extents (`[-1,-1,1,1]`) and fall back to DEM-derived extent computation (resolved path + reprojection to `ESRI:103878`) before publishing map extent updates.
- **Provider Routing**:
    - Local: Ollama and Python subprocess adapters.
    - Local external MCP CLI agents: Codex CLI and Gemini CLI adapters (`external_mcp_agent` execution mode).
    - Remote: OpenAI, Anthropic, Google adapters with usage telemetry and cache-attempt/cache-applied indicators.
    - Unified RAG wrapping is configured under `[backend.llm.rag]` and decorates selected backend tool-loop providers in place (for example `ollama`, `openai`) without changing provider IDs.
    - External CLI providers (`external_mcp_agent` mode, for example Codex CLI/Gemini CLI) are intentionally not wrapped by backend RAG in the current implementation.
    - Provider defaults and availability are configured under `[backend.llm]`.
    - Provider registry initialization is lazy; provider/RAG setup occurs on first assistant-dependent use rather than as a mandatory backend-startup prerequisite.
- **RAG Retrieval and Index Policy**:
    - RAG uses a workspace-global SQLite index (`.assistant/rag/global_rag.db`) with FTS5 lexical retrieval.
    - Source corpus defaults to `docs/rag_corpus` and supports `.md`, `.txt`, `.csv`, `.pdf`, `.html/.htm`, and `.json`.
    - Retrieval is channel-aware (`procedural`, `domain`, `mixed`) with routed fan-out/fusion in wrapper providers.
    - Lexical query planning uses filtered query terms with configurable term cap and `AND`->`OR` fallback for low-hit cases.
    - Retrieved chunk text (not short snippets) is injected into model context with source tags; snippets are stored for metadata/reference display.
    - `metadata.source_references` persists structured provenance (`relative_path`, `chunk_id`, score, snippet, title, channel) without API/WS schema changes.
    - Global RAG index construction is lazy behind assistant/provider initialization; RAG storage failures are assistant-scope failures, not backend-startup failures for core control-plane APIs.
- **RAG Corpus Authoring Contract**:
    - Markdown/text corpus docs can include front matter (`key: value`) such as `title`, `channel`, `source_kind`, `source_ref`, `chunking`, and chunk-size hints.
    - `index: false` excludes a document from retrieval indexing (used by operational docs such as corpus README).
    - `source_kind: file` allows descriptor docs to ingest external static files from allowlisted roots.
    - `source_kind: url` supports static fetch paths only when enabled; JS-render/crawl behavior is intentionally out of scope.
    - If a descriptor doc references an in-corpus source file (for example same-basename `.txt` -> `.pdf`), the referenced file is reserved and skipped as standalone indexing input to avoid duplicate retrieval entries.
- **Prompt Caching Policy**:
    - OpenAI-backed turns attach a stable `prompt_cache_key` derived from provider/model, system prompt, tool schema, active scenario, and compacted session summary.
    - The cacheable prefix is intentionally kept stable by excluding large rich-output payloads from model replay.
    - Cache telemetry remains visible in assistant usage metadata (`cached_prompt_tokens`, cache-attempt/cache-applied indicators).
- **Execution Mode Contract**:
    - `tool_loop`: backend-mediated iterative tool-calling providers.
    - `external_mcp_agent`: external CLI agents that plan and call Lunar Analyst tools through MCP transport.
- **External Agent Scenario Context Contract**:
    - External-agent system prompts include both `Active scenario_id` and `Active scenario_directory`.
    - In `scenario_root` mode, provider launch uses the active scenario directory as per-turn working directory (validated to remain under configured scenarios root), so switching scenarios across turns does not require restarting the CLI integration.
- **Ollama Model Discovery Policy**: Ollama provider model lists are discovered from local runtime tags (`/api/tags`) when available, with configured model-list fallback when runtime discovery is unavailable.
- **Ollama Context Window Policy**:
    - Backend sets Ollama `num_ctx` explicitly on requests.
    - `num_ctx` selection is stable per model and targets the configured/model-capped maximum context window (default `32768` when model limits are unknown), rather than shrinking to prompt-sized windows.
    - Provider captures `num_ctx`/`num_predict` into completion metadata for eval observability.
- **Parser Describe Follow-up Policy**: Parser fast-path describe tools (`capabilities.describe`, `artifact.describe_*`) attempt one model follow-up completion to convert tool JSON into user-facing prose; if model output is empty/unavailable, assistant falls back to structured tool-summary text.
- **Provider Failure Recovery Policy**: Model tool-loop retries alternate provider/model pairs on provider-call failures (for example local Ollama HTTP 500 runner failures) before declaring turn failure; usage telemetry tracks `fallback_used`.
- **Cross-Provider Fallback Control Policy**:
    - Cross-provider fallback is disabled in the current runtime policy; turn retries remain constrained to the selected provider/model path.
- **Mutation Completion Guardrails**:
    - For state-changing intents routed through model-loop, turns are not treated as successful mutation outcomes unless a mutating tool call actually executes.
    - If mutation intent is detected but unsatisfied, assistant returns explicit failure/clarification guidance rather than implicit success.
- **Deterministic Guidance Trigger Policy**:
    - RAG wrapper can apply deterministic procedural guidance triggers for matched imperative intents (for example layer-visibility policy few-shot snippets), independent of broad lexical retrieval ranking.
- **Execution Provenance Metadata**:
    - Assistant message metadata includes execution origin tags (`deterministic` vs `model_reasoned`) for traceability.
- **Context Strategy Characterization (Literature-Aligned)**:
    - **Hybrid neuro-symbolic orchestration**: entity-kind-aware deterministic routing via unified recognizer (typed rule matrix + verb operation candidates + resolved entity kinds) for narrow imperative intents, plus model reasoning for open-ended requests via primary-LLM fallback on unmatched segments.
    - **ReAct-style bounded tool loop**: iterative tool-calling with explicit iteration/tool-count limits.
    - **Routed RAG with late fusion**: channel-aware retrieval (`procedural`/`domain`/`mixed`) with merged context injection.
    - **Context compression/distillation**: large tool outputs are compacted before replay into model context.
    - **Hierarchical memory**: persisted full transcript plus compaction summaries for active context control.
    - **Policy-guarded agent execution**: confirmation gates, mutation postconditions, complexity guards, and fallback controls.
- **External MCP Fallback Policy**:
    - External-agent system prompt now includes explicit MCP invocation guidance (use `tools/call`, not `resources/read`, and treat dotted names like `capabilities.describe` as exact identifiers).
    - When external CLI output indicates MCP server/tool-call confusion (`unknown MCP server`, `No MCP server named ...`, or shell cmdlet-not-found frames), the backend retries once with explicit guidance.
    - If confusion persists and the user prompt has explicit numbered call lines (`1) Call \`tool.name\` with {...}`), backend executes a safe server-side fallback for non-mutating tools and returns raw `{"tool_outputs":[...]}` JSON.
    - Fallback status is persisted in assistant metadata/usage (`fallback_used`, `fallback_kind`) and surfaced in the assistant response UI.
- **Session Persistence and Resume**:
    - Sessions/messages/turns/tool calls/confirmations are persisted on disk and resumable by `session_id`.
    - Session compaction writes a system summary message plus compaction metadata for long-session context control.
    - Assistant sessions use transactional SQLite storage (`assistant_sessions.db`).
- **MCP Exposure**:
    - HTTP transport: `POST /api/v1/mcp` (JSON-RPC methods: `initialize`, `tools/list`, `tools/call`).
    - SSE transport: `GET /api/v1/mcp/sse` for stream/session setup and `POST /api/v1/mcp/sse/{session_id}` for request submission.
    - SSE compatibility transport: `POST /api/v1/mcp/sse` accepts direct JSON-RPC posts from clients that do not use session-scoped post paths.
    - stdio transport: `python -m backend.tools.run_mcp_server`.
    - MCP HTTP/SSE auth token enforcement is applied when `[backend.mcp].http_auth_token_env` resolves to a token.
    - MCP tool metadata includes confirmation hints for mutating tools.
    - Tool surface includes predefined job list/run, scenario script/notebook list/run, run status/log polling, and cancellation, using canonical `implementation_name`/`job_id` contracts.
    - Run log polling supports configurable head/tail slices and log size metadata.
- **Assistant Configuration Variables (Current)**:
    - Core assistant controls under `[backend.llm]`:
        - `enabled`
        - `default_provider`, `default_model`
        - `action_router_spec_path`
        - `system_prompt_path`
        - `require_confirmation_for_mutations`
        - `max_context_tokens`, `default_max_output_tokens`
        - Note: removed assistant-routing/session-store keys are rejected as invalid config (same handling as unknown/malformed keys).
    - Provider/runtime subsections:
        - `[backend.llm.evals]` (`default_provider`, `default_model` for benchmark runs)
        - `[backend.llm.ollama]`, `[backend.llm.local_subprocess]`, `[backend.llm.codex_cli]`, `[backend.llm.gemini_cli]`
        - `[backend.llm.remote.openai]`, `[backend.llm.remote.anthropic]`, `[backend.llm.remote.google]`
        - `[backend.llm.rag]` (RAG channel routing/index configuration)
        - `[backend.llm.performance]` (tool-loop iteration limits, fallback behavior)

### 3.3 Desktop MapViewer (Legacy Reference)
- **Status**: The WinForms/SkiaSharp desktop viewer is legacy and maintained in an external repository. It is not part of the active runtime path for this codebase.
- **Role in this repo**: Design reference only for selected native rendering/compute concepts; no current product requirements depend on the desktop UI.

### 3.4 MoonLayers Widget (Notebook Widget)
- **Scope**: Notebook-centric map widget for Marimo/Jupyter workflows, complementary to (not replacing) the React web application.
- **Frontend Stack**: Vite-built ES modules using OpenLayers, `proj4`, `geotiff.js`, and `ol-layerswitcher`.
- **Backend Integration**: `moonlayers/geotiff_server.py` provides a per-process HTTP server streaming local COGs via range requests, bypassing browser Data URL limits.
- **NASA Trek Integration**: Built-in boolean search engine for the Moon Trek catalog (800+ layers); auto-configures `TileGrid` from WMTS GetCapabilities metadata.
- **Implementation**: Uses `sync_geotiffs` and `_widget_ready` signals to resolve asynchronous initialization race conditions in notebook environments.
- **OpenLayers Raster Rendering Notes**: For single-band hillshade GeoTIFFs, use explicit grayscale style mapping and nodata transparency in `WebGLTile` layers; tune tile loading/cache settings for pan/zoom stability.

### 3.5 New Horizon Engine (C#, ILGPU/CUDA)
- **Objective**: Generate 0.25° angular resolution (1440 bins) horizon profiles from 2D DEM height-fields.
- **Technology**: `ILGPU` enables high-performance CUDA kernels. The fast path uses a multi-level min/max quadtree pyramid for hierarchical ray marching through terrain.
- **Validation Modes**:
    - `ReferenceHorizonGenerator` is the correctness reference. It is CPU/double based and too slow for production-scale horizon generation.
    - `QuadTreeHorizonGenerator` is the production candidate and current fast path. It must remain visually artifact-free in downstream shadow/lightmap products while preserving most of the current compute-time advantage.
- **Fast-Path Ray Model**:
    - Horizon output is generated in 128x128 patches.
    - For the default 8x8 subpatch size, each patch uses a subpatch-center grid rather than fitting a separate exact ray for every pixel.
    - For each azimuth and DEM pass, ray segment coefficients are fit at subpatch centers and reused by nearby pixels.
    - The GPU then marches one ray segment per pixel/azimuth/DEM through the existing quadtree terrain traversal.
- **Current Horizon Generation Algorithm**:
    - The production path is a hybrid CPU/GPU approximation pipeline. CPU code fits local projected-pixel-space ray polynomials and planar-to-chord distance corrections at subpatch centers; GPU code evaluates those fitted segments for every pixel and azimuth.
    - Each patch produces one horizon tile containing 1440 horizon angles per pixel. The GPU accumulation buffer stores apparent horizon slope across DEM passes and converts slope to degrees once after all passes complete, avoiding per-pass `atan`/`tan` round trips.
    - DEMs are processed as ordered ray segments, allowing a fine inner DEM to be followed by coarser or wider-coverage DEMs. The GPU launches one pass per DEM and accumulates the maximum observed slope into the same horizon buffer.
    - Level-0 terrain samples are bilinear. Coarser pyramid levels store conservative block maxima used for hierarchical culling; blocks that cannot exceed the current horizon are skipped without descending to level 0.
    - Short-range distance/slope calculations use a direct local approximation. Beyond the near-field threshold, the kernel uses a fitted planar-to-chord correction so projected traversal distance maps back to lunar curved-surface geometry.
    - The active chord-correction model is fixed-radius spherical correction: the correction assumes a sphere with radius equal to the lunar reference radius plus the observer-pixel elevation. The older DEM-elevation correction remains in code behind `UseDemElevationChordCorrection` for comparison.
    - Adaptive stepping uses projected ray tangent, margin below the current horizon slope, an angular step cap, and a raster-resolution floor. The current floor is 0.5x active DEM resolution by default and 0.8x for DEM 0 after 100 m from the observer.
    - See `docs/horizon_generation_algorithm.md` for the detailed algorithm walk-through, simplifying assumptions, and current timing notes.
- **Current Production Subpatch Interpolation**:
    - The earlier hard-owner model, where each pixel selected exactly one subpatch polynomial, produced visible 8-pixel seams because the selected fitted ray changed discontinuously at subpatch boundaries.
    - Production now bilinearly interpolates four surrounding subpatch ray segments for each pixel:
        1. identify the four surrounding subpatch segment centers;
        2. load the four ray segments for the current azimuth and DEM pass;
        3. shift each source segment from its fitted center to the target pixel;
        4. bilinearly interpolate the shifted segment coefficients and distance-mapping fields;
        5. march one interpolated segment through the quadtree kernel.
    - This keeps terrain sampling/traversal at one marched ray per pixel/azimuth/DEM, avoiding the much higher cost of marching four rays or fitting per-pixel rays.
- **Segment Grid and Patch Boundaries**:
    - For 8x8 subpatch interpolation, each 128x128 patch consumes an 18x18 window of subpatch centers: the 16x16 interior centers plus a one-center interpolation halo.
    - In the patch pipeline, adjacent patch halo centers are reused through a per-job segment-center cache so shared centers are not refit independently for neighboring patches.
    - At DEM edges, requested halo centers are clamped to valid DEM-wide subpatch centers, and the GPU shifts reused segments from their actual clamped centers rather than from off-DEM ideal halo coordinates.
    - The GPU patch interface still receives a compact contiguous segment array for the patch-local center window.
- **Grid-Convergence Handling**:
    - A previous tile-relative integer azimuth-bin correction caused large artifacts at 128-pixel patch boundaries.
    - The subpatch kernel now applies any bin correction in the subpatch-relative frame instead of the 128x128 tile-relative frame.
    - Horizon files are intended to represent each pixel's horizon in that pixel's local ENU frame; consumers such as lightmap generation should not need to apply patch-local rounded azimuth shifts.
- **Experiment Conclusions Captured in `docs/QUADTREE_UPDATE.md`**:
    - The original 128-pixel seam was largely caused by the tile-relative grid-convergence remap.
    - Disabling the subpatch bin remap did not explain the remaining 8-pixel seams.
    - Reducing the subpatch size from 8x8 to 4x4 improved quality but was too expensive for the desired production path.
    - Per-pixel ENU-frame correction helped slightly in diagnostics but was not the dominant error source and is not part of the production fix.
    - Forced common subpatch ownership proved that the remaining 8-pixel seam came from discontinuous switching between neighboring subpatch polynomials.
    - Bilinear segment interpolation removed the confirmed hard-owner discontinuity while preserving most of the fast-path performance.
- **Diagnostic/Configuration Knobs**:
    - `QUADTREE_PIPELINE_SUBPATCH_SIZE` remains available for subpatch-size sensitivity tests.
    - `QUADTREE_PIPELINE_PROFILE` enables runtime host-side pipeline timing logs.
    - `QUADTREE_TRAVERSAL_PROFILE` is a compile-time symbol for GPU traversal counters; normal builds omit the counter buffer, atomic updates, and hot-loop profiling branches entirely.
    - Removed diagnostic-only paths include forced common-owner selection and per-pixel ENU frame correction buffers.
- **Horizon File Output Modes**: Horizon generation now supports direct compressed tile output (`.cbin`) when requested, using atomic temp-file writes and fallback to uncompressed `.bin` if compression fails for a tile.
- **Bridge Parameter/Skip Semantics**: The bridge now passes caller-supplied observer elevation through to generation and treats both `.bin` and `.cbin` as existing outputs during overwrite checks.
- **Streaming Lightmap Arrays**: `pipeline/streaming/LightmapArrayStreamingBridge.cs` now executes tile processing through the shared `Pipeline<T>` + `PipelineStep<TInput,TOutput>` infrastructure with explicit stage separation:
    - Horizon file parse/read stage.
    - Lightmap array compute/write stage.
- **Parallelism Controls (Wired)**:
    - `LightmapArrayStreamRequest.MaxReadParallelism` controls read-stage `MaxDegreeOfParallelism`.
    - `LightmapArrayStreamRequest.MaxComputeParallelism` controls compute-stage `MaxDegreeOfParallelism`.
    - `ReadyQueueCapacity` is used as bounded pipeline capacity and, together with the free-buffer pool, preserves backpressure semantics.

### 3.6 Analytics & Map Algebra
- **Operational Compute Path (Current)**: Production raster analytics are implemented in Python handler contracts (`backend/jobs/handlers.py`) with `rasterio`/`numpy`/`scipy` and selective native `moonlib.dll` integration for specialized compute (for example horizons/lighting workflows).
- **Future Expansion Track**: Broader Dask/xarray orchestration remains a planned scale-out direction, not the current default runtime path.
- **Map Algebra DSL (Implemented v1)**:
    - Implemented in `backend/jobs/map_algebra.py` with AST-validated expression parsing and bounded complexity limits.
    - Bound to typed implementation `ToolImplementations.raster_calculate` and exposed as MCP/assistant tool `raster.calculate`.
    - Input model supports:
        - Scenario-relative raster files and product-id raster references.
        - Temporal signal bindings (`lighting_raster`, `earth_above_horizon`, `sun_above_horizon`) streamed from lightmap pipeline.
    - Temporal contract:
        - Time range required for temporal signals: `time_start_utc`, `time_stop_utc`, `time_step_hours`.
        - 3D tensors use shape `[time, height, width]`.
        - Reducers `min`, `max`, `avg`, `std` reduce over the temporal axis to produce 2D output.
    - Grid/schema contract:
        - Output grid is defined by the active scenario DEM (CRS, transform, width, height).
        - Raster inputs are reprojected/aligned to DEM grid in v1 when needed.
        - Default resampling is `bilinear` (override: `nearest`, `cubic`).
    - Data type/no-data policy:
        - Output dtype can be explicit or inferred from semantic context; fallback is `float32`.
        - No-data is propagated where dtype supports it (default for non-byte outputs).
        - Byte outputs default to nodata-disabled unless explicitly required by data semantics.
        - For layer-published selection masks, nodata-enabled byte output is opt-in via `publish_layer.transparent_background=true` (writes `NODATA=0` for `uint8` mask outputs).
    - Error taxonomy:
        - Map algebra failures return explicit machine codes (`map_algebra_*`) for parse/validation, input resolution, reprojection, temporal streaming/reducer contract, output path conflicts, cancellation, and internal errors.
    - Additional mask-oriented helpers:
        - `label_regions(mask[, cleanup_mode[, cleanup_iterations]])` returns `int32` connected-component ids (8-connectivity) with optional cleanup modes `none | erosion | opening`.
        - `region_sizes(mask[, cleanup_mode[, cleanup_iterations]])` returns `int32` region-size rasters (each connected component filled with its pixel count).
        - `filter_regions_by_size(mask, threshold, comparator[, cleanup_mode[, cleanup_iterations]])` returns a boolean mask that filters connected regions by size while preserving original region geometry; `comparator` supports `>=` and `<=`.
        - `find_borders(mask)` returns a boolean inner-border mask.
- **Scripted Raster Transform (Implemented v1)**:
    - Implemented in `backend/jobs/raster_transform.py` and bound to typed implementation `ToolImplementations.raster_transform`, with MCP/assistant tool exposure as `raster.transform` and generated jobs route `POST /api/v1/jobs/raster-transform`.
    - Authoring contract supports either a single expression or a restricted multi-statement block that must assign the final raster to `result`.
    - Safe script namespace is intentionally narrow: vectorized arithmetic/comparison/boolean operations plus sealed functions `where`, `slope`, `aspect`, `hillshade`, and temporal reducers `min`, `max`, `avg`, `std`.
    - Compatibility parser behavior accepts `import numpy as np` as a no-op line; `import numpy` (without alias) and non-facade `np.<fn>` calls remain validation errors.
    - Input binding contract supports scenario-relative raster paths and `product_id` references, plus a reserved `times` binding and horizon-derived `temporal_source` bindings (`sun_fraction`, `sun_over_horizon_deg`, `earth_over_horizon_deg`); legacy `signal` bindings remain supported as a temporary compatibility path.
    - Planner/runtime contract:
        - The target grid remains the active scenario DEM grid.
        - The planner estimates working-set size, chooses `full_extent_static`, `full_extent_temporal`, or `tiled_temporal`, and rejects oversized requests with `raster_transform_plan_too_large`.
        - Planning thresholds are configurable under `[backend.raster_transform]`.
        - Output-path conflict policy follows canonical `overwrite_mode` semantics (`ask`, `always`, `never`) with compatibility normalization from legacy `overwrite`.
    - Temporal and validity contract:
        - Temporal arrays use canonical `[time, y, x]` ordering and persisted outputs must reduce back to 2D.
        - Final output validity is computed conservatively from participating bound raster inputs instead of relying on intermediate NumPy semantics.
        - Missing horizon patches are short-circuited to output nodata and counted in lineage/progress metadata instead of failing the whole job.
    - Notebook/script parity surface:
        - `backend.notebook.notebook_helper` now re-exports `Raster`, `GridSpec`, `ExecutionHints`, `raster_let`, `scenario_dem`, `raster_file`, `slope_raster`, `aspect_raster`, `hillshade_raster`, and `write_output_raster`.
        - This allows local script/notebook authoring to mirror the same planner/runtime model used by `ToolImplementations.raster_transform`.
- **Viewshed + Routing Metrics (Implemented hybrid path, ADR 0036)**:
    - Public job/tool surface:
        - `terrain.viewshed` (`ToolImplementations.generate_los_viewshed`, route: `POST /api/v1/jobs/generate-los-viewshed`).
        - `terrain.mask_connectivity_metrics` (`ToolImplementations.analyze_observer_mask_connectivity`).
    - Backend strategy:
        - GDAL (`osgeo.gdal.ViewshedGenerate`) is the baseline backend for single-observer and small-list observer cases.
        - Optional CUDA backend (Numba) is available for larger/denser observer sets; `backend.viewshed.backend_mode = gdal | cuda | auto` controls routing.
        - Auto-routing uses observer count/density/connectivity metrics and emits routing decision metadata (`backend_mode_requested`, `backend_mode_selected`, fallback fields).
        - For high-observer workloads routed to CUDA, runtime failure is treated as terminal (`viewshed_cuda_runtime_failed`) and GDAL fallback is intentionally disabled for that workload class.
    - Shared connectivity metric primitive:
        - Internal helper `ToolImplementations._compute_mask_connectivity_metrics(...)` computes `(component_count, largest_component, adjacency_ratio)` with 8-connectivity and optional routing-mask cleanup (`none | erosion | opening`, iteration count).
    - Lunar curvature/precision policy:
        - Supports parabolic curvature approximation with configurable tolerance (`backend.viewshed.parabolic_error_tolerance_m`) and precision policy wiring under `[backend.viewshed]`.
    - Script/notebook helper parity:
        - `backend.notebook.notebook_helper` exposes `label_regions(mask, cleanup_mode, cleanup_iterations)`, `region_sizes(mask, cleanup_mode, cleanup_iterations)`, `filter_regions_by_size(mask, threshold, comparator, cleanup_mode, cleanup_iterations)`, `find_borders(mask)`, and `compute_mask_connectivity_metrics(mask, cleanup_mode, cleanup_iterations)` for agent-authored Python scripts and notebook jobs.
- **C# Map Algebra**: Native map algebra documentation/code exists under `native/new_horizon/docs`, but the active Lunar Analyst map algebra toolchain is the Python handler/DSL path (`raster.calculate`, `raster.transform`).

### 3.7 Client-Side Raster Colormaps and Styling (ADR 0007, ADR 0008)
- **Design Philosophy**: Colormaps and raster styling (opacity, brightness, contrast) are implemented as a high-performance client-side rendering feature using **OpenLayers WebGLTile** and style expressions. This enables real-time interactive visualization without requiring server-side compute or derivative generation for style changes.
- **Colormap Representation**: Colormaps are defined as JSON objects containing an array of `stops`. Each stop maps a normalized `value` (0.0 to 1.0) to an RGBA color `[R, G, B, A]` (0-255 for RGB, 0-1 for A).
- **Resolution Hierarchy**: Available colormaps are merged from three prioritized levels, allowing for both global defaults and project-specific overrides:
    1. **Built-in**: Hardcoded scientific palettes (e.g., `viridis`, `magma`, `plasma`, `gray`) provided by the backend.
    2. **App-level**: Shared custom colormaps stored in `config/colormaps/map_colormaps.json`.
    3. **Scenario-level**: Project-specific overrides or additions stored in `{scenario_root}/colormaps/map_colormaps.json`.
- **Rendering Pipeline**: The frontend (`rasterStyle.ts`) generates dynamic GPU expressions for the WebGLTile layer that:
    - normalize pixel values from `band 1` using layer-specific `valueMin`/`valueMax` ranges;
    - apply real-time brightness and contrast adjustments through style variables;
    - linearly interpolate colors across the selected colormap stops on the GPU;
    - apply transparency masks for `nodataCutoff` values and `alphaBand` coverage.
- **Future Extension Path**: Per **ADR 0008**, the styling system includes an explicit contract for advanced custom shaders (opt-in via `LayerState.style.shader`), while maintaining the expression-based colormap pipeline as the robust default fallback.
- **Persistence**: Layer-specific colormap selections and style parameters are persisted in the scenario's SQLite database and restored across sessions.

---

## 4. Integration & Workflows

- **Project Lifecycle**: Define Project DEM/Projection -> Preprocess/Align ancillary rasters -> Generate derived layers via handler-backed Python/native compute -> Register outputs in SQLite/SpatiaLite -> Visualize in Web Application/MoonLayers.
- **Data Sharing**: Web application and notebook clients point at the same project directory. Clients use timestamp and size checks to monitor for updates.
- **Raster Transform Lifecycle**: Typed job or assistant/MCP tool call -> parse/validate restricted script -> build planner summary + enforce working-set limits -> align static raster inputs to the scenario DEM grid -> optionally stream horizon-derived temporal inputs -> execute transform -> apply conservative validity mask -> persist GeoTIFF -> register product/file artifact.
- **Viewshed Lifecycle**: Typed job or assistant/MCP tool call -> resolve observer mode (single/list/mask) -> compute observer distribution metrics -> select backend (`gdal`/`cuda`/`auto`) -> execute LOS/viewshed kernel -> merge outputs for multi-observer reducers (`any_visible`/`visibility_count`) -> persist GeoTIFF + register artifact + routing metadata; high-observer CUDA runtime failures return `viewshed_cuda_runtime_failed` rather than falling back to GDAL.
- **Assistant Turn Lifecycle**: User prose request -> assistant turn created -> optional tool proposal -> confirmation gate for mutating actions -> tool/model execution -> event streaming to assistant response panel -> persisted turn/message/tool-call records.
- **Hybrid Turn Lifecycle (Imperative Intents)**: User prose request -> prompt segmentation -> provisional classification (`command`/`create_product`/`other`) with always-on candidate entity/product extraction -> entity resolution + verb operation candidates (per segment) -> unified deterministic recognizer -> deterministic execution for matched segments (or clarification for ambiguous) -> primary-LLM continuation for unmatched (`no_match`) segments with `<DOMAIN_ENTITY_CONTEXT>` injection (including `candidate_product_types`) -> confirmation gate for mutating actions -> persisted turn/tool-call records with execution-origin metadata.
- **Assistant Context Lifecycle**: Full message history remains on disk; compaction creates summary checkpoints to reduce live context footprint while preserving auditability of prior raw messages.
- **Assistant Artifact Lifecycle**: Tool execution may persist or register scenario-local artifacts, assistant messages store typed output manifests for UI rendering, and the response pane resolves those manifests through file-id-backed asset endpoints where available.
- **MCP Lifecycle**: External MCP clients can discover and invoke Lunar Analyst tools through HTTP or stdio transports, using the same underlying tool registry and execution services as the built-in assistant.
- **Unified Tool Identity Lifecycle**: Requests, events, and tool metadata should use canonical `implementation_name` and `job_id`; compatibility aliases are retained only for legacy input interoperability.
- **Moon Trek Lifecycle**: Catalog search -> add overlay -> auto-fit map to product extent (when metadata bbox is available) -> load through feature/WMTS fallback chain -> manage overlay in the same ordering/style workflow as scenario layers.
- **Backend Startup Lifecycle**: Uvicorn readiness is reported without waiting for optional startup maintenance tasks; service warmup, Marimo auto-start, and scenario auto-discovery run asynchronously after startup.
- **Assistant Lazy-Init Lifecycle (ADR 0040)**: Core API routes can serve before assistant providers or RAG indexes are initialized. The first assistant-dependent request performs one-time provider initialization and any required RAG setup under concurrency-safe lazy-init control; initialization failures are cached/logged and returned on assistant paths only.
- **Native/GDAL Import-Order Lifecycle**: Backend startup performs best-effort native bootstrap preflight before broad module imports, and rasterio usage is loaded lazily at runtime to reduce DLL-root conflicts between Python GDAL and moonlib/native paths.
- **Script Runtime Isolation Lifecycle (ADR 0037)**: Script/notebook jobs are launched in explicit runtime modes (`osgeo` vs `moonlib`) with mode-specific environment setup and conflict enforcement to prevent cross-stack GDAL/PROJ contamination.
- **Coordinate Reference Systems**: Primary projection is ESRI:103878 (Lunar South Pole Stereographic). All components respect the Moon Mean Earth fixed frame. GDAL/Proj are used for all coordinate transformations.
- **Map Delivery CRS Contract**: Backend map-facing raster endpoints are responsible for CRS normalization (warp to map CRS where required); clients should not assume arbitrary source-CRS reprojection is reliable for lunar custom CRS definitions.
    - CRS agreement checks use semantic equivalence (including unnamed-but-equivalent lunar stereographic WKTs), not authority-name string matching only.
    - The shared semantic comparator is applied in raster delivery, vector-delivery CRS normalization, map-algebra input alignment checks, and eval raster postcondition assertions.
- **Import Policy Contract**: GeoTIFF imports are converted to tiled/overview COG-style outputs by default for map delivery performance; a diagnostics bypass path keeps native-format copies when explicitly requested.
- **Scenario File Naming Contract**: Canonical naming policy is defined in `docs/ADR.0002.scenario_filesystem_and_catalog.md` (normative rules) and `docs/SCENARIO_FILE_NAMING.md` (concrete templates/examples).
- **Horizon Compression Contract**: When `compress_horizons=true`, generation flows target compressed outputs and perform post-run checks so uncompressed legacy horizon tiles do not silently remain.

### 4.1 Assistant Eval Log Interpretation (Quick Guide)
- **Normative Scoring Spec Reference**: `docs/ASSISTANT_EVAL_SPEC.md` is the authoritative benchmark/scoring/gating contract.
- Treat each case as two checks, in order:
  - Contract check: did the turn use the required tool family (`scenario.write_run_script`, `raster.calculate`, etc.)?
  - Postcondition check: did the expected artifact/state actually exist and validate after execution?
- `mode=tool_call` with `primary_tool=tools.search` is not a pass for functional execution goals unless a subsequent turn in the same test session performs the required execution tool call.
- `turn_status=failed` with no top-level `error` usually means a tool call was made but rejected/failed internally (argument/schema mismatch or runtime failure).
- `Failed cases (hard errors)` indicates execution exceptions bubbled out of tool/runtime layers (for example GDAL nodata dtype errors, missing Python modules).
- `Failed cases (non-success outcomes without hard error)` indicates model/flow outcomes that completed a turn but did not meet success semantics.
- `Failed cases (pytest assertion failures)` maps to deterministic test assertions (missing files, wrong tool family, wrong layer state, wrong output schema).
- Empty-completion warnings from providers (for example Ollama returning completion_tokens=1) are a strong signal for unstable model behavior and often correlate with `tools.search`-only or `respond` fallbacks.
- Scenario overrides (`--scenario ...`) can invalidate fixture assumptions. Missing input assets (for example `nir.tif`) are fixture/precondition failures unless tests synthesize/prepare those assets in setup.
- Current scorer (`backend/evals/assistant/score.py`) includes:
  - weighted component scoring (`routing`, `tool/action`, `execution`, `postcondition`, `safety`);
  - mandatory-fail overrides for safety-critical violations;
  - suite-level scores and blocking suite gates (for example `safety_policy`, `deterministic_intents`, `mixed_turns`, `regression_replay`).

### 4.2 Leaderboard Eval Artifacts and UI
- `backend.evals.assistant.leaderboard` and `backend.evals.assistant.leaderboard_ui` now emit/store richer per-case observability for diagnosis, not just aggregate pass rate.
- Case-level artifacts include model-lineage fields:
  - `requested_model`, `final_model`, `fallback_used`, `model_attempts`, `fallback_chain`.
- Case-level artifacts include Ollama context-window telemetry when applicable:
  - `num_ctx`, `num_ctx_capture_count`, and per-attempt `num_ctx_captures`.
- Case-level artifacts can include full injected RAG text for eval runs (capture is eval-oriented/optional):
  - `rag_context_text`, `rag_context_chars`, `rag_context_capture_count`, `rag_context_captures`.
- UI behavior:
  - Past runs are loaded from timestamped run directories and can be browsed directly in a dedicated Past Runs panel.
  - Selecting a past-run row auto-loads that run (no explicit "Load Selected" action).
  - When a run completes, the run list is refreshed and the completed run is auto-selected so Model Results / Case Results / Case Detail remain synchronized.

---

## 5. Current State & Known Gaps

- **Desktop Viewer**: Legacy/external only; current product investment is in the browser-first React + FastAPI stack.
- **MoonLayers**: Production-ready for notebooks. Lacks HTTP response caching, additional layer protocols (XYZ/WMS), and advanced measurement tools.
- **Assistant/MCP Scope**: Initial assistant and MCP stack is implemented and usable, but some behaviors are intentionally minimal in this iteration:
    - Tool calling is now hybrid (deterministic + model-loop). Deterministic coverage is intentionally narrow and evolves incrementally via action specs.
    - Assistant event delivery is in-memory fan-out polling per backend process (no persistent/brokered WS event bus).
    - External CLI agent integrations remain CLI-version-sensitive; fallback/retry handling now covers common MCP/server/tool-invocation confusion cases, but prompt and provider wiring can still require updates as Codex/Gemini CLIs evolve.
    - File-backed artifact registration now covers assistant-generated previews and scenario-local plot/image outputs, but broader scenario discovery still does not auto-register arbitrary image formats outside assistant/tool flows.
- **Large Local Model Stability Caveat**: Large Ollama models can intermittently fail under local resource pressure (GPU/CPU memory and context-size pressure). Recovery now includes alternate-model fallback, but model-specific reliability remains environment-dependent.
- **Moon Trek Caching Gap**: Trek cache is currently process-memory only (TTL-based). Cached feature payloads are shared across sessions in a running backend but are not persisted across backend restarts.
- **Analytics**: Operational compute is handler-centered and includes deployed map algebra and viewshed tooling (`terrain.viewshed`, `terrain.mask_connectivity_metrics`) with configurable `gdal|cuda|auto` routing. Dask/xarray scale-out remains a future expansion track.
- **Raster and Viewshed Worker Isolation Gap**: The `raster.calculate`, `raster.transform`, and `terrain.viewshed` contracts include GDAL-heavy and, for temporal raster operations, native lightmap-streaming paths. They still execute through the backend handler/runtime module because they depend on backend scenario/catalog state that is not yet fully represented in the worker protocol context. They must not be used as precedent for adding new inline long-running native paths; the intended direction is to move them onto the shared worker protocol once their scenario-state dependencies are made explicit and serializable.
- **New Horizon**: The current fast production path is `QuadTreeHorizonGenerator` with bilinear subpatch segment interpolation, subpatch-relative grid-convergence correction, shared per-job segment-center caching, and DEM-edge clamping for interpolation halos. `ReferenceHorizonGenerator` remains the correctness reference, not a production path. Streaming lightmap array execution uses the shared pipeline framework with request-driven stage parallelism, while broader integration with remaining visual and service surfaces continues.
- **New Horizon Fitting Robustness (Needs Work)**: The chord-fitting path currently uses a temporary degrade-gracefully fallback instead of hard failure in two bad-data conditions in `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs`:
    - First sample out of DEM bounds (around `FitPlanarToChordCubicWithTerrain`, near line 3581) now logs a warning and falls back to linear coefficients (`c1=1, c2=0, c3=0`).
    - Invalid `C1` outside `[0.5, 2]` (near line 3673) now logs a warning and clamps/falls back to linear coefficients.
    - This keeps long runs alive, but it is a mitigation rather than a final fix; root-cause investigation and a scientifically validated fitting strategy are still required.

## 6. Design Principles
- **Performance First**: Optimize interactive web rendering and high-throughput compute kernels; use GPU acceleration where it materially improves scientific workflows.
- **Project-Centric**: Unified filesystem structure for all artifacts.
- **Offline Readiness**: Robust disk caching for tiles and terrain data.
- **Scientific Accuracy**: Double-precision geodetic math for all polar transformations.
- **Tool-Implementation-Centered Compute**: In the FastAPI backend, actual calculation logic belongs in `backend/jobs/handlers.py` method bodies. `ToolImplementations` signatures define the tool contract and generated routes; avoid duplicate parallel compute-contract definitions elsewhere.
- **Worker-Only Handler Discipline**: New long-running native, `.NET`, `pythonnet`, `LightmapStreamingClient`, or GDAL-heavy typed handlers must be declared worker-only and must report progress/cancellation through the shared worker protocol. Do not add inline FastAPI execution for these paths except behind the explicit `LUNAR_ANALYST_NATIVE_INLINE_HANDLERS` debug escape hatch.

## 7. Terminology Note
- The assistant turn artifact is an execution plan, not a search planner. Public contract fields now use execution-plan terminology such as `execution_plan_status`.
- Intent classification labels are `command`, `create_product`, and `other`.
- Candidate extraction and final classification are separate concerns: candidate entities/product-types may be captured even when a segment remains `other`.
- Turn-level usage metadata records the overall handling path as `turn_handling_mode`; per-segment execution state remains `execution_mode` in the execution-plan structures.
