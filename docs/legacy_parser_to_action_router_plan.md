# Legacy Parser to Action Router Migration Plan

## Goal
Move deterministic command cases from the legacy parser (`_plan_tool_call`) into the data-driven action router, disable legacy parser execution, and keep parser code in place temporarily for rollback.

## Scope
- Assistant deterministic routing only.
- No removal of legacy parser code.
- Keep model-tool-loop behavior unchanged for unmatched prompts.

## Plan
1. Inventory legacy parser cases and group by intent family.
2. Add `ActionSpec` entries for legacy deterministic cases in `command_router.py`.
3. Extend slot normalization for argument extraction (JSON params, quoted paths, optional flags).
4. Disable legacy parser execution by config + dependency wiring (`legacy_parser_enabled=false`).
5. Keep parser methods in code for temporary compatibility.
6. Add/update tests for migrated router actions.
7. Verify with worker tests.

## Execution Notes
- Migrated into router:
  - `capabilities.describe`
  - `jobs.list_predefined`
  - `jobs.run_predefined`
  - `job.launch`
  - `scenario.switch`
  - `scenario.list`
  - `scenario.list_scripts`
  - `scenario.list_notebooks`
  - `scenario.run_script`
  - `scenario.run_marimo_notebook`
  - `runs.get_logs`
  - `runs.get_status`
  - `runs.cancel`
  - `scenario.revoke_script_overwrite`
  - `product.list`
  - `product.files`
  - `scenario.import_geotiff`
  - `scenario.move_path`
  - `layer.set_state_by_id`
  - existing `layer.set_visible_by_name`
  - existing `layer.list_visible`
- Disabled legacy parser in app config and dependency wiring.
- Parser code retained but no longer used unless explicitly re-enabled.
