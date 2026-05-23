# Debugging Steps: Phase 4.6 Test Sequence

This file captures the exact command sequence we used to validate Phase 4.6 and related contract tests.
Run these commands in order, one at a time, using the repo root (`D:\projects\lunar_analyst`).

Notes:
- Use Python from `D:\projects\env_311\Scripts\activate.bat`.
- Use unique `--basetemp` directories per run to reduce temp-directory collisions.
- In this environment, failures observed were real test/code issues (not the prior global temp permission error).

## Step 1: Verify Python environment

```powershell
cmd /c "D:\projects\env_311\Scripts\activate.bat && python --version"
```

## Step 2a: Verify working directory

```powershell
Get-Location
```

## Step 2b: Check repo status

```powershell
git status --short
```

## Step 3: Compile sanity check

```powershell
cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m compileall backend"
```

## Step 4: OpenAPI contract smoke test

```powershell
cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/contract/test_openapi_contract.py -q --basetemp D:\projects\lunar_analyst\.pytest_step_1"
```

## Step 5: Phase 4.6 tests

```powershell
cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/contract/test_phase4_6_path_first_identity.py -q --basetemp D:\projects\lunar_analyst\.pytest_step_2"
```

Observed in latest run:
- `test_reconcile_detects_filesystem_rename_as_remove_and_add` failed.
- `test_move_path_rolls_back_on_persist_failure` failed.

## Step 6: Phase 4.5 scenario ingest tests

```powershell
cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/contract/test_phase4_5_scenario_toml_ingest.py -q --basetemp D:\projects\lunar_analyst\.pytest_step_3"
```

Observed in latest run:
- `test_discover_rebuilds_missing_scenario_db_from_filesystem` failed with file-in-use (`WinError 32` unlink `scenario.db`).

## Step 7: Combined gate

```powershell
cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m pytest backend/tests/contract/test_phase4_6_path_first_identity.py backend/tests/contract/test_phase4_5_scenario_toml_ingest.py backend/tests/contract/test_openapi_contract.py -q --basetemp D:\projects\lunar_analyst\.pytest_step_4"
```

This final combined command should be run after Steps 5 and 6 so failures are easier to interpret.
