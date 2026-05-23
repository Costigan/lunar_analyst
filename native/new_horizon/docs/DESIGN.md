# New Horizon System Design Document

## 1. Project Context & Objectives

**new_horizon** is a high-performance C# application designed for generating horizon profiles from Digital Elevation Models (DEMs), specifically developed for planetary science and rover path planning applications. The primary use case is the VIPER lunar mission, where accurate terrain visibility calculations are critical for lighting analysis, communication planning, and path finding.

### Core Goals
- **Generate horizon profiles** from 2D height-field terrain data (DEMs) with 0.25° angular resolution (1440 bins per horizon)
- **Planetary-scale performance** through GPU acceleration using ILGPU for CUDA/OpenCL backends
- **Scientific accuracy** with rigorous geodetic corrections for map projection distortions
- **Dual implementation strategy** providing both reference (ground truth) and high-performance (GPU-accelerated) implementations

### Next Evolution (Expected Features)
- Multi-target planetary body support (Mars, asteroids) beyond lunar applications
- Real-time horizon generation for live mission support
- Integration with rover navigation and communication planning systems
- Advanced caching and distributed processing capabilities

## 2. High-Level Architecture Overview

The system follows a modular architecture with clear separation between computation engines, data processing, and validation tooling:

```
┌─────────────────┐    ┌──────────────────┐
│  horizon_runner │────│     moonlib      │
│  (Console App)  │    │  (Core Library)  │
└─────────────────┘    └──────────────────┘
                              │
                    ┌─────────┼─────────┐
                    │         │         │
        ┌──────────────┐ ┌────────────┐ ┌─────────────┐
        │ Reference    │ │ QuadTree   │ │ Data        │
        │ Generator    │ │ Generator  │ │ Processing  │
        │ (CPU/Double) │ │ (GPU/Fast) │ │ (GDAL/CRS)  │
        └──────────────┘ └────────────┘ └─────────────┘
```

### External Dependencies
- **GDAL 3.11.3**: Geospatial data processing, DEM loading (GeoTIFFs)
- **ILGPU 1.5.3**: GPU acceleration framework (CUDA/OpenCL)
- **Serilog**: Structured logging
- **NAIF SPICE**: Planetary ephemeris and coordinate transformations
- **.NET 9.0**: Runtime platform

### Data Flow
1. **Input**: GeoTIFF DEMs → **GDAL** → Elevation data + CRS metadata
2. **Processing**: Ray casting through **Reference** or **QuadTree** generators
3. **Output**: Binary horizon files (`.bin`) or compressed formats
4. **Validation**: Native tests and runner outputs for debugging and analysis

## 3. Codebase Structure & Navigation

```
new_horizon/
├── moonlib/                    # Core computation library
│   ├── math/                   # Self-contained math library (vectors, matrices)
│   ├── pipeline/               # Data processing pipeline components
│   ├── spice/                  # NAIF SPICE wrapper utilities
│   ├── util/                   # General utilities and helpers
│   ├── ElevationMap.cs         # DEM data management and CRS handling
│   ├── ReferenceHorizonGenerator.cs  # CPU reference implementation
│   ├── QuadTreeHorizonGenerator.cs   # GPU accelerated implementation
│   ├── HorizonCompressor.cs    # Data compression for output horizons
│   └── [Other core classes]
├── horizon_runner/             # Console application entry point
│   ├── Program.cs              # Main entry, run mode selection (0-5)
│   └── [Utility classes]
├── tests/HorizonGen.Tests/     # MSTest suite
│   ├── SyntheticDemTests.cs    # In-memory DEM testing
│   ├── QuadTreeHorizonGeneratorTests.cs  # GPU validation
│   └── SinglePointComparisonTests.cs     # High-fidelity integration tests
└── docs/                       # Documentation
```

### Key Entry Points
- **horizon_runner/Program.cs**: Console application with configurable run modes (0-5)
- **moonlib/ElevationMap.cs**: Primary interface for DEM data loading and coordinate systems
- **moonlib/QuadTreeHorizonGenerator.cs**: Main high-performance implementation
- **tests/**: MSTest suite entry point for validation

### Module Boundaries
- **moonlib**: Core algorithms, data structures, and GPU kernels (no UI dependencies)
- **horizon_runner**: CLI interface and pipeline orchestration
- **tests**: Validation and regression testing

## 4. Core Functional Components

### 4.1 Digital Elevation Model (DEM) Management
**Primary Class**: `ElevationMap`
- **Responsibility**: DEM loading, coordinate system management, CRS transformations
- **Key Features**:
  - GDAL-based GeoTIFF reading with metadata extraction
  - Coordinate Reference System (CRS) conversions between pixel, projected, and geodetic coordinates
  - Support for multiple lunar coordinate systems (Stereographic, LongLat)
  - Bilinear interpolation for sub-pixel sampling

### 4.2 Reference Horizon Generator
**Primary Class**: `ReferenceHorizonGenerator`
- **Responsibility**: Ground truth horizon calculation using CPU double-precision mathematics
- **Key Features**:
  - Point-projection approach with exact geometric calculations
  - Double-precision coordinate transformations (critical for polar regions ~85°S)
  - Rigorous geodetic corrections for map projection distortions
  - Used as baseline for validating GPU implementation

### 4.3 QuadTree Horizon Generator (High-Performance)
**Primary Class**: `QuadTreeHorizonGenerator`
- **Responsibility**: GPU-accelerated horizon generation using hierarchical data structures
- **Key Features**:
  - **Min-Max Pyramid Construction**: Multi-level quadtree for efficient terrain queries
  - **GPU-Accelerated Pipeline**: ILGPU kernels for ray casting and point projection
  - **Hierarchical Culling**: Skip low-significance terrain using min-max bounds
  - **Multiple Ray Modes**:
    - Compact Mode: Single ray per tile (translation invariant)
    - Subpatch Mode: Localized polynomials for improved edge accuracy
    - Full Mode: Unique ray per pixel (near projection singularities)
  - **Near-Field Ray Casting**: Dedicated kernel for close-range high-fidelity sampling
  - **Multi-DEM Support**: Processes multiple overlapping DEMs with accumulation

### 4.4 Coordinate System & Geodetic Engine
**Primary Components**: `MoonSrsLambdaFactory`, coordinate transformation utilities
- **Responsibility**: Accurate coordinate conversions and geodetic calculations
- **Key Features**:
  - East-North-Up (ENU) local tangent frame standardization
  - Grid convergence and scale factor corrections
  - Matrix-based transformations with explicit basis vectors
  - Support for lunar stereographic and lat/lon projections

### 4.5 Data Compression & Serialization
**Primary Class**: `HorizonCompressor`
- **Responsibility**: Efficient storage and retrieval of horizon data
- **Key Features**:
  - Binary format optimized for 128×128 pixel patches
  - 1440 angular bins per horizon (0.25° resolution)
  - Type-safe angle representation via `HorizonAngles` struct
  - Metadata handling and versioning support

### 4.6 Math Library
**Location**: `moonlib/math/`
- **Responsibility**: Self-contained mathematics library reducing external dependencies
- **Components**:
  - Vector types: `Vector2`, `Vector3`, `Vector4` (float and double precision)
  - Matrix types: `Matrix3`, `Matrix4` (float and double precision)
  - Quaternions, utility functions, and geometric intersection tests

## 5. APIs & Interfaces

### 5.1 Public Horizon Generation API

```csharp
// Primary interface for horizon generation
public class QuadTreeHorizonGenerator
{
    public HorizonAngles GenerateHorizon(
        List<ElevationMap> dems,
        double observerLat,
        double observerLon,
        float observerElevation)
    
    public HorizonAngles GenerateDebugProfile(...)  // Detailed tracing
}

// Type-safe angle representation
public struct HorizonAngles
{
    public float[] Degrees { get; }
    public static HorizonAngles FromRadians(float[] radians)
    public HorizonAngles Clone()
    public Span<float> AsSpan()
}
```

### 5.2 DEM Data Interface

```csharp
public class ElevationMap
{
    // Core data access
    public float[,] Elevation { get; }
    public double[] GeoTransform { get; }
    
    // Coordinate transformations
    public CRSPoint PixelToCRS(PixelPoint pixel)
    public PixelPoint CRSToPixel(CRSPoint crs)
    
    // Geodetic conversions
    public (double lat, double lon) CRSToLongLat(CRSPoint crs)
    public CRSPoint LongLatToCRS(double lat, double lon)
}
```

### 5.3 Diagnostics & Debugging Interface

```csharp
// Diagnostics callback for inspection
public delegate void HorizonDiagnosticsCallback(
    HorizonDiagnosticsBuffer bufferType,
    ReadOnlySpan<float> horizonAngles);

public enum HorizonDiagnosticsBuffer
{
    FarField, NearField, DEM1, DEM2, /* ... */ Final
}
```

### 5.4 External Interface Expectations
- **No network APIs**: All processing is local file-based
- **GDAL Integration**: Standard OGR/GDAL interfaces for geospatial data
- **ILGPU Compatibility**: GPU kernels must be compatible with CUDA/OpenCL runtimes

## 6. Data Schemas & Models

### 6.1 Horizon Data Format

```csharp
// Binary format: horizon_{row:D5}_{col:D5}_{elev*10:D3}.bin
// Structure: 128 × 128 × 1440 × float32 = 94,371,840 bytes per patch
public struct HorizonPatch
{
    public int TileRow;        // Y coordinate of upper-left corner
    public int TileCol;        // X coordinate of upper-left corner  
    public float Elevation;    // Observer elevation in meters
    public float[,,] Angles;   // [row, col, azimuth] in degrees
}
```

### 6.2 DEM Metadata

```csharp
public struct SrsDescriptor
{
    public string Name;           // e.g., "Moon 2000 / South Pole Stereographic"
    public string Authority;      // e.g., "EPSG"
    public int Code;             // EPSG code
    public double CentralMeridian;
    public double StandardParallel;
    public double FalseEasting;
    public double FalseNorthing;
}
```

### 6.3 Ray Segment Data (GPU Kernel Input)

```csharp
public struct RaySegment
{
    // Polynomial coefficients for pixel position as function of distance
    public float A1, A2, A3, A4;  // X(s) = A1*s + A2*s² + A3*s³ + A4*s⁴
    public float B1, B2, B3, B4;  // Y(s) = B1*s + B2*s² + B3*s³ + B4*s⁴
    
    // Planar-to-chord distance conversion
    public float PlanarToChordC1, PlanarToChordC2, PlanarToChordC3;      // chord = C1*s + C2*s² + C3*s³
    
    public float StartDistKm;     // Ray start distance in km
    public float EndDistKm;       // Ray end distance in km
    public float ChordStartKm;    // 3D chord distance at start
    public float ChordEndKm;      // 3D chord distance at end
}
```

### 6.4 Sample Data Records

**Example Horizon File Naming**:
```
horizon_03280_00837_012.bin  # Row 3280, Col 837, Elevation 1.2m
```

**Example CRS Point**:
```csharp
var crsPoint = new CRSPoint(-956431.2, 1823456.7);  // Stereographic meters
var latLon = elevationMap.CRSToLongLat(crsPoint);   // (-85.12°, -45.67°)
```

## 7. Business Rules & Logic

### 7.1 Angular Resolution Requirements
- **Fixed Resolution**: 0.25° angular resolution (1440 bins per horizon)
- **Azimuth Mapping**: Clockwise from North (0° = North, 90° = East, 180° = South, 270° = West)
- **Index Mapping**: `azimuthIndex = (int)(azimuth * 4)` where azimuth is in degrees

### 7.2 Terrain Patch Processing Rules
- **Standard Patch Size**: 128×128 pixels
- **Processing Order**: Row-major ordering (row index first in file naming)
- **Observer Elevation**: Sampled at DEM post (integer coordinates) + specified offset

### 7.3 Coordinate System Transformation Rules
- **Precision Requirements**: Double-precision for all coordinate transformations
- **Unit Consistency**: All global vectors in meters; polynomial parameters in kilometers
- **Chord vs Tangent Distance**: GPU kernels expect chord distance (3D straight-line), not tangent

### 7.4 Multi-DEM Processing Logic
- **Pass Ordering**: Process DEMs in order, accumulating maximum elevation angles
- **Overlap Handling**: Later DEMs override earlier ones where they overlap
- **Boundary Conditions**: Ray segments must span DEM boundaries with minimum sample requirements

### 7.5 GPU Kernel Performance Rules
- **Memory Coalescing**: Buffer layouts optimized for adjacent thread access patterns
- **Hierarchical Culling**: Use min-max bounds to skip insignificant terrain blocks
- **Step Size Constraints**:
  - Never smaller than 1 pixel in active DEM
  - Angular error budget: < 0.05° assuming 30° max terrain slope
  - Margin-based acceleration when terrain is well below current horizon

### 7.6 Numerical Stability Invariants
- **Polynomial Parameter Units**: Always use kilometers for distance parameter `s`
- **Float32 Precision Guards**: Coefficients must remain within float32 precision range
- **Curvature Corrections**: Apply spherical drop formula: `z_local = ((h - z_obs) * (2R + h + z_obs) - s²) / (2(R + z_obs))`

## 8. Non-Functional Requirements & Constraints

### 8.1 Performance Targets
- **GPU Acceleration**: 10-100× speedup over CPU reference implementation
- **GPU Operations**: Use single-precision. Double precision on my 5090 mobile GPU is 64x slower.
- **Memory Usage**: Support processing of multi-gigabyte DEM datasets
- **Patch Processing**: Target sub-second processing per 128×128 patch on modern GPUs
- **Accuracy Requirements**: < 0.1° horizon elevation angle error vs reference implementation

### 8.2 Reliability & Robustness
- **Numerical Stability**: Maintain accuracy at polar latitudes (~85° south lunar regions)
- **GPU Error Handling**: Graceful fallback to CPU implementation on GPU failure
- **Memory Management**: Proper disposal of GPU resources and GDAL handles
- **Deterministic Results**: Reproducible output given same input parameters

### 8.3 Precision & Accuracy Constraints
- **Coordinate Precision**: 64-bit double precision for geodetic transformations
- **Angular Precision**: Final horizon angles accurate to 0.01° or better
- **Distance Precision**: Polynomial fitting errors < 0.001 pixels (~5mm)
- **Curvature Modeling**: Account for lunar spherical geometry (R = 1,737,400m)

### 8.4 Platform & Runtime Constraints
- **Target Platform**: Windows (primary), cross-platform .NET 9.0
- **GPU Requirements**: CUDA-capable or OpenCL-compatible graphics hardware
- **Memory Requirements**: Minimum 8GB RAM for typical lunar DEM processing
- **Disk Space**: Temporary pyramid caches (.pyr.bin files) require additional storage

### 8.5 Security & Data Privacy
- **No External Communication**: All processing performed locally
- **File System Access**: Read-only access to DEM files, write access to output directories
- **No Sensitive Data**: DEM data considered public scientific information

## 9. Build, Test, & Deployment

### 9.1 Local Development Setup

```powershell
# Build the solution
dotnet build new_horizon.sln

# Run all tests
dotnet test

# Run specific test categories
dotnet test --filter "FullyQualifiedName~SyntheticDemTests"
dotnet test --filter "Name=CompareSinglePoint_ReferenceVsQuadTree"
```

### 9.2 Environment Requirements
- **.NET 9.0 SDK**: Required for building and running
- **GDAL 3.11.3**: Automatically installed via NuGet packages
- **GPU Drivers**: CUDA toolkit or OpenCL runtime for GPU acceleration
- **Python Environment**: Optional, for analysis scripts at `D:\projects\env_gdal\Scripts\python.exe`

### 9.3 Configuration Management
- **Run Modes**: Console app supports modes 0-5 via command line argument
- **DEM Paths**: Hardcoded in `Program.cs`, modify as needed for different datasets
- **Logging Configuration**: Serilog configuration in application startup

### 9.4 Test Execution Strategy
- **Synthetic Tests**: Fast unit tests using in-memory generated DEMs
- **Integration Tests**: Real DEM processing (skipped if external files unavailable)
- **GPU Validation**: ILGPU kernel testing with mock data
- **Regression Testing**: Cross-validation between Reference and QuadTree implementations

### 9.5 Deployment Process
- **Standalone Executable**: Self-contained native runner application
- **GDAL Dependencies**: Included via NuGet packages
- **GPU Runtime**: Requires CUDA/OpenCL runtime on target machine
- **Data Files**: DEM files must be accessible to application

## 10. Existing Deficiencies & TODOs

### 10.1 Known Technical Debt
- **Hardcoded DEM Paths**: Configuration should be externalized from `Program.cs`
- **Error Handling**: Need more graceful error handling for GPU initialization failures
- **Memory Management**: Some GPU memory allocations could be better optimized
- **Code Duplication**: Ray building logic duplicated between Reference and QuadTree generators

### 10.2 Missing Features (Planned)
- **Multi-Planet Support**: Currently lunar-specific, needs generalization
- **Real-time Processing**: Batch processing only, no streaming/real-time capabilities
- **Distributed Processing**: No support for multi-machine processing
- **Advanced Caching**: Pyramid caches could be more intelligent about invalidation

### 10.3 Performance Optimizations (TODO)
- **Memory Coalescing**: Further optimization of GPU memory access patterns
- **Kernel Fusion**: Combine multiple GPU kernel launches where possible
- **CPU Parallelization**: Reference implementation could benefit from multi-threading
- **I/O Optimization**: Batch processing of multiple patches

### 10.4 Testing & Validation Gaps
- **Edge Case Coverage**: Need more comprehensive testing near map projection singularities
- **Large Dataset Testing**: Limited testing with very large (>10GB) DEM files  
- **Cross-Platform Testing**: Primary testing on Windows, limited Linux/macOS validation
- **Performance Regression**: No automated performance benchmarking

### 10.5 Documentation & Maintenance
- **API Documentation**: Limited inline documentation for complex algorithms
- **Algorithm Description**: Some mathematical formulations need better documentation
- **Debugging Guides**: Need more comprehensive debugging workflow documentation

## 11. Coding Standards & Conventions

### 11.1 C# Language Standards
- **Target Framework**: .NET 9.0 with nullable reference types enabled
- **Language Features**: Modern C# features encouraged (pattern matching, using declarations)
- **Async/Await**: Used sparingly; most operations are compute-bound
- **Memory Safety**: Careful with unsafe code blocks (used in GPU interop)

### 11.2 Naming Conventions
- **Classes**: PascalCase (e.g., `QuadTreeHorizonGenerator`)
- **Methods**: PascalCase (e.g., `GenerateHorizon`)
- **Fields**: camelCase with underscore prefix for private fields (e.g., `_width`)
- **Constants**: ALL_CAPS with underscores (e.g., `INV_TAN_MAX_SLOPE`)
- **File Naming**: Match primary class name, use PascalCase

### 11.3 Architectural Patterns
- **Separation of Concerns**: Clear boundaries between computation, data access, and UI
- **Dependency Injection**: Minimal DI; prefer explicit dependencies
- **Factory Pattern**: Used for coordinate system transformations (`MoonSrsLambdaFactory`)
- **Strategy Pattern**: Different ray casting modes (Compact, Subpatch, Full)

### 11.4 Error Handling Patterns
- **Exceptions**: Use for exceptional conditions; prefer validation over exceptions
- **Return Types**: Use nullable types or custom result types for expected failures
- **Logging**: Structured logging with Serilog, appropriate log levels
- **Resource Disposal**: Consistent use of `using` statements for GDAL and GPU resources

### 11.5 Git & Version Control
- **Branch Strategy**: Feature branches off main development branch
- **Commit Messages**: Descriptive commits with issue references where applicable
- **Code Reviews**: Required for changes to core algorithms
- **Binary Files**: Use Git LFS for large test data files

## 12. Dependencies & Versioning

### 12.1 Primary Dependencies

| Package | Version | Purpose | Notes |
|---------|---------|---------|--------|
| **GDAL** | 3.11.3 | Geospatial data processing | Critical for DEM reading |
| **ILGPU** | 1.5.3 | GPU acceleration | Core performance dependency |
| **Serilog** | 4.3.0 | Structured logging | Standard throughout |
| **.NET Runtime** | 9.0 | Platform runtime | Latest LTS version |

### 12.2 Math & Graphics Dependencies

| Package | Version | Purpose | Notes |
|---------|---------|---------|--------|
| **System.Drawing.Common** | 9.0.10 | Basic graphics types | Cross-platform compatibility |
| **Self-contained Math** | N/A | Vector/Matrix operations | Reduces external dependencies |

### 12.3 Version Pinning Strategy
- **Critical Dependencies**: GDAL and ILGPU versions are pinned to ensure compatibility
- **Framework Dependencies**: Target specific .NET version for predictability
- **NuGet Package Restore**: All dependencies managed through NuGet package references
- **No Floating Versions**: Avoid version ranges to ensure reproducible builds

### 12.4 Known Compatibility Issues
- **CUDA Compute Capability**: ILGPU requires GPU with Compute Capability 3.0+
- **OpenCL Versions**: Some older OpenCL implementations may have reduced performance
- **GDAL Thread Safety**: Some GDAL operations require careful threading considerations
- **Windows vs Linux**: GDAL native libraries differ between platforms

### 12.5 Upgrade Strategy
- **Dependency Testing**: Comprehensive test suite before dependency upgrades
- **Staging Upgrades**: Test in isolated environment before production
- **Rollback Plan**: Maintain previous working versions in version control
- **Breaking Changes**: Careful evaluation of breaking changes in major version updates

## 13. Error Handling & Logging

### 13.1 Error Propagation Strategy
- **GPU Kernel Failures**: Catch ILGPU exceptions and provide fallback to CPU processing
- **GDAL Errors**: Wrap GDAL exceptions with meaningful context about file operations
- **Mathematical Errors**: Validate inputs to prevent division by zero, invalid coordinates
- **File I/O Errors**: Clear error messages with file paths and access requirements

### 13.2 Logging Architecture

```csharp
// Serilog configuration with structured logging
Log.Logger = new LoggerConfiguration()
    .MinimumLevel.Debug()
    .WriteTo.Console(restrictedToMinimumLevel: LogEventLevel.Information)
    .WriteTo.File("log.txt", rollingInterval: RollingInterval.Day)
    .CreateLogger();
```

### 13.3 Log Levels & Usage
- **Debug**: Detailed algorithm traces, coordinate transformations, kernel parameters
- **Information**: Processing progress, file operations, performance metrics
- **Warning**: Numerical precision issues, fallback operations, missing optional data
- **Error**: Critical failures, invalid inputs, resource allocation problems
- **Fatal**: Application-terminating errors, unrecoverable GPU failures

### 13.4 Error Context & Debugging
- **Diagnostic Callbacks**: Optional callbacks provide detailed algorithm traces
- **CSV Trace Output**: Emulator classes output detailed ray traces for debugging
- **Performance Counters**: GPU memory usage, kernel execution times
- **Coordinate Validation**: Extensive validation of coordinate transformations

### 13.5 Monitoring & Alerting
- **No External Monitoring**: Application designed for local/offline use
- **Console Output**: Real-time status updates during processing
- **Log File Analysis**: Post-processing analysis of log files for performance tuning
- **Test Framework Integration**: Detailed error reporting in automated tests

## 14. Testing Strategy & Coverage

### 14.1 Test Architecture
The testing strategy employs multiple layers to ensure both correctness and performance:

```csharp
tests/HorizonGen.Tests/
├── SyntheticDemTests.cs                    # Fast, deterministic testing
├── QuadTreeHorizonGeneratorTests.cs       # GPU kernel validation
├── SinglePointComparisonTests.cs          # High-fidelity integration
├── ElevationMapTests.cs                   # CRS and coordinate testing
└── [Other component tests]
```

### 14.2 Testing Categories

#### Synthetic DEM Testing
- **Purpose**: Fast, deterministic validation using in-memory generated DEMs
- **Coverage**: Flat planes, peaked terrain, geometric edge cases
- **Benefits**: No external file dependencies, reproducible results
- **Example**: Validate horizon calculation over perfect flat terrain yields zero elevation angles

#### GPU Validation Testing  
- **Purpose**: Structural validation of ILGPU kernels and GPU data structures
- **Coverage**: Pyramid construction, kernel parameters, memory layouts
- **Benefits**: Isolates GPU-specific issues from algorithmic problems
- **Example**: Verify min-max pyramid correctly represents terrain hierarchical bounds

#### High-Fidelity Integration Testing
- **Purpose**: Cross-validation between Reference and QuadTree implementations on real data
- **Coverage**: Real VIPER DEM tiles, multi-DEM processing, edge conditions
- **Failure Handling**: Automatic generation of CSV traces when discrepancies > 0.25°
- **Example**: Process actual lunar terrain and ensure GPU results match reference within tolerance

### 14.3 Coverage Goals & Metrics
- **Core Algorithm Coverage**: 100% coverage of horizon generation APIs
- **Edge Case Coverage**: Comprehensive testing near map projection singularities
- **Error Path Coverage**: Validate error handling for invalid inputs and GPU failures
- **Performance Regression**: Automated detection of significant performance changes

### 14.4 Test Data Management
- **Synthetic Data**: Generated programmatically, no external dependencies
- **Real DEM Files**: External GeoTIFFs, tests skipped if unavailable
- **Test Artifacts**: CSV traces, diagnostic outputs in repository root for analysis
- **Environment Variables**: Configure external DEM paths for integration tests

### 14.5 Continuous Integration Strategy
```powershell
# Standard test execution
dotnet test  # Runs all tests, skips integration tests if DEM files missing

# Specific test categories
dotnet test --filter "FullyQualifiedName~SyntheticDemTests"      # Fast synthetic tests
dotnet test --filter "Name=CompareSinglePoint_ReferenceVsQuadTree" # Integration
dotnet test tests/HorizonGen.Tests/HorizonGen.Tests.csproj     # Scoped execution
```

## 15. Security & Privacy Considerations

### 15.1 Security Model
- **Local Processing Only**: No network communication, all data processing local
- **File System Access**: Read access to DEM files, write access to output directories only
- **No Authentication**: Desktop application, no user authentication required
- **No Sensitive Data**: DEM data considered public scientific information

### 15.2 Data Privacy
- **Input Data**: Digital Elevation Models are publicly available scientific datasets
- **Output Data**: Horizon profiles contain no personally identifiable information
- **Temporary Files**: Pyramid caches (.pyr.bin) contain only terrain data
- **Log Files**: May contain file paths but no sensitive information

### 15.3 System Security
- **Code Injection**: No dynamic code execution or script evaluation
- **Buffer Overflows**: Managed C# with careful unsafe code in GPU interop sections
- **Resource Exhaustion**: GPU memory limits enforced, graceful handling of large datasets
- **File Path Validation**: Validate file paths to prevent directory traversal

### 15.4 Threat Model Assumptions
- **Trusted Execution Environment**: Application runs on user's local machine
- **Trusted Input Data**: DEM files assumed to be valid, non-malicious scientific data
- **No Network Threats**: Offline processing eliminates network-based attack vectors
- **Physical Security**: User responsible for securing local machine and data

### 15.5 Compliance & Auditing
- **No Regulatory Requirements**: Scientific application with no compliance mandates
- **Audit Trail**: Processing logs provide record of operations performed
- **Data Retention**: No automatic data retention policies, user manages output files
- **Export Control**: No export restrictions on digital elevation model processing

## 16. Glossary & Acronyms

### 16.1 Domain-Specific Terms

**Azimuth**: Horizontal angle measured clockwise from North (0°-360°). In this system, 0° = North, 90° = East, 180° = South, 270° = West.

**Chord Distance**: Straight-line 3D distance from observer to surface point, critical for accurate slope calculations. Formula: `2R * sin(θ/2)` where θ is central angle.

**Digital Elevation Model (DEM)**: 2D raster representing terrain height values, typically stored as GeoTIFF files with elevation in meters.

**Elevation Angle**: Vertical angle from horizontal to visible horizon, measured in degrees. The primary output of horizon generation.

**Grid Convergence**: Angular difference between "Grid North" (map projection) and "True North" (geographic), requiring azimuth corrections.

**Horizon Profile**: Array of 1440 elevation angles representing the visible horizon at 0.25° azimuth resolution.

**Min-Max Pyramid**: Hierarchical data structure where each level stores maximum elevations for blocks in the finer level, enabling efficient spatial queries.

**Observer Frame**: Local coordinate system centered at observer position, using East-North-Up (ENU) convention.

**Scale Factor (k)**: Correction factor for map projection distortions, converting map distances to true ground distances.

**Tangent Distance**: Distance along flat plane tangent to sphere at observer position. Formula: `R * tan(θ)`. Larger than chord distance for same central angle.

### 16.2 Technical Acronyms

**CRS**: Coordinate Reference System - defines how coordinates relate to real-world positions

**ENU**: East-North-Up local coordinate frame standard used throughout the system

**GDAL**: Geospatial Data Abstraction Library - used for reading DEM files and coordinate transformations

**ILGPU**: IL-based GPU acceleration framework for C#/.NET

**NAIF SPICE**: NASA Navigation and Ancillary Information Facility spacecraft ephemeris system

**VIPER**: Volatiles Investigating Polar Exploration Rover - NASA lunar mission this system supports

### 16.3 Mathematical Notation

**R**: Lunar radius = 1,737,400 meters

**θ**: Central angle (radians) for spherical geometry calculations

**s**: Distance parameter used in polynomial fitting, always expressed in kilometers

**k**: Scale factor for map projection corrections

**γ**: Grid convergence angle for azimuth corrections

### 16.4 File Format Extensions

**.tif, .tiff**: GeoTIFF format for input Digital Elevation Models

**.bin**: Binary horizon data files (raw float32 arrays)

**.pyr.bin**: Cached pyramid/quadtree files for GPU processing acceleration

**.csv**: Comma-separated trace files output by emulator classes for debugging

## 17. Onboarding & Development Workflow

### 17.1 Developer Prerequisites
- **Development Environment**: Visual Studio 2022 or VS Code with C# extension
- **.NET 9.0 SDK**: Required for building and running the application
- **Git Knowledge**: Familiarity with Git workflow and branching strategies
- **C# Proficiency**: Understanding of modern C# features, async/await, nullable references
- **Optional**: CUDA/OpenCL knowledge for GPU kernel development

### 17.2 Setup Checklist

```powershell
# 1. Clone repository
git clone <repository-url>
cd new_horizon

# 2. Restore packages and build
dotnet restore
dotnet build new_horizon.sln

# 3. Run tests to verify environment
dotnet test

# 4. Try running the console application
cd horizon_runner
dotnet run -- 5  # Run mode 5 (adjust as needed)

# 5. Optional: Set up debugging with real DEM data
# Edit Program.cs to point to available DEM files
```

### 17.3 Sample Development Tasks

#### First Task: Add Logging to Existing Function
1. Find a function in `QuadTreeHorizonGenerator.cs` lacking debug logging
2. Add structured logging using Serilog: `Log.Debug("Processing {Function} with {Parameters}", ...)`
3. Run tests to ensure no regressions
4. Verify log output appears in console

#### Intermediate Task: Add Synthetic DEM Test
1. Examine `SyntheticDemTests.cs` for patterns
2. Create new test method with different terrain shape (e.g., conical peak)
3. Validate both Reference and QuadTree generators produce expected results
4. Ensure test runs quickly (< 1 second) for CI integration

#### Advanced Task: Optimize GPU Kernel Performance
1. Profile existing GPU kernel using ILGPU profiling tools
2. Identify memory access patterns or computational bottlenecks
3. Implement optimization while maintaining numerical accuracy
4. Validate with comprehensive test suite, especially `SinglePointComparisonTests`

### 17.4 Debugging Workflow
1. **Reproduce Issue**: Create failing test or identify specific DEM/coordinates
2. **Enable Diagnostics**: Use `HorizonDiagnosticsCallback` for detailed traces
3. **Run Emulators**: Execute `ReferenceRayEmulator` and `QuadTreeRayEmulator` to generate CSV traces
4. **Compare Results**: Use generated traces, CSVs, and test artifacts for visual analysis
5. **Isolate Problem**: Narrow down to specific azimuth, distance range, or DEM region
6. **Fix and Validate**: Implement fix and ensure all tests pass

### 17.5 Contributing Guidelines
- **Code Reviews**: All changes require review before merging
- **Test Requirements**: New features must include comprehensive tests
- **Documentation**: Update relevant documentation for API changes
- **Performance**: Validate performance impact, especially for GPU kernels
- **Backward Compatibility**: Maintain compatibility with existing horizon file formats

## 18. Change History & Rationale

### 18.1 Key Architectural Decisions

#### Decision: Dual Implementation Strategy (Reference + QuadTree)
- **Rationale**: Scientific applications require ground truth for validation
- **Alternative Considered**: Single GPU implementation only
- **Trade-offs**: Additional complexity vs. confidence in results
- **Outcome**: Enables continuous validation and debugging of high-performance implementation

#### Decision: Polynomial Ray Representation
- **Rationale**: GPU kernels must avoid complex geodetic math; pre-computed polynomials provide efficiency
- **Alternative Considered**: Real-time geodetic calculations on GPU
- **Trade-offs**: Memory usage vs. computational complexity
- **Outcome**: 4th-order polynomials provide excellent accuracy with manageable memory footprint

#### Decision: Kilometers for Polynomial Parameters
- **Rationale**: Numerical conditioning critical for high-order polynomial fitting
- **Alternative Considered**: Meters or normalized coordinates
- **Analysis**: Meters caused coefficient underflow in float32; kilometers maintain precision
- **Validation**: Analysis script at `analysis/polynomial_accuracy_analysis.py`

#### Decision: East-North-Up (ENU) Coordinate Frame
- **Rationale**: Standardization across all azimuth and elevation calculations
- **Alternative Considered**: North-East-Down (NED) or body-fixed coordinates
- **Benefits**: Consistent with surveying conventions, intuitive azimuth mapping
- **Implementation**: Explicit matrix construction with basis vectors

### 18.2 Performance Optimization History

#### Optimization: Memory Layout for GPU Coalescing
- **Problem**: Random memory access patterns causing GPU performance issues
- **Solution**: Transposed buffer layouts: `[Azimuth][DEM]`, `[Azimuth][Subpatch][DEM]`, `[Azimuth][Pixel][DEM]`
- **Impact**: 3-5× GPU memory bandwidth improvement
- **Date**: Implemented during ILGPU integration phase

#### Optimization: Hierarchical Culling with Min-Max Pyramids
- **Problem**: Ray casting sampling every terrain point was computationally expensive
- **Solution**: Multi-level quadtree with conservative culling based on slope bounds
- **Alternative Considered**: Octree or other spatial data structures
- **Impact**: 10-50× speedup for typical lunar terrain
- **Validation**: Maintained < 0.25° accuracy vs reference implementation

### 18.3 Critical Bug Fixes

#### Bug Fix: Chord vs Tangent Distance Confusion (Jan 2026)
- **Problem**: GPU kernel using inflated tangent distance instead of chord distance
- **Symptoms**: Artificially negative slopes at long range (> 400km)
- **Root Cause**: `BuildRaySamples` storing tangent distance in polynomial fit
- **Solution**: Compute and store actual chord distance to surface points
- **Impact**: ~0.1° horizon angle error correction at long ranges

#### Bug Fix: Double-Conversion in Radians/Degrees (Historical)
- **Problem**: `HorizonComparator` applying degrees-to-radians conversion twice
- **Symptoms**: Systematic error in comparison metrics
- **Solution**: Type-safe `HorizonAngles` struct enforcing units
- **Prevention**: All public APIs now use typed angle representations

### 18.4 Feature Evolution

#### Feature: Diagnostics Callback System
- **Original**: Direct file writing for debugging data
- **Evolution**: Callback-based system for flexible diagnostics handling
- **Benefits**: Better testability, no disk I/O dependencies in unit tests
- **Migration**: Backward compatible with existing debugging workflows

#### Feature: Multi-DEM Processing Pipeline
- **Original**: Single DEM processing only
- **Evolution**: Support for multiple overlapping DEMs with accumulation
- **Challenges**: Coordinate system alignment, memory management
- **Solution**: Pass-based processing with horizon angle accumulation

#### Feature: Near-Field Ray Casting
- **Motivation**: QuadTree hierarchy less effective for very close terrain
- **Implementation**: Dedicated GPU kernel with linear ray marching
- **Integration**: Results merged with far-field QuadTree output
- **Threshold**: Typically applied within 50 meters of observer
