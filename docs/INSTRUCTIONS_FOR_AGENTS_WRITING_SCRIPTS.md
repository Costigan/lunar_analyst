# Agent Instructions: Notebook and Script API Reference

This document describes Python APIs and specialized tools that AI agents (and humans) should use when writing scripts, notebooks, or performing computations for Lunar Analyst.

## 0. Typical User Requests -> Functions to Use

Use this section first when planning implementation.

| Typical request | Recommended functions | Notes |
|---|---|---|
| Simple raster arithmetic or boolean masks (e.g., "slope > 15") | `raster.calculate` (DSL) | **Preferred Path.** Safer, faster, and requires no script writing. |
| Generate a time-aggregated lightmap GeoTIFF from DEM + horizons | `run_lightmap_streaming_raster_job`, `LightmapRunConfig` | Put scientific logic in `tile_transform`; let helper own streaming/GDAL boilerplate. |
| Change how streamed time-axis values are reduced per pixel (mean/max/percent-lit/etc.) | `run_lightmap_streaming_raster_job` with custom `tile_transform` | Keep transform pure and 2D output. |
| Control output GeoTIFF dtype (for size/perf/precision) | `LightmapRunConfig.output_dtype` or `params["output_dtype"]` | Transform output is coerced to chosen dtype before write. |
| Make script work both under job runner and standalone | `resolve_scenario_identity_and_root`, `resolve_dem_path_from_params`, `register_output_if_available`, `report_progress` | Helper functions are context-aware and runner-safe. |
| Resolve user-supplied relative paths safely | `safe_scenario_relative_path`, `resolve_scenario_relative_dir`, `replace_output_file` | Prevents traversal; keeps writes under scenario root. |
| Long-running loop with cancellation/progress | `is_cancelled`, `report_progress` | Check cancellation frequently; emit structured progress by stage. |
| Register produced artifacts without crashing outside runner | `register_output_if_available` | Returns `False` when context is unavailable. |
| Write a JSON manifest and output stats | `write_json`, `directory_file_stats` | Useful for audit/debug payloads. |
| Call low-level native bridge directly for advanced workflows | `create_moonlib_bridge`, `to_dotnet_string_list` | Use only when high-level helper is insufficient. |

## 1. Security & Efficiency Policy

### 1.1 Prefer DSL over Scripts
For any operation that can be expressed as raster arithmetic, terrain analysis (slope/aspect), or masking, **you MUST use the `raster.calculate` tool** instead of writing a Python script. 
- **Safety:** The DSL is executed in a restricted AST environment.
- **Performance:** Evaluated in-process without subprocess overhead.
- **Reliability:** Avoids file I/O boilerplate and registration errors.

### 1.2 Script Safety
If a full Python script is required (e.g., complex multi-stage iterative loops or external data fetching):
- **No Absolute Paths:** Use scenario-relative paths via `safe_scenario_relative_path`.
- **No Traversal:** Never use `..` in paths.
- **Registration:** Always call `register_output_if_available` for produced files.

## 2. Golden Examples

### 2.1 Map Algebra DSL (High Efficiency)
Use this via the `raster.calculate` tool. Note the NumPy-like syntax.

```python
# Task: Find south-facing slopes steeper than 20 degrees
# Expression:
(slope(primary_dem) > 20) & (aspect(primary_dem) > 135) & (aspect(primary_dem) < 225)
```

### 2.2 Minimal streamed lightmap script (Advanced Pattern)

```python
from __future__ import annotations

import numpy as np

from backend.notebook.notebook_helper import LightmapRunConfig
from backend.notebook.notebook_helper import run_lightmap_streaming_raster_job


def compress_tile(tile_3d: np.ndarray) -> np.ndarray:
    # Example: mean over time, normalize uint8 range to 0..1 float
    tile_2d = tile_3d.mean(axis=0, dtype=np.float32)
    tile_2d = (tile_2d / np.float32(255.0)).astype(np.float32, copy=False)
    return tile_2d


result = run_lightmap_streaming_raster_job(
    config=LightmapRunConfig(
        time_start_utc="2027-09-01T00:00:00",
        time_stop_utc="2027-10-01T00:00:00",
        time_step_hours=2.0,
        default_horizons_relative_dir="lighting/horizons",
        default_output_relative_path="lighting/lightmap_mean.tif",
        output_subkind="lightmap_streaming_time_mean",
        output_dtype="float32",
        output_nodata=-9999.0,
    ),
    tile_transform=compress_tile,
)
print(result)
```

## 3. High-Level Helper APIs (`backend.notebook.notebook_helper`)

( ... rest of original documentation ... )
