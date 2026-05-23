# How to Test Lunar Analyst

This document defines the active Linux-only test workflow for Lunar Analyst.

All command examples assume:

- current working directory is the repo root
- the repo-managed environment lives at `.venv`
- frontend dependencies have been installed with `npm`

Bootstrap first when environment state is uncertain:

```bash
./scripts/bootstrap.sh
```

## 1. Automated Test Suites

### Backend Python Tests

Run all backend tests:

```bash
.venv/bin/python -m pytest backend/tests -q
```

Run a focused backend test file:

```bash
.venv/bin/python -m pytest backend/tests/worker/test_hillshade_job_flow.py -q
```

### Raster Transform and Script Runtime Coverage

Use these targeted tests when changing scripted raster-transform behavior, notebook/script helpers, or assistant tool contract behavior:

```bash
.venv/bin/python -m pytest backend/tests/worker/test_raster_transform_runtime.py -q
.venv/bin/python -m pytest backend/tests/worker/test_raster_transform_handler.py -q
.venv/bin/python -m pytest backend/tests/worker/test_map_algebra_handler.py -q
.venv/bin/python -m pytest backend/tests/worker/test_notebook_helper.py -q
.venv/bin/python -m pytest backend/tests/worker/test_mcp_tool_registry.py backend/tests/contract/test_openapi_contract.py backend/tests/contract/test_phase6_mcp_http.py -q
```

### Frontend Tests

Run all frontend tests from the repo root:

```bash
npm run test
```

Run a focused frontend test:

```bash
npm run test -- src/__tests__/filterMatch.test.ts
```

### Native .NET Tests

Run all native tests:

```bash
dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal
```

Run a focused native test class:

```bash
dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj --filter LightmapArrayStreamingBridgeTests -v minimal
```

### Contract and Schema Tests

Refresh exported contracts:

```bash
.venv/bin/python -m backend.tools.export_openapi
.venv/bin/python -m backend.tools.export_contract_schemas
```

Run contract tests:

```bash
.venv/bin/python -m pytest backend/tests/contract -q
```

## 2. Assistant Benchmark Evals

### Launch the Eval UI

```bash
.venv/bin/python -m backend.evals.assistant.leaderboard_ui
```

Notes:

- The leaderboard UI is a local Tkinter desktop app.
- It stores runs under `backend/evals/assistant/leaderboard_runs/ui`.

### Run Benchmarks

Functional suite:

```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --suite functional
```

Domain suite:

```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --suite domain
```

All suites:

```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --suite all
```

Planner-only smoke run:

```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --planner-only --max-cases 10 --output backend/evals/assistant/predictions_planner.jsonl
```

Selected cases only:

```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --case-id func_script_raster_001 --case-id dom_env_001 --output backend/evals/assistant/predictions_subset.jsonl
```

Override provider/model:

```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --provider ollama --model gpt-oss:20b --output backend/evals/assistant/predictions_ollama.jsonl
```

Disable confirmation auto-resolution:

```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --confirmation-decision none --output backend/evals/assistant/predictions_no_confirm.jsonl
```

Emit CSV/XLSX reports:

```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --output backend/evals/assistant/predictions.jsonl --csv-out backend/evals/assistant/predictions.csv --xlsx-out backend/evals/assistant/predictions.xlsx
```

Human-readable output:

```bash
.venv/bin/python -m backend.evals.assistant.run_benchmark --output backend/evals/assistant/predictions.jsonl --human-readable --human-readable-out backend/evals/assistant/predictions.txt
```

Score predictions:

```bash
.venv/bin/python -m backend.evals.assistant.score --predictions backend/evals/assistant/predictions.jsonl
.venv/bin/python -m backend.evals.assistant.score --predictions backend/evals/assistant/predictions.csv
.venv/bin/python -m backend.evals.assistant.score --predictions backend/evals/assistant/predictions.xlsx
```

Optional JSON score report:

```bash
.venv/bin/python -m backend.evals.assistant.score --predictions backend/evals/assistant/predictions.jsonl --json-out backend/evals/assistant/score_report.json
```

## 3. Manual Verification

### Start the Backend

```bash
./scripts/run-host-dev.sh
```

### Start the Frontend Dev Server

```bash
cd backend/web/lunar_analyst
npm run dev
```

### Build Frontend Assets

```bash
npm run build:frontends
```

### Verify MoonLayers Import

```bash
.venv/bin/python -c "import moonlayers; print(moonlayers.__file__)"
```

### Representative End-to-End Checks

After backend startup, verify:

- the browser app loads at `http://127.0.0.1:8000/lunar_analyst/`
- scenario list/create/discovery works
- map layers render
- notebook opening works
- assistant requests can run and stream results
- native smoke-dependent workflows behave as expected for your change area
