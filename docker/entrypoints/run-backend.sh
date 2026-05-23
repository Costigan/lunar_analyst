#!/usr/bin/env bash
set -euo pipefail

export LUNAR_ANALYST_CONFIG_TOML="${LUNAR_ANALYST_CONFIG_TOML:-/opt/lunar-analyst/config/lunar_analyst.container.toml}"
export LUNAR_ANALYST_WORKSPACE_ROOT="${LUNAR_ANALYST_WORKSPACE_ROOT:-/var/lib/lunar-analyst/workspace}"
export HOME="${HOME:-/tmp/lunar-analyst-home}"
export PYTHONPATH="/opt/lunar-analyst:/opt/lunar-analyst/moonlayers_pkg${PYTHONPATH:+:$PYTHONPATH}"

mkdir -p \
  "$HOME" \
  "$LUNAR_ANALYST_WORKSPACE_ROOT" \
  "$LUNAR_ANALYST_WORKSPACE_ROOT/.assistant" \
  "$LUNAR_ANALYST_WORKSPACE_ROOT/.assistant/rag"

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec python -m uvicorn backend.api.app:app \
  --host "${LUNAR_ANALYST_HOST:-0.0.0.0}" \
  --port "${LUNAR_ANALYST_PORT:-8000}"
