# Pipeline Usage Example

This document shows how to use the `GenerateHorizonsForAllPatches` method to generate horizon files for all patches in a DEM.

## Basic Usage

```csharp
using horizongen;
using OSGeo.GDAL;

// Initialize GDAL
Gdal.AllRegister();

// Load your nested DEMs (primary DEM first)
var dems = new List<ElevationMap>
{
    ElevationMap.Load("path/to/inner_dem.tif"),    // Primary (inner) DEM
    ElevationMap.Load("path/to/middle_dem.tif"),   // Middle DEM
    ElevationMap.Load("path/to/outer_dem.tif")     // Outer DEM
};

// Create generator instance
using var generator = new QuadTreeHorizonGenerator();

// Generate horizon files for all 128x128 patches
string outputDirectory = "path/to/output/horizons";
float observerElevation = 2.0f; // 2 meters above terrain

try
{
    generator.GenerateHorizonsForAllPatches(
        outputDirectory, 
        dems, 
        observerElevation
    );
    
    Console.WriteLine("Successfully generated all horizon files!");
}
catch (ArgumentException ex)
{
    Console.WriteLine($"Invalid input: {ex.Message}");
}
```

## Requirements

### DEM Dimensions

The primary (inner) DEM **must** have dimensions that are even multiples of 128:

✅ **Valid dimensions**:
- 128 × 128
- 256 × 256
- 512 × 512
- 1024 × 1024
- 2048 × 2048
- 4096 × 4096
- 128 × 256 (width and height can differ)

❌ **Invalid dimensions**:
- 100 × 100 (not a multiple of 128)
- 256 × 300 (height not a multiple of 128)
- 1000 × 1000 (not a multiple of 128)

If the DEM dimensions are invalid, the method will throw an `ArgumentException` immediately.

## Output

### File Naming Convention

Files are named using the pattern:
```
horizon_{tileX:D5}_{tileY:D5}_{observerElevation*10:D3}.bin
```

Examples:
- `horizon_00000_00000_020.bin` - Patch at (0,0) with 2.0m observer elevation
- `horizon_00128_00256_020.bin` - Patch at (128,256) with 2.0m observer elevation
- `horizon_00000_00000_015.bin` - Patch at (0,0) with 1.5m observer elevation

### File Format

Each file contains:
- **Size**: 94,371,840 bytes (128 × 128 × 1440 × 4)
- **Format**: Raw binary float32 array
- **Units**: Angles in degrees
- **Layout**: `[pixel_row][pixel_col][azimuth]` flattened to 1D array
- **Azimuth Range**: 0° to 360° in 0.25° increments (1440 values)

### Example Output Structure

For a 512×512 DEM:
```
output/horizons/
├── horizon_00000_00000_020.bin
├── horizon_00128_00000_020.bin
├── horizon_00256_00000_020.bin
├── horizon_00384_00000_020.bin
├── horizon_00000_00128_020.bin
├── horizon_00128_00128_020.bin
├── horizon_00256_00128_020.bin
├── horizon_00384_00128_020.bin
├── horizon_00000_00256_020.bin
├── horizon_00128_00256_020.bin
├── horizon_00256_00256_020.bin
├── horizon_00384_00256_020.bin
├── horizon_00000_00384_020.bin
├── horizon_00128_00384_020.bin
├── horizon_00256_00384_020.bin
└── horizon_00384_00384_020.bin
```

Total: 16 files (4×4 patches)

## Configuration Options

The generator can be configured with constructor parameters:

```csharp
using var generator = new QuadTreeHorizonGenerator(
    disableHierarchy: false,              // Use hierarchical pyramid traversal
    enableNearFieldReferenceMerge: true,  // Merge near-field high-fidelity results
    nearFieldClampMeters: 250f,           // Near-field distance threshold
    diagnosticsCallback: null             // Optional diagnostics callback
);
```

### Parameters

- **disableHierarchy**: 
  - `false` (default): Use hierarchical min-max pyramid for efficient traversal
  - `true`: Always traverse at finest level (slower, but more straightforward)

- **enableNearFieldReferenceMerge**:
  - `false` (default): Use quadtree results only
  - `true`: Merge with high-fidelity near-field ray casting

- **nearFieldClampMeters**:
  - Distance threshold (in meters) for near-field processing
  - Default: 250m
  - Only used if `enableNearFieldReferenceMerge = true`

- **diagnosticsCallback**:
  - Optional callback for capturing intermediate horizon buffers
  - Useful for debugging and validation

## Performance

### Example Timings

For a 4096×4096 primary DEM with 3 nested DEMs:

| Stage | Time | Notes |
|-------|------|-------|
| Stage 1: Load Pyramids | 0.5s | Cached from previous run |
| Stage 2: Calculate Segments | 25s | Parallel on 24 CPU cores |
| Stage 3: GPU + File I/O | 80s | 1024 patches × ~78ms each |
| **Total** | **105.5s** | **~1.75 minutes** |

### Scaling

- **Linear with patch count**: Doubling DEM size quadruples patch count and time
- **Linear with DEM count**: Each additional DEM adds proportional GPU time
- **Parallel CPU scaling**: Ray segment calculation scales with available cores

## Comparison with Single-Patch API

### Single-Patch API (Existing)

```csharp
// Generate horizons for one 128x128 patch
var horizons = generator.GenerateHorizons(
    dems, 
    tileX: 0, 
    tileY: 0, 
    width: 128, 
    height: 128, 
    observerElevation: 2.0f
);

// Manually write to file
string fileName = QuadTreeHorizonGenerator.BuildDefaultFileName(0, 0, 2.0f);
string filePath = Path.Combine(outputDirectory, fileName);
Utilities.WriteBinaryArray(filePath, horizons.Degrees);
```

### Multi-Patch Pipeline API (New)

```csharp
// Generate horizons for all patches in one call
generator.GenerateHorizonsForAllPatches(
    outputDirectory, 
    dems, 
    observerElevation: 2.0f
);
// All files automatically written
```

### Advantages of Pipeline API

1. **Convenience**: Single method call instead of manual loops
2. **Performance**: Parallel CPU pre-calculation of ray segments
3. **Validation**: Automatic DEM dimension checking
4. **Logging**: Built-in progress reporting
5. **Consistency**: Guaranteed correct file naming and layout

## Error Handling

### Common Errors

**Invalid DEM dimensions:**
```csharp
// Throws: ArgumentException
// Message: "Primary DEM width (1000) must be an even multiple of 128."
generator.GenerateHorizonsForAllPatches(outputDir, dems, 2.0f);
```

**Missing DEMs:**
```csharp
// Throws: ArgumentException
// Message: "At least one DEM is required."
var emptyDems = new List<ElevationMap>();
generator.GenerateHorizonsForAllPatches(outputDir, emptyDems, 2.0f);
```

**Invalid output directory:**
```csharp
// Throws: ArgumentException
// Message: "Output directory must be provided."
generator.GenerateHorizonsForAllPatches("", dems, 2.0f);
```

## Integration with Tests

The existing test suite continues to work unchanged, as the single-patch API is preserved:

```csharp
[TestMethod]
public void TestSinglePatch()
{
    using var generator = new QuadTreeHorizonGenerator();
    var horizons = generator.GenerateHorizons(testDems, 0, 0, 128, 128, 2.0f);
    
    Assert.IsNotNull(horizons);
    Assert.AreEqual(128 * 128 * 1440, horizons.Length);
}
```

New tests for the pipeline can be added:

```csharp
[TestMethod]
public void TestPipelineGeneratesAllPatches()
{
    using var generator = new QuadTreeHorizonGenerator();
    string outputDir = Path.Combine(Path.GetTempPath(), "test_horizons");
    
    // Use 256x256 DEM -> 4 patches
    generator.GenerateHorizonsForAllPatches(outputDir, testDems, 2.0f);
    
    // Verify 4 files created
    var files = Directory.GetFiles(outputDir, "horizon_*.bin");
    Assert.AreEqual(4, files.Length);
    
    // Verify expected filenames
    Assert.IsTrue(File.Exists(Path.Combine(outputDir, "horizon_00000_00000_020.bin")));
    Assert.IsTrue(File.Exists(Path.Combine(outputDir, "horizon_00128_00000_020.bin")));
    Assert.IsTrue(File.Exists(Path.Combine(outputDir, "horizon_00000_00128_020.bin")));
    Assert.IsTrue(File.Exists(Path.Combine(outputDir, "horizon_00128_00128_020.bin")));
}

[TestMethod]
[ExpectedException(typeof(ArgumentException))]
public void TestPipelineRejectsInvalidDimensions()
{
    using var generator = new QuadTreeHorizonGenerator();
    
    // Create DEM with invalid dimensions (not multiple of 128)
    var invalidDem = CreateTestDEM(width: 1000, height: 1000);
    var dems = new List<ElevationMap> { invalidDem };
    
    // Should throw ArgumentException
    generator.GenerateHorizonsForAllPatches(outputDir, dems, 2.0f);
}
```

## Summary

The `GenerateHorizonsForAllPatches` method provides an efficient, convenient way to generate horizon files for entire DEMs. It:

- ✅ Validates input automatically
- ✅ Processes patches efficiently with CPU/GPU overlap
- ✅ Handles file naming and I/O consistently
- ✅ Provides progress logging
- ✅ Integrates seamlessly with existing code
- ✅ Preserves backward compatibility with existing tests

For most use cases, this is the recommended API for batch horizon generation.
