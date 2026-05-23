# Running Tests (From Project Root)

This guide shows the active Linux-only test workflow from the repo root.

## Prerequisites

- Bootstrap the repo-managed environment: `./scripts/bootstrap.sh`
- Use `.venv/bin/python` for backend Python commands.
- Use `.NET 9` for native tests.
- Use `npm` for frontend tests.

## Frontend Tests

Run all frontend tests:

```bash
npm run test
```

Run a single frontend test file:

```bash
npm run test -- src/__tests__/filterMatch.test.ts
```

## Backend Tests

Run all backend tests:

```bash
.venv/bin/python -m pytest backend/tests -q
```

Run a specific backend test file:

```bash
.venv/bin/python -m pytest backend/tests/worker/test_hillshade_job_flow.py -q
```

## Native Backend Tests

Run all native tests:

```bash
dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal
```

Run a focused native test subset:

```bash
dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj --filter LightmapArrayStreamingBridgeTests -v minimal
```

## Contract Tests

Refresh generated contract artifacts:

```bash
.venv/bin/python -m backend.tools.export_openapi
.venv/bin/python -m backend.tools.export_contract_schemas
```

Then run contract tests:

```bash
.venv/bin/python -m pytest backend/tests/contract -q
```

## Local Verification Bundle (ADR.0049)

Run the canonical local verification bundle:

```bash
scripts/run_local_verification.sh
```

This executes exports + contract/worker/integration/frontend/native verification in the standardized order.

## Suggested Run-Everything Sequence

1. `npm run test`
2. `.venv/bin/python -m pytest backend/tests -q`
3. `dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal`
4. `.venv/bin/python -m backend.tools.export_openapi`
5. `.venv/bin/python -m backend.tools.export_contract_schemas`
6. `.venv/bin/python -m pytest backend/tests/contract -q`

## Common Issues

### `npm run build` / `npm run test` confusion

At the repo root:

- `npm run test` runs the frontend tests
- `npm run build` does not exist

Use:

```bash
npm run build:map
```

or:

```bash
npm --prefix backend/web/lunar_analyst run build
```

### Frontend test command fails with unsupported flag

Vitest is not Jest. Use:

```bash
npm run test -- src/__tests__/filterMatch.test.ts
```

not `--runTestsByPath`.

### Native test file-lock / cache issues

If `dotnet test` fails due locked cache files:

1. Close IDE and test runner processes.
2. Delete the affected `obj/Debug/net9.0` folder.
3. Re-run `dotnet test`.
