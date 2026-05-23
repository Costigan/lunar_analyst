#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/docker"

if [[ "${1:-}" == "--volumes" ]]; then
  docker compose -f compose.dev.yml down -v
  exit 0
fi

docker compose -f compose.dev.yml down
