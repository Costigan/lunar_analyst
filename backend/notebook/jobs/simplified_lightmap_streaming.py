from __future__ import annotations

import numpy as np

from backend.notebook.notebook_helper import LightmapRunConfig
from backend.notebook.notebook_helper import run_lightmap_streaming_raster_job


TIME_START_UTC = "2027-09-01T00:00:00"
TIME_STOP_UTC = "2027-10-01T00:00:00"
TIME_STEP_HOURS = 2.0


def compress_tile(tile_3d: np.ndarray) -> np.ndarray:
    # KEY LINES: Replace these with another reduction/metric as needed.
    tile_2d = tile_3d.mean(axis=0, dtype=np.float32)
    tile_2d = (tile_2d / np.float32(255.0)).astype(np.float32, copy=False)
    return tile_2d


if __name__ == "__main__":
    print(
        run_lightmap_streaming_raster_job(
            config=LightmapRunConfig(
                time_start_utc=TIME_START_UTC,
                time_stop_utc=TIME_STOP_UTC,
                time_step_hours=TIME_STEP_HOURS,
                default_horizons_relative_dir="lighting/horizons",
                default_output_relative_path="lighting/lightmap_streaming_mean_20270901_20271001_2h.tif",
                output_subkind="lightmap_streaming_time_mean",
                output_dtype="float32",
                output_nodata=-9999.0,
                buffer_count=6,
                poll_timeout_ms=250,
                patch_width=128,
                patch_height=128,
            ),
            tile_transform=compress_tile,
        ),
        flush=True,
    )
