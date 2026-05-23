#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

run_step() {
  local label="$1"
  shift
  local start end elapsed
  echo
  echo "==> $label"
  start="$(date +%s)"
  "$@"
  end="$(date +%s)"
  elapsed="$((end - start))"
  echo "<== $label completed in ${elapsed}s"
}

run_step "Export OpenAPI" .venv/bin/python -m backend.tools.export_openapi
run_step "Export Contract Schemas" .venv/bin/python -m backend.tools.export_contract_schemas
run_step "Contract Drift Check" scripts/check_contract_drift.sh
run_step "Contract Tests" .venv/bin/python -m pytest backend/tests/contract -q
run_step "Worker Tests" .venv/bin/python -m pytest backend/tests/worker -q
run_step "Integration Tests" .venv/bin/python -m pytest backend/tests/integration -q
run_step "Frontend Tests" npm run test
run_step "Native Horizon Tests" dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal

echo
echo "Local verification bundle complete."
