# BUG: Phase 0.9 browser map renders blank blue background (no tiled base layer)

## Summary
Phase 0.9 map milestone page loads and initializes without runtime exceptions, but the map canvas remains a uniform blue background. Moon Trek WMTS capabilities are fetched successfully, tile URL templates are generated correctly, and direct tile fetches return `200`, yet OpenLayers does not render the tiled base layer in the map view.

## Current Status
- Reproducible on local development setup.
- Build marker currently in frontend script: `MAP_MILESTONE_BUILD=20260215j`.
- Page URL: `/map-milestone/`.
- FastAPI route serving frontend: `app.mount("/map-milestone", StaticFiles(..., html=True))`.

## Reproduction
1. Start backend from repo root (`D:\projects\lunar_analyst`):
   - `cmd /c "D:\projects\env_311\Scripts\activate.bat && python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000 --reload"`
2. Open `http://127.0.0.1:8000/map-milestone/`.
3. Observe blue background with no visible tiled base layer.

## Observed Console Output (latest)
From user run with `MAP_MILESTONE_BUILD=20260215j`:
- `MAP_MILESTONE_BUILD=20260215j`
- `Fitted to base extent: [-931100, -931100, 931100, 931100]`
- No Moon Trek PNG tile requests beyond:
  - `https://trek.nasa.gov/tiles/Moon/SP/LRO_WAC_Mosaic_SPole60_100mp/1.0.0/WMTSCapabilities.xml`
- No visible rendered tile layer.

Earlier diagnostic runs confirmed:
- WMTS URL template generation:
  - `https://trek.nasa.gov/tiles/Moon/SP/LRO_WAC_Mosaic_SPole60_100mp/1.0.0//default/default028mm/{TileMatrix}/{TileRow}/{TileCol}.png`
- Sample tile URL fetch succeeds (`200`):
  - `.../default/default028mm/1/1/1.png`
- Static probe (single known tile as `ImageStatic`) **did render**.

## Why this is important
Phase 0.9 acceptance goal is a visible browser milestone: Moon Trek tiled base + aligned hillshade overlay. Base tile rendering is still blocked.

---

## Code Context (where bug manifests)

### Frontend milestone files
- `backend/web/map_milestone/index.html`
- `backend/web/map_milestone/styles.css`
- `backend/web/map_milestone/app.js`

### Backend wiring/config
- `backend/api/app.py`
- `backend/api/routers/map_milestone.py`
- `config/lunar_analyst.toml`

### Plan + scope
- `docs/PLAN.md` (Phase 0.9 checklist)

---

## Known-good external reference within workspace
MoonLayers code that successfully handles Moon projections/WMTS logic in other contexts:
- `..\moonlayers\src\projection.js`
- `..\moonlayers\src\wmts.js`
- `..\moonlayers\src\widget.js`

These were used as conceptual reference and partially adapted, but direct reuse in this milestone page still did not resolve runtime rendering of tiled layers.

---

## What has already been tried

1. Basic React/OpenLayers page -> replaced with plain JS module page.
2. Added CRS alias registrations and equivalences:
   - `ESRI:103878`
   - `urn:ogc:def:crs:ESRI::103878`
   - `EPSG::0`
   - `urn:ogc:def:crs:EPSG::0`
3. Ensured import map correctness for all OL modules.
4. WMTS options from capabilities:
   - explicit layer/style/matrix set
   - crossOrigin
   - tileGrid NaN extent fix
5. View adjustments:
   - fixed center/zoom
   - `fit()` to derived extents
   - using both ESRI and WMTS projection variants
   - using WMTS tile-grid resolutions
6. Fallback experiments:
   - XYZ fallback from WMTS URL template
   - custom `TileImage` manual tile URL function
7. Diagnostics added:
   - source state logging
   - tile URL function logging
   - rendercomplete/moveend logging
   - sample tile URL fetch (200)
8. Static probe:
   - Add one fetched tile as `ImageStatic` overlay -> **renders**.
   - Confirms map canvas and image rendering path are functional.

---

## Working assumptions from evidence
1. Moon Trek service and URL template are valid.
2. OpenLayers can render in this page (static probe proved).
3. Failure is specific to OL tile-layer scheduling/draw path in this configuration.
4. Hillshade endpoint is not the primary blocker for base tile visibility.

---

## Suggested next debugging steps (for next LLM)
1. Build a minimal standalone OL tile repro in this same page:
   - remove hillshade entirely
   - single `TileLayer + WMTS` only
   - no fallback logic
   - verify tile requests fire
2. If still no requests, replace WMTS source with:
   - explicit `TileImage` + custom `tileGrid` generated from capabilities matrix set (not reused from WMTS options object)
3. Verify tile coordinate sign conventions:
   - check whether OL expects TMS-style `y` inversion for this WMTS setup
4. Inspect whether layer extent clipping is implicitly applied by source/layer (despite no explicit clipping set now).
5. Compare against a known working OL WMTS example with custom non-EPSG projection and incrementally port differences.
6. Capture one successful multi-tile render before re-introducing GeoTIFF layer.

---

## Additional docs to read for full project context
- `AGENTS.md` (project invariants and process rules)
- `docs/NEW_DESIGN.md` (architecture decisions and design direction)
- `docs/API_CONTRACT.md` (contract-first strategy context)
- `docs/contracts/README.md` (contract artifact location policy)
- `backend/README.md` (backend usage + phase notes)

---

## Non-issue note
`GET /favicon.ico 404` is unrelated noise and not the map rendering blocker.
