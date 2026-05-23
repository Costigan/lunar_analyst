import marimo

__generated_with = "0.11.0"
app = marimo.App(width="medium")


@app.cell
def __():
    import numpy as np
    from backend.notebook.notebook_helper import LightmapRunConfig
    from backend.notebook.notebook_helper import run_lightmap_signal_streaming_raster_job
    from backend.worker.lightmap_streaming import TemporalSignalSpecPy
    return (
        LightmapRunConfig,
        TemporalSignalSpecPy,
        np,
        run_lightmap_signal_streaming_raster_job,
    )


@app.cell
def __():
    # Set RUN_JOB=True when you're ready to execute.
    RUN_JOB = False

    TIME_START_UTC = "2027-09-01T00:00:00"
    TIME_STOP_UTC = "2027-10-01T00:00:00"
    TIME_STEP_HOURS = 2.0

    # Example custom metric:
    # Max contiguous duration where sun_fraction >= MIN_SUN_FRACTION_U8
    # AND earth_center_margin_deg >= EARTH_CENTER_MARGIN_THRESHOLD_DEG
    MIN_SUN_FRACTION_U8 = 32
    EARTH_CENTER_MARGIN_THRESHOLD_DEG = 0.0
    DURATION_UNIT = "hours"  # hours | samples

    OUTPUT_RELATIVE_PATH = "lighting/examples/custom_chunked_signal_stream_combined_duration.tif"
    return (
        DURATION_UNIT,
        EARTH_CENTER_MARGIN_THRESHOLD_DEG,
        MIN_SUN_FRACTION_U8,
        OUTPUT_RELATIVE_PATH,
        RUN_JOB,
        TIME_START_UTC,
        TIME_STEP_HOURS,
        TIME_STOP_UTC,
    )


@app.cell
def __(
    DURATION_UNIT,
    EARTH_CENTER_MARGIN_THRESHOLD_DEG,
    MIN_SUN_FRACTION_U8,
    TIME_STEP_HOURS,
    np,
):
    class MaxContiguousSunEarthReducer:
        """Chunked reducer example for SignalStream mode.

        Expected channels:
        - channel 0: sun_fraction_u8 (float32 in mixed payloads, still on 0..255 scale)
        - channel 1: earth_center_margin_deg_f32
        """

        def __init__(self) -> None:
            self._sun_threshold = np.float32(MIN_SUN_FRACTION_U8)
            self._earth_threshold = np.float32(EARTH_CENTER_MARGIN_THRESHOLD_DEG)
            self._step_value = (
                np.float32(TIME_STEP_HOURS)
                if str(DURATION_UNIT).strip().lower() == "hours"
                else np.float32(1.0)
            )

        def init_tile_state(self, tile_meta):
            h = int(tile_meta.height)
            w = int(tile_meta.width)
            return {
                "current_run": np.zeros((h, w), dtype=np.float32),
                "max_run": np.zeros((h, w), dtype=np.float32),
            }

        def update(self, state, tile_chunk, tile_meta):
            # tile_chunk shape: [time, channel, h, w]
            sun = tile_chunk[:, 0].astype(np.float32, copy=False)
            earth = tile_chunk[:, 1].astype(np.float32, copy=False)
            for t in range(tile_chunk.shape[0]):
                mask = (sun[t] >= self._sun_threshold) & (earth[t] >= self._earth_threshold)
                state["current_run"][mask] += self._step_value
                state["current_run"][~mask] = np.float32(0.0)
                np.maximum(state["max_run"], state["current_run"], out=state["max_run"])
            return state

        def finalize(self, state, tile_meta):
            return state["max_run"]

    reducer = MaxContiguousSunEarthReducer()
    reducer
    return (reducer,)


@app.cell
def __(TemporalSignalSpecPy):
    signals = [
        TemporalSignalSpecPy(signal="sun_fraction_u8"),
        TemporalSignalSpecPy(signal="earth_center_margin_deg_f32"),
    ]
    signals
    return (signals,)


@app.cell
def __(
    LightmapRunConfig,
    OUTPUT_RELATIVE_PATH,
    RUN_JOB,
    TIME_START_UTC,
    TIME_STEP_HOURS,
    TIME_STOP_UTC,
    reducer,
    run_lightmap_signal_streaming_raster_job,
    signals,
):
    if not RUN_JOB:
        result = {
            "status": "not_run",
            "message": "Set RUN_JOB=True to run the custom chunked signal-stream reducer.",
            "output_relative_path": OUTPUT_RELATIVE_PATH,
        }
    else:
        result = run_lightmap_signal_streaming_raster_job(
            config=LightmapRunConfig(
                time_start_utc=TIME_START_UTC,
                time_stop_utc=TIME_STOP_UTC,
                time_step_hours=TIME_STEP_HOURS,
                default_horizons_relative_dir="lighting/horizons",
                default_output_relative_path=OUTPUT_RELATIVE_PATH,
                output_subkind="custom_chunked_signal_stream",
                output_dtype="float32",
                output_nodata=-9999.0,
            ),
            signals=signals,
            reducer=reducer,
        )
    result
    return (result,)


if __name__ == "__main__":
    app.run()
