#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

docker build -f docker/Dockerfile.base -t lunar-analyst-base .
docker build -f docker/Dockerfile.dev -t lunar-analyst-dev .
docker build -f docker/Dockerfile.runtime -t lunar-analyst-runtime .
