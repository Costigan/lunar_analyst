# Sun Vector Azimuth Bug

## Summary

The sun vector azimuth calculation in `ElevationMap.GetMatrix()` produces incorrect results due to an inconsistent application of the ENU (East-North-Up) frame rotation in the transformation matrix composition.

## Bug Description

### Symptoms

When calculating sun position at a lunar location using `btnShowSunVector_Click`:
- **Calculated azimuth**: ~80° (clockwise from North)
- **Actual azimuth from photographs**: ~-45° (or ~315°)
- **Error magnitude**: ~125° discrepancy

Test case: 2009-09-30T23:46:07 UTC at VIPER landing site

### Root Cause

The bug exists in `moonlib/ElevationMap.cs`, method `GetMatrix()` (lines 383-395). The transformation matrix that converts from MOON_ME (Moon Mean Earth) frame to local ENU frame has an incorrect composition order.

**Current (buggy) implementation:**
```csharp
public Matrix4d GetMatrix(int line, int sample)
{
    var (vec, lat_rad, lon_rad) = GetMoonCoordinates(new PixelPoint(sample, line));
    var zaxis = new Vector3d(0d, 0d, 1d);
    var yaxis = new Vector3d(0d, 1d, 0d);
    var a = Matrix4d.CreateFromAxisAngle(zaxis, -lon_rad);
    var b = Matrix4d.CreateFromAxisAngle(yaxis, -(Math.PI / 2 - lat_rad));
    var fixEnu = Matrix4d.CreateFromAxisAngle(zaxis, -Math.PI / 2d);
    var c = Matrix4d.CreateTranslation(-vec);
    var mat = c * a * b * fixEnu;  // BUG: Wrong order!
    return mat;
}
```

The problem is the order of operations: **Translation → Longitude → Latitude → ENU-fix**

This applies the `-90°` ENU frame rotation **after** the longitude rotation, which causes the azimuth to be incorrectly rotated relative to the horizon generator's expectations.

### Why This Matters

The horizon generation system uses a different but related function `GetRotationMatrixd()` in `QuadTreeHorizonGenerator.cs` (lines 2359-2367):

```csharp
internal static Matrix4d GetRotationMatrixd(double lat_rad, double lon_rad)
{
    var zaxis = new Vector3d(0d, 0d, 1d);
    var yaxis = new Vector3d(0d, 1d, 0d);
    var mat1 = Matrix4d.CreateFromAxisAngle(zaxis, -lon_rad);
    var mat2 = Matrix4d.CreateFromAxisAngle(yaxis, -((Math.PI / 2d) - lat_rad));
    var fixEnu = Matrix4d.CreateFromAxisAngle(zaxis, -Math.PI / 2d);
    return mat1 * mat2 * fixEnu;  // Longitude → Latitude → ENU-fix (no translation)
}
```

This function creates a rotation-only matrix (no translation) in the order: **Longitude → Latitude → ENU-fix**

The horizon generator then uses this in `ComputeDirectionVector()` (lines 2369-2375):

```csharp
internal static Vector3d ComputeDirectionVector(Matrix4d obsToMe, double az)
{
    double angle = (Math.PI / 2.0) - az;
    var dirObs = new Vector3d(Math.Cos(angle), Math.Sin(angle), 0.0);
    var dirMe = Vector3d.Transform(dirObs, obsToMe);
    return Vector3d.Normalize(dirMe);
}
```

Where `angle = π/2 - az` converts azimuth (0° = North, 90° = East) to a direction vector in the observer's ENU frame:
- `az = 0` (North) → `angle = π/2` → `dirObs = (0, 1, 0)` ✓
- `az = π/2` (East) → `angle = 0` → `dirObs = (1, 0, 0)` ✓

And the inverse calculation in `GetAzEl()` (lines 424-426):

```csharp
// In ENU frame: X=East, Y=North. For clockwise-from-North azimuth: Atan2(East, North)
var azimuth_rad = (float)Math.Atan2(x, y);  // [-PI,PI]
if (azimuth_rad < 0) azimuth_rad += (float)(2 * Math.PI);  // [0,2PI]
```

This calculates azimuth as `Atan2(East, North)`:
- Vector `(1, 0, 0)` (East) → `Atan2(1, 0) = π/2` = 90° ✓
- Vector `(0, 1, 0)` (North) → `Atan2(0, 1) = 0` ✓

So the **azimuth conventions are consistent** between horizon generation and sun vector calculation.

### The Inconsistency

The problem is that `GetMatrix()` applies transformations to **positions** (which require translation), while `GetRotationMatrixd()` applies transformations to **direction vectors** (which do not require translation, only rotation).

When `GetMatrix()` is used in sun vector calculation:

1. Sun position in MOON_ME frame: `sunPos_me`
2. Apply `c * a * b * fixEnu`:
   - `c`: Translate to observer origin ✓
   - `a`: Rotate by longitude ✓
   - `b`: Rotate by latitude ✓
   - `fixEnu`: Rotate -90° around Z-axis ✗ **TOO LATE!**

The `-90°` rotation happens **after** the longitude rotation `a`, which means the ENU frame ends up rotated incorrectly relative to how the horizon generator expects it.

For the transformations to be consistent, the `-90°` ENU fix rotation must be applied **immediately after translation**, before the geographic rotations. This ensures the local coordinate system is properly oriented before applying the geographic transformations.

## The Fix

The matrix in `GetMatrix()` needs to be the **inverse** of the `obsToMe` transformation used in horizon generation. The current implementation constructs a "forward" transformation (local→ME) but applies it to transform ME→local positions.

```csharp
public Matrix4d GetMatrix(int line, int sample)
{
    var (vec, lat_rad, lon_rad) = GetMoonCoordinates(new PixelPoint(sample, line));
    var zaxis = new Vector3d(0d, 0d, 1d);
    var yaxis = new Vector3d(0d, 1d, 0d);
    
    // Inverse rotations: opposite angles in reverse order
    var fixEnuInv = Matrix4d.CreateFromAxisAngle(zaxis, Math.PI / 2d);
    var bInv = Matrix4d.CreateFromAxisAngle(yaxis, (Math.PI / 2 - lat_rad));
    var aInv = Matrix4d.CreateFromAxisAngle(zaxis, lon_rad);
    var c = Matrix4d.CreateTranslation(-vec);
    
    // ME→local: translate, then apply inverse rotations in reverse order
    var mat = fixEnuInv * bInv * aInv * c;
    return mat;
}
```

New transformation: **inverse(ENU-fix) → inverse(latitude) → inverse(longitude) → translation**

## Rationale

### Coordinate System Convention

The project uses an ENU (East-North-Up) local coordinate system where:
- **X-axis**: East
- **Y-axis**: North
- **Z-axis**: Up (radially outward from Moon center)

Azimuth is measured **clockwise from North**, following the convention documented in `DESCRIPTION.md`:
- 0° = North
- 90° = East
- 180° = South
- 270° = West

### Transformation Chain

To transform a position from MOON_ME to local ENU, we need:

1. **Translation** (`c`): Move Moon-center origin to observer position
2. **ENU fix** (`fixEnu`): Establish ENU coordinate axes orientation
3. **Geographic rotations** (`a`, `b`): Rotate from geographic reference to local vertical

The key insight is that the **ENU frame orientation must be established before applying geographic rotations**. If we apply geographic rotations first (as in the buggy code), the `-90°` Z-rotation happens in a coordinate system that's already been rotated by longitude, causing the axes to end up misaligned.

### Why This Fixes It

With the corrected order `c * fixEnu * a * b`:

1. Translate sun position to observer origin
2. Apply `-90°` Z-rotation to establish ENU frame (X=East, Y=North)
3. Apply longitude rotation to align with observer's meridian
4. Apply latitude rotation to align with local vertical

This matches how the horizon generator constructs direction vectors: it creates a direction in ENU frame, then applies `mat1 * mat2 * fixEnu` (which is equivalent to the rotation part of `fixEnu * a * b` when composed in reverse for inverse transforms).

### Mathematical Consistency

The transformation for a position vector `p` from MOON_ME to ENU should satisfy:

```
p_ENU = GetMatrix() * p_ME
```

And a direction vector `d` from ENU to MOON_ME should satisfy:

```
d_ME = GetRotationMatrixd() * d_ENU
```

Since `GetRotationMatrixd() = a * b * fixEnu`, the inverse (ME to ENU) is:

```
d_ENU = (a * b * fixEnu)^-1 * d_ME
      = fixEnu^-1 * b^-1 * a^-1 * d_ME
```

For rotations, transpose = inverse, so:

```
d_ENU = fixEnu^T * b^T * a^T * d_ME
```

The translation-free version of `GetMatrix()` should match this pattern, which means:

```
GetMatrix_rotation_only = fixEnu^T * b^T * a^T
```

But matrix multiplication from the left is equivalent to applying operations in reverse order from the right, so:

```
GetMatrix(position) = c * fixEnu * a * b
```

This gives us the correct transformation order.

## Impact

This bug affects:

1. **Sun vector visualization** in CompareHorizons tool
2. **Lightmap generation** (any code using `GetAzEl()` to compare sun position with horizon profiles)
3. **Any future features** that rely on calculating celestial object positions in local ENU frame

The horizon generation itself is **not affected** because it uses `GetRotationMatrixd()` and `ComputeDirectionVector()` which are internally consistent.

## Testing

After applying the fix, the sun vector azimuth should match photographic observations within measurement error (~5-10°).

Re-test with:
- Date/Time: 2009-09-30T23:46:07 UTC
- Location: VIPER landing site (near lunar south pole)
- Expected azimuth: ~315° (or -45°)
- Previous calculated: ~80° (125° error)
- After fix: Should be within 10° of 315°
