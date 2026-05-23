import marimo

__generated_with = "0.17.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    from moonlayers import MoonMap
    return MoonMap, mo


@app.cell
def _(mo):
    mo.md(r"""## Minimal Widget Readiness Test""")
    return


@app.cell
def _(MoonMap):
    # Create widget with minimal config - no GeoTIFF loading
    moon_map = MoonMap()
    return (moon_map,)


@app.cell
def _(moon_map):
    # Display widget first
    moon_map
    return


@app.cell
def _(mo, moon_map):
    # Test wait_until_ready with a reasonable timeout
    # Note: In marimo, this may take ~10 seconds due to anywidget sync latency
    # For normal usage, explicit waiting is unnecessary - just call add_geotiff() etc.
    try:
        moon_map.wait_until_ready(timeout=15)
        status = mo.md("✅ **Widget became ready!**")
    except TimeoutError as e:
        status = mo.md(f"❌ **Timeout error**: {e}")
    status
    return


if __name__ == "__main__":
    app.run()
