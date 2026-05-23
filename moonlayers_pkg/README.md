# MoonLayers

MoonLayers is an interactive lunar mapping widget for Marimo and Jupyter, built with OpenLayers and anywidget. It supports advanced lunar projections, WMTS tiles from NASA Moon Trek, dynamic GeoTIFF raster layers, and vector overlays. Designed for robust, zero-configuration use in notebook environments, MoonLayers focuses on reliability, extensibility, and ease of use.

---

## Features

- **Lunar Map Widget**: Interactive, embeddable in Marimo and Jupyter notebooks.
- **Advanced Projections**: Full support for custom lunar projections via proj4.
- **Layer Types**: WMTS, GeoTIFF (COG), GeoJSON, with architecture for future formats (WMS, XYZ, vector tiles).
- **Dynamic GeoTIFF Streaming**: Integrated threaded HTTP server for efficient tile streaming and range requests.
- **Layer Controls**: Interactive ordering, toggling, and management via ol-layerswitcher.
- **Zero Configuration**: Automatic server startup, file registration, and smart defaults for seamless user experience.
- **Bidirectional Communication**: Robust Python ↔ JavaScript state sync and event callbacks.

---

## Quick Start

1. **Install dependencies**
   ```sh
   npm install
   ```
2. **Build frontend assets**
   ## Developing with VS Code

   These settings are included so you can build, run, and debug with one click.

   - Prerequisites: Python 3.8+, Node.js 18+, pip, and the VS Code Python extension.
   - Optional but recommended: a virtual environment (e.g., .venv).

   1) First-time setup
   - Open the folder in VS Code.
   - Select your Python interpreter (Command Palette → "Python: Select Interpreter").
   - Press Ctrl+Shift+B to run the default Build All task. This runs:
      - npm install
      - npm run build
      - python -m build
   - For editable installs during development, run the task Python: install editable (equivalent to pip install -e .). If you need dev tools (pytest, marimo), install the dev extra:

   ```pwsh
   pip install -e .[dev]
   ```

   2) Everyday development
   - Start the background bundler: Terminal → Run Task… → Frontend: watch.
   - Run tests: Run and Debug panel → Python: run pytest.
   - Run the manual script: Run and Debug → Python: manual_test.py.
   - Launch the Marimo demo: Run and Debug → Marimo: south_pole_demo.mo.py.

   3) Troubleshooting
   - If marimo isn’t found, install it (either via the dev extra or directly):

   ```pwsh
   pip install marimo
   ```

   - If npm isn’t found, install Node.js 18+ from https://nodejs.org/.
   - If builds don’t reflect changes, ensure Frontend: watch is running or re-run Frontend: build.

   ```sh
   npm run build
   ```
3. **Build Python package**
   ```sh
   python -m build
   ```
4. **Install locally**
   ```sh
   pip install dist/<your-package>.whl
   # or for development
   pip install -e .
   ```

---

## Usage Example

```python
from moonlayers import MoonMap

# Create and display the widget
map = MoonMap()
display(map)

# Add layers after widget is displayed
map.add_wmts_layer(...)
map.add_geotiff_layer(...)
```

See `examples/` for Marimo and Jupyter demo notebooks.

---

## Architecture Overview

- **moonlayers/geotiff_server.py**: Integrated HTTP server for GeoTIFF streaming, file registry, and range request handling.
- **moonlayers/moon_map.py**: Main widget logic, integrates HTTP server, manages layers and controls.
- **src/**: JavaScript source for widget, layers, controls, interactions, and export (compiled to `moonlayers/static/`).
- **moonlayers/static/**: Compiled frontend assets served by the Python package.
- **tests/**: Unit and manual test scripts for backend and frontend validation.
- **docs_old/**: Comprehensive documentation, guides, and technical notes.

---

## Implementation Highlights

- **Integrated HTTP Server**: Serves local GeoTIFFs via unique URLs, supports HTTP range requests, and cleans up on kernel exit.
- **Widget Initialization Pattern**: Always display the widget before adding layers to ensure frontend initialization.
- **Smart Defaults**: Uses LRO WAC South Pole Mosaic as the default base layer.
- **Extensible Layer System**: Modular design for easy addition of new layer types and features.
- **Frontend Architecture**: Built with OpenLayers, ol-layerswitcher, geotiff.js, and proj4.

---

## Documentation

- See `docs_old/README.md` for API documentation and examples.
- See `docs_old/BUILD.md` for build instructions.
- See `docs_old/MARIMO_USAGE_PATTERN.md` for notebook usage guidance.
- See `docs_old/GEOTIFF_BLOB_URL_LIMITATION.md` and `docs_old/GEOTIFF_HTTP_SERVER_IMPLEMENTATION.md` for technical details.
- Additional guides and troubleshooting in `docs_old/`.

---

## Technical Tradeoffs

- **HTTP Server vs. Data URLs**: Chose integrated HTTP server for reliability and performance.
- **Singleton Server Pattern**: Ensures only one server per process, avoids port conflicts.
- **Localhost Binding**: Prioritizes security and simplicity.
- **Dynamic Port Allocation**: Avoids manual configuration and port conflicts.
- **Traitlets for State Sync**: Used for robust Python ↔ JavaScript communication.

---

## Lessons Learned

- Always display the widget before adding layers.
- Proper HTTP range support is essential for tile streaming.
- Automatic server startup and file registration improve reliability.
- Modular design enables extensibility.

---

## Future Directions

- Caching and compression for HTTP server responses.
- Support for additional layer types (WMS, XYZ, vector tiles).
- Enhanced error handling and user feedback in the UI.
- 3D lunar globe visualization and advanced measurement tools.

---

## License

See `LICENSE` for details.

---

## References

- [OpenLayers](https://openlayers.org/)
- [NASA Moon Trek](https://trek.nasa.gov/moon/)
- [Marimo](https://marimo.io/)
- [Jupyter](https://jupyter.org/)

---

**Status:** Production-ready, robust, and extensible for lunar mapping in notebook environments.
