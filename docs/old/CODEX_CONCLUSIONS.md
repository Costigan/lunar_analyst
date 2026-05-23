# Codex React UI Audit Conclusions

Date: 2026-02-18
Scope: Read-only audit of `backend/web/map_milestone/src` against `docs/CURRENT_BUGS.md`.

Status: Historical snapshot before subsequent bug-fix work. See `docs/CURRENT_BUGS.md` for current status.

## Summary

I do see the reported React UI bugs in the current implementation. Five are directly confirmed in code, and one (startup right-pane flicker) is strongly plausible from code paths and style composition.

## Findings

1. Duplicate layer creation after product drop: confirmed
- Two drop handlers in `LayerManagerPane` can both trigger layer creation (`backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:191`, `backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:218`).
- Both call `addProductLayerAtIndex(...)` (`backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:202`, `backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:223`).
- Empty-list container path is active when no layers exist (`backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:219`).
- Generic layer naming is hardcoded (`backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:162`).

2. Colormap changes not affecting raster appearance: confirmed (for some options)
- UI allows `magma`, `inferno`, and `plasma` (`backend/web/map_milestone/src/components/layers/LayerCard.tsx:139`, `backend/web/map_milestone/src/components/layers/LayerCard.tsx:140`, `backend/web/map_milestone/src/components/layers/LayerCard.tsx:141`).
- Runtime map colormaps only define `gray` and `viridis` (`backend/web/map_milestone/src/map/mapController.ts:40`, `backend/web/map_milestone/src/map/mapController.ts:49`).
- Unknown colormaps fall back to default (`backend/web/map_milestone/src/map/rasterStyle.ts:37`).

3. Layer panel information architecture mismatch: confirmed
- Layer filter/selection list is separate from reorderable cards (`backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:239`, `backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:253`).
- Base layer is outside filtered list as a separate block (`backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:286`, `backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:296`).

4. Raster warped corner nodata rendered black: confirmed
- New raster layer style defaults do not include `valueMin`, `valueMax`, or `nodataCutoff` (`backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:168`).
- Transparency masking only activates when range/nodata exists (`backend/web/map_milestone/src/map/rasterStyle.ts:34`, `backend/web/map_milestone/src/map/rasterStyle.ts:60`, `backend/web/map_milestone/src/map/rasterStyle.ts:63`, `backend/web/map_milestone/src/map/rasterStyle.ts:66`).
- Without mask, band values are clamped and rendered opaque (`backend/web/map_milestone/src/map/rasterStyle.ts:42`, `backend/web/map_milestone/src/map/rasterStyle.ts:67`).

5. Colormap control updates in UI but no visible change: confirmed
- Colormap control patches style on each change (`backend/web/map_milestone/src/components/layers/LayerCard.tsx:131`).
- Because unsupported colormaps fall back, visible map output may appear unchanged (`backend/web/map_milestone/src/map/rasterStyle.ts:37`).

6. Slow slider interactions (opacity/brightness/contrast): confirmed
- Sliders patch per input event (`backend/web/map_milestone/src/components/layers/LayerCard.tsx:76`, `backend/web/map_milestone/src/components/layers/LayerCard.tsx:95`, `backend/web/map_milestone/src/components/layers/LayerCard.tsx:115`).
- Each patch immediately reloads all layers (`backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:128`, `backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:129`).

7. Startup flicker in right pane: plausible from code, not fully provable via static read-only inspection
- WS layer events cause recurring scenario layer refreshes (`backend/web/map_milestone/src/App.tsx:168`, `backend/web/map_milestone/src/App.tsx:171`, `backend/web/map_milestone/src/App.tsx:176`).
- Mixed stylesheet import may contribute to repaint churn at startup (`backend/web/map_milestone/src/main.tsx:5`, `backend/web/map_milestone/styles.css:69`, `backend/web/map_milestone/src/styles/app.css:60`).
- Jobs WS bursts are less likely startup cause because jobs socket opens only with active job (`backend/web/map_milestone/src/components/jobs/JobsManagerPane.tsx:58`).

## Conclusion

The bug report in `docs/CURRENT_BUGS.md` matches the current React code state. I see concrete implementation evidence for duplicate layer creation risk, generic naming, split layer lists, raster nodata masking gaps, unsupported colormap options, and slider lag. The startup flicker remains a runtime symptom but has credible code-level contributors.

## Delta After Reviewing Gemini Conclusions

Gemini identified one additional issue that I agree with and had not explicitly called out as its own finding:

- Fragmented layer-state authority and redundant layer fetching between `App.tsx` and `LayerManagerPane.tsx`.
  - App-level map state: `backend/web/map_milestone/src/App.tsx:81`, `backend/web/map_milestone/src/App.tsx:152`, `backend/web/map_milestone/src/App.tsx:168`
  - Layer manager local state/fetch path: `backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:80`, `backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:87`, `backend/web/map_milestone/src/components/layers/LayerManagerPane.tsx:101`

I agree this is a legitimate contributor to startup/update churn and likely part of the flicker symptom. Other Gemini findings align with this document.
