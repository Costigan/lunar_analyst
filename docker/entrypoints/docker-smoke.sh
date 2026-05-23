#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${LUNAR_ANALYST_DEV_WORKDIR:-/workspace/lunar_analyst}"
CONFIG_PATH="${LUNAR_ANALYST_DEV_CONFIG:-$WORKDIR/config/lunar_analyst.devcontainer.toml}"

cd "$WORKDIR"
export LUNAR_ANALYST_CONFIG_TOML="$CONFIG_PATH"

python --version
node --version
npm --version
dotnet --info >/tmp/lunar-analyst-dotnet-info.txt

python -m pip install -e ./moonlayers_pkg
npm --prefix backend/web/lunar_analyst ci --no-fund --no-audit
npm --prefix moonlayers_pkg ci --no-fund --no-audit

python -m pytest backend/tests/worker/test_workspace_path_contract.py backend/tests/worker/test_raster_transform_runtime.py -q
python - <<'PY'
import backend.api.dependencies as deps
services = deps.build_service_container()
print("workspace_root", services.stores.workspace_root)
services.job_service.shutdown()
services.notebook_job_service.terminate_all_running(reason="docker-smoke")
services.marimo_service.stop_if_running()
PY
