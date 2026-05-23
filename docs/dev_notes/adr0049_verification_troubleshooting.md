# ADR.0049 Verification Troubleshooting (Linux)

This note documents common failures when running `scripts/run_local_verification.sh`.

## Contract Drift Check Fails

Symptom:
- `scripts/check_contract_drift.sh` exits non-zero and reports diff under `docs/contracts/generated/v1`.

Action:
1. Re-run:
   - `.venv/bin/python -m backend.tools.export_openapi`
   - `.venv/bin/python -m backend.tools.export_contract_schemas`
2. Commit updated generated files under `docs/contracts/generated/v1`.

## Native Horizon Tests Fail with GDAL/PInvoke Errors

Symptom:
- `dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal`
  fails with `DllNotFoundException` for `gdal_wrap`.

Action:
1. Run:
   - `dotnet restore native/new_horizon/new_horizon.sln`
   - `dotnet build native/new_horizon/new_horizon.sln -v minimal`
2. Confirm outputs use Linux RID paths (`linux-x64`) and required native runtime files exist.
3. Re-run the test command.

## Python Contract/Worker/Integration Suite Hangs

Symptom:
- pytest appears stalled for a long period.

Action:
1. Re-run targeted slices first to localize:
   - `.venv/bin/python -m pytest backend/tests/worker/test_mcp_tool_registry.py -q`
   - `.venv/bin/python -m pytest backend/tests/contract/test_phase6_assistant_api.py -q`
2. Then run full suites.
3. If hangs persist, capture stack dump (`pytest -vv -s` plus process-level traceback tooling) and record in dev notes.

## Bootstrap/Import-Time Native Preflight Issues in Tests

Symptom:
- failures during import/collection due to native bootstrap.

Action:
- Ensure tests run with `LUNAR_ANALYST_SKIP_IMPORT_TIME_NATIVE_PREFLIGHT=1` (already set in `backend/tests/conftest.py`).
- Use explicit native health probing endpoints/tests for bootstrap diagnostics instead of import-time behavior.
