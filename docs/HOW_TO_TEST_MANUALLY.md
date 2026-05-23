# How to Manually Test

This document describes the active Linux-only manual test workflow.

## MoonLayers In-Repo Setup

1. Install the local package in the repo-managed environment:

```bash
.venv/bin/python -m pip install -e ./moonlayers_pkg
```

Fallback when editable install is not available:

```bash
export PYTHONPATH="/e/projects/lunar_analyst/moonlayers_pkg"
```

2. Build MoonLayers frontend assets:

```bash
cd moonlayers_pkg
npm install
npm run build
cd ..
```

3. Verify import origin:

```bash
.venv/bin/python -c "import moonlayers; print(moonlayers.__file__)"
```

Expected: the printed path is under `moonlayers_pkg/moonlayers/`.

## Plain Script Smoke

Run a plain Python command from the repo root:

```bash
.venv/bin/python -c "import moonlayers; print('ok', moonlayers.__file__)"
```

Expected: the command succeeds and the import path points to `moonlayers_pkg`.

## Map Milestone Startup

1. Start backend:

```bash
./scripts/run-host-dev.sh
```

2. Open:

`http://127.0.0.1:8000/lunar_analyst/`

3. Expected:

- redirects to `/lunar_analyst/`
- map loads Moon Trek base and hillshade overlay
- status reaches `Map ready`

## Layer Controls Checklist

1. Base layer controls
- Toggle `Moon Trek Base > Visible` off and on.
- Move `Moon Trek Base > Opacity` down and up.
- Expected: immediate visibility and opacity response.

2. Hillshade controls
- Toggle `Hillshade Overlay > Visible` off and on.
- Move `Hillshade Overlay > Opacity`, `Brightness`, and `Contrast`.
- Expected: immediate visual updates while dragging.

3. Colormap control
- Change `Colormap` to multiple entries.
- Expected: palette updates immediately and alignment is preserved.

4. Pan/zoom redraw persistence
- Set non-default hillshade style values, then pan and zoom repeatedly.
- Expected: chosen style remains applied after redraw with no permanent tile loss.

5. Console/network sanity
- Check browser dev tools while interacting.
- Expected: no repeated client errors and `/api/v1/lunar-analyst/colormaps` returns `200`.
