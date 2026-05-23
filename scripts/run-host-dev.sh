#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export LUNAR_ANALYST_CONFIG_TOML="${LUNAR_ANALYST_CONFIG_TOML:-$ROOT_DIR/config/lunar_analyst.toml}"
export LUNAR_ANALYST_WORKSPACE_ROOT="${LUNAR_ANALYST_WORKSPACE_ROOT:-/e/lunar_analyst_scenarios}"

MOONLIB_NATIVE_DIR="$ROOT_DIR/native/new_horizon/moonlib/bin/Debug/net9.0/linux-x64"
if [[ -d "$MOONLIB_NATIVE_DIR" ]]; then
  export LD_LIBRARY_PATH="$MOONLIB_NATIVE_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

PYTHON_BIN="${LUNAR_ANALYST_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "run-host-dev.sh: no Python interpreter found. Set LUNAR_ANALYST_PYTHON or install python3." >&2
    exit 1
  fi
fi

cd "$ROOT_DIR"

export LUNAR_ANALYST_HOST="${LUNAR_ANALYST_HOST:-0.0.0.0}"
exec "$PYTHON_BIN" -m uvicorn backend.api.app:app \
  --host "$LUNAR_ANALYST_HOST" \
  --port "${LUNAR_ANALYST_PORT:-8000}" \
  --reload
