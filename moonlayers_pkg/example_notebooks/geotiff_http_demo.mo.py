import marimo

__generated_with = "0.16.5"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
        # GeoTIFF Demo - HTTP Server Version
        
        This demo shows how to load GeoTIFF from an HTTP URL (recommended approach).
        
        ## Setup
        
        1. **Start HTTP server** in another terminal:
           ```bash
           python -m http.server 8000 --directory .
           ```
        
        2. **Run this notebook**
        
        ## Why HTTP is better
        
        - ✅ Full tile streaming support (no failures)
        - ✅ Range requests work properly
        - ✅ No browser memory limits
        - ✅ Fast initial load (only visible tiles downloaded)
        """
    )
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
    # Create the map widget
    moon_map = MoonMap()

    # Add GeoTIFF from HTTP URL (localhost server)
    # Make sure you started: python -m http.server 8000 --directory .
    hillshade_layer_id = moon_map.add_geotiff(
        'http://localhost:8000/data/pr_repositioning_hillshade.tif',
        layer_id='hillshade_http',
        name='PR Hillshade (HTTP)',
        opacity=0.8
    )
    print(f"Added layer: {hillshade_layer_id}")
    return (moon_map, hillshade_layer_id)


@app.cell
def _(moon_map):
    # Display the map
    moon_map
    return


@app.cell
def _(mo):
    mo.md(
        r"""
        ## Notes
        
        - If the layer doesn't appear, check that the HTTP server is running
        - Open http://localhost:8000/data/pr_repositioning_hillshade.tif in a browser to verify it's accessible
        - Check the browser console for any errors
        """
    )
    return


if __name__ == "__main__":
    app.run()
