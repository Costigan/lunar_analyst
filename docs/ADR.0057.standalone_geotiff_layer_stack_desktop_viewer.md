# ADR.0057: Stand-Alone GeoTIFF Layer Stack Desktop Viewer

- Status: Accepted
- Date: 2026-05-15
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0046.remove_windows_support_and_standardize_on_linux.md`

## Context

Lunar Analyst currently focuses on a browser-first React/OpenLayers + FastAPI architecture for scenario-based analysis. A new requirement introduces a separate stand-alone Python desktop program to quickly inspect stacked GeoTIFF layers outside the main web runtime.

The requested desktop tool must support:

- loading multiple GeoTIFF layers rendered bottom-to-top,
- per-layer opacity control,
- adding and removing layers,
- a distinct time-series layer type whose opacity applies to all images in that series,
- adding time-series layers from either selected files or a directory,
- parsing timestamps from filenames such as `sun_image_2027-09-01T02-00-00.tif`,
- one global datetime slider whose range is the union across loaded time-series layers,
- slider snapping behavior that selects each time-series frame with timestamp `<=` selected slider time (latest eligible frame),
- fast redraw when moving the slider for large series (initially about 2100 frames),
- persistence of loaded layers across application restarts,
- Linux-only support.

## Problem

The existing main runtime is not optimized for a light, local, immediate desktop-only layer inspection loop that scientists can use without launching the full web control plane.

A direct naive implementation that reloads GeoTIFF bytes from disk for every slider change will perform poorly for large time-series sets. We need a desktop architecture that provides deterministic layer composition, responsive timestamp scrubbing, and robust persistence while using widely-adopted Python libraries.

## Decision

Implement a Linux-only stand-alone Python desktop application using:

- `PySide6` (Qt for Python) for GUI,
- `rasterio` for GeoTIFF reads and metadata,
- `numpy` for raster array handling,
- Qt `QGraphicsView`/`QPixmap` composition for image presentation,
- a process-local LRU image cache for decoded time-series frames,
- a single JSON state file stored in the same directory as the application entry script.

The application is separate from FastAPI, worker, and Moonlib runtime paths. It does not mutate Lunar Analyst scenario DB state.

## Scope

In scope:

- Stand-alone windowed Linux desktop app.
- Layer model with two layer kinds:
  - single-image layer,
  - time-series layer.
- Layer list management: add, remove, reorder.
- Per-layer opacity control.
- Global time slider and label.
- Time-series loading from files or directory.
- Timestamp extraction and ordered frame indexing.
- Frame selection by floor-time (`<=` slider time).
- LRU caching for decoded/raster-ready frames.
- Persistent app state file in app folder.

Out of scope:

- Integration with FastAPI job system, WebSocket events, or scenario catalog.
- CRS reprojection between mismatched raster grids.
- Editing/writing GeoTIFF output.
- Non-Linux packaging targets.
- Advanced cartographic styling beyond basic grayscale/RGB display and alpha.

## Normative Design

### 1. Runtime and Entry Point

- App runs under Python 3.11 on Linux.
- Entry point module: `standalone/geotiff_layer_viewer/app.py` (runnable via `python -m standalone.geotiff_layer_viewer`).
- Desktop-only event loop via Qt.

### 2. Raster Assumptions and Validation

- All loaded rasters are expected to share dimensions and resolution.
- On add, validate width, height, band count compatibility with current canvas baseline.
- If incompatible, reject load with explicit user-facing error.
- No implicit reprojection or resampling is performed.

### 3. Layer Data Model

`LayerBase` fields:

- `layer_id` (stable UUID string),
- `name` (display label),
- `opacity` (`0.0..1.0`),
- `visible` (bool),
- `z_index` (list order; lower index = lower layer).

`SingleImageLayer` fields:

- `path` (absolute normalized path),
- `raster_signature` (shape + dtype + band count metadata).

`TimeSeriesLayer` fields:

- `series_name` (common filename prefix before timestamp token),
- `frames` (sorted list of `{timestamp_utc, path}`),
- `raster_signature`.

### 4. Time-Series Ingestion

Add flow must provide a distinct UI action separate from single-image add.

Supported inputs:

- explicit multi-file selection,
- directory selection (scan `*.tif`/`*.tiff`).

Timestamp parsing contract:

- Filenames must contain an ISO-like token `YYYY-MM-DDTHH-MM-SS`.
- Token is interpreted as UTC naive timestamp unless future requirement adds timezone support.
- Prefix (for example `sun_image_`) may vary by series; all files in one added series are grouped by user selection scope, not global prefix deduplication.

Validation:

- Ignore files without parseable timestamp, report count and examples.
- Require at least one valid frame to create layer.
- Sort frames ascending by timestamp.

### 5. Global Time Slider Contract

- Single slider controls all time-series layers.
- Slider domain is `[global_min_ts, global_max_ts]` where bounds are the union over every loaded time-series frame timestamp.
- Slider uses continuous datetime mapping (internally integer seconds since epoch for Qt widget compatibility).
- For each time-series layer at slider time `T`, choose frame `max(ts) where ts <= T`.
- If no frame exists with `ts <= T` for a layer, that layer renders as transparent/no frame until slider reaches its first timestamp.
- Changing slider triggers re-evaluation for all time-series layers and redraw.

### 6. Rendering Pipeline

- Composition order strictly follows layer list from bottom to top.
- Each layer applies opacity in compositor order.
- Single-image layer draws fixed raster.
- Time-series layer draws currently selected frame raster.
- Opacity on a time-series layer applies uniformly to whichever frame is active.
- UI should allow drag/drop reorder with immediate redraw.

### 7. LRU Cache Strategy

Purpose: avoid repeated disk decode and conversion while scrubbing slider.

Cache key:

- `(absolute_path, mtime_ns, size_bytes)`.

Cache value:

- raster-ready array (for display) plus minimal metadata needed by renderer.

Policy:

- LRU eviction by item count and/or approximate byte budget.
- Initial default target: configurable, with practical baseline around 1-4 GB depending on raster size.
- Cache lives in-memory per app process (no disk cache in v1).

Performance behavior:

- On slider move, layer frame lookup is O(log n) per series via binary search over sorted timestamps.
- Render path first checks cache, falls back to disk read + decode + cache insert.
- Optional near-neighbor prefetch (`current index +/- k`) may be added as non-blocking optimization if needed.

### 8. Persistence Contract

State file:

- Single JSON file colocated with app entry script directory.
- Suggested name: `geotiff_layer_viewer_state.json`.

Persisted fields:

- ordered layer list,
- each layer kind and parameters,
- opacity/visibility,
- last slider timestamp,
- window geometry (optional but recommended).

Startup behavior:

- Load file if present.
- Drop missing/unreadable paths with warning dialog/log and continue.
- Recompute global slider union from surviving time-series layers.

Save behavior:

- Save on significant state changes and on graceful exit.
- Use atomic write pattern (`.tmp` then rename) to reduce corruption risk.

### 9. UI Surface

Minimum controls:

- Layer panel with:
  - ordered rows,
  - remove action,
  - per-layer opacity slider,
  - reorder interaction.
- Actions:
  - `Add Image Layer...`
  - `Add Time-Series Layer (Files)...`
  - `Add Time-Series Layer (Directory)...`
- Global timeline:
  - datetime slider,
  - current datetime readout.
- Main canvas for composed raster display.

### 10. Error Handling and Observability

- User-facing dialogs for load/parse/compatibility errors.
- Structured logs to stdout/stderr for diagnostics.
- Non-fatal handling for partial ingest failures (for example, some invalid filenames in a directory).

## Consequences

Positive:

- Fast local inspection workflow independent of web stack startup.
- Deterministic time-series behavior with one shared temporal control.
- Responsive slider scrubbing through LRU-cached frame reuse.
- Simple persistence model with a single colocated file.

Negative:

- Separate UI stack increases maintenance surface.
- Full in-memory frame caching can consume substantial RAM for large rasters.
- No CRS harmonization means mismatched datasets are rejected rather than reconciled.

## Risks and Mitigations

- Risk: memory pressure with large-frame cache.
  - Mitigation: configurable max cache bytes/items, clear-cache control, telemetry logs for hit/miss/evictions.
- Risk: slow startup when restoring very large state.
  - Mitigation: lazy frame decode (metadata first), defer raster reads until first render.
- Risk: ambiguous filename timestamps.
  - Mitigation: strict parser and explicit skipped-file reporting.

## Implementation Plan (Follow-On Task)

1. Create stand-alone app module and Qt UI skeleton.
2. Implement layer model and persistence schema.
3. Implement single-layer load + render + opacity.
4. Implement time-series ingest from files/directory and timestamp parsing.
5. Implement global slider union range and floor-time frame selection.
6. Implement LRU cache and performance instrumentation.
7. Add tests:
   - timestamp parser unit tests,
   - frame-selection (floor-time) unit tests,
   - persistence round-trip unit tests,
   - basic UI smoke test if feasible in headless CI.

## Acceptance Criteria

- Can load multiple single GeoTIFF layers and render in correct stack order.
- Can add/remove/reorder layers interactively.
- Can adjust each layer opacity and observe immediate effect.
- Can add time-series layer from selected files and from directory.
- Global slider range equals union of all loaded time-series timestamp ranges.
- At any slider time, each series displays latest frame with `timestamp <= slider_time`.
- Slider interactions remain responsive on representative time-series sets (~2100 frames) with cache enabled.
- Layer/time state persists across restart via single JSON file in app directory.
- Runs on Linux with documented dependency list.
