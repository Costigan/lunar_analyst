#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${LUNAR_ANALYST_RUNTIME_PORT:-18080}"
SCENARIO_ROOT="${LUNAR_ANALYST_RUNTIME_SMOKE_SCENARIO_ROOT:-phasec_runtime_smoke}"
DEFAULT_WORKSPACE_ROOT="${LUNAR_ANALYST_HOST_WORKSPACE_ROOT:-/e/lunar_analyst_scenarios}"
WORKSPACE_ROOT="${LUNAR_ANALYST_RUNTIME_WORKSPACE_ROOT:-$DEFAULT_WORKSPACE_ROOT}"
CONTAINER_NAME="lunar-analyst-runtime-smoke-$$"

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait_for_http() {
  local url="$1"
  local attempts="${2:-60}"
  local sleep_seconds="${3:-1}"
  local i
  for ((i = 1; i <= attempts; i += 1)); do
    if curl -fsS "$url" >/dev/null 2>/dev/null; then
      return 0
    fi
    sleep "$sleep_seconds"
  done
  echo "Timed out waiting for $url" >&2
  return 1
}

start_runtime() {
  docker run -d \
    --name "$CONTAINER_NAME" \
    -p "${PORT}:8000" \
    -v "${WORKSPACE_ROOT}:/var/lib/lunar-analyst/workspace" \
    lunar-analyst-runtime >/dev/null
  wait_for_http "http://127.0.0.1:${PORT}/api/v1/health"
}

stop_runtime() {
  docker rm -f "$CONTAINER_NAME" >/dev/null
}

"$ROOT_DIR/scripts/docker-build.sh"
mkdir -p "$WORKSPACE_ROOT"

start_runtime

FRONTEND_HTML="$(curl -fsS "http://127.0.0.1:${PORT}/lunar_analyst/")"
printf '%s' "$FRONTEND_HTML" | grep -q "/lunar_analyst/assets/"

CREATE_RESPONSE="$(
  curl -fsS \
    -X POST \
    -H "content-type: application/json" \
    -d "{\"scenario_root\":\"${SCENARIO_ROOT}\",\"name\":\"Phase C Runtime Smoke\",\"owner\":\"docker-runtime-smoke\"}" \
    "http://127.0.0.1:${PORT}/api/v1/scenarios"
)"
SCENARIO_ID="$(
  python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["scenario_id"])' <<<"$CREATE_RESPONSE"
)"

docker exec "$CONTAINER_NAME" python -c '
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
from backend.core.config import ESRI_103878_WKT

scenario_root = Path("/var/lib/lunar-analyst/workspace") / "'"$SCENARIO_ROOT"'"
scenario_root.mkdir(parents=True, exist_ok=True)
(scenario_root / "inputs").mkdir(parents=True, exist_ok=True)
transform = from_origin(0.0, 2.0, 1.0, 1.0)
for path, values in (
    (scenario_root / "dem.tif", np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)),
    (scenario_root / "inputs" / "a.tif", np.array([[5.0, 6.0], [7.0, 8.0]], dtype=np.float32)),
):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=int(values.shape[1]),
        height=int(values.shape[0]),
        count=1,
        dtype=str(values.dtype),
        crs=ESRI_103878_WKT,
        transform=transform,
        nodata=-9999.0,
    ) as ds:
        ds.write(values, 1)
'

JOB_RESPONSE="$(
  curl -fsS \
    -X POST \
    -H "content-type: application/json" \
    -d "{\"scenario_id\":\"${SCENARIO_ID}\",\"script\":\"result = a + 1\",\"inputs\":{\"a\":{\"relative_path\":\"inputs/a.tif\"}},\"output_relative_path\":\"analysis/runtime_smoke_plus_one.tif\",\"overwrite_mode\":\"always\",\"mode\":\"immediate\"}" \
    "http://127.0.0.1:${PORT}/api/v1/jobs/raster-transform"
)"
JOB_ID="$(
  python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["job_id"])' <<<"$JOB_RESPONSE"
)"

JOB_STATUS=""
for _ in $(seq 1 60); do
  JOB_STATUS="$(
    curl -fsS "http://127.0.0.1:${PORT}/api/v1/jobs/${JOB_ID}" \
      | python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["status"])'
  )"
  if [ "$JOB_STATUS" = "completed" ]; then
    break
  fi
  if [ "$JOB_STATUS" = "failed" ] || [ "$JOB_STATUS" = "cancelled" ]; then
    echo "Runtime smoke job ${JOB_ID} ended with status ${JOB_STATUS}" >&2
    exit 1
  fi
  sleep 1
done

[ "$JOB_STATUS" = "completed" ]
[ -f "${WORKSPACE_ROOT}/${SCENARIO_ROOT}/analysis/runtime_smoke_plus_one.tif" ]

curl -fsS "http://127.0.0.1:${PORT}/api/v1/assistant/providers" >/dev/null
[ -f "${WORKSPACE_ROOT}/.assistant/rag/global_rag.db" ]
[ -f "${WORKSPACE_ROOT}/scenario_catalog.db" ]

docker logs "$CONTAINER_NAME" 2>&1 | grep -Eq "Uvicorn running on|GET /api/v1/health"

stop_runtime
start_runtime

SCENARIOS_AFTER_RESTART="$(
  curl -fsS "http://127.0.0.1:${PORT}/api/v1/scenarios" \
    | python3 -c 'import json,sys; print(" ".join(sorted(item["scenario_id"] for item in json.loads(sys.stdin.read()))))'
)"

printf '%s' "$SCENARIOS_AFTER_RESTART" | grep -q "$SCENARIO_ID"
[ -f "${WORKSPACE_ROOT}/scenario_catalog.db" ]
[ -f "${WORKSPACE_ROOT}/.assistant/rag/global_rag.db" ]
[ -f "${WORKSPACE_ROOT}/${SCENARIO_ROOT}/analysis/runtime_smoke_plus_one.tif" ]

echo "Runtime smoke passed."
echo "Workspace root: ${WORKSPACE_ROOT}"
echo "Scenario: ${SCENARIO_ID}"
