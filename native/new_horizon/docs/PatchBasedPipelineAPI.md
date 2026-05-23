# New Patch-Based Pipeline API

## Overview

The horizon generation pipeline has been refactored to support flexible patch selection using LINQ. This allows you to easily filter which patches to process.

## Key Components

### 1. `PatchDescriptor` Class

Represents a single 128×128 pixel patch within a DEM:

```csharp
public class PatchDescriptor
{
    public int TileX { get; set; }      // Column coordinate in DEM
    public int TileY { get; set; }      // Row coordinate in DEM
    public int Index { get; set; }      // Row-major index (0-based)
    public int PatchX { get; set; }     // Column in patch grid
    public int PatchY { get; set; }     // Row in patch grid
}
```

### 2. `GeneratePatchList()` Static Method

Generates a list of all patches for a DEM:

```csharp
public static List<PatchDescriptor> GeneratePatchList(ElevationMap primaryDem)
```

- Returns patches in row-major order (left-to-right, top-to-bottom)
- Validates DEM dimensions are multiples of 128
- Throws `ArgumentException` if validation fails

### 3. `GenerateHorizonsForPatches()` Method

Processes a specified list of patches with enhanced progress reporting:

```csharp
public void GenerateHorizonsForPatches(
    string outputDirectory, 
    List<ElevationMap> dems, 
    List<PatchDescriptor> patches, 
    float observerElevation = 0.0f)
```

**Progress reporting includes:**
- Current patch / Total patches
- Percentage complete
- Time for current patch
- Average time per patch
- **Estimated time to completion (ETA)**

## Usage Examples

### Example 1: Process First N Patches

```csharp
using horizongen;

// Load DEMs
var dems = new List<ElevationMap>
{
    new ElevationMap("inner.tif"),
    new ElevationMap("middle.tif"),
    new ElevationMap("outer.tif")
};

// Generate full patch list
var allPatches = QuadTreeHorizonGenerator.GeneratePatchList(dems[0]);
Console.WriteLine($"Total patches: {allPatches.Count}");

// Select first 10 patches using LINQ
var patchesToProcess = allPatches.Take(10).ToList();

// Process selected patches
using var generator = new QuadTreeHorizonGenerator();
generator.GenerateHorizonsForPatches(
    outputDirectory: "output/horizons",
    dems: dems,
    patches: patchesToProcess,
    observerElevation: 2.0f
);
```

### Example 2: Process Specific Patch Range

```csharp
// Process patches 100-199 (second hundred)
var patchesToProcess = allPatches
    .Skip(100)
    .Take(100)
    .ToList();

generator.GenerateHorizonsForPatches(outputDir, dems, patchesToProcess, 2.0f);
```

### Example 3: Process Patches by Region

```csharp
// Process only patches in a specific rectangular region
// For example: patches in the top-left quadrant
var patchesToProcess = allPatches
    .Where(p => p.PatchX < 20 && p.PatchY < 20)
    .ToList();

generator.GenerateHorizonsForPatches(outputDir, dems, patchesToProcess, 2.0f);
```

### Example 4: Process Every Nth Patch (Sampling)

```csharp
// Process every 10th patch for quick validation
var patchesToProcess = allPatches
    .Where((p, index) => index % 10 == 0)
    .ToList();

generator.GenerateHorizonsForPatches(outputDir, dems, patchesToProcess, 2.0f);
```

### Example 5: Process Patches by Index List

```csharp
// Process specific patches by their indices
var indicesToProcess = new[] { 0, 5, 10, 50, 100, 500 };
var patchesToProcess = allPatches
    .Where(p => indicesToProcess.Contains(p.Index))
    .ToList();

generator.GenerateHorizonsForPatches(outputDir, dems, patchesToProcess, 2.0f);
```

### Example 6: Process All Patches (Original Behavior)

```csharp
// Use convenience method for all patches
generator.GenerateHorizonsForAllPatches(outputDir, dems, 2.0f);

// OR equivalently:
var allPatches = QuadTreeHorizonGenerator.GeneratePatchList(dems[0]);
generator.GenerateHorizonsForPatches(outputDir, dems, allPatches, 2.0f);
```

## Enhanced Progress Output

The new progress reporting provides detailed timing information:

```
Starting pipelined horizon generation for 10 patches
Primary DEM dimensions: 4992x5248
Stage 1: Building/loading pyramids for 3 DEMs...
Pyramids ready in 0.52s
Stage 2: Pre-calculating ray segments for 10 patches (parallel on CPU)...
Ray segments pre-calculated in 2.34s
Stage 3: Processing 10 patches on GPU and writing results...
Progress: 1/10 (10.0%) | Patch time: 5.23s | Avg: 5.23s/patch | ETA: 47s
Progress: 2/10 (20.0%) | Patch time: 5.18s | Avg: 5.20s/patch | ETA: 42s
...
Progress: 10/10 (100.0%) | Patch time: 5.31s | Avg: 5.25s/patch | ETA: 0s
GPU processing completed in 52.50s
Total pipeline time: 55.36s for 10 patches (5.54s per patch)
```

**ETA Format:**
- < 1 minute: "45s"
- 1-60 minutes: "5m 23s"
- > 1 hour: "2h 15m 30s"

## Test Integration

The test has been updated to use the new API:

```csharp
[TestMethod]
public void GenerateHorizonsForAllPatches_VIPER_DEMs()
{
    // Load DEMs
    var dems = new List<ElevationMap> { ... };
    
    // Set N to desired patch count
    int N = 10;  // Adjust this value
    
    // Generate patch list and filter
    var allPatches = QuadTreeHorizonGenerator.GeneratePatchList(dems[0]);
    var patchesToProcess = allPatches.Take(N).ToList();
    
    // Process patches
    using var generator = new QuadTreeHorizonGenerator();
    generator.GenerateHorizonsForPatches(outputDir, dems, patchesToProcess, 2.0f);
    
    // Verify results...
}
```

## Benefits

1. **Flexible Selection**: Use LINQ to select any subset of patches
2. **Better Visibility**: Know upfront how many patches will be processed
3. **Improved Progress**: ETA helps plan workflow and estimate completion
4. **Easier Testing**: Quickly process small subsets for validation
5. **Backward Compatible**: Old `GenerateHorizonsForAllPatches()` still works

## Performance Notes

- **Stage 2 (CPU)**: Scales with number of patches (parallel processing)
- **Stage 3 (GPU)**: Sequential processing with detailed timing
- **Memory**: Pre-calculated segments stored for all selected patches
- **Typical timing**: 4-6 seconds per patch on RTX 5090 Mobile

## Migration Guide

### Old Code:
```csharp
generator.GenerateHorizonsForAllPatches(outputDir, dems, 2.0f);
```

### New Code (All Patches):
```csharp
// No change needed - old method still works!
generator.GenerateHorizonsForAllPatches(outputDir, dems, 2.0f);
```

### New Code (Filtered Patches):
```csharp
var patches = QuadTreeHorizonGenerator.GeneratePatchList(dems[0]);
var selected = patches.Take(100).ToList();  // First 100
generator.GenerateHorizonsForPatches(outputDir, dems, selected, 2.0f);
```

## Summary

The refactored API provides maximum flexibility while maintaining backward compatibility. Use LINQ to select exactly which patches you need, and enjoy improved progress reporting with ETAs for long-running jobs.
