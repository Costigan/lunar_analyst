import marimo

__generated_with = "0.11.0"
app = marimo.App(width="medium")


@app.cell
def __():
    from pathlib import Path
    from backend.notebook.client import NotebookClient
    return NotebookClient, Path


@app.cell
def __(Path):
    # Update these for your environment before running the next cells.
    API_BASE_URL = "http://127.0.0.1:8000"
    CLIENT_NAME = "marimo-demo"
    SCENARIO_ROOT = "marimo_demo"
    SCENARIO_NAME = "Marimo Demo Scenario"
    SCENARIO_OWNER = "notebook_user"
    GEOTIFF_PATH = Path("/e/lunar_analyst_scenarios/marimo_demo/outputs/example.tif")
    return (
        API_BASE_URL,
        CLIENT_NAME,
        GEOTIFF_PATH,
        SCENARIO_NAME,
        SCENARIO_OWNER,
        SCENARIO_ROOT,
    )


@app.cell
def __(
    API_BASE_URL,
    CLIENT_NAME,
    GEOTIFF_PATH,
    NotebookClient,
    SCENARIO_NAME,
    SCENARIO_OWNER,
    SCENARIO_ROOT,
):
    notebook = NotebookClient.open_session(
        base_url=API_BASE_URL,
        client_name=CLIENT_NAME,
        timeout_seconds=60.0,
    )
    scenario = notebook.create_scenario(
        scenario_root=SCENARIO_ROOT,
        name=SCENARIO_NAME,
        owner=SCENARIO_OWNER,
    )
    scenario_id = scenario["scenario_id"]
    roundtrip = notebook.import_geotiff_create_layer_and_zoom(
        scenario_id=scenario_id,
        source_path=str(GEOTIFF_PATH),
        title=f"Notebook Raster: {GEOTIFF_PATH.name}",
        kind="analysis",
        subkind="notebook_output",
        bypass_cog=True,
        z_index=50,
        padding_px=48,
    )
    notebook.close()

    result = {
        "scenario_id": scenario_id,
        "product_id": roundtrip["product"]["product_id"],
        "source_file_id": roundtrip["file_id"],
        "layer_id": roundtrip["layer"]["layer_id"],
        "map_zoom_event": roundtrip["map_zoom"]["event"],
    }
    result
    return result


@app.cell
def __(result):
    print("Loaded GeoTIFF into map layer state and queued map zoom:")
    print(result)
    print("Open /lunar_analyst/.")
    print("If needed, reload the page so new scenarios appear in the explorer.")
    print(f"Then select scenario {result['scenario_id']} to view the new layer and zoom.")
    return


if __name__ == "__main__":
    app.run()
