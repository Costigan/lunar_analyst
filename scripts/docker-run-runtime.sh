#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_WORKSPACE_ROOT="${LUNAR_ANALYST_HOST_WORKSPACE_ROOT:-/e/lunar_analyst_scenarios}"
WORKSPACE_ROOT="${LUNAR_ANALYST_RUNTIME_WORKSPACE_ROOT:-$DEFAULT_WORKSPACE_ROOT}"
PORT="${LUNAR_ANALYST_RUNTIME_PORT:-8000}"

mkdir -p "$WORKSPACE_ROOT"

docker run --rm -it \
  -p "${PORT}:8000" \
  -v "${WORKSPACE_ROOT}:/var/lib/lunar-analyst/workspace" \
  lunar-analyst-runtime
