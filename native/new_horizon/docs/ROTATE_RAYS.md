# Proposed Fix: Compact Mode with Grid Convergence Compensation ("Smart Rotation")

## Problem

A visible "line" artifact was observed in shadow maps generated from the quadtree horizon generator. The artifact manifested as a sharp discontinuity in horizon elevation angles at the boundary between internal processing patches (specifically at x=4095/4096), despite the terrain being continuous.

- **Observed:** Horizon at Azimuth 347.25 differed significantly between Observer(4095, 860) and Observer(4096, 860), which are adjacent pixels.
- **Impact:** Produced artificial lines in lightmaps/shadows used for path planning.
- **Location:** Lat ~85.4°S (VIPER mission area).

### Diagnosis & Root Cause

#### 1. Compact Mode Approximation
The QuadTree generator implemented a "Compact Mode" optimization for latitudes < 89° (previously considered "non-polar").
- **Mechanism:** In Compact Mode, the algorithm computes a *single* ray trajectory (polynomial) for the center of a 128x128 pixel patch.
- **Assumption:** It assumes that the ray's shape and direction in *Grid Space* are translation-invariant across the patch (i.e., you can just slide the start point without rotating the ray).

#### 2. Grid Convergence Failure
At high latitudes (85°S), this assumption fails due to **Grid Convergence**—the angle between True North (geodetic) and Grid North (projected map).
- The projection is Polar Stereographic.
- Grid Convergence changes as a function of longitude (and thus X/Y position on the map).
- Across a single 128-pixel patch width (~2.5 km), the Grid Convergence changes by approximately **0.052°**.

#### 3. The Artifact
Because Compact Mode used the *patch center's* Grid Convergence for all pixels in the patch:
- Pixels at the right edge of Patch N used a ray rotated by $\gamma_{center\_N}$.
- Pixels at the left edge of Patch N+1 used a ray rotated by $\gamma_{center\_N+1}$.
- At the boundary (x=4095 to 4096), the ray direction effectively jumped by $\Delta\gamma \approx 0.052°$.
- Over a 100km horizon distance, a 0.05° rotation shifts the sampling path by ~90 meters, causing the ray to hit different terrain features.

Disabling "Compact Mode" (moving to "Full Mode") solved the horizon discontinuity artifact but increased computation time by orders of magnitude (from seconds to hours). We need a solution that retains the performance of Compact Mode while eliminating the grid convergence artifacts at high latitudes.

## Solution Approach
We will re-enable Compact Mode but modify the GPU kernel to compensate for the rotation of the grid coordinate system (Grid Convergence) across the width of a patch.

### The Physics
1.  **Ray Shape:** The cubic polynomial describing the geodesic ray's path on the map is largely translation-invariant over small distances (128 pixels / 2.5km). Its shape (curvature) is determined by projection distortion gradients, which change slowly.
2.  **Ray Orientation:** The orientation of the ray relative to the grid (Grid Azimuth) changes rapidly near the poles. "True North" rotates by ~0.05Â° across a single patch at 85Â°S.
3.  **The Artifact:** The previous implementation used a fixed Grid Azimuth for the entire patch. This caused rays at the patch edge to point in the wrong direction relative to local North, causing a "jump" in terrain sampling at the patch boundary.

### The Algorithm
1.  **CPU (Per Patch):**
    *   Calculate Grid Convergence ($\gamma$) at the patch center.
    *   Calculate the gradient of convergence ($\Delta\gamma_x, \Delta\gamma_y$) across the patch dimensions.
    *   Pass `GammaCenter` and its gradients to the GPU via `KernelParams`.

2.  **GPU (Per Pixel):**
    *   Calculate the local Grid Convergence for the specific pixel:
        $$ \gamma_{pixel} = \gamma_{center} + \Delta\gamma_x \cdot dx + \Delta\gamma_y \cdot dy $$
    *   Calculate the required rotation correction:
        $$ \delta = \gamma_{center} - \gamma_{pixel} $$
    *   **Rotate the Ray:** Apply a 2D rotation matrix to the pre-calculated polynomial coefficients $(A_n, B_n)$ by angle $\delta$.
        *   This effectively re-orients the "Center Ray" to align with the "Pixel's True North".

This means that Compact Mode should always be used, independent of latitude.

### Expected Result
*   **Accuracy:** Eliminates the discontinuity artifact by ensuring every pixel traces a ray in the correct True Azimuth direction.
*   **Performance:** Maintains the performance of Compact Mode (single polynomial fit per patch), adding only a few scalar multiplications and a rotation (sin/cos) per pixel on the GPU.
