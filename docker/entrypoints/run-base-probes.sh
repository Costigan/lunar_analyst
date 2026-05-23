#!/usr/bin/env bash
set -euo pipefail

python --version
dotnet --info
python - <<'PY'
import fastapi
import rasterio
from osgeo import gdal
import clr

print("fastapi", fastapi.__version__)
print("rasterio", rasterio.__version__)
print("gdal", gdal.VersionInfo("--version"))
print("pythonnet", clr.__name__)
PY
