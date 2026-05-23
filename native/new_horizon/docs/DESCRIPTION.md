Project: new_horizon

### OVERVIEW

This is a C# application designed to generate horizon profiles from 2D height-field terrain data (Digital Elevation Models). It is used for planetary science and rover path planning.

### KEY FUNCTIONALITY

* Horizon Generation: Calculates the maximum elevation angle visible from an observer's perspective for every 0.25° of azimuth (1440 bins total). The azimuth mapping is clockwise, starting from North (0° = North, 90° = East, 180° = South, 270° = West).
* Parallelism: Utilizes ILGPU to offload heavy computational tasks (ray casting and point projection) to the GPU (CUDA/OpenCL).
* Geospatial Data: Uses GDAL to read and process standard DEM formats (e.g., GeoTIFFs).

### PROJECT STRUCTURE

* `moonlib/` (Library): The core engine.
  * HorizonGenerator.cs: Contains the main algorithms, including a "Far Kernel" point-projection approach and a "Ray Casting" approach.
  * QuadTreeHorizonGenerator.cs: New implementation for accelerated ray casting using min-max quadtrees.
  * ElevationMap.cs: Manages DEM data loading and coordinate systems.
  * HorizonCompressor.cs: Handles data compression for the output horizons.
  * math/: Custom vector and matrix math structs tailored for the simulation.
* `horizon_runner/` (Console App): The entry point.
  * Configures the environment (GDAL, Serilog) and executes the generation pipeline. Currently, it is set up to run GenerateHorizons (the point-based approach), or the new QuadTreeHorizonGenerator.
* tests/: Contains unit tests (HorizonGen.Tests) verifying components like the compressor, along with `QuadTreeHorizonGeneratorTests` which validates the new ray casting implementation, utilizing in-memory DEMs for robust and efficient testing.

### CONVENTIONS

* Elevation angles are returned by algorithms and stored in files in units of degrees.  Inside the algorithms, they may be slopes or angles in radians, but externally, they must be degrees.
* Horizons are represented at an angular resolution of 0.25 degrees, so they contain 1440 elements. The mapping is clockwise, starting from North (index 0 = North, index 360 = East, index 720 = South, index 1080 = West).
* Terrain patches are 128 by 128 pixels in size, hence files storing one terrain patch's worth of horizons contain 128 x 128 x 1440 angles.  In raw horizon files, the angles-in-degrees are represented as float32 values, so this is 128 x 128 x 1440 x 4 = 94371840 bytes.  These files will eventually contain some metadata too, but not yet.
* Horizon files are named by this function in QuadTreeHorizonGenerator.cs.  This places the row (or y value) of the upper left corner of the 128 x 128 pixel tile first in the filename with the column (x value) and elevation later.  There may be errors around this ordering.

        public static string BuildDefaultFileName(int tileCol, int tileRow, float observerElevation)
        {
            return $"horizon_{tileRow:D5}_{tileCol:D5}_{((int)(observerElevation * 10)):D3}.bin";
        }

### NUMERICAL PRECISION AND STABILITY

#### Double-Precision Coordinate Transformations

The emulator and CPU-side segment building code use double-precision (float64) for all coordinate transformations involving latitude/longitude and 3D vector calculations. This is critical for accuracy at polar latitudes where the observer is located (~85° south).

At polar latitudes with a large planetary radius (~1,737,400 meters), single-precision floating point (float32) does not provide sufficient precision for:
- Inverse map projection (CRS → lat/lon)
- Lat/lon → ECEF 3D vector conversion
- Observer-frame coordinate transformations

The GPU kernels themselves still use single-precision for performance, but all geometric setup is done in double-precision on the CPU.

#### Unit Consistency and Global Vector Precision

All global vector calculations (e.g., Sun and Earth positions) are standardized to **meters**. This ensures consistency with the observer's position and the lunar radius, preventing "unit parallax" errors.

**Precision Analysis (64-bit Double):**
- **Distance Magnitude**: The Sun is $\sim 1.5 \times 10^{11}$ meters away.
- **Absolute Precision**: A 64-bit `double` provides 15-17 significant digits. At $10^{11}$ meters, the absolute resolution is $\sim 10^{-5}$ meters (0.01 mm).
- **Angular Precision**: This translates to an angular resolution of $\sim 6.6 \times 10^{-17}$ radians, which is several orders of magnitude finer than the project's $0.01^\circ$ error budget.
- **Correctness**: Using inconsistent units (e.g., Sun in km, Observer in meters) previously introduced an artificial parallax error of $\sim 0.66^\circ$ at the Moon's surface. Standardizing to meters in the transformation pipeline eliminates this error.

#### Polynomial Parameter Units: Kilometers

The polynomial representation of the ray path uses **kilometers** as the distance parameter, not meters. This is a critical choice for numerical conditioning of the 4th-order polynomial fit.

**Analysis of Meters vs Kilometers:**

With meters as the distance unit (s ranging from 2 to 100,000):
- Coefficient a₄ becomes ~10⁻²¹, below float32 precision
- Polynomial fit errors exceed 1000 pixels (~5 km)
- Even near-field samples (< 100m) have ~20 pixels of error

With kilometers as the distance unit (s ranging from 0.002 to 100):
- All coefficients remain within float32 precision range
- Polynomial fit errors < 0.001 pixels (~5 mm)
- Near-field accuracy: ~0.00001 pixels (~0.05 mm)

**Float32 precision impact:** Using float32 for the kilometer-based polynomial adds only ~0.0005 pixels of error compared to float64, which is negligible.

The analysis script validating this choice is available at `analysis/polynomial_accuracy_analysis.py`.

#### Distance Conversions

The system maintains clear separation between units:
- **Polynomial parameter `s`**: Always in kilometers (CPU segments, GPU kernel, and emulator now use km end-to-end)
- **Physical calculations** (chord distance, slopes): Always in meters
- **Planar-to-chord conversion**: Takes meters, returns meters
- **Output/reporting**: Distances reported in meters for consistency

The conversion happens at the boundaries:
- CPU builds ray samples in meters, then stores all `RaySegment` distances (start/end/chord) in km before passing them to GPU/Emulator
- GPU evaluates polynomials in km but converts back to meters whenever it needs physical distances (planar->chord, curvature, slope tests), ensuring per-DEM passes stay in sync even when rays span multiple nested DEMs
- Emulator mirrors the exact same km↔m boundaries so multi-DEM passes stay numerically aligned

#### Chord Distance vs Tangent Distance (Critical Geometry)

The GPU kernel's spherical curvature formula expects **chord distance** (straight-line 3D distance from observer to surface point), NOT tangent distance (distance along the flat tangent plane). This distinction is critical for accurate slope calculations at long ranges.

**Definitions:**
- **Tangent Distance**: Distance walked along the flat plane tangent to the sphere at the observer's position. Formula: `s_tangent = R * tan(θ)` where θ is the central angle.
- **Chord Distance**: Straight-line 3D distance from observer to the surface point. Formula: `s_chord = 2R * sin(θ/2)`.
- **Arc Distance**: Distance along the curved surface. Formula: `s_arc = R * θ`.

**Key relationship:** For any θ > 0: `chord < arc < tangent`. At 400km tangent distance on the Moon:
- Tangent: 400 km
- Chord: ~392 km  
- Difference: ~8 km (2%)

**How `BuildRaySamples` works:**
1. Walks along the **tangent direction** in 3D space: `sample = observerVec + dirMeNormalized * tangentDist`
2. Projects this point onto the sphere to get lat/lon: `VecME2LatLon(sample)`
3. Converts lat/lon to pixel coordinates for polynomial fitting
4. Computes the **chord distance** from observer to the surface point at radius R
5. Stores `(chordDistance, pixelX, pixelY)` in the sample list

The `TrySampleWithChordDistance` helper computes chord distance as:
```csharp
var surfacePoint = LatLonToVectorMeters(lat, lon, R);  // Point on sphere at radius R
chordDistMeters = (surfacePoint - observerVec).Length;  // 3D distance
```

**Why this matters:** The GPU kernel uses the formula:
```
z_local = ((h - z_obs) * (2R + h + z_obs) - s²) / (2(R + z_obs))
```
This formula solves for vertical drop given the hypotenuse `s`. If `s` is inflated (tangent instead of chord), the calculated drop is exaggerated, producing artificially negative slopes.

**Historical bug (fixed Jan 2026):** `BuildRaySamples` previously stored tangent distance as `s`. At 400km, this caused ~0.002 slope error (~0.1° horizon angle error). Fixed by computing and storing chord distance instead.


### CURRENT STATUS

The project is in active development. It contains a CPU-based reference implementation of a horizon generator and a high-performance implementation that uses the GPU and approximations that speed up execution.  We believe the reference implementation is correct and are developing and debugging the high-erformance implementation by comparing it's results against the reference.

### New, High-Performance Ray Casting Implementation: `QuadTreeHorizonGenerator`

A new, highly optimized ray casting approach has been implemented in `moonlib/QuadTreeHorizonGenerator.cs`. This implementation addresses the performance and accuracy challenges of traditional ray casting by leveraging **Min-Max Quadtrees (Pyramids)** for efficient terrain traversal.

**Diagnostics Callback:**
The `QuadTreeHorizonGenerator` now supports an optional diagnostics callback mechanism. If set, this callback is invoked whenever a horizon buffer (such as the final merged result, near-field, or per-DEM pass) is available. The callback receives an enumerated value (`HorizonDiagnosticsBuffer`) indicating the buffer type (e.g., `FarField`, `NearField`, `DEM1`, ..., `DEMN`) and a read-only span of horizon angles **in degrees**. This replaces the previous approach of writing diagnostic files to disk, allowing for more flexible and testable diagnostics handling.

**Type Safety with `HorizonAngles`:**
All public APIs in `QuadTreeHorizonGenerator` now return `HorizonAngles` structs instead of raw `float[]` arrays. The `HorizonAngles` struct wraps a `float[] Degrees` property and provides utility methods such as `FromRadians()`, `Clone()`, and `AsSpan()`. GPU kernels compute horizon data in radians (via `XMath.Atan(slope)`), which are converted to degrees using `HorizonAngles.ConvertRadiansToDegreesInPlace()` before being wrapped in the struct and returned to callers. This design ensures that all horizon data exposed through public APIs is clearly typed as angles in degrees, eliminating ambiguity and preventing unit conversion bugs (such as the double-conversion bug previously found in `HorizonComparator`). A parallel `HorizonSlopes` struct exists for APIs that explicitly deal with slope data rather than angles.

**Key Features:**

*   **Min-Max Pyramid Data Structure**: For each Digital Elevation Model (DEM), a multi-level pyramid is constructed. Each level stores the maximum elevation for blocks in the finer level, enabling hierarchical queries.
*   **GPU-Accelerated Pyramid Construction**: Pyramids are built on the GPU using ILGPU (`DownsampleKernel`). Results are cached to `.pyr.bin` for reuse.
*   **GPU MARCHES IN PIXEL SPACE ONLY**: The production kernel evaluates rays purely in DEM pixel-space. There is no CRS or geodetic computation inside the GPU loop.
*   **CPU does 3D geodesic and fitting**: The CPU casts each azimuth ray in true 3D space (great-circle on the sphere) using **double precision** math to ensure exact alignment with reference geodesics. It fits either a single cubic polynomial per azimuth (Standard/Compact Mode) or multiple localized polynomials per azimuth (Subpatch Mode) to reduce approximation error at patch edges. These coefficients are passed to the GPU, which then marches along the cubic paths in pixel-space.
*   **Observer Height Sampling**: The observer’s terrain height is sampled at the DEM post (integer coordinates).
*   **Hierarchical Culling with Toggle**: All production kernels (Standard and Subpatch) perform hierarchical min-max culling (start-level heuristic, drill/skip) in pixel-space. A constructor flag allows disabling the hierarchy to always sample Level 0 with cubic stepping.
*   **Space Skipping and Drill-Down**: For each visited block, max height is used to compute a possible slope; if not competitive with current horizon (plus tiny eps), the ray advances conservatively to the block exit using the local cubic tangent. Otherwise it drills down and samples Level 0.
*   **Multi-DEM Passes**: The pipeline runs a pass per DEM and accumulates the max horizon angle across passes.
*   **Near-Field Ray Casting**: For very close-range terrain (e.g., within 50 meters), a dedicated GPU kernel performs linear ray-marching on a temporary, merged, bordered DEM constructed in GPU memory. This ensures high-fidelity results where the flat-earth approximation is valid and detailed sampling is critical, and its results are merged with the far-field quadtree output.

**Geodetic Accuracy:**
Unlike simple ray casters that assume a flat Cartesian plane, this implementation applies rigorous geodetic corrections to account for the distortions inherent in map projections (e.g., Stereographic).
*   **Scale Factor ($k$)**: Corrects horizontal distances. Map distance is converted to true ground distance using the local scale factor ($k$) calculated at the observer's position.
*   **Grid Convergence ($\gamma$)**: Corrects azimuth. The angle between "Grid North" and "True North" is calculated, allowing the algorithm to rotate the ray to align with the true geographic heading. (Note: Currently disabled pending validation).

**Debugging Implementation:**

To verify the accuracy and behavior of the QuadTree algorithm, a specialized debugging pipeline is implemented via `GenerateDebugProfile`.

*   **Aligned Logic**: The debugging kernel mirrors the production kernel’s pixel-space traversal, consuming the same cubic segments produced by the CPU.
*   **Instrumentation**: Unlike the production kernel which outputs only the final horizon angle, the debug kernel outputs a trace of `DebugSample` structs. These capture the state of the ray at every step, including the decision made (Skip, Drill, Sample), the calculated slope, and the conservative bounds used.
*   **Coordinate Consistency**: The debug pipeline now loads raw DEMs directly (bypassing the obsolete `DemPreprocessor`) and calculates the absolute observer height (`TotalObsZ = TerrainHeight + Offset`) on the CPU using bilinear interpolation. This ensures that the debug trace operates in the same coordinate space and physical geometry as the production and reference generators.

**Enhanced Accuracy**: Pixel-space hierarchical traversal with cubic stepping drills to Level 0 only when needed, avoiding “stepping over” features while keeping the hot loop simple and fast. Parity between kernels ensures that Subpatch Mode provides improved edge accuracy without the performance penalty of brute-force sampling.

**Design Tradeoffs:**
* Pixel-space skip bounds use the local cubic tangent against axis-aligned pixel blocks; CRS AABBs are not required since the GPU operates only in pixel space.
* Epsilon is kept minimal to avoid premature skips; an adaptive term may be tuned via `(levelMapRes / R)` if needed.
* Starting at a higher level improves throughput for distant terrain; near the observer the kernel naturally drills down.
* Guard bands slightly expand block footprints, trading small extra work for robustness against affine skew and rounding.

**Adaptive Stepping Constants:**
* `INV_TAN_MAX_SLOPE = 1.732` — Based on 99th percentile terrain slope of 30° (1/tan(30°)).
* `ANGULAR_STEP_FACTOR = 0.00151` — Derived from 0.05° angular error budget divided by tan(30°).

### COORDINATE TRANSFORMATIONS

#### Local Tangent Frame: ENU (East-North-Up)

The application standardizes on the **East-North-Up (ENU)** local tangent frame for all azimuth and elevation calculations.

- **X-axis**: East (tangent to the local parallel).
- **Y-axis**: North (tangent to the local meridian).
- **Z-axis**: Up (normal to the reference sphere).

#### Matrix Conventions

To ensure mathematical consistency across the project, matrices are constructed using explicit basis vectors:

1.  **ME-to-ENU (World to Local)**: Used by `GetAzEl` to project a world vector (e.g., Sun) into the observer's frame.
    - Matrix columns are basis vectors: `Column0=East`, `Column1=North`, `Column2=Up`.
    - Transformation: `V_enu = (V_me - P_obs) * M`.
    - Implementation: `ElevationMap.GetMoonMEToENU`.

2.  **ENU-to-ME (Local to World)**: Used by horizon generators to cast rays from the observer into the world.
    - Matrix rows are basis vectors: `Row0=East`, `Row1=North`, `Row2=Up`.
    - Transformation: `V_me = V_enu * M`.
    - Implementation: `QuadTreeHorizonGenerator.GetRotationMatrixd` and `ReferenceHorizonGenerator.GetRotationMatrixd`.

### Developer Notes

For those delving into the codebase (`moonlib/`), the following technical details explain key architectural decisions and optimization strategies.

### Memory Layout & Coalescing
The GPU kernel relies on coalesced memory access for performance. The `RaySegment` buffer is transposed into one of three layouts based on the active mode:
- **Compact Mode**: `[Azimuth][DEM]` (Assumes translation invariance across the 128x128 patch).
- **Subpatch Mode**: `[Azimuth][Subpatch][DEM]` (Localized polynomials for 4x4 or 8x8 regions).
- **Full Mode**: `[Azimuth][Pixel][DEM]` (Unique ray per pixel, used near map projection singularities).

This layout ensures that adjacent GPU threads (which typically process adjacent pixels) read from contiguous memory addresses, maximizing memory bandwidth utilization.

### Coordinate Space Strategy
*   **Hybrid Ray Calculation:** To balance precision and performance, the system switches modes based on proximity to the projection singularity (Poles).
    *   **Compact Mode:** Far from the pole (>50km), rays are calculated once per tile center. Translation invariance is assumed (i.e., the ray shape $x(s)$ doesn't change across the tile, only the starting position).
    *   **Subpatch Mode:** A refinement of Compact Mode that uses multiple polynomials to eliminate "edge artifacts" while maintaining high performance via hierarchical traversal.
    *   **Full Mode:** Near the pole, Grid Convergence changes rapidly. Unique rays are calculated for *every pixel* to maintain geodetic accuracy.
*   **Polynomial Fitting:** The GPU does not perform complex geodetic math. The CPU pre-calculates the "true" geodesic path and fits cubic polynomials ($x(s), y(s)$) to it. The GPU simply evaluates these polynomials.

### Math Library: `moonlib.math`
A self-contained mathematics library located in `moonlib/math/`. It mimics standard graphics math libraries (like OpenTK) but is embedded to reduce dependencies.
*   **Vectors**: `Vector2`, `Vector3`, `Vector4` (float and double precision).
*   **Matrices**: `Matrix3`, `Matrix4` (float and double precision).
*   **Quaternions**: `Quaternion` (float and double precision).
*   **Utilities**: `MathHelper`, `Extensions` (unit conversions, intersections).

### Known Approximations
*   **Surface Drop:** The vertical curvature drop is approximated as $2R \sin^2(\theta/2)$, where $\theta = s/R$. This assumes the "vertical" direction rotates uniformly with distance $s$. While highly accurate, it differs slightly from the strict vector-based definition in the Reference implementation at extreme ranges.
*   **Step Size:** The kernel uses adaptive margin-based stepping that balances accuracy and performance:
    *   **Pixel floor:** Never steps smaller than 1 pixel in the active DEM (`dsPixel = 1/tangent_magnitude`).
    *   **Margin-based acceleration:** When the sampled terrain is well below the current horizon, larger steps are safe. The step size scales with `margin * distance * INV_TAN_MAX_SLOPE`, where margin is the difference between the current horizon slope and the sampled slope.
    *   **Angular error budget cap:** Steps are capped at `distance * ANGULAR_STEP_FACTOR` to ensure angular error stays below 0.05° assuming 30° max terrain slope (99th percentile).
    *   The final step is `max(dsPixel, min(dsMargin, dsAngular))`. 
*   **Quadtree Max Slope:** When evaluating whether a quadtree block can be culled, the kernel uses the chord distance to the *nearest* point in the block (entry point), not the current sample point. This provides a conservative (higher) slope estimate for the entire block.

### Ray Sample Generation

For polynomial fitting of ray paths, `BuildRaySamples` generates evenly-spaced samples across the ray's span within each DEM. This ensures well-conditioned matrices for the quartic polynomial fitting, which is critical for large DEMs where the ray may span hundreds of kilometers.

**Key functions in `QuadTreeHorizonGenerator.cs`:**

| Function | Purpose |
|----------|---------|
| `BuildRaySamples` | Main entry point. Walks along tangent direction, samples pixel positions, stores **chord distance** as `s`. Returns list of `(s_km, px, py)` tuples. |
| `TrySampleWithChordDistance` | Given tangent distance, computes pixel coords AND chord distance to surface point at radius R. |
| `TrySampleChord` | Simpler version that only returns pixel coords (used for bounds probing). |
| `FitQuartic4TermsDouble` | Fits 4th-order polynomial to map `s → pixel_offset`. |
| `FitPlanarToChordCubic` | Fits cubic polynomial to map `planar_distance → chord_distance`. Used by GPU to recover true distance from pixel positions. |
| `EnsureMinimumSamples` | Extends sample list if span is too short for stable polynomial fit. |

**Data flow:**
```
Observer position (lat/lon/height)
    ↓
ComputeDirectionVector(obsToMe, azimuth)  →  3D direction in ME frame
    ↓
BuildRaySamples:
    for each tangent distance step:
        TrySampleWithChordDistance(tangentDist) → (px, py, chordDist)
        store (chordDist_km, px, py)
    ↓
FitQuartic4TermsDouble  →  polynomial coeffs (A1-A4, B1-B4)
FitPlanarToChordCubic   →  planar-to-chord coeffs (C1-C3)
    ↓
RaySegment struct passed to GPU
```

### Debugging & Emulation
Two "Emulator" classes exist (`ReferenceRayEmulator.cs` and `QuadTreeRayEmulator.cs`) to allow CPU-based stepping and inspection of the algorithms. These are critical for verifying that the GPU logic (which is hard to debug) matches the intended geometric behavior. They output CSV traces for detailed analysis.

## Testing Strategy

The `tests/HorizonGen.Tests` project exercises the system at several layers so we can catch numerical regressions without re-running the entire GPU pipeline on flight-scale DEMs.

* **Core data structures.** `ElevationMapTests.cs`, `MoonSrsLambdaFactoryTests.cs`, `DemMetadataTests.cs`, and the various `HorizonGenerator*BoundingBox*.cs` suites validate coordinate transforms, GDAL metadata handling, and the geometric utilities that wrap bounding boxes and spiral sampling.
* **Compression and utilities.** `HorizonCompressorTests.cs` plus `CoordinateConversionComparisonTests.cs` keep the serializer and coordinate conversion helpers in sync with historical behaviour.
* **Synthetic DEM coverage.** `SyntheticDemTests.cs` fabricates long/lat and stereographic rasters on the fly so we can deterministically probe: (1) reference horizons over flat planes, (2) ray-segment generation in both compact/full modes, and (3) the CPU emulators (`QuadTreeRayEmulator`, `ReferenceRayEmulator`) operating at DEM borders. These tests ensure sampling guarantees—such as minimum span and per-DEM traces—stay intact whenever we tweak the kernel inputs.
* **GPU ray-caster validation.** `QuadTreeHorizonGeneratorTests.cs` performs structural checks on the min-max pyramid, kernel parameters, and serialization. It uses in-memory DEMs so the ILGPU kernels can be invoked quickly inside `dotnet test`.
* **High-fidelity comparisons.** `SinglePointComparisonTests.cs` replays real VIPER DEM tiles (when present on disk) and cross-checks the generated horizons against the double-precision reference algorithm. When discrepancies exceed the configured threshold (0.25°) the test automatically runs both emulators and drops CSV traces (`reference_trace.csv`, `quadtree_trace.csv`, plus one trace per DEM in multi-pass mode) at the repository root for offline analysis. These tests are skipped automatically if the external GeoTIFFs are unavailable.

To run every test locally, execute `dotnet test` from the repository root. This will build the ILGPU kernels, run the MSTest suite, and—if the external DEM paths are valid—perform the long-running single-point comparisons. When only unit coverage over synthetic DEMs is needed, you can scope the run to `tests/HorizonGen.Tests/HorizonGen.Tests.csproj` and set the DEM environment variables to skip the integration fixtures.

### CompareHorizons Tool

The `CompareHorizons` project is a Windows Forms application designed to visualize and debug the horizon generation process. It allows developers to compare the output of the high-performance QuadTree-based generator against the reference point-projection generator or historical data.

**Key Features:**

*   **Interactive Map View**: Displays Digital Elevation Models (DEMs) with overlays for hillshade, sun illumination, and observer position. Users can pan, zoom, and select specific pixels to investigate.
*   **Horizon Profile Plot**: Shows the generated horizon profile (Elevation Angle vs. Azimuth) for the selected observer. It can overlay multiple datasets:
    *   **QT Horizon (file)**: The horizon loaded from a pre-calculated binary file.
    *   **Ref Horizon**: The "ground truth" horizon generated on-the-fly using the reference algorithm.
    *   **QT Horizon (Pt)**: The horizon generated on-the-fly using the QuadTree algorithm for the single selected point.
    *   **Traces**: Sparse traces showing the path of specific rays.
*   **Ray Tracing & Debugging**:
    *   **Ray Profile View**: A detailed cross-section view of a specific azimuth. It plots the terrain elevation under the ray (Reference vs. QuadTree) as a function of distance. This is critical for identifying exactly *where* the QuadTree algorithm might be skipping or drilling incorrectly.
    *   **Visual Debugging**: The map view draws the actual path of the rays (using the CPU emulators), showing how they traverse different DEMs.
*   **Sun & Lighting**: Can load and display sun illumination maps to visualize how horizons correlate with lighting conditions for specific timestamps.
*   **Time Controller**: Includes a dedicated time control to step through simulation time, updating sun lighting overlays accordingly.