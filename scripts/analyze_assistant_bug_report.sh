#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

echo "Analyzing assistant bug report: ${1:-latest}"
exec "$PYTHON" -m backend.tools.analyze_assistant_bug_report --launch "$@"
