# ADR.0049 Phase 0 Baseline (2026-04-12)

This note records the initial baseline required by `docs/ADR.0049.python_core_modularization_ci_and_leak_free_reliability_program.md`.

## Environment

- Repo root: `/e/projects/lunar_analyst`
- Python: `.venv/bin/python` (`3.11.15`)
- Bootstrap: `./scripts/bootstrap.sh` completed (required one elevated/network run for Python build deps)

## Baseline Module Metrics

Collected with:

```bash
.venv/bin/python -m backend.tools.collect_modularization_metrics \
  --repo-root . \
  --json-out .assistant/adr0049/baseline_metrics_2026-04-12.json
```

| Module | Total Lines | Non-Empty Lines | Function Defs | Class Defs | Import Fan-Out |
|---|---:|---:|---:|---:|---:|
| `backend/api/dependencies.py` | 5916 | 5440 | 244 | 23 | 28 |
| `backend/jobs/handlers.py` | 5658 | 5362 | 68 | 27 | 20 |
| `backend/services/assistant/tool_registry.py` | 2069 | 1953 | 43 | 1 | 12 |

## Local Verification Bundle

Canonical bundle command:

```bash
scripts/run_local_verification.sh
```

Bundle steps:

1. `.venv/bin/python -m backend.tools.export_openapi`
2. `.venv/bin/python -m backend.tools.export_contract_schemas`
3. `.venv/bin/python -m pytest backend/tests/contract -q`
4. `.venv/bin/python -m pytest backend/tests/worker -q`
5. `.venv/bin/python -m pytest backend/tests/integration -q`
6. `npm run test`
7. `dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal`

## Baseline Command Outcomes

Measured commands and durations (using `time timeout ...` where noted):

- `.venv/bin/python -m backend.tools.export_openapi`: PASS, ~2s
- `.venv/bin/python -m backend.tools.export_contract_schemas`: PASS, ~1s
- `time timeout 300 .venv/bin/python -m pytest backend/tests/contract -q`: TIMEOUT, ~300s
- `time timeout 300 .venv/bin/python -m pytest backend/tests/worker -q`: TIMEOUT, ~300s (progress reached ~32%)
- `time timeout 300 .venv/bin/python -m pytest backend/tests/integration -q`: TIMEOUT, ~303s
- `time timeout 600 npm run test`: PASS, ~0.9s
- `time timeout 600 dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal`: FAIL quickly (~1.4s) due missing GDAL payload (`gdal_wrap` / `gdal` runtime assets under test bin output)

## Known Flaky/Leak-Prone Reproduction Notes

- Contract suite hang reproduction:
  - `.venv/bin/python -m pytest backend/tests/contract -q`
  - Observed behavior: prints initial progress (`.`) then stalls until timeout.
- Worker suite hang reproduction:
  - `.venv/bin/python -m pytest backend/tests/worker -q`
  - Observed behavior: progresses to ~32% then stalls until timeout.
- Integration suite hang reproduction:
  - `.venv/bin/python -m pytest backend/tests/integration -q`
  - Observed behavior: prints initial progress (`.`) then stalls until timeout.
- Native test runtime payload issue:
  - `dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal`
  - Observed behavior: assembly initialization fails because GDAL payload is missing from test runtime output path.

## Artifacts

- Raw baseline metrics JSON: `.assistant/adr0049/baseline_metrics_2026-04-12.json`
- Raw command log (partial + iterative): `.assistant/adr0049/baseline_test_results_2026-04-12.txt`
