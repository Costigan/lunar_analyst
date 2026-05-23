# Bug Report: Horizon Discontinuity at Patch Boundaries (High Latitude)

## Description
A visible "line" artifact was observed in shadow maps generated from the quadtree horizon generator. The artifact manifested as a sharp discontinuity in horizon elevation angles at the boundary between internal processing patches (specifically at x=4095/4096), despite the terrain being continuous.

- **Observed:** Horizon at Azimuth 347.25 differed significantly between Observer(4095, 860) and Observer(4096, 860), which are adjacent pixels.
- **Impact:** Produced artificial lines in lightmaps/shadows used for path planning.
- **Location:** Lat ~85.4°S (VIPER mission area).

## Diagnosis & Root Cause

### 1. Compact Mode Approximation
The QuadTree generator implemented a "Compact Mode" optimization for latitudes < 89° (previously considered "non-polar").
- **Mechanism:** In Compact Mode, the algorithm computes a *single* ray trajectory (polynomial) for the center of a 128x128 pixel patch.
- **Assumption:** It assumes that the ray's shape and direction in *Grid Space* are translation-invariant across the patch (i.e., you can just slide the start point without rotating the ray).

### 2. Grid Convergence Failure
At high latitudes (85°S), this assumption fails due to **Grid Convergence**—the angle between True North (geodetic) and Grid North (projected map).
- The projection is Polar Stereographic.
- Grid Convergence changes as a function of longitude (and thus X/Y position on the map).
- Across a single 128-pixel patch width (~2.5 km), the Grid Convergence changes by approximately **0.052°**.

### 3. The Artifact
Because Compact Mode used the *patch center's* Grid Convergence for all pixels in the patch:
- Pixels at the right edge of Patch N used a ray rotated by $\gamma_{center\_N}$.
- Pixels at the left edge of Patch N+1 used a ray rotated by $\gamma_{center\_N+1}$.
- At the boundary (x=4095 to 4096), the ray direction effectively jumped by $\Delta\gamma \approx 0.052°$.
- Over a 100km horizon distance, a 0.05° rotation shifts the sampling path by ~90 meters, causing the ray to hit different terrain features.

## Fix Implementation

**Disable Compact Mode for High Latitudes.**
The heuristic in `QuadTreeHorizonGenerator.cs` was modified to force "Full Mode" (unique ray per pixel) at all relevant latitudes.

**Old Code:**
```csharp
// Only use Full Mode if very close to pole (>89 deg)
bool isNearPole = Math.Abs(centerLat) > (89.0 * Math.PI / 180.0);
```

**New Code:**
```csharp
// Always use Full Mode to handle grid convergence correctly at 85S
bool isNearPole = Math.Abs(centerLat) > (-1.0 * Math.PI / 180.0); 
```

### Verification results (Azimuth 347.25)
After the fix, the horizon angle difference between x=4095 and x=4096 reduced to **0.0011°**, which is physically consistent and invisible in the output products.

| Metric | Value |
| :--- | :--- |
| **Grid Convergence Jump (Root Cause)** | ~0.052° |
| **Old Horizon Diff** | > 0.1° (Visible) |
| **New Horizon Diff** | 0.0011° (Invisible) |

## Final Resolution: Analytic GPU Geodesic Generation

Instead of using the "Full Mode" polynomial fitting (which took 2.2 hours on CPU), we implemented a fully **Analytic Ray Generation** on the GPU.

### Mechanism
1.  **CPU:** Skipped all ray pre-calculation and polynomial fitting.
2.  **GPU Kernel:**
    *   For every pixel and every step of the ray:
    *   Calculates the exact Geodesic coordinate $(\phi, \lambda)$ on the sphere.
    *   Projects this coordinate to the DEM's Polar Stereographic system $(X, Y)$.
    *   Samples elevation and computes slope.

### Results
*   **Accuracy:** Verified continuity at the patch boundary (x=4095/4096). Difference is **0.0067°**, effectively invisible.
*   **Performance:** ~128 seconds per patch (GPU). While slower than the optimized "Compact Mode" (0.2s), it is vastly faster than the CPU-based "Full Mode" (hours) and provides "Ground Truth" accuracy without approximation errors.
*   **Robustness:** Handles any observer location and grid convergence correctly by solving the geometry from first principles on the fly.

