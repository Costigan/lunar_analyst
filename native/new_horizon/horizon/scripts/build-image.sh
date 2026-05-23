#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="${1:-lunar-horizon:local}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

cd "${REPO_ROOT}"

docker build \
  -f native/new_horizon/horizon/Dockerfile \
  -t "${IMAGE_TAG}" \
  .

echo "Built image: ${IMAGE_TAG}"
