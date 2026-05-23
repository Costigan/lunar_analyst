# GEMINI.md

## Lunar Analyst (Quick Model Context)

Lunar Analyst is a python lunar south-pole analysis toolkit with a web UI with some compute-intensive tasks done using .net core.

### Primary Goal
- Scenario-based lunar terrain/lighting analysis with high-fidelity map visualization and notebook-driven workflows.

### Core Stack
- FastAPI backend (authoritative control plane)
- React + OpenLayers web client (and Tauri-hostable)
- Python worker process for heavy compute (`pythonnet` + .NET `moonlib`)
- Marimo/notebook workflows through backend APIs (not direct DB mutation)

### Key Invariants
- Runtime baseline: Python 3.11 + .NET 9.0.
- Scenario is the source of truth on disk (`primary_dem.tif`, `scenario.db`).
- CRS discipline: explicit CRS handling; no silent reprojection.
- Filesystem safety: normalized in-root paths; reject traversal.
- Long jobs: support cancellation + structured progress events.
- Compute contract rule: real job logic belongs in `backend/jobs/handlers.py` methods (no parallel duplicate contract layer).

### Practical Guidance
- Read ../../../docs/INSTRUCTIONS_FOR_AGENTS_WRITING_SCRIPTS.md for detailed guidance on building scripts.
- Prefer helpers in `backend/notebook/notebook_helper.py` for scripts/notebooks.
- Keep algorithm-specific math in small transform functions; keep orchestration in helpers.
- Register outputs via backend/runtime helpers so products appear in app workflows.
- Add focused tests for changed behavior (`backend/tests/...`).

# Agent Instructions: Notebook and Script API Reference

This document describes Python APIs that AI agents (and humans) should use when writing scripts/notebooks for Lunar Analyst.

## 0. Typical User Requests -> Functions to Use

Use this section first when planning implementation.

| Typical request | Recommended functions | Notes |
|---|---|---|
| Generate a time-aggregated lightmap GeoTIFF from DEM + horizons | `run_lightmap_streaming_raster_job`, `LightmapRunConfig` | Put scientific logic in `tile_transform`; let helper own streaming/GDAL boilerplate. |
| Change how streamed time-axis values are reduced per pixel (mean/max/percent-lit/etc.) | `run_lightmap_streaming_raster_job` with custom `tile_transform` | Keep transform pure and 2D output. |
| Control output GeoTIFF dtype (for size/perf/precision) | `LightmapRunConfig.output_dtype` or `params["output_dtype"]` | Transform output is coerced to chosen dtype before write. |
| Make script work both under job runner and standalone | `resolve_scenario_identity_and_root`, `resolve_dem_path_from_params`, `register_output_if_available`, `report_progress` | Helper functions are context-aware and runner-safe. |
| Resolve user-supplied relative paths safely | `safe_scenario_relative_path`, `resolve_scenario_relative_dir`, `replace_output_file` | Prevents traversal; keeps writes under scenario root. |
| Long-running loop with cancellation/progress | `is_cancelled`, `report_progress` | Check cancellation frequently; emit structured progress by stage. |
| Register produced artifacts without crashing outside runner | `register_output_if_available` | Returns `False` when context is unavailable. |
| Write a JSON manifest and output stats | `write_json`, `directory_file_stats` | Useful for audit/debug payloads. |
| Call low-level native bridge directly for advanced workflows | `create_moonlib_bridge`, `to_dotnet_string_list` | Use only when high-level helper is insufficient. |
| Full custom streaming control (buffers, poll loop, status) | `LightmapStreamRequestPy`, `LightmapStreamingClient`, `stream_tiles` | Advanced path; more error-handling responsibility. |

## 1. Global Implementation Checklist

Before returning a script:
- Confirm all user-provided paths are scenario-relative and validated.
- Confirm outputs are written under scenario root.
- Confirm long loops check cancellation and emit progress.
- Confirm artifacts are registered (`register_output_if_available`) when applicable.
- Confirm output dtype choice is explicit and documented.
- Confirm script runs both in runner mode and direct mode when requested.

## 2. Golden Examples

### 2.1 Minimal streamed lightmap script (recommended pattern)

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

### 2.2 Simple JSON sidecar manifest

```python
from pathlib import Path
from backend.notebook.notebook_helper import directory_file_stats, write_json

output_dir = Path("D:/lunar_analyst_scenarios/test_scenario/lighting")
file_count, size_bytes = directory_file_stats(output_dir)
write_json(
    output_dir / "manifest.json",
    {"file_count": file_count, "size_bytes": size_bytes},
)
```

## 3. High-Level Helper APIs (`backend.notebook.notebook_helper`)

Prefer these APIs first.

### 3.1 `run_lightmap_streaming_raster_job`

Signature:
```python
run_lightmap_streaming_raster_job(
    *,
    config: LightmapRunConfig,
    tile_transform: Callable[[np.ndarray], np.ndarray],
) -> dict[str, Any]
```

Purpose:
- Run native lightmap tile streaming.
- Apply `tile_transform` to each streamed tile.
- Write a single-band GeoTIFF aligned to scenario DEM.
- Handle context resolution, cancellation/progress, and output registration.

#### 3.1.1 `tile_transform` contract
- Input: `tile_3d: np.ndarray` with shape `[time, height, width]` (typically `uint8`).
- Output: `np.ndarray` with shape `[height, width]` (2D).
- Output dtype can differ from configured output dtype.

#### 3.1.2 Output dtype control and coercion behavior (current)
- Output GeoTIFF band dtype is chosen from:
  1. `params["output_dtype"]` when present
  2. otherwise `config.output_dtype`
- Supported: `uint8`, `int16`, `uint16`, `int32`, `uint32`, `float32`, `float64`
- If transform dtype differs, helper coerces with NumPy cast:
  - `tile_2d = tile_2d.astype(gdal_np_dtype, copy=False)`
- No extra clipping/rounding/NaN policy is applied before cast; NumPy casting semantics apply.

#### 3.1.3 Parameter override precedence
When running under job runner, `ctx.params` may override config defaults:
- `horizons_relative_dir`
- `output_relative_path`
- `output_dtype`
- `nodata_value`
- `surrounding_dem_paths`
- `observer_elevation_meters`
- `time_start_utc`, `time_stop_utc`, `time_step_hours`
- `use_spice_sun_vectors`
- `buffer_count`, `poll_timeout_ms`
- `patch_width`, `patch_height`
- `max_read_parallelism`, `max_compute_parallelism`, `ready_queue_capacity`

Not overridden from params in current helper:
- `output_kind`, `output_subkind`, `output_render_mode`
- `stream_progress_tile_interval`

#### 3.1.4 Common failure modes
- Scenario root, DEM, or horizons directory missing.
- `tile_transform` does not return ndarray or returns non-2D output.
- Transform output is smaller than streamed tile dimensions.
- Unsupported `output_dtype`.
- GDAL open/create failures.
- Cancellation triggered during streaming.

#### 3.1.5 Return value
Summary dict including:
- `scenario_id`, `scenario_root`
- `dem_relative_path`, `horizons_relative_dir`
- `output_relative_path`
- `time_start_utc`, `time_stop_utc`, `time_step_hours`
- `tiles_written`, `value_min`, `value_max`

### 3.2 `LightmapRunConfig`

Fields:
- Required:
  - `time_start_utc: str`
  - `time_stop_utc: str`
  - `time_step_hours: float`
- Optional:
  - `default_horizons_relative_dir: str = "lighting/horizons"`
  - `default_output_relative_path: str = "lighting/lightmap_streaming_time_mean.tif"`
  - `output_kind: str = "raster"`
  - `output_subkind: str = "lightmap_streaming_time_mean"`
  - `output_render_mode: str | None = "raster"`
  - `output_dtype: str = "float32"`
  - `output_nodata: float = -9999.0`
  - `patch_width: int = 128`
  - `patch_height: int = 128`
  - `max_read_parallelism: int = 4`
  - `max_compute_parallelism: int = 24`
  - `ready_queue_capacity: int = 64`
  - `default_observer_elevation_meters: float = 0.0`
  - `default_use_spice_sun_vectors: bool = True`
  - `buffer_count: int = 6`
  - `poll_timeout_ms: int = 250`
  - `stream_progress_tile_interval: int = 20`

### 3.3 `resolve_scenario_identity_and_root`

```python
resolve_scenario_identity_and_root(
    *,
    default_scenario_id: str = "test_scenario",
    default_scenario_parent_dir: str | Path = "D:/lunar_analyst_scenarios",
    scenario_id_env: str = "LUNAR_NOTEBOOK_SCENARIO_ID",
    scenario_root_env: str = "LUNAR_NOTEBOOK_SCENARIO_ROOT",
) -> tuple[str, Path]
```

Behavior:
- Under runner: returns scenario from runtime context.
- Outside runner: resolves from env vars or defaults.

### 3.4 `resolve_dem_path_from_params`

```python
resolve_dem_path_from_params(
    *,
    scenario_root: Path,
    scenario_id: str,
    params: dict[str, Any],
    param_name: str = "dem_relative_path",
    default_relative_path: str = "dem.tif",
) -> Path
```

Behavior:
- Uses scenario-relative param path when provided.
- Falls back to `resolve_primary_dem_path(...)`.

### 3.5 `resolve_scenario_relative_dir`

```python
resolve_scenario_relative_dir(
    *,
    scenario_root: Path,
    raw: str,
    default: str,
    create: bool = True,
) -> tuple[str, Path]
```

Behavior:
- Validates relative path and resolves under scenario root.
- Creates directory when `create=True`.

### 3.6 `safe_scenario_relative_path`

Purpose:
- Validate scenario-relative paths and reject traversal/absolute patterns.

### 3.7 `report_progress`

Purpose:
- Emit structured job progress (`percent`, `message`, `stage`).

### 3.8 `is_cancelled`

Purpose:
- Read cancellation signal from runner context.

### 3.9 `register_output_if_available`

Purpose:
- Register output if context exists.
- Returns `False` (instead of raising) when context is unavailable.

### 3.10 `replace_output_file`

Purpose:
- Remove existing file and optional `.aux.xml` sidecar before regeneration.

### 3.11 `write_json`

```python
write_json(path: Path, payload: dict[str, Any], *, indent: int = 2, sort_keys: bool = True) -> None
```

Purpose:
- Write JSON payload with trailing newline; creates parents as needed.

### 3.12 `directory_file_stats`

```python
directory_file_stats(path: Path) -> tuple[int, int]
```

Purpose:
- Recursively count files and total byte size.

### 3.13 `bool_param`

```python
bool_param(params: dict[str, Any], key: str, default: bool) -> bool
```

Recognized truthy strings: `1`, `true`, `yes`, `on`  
Recognized falsy strings: `0`, `false`, `no`, `off`

### 3.14 `create_moonlib_bridge`

```python
create_moonlib_bridge(*, force_bootstrap: bool = True, verify_bridge_smoke: bool = False) -> Any
```

Purpose:
- Bootstrap native runtime + CLR GDAL registration and return `moonlib.MoonlibBridge()`.

### 3.15 `bootstrap_native_and_register_gdal`

```python
bootstrap_native_and_register_gdal(*, force: bool = True, verify_bridge_smoke: bool = False) -> int
```

Purpose:
- Initialize pythonnet/native runtime.
- Register GDAL in CLR runtime.
- Return driver count.

### 3.16 `to_dotnet_string_list`

```python
to_dotnet_string_list(values: Iterable[Any]) -> Any
```

Purpose:
- Convert Python sequence to `.NET List[String]`.

## 4. Low-Level Streaming APIs (`backend.worker.lightmap_streaming`)

Use only for advanced/custom workflows.

### 4.1 `LightmapStreamRequestPy`
Request dataclass for streaming job setup. Key fields include scenario/DEM/horizon paths, UTC window, step, observer height, tile and concurrency controls.

Useful methods:
- `time_count() -> int`
- `tile_shape() -> tuple[int, int, int]`

### 4.2 `LightmapStreamingClient`
Common methods:
- `start(request) -> str`
- `register_buffer(job_id, buffer_id, arr) -> bool`
- `poll_next_tile(job_id, timeout_ms) -> StreamTileMetaPy | None`
- `release_buffer(job_id, buffer_id) -> bool`
- `get_status(job_id) -> LightmapStreamStatusPy`
- `cancel(job_id) -> bool`
- `dispose(job_id) -> bool`

### 4.3 `stream_tiles`

```python
stream_tiles(
    client: LightmapStreamingClient,
    request: LightmapStreamRequestPy,
    *,
    buffer_count: int = 8,
    poll_timeout_ms: int = 250,
) -> Iterator[tuple[StreamTileMetaPy, np.ndarray]]
```

Behavior:
- Starts streaming job and registers buffers.
- Yields `(tile_meta, tile_3d_buffer)` for ready tiles.
- Releases each yielded buffer.
- Cancels/disposes on iterator shutdown.

## 5. Runtime APIs (`backend.notebook.runtime`)

Lower-level APIs that `notebook_helper` wraps.

### 5.1 `get_context() -> NotebookJobContext`
Returns runtime context from context JSON/env.

### 5.2 `is_running_under_job_runner() -> bool`
Detects managed runner environment.

### 5.3 `register_output(...)`
Strict output registration call (use `register_output_if_available` when context may be absent).

### 5.4 `resolve_primary_dem_path(...) -> Path`
Resolves canonical primary DEM from scenario DB metadata (with fallback behavior).

## 6. Validation Commands (Local)

Use the repo-managed `.venv` Python.

- Run helper tests:
  - `.venv/bin/python -m pytest backend/tests/worker/test_notebook_helper.py -q`
- Run contract tests (if script touches API/contract behavior):
  - `.venv/bin/python -m pytest backend/tests/contract -q`

## 7. Agent Guidance

- Prefer high-level helpers over raw bridge calls.
- Keep script-specific science/math in small transform functions.
- Keep orchestration and path/runtime safety in shared helpers.
- When uncertain, implement additive changes and preserve existing helper contracts.
