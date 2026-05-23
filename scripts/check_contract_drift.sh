#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

.venv/bin/python -m backend.tools.export_openapi
.venv/bin/python -m backend.tools.export_contract_schemas

if ! git diff --quiet -- docs/contracts/generated/v1; then
  echo "Contract drift detected in docs/contracts/generated/v1." >&2
  echo "Run exports and commit updated generated contracts." >&2
  git --no-pager diff -- docs/contracts/generated/v1 >&2 || true
  exit 1
fi

echo "No contract drift detected."
