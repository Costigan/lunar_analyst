# Form2 Implementation Status

## Completed Tasks
- [x] **Fix Compilation Errors**: Removed `MainForm` (dead code) from the project build. Fixed `GDAL.ReadRaster` overload in `Form2`.
- [x] **Coordinate Logic**: Implemented `HandleMapClick` with proper inverse transformation from Screen Space -> Master CRS -> Pixel Space.
- [x] **Lightmap Integration**: Connected `btnGenerateLightmap` to `LightmapPipeline`. It now generates a shadow map for the specified date and overlays it on the map.
- [x] **Plotting**: Implemented `DrawHorizonPlot` to visualize horizon profiles.
- [x] **Reference Generation**: Implemented on-demand reference horizon generation for arbitrary points using `Surface + 2.5m` observer height.
- [x] **UI Layout Fix**: Fixed `Form2.Designer.cs` where `Controls.Add` was missing, ensuring controls are actually visible.
- [x] **Reference Accuracy Fix**: Corrected `GenerateReferenceHorizon` to pass a relative Z offset (2.5m) instead of absolute elevation, resolving the "very different horizon" bug.
- [x] **Designer Crash Fix**: Modified `Form2` constructor to lazy-initialize `moonlib` dependencies and skip initialization in Design Mode.
- [x] **Hillshade Overlay**: Added "Show Hillshade" checkbox to render a background hillshade image within the primary DEM bounds.
- [x] **Hillshade Default**: Enabled hillshade by default and ensured image loads on startup.

## Verification Steps for User
1. **Run Application**: Start `CompareHorizons`.
2. **Check Default**: Verify "Show Hillshade" is checked and the hillshade image is visible on the map (grayscale terrain) immediately on load.
3. **Load File**: Open a `horizon_*.bin` file. Verify the map zooms to the patch location.
4. **Lock & Pick**: Check "Lock Map & Pick", then click anywhere on the map.
   - Verify the Red Crosshair moves to the clicked location.
   - Verify the Plot updates.
5. **Generate Reference**: Click "Generate Reference" with a point selected.
   - Verify a Red trace appears on the plot.
6. **Lightmap**: Enter a valid date (default provided) and click "Generate Lightmap".
   - Verify a shadow overlay appears on top of the hillshade.

## Notes
- `MainForm.cs` and `MainForm.Designer.cs` are excluded from the build but remain in the file system as backups.