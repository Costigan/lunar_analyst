# Form2 Implementation Plan

This document outlines the steps required to complete the implementation of `Form2` in the `CompareHorizons` project. `Form2` is a modernized version of `MainForm` with a revamped UI layout and enhanced functionality for global pixel selection and lightmap generation.

## 1. Goal
Replace the tab-based layout of `MainForm` with a split-panel design in `Form2`:
- **Left Panel:** Controls (Generation, Locking, Lightmap).
- **Right Panel (Split Horizontal):**
    - **Top:** Horizon Plot (`pbPlot`).
    - **Bottom:** Elevation Map (`pbMap`).

Enable "Lock Map" functionality to allow picking any global pixel coordinates on the primary DEM, loading the corresponding horizon from the file system, and generating reference horizons on demand.

## 2. Current Status
- `Form2.cs` has been rewritten to remove obsolete controls (`pbGrid`, `TabControl`) and integrate the new `splitRight` container.
- `Form2.Designer.cs` has been updated to reflect the new layout.
- `HorizonFileIndex` class added to `Form2.cs` for efficient file lookups based on global coordinates.
- `LightmapPipeline` is integrated for full-resolution lightmap generation.

## 3. Pending Tasks

### A. Fix Compilation Errors
The previous session ended with build errors due to:
1.  **MainForm.Designer.cs Restoration**: The user restored `MainForm.Designer.cs`, but `MainForm.cs` might still have code referencing controls that were removed or modified if it wasn't fully reverted. **Action:** Verify `MainForm` compiles. If not, revert `MainForm.cs` to a known good state or fix the references.
2.  **Form2 Syntax**: Ensure `Form2.cs` is syntactically correct (check for matching braces and valid method signatures).
3.  **Form2 Designer**: Verify `Form2.Designer.cs` initializes `splitRight`, `pbPlot`, and `pbMap` correctly and removes initialization for deleted controls (`pbGrid`, `tabControl1`).

### B. Verify Logic in Form2.cs
1.  **Coordinate Systems:**
    - Ensure `HandleMapClick` correctly converts screen coordinates to **Master DEM Global Pixels**.
    - Ensure `_fileIndex.LoadHorizon(x, y)` correctly maps global pixels to the specific `horizon_*.bin` file and offset.
    - Verify `ReferenceHorizonGenerator` uses the correct `PixelOrigin` (Z value specifically). Current implementation adds `2.5m` to the surface elevation. Confirm this assumption or refine it.

2.  **Event Handling:**
    - Test "Lock Map" checkbox. When checked, clicking the map should select a pixel (draw crosshair) and update the plot. When unchecked, it should pan/zoom.
    - Test `pbPlot` double-click to set azimuth.

3.  **Lightmap Integration:**
    - Verify `btnGenerateLightmap` triggers the background pipeline.
    - Verify the resulting Bitmap is correctly overlaid on `pbMap` using the Master DEM's geotransform.

### C. Run and Test
1.  Build the solution: `dotnet build`.
2.  Run `CompareHorizons`.
3.  Load a horizon file (e.g., `horizon_1280_2560_15500.bin`).
    - **Expected:** Map loads, Plot clears (or shows default).
4.  Toggle "Lock Map & Pick". Click a location.
    - **Expected:** Crosshair appears. If a horizon file exists for that pixel, the "Loaded Horizon" (Blue) trace appears.
5.  Click "Generate Reference".
    - **Expected:** "Reference Horizon" (Red) trace appears matching the loaded one. Red dot added to map cache.
6.  Generate Lightmap.
    - **Expected:** Progress bar fills, and a shadow map overlays the terrain.

## 4. Next Steps for Developer
1.  **Check `MainForm`**: Run `dotnet build`. If `MainForm` errors persist, use `git checkout` or manual fixes to restore it to a compiling state (it is a backup, so it doesn't need the new features).
2.  **Review `Form2.Designer.cs`**: Open it and ensure `InitializeComponent` is clean and contains no references to deleted fields.
3.  **Refine `HorizonFileIndex`**: The current implementation assumes a specific naming convention and 128x128 patches. Ensure this matches the actual data generation process.
4.  **Z-Coord Handling**: The `GenerateReferenceHorizon` method currently estimates observer Z as `Surface + 2.5m`. Ideally, this should match the exact Z used during the original generation if available, or be an explicit user setting.

## 5. Key File Locations
- `CompareHorizons/Form2.cs`: Main logic for the new form.
- `CompareHorizons/Form2.Designer.cs`: Layout code.
- `moonlib/pipeline/LightmapPipeline.cs`: Lightmap generation logic.
- `moonlib/ElevationMap.cs`: Coordinate system transformations (`PixelToCRS`, `CRSToPixel`).
