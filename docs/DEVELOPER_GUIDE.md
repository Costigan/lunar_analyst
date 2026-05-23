# Lunar Analyst Developer Guide

Welcome to the Lunar Analyst developer guide. This document provides the essential information needed to set up, develop, and test the Lunar Analyst toolkit.

---

## 1. Quick Start

Bootstrap the repo-managed environment first:

1.  **Linux bootstrap:**
    ```bash
    ./scripts/bootstrap.sh
    ```

After bootstrap completes, these commands will get you running:

1.  **Start the Backend (API + Worker, host-native Linux baseline):**
    ```bash
    ./scripts/run-host-dev.sh
    ```
2.  **Start the Frontend (Vite Dev Server):**
    ```bash
    cd backend/web/lunar_analyst
    npm run dev
    ```
3.  **Run All Tests:**
    Follow the "Run Everything" sequence in `docs/RUNNING_TESTS.md`.

---

## 2. Tech Stack

Lunar Analyst is built with a multi-process, multi-language architecture:

-   **Backend Control Plane:** Python 3.11 + FastAPI.
-   **Heavy Compute:** Python Worker + `pythonnet` + .NET 9.0 (`moonlib.dll`).
-   **Native Engines:** C# / CUDA / ILGPU for high-performance terrain analysis.
-   **Web Client:** React + Vite + OpenLayers + Blueprint JS 6.
-   **Notebook Integration:** Marimo + `moonlayers_pkg` (anywidget).
-   **AI Assistant:** FastAPI-integrated LLM orchestration with MCP (Model Context Protocol) support.

---

## 3. Environment Setup

### 3.1 Python
-   **Version:** Python 3.11.
-   **Primary Environment:** repo-managed `.venv` created by `./scripts/bootstrap.sh`.
-   **Linux Baseline:** Host-native Pop!_OS with a normal repo checkout and a separate configured `workspace_root`.
-   **GDAL/OSGeo:** `bootstrap.sh` installs Python `GDAL` to match the system `libgdal` reported by `gdal-config`; no separate GDAL-only environment is required for normal development.
-   **Canonical manifests:** `requirements.in` and `requirements.txt` at the repo root.
-   **Generation workflow:** edit `requirements.in`, then regenerate `requirements.txt` with `./scripts/compile_requirements.sh` (`--force` to bypass the up-to-date check). `bootstrap.sh` installs from the checked-in `requirements.txt` by default, regenerates only if it is missing, and supports `--refresh-requirements` for an explicit refresh.
-   **Bootstrap script:** `scripts/bootstrap.sh`.
-   **Verification:** `scripts/verify_env.py`.
-   **spaCy:** Required. The managed setup installs the `en_core_web_sm` model; prompt segmentation is not supported without it.

### 3.2 Frontend (Node.js)
-   **Tooling:** NPM 10+.
-   **Directories:**
    -   `backend/web/lunar_analyst/`: Main React application.
    -   `moonlayers_pkg/`: OpenLayers widget for notebooks.

### 3.3 Native Backend (.NET)
-   **Version:** .NET 9.0 SDK.
-   **Assemblies:** Native code is compiled into `moonlib.dll` located in `native/new_horizon/`.

---

## 4. Architecture & Core Concepts

### 4.1 Process Model
Lunar Analyst uses a four-process topology to ensure reliability and isolation:
1.  **FastAPI Service:** Authoritative control plane (REST/WS).
2.  **Compute Worker:** Separate Python process for heavy compute (hosts `moonlib`).
3.  **Marimo:** Exploratory notebook process.
4.  **Browser/Tauri Client:** User interface.

### 4.2 Scenario Filesystem
Each analysis is encapsulated in a **Scenario**:
-   **Root:** `{workspace_root}/{scenario_slug}/`
-   **Database:** `scenario.db` (SQLite/SpatiaLite).
-   **Primary DEM:** `dem.tif` (fixed name).
-   **Display Cache:** `display/` (auto-managed derivatives).
-   **Assistant State:** `{workspace_root}/.assistant/`

### 4.3 Job System
Jobs are defined as typed handlers in `backend/jobs/handlers.py`. 
-   **Registration:** Decorated with `@contract`.
-   **Execution:** Dispatched via `JobService` to the Worker process.
-   **Progress:** Streamed over WebSockets (`/api/v1/events`).

### 4.4 Assistant & MCP
The built-in AI assistant can execute tools, describe artifacts, and manage scenarios.
-   **Tools:** Defined in `backend/services/assistant/tool_registry.py`.
-   **MCP Gateway:** Exposes Lunar Analyst capabilities to external agents via HTTP/SSE or stdio.

---

## 5. Development Workflows

### 5.1 Adding a New Tool
1.  Define the `Result` model in `backend/jobs/handlers.py`.
2.  Implement the method in `ToolImplementations` (or `JobHandlers` compatibility alias) with `@contract` decorator.
3.  Update the UI `Tools` panel to include the new tool definition.

### 5.2 Adding an Assistant Tool
1.  Implement the tool logic in a service (e.g., `ScenarioService`).
2.  Register the tool in `backend/services/assistant/tool_registry.py`.
3.  Add a confirmation policy if the tool is mutating.

### 5.3 Adding a Canonical Create-Product Recipe
Use this workflow when you want `create_product` segments to map to deterministic recipe execution (ADR 0042), while keeping segment classification ownership in ADR 0043.

1.  Add or validate the product type in `backend/services/assistant/product_type_dictionary.py`.
    - Ensure the `ProductTypeSpec` exists for your `product_type`.
    - For deterministic recipe routing, set:
      - `canonical_recipe_ids`
      - `reuse_keys` (if non-default)
      - `required_parameters` (if needed)
    - Keep `precursor_requirements` accurate; execution-plan metadata uses this for prerequisite counts.

2.  Add the recipe template in `backend/services/assistant/canonical_recipe_catalog.py`.
    - Add a `RecipeTemplateSpec(...)` entry with:
      - unique `recipe_id`
      - `product_type` matching dictionary label
      - `requires` prerequisites (for example `("dem",)` or `("source_raster",)`)
      - `execution_ref` (currently deterministic path expects `raster.calculate`)
      - `expression_template`
      - `default_output_relative_path`
      - optional `required_parameters`
      - optional `reuse_keys`
    - Keep recipe templates thin: they map intent to governed tool calls; they do not redefine tool schemas.

3.  Wire planner behavior in `backend/services/assistant/create_product_planner.py`.
    - Confirm `select_recipe_for_classification(...)` can select your recipe for the requested `product_type`.
    - Extend `expand_prerequisites(...)` if your `requires` introduces new prerequisite semantics.
    - Extend `resolve_required_parameters(...)` if your recipe needs additional extracted parameters.
    - Extend `compile_recipe_step_to_tool_call(...)` if argument mapping differs from existing patterns.
    - Reuse/fallback behavior:
      - keep reuse conservative (`compute_reuse_key_fingerprint(...)`, `find_reusable_product(...)`);
      - emit structured blocks via `build_structured_block(...)` with machine reason codes.

4.  Ensure assistant execution and metadata remain coherent.
    - `backend/services/assistant/assistant_service.py` executes recipe steps from `CreateProductPlan.steps`.
    - Execution-plan metadata should include `requested_product_type`, `selected_recipe_id`, and `prerequisite_count`.
    - Turn-state merge should surface `recipe_summary` / `prerequisite_outcomes` where available.

5.  Add fixture and test coverage (required).
    - Fixtures:
      - `backend/tests/fixtures/assistant_segmentation_classification/golden_cases_v2.jsonl`
      - `backend/tests/fixtures/assistant_segmentation_classification/non_command_extraction_cases.jsonl`
    - Unit tests:
      - `backend/tests/worker/test_canonical_recipe_catalog.py`
      - `backend/tests/worker/test_create_product_planner.py`
      - `backend/tests/worker/test_product_type_dictionary.py`
    - Integration metadata tests:
      - `backend/tests/worker/test_assistant_hybrid_metadata.py`
      - `backend/tests/worker/test_turn_execution_plan.py`
    - Segmentation/classification fixture checks:
      - `backend/tests/worker/test_segmentation_classification_golden.py`
      - `backend/tests/worker/test_segmentation_non_command_extraction_fixtures.py`

6.  Run verification before merge.
    ```bash
    .venv/bin/python -m pytest backend/tests/worker -q
    .venv/bin/python -m pytest backend/tests/contract -q
    ```

7.  Rollout and rollback rules.
    - Feature flag: `backend.llm.create_product_recipe_catalog_enabled` (default `false` in repo configs).
    - To enable deterministic recipe routing in an environment, set this flag to `true`.
    - Rollback path is explicit: set flag back to `false` to disable recipe-catalog execution without changing ADR 0043 classification behavior.

### 5.4 Modifying Native Code
1.  Edit C# code in `native/new_horizon/`.
2.  Build the project: `dotnet build native/new_horizon/`.
3.  Restart the backend to reload the native bridge.

### 5.5 Dev Container Workflow

Container-based development is a supported parity workflow, not the primary edit loop.

For the default host-native Linux loop, use `./scripts/run-host-dev.sh`.

- Build the base and dev images: `./scripts/docker-build.sh`
- Start the bind-mounted dev container: `./scripts/docker-run-dev.sh`
- `docker-run-dev.sh` drops you into an interactive shell inside a one-off dev container; look for a prompt like `lunar@<container-id>:/workspace/lunar_analyst$`
- Run the container smoke checks: `./scripts/docker-smoke.sh`
- Or, from inside the container shell, run `/usr/local/bin/docker-smoke.sh`
- Tear down the compose-managed resources: `./scripts/docker-down.sh`
- The dev container uses:
  - repo checkout: `/workspace/lunar_analyst`
  - mounted workspace root: `/var/lib/lunar-analyst/workspace`
  - config: `config/lunar_analyst.devcontainer.toml`

Use host-native Pop!_OS for the default loop, and use the dev container to validate Ubuntu/container-runtime parity.

### 5.6 Runtime Container Workflow

Phase C adds the immutable runtime image used for local production-style validation.

- Build all images: `./scripts/docker-build.sh`
- Run the runtime image: `./scripts/docker-run-runtime.sh`
- Run the runtime smoke flow: `./scripts/docker-runtime-smoke.sh`
- Runtime config in-image: `/opt/lunar-analyst/config/lunar_analyst.container.toml`
- Runtime writable state: `/var/lib/lunar-analyst/workspace`
- The runtime image does not mount the repo checkout

Use this mode for:

- release-style image validation
- restart/persistence checks
- NRP deployment parity checks

The runtime smoke path verifies:

- API and built-frontend startup
- scenario create/list/rediscovery behavior
- persistence of `scenario_catalog.db`
- persistence of `.assistant/rag/global_rag.db`
- one representative raster job writing under the scenario root

### 5.7 NRP Deployment Assets

First-slice NRP manifests live under `deploy/nrp/`.

Current Phase C deployment assumptions:

- exactly one replica
- `Recreate` rollout strategy to avoid overlapping writers
- one PVC mounted as `/var/lib/lunar-analyst/workspace`
- runtime config mounted from `ConfigMap`
- secrets injected by env refs, not written into workspace storage

Use `deploy/nrp/namespace-notes.md` for the apply order and required operator edits.

### 5.8 Prompt Segmentation & Planning Scripts

Use these scripts to validate segmentation/classification behavior and deterministic create-product planning.

Prerequisite:
```bash
.venv/bin/python --version
```

#### `show_prompt_segmentation.py`

Default input:
- `scripts/sample_prompts_for_segmentation.txt` (one prompt per non-comment line)

Run:
```bash
.venv/bin/python scripts/show_prompt_segmentation.py
```

Useful variants:
```bash
# JSON report output
.venv/bin/python scripts/show_prompt_segmentation.py --json

# Use a different prompt file
.venv/bin/python scripts/show_prompt_segmentation.py /path/to/prompts.txt

# Provide scenario context
.venv/bin/python scripts/show_prompt_segmentation.py --scenario-id scn_test_scenario

# Use Ollama-backed non-command extraction
.venv/bin/python scripts/show_prompt_segmentation.py --use-ollama
```

#### `show_prompt_plans.py`

Default input:
- `scripts/sample_prompts_for_planning.json`

Input formats:
- `.json`: list of objects with `prompt`, `required_files_before`, `delete_files_before`, `required_files_after`
- non-`.json` (for example `.txt`): one prompt per non-comment line

Run:
```bash
.venv/bin/python scripts/show_prompt_plans.py
```

Useful variants:
```bash
# Plan-only explicit mode (no execution)
.venv/bin/python scripts/show_prompt_plans.py --execution-mode none

# Execute with direct backend-service calls
.venv/bin/python scripts/show_prompt_plans.py --execution-mode direct

# Execute through FastAPI tool endpoints
.venv/bin/python scripts/show_prompt_plans.py --execution-mode api --api-base-url http://127.0.0.1:8000

# Emit JSON output
.venv/bin/python scripts/show_prompt_plans.py --json

# Use a specific scenario directory
.venv/bin/python scripts/show_prompt_plans.py --scenario-dir /e/lunar_analyst_scenarios/test_scenario/

# Use a different input file (.json or .txt)
.venv/bin/python scripts/show_prompt_plans.py /path/to/prompts.json
```

Notes:
- In JSON input mode, prompts are skipped when any `required_files_before` path is missing.
- `delete_files_before` entries outside `--scenario-dir` are refused.
- Missing `required_files_after` paths are reported as "required file not created."
- Default mode is planning-only (`--execution-mode none`).

---

## 6. Testing

We maintain a rigorous testing policy. Always verify your changes before committing.

### 6.1 Backend (Python)
```bash
.venv/bin/python -m pytest backend/tests -q
```

### 6.2 Frontend (Vitest)
```bash
npm run test
```

### 6.3 Native (.NET)
```bash
dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj
```

### 6.4 Contract Tests
Essential when modifying API or Job signatures:
```bash
.venv/bin/python -m backend.tools.export_openapi
.venv/bin/python -m backend.tools.export_contract_schemas
.venv/bin/python -m pytest backend/tests/contract -q
```

---

## 7. Standards & Conventions

-   **CRS:** The authoritative projection is **ESRI:103878** (Lunar South Pole Stereographic).
-   **Timestamps:** UTC only, formatted as `YYYY-MM-DDTHH-MM-SS` (omit `Z`).
-   **File Naming:** See `docs/SCENARIO_FILE_NAMING.md` for deterministic templates.
-   **Paths:** Always use scenario-root-relative paths in databases and APIs.

---

## 8. Reference Documentation

For deeper dives, consult the following:
-   **System Design:** `docs/DESIGN.md`
-   **Developer Setup:** `docs/DEVELOPER_SETUP.md`
-   **Process Model ADR:** `docs/ADR.0001.process_model.md`
-   **Scenario Conventions ADR:** `docs/ADR.0002.scenario_filesystem_and_catalog.md`
-   **API Contract:** `docs/API_CONTRACT.md`
-   **User Guide:** `docs/USER_GUIDE.md`
