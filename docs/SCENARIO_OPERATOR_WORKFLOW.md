# Scenario Operator Workflow

Status: Active
Date: 2026-02-17
Related:
- `docs/PHASE4_5_HORIZON_SHARED_STORE_PLAN.md`
- `config/lunar_analyst.toml`

## 1. Purpose
Operational steps for creating and removing scenarios using the `scenario.toml` discovery path.

## 2. Prerequisites
- Know the effective `workspace_root` (from `LUNAR_ANALYST_WORKSPACE_ROOT` or `config/lunar_analyst.toml`).
- Scenario directory name must match slug pattern: `^[a-z0-9][a-z0-9_-]{2,31}$`.

## 3. Create a Scenario (Manual Discovery)

1. Create a directory under `workspace_root`, for example:
   - `{workspace_root}/test_scenario/`
2. Add `{workspace_root}/test_scenario/scenario.toml`:

```toml
schema_version = 1

[dem]
primary_path = "D:/data/dem/main.tif"
surrounding_paths = ["D:/data/dem/a.tif", "D:/data/dem/b.tif"]

[time_interval]
start_utc = "2026-03-01T00:00:00Z"
stop_utc = "2026-03-10T00:00:00Z"
time_step_hours = 1
```

3. Trigger discovery:
   - `POST /api/v1/scenarios:discover`
4. Verify:
   - `GET /api/v1/scenarios`
   - `GET /api/v1/scenarios/discovery-status`

Notes:
- `primary_path` may be absolute or relative to the scenario directory.
- `surrounding_paths` are validated and stored as references; they are not copied.
- Timestamps are UTC; trailing `Z` is optional, but if timezone suffix is present it must be `Z`.

## 4. DEM Canonicalization Behavior

During ingest:
- If `dem.primary_path` is outside scenario directory: copy to `dem.tif`.
- If `dem.primary_path` is inside scenario directory but not named `dem.tif`: rename/move to `dem.tif`.
- If already `dem.tif`: no file move/copy.

## 5. Startup Auto-Discovery

In `config/lunar_analyst.toml`:

```toml
[backend.scenario_discovery]
auto_discover_on_startup = true
reconcile_missing_on_startup = false
```

Behavior:
- `auto_discover_on_startup = true`: runs the same flow as `POST /api/v1/scenarios:discover` at startup.
- `reconcile_missing_on_startup = true`: additionally forgets scenarios still cataloged whose directories are missing.

## 6. Forgetting Scenarios (No Filesystem Delete)

Use:
- `DELETE /api/v1/scenarios/{scenario_id}`

Behavior:
- Removes scenario from catalog/in-memory state.
- Does not delete scenario directory on disk.
- Does not GC shared horizons.

Operational implication:
- If the directory and `scenario.toml` still exist, discovery can add the scenario back later.

## 7. Auto-Forget of Missing Scenarios

If `reconcile_missing_on_startup = true` and `auto_discover_on_startup = true`:
- startup discovery will forget catalog scenarios whose directories are missing from disk.
- This is catalog reconciliation, not filesystem cleanup.
