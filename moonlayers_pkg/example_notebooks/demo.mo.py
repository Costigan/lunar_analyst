import marimo

__generated_with = "0.17.3"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import numpy as np
    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # GeoTIFF Layer Demo

    This demo shows how to add custom GeoTIFF raster layers to the MoonMap widget.

    ## Features
    - **Simple creation**: Just `MoonMap()` with sensible defaults
    - **Auto-loading**: Trek layer catalog loads when search UI is opened
    - **Integrated HTTP server**: Local files automatically served via background server
    - **Load from URL**: Cloud Optimized GeoTIFF (COG) support
    - **Load from file**: Local files from anywhere on filesystem

    ## How It Works
    When you add a GeoTIFF from a local file path, MoonMap automatically:
    1. Starts an HTTP server in a background thread (first time only)
    2. Registers the file path with the server
    3. Converts the file path to a `http://127.0.0.1:PORT/geotiff_N` URL
    4. Serves the file with proper HTTP range request support for tile streaming

    This avoids the blob URL limitations that cause tile loading failures.
    """)
    return


@app.cell
def _():
    from moonlayers import MoonMap
    return (MoonMap,)


@app.cell
def _(mo):
    mo.md(r"""## Create the Map Widget""")
    return


@app.cell
def _(MoonMap):
    # Create the map widget - no configuration needed!
    # Uses default south polar base layer
    moon_map = MoonMap()
    return (moon_map,)


@app.cell
def _(moon_map):
    # Display the map FIRST
    moon_map
    return


@app.cell
def _(moon_map):
    # The widget queues GeoTIFF layer additions until it's ready
    # No need to wait explicitly!
    moon_map.add_geotiff('data/malapert-hillshade.tif', layer_id='hillshade')
    moon_map.add_geotiff('data/malapert-psr-tiled.tif', layer_id='psr')
    moon_map.add_geotiff('data/malapert-landing-sites2-tiled.tif', layer_id='landing')
    moon_map.add_geotiff('data/malapert-mission-duration-tiled.tif', layer_id='duration')
    return


@app.cell
def _():
    #moon_map.add_geotiff('d:/datasets/viper_v71_2024_medium/other/hillshade.tif', layer_id='viper_1')
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
