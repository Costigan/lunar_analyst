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
    # MoonLayers: Lunar South Pole Mapping Demo

    This demo shows the capabilities of the `moonlayers` package for visualizing 
    lunar south-polar data using OpenLayers in a polar stereographic projection.

    ## Features Demonstrated

    - **Simple Setup**: Works with just `MoonMap()` - no configuration needed!
    - **WMTS Base Layer**: Automatic Moon Trek south-polar mosaic
    - **Auto-loading Catalog**: Trek layers load when search UI is opened
    - **Interactive Controls**: Zoom, pan, rotate, scale line, mouse position
    - **Layer Management**: Layer switcher for visibility and opacity control
    - **Measurements**: Distance and area measurements (geodesic on Moon sphere)
    - **Export**: PNG and PDF export functionality
    - **Interactivity**: Feature popups and hover highlights
    """
    )
    return


@app.cell
def _():
    from moonlayers import MoonMap
    return (MoonMap,)


@app.cell
def _(mo):
    mo.md(r"""## Basic Moon Map with WMTS""")
    return


@app.cell
def _(MoonMap):
    # Create a basic moon map - defaults to LRO WAC South Pole Mosaic
    # All you need is MoonMap()!
    # 
    # You can still customize with parameters like:
    # - wmts={...} for different base layers
    # - projection="ESRI:103878" or custom proj4
    # - controls={...} to enable/disable features
    # - view={'center': [x,y], 'zoom': z} for initial position
    moon_map = MoonMap()
    return (moon_map,)


@app.cell
def _(moon_map):
    # Display the map
    moon_map
    return


@app.cell
def _(mo):
    mo.md(r"""## Interactive Controls""")
    return


@app.cell
def _(mo):
    # Create UI controls for interacting with the map
    zoom_slider = mo.ui.slider(0, 8, value=3, label="Zoom Level", step=0.1)
    center_x = mo.ui.number(-1000000, 1000000, value=0, label="Center X", step=10000)
    center_y = mo.ui.number(-1000000, 1000000, value=0, label="Center Y", step=10000)
    rotation_slider = mo.ui.slider(0, 6.28, value=0, label="Rotation (rad)", step=0.1)

    mo.hstack([zoom_slider, center_x, center_y, rotation_slider], justify="start")
    return center_x, center_y, rotation_slider, zoom_slider


@app.cell
def _(center_x, center_y, moon_map, rotation_slider, zoom_slider):
    # Update map view when controls change
    moon_map.set_view(
        center=[center_x.value, center_y.value],
        zoom=zoom_slider.value,
        rotation=rotation_slider.value
    )
    return


@app.cell
def _(mo):
    mo.md(r"""## Map with GeoJSON Overlay Example""")
    return


@app.cell
def _(mo):
    mo.md(
        r"""
    You can add GeoJSON vector layers to display points, lines, or polygons.
    Here's how to add a layer (you'll need to provide a valid GeoJSON URL):
    """
    )
    return


@app.cell
def _():
    # Example: Adding a GeoJSON layer (commented out - provide your own URL)
    # moon_map_with_geojson = MoonMap(
    #     projection="ESRI:103878",
    #     wmts={
    #         "get_capabilities_url": "https://trek.nasa.gov/tiles/Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/WMTSCapabilities.xml",
    #         "layer": "LRO_WAC_Mosaic_Global_303ppd_v02",
    #     },
    #     geojsons=[
    #         {
    #             "url": "https://example.com/landing_sites.geojson",
    #             "layer_id": "landing_sites",
    #             "name": "Apollo Landing Sites",
    #             "style": {
    #                 "stroke": {"color": "#ff0000", "width": 2},
    #                 "fill": {"color": "rgba(255, 0, 0, 0.2)"},
    #                 "image": {
    #                     "type": "circle",
    #                     "radius": 8,
    #                     "fill": "#ff0000",
    #                     "stroke": "#ffffff",
    #                     "strokeWidth": 2
    #                 }
    #             },
    #             "opacity": 0.8,
    #             "visible": True
    #         }
    #     ]
    # )
    return


@app.cell
def _(mo):
    mo.md(r"""## Export Functionality""")
    return


@app.cell
def _(mo):
    export_png_btn = mo.ui.button(label="Export PNG", value=0)
    export_pdf_btn = mo.ui.button(label="Export PDF", value=0)

    mo.hstack([export_png_btn, export_pdf_btn], justify="start")
    return export_pdf_btn, export_png_btn


@app.cell
def _(export_pdf_btn, export_png_btn, moon_map):
    # Handle export button clicks
    if export_png_btn.value > 0:
        moon_map.export_png(scale=2.0)

    if export_pdf_btn.value > 0:
        moon_map.export_pdf(size="A4", dpi=150)
    return


@app.cell
def _(mo):
    mo.md(
        r"""
    ## Event Callbacks

    You can register callbacks to respond to map events:
    """
    )
    return


@app.cell
def _(moon_map):
    # Example: Register event callbacks
    clicked_features = []

    def on_feature_click(feature, coord):
        print(f"Feature clicked at {coord}: {feature}")
        clicked_features.append(feature)

    def on_extent_change(center, zoom, rotation, extent):
        if zoom is not None:
            print(f"View changed: zoom={zoom:.2f}, center={center}")
        else:
            print(f"View changed: center={center}")

    def on_export_done(kind, data):
        print(f"Export complete: {kind}, data length: {len(data)} bytes")
        # You can save the base64 data or trigger a download

    moon_map.on_click_feature(on_feature_click)
    moon_map.on_extent_changed(on_extent_change)
    moon_map.on_export_complete(on_export_done)
    return


@app.cell
def _(mo):
    mo.md(
        r"""
    ## Advanced: Custom Projection

    You can use a custom proj4 string for specialized projections:
    """
    )
    return


@app.cell
def _():
    # Example: Custom projection (commented out)
    # custom_map = MoonMap(
    #     projection={
    #         "code": "CUSTOM:MOON_SOUTH",
    #         "proj4": "+proj=stere +lat_0=-85.42088 +lon_0=31.6218 +k=1 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs",
    #         "extent": [-2000000, -2000000, 2000000, 2000000]
    #     },
    #     wmts={
    #         "get_capabilities_url": "https://trek.nasa.gov/tiles/Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/WMTSCapabilities.xml",
    #         "layer": "LRO_WAC_Mosaic_Global_303ppd_v02",
    #     }
    # )
    return


@app.cell
def _(mo):
    mo.md(
        r"""
    ## Notes

    - **Projection**: ESRI:103878 is Moon South Pole Stereographic
    - **WMTS**: Loads tiles from NASA Moon Trek service
    - **Coordinates**: Mouse position shows lat/lon in IAU Moon geographic CRS
    - **Scale**: Scale line shows distances in meters/km using Moon's radius (1,737,400 m)
    - **Browser Cache**: Tile caching is handled by the browser
    - **Export**: CORS must be enabled on tile servers for export to work

    ## Troubleshooting

    If the map doesn't load:
    1. Check browser console for errors
    2. Verify Moon Trek service is accessible
    3. Ensure CORS is enabled for tile sources
    4. Try refreshing the page
    """
    )
    return


if __name__ == "__main__":
    app.run()
