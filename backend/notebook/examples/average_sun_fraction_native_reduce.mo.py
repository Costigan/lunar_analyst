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

    OUTPUT_RELATIVE_PATH = "lighting/examples/avg_sun_fraction_native_reduce.tif"
    OUTPUT_NORMALIZED_01 = True  # True => values in [0,1]
    return (
        OUTPUT_NORMALIZED_01,
        OUTPUT_RELATIVE_PATH,
        RUN_JOB,
        TIME_START_UTC,
        TIME_STEP_HOURS,
        TIME_STOP_UTC,
    )


@app.cell
def __(OUTPUT_NORMALIZED_01):
    reducers = [
        {
            "kind": "average_sun_fraction",
            "output_normalized_01": bool(OUTPUT_NORMALIZED_01),
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
            "message": "Set RUN_JOB=True to generate the average sun fraction raster.",
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
                output_subkind="avg_sun_fraction_native_reduce",
                output_dtype="float32",
                output_nodata=-9999.0,
            ),
            reducers=reducers,
        )
    result
    return (result,)


if __name__ == "__main__":
    app.run()
