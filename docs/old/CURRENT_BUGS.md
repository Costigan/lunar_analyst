# Current Bugs (React Map Milestone)

This file tracks currently open React map milestone regressions.

Updated: 2026-02-18

## Active Bugs

No currently confirmed open items from the prior 2026-02-18 regression list.

## Recently Resolved

1. Startup right-pane flicker
- Resolved in React layer card/details state handling and right-pane update stabilization.
- See `docs/FLICKERING_PANEL_BUG.md` for root cause and fix details.

2. Duplicate layer creation after product drop
- Drop handling now prevents duplicate create paths and stops propagation at drop zones.

3. Layer panel IA mismatch
- Layer manager now uses one filterable reorderable list for scenario layers.

4. Raster warped nodata corners rendering black
- Raster style/source now propagate range + nodata diagnostics and apply transparent masking.

5. Colormap updates with no visible change
- Runtime colormap registry now includes UI-supported colormaps (`gray`, `viridis`, `magma`, `inferno`, `plasma`).

6. Slow slider interactions
- Layer style patching now supports debounced persistence with immediate local state updates.

## Notes

If new regressions are found, add them under `Active Bugs` with reproduction steps and timestamp.
