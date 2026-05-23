import marimo

__generated_with = "0.17.3"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # MoonLayers: New Features Demo

    This demo showcases the newly implemented features:

    1. **Layer Ordering Controls**: Reorder layers using ↑/↓ buttons in the layer switcher
    2. **Feature (Vector) Layer Support**: Add vector layers from ArcGIS MapServer
    3. **GeoTIFF/Raster Layer Support**: Add raster data layers

    ## Setup
    """)
    return


@app.cell
def _():
    from moonlayers import MoonMap
    return (MoonMap,)


@app.cell
def _(MoonMap):
    # Create a moon map with layer switcher enabled
    moon_map = MoonMap(
        projection="ESRI:103878",
        wmts={
            "get_capabilities_url": "https://trek.nasa.gov/tiles/Moon/SP/LRO_WAC_Mosaic_SPole60_100mp/1.0.0/WMTSCapabilities.xml",
            "layer": "LRO_WAC_Mosaic_SPole60_100mp",
            "format": "image/png",
            "attributions": "NASA/GSFC/ASU - LRO WAC Mosaic"
        },
        controls={
            "zoom": True,
            "zoom_slider": True,
            "rotate": True,
            "scale_line": True,
            "mouse_position": {
                "proj": "IAU_MOON_GEOG",
                "precision": 4
            },
            "fullscreen": True
        },
        layer_switcher=True,  # Enable layer switcher with ordering controls
        view={
            "center": [0, 0],
            "zoom": 3,
            "rotation": 0.0
        }
    )
    return (moon_map,)


@app.cell
def _(moon_map):
    # Display the map
    moon_map
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Feature 1: Layer Ordering Controls

    After adding multiple layers (see below), you can reorder them:

    1. Open the **Layer Switcher** (top right corner)
    2. Look for the **↑** and **↓** buttons next to each Trek layer
    3. Click **↑** to move a layer up (make it more visible/on top)
    4. Click **↓** to move a layer down (push it back)

    The base layer always stays at the bottom and cannot be moved.

    ### Test it:
    Let's add a few layers to see the ordering controls in action.
    """)
    return


@app.cell
def _(moon_map):
    # Fetch Trek layers
    layers = moon_map.fetch_trek_layers()
    print(f"Total Trek layers available: {len(layers)}")
    return (layers,)


@app.cell
def _(moon_map):
    # Find and add some Mosaic layers
    mosaic_layers = moon_map.search_layers("LRO AND Mosaic AND SPole")
    print(f"Found {len(mosaic_layers)} LRO Mosaic layers")

    # Add first 2 mosaic layers
    for i, layer in enumerate(mosaic_layers[:2]):
        print(f"\nAdding layer {i+1}: {layer['title']}")
        print(f"  Service types: {layer.get('serviceTypes', [])}")
        moon_map.add_layer(layer['item_UUID'])
    return


@app.cell
def _(mo):
    mo.md(r"""
    **Now check the layer switcher!** You should see:
    - Up/down arrows (↑↓) next to each Trek layer
    - Click them to reorder the layers
    - Watch how the visual stacking changes on the map
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Feature 2: Feature (Vector) Layer Support

    Trek layers with `serviceTypes: ["Feature"]` are now supported.
    These are vector layers (points, lines, polygons) served from ArcGIS MapServer.

    Let's find and add some feature layers:
    """)
    return


@app.cell
def _(layers):
    # Find Feature layers
    feature_layers = [l for l in layers if 'Feature' in l.get('serviceTypes', [])]
    print(f"Found {len(feature_layers)} Feature layers in the catalog")

    # Show some examples
    print("\nExample Feature layers:")
    for i, layer in enumerate(feature_layers[:10]):
        print(f"{i+1}. {layer['title']}")
        print(f"   Product: {layer['productLabel']}")
        print(f"   Types: {layer.get('serviceTypes', [])}")
        print()
    return (feature_layers,)


@app.cell
def _(feature_layers, moon_map):
    # Try to add a feature layer
    if feature_layers:
        test_feature = feature_layers[0]
        print(f"Attempting to add feature layer: {test_feature['title']}")
        print(f"Service types: {test_feature.get('serviceTypes', [])}")

        try:
            moon_map.add_layer(test_feature['item_UUID'])
            print("✓ Feature layer added successfully!")
            print("Check the map - you should see vector features overlaid.")
        except Exception as e:
            print(f"✗ Error adding feature layer: {e}")
    else:
        print("No feature layers found to test")
    return


@app.cell
def _(mo):
    mo.md(r"""
    **Feature Layer Details:**
    - Vector features are loaded on-demand based on the current map viewport
    - Styling is automatic based on geometry type (points, lines, polygons)
    - Features support interaction (hover/click for properties)
    - Efficient bbox-based loading strategy for large datasets
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Feature 3: GeoTIFF/Raster Layer Support

    Trek layers with `serviceTypes: ["Raster"]` or `["GeoTIFF"]` are now supported.
    These use Cloud Optimized GeoTIFF (COG) format for efficient streaming.

    Let's find raster layers:
    """)
    return


@app.cell
def _(layers):
    # Find Raster layers
    raster_layers = [l for l in layers if 'Raster' in l.get('serviceTypes', []) or 'GeoTIFF' in l.get('serviceTypes', [])]
    print(f"Found {len(raster_layers)} Raster/GeoTIFF layers in the catalog")

    if raster_layers:
        print("\nExample Raster layers:")
        for i, layer in enumerate(raster_layers[:5]):
            print(f"{i+1}. {layer['title']}")
            print(f"   Product: {layer['productLabel']}")
            print(f"   Types: {layer.get('serviceTypes', [])}")
            print()
    else:
        print("\nNote: Most Trek layers use WMTS (Mosaic) format rather than direct GeoTIFF.")
        print("GeoTIFF support is ready for when such layers are available.")
    return (raster_layers,)


@app.cell
def _(moon_map, raster_layers):
    # Try to add a raster layer if available
    if raster_layers:
        test_raster = raster_layers[0]
        print(f"Attempting to add raster layer: {test_raster['title']}")

        try:
            moon_map.add_layer(test_raster['item_UUID'])
            print("✓ Raster layer added successfully!")
        except Exception as e:
            print(f"✗ Error adding raster layer: {e}")
            print("This may occur if the GeoTIFF URL pattern is different than expected.")
    else:
        print("No raster layers found to test")
        print("\nThe system tries multiple URL patterns:")
        print("  - https://trek.nasa.gov/tiles/Moon/SP/{productLabel}/data.tif")
        print("  - https://trek.nasa.gov/moon/pds/GeoTiffProducts/{productLabel}.tif")
        print("  - https://trek.nasa.gov/tiles/Moon/{productLabel}.tif")
    return


@app.cell
def _(mo):
    mo.md(r"""
    **GeoTIFF Layer Details:**
    - Uses WebGL rendering for efficient performance
    - Supports Cloud Optimized GeoTIFF (COG) format
    - Automatic pyramided overview loading
    - Multi-band imagery support
    - Efficient partial reads (only loads visible tiles)
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Testing Layer Order Persistence

    The layer order is now part of the saved state.
    Let's test saving and restoring:
    """)
    return


@app.cell
def _(moon_map):
    # Get current state with layer order
    state = moon_map.get_map_state()
    print("Current map state:")
    print(f"  Active layers: {state['active_layers']}")
    print(f"  Layer order: {[l for l in state['active_layers']]}")
    return (state,)


@app.cell
def _(mo, state):
    mo.md(f"""
    **Layer order in state:**

    The `active_layers` array preserves the order from bottom to top:
    - First item: bottom layer (rendered first)
    - Last item: top layer (rendered last, most visible)

    Current order: {state['active_layers']}
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Summary of New Features

    ### ✓ Layer Ordering Controls
    - **Location**: Layer switcher (top right)
    - **Controls**: ↑ (move up) and ↓ (move down) buttons
    - **Function**: Reorder layers visually
    - **State**: Order is saved in `active_layers` array
    - **Base layer**: Always stays at bottom

    ### ✓ Feature (Vector) Layer Support
    - **Source**: ArcGIS MapServer REST API
    - **Format**: GeoJSON
    - **Strategy**: Bbox-based loading (efficient for large datasets)
    - **Styling**: Automatic based on geometry type
    - **Features**: Points, lines, polygons with attributes

    ### ✓ GeoTIFF/Raster Layer Support
    - **Source**: Cloud Optimized GeoTIFF (COG)
    - **Rendering**: WebGL for performance
    - **Features**: Pyramided overviews, multi-band support
    - **Loading**: Efficient tiled/partial reads
    - **URL patterns**: Multiple fallback patterns tried

    ## Next Steps

    1. **Test layer ordering** by adding multiple layers and using ↑↓ buttons
    2. **Search for feature layers** using the search panel (left side)
    3. **Explore different layer types** in the Trek catalog
    4. **Save and restore** map states with proper layer ordering

    ## API Changes

    No breaking changes - all existing code continues to work.
    The new features are automatic based on layer `serviceTypes`.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Search Tips for Finding Different Layer Types

    **To find Mosaic (WMTS) layers:**
    ```python
    moon_map.search_layers("Mosaic")
    ```

    **To find Feature layers:**
    ```python
    feature_layers = [l for l in moon_map.fetch_trek_layers()
                      if 'Feature' in l.get('serviceTypes', [])]
    ```

    **To find Raster layers:**
    ```python
    raster_layers = [l for l in moon_map.fetch_trek_layers()
                     if 'Raster' in l.get('serviceTypes', [])]
    ```
    """)
    return


if __name__ == "__main__":
    app.run()
