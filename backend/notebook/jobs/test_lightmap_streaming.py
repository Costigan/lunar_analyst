from __future__ import annotations

from pathlib import Path
import traceback
from typing import Any

import numpy as np

from backend.notebook.notebook_helper import get_context
from backend.notebook.notebook_helper import is_cancelled
from backend.notebook.notebook_helper import is_running_under_job_runner
from backend.notebook.notebook_helper import register_output_if_available
from backend.notebook.notebook_helper import replace_output_file
from backend.notebook.notebook_helper import report_progress
from backend.notebook.notebook_helper import resolve_dem_path_from_params
from backend.notebook.notebook_helper import safe_scenario_relative_path
from backend.worker.gdal_runtime import configure_gdal_runtime
from backend.worker.lightmap_streaming import LightmapStreamRequestPy
from backend.worker.lightmap_streaming import LightmapStreamingClient
from backend.worker.lightmap_streaming import stream_tiles

TIME_START_UTC = "2027-09-01T00:00:00"
TIME_STOP_UTC = "2027-10-01T00:00:00"
TIME_STEP_HOURS = 2.0


def _log(message: str) -> None:
    print(f"[test_lightmap_streaming] {message}", flush=True)


def _safe_report_progress(*, percent: float, message: str, stage: str) -> None:
    if not is_running_under_job_runner():
        return
    report_progress(percent=percent, message=message, stage=stage)


def _safe_is_cancelled() -> bool:
    if not is_running_under_job_runner():
        return False
    return is_cancelled()


def _resolve_runtime_context() -> tuple[str, Path, dict[str, Any]]:
    if is_running_under_job_runner():
        ctx = get_context()
        scenario_id = str(ctx.scenario_id)
        scenario_root = Path(ctx.scenario_root_dir).resolve()
        params = ctx.params if isinstance(ctx.params, dict) else {}
        return scenario_id, scenario_root, params

    return (
        "test_scenario",
        Path("d:/lunar_analyst_scenarios/test_scenario").resolve(),
        {},
    )


def run(_context: object | None = None) -> dict[str, Any]:
    scenario_id, scenario_root, params = _resolve_runtime_context()
    _log(f"start run scenario_id={scenario_id} scenario_root={scenario_root}")
    if not scenario_root.exists() or not scenario_root.is_dir():
        raise FileNotFoundError(f"Scenario root does not exist: {scenario_root}")

    # Bootstrap moonlib before importing GDAL, so strict DLL resolver preloads
    # its native dependency chain (including sqlite3.dll) first.
    _log("bootstrapping LightmapStreamingClient")
    _safe_report_progress(percent=5.0, message="Initializing native bridge", stage="init")
    client = LightmapStreamingClient(force_bootstrap=True, verify_bridge_smoke=False)
    _log("native bridge ready")

    _log("configuring GDAL runtime")
    _safe_report_progress(percent=10.0, message="Initializing GDAL runtime", stage="init")
    configure_gdal_runtime()
    from osgeo import gdal

    gdal.UseExceptions()
    _log("GDAL runtime configured")

    _log("resolving DEM path")
    dem_path = resolve_dem_path_from_params(
        scenario_root=scenario_root,
        scenario_id=scenario_id,
        params=params,
    )
    _log(f"DEM path={dem_path}")
    try:
        dem_rel = dem_path.relative_to(scenario_root).as_posix()
    except ValueError:
        dem_rel = str(dem_path)

    horizons_rel = safe_scenario_relative_path(
        str(params.get("horizons_relative_dir", "lighting/horizons")).strip(),
        default="lighting/horizons",
    )
    horizons_dir = (scenario_root / horizons_rel).resolve()
    _log(f"horizons_dir={horizons_dir}")
    if not horizons_dir.exists() or not horizons_dir.is_dir():
        raise FileNotFoundError(f"Horizons directory does not exist: {horizons_dir}")

    output_rel = safe_scenario_relative_path(
        str(
            params.get(
                "output_relative_path",
                "lighting/lightmap_streaming_mean_20270901_20271001_2h.tif",
            )
        ).strip(),
        default="lighting/lightmap_streaming_mean_20270901_20271001_2h.tif",
    )
    output_path = (scenario_root / output_rel).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    replace_output_file(output_path)
    _log(f"output_path={output_path}")

    observer_elevation_meters = float(params.get("observer_elevation_meters", 0.0))
    surrounding_dem_paths_raw = params.get("surrounding_dem_paths", [])
    if not isinstance(surrounding_dem_paths_raw, list):
        raise ValueError("params.surrounding_dem_paths must be a list of paths.")
    surrounding_dem_paths = [
        Path(str(item)).expanduser().resolve()
        for item in surrounding_dem_paths_raw
        if str(item).strip()
    ]

    buffer_count = max(1, int(params.get("buffer_count", 6)))
    poll_timeout_ms = max(1, int(params.get("poll_timeout_ms", 250)))
    use_spice_sun_vectors = bool(params.get("use_spice_sun_vectors", True))
    nodata_value = float(params.get("nodata_value", -9999.0))

    request = LightmapStreamRequestPy(
        scenario_root_dir=scenario_root,
        dem_path=dem_path,
        surrounding_dem_paths=surrounding_dem_paths,
        horizon_dir=horizons_dir,
        start_utc=TIME_START_UTC,
        stop_utc=TIME_STOP_UTC,
        time_step_hours=TIME_STEP_HOURS,
        observer_elevation_meters=observer_elevation_meters,
        patch_width=128,
        patch_height=128,
        max_read_parallelism=max(1, int(params.get("max_read_parallelism", 4))),
        max_compute_parallelism=max(1, int(params.get("max_compute_parallelism", 24))),
        ready_queue_capacity=max(1, int(params.get("ready_queue_capacity", 64))),
        use_spice_sun_vectors=use_spice_sun_vectors,
    )
    _log(
        "request built "
        f"time=[{TIME_START_UTC}..{TIME_STOP_UTC}] step_hours={TIME_STEP_HOURS} "
        f"spice={use_spice_sun_vectors} buffer_count={buffer_count}"
    )

    _log("opening DEM with GDAL")
    dem_ds = gdal.Open(str(dem_path), gdal.GA_ReadOnly)
    if dem_ds is None:
        raise RuntimeError(f"Failed to open DEM: {dem_path}")

    width = int(dem_ds.RasterXSize)
    height = int(dem_ds.RasterYSize)
    projection = dem_ds.GetProjection() or ""
    geotransform = dem_ds.GetGeoTransform(can_return_null=True)
    if geotransform is None:
        raise RuntimeError("Primary DEM has no geotransform.")
    _log(f"DEM opened size={width}x{height}")

    driver = gdal.GetDriverByName("GTiff")
    if driver is None:
        raise RuntimeError("GDAL GTiff driver not available.")

    out_ds = driver.Create(
        str(output_path),
        width,
        height,
        1,
        gdal.GDT_Float32,
        options=[
            "TILED=YES",
            "BLOCKXSIZE=128",
            "BLOCKYSIZE=128",
            "COMPRESS=LZW",
            "BIGTIFF=IF_SAFER",
        ],
    )
    if out_ds is None:
        raise RuntimeError(f"Failed to create output GeoTIFF: {output_path}")
    _log("GeoTIFF dataset created")

    out_ds.SetProjection(projection)
    out_ds.SetGeoTransform(geotransform)
    out_band = out_ds.GetRasterBand(1)
    if out_band is None:
        raise RuntimeError("Failed to access output raster band.")
    out_band.SetNoDataValue(nodata_value)
    out_band.Fill(nodata_value)
    _log("output band initialized with nodata fill")

    tiles_written = 0
    global_min: float | None = None
    global_max: float | None = None

    try:
        _log("starting tile streaming loop")
        _safe_report_progress(percent=15.0, message="Streaming lightmap tiles", stage="stream")
        for tile_meta, tile_3d in stream_tiles(
            client,
            request,
            buffer_count=buffer_count,
            poll_timeout_ms=poll_timeout_ms,
        ):
            if _safe_is_cancelled():
                raise RuntimeError("Job cancelled while streaming lightmap tiles.")

            # KEY LINES: These two lines are the key portion of this script.  The take the time-series lighting
            # for each pixel along the 0 axis and compress them in some way to a single value.  This
            # calculation will be varied within the basic framework of this script.
            tile_2d = tile_3d.mean(axis=0, dtype=np.float32)
            tile_2d = (tile_2d / np.float32(255.0)).astype(np.float32, copy=False)

            xoff = int(tile_meta.patch_col)
            yoff = int(tile_meta.patch_row)
            tw = int(tile_meta.width)
            th = int(tile_meta.height)

            write_w = min(tw, width - xoff)
            write_h = min(th, height - yoff)
            if write_w <= 0 or write_h <= 0:
                continue

            out_band.WriteArray(tile_2d[:write_h, :write_w], xoff=xoff, yoff=yoff)
            tiles_written += 1

            local_min = float(tile_2d[:write_h, :write_w].min())
            local_max = float(tile_2d[:write_h, :write_w].max())
            global_min = local_min if global_min is None else min(global_min, local_min)
            global_max = local_max if global_max is None else max(global_max, local_max)

            if tiles_written % 20 == 0:
                _log(f"tiles_written={tiles_written}")
                _safe_report_progress(
                    percent=min(90.0, 15.0 + (tiles_written / 20.0)),
                    message=f"Wrote {tiles_written} tile(s)",
                    stage="stream",
                )
    finally:
        _log("flushing and closing GDAL datasets")
        out_band.FlushCache()
        out_ds.FlushCache()
        out_band = None
        out_ds = None
        dem_ds = None
        file_size = output_path.stat().st_size if output_path.exists() else -1
        _log(f"output file size bytes={file_size}")

    register_output_if_available(
        relative_path=output_rel,
        kind="raster",
        subkind="lightmap_streaming_time_mean",
        render_mode="raster",
        metadata={
            "source_dem": dem_rel,
            "horizons_relative_dir": horizons_rel,
            "time_start_utc": TIME_START_UTC,
            "time_stop_utc": TIME_STOP_UTC,
            "time_step_hours": TIME_STEP_HOURS,
            "value_range": "0..1",
            "tiles_written": tiles_written,
        },
    )

    _safe_report_progress(
        percent=95.0,
        message="Lightmap streaming test complete",
        stage="finalize",
    )
    _log(f"complete tiles_written={tiles_written}")

    payload: dict[str, Any] = {
        "scenario_id": scenario_id,
        "scenario_root": str(scenario_root),
        "dem_relative_path": dem_rel,
        "horizons_relative_dir": horizons_rel,
        "output_relative_path": output_rel,
        "time_start_utc": TIME_START_UTC,
        "time_stop_utc": TIME_STOP_UTC,
        "time_step_hours": TIME_STEP_HOURS,
        "tiles_written": tiles_written,
        "value_min": global_min,
        "value_max": global_max,
    }
    return payload


if __name__ == "__main__":
    try:
        print(run(), flush=True)
    except Exception as exc:
        _log(f"FAILED: {exc}")
        traceback.print_exc()
        raise
