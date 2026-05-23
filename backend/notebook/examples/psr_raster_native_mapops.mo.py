import marimo

__generated_with = "0.11.0"
app = marimo.App(width="medium")


@app.cell
def __():
    from backend.jobs.handlers import JobHandlers
    return (JobHandlers,)


@app.cell
def __():
    # Set RUN_JOB=True when you're ready to execute.
    RUN_JOB = False

    # These paths should point at an existing scenario DEM + precomputed horizons.
    SCENARIO_ID = "test_scenario"
    SCENARIO_ROOT_DIR = None  # e.g. r"D:/lunar_analyst_scenarios/test_scenario" or None to use configured resolver
    DEM_PATH = r"/e/lunar_analyst_scenarios/test_scenario/dem.tif"
    HORIZONS_DIR = r"/e/lunar_analyst_scenarios/test_scenario/lighting/horizons"
    OUTPUT_PATH = r"/e/lunar_analyst_scenarios/test_scenario/lighting/examples/psr.tif"
    return DEM_PATH, HORIZONS_DIR, OUTPUT_PATH, RUN_JOB, SCENARIO_ID, SCENARIO_ROOT_DIR


@app.cell
def __(DEM_PATH, HORIZONS_DIR, OUTPUT_PATH, SCENARIO_ID, SCENARIO_ROOT_DIR, mo):
    mo.md(
        f"""
        **Configured inputs**

        - `scenario_id`: `{SCENARIO_ID}`
        - `scenario_root_dir`: `{SCENARIO_ROOT_DIR}`
        - `dem_path`: `{DEM_PATH}`
        - `horizons_dir`: `{HORIZONS_DIR}`
        - `output_path`: `{OUTPUT_PATH}`
        """
    )
    return


@app.cell
def __(JobHandlers, DEM_PATH, HORIZONS_DIR, OUTPUT_PATH, RUN_JOB, SCENARIO_ID, SCENARIO_ROOT_DIR):
    if not RUN_JOB:
        result = {
            "status": "not_run",
            "message": "Set RUN_JOB=True to generate a PSR raster using native moonlib mapops.",
            "output_path": OUTPUT_PATH,
        }
    else:
        result = JobHandlers.generate_psr_raster(
            scenario_id=SCENARIO_ID,
            scenario_root_dir=SCENARIO_ROOT_DIR,
            dem_path=DEM_PATH,
            horizons_dir=HORIZONS_DIR,
            output_path=OUTPUT_PATH,
        ).model_dump()
    result
    return (result,)


@app.cell
def __(result):
    print(result)
    return


if __name__ == "__main__":
    app.run()
