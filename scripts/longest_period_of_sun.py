import marimo

__generated_with = "0.17.3"
app = marimo.App(width="medium")


@app.cell
def _():
    from __future__ import annotations

    import numpy as np

    from backend.notebook.notebook_helper import LightmapRunConfig
    from backend.notebook.notebook_helper import run_lightmap_streaming_raster_job


    TIME_START_UTC = "2027-09-01T00:00:00"
    TIME_STOP_UTC = "2028-03-01T00:00:00"
    TIME_STEP_HOURS = 2.0


    def compress_tile(tile_3d: np.ndarray) -> np.ndarray:
        """
        Identifies periods of time for each pixel where there is sun (value > 0) 
        and returns the duration of the longest such period in hours.
    
        Args:
            tile_3d: A 3D numpy array of shape (Time, Height, Width).
        
        Returns:
            A 2D numpy array of shape (Height, Width) containing the duration in hours.
        """
        # tile_3d shape is expected to be (Time, Height, Width) based on usage of axis=0 in reference.
        T, H, W = tile_3d.shape
    
        # Initialize max_run and current_run accumulators
        max_run = np.zeros((H, W), dtype=np.int32)
        current_run = np.zeros((H, W), dtype=np.int32)
    
        # Iterate through time steps to track runs
        for t in range(T):
            # Boolean mask (0 or 1) for the current time step where sun is present
            is_sunny = tile_3d[t] > 0
        
            # Increment all runs by 1
            current_run += 1
        
            # Multiply by the mask:
            # - If sunny (1): keeps the incremented value
            # - If not sunny (0): resets to 0
            current_run *= is_sunny
        
            # Update max_run with the current run lengths
            np.maximum(max_run, current_run, out=max_run)
        
        # Convert steps to hours (float32 as requested)
        max_duration_hours = max_run.astype(np.float32) * TIME_STEP_HOURS
    
        return max_duration_hours

    print(
        run_lightmap_streaming_raster_job(
    	config=LightmapRunConfig(
    	    time_start_utc=TIME_START_UTC,
    	    time_stop_utc=TIME_STOP_UTC,
    	    time_step_hours=TIME_STEP_HOURS,
    	    default_horizons_relative_dir="lighting/horizons",
    	    default_output_relative_path="lighting/lightmap_longest_sun_duration_20270901_20280301.tif",
    	    output_subkind="lightmap_longest_sun_duration",
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


    return


if __name__ == "__main__":
    app.run()
