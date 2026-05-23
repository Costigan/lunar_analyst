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
    # MoonLayers: Trek Layer Search Demo

    This demo shows how to search and add layers from NASA's Moon Trek catalog.

    ## Features Demonstrated

    - **Layer Search UI**: Interactive search panel on the left side of the map
    - **Boolean Search**: Use AND, OR, NOT operators to find specific layers
    - **Programmatic Search**: Search layers from Python code
    - **Add/Remove Layers**: Add layers from search UI or Python code
    - **State Management**: Save and restore map state including layers
    """
    )
    return


@app.cell
def _():
    from moonlayers import MoonMap
    return (MoonMap,)


@app.cell
def _(mo):
    mo.md(r"""## Create Moon Map with Layer Search""")
    return


@app.cell
def _(MoonMap):
    # Create a moon map with base layer
    # The search panel will appear on the left side
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
            "overview_map": False,
            "fullscreen": True
        },
        layer_switcher=True,
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
    mo.md(
        r"""
    ## Using the Search UI

    Look for the **"Search Trek Layers"** panel on the left side of the map above.

    ### Search Examples:
    - Type `Artemis` to find Artemis-related layers
    - Type `LRO LOLA` to find LOLA elevation data
    - Type `Artemis AND Mosaic` to find Artemis mosaics
    - Type `(Artemis OR Apollo) AND -crater` for Artemis or Apollo but not crater-related

    ### Using the Search Panel:
    1. Type your search in the input field
    2. Results appear below with layer titles
    3. Hover over a result to see the layer ID
    4. Click the **+** button to add a layer to the map
    5. Right-click a result to open it on the Trek website

    Added layers appear in the layer switcher (top right).
    """
    )
    return


@app.cell
def _(mo):
    mo.md(r"""## Programmatic Layer Search""")
    return


@app.cell
def _(moon_map):
    # Fetch the Trek layer catalog
    # This is cached after the first call
    layers = moon_map.fetch_trek_layers()
    print(f"Trek catalog contains {len(layers)} layers")
    return


@app.cell
def _(moon_map):
    # Search for specific layers
    artemis_layers = moon_map.search_layers("Artemis")
    print(f"Found {len(artemis_layers)} Artemis layers")

    # Show the first few
    for l in artemis_layers[:5]:
        print(f"  - {l['title']} (ID: {l['item_UUID']})")
    return artemis_layers, l


@app.cell
def _(l, moon_map):
    # Complex search with boolean operators
    endurance_layers = moon_map.search_layers("Endurance AND Path")
    print(f"Found {len(endurance_layers)} Endurance path layers")

    for lalyer in endurance_layers:
        print(f"  - {l['title']}")
        print(f"    Label: {l['productLabel']}")
    return


@app.cell
def _(mo):
    mo.md(r"""## Programmatically Add/Remove Layers""")
    return


@app.cell
def _(artemis_layers, moon_map):
    # Add a layer by UUID or productLabel
    if artemis_layers:
        # Add the first Artemis layer
        layer_to_add = artemis_layers[0]
        print(f"Adding layer: {layer_to_add['title']}")
        moon_map.add_layer(layer_to_add['productLabel'])
    return


@app.cell
def _(mo):
    mo.md(r"""Check the layer switcher - the Artemis layer should now appear!""")
    return


@app.cell
def _():
    # Remove the layer
    # Uncomment to test:
    # moon_map.remove_layer(layer_to_add['item_UUID'])
    # print(f"Removed layer: {layer_to_add['title']}")

    # You can also remove by productLabel
    # moon_map.remove_layer(layer_to_add['productLabel'])
    pass
    return


@app.cell
def _(mo):
    mo.md(r"""## View Active Layers""")
    return


@app.cell
def _(moon_map):
    # Get list of currently active layers
    active = moon_map.active_layers
    print(f"Currently active layers: {len(active)}")
    for layer_id in active:
        print(f"  - {layer_id}")
    return


@app.cell
def _(mo):
    mo.md(r"""## Save and Restore State""")
    return


@app.cell
def _(moon_map):
    # Save the current state
    state = moon_map.get_map_state()
    print("Saved state:")
    print(f"  Projection: {state['projection']}")
    print(f"  View: {state['view']}")
    print(f"  Active layers: {len(state['active_layers'])}")
    return (state,)


@app.cell
def _(mo, state):
    # Display the state (can be saved to file)
    mo.json(state)
    return


@app.cell
def _():
    # To restore state later:
    # moon_map.set_map_state(state)
    # This will:
    # - Restore the view (center, zoom, rotation)
    # - Remove current layers and add layers from saved state
    # - Restore control settings
    pass
    return


@app.cell
def _(mo):
    mo.md(
        r"""
    ## Search Pattern Syntax

    The search supports these operators:

    - **AND** (or `&`): Both terms must match
      - Example: `Artemis AND Mosaic`

    - **OR** (or `|`): Either term must match
      - Example: `Artemis OR Apollo`

    - **NOT** (or `-`): Term must not match
      - Example: `LRO NOT crater`
      - Example: `LRO -crater`

    - **Parentheses**: Group complex expressions
      - Example: `(Artemis OR Apollo) AND Mosaic`
      - Example: `(Endurance OR Path) AND -crater`

    Search is case-insensitive and matches against:
    - `productLabel`: Internal layer name
    - `title`: Display name
    - `description`: Full layer description
    """
    )
    return


@app.cell
def _(mo):
    mo.md(r"""## Example: Search for LOLA Elevation Data""")
    return


@app.cell
def _(moon_map):
    # Find LOLA DEM layers
    lola_layers = moon_map.search_layers("LOLA AND DEM")
    print(f"Found {len(lola_layers)} LOLA DEM layers")

    for layer in lola_layers[:10]:  # Show first 10
        print(f"  - {layer['title']}")
        service_types = layer.get('serviceTypes', [])
        print(f"    Service types: {', '.join(service_types)}")
    return


@app.cell
def _(mo):
    mo.md(
        r"""
    ## Tips

    1. **Performance**: Don't add too many layers at once. Keep it to 3-5 visible layers.

    2. **Layer Switcher**: Use the layer switcher (top right) to:
       - Toggle layer visibility
       - Adjust layer opacity
       - See layer names

    3. **Search Panel**: The search panel is collapsible - click the header to collapse/expand.

    4. **Right-Click**: Right-click any search result to view it on the Trek website.

    5. **Layer IDs**: Hover over search results to see the layer UUID in a tooltip.

    6. **State Persistence**: Use `get_map_state()` and `set_map_state()` to save your work:
       ```python
       import json

       # Save
       state = moon_map.get_map_state()
       with open('my_map.json', 'w') as f:
           json.dump(state, f)

       # Restore
       with open('my_map.json', 'r') as f:
           state = json.load(f)
       moon_map.set_map_state(state)
       ```
    """
    )
    return


@app.cell
def _(moon_map):
    layers2 = moon_map.fetch_trek_layers()
    raster_layers2 = [l for l in layers2 if 'Raster' in l.get('serviceTypes', []) or l.get('productCat1') == 'Raster']
    print(f"Found {len(raster_layers2)} raster layers")
    return


if __name__ == "__main__":
    app.run()
