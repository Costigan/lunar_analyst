# Implementation Plan: GPU-Based Analytic Ray Generation

## Goal
Replace the CPU-heavy polynomial fitting (Compact/Full Mode) with on-the-fly analytic geodesic calculation on the GPU. This eliminates the 2.2-hour CPU pre-calculation time while maintaining per-pixel accuracy ("Full Mode" quality) and handling Grid Convergence correctly.

## Core Concept
Instead of pre-calculating ray paths on the CPU and fitting them to cubic polynomials, the GPU kernel will calculate the exact path of the ray (Great Circle) step-by-step.
For each step $s$ (distance) along a ray:
1.  **Geodesic Direct:** Calculate position $(\phi, \lambda)$ on the sphere given Observer $(\phi_0, \lambda_0)$, Azimuth $\alpha$, and Distance $s$.
2.  **Projection:** Project $(\phi, \lambda)$ into the DEM's coordinate system $(X, Y)$.
3.  **Sampling:** Sample elevation $H$ at $(X, Y)$ and compute slope.

## Detailed Steps

### 1. Modify `KernelParams`
We need to pass the Observer's geodetic coordinates to the GPU.
**File:** `moonlib/QuadTreeHorizonGenerator.cs`
-   Add `float ObserverLat` (radians) to `KernelParams`.
-   Add `float ObserverLon` (radians) to `KernelParams`.
-   (Optional) Remove `Gamma` fields if they were added (I haven't added them yet).

### 2. Update `CalculateRaySegments`
This method currently consumes 99% of the CPU time.
**File:** `moonlib/QuadTreeHorizonGenerator.cs`
-   **Bypass:** If using Analytic Mode (new default), skip the `Parallel.For` loops that generate samples and fit polynomials.
-   **Dummy Return:** Return an empty or dummy `RaySegment` array to satisfy the existing method signature (or refactor to make it nullable). The kernel won't use it.
-   **Compute Observer:** Ensure `ObserverLat/Lon` is calculated for the *center* of the tile (or passed down if we want per-pixel, but per-pixel lat/lon is implicitly defined by the pixel grid).
    -   *Correction:* The kernel runs per-pixel. The GPU needs to know the Observer's Lat/Lon *for that specific pixel*.
    -   The GPU kernel already has `tileColBase`, `tileRowBase`, and `pixelIdx`. It can calculate the Global Pixel $(C, R)$.
    -   It needs the **DEM Projection Parameters** to convert $(C, R) \to (X, Y) \to (\text{Lat}, \text{Lon})$.
    -   `PyramidView` already contains `ProjectionParams Proj`.
    -   So the GPU *can* calculate the Observer's start position itself!
    -   **Result:** We might not even need to pass `ObserverLat/Lon` in `KernelParams` if the GPU derives it from the pixel index.

### 3. Rewrite `QuadTreeRayCastKernel`
**File:** `moonlib/QuadTreeHorizonGenerator.cs`
-   **Remove:** `RaySegment` usage (`seg.A1`, `seg.StartPixel`, etc.).
-   **New Logic:**
    1.  **Start Point:**
        -   Convert `pixelIdx` -> `(col, row)`.
        -   `PixelToCRS` -> `(x, y)`.
        -   `InverseProject` -> `(lat0, lon0)` (Observer Position).
    2.  **Azimuth:**
        -   `azIdx` -> `TrueAzimuth` (e.g. `azIdx * BeamWidth`).
        -   *Note:* Previous code might have used Grid Azimuth. We must ensure we define Azimuth relative to North.
    3.  **Marching Loop:**
        -   Iterate distance $s$ (meters/km).
        -   **Geodesic Function (Inline):**
            -   Given $(\phi_0, \lambda_0, \alpha, s)$, compute $(\phi, \lambda)$.
            -   Formula: Standard Spherical Geodesic Direct (haversine/cosine rules).
        -   **Projection Function (Inline):**
            -   Given $(\phi, \lambda)$, compute $(X, Y)$ using `PyramidView.Proj` parameters.
            -   Formula: Polar Stereographic equations (using `XMath.Sin`, `XMath.Cos`).
        -   **Sample:**
            -   `SampleBilinear(X, Y)`.
            -   Compute slope.

### 4. GPU Math Functions
We need to implement these helper functions inside the kernel (or as `static` functions marked for ILGPU):
-   `InverseProject(x, y, projParams)`: Stereographic $(X,Y) \to (\text{Lat}, \text{Lon})$.
-   `ForwardProject(lat, lon, projParams)`: $(\text{Lat}, \text{Lon}) \to \text{Stereographic } (X,Y)$.
-   `GeodesicDirect(lat, lon, az, dist, R)`: Steps along great circle.

### 5. Cleanup
-   Remove the `isCompact` branch in `QuadTreeHorizonGenerator`.
-   Remove the CPU fitting logic code (or comment it out).

## Performance Considerations
-   **Arithmetic Intensity:** This adds ~20-30 FLOPs + 4-6 trig operations per step.
-   **Memory Latency:** The bottleneck is fetching elevation data from VRAM. The extra ALU ops should effectively be "free" as they hide the memory latency.
-   **Precision:** `float` (FP32) precision for Lat/Lon on the Moon (R=1737km):
    -   1 degree $\approx$ 30km.
    -   Float significand: 23 bits ($\approx 7$ decimal digits).
    -   Precision $\approx 1$ meter.
    -   This should be sufficient for horizon generation, especially since we work with *deltas* or local coordinates where possible, but `GeodesicDirect` on global lat/lon might be borderline.
    -   *Mitigation:* `CHANGE.md` suggested working in local tangent plane. If global lat/lon precision is an issue, we can implement the "Local Tangent" math from `CHANGE.md`, then rotate/scale to global map.
    -   However, the DEM pixels are defined in the Global Map.
    -   Let's stick to Global Lat/Lon first. If precision jitters, we'll switch to local tangents.

## Validation
-   Run `DebugSingleRay` (trace mode) for pixels 4095 and 4096.
-   Verify smooth transition (similar to "Full Mode" result).
