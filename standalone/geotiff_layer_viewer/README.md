# GeoTIFF Layer Viewer

Linux-only stand-alone desktop app for stacking GeoTIFF layers and scrubbing time-series frames.

## Run

```bash
.venv/bin/python -m standalone.geotiff_layer_viewer
```

## Features

- Add/remove/reorder image layers.
- Add time-series layers from selected files or directories.
- Global datetime slider with floor-time frame selection (`<=` selected time).
- Per-layer opacity controls.
- In-memory LRU cache for decoded frames.
- Persistent layer state saved at:
  - `standalone/geotiff_layer_viewer/geotiff_layer_viewer_state.json`
