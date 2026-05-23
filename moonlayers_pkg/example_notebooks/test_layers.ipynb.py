import marimo

__generated_with = "0.16.5"
app = marimo.App(width="medium")


@app.cell
def _():
    from moonlayers import MoonMap

    moon_map = MoonMap(projection="ESRI:103878", layer_switcher=True)

    # Add feature layer by productLabel (cleaner!)
    moon_map.add_layer('A3_Named_regions_SP')

    # Add mosaic layers by productLabel
    moon_map.add_layer('LRO_WAC_Mosaic_SPole60_100mp')
    #moon_map.add_layer('LRO_LOLA_ClrShade_SPole_Mosaic')

    moon_map
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
