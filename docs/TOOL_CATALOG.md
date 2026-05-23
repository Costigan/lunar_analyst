# Lunar Analyst Tool Catalog

Comprehensive reference for all assistant-facing tools, including arguments, return types, and operational behavior.

## Canonical Terms
- **Tool Contract**: A governed, typed definition of a tool's inputs and outputs.
- **Tool**: A callable capability exposed to the assistant or via MCP.
- **Job**: A specific execution instance of a tool, tracked by a `job_id`.
- **Implementation Name**: The canonical identifier for the underlying code (e.g., `ToolImplementations.raster_calculate`).
- **Tool Name**: The public-facing dotted name (e.g., `raster.calculate`).

---

## 1. Scenario Management

### `scenario.list`
List all known scenarios in the workspace.
- **Arguments**: None.
- **Returns**: `{"items": list[Scenario], "count": int}`.

### `scenario.get`
Retrieve a specific scenario by its ID.
- **Arguments**:
  | Name | Type | Required | Description |
  |---|---|---|---|
  | `scenario_id` | string | Yes | The unique identifier for the scenario. |
- **Returns**: `Scenario` object (JSON).

### `scenario.set_current`
Set the active scenario for the current session. Supports flexible matching by ID, name, or root path.
- **Arguments**:
  | Name | Type | Required | Description |
  |---|---|---|---|
  | `scenario_ref` | string | Yes | Fuzzy match string for scenario ID or name. |
- **Returns**: Selection status and matched scenario metadata.

### `scenario.import_geotiff`
Import an external GeoTIFF into the scenario. By default, converts to Cloud Optimized GeoTIFF (COG).
- **Arguments**:
  | Name | Type | Required | Default | Description |
  |---|---|---|---|---|
  | `scenario_id` | string | Yes | - | Target scenario ID. |
  | `source_path` | string | Yes | - | Absolute local path to source GeoTIFF. |
  | `kind` | string | No | "imports" | Product kind folder. |
  | `subkind` | string | No | "geotiff" | Product subkind. |
  | `bypass_cog` | boolean | No | False | If true, skips COG conversion. |
- **Returns**: Imported `Product` metadata.
- **Confirmation**: Required (`import_file`).

### `scenario.move_path`
Move or rename a file or directory within the scenario root.
- **Arguments**:
  | Name | Type | Required | Description |
  |---|---|---|---|
  | `scenario_id` | string | Yes | Scenario ID. |
  | `source_relative_path` | string | Yes | Path relative to scenario root. |
  | `target_relative_path` | string | Yes | New path relative to scenario root. |
- **Returns**: Move operation status.
- **Confirmation**: Required (`move_path`).

---

## 2. Scripting & Notebooks

### `scenario.list_scripts`
List runnable Python scripts (`.py`) within a scenario.
- **Arguments**: `scenario_id` (string).
- **Returns**: List of script entries with relative paths.

### `scenario.list_notebooks`
List runnable Marimo notebooks within a scenario.
- **Arguments**: `scenario_id` (string).
- **Returns**: List of notebook entries.

### `scenario.run_script` / `scenario.run_marimo_notebook`
Execute a script or notebook as a background job.
- **Arguments**:
  | Name | Type | Required | Description |
  |---|---|---|---|
  | `scenario_id` | string | Yes | Scenario ID. |
  | `relative_path` | string | Yes | Path to script/notebook relative to scenario root. |
- **Returns**: `job_id` and initial status.
- **Confirmation**: Required (`launch_job`).

### `scenario.write_script`
Create or update a Python script in the scenario.
- **Arguments**:
  | Name | Type | Required | Default | Description |
  |---|---|---|---|---|
  | `scenario_id` | string | Yes | - | Scenario ID. |
  | `relative_path` | string | Yes | - | Target path (must end in `.py`). |
  | `content` | string | Yes | - | Python source code. |
  | `overwrite` | boolean | No | False | Allow overwriting existing file. |
- **Returns**: Write status and byte count.
- **Confirmation**: Conditional (`write_notebook`).

### `scenario.write_run_script`
Convenience tool to write a script and execute it immediately in one turn.
- **Arguments**: Same as `scenario.write_script`.
- **Returns**: Combined results of write and launch.
- **Confirmation**: Required (`launch_job`).

### `scenario.revoke_script_overwrite`
Revoke session-scoped overwrite approval for a script. This is used to reset the "Always Allow" state for specific script writes.
- **Arguments**:
  | Name | Type | Required | Description |
  |---|---|---|---|
  | `scenario_id` | string | Yes | Scenario ID. |
  | `relative_path` | string | Yes | Path to script. |
- **Returns**: Revocation status.

---

## 3. Product & Layer Inspection

### `product.list`
List all cataloged products for a scenario.
- **Arguments**: `scenario_id` (string).
- **Returns**: List of `Product` objects.

### `product.files`
List all files associated with a specific product ID.
- **Arguments**: `product_id` (string).
- **Returns**: List of `FileRecord` objects.

### `layer.list_visible`
List layers currently active/visible in the map view.
- **Arguments**:
  | Name | Type | Required | Description |
  |---|---|---|---|
  | `scenario_id` | string | Yes | Scenario ID. |
  | `base_layer_visible`| boolean | No | Include Moon Trek base layer. |
- **Returns**: List of visible layer states.

### `layer.update_state`
Modify layer properties like visibility, opacity, or style.
- **Arguments**:
  | Name | Type | Required | Description |
  |---|---|---|---|
  | `layer_id` | string | Yes | ID of the layer to update. |
  | `visible` | boolean | No | Visibility toggle. |
  | `opacity` | float | No | Opacity (0.0 to 1.0). |
  | `z_index` | integer | No | Render order. |
- **Returns**: Updated layer state.
- **Confirmation**: Required (`update_layer_state`).

---

## 4. Artifact Analysis (GeoTIFF, CSV, Plots)

### `artifact.describe_geotiff`
Get metadata for a GeoTIFF (dimensions, CRS, bands, nodata, statistics).
- **Arguments**: (Provide one) `file_id`, `path`, or `scenario_id` + `relative_path`.
- **Returns**: Detailed metadata JSON.

### `artifact.preview_geotiff`
Generate a low-resolution PNG preview of a GeoTIFF for UI display.
- **Arguments**: Same as `describe_geotiff`.
- **Returns**: Artifact reference to the generated PNG.

### `artifact.stats_geotiff`
Compute exact raster statistics for all bands.
- **Arguments**: Same as `describe_geotiff`.
- **Returns**: Min, max, mean, std, and valid pixel counts per band.

### `artifact.describe_table`
Inspect a CSV/tabular file and return a preview of rows.
- **Arguments**: Same as `describe_geotiff`.
- **Returns**: Column list and row sample.

### `artifact.describe_plot`
Describe an image or plot artifact.
- **Arguments**: Same as `describe_geotiff`.
- **Returns**: Image metadata and render reference.

---

## 5. Job & Run Control

### `jobs.list_predefined`
List predefined scenario-independent jobs (native tools).
- **Arguments**: None.
- **Returns**: List of `JobDefinition` objects.

### `jobs.run_predefined`
Run a predefined job by its implementation name.
- **Arguments**:
  | Name | Type | Required | Description |
  |---|---|---|---|
  | `implementation_name`| string | Yes | Canonical identifier (e.g., `ToolImplementations.generate_horizons`). |
  | `params` | object | Yes | Key-value arguments for the job. |
- **Returns**: `job_id` and initial status.
- **Confirmation**: Required (`launch_job`).

### `runs.get_status`
Check the execution status of a background job.
- **Arguments**: `job_id` (or `run_id`).
- **Returns**: Status (queued, running, completed, failed, cancelled) and metadata.

### `runs.get_logs`
Retrieve stdout/stderr logs for a job.
- **Arguments**:
  | Name | Type | Required | Default | Description |
  |---|---|---|---|---|
  | `job_id` | string | Yes | - | Job ID. |
  | `head_lines` | integer | No | 40 | Number of lines from start. |
  | `tail_lines` | integer | No | 80 | Number of lines from end. |
  | `stream` | enum | No | "stdout" | "stdout", "stderr", or "combined". |
- **Returns**: Log slices and line counts.

### `runs.cancel` / `job.cancel`
Stop a running job.
- **Arguments**: `job_id`.
- **Returns**: Final status after cancellation request.
- **Confirmation**: Required (`launch_job`).

---

## 6. Raster Analysis Tools

### `raster.calculate`
Evaluate a Map Algebra expression using a restricted DSL. Supports temporal signals (lighting, horizons).
- **Arguments**:
  | Name | Type | Required | Default | Description |
  |---|---|---|---|---|
  | `scenario_id` | string | Yes | - | Target scenario. |
  | `expression` | string | Yes | - | Map Algebra expression (e.g., `slope(dem) > 15`). |
  | `inputs` | object | Yes | - | Map of variable names to `RasterInputReference`. |
  | `output_relative_path`| string | No | - | Destination GeoTIFF path. |
  | `overwrite_mode` | enum | No | `ask` | Output conflict behavior: `ask`, `always`, `never`. |
  | `publish_layer` | object | No | - | Optional one-call layer publish options (`enabled`, `title`, `visible`, `opacity`, `z_index`, `style`, `on_existing`, `transparent_background`). |
  | `time_start_utc` | string | No | - | Start time for temporal signals. |
  | `time_stop_utc` | string | No | - | Stop time for temporal signals. |
  | `time_step_hours` | float | No | - | Time step for streaming. |
- **Returns**: `RasterCalculateResult` with output path and product details.
- **Behavior Notes**:
  - For selection/highlight masks, set `publish_layer.transparent_background=true` to publish `uint8` mask outputs with `NODATA=0` (non-selected pixels transparent).
  - If `publish_layer.transparent_background` is omitted/false, binary mask outputs remain `0/1` with no nodata by default.
- **Confirmation**: Required (`launch_job`).

### `raster.transform`
Execute a multi-statement raster transform script.
- **Arguments**: Similar to `raster.calculate`, but uses `script` instead of `expression`.
- **Returns**: `RasterTransformResult`.
- **Confirmation**: Required (`launch_job`).

### `generate_horizons`
High-performance native horizon generation for a DEM.
- **Arguments**:
  | Name | Type | Required | Default | Description |
  |---|---|---|---|---|
  | `scenario_id` | string | Yes | - | Scenario ID. |
  | `dem_path` | string | Yes | - | Path to source DEM. |
  | `horizons_dir` | string | Yes | - | Destination directory for horizon tiles. |
  | `overwrite_horizons`| boolean | No | False | Force regeneration. |
  | `compress_horizons` | boolean | No | True | Use `.cbin` compressed format. |
- **Returns**: `GenerateHorizonsResult`.
- **Confirmation**: Required (`launch_job`).

### `assistant.rag_ingest`
Ingest documentation or scenario-relative text files into the assistant's retrieval index.
- **Arguments**:
  | Name | Type | Required | Default | Description |
  |---|---|---|---|---|
  | `scenario_id` | string | No | "global"| Scenario ID. |
  | `relative_root` | string | No | "" | Sub-folder to scan. |
  | `rebuild` | boolean | No | False | Wipe index before ingest. |
  | `extensions` | array | No | None | File extensions (e.g., `[".md", ".txt"]`). |
- **Returns**: Ingest statistics (scanned, added, updated, deleted).
- **Confirmation**: Required (`launch_job`).

---

## 7. Tool Discovery

### `tools.search`
Search tool contracts by keyword and return matching tool names/titles.
- **Arguments**:
  | Name | Type | Required | Description |
  |---|---|---|---|
  | `keywords` | string or array | Yes | Keyword query used to find relevant tools. |
- **Returns**: Matching tool descriptors for focused discovery.

### `tools.describe`
Return full contract/schema details for specific tools by exact name.
- **Arguments**:
  | Name | Type | Required | Description |
  |---|---|---|---|
  | `tool_names` | array | Yes | Exact tool names (e.g., `raster.calculate`). |
- **Returns**: Full tool contract payloads including arguments and types.

---

## 8. Capabilities & Help

### `capabilities.describe`
Returns a comprehensive text description of what Lunar Analyst can do and a list of all available tool names.
- **Arguments**: None.
- **Returns**: `{"text": string, "tool_names": list[string]}`.

---

## Related Documentation
- `docs/AGENT_PROMPT.md`: Guidance on how the assistant should select and use these tools.
- `docs/DESIGN.md`: High-level system architecture.
- `docs/ADR.0019.unified_tool_model.md`: Technical details on the tool registration system.
