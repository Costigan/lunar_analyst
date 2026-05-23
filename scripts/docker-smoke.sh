#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LUNAR_ANALYST_DEV_UID="${LUNAR_ANALYST_DEV_UID:-$(id -u)}"
export LUNAR_ANALYST_DEV_GID="${LUNAR_ANALYST_DEV_GID:-$(id -g)}"

"$ROOT_DIR/scripts/docker-build.sh"

cd "$ROOT_DIR/docker"
docker compose -f compose.dev.yml run --rm app-dev /usr/local/bin/docker-smoke.sh
