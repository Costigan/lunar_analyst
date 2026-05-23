import marimo

__generated_with = "0.11.0"
app = marimo.App(width="medium")


@app.cell
def __():
    from backend.notebook.notebook_helper import LightmapRunConfig
    from backend.notebook.notebook_helper import run_lightmap_native_reduction_raster_job
    return LightmapRunConfig, run_lightmap_native_reduction_raster_job


@app.cell
def __():
    # Set RUN_JOB=True when you're ready to execute.
    RUN_JOB = False

    TIME_START_UTC = "2027-09-01T00:00:00"
    TIME_STOP_UTC = "2027-10-01T00:00:00"
    TIME_STEP_HOURS = 2.0

    EARTH_THRESHOLD_DEG = 0.0
    THRESHOLD_REFERENCE = "lower_limb_margin"  # center_margin | lower_limb_margin | upper_limb_margin
    DURATION_UNIT = "hours"  # hours | samples

    OUTPUT_RELATIVE_PATH = "lighting/examples/earth_above_terrain_duration_native_reduce.tif"
    return (
        DURATION_UNIT,
        EARTH_THRESHOLD_DEG,
        OUTPUT_RELATIVE_PATH,
        RUN_JOB,
        THRESHOLD_REFERENCE,
        TIME_START_UTC,
        TIME_STEP_HOURS,
        TIME_STOP_UTC,
    )


@app.cell
def __(DURATION_UNIT, EARTH_THRESHOLD_DEG, THRESHOLD_REFERENCE):
    reducers = [
        {
            "kind": "cumulative_duration_where",
            "unit": DURATION_UNIT,
            "margin_predicate": {
                "signal": "earth_center_margin_deg_f32",
                "reference": THRESHOLD_REFERENCE,
                "threshold_value": float(EARTH_THRESHOLD_DEG),
                "greater_than_or_equal": True,
            },
            "output_type": "float32",
        }
    ]
    reducers
    return (reducers,)


@app.cell
def __(
    LightmapRunConfig,
    OUTPUT_RELATIVE_PATH,
    RUN_JOB,
    TIME_START_UTC,
    TIME_STEP_HOURS,
    TIME_STOP_UTC,
    reducers,
    run_lightmap_native_reduction_raster_job,
):
    if not RUN_JOB:
        result = {
            "status": "not_run",
            "message": "Set RUN_JOB=True to generate the Earth-above-terrain duration raster.",
            "output_relative_path": OUTPUT_RELATIVE_PATH,
        }
    else:
        result = run_lightmap_native_reduction_raster_job(
            config=LightmapRunConfig(
                time_start_utc=TIME_START_UTC,
                time_stop_utc=TIME_STOP_UTC,
                time_step_hours=TIME_STEP_HOURS,
                default_horizons_relative_dir="lighting/horizons",
                default_output_relative_path=OUTPUT_RELATIVE_PATH,
                output_subkind="earth_above_terrain_duration_native_reduce",
                output_dtype="float32",
                output_nodata=-9999.0,
            ),
            reducers=reducers,
        )
    result
    return (result,)


if __name__ == "__main__":
    app.run()
