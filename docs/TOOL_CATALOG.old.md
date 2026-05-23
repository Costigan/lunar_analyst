# Lunar Analyst Tool Catalog (Trimmed)

Concise reference for current tool names and intent.
For operating behavior, safety workflow, and selection policy, use `docs/AGENT_PROMPT.md`.

## Canonical Terms
- `tool contract`: governed typed definition
- `tool`: callable capability
- `job`: one execution of a tool
- `helper`: local convenience API

## Tool List (Current Surface)

### Public Tools
- `capabilities.describe`
- `scenario.list`
- `scenario.get`
- `scenario.set_current`
- `scenario.list_scripts`
- `scenario.list_notebooks`
- `scenario.run_script` (confirmation required)
- `scenario.run_marimo_notebook` (confirmation required)
- `scenario.write_script` (conditional confirmation on overwrite)
- `scenario.write_run_script` (confirmation required)
- `scenario.revoke_script_overwrite`
- `scenario.import_geotiff` (confirmation required)
- `scenario.move_path` (confirmation required)
- `product.list`
- `product.files`
- `layer.list_visible`
- `layer.update_state` (confirmation required)
- `artifact.describe_geotiff`
- `artifact.preview_geotiff`
- `artifact.stats_geotiff`
- `artifact.describe_table`
- `artifact.describe_plot`
- `raster.calculate` (confirmation required)
- `raster.transform` (confirmation required)

### System / Compatibility Tools
- `jobs.list_predefined`
- `jobs.run_predefined` (confirmation required)
- `runs.get_status`
- `runs.get_logs`
- `runs.cancel` (confirmation required)
- `job.launch` (confirmation required)
- `job.cancel` (confirmation required)

## Naming and Compatibility
- Canonical runtime identifiers: `implementation_name`, `job_id`
- Compatibility aliases still accepted: `handler_name`, `run_id`

## Implementation Note
- Typed tool implementations are currently in `backend/jobs/handlers.py` (transitional location under the unified tool model).

## Related Docs
- `docs/AGENT_PROMPT.md`
- `docs/ADR.0019.unified_tool_model.md`
- `docs/DESIGN.md`
