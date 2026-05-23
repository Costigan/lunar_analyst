# Azimuth and Elevation Handling in new_horizon

This document describes how azimuth and elevation are defined and handled throughout the `new_horizon` application, particularly focusing on the coordinate transformations used for horizon generation and lightmap calculation.

## Conventions

### Azimuth
- **Definition**: Clockwise angle from North.
- **Mapping**:
    - 0° = North
    - 90° = East
    - 180° = South
    - 270° = West
- **Resolution**: 1440 bins (0.25° per bin).
- **Indexing**: `index = (int)(azimuth_deg * 4)`.

### Elevation
- **Definition**: Angle above the local tangent plane (horizon).
- **Units**: Degrees.
- **Values**: Typically ranges from -90° to 90°. In horizon profiles, these are the maximum visible angles.

## Coordinate Frames

### Moon Mean Earth (MME)
The primary body-fixed coordinate system for the Moon.
- **Origin**: Center of the Moon.
- **Z-axis**: North Pole.
- **X-axis**: Intersection of the Prime Meridian and the Equator.

### East-North-Up (ENU)
The standard local tangent frame used for azimuth and elevation calculations.
- **X-axis**: East (along the local parallel).
- **Y-axis**: North (tangent to the meridian, pointing towards the North Pole).
- **Z-axis**: Up (normal to the reference sphere).

### North-East-Down (NED)
An alternative local frame sometimes used in the codebase (e.g., in `MoonME_to_ZDown`).
- **X-axis**: North.
- **Y-axis**: East.
- **Z-axis**: Down (pointing towards the Moon's center).

## Mathematical Implementation

### 1. ME to Local Transformation
The matrix `M` that transforms a vector `v_me` from the MME frame to the local ENU frame is constructed such that:
- `v_enu = (v_me - observer_me) * M`
- Column 0 of `M` is the `East` basis vector in MME.
- Column 1 of `M` is the `North` basis vector in MME.
- Column 2 of `M` is the `Up` basis vector in MME.

In the `Matrix4d` struct (row-major), this corresponds to:
- `Row 0 = [East.X, North.X, Up.X, 0]`
- `Row 1 = [East.Y, North.Y, Up.Y, 0]`
- `Row 2 = [East.Z, North.Z, Up.Z, 0]`
- `Row 3 = [Translation]`

### 2. Azimuth and Elevation Calculation
Given a local vector `v = (x, y, z)` in the **ENU** frame:
- `azimuth_rad = atan2(x, y)`  (East, North)
- `elevation_rad = atan2(z, sqrt(x^2 + y^2))` (Up, GroundDistance)

## Discrepancies and Issues Found

### LightmapPipeline Coordinate Mismatch
The `LightmapPipeline` currently uses `MoonME_to_ZDown` which results in an **NED** frame.
- `temp.X` = North
- `temp.Y` = East
- `temp.Z` = Down

When passed to `GetAzEl(Vector3d point, Matrix4d mat)`:
- It calculates `atan2(temp.X, temp.Y)` which is `atan2(North, East)`. This is `90° - Azimuth`.
- It calculates `atan2(temp.Z, alen)` which is `atan2(Down, alen)`. This is positive when the sun is *below* the horizon.

### Inconsistent Matrix Generation
- `ElevationMap.MoonME_to_ZDown`: Produces **NED** (X=N, Y=E, Z=D).
- `ElevationMap.GetMatrix`: Produces a non-standard frame (X=-N, Y=E, Z=U).
- `QuadTreeHorizonGenerator.GetRotationMatrixd`: Produces a standard **ENU** (X=E, Y=N, Z=U).

### Units Mismatch
In `ElevationMap.MoonME_to_ZDown`, the translation is calculated using `moonRadius = 1737.4f` (km), resulting in `xs, ys, zs` in kilometers. However, the rest of the application (and GDAL/SPICE managers) typically works in **meters**. This causes a massive offset when transforming vectors from MME to the local frame.

## Recommendations
To ensure consistency and fix the lightmap generation:
1.  **Standardize on ENU**: All local transformations should target the ENU frame for compatibility with `GetAzEl`.
2.  **Fix LightmapPipeline**: Replace `MoonME_to_ZDown` with a proper `MoonME_to_ENU` transformation.
3.  **Update GetAzEl**: Ensure `GetAzEl` always receives an ENU-targeted matrix.
4.  **Remove Manual Adjustments**: Remove `az_deg -= 90f` and similar hacks once the underlying coordinate systems are aligned.

## Impact on Data

**Will regenerating horizon files change their content?**
**No.** Both `ReferenceHorizonGenerator` and `QuadTreeHorizonGenerator` already use the standard ENU rotation logic (`GetRotationMatrixd`). The values stored in the horizon files are correct relative to the established North=0, East=90 convention.

**Will lightmaps change?**
**Yes, significantly.** The current `LightmapPipeline` interprets the horizons incorrectly because it uses an NED frame and inconsistent units. Fixing these will align the sun's position with the correct horizon bins, resolving the "wildly different" results observed compared to the older implementation.
