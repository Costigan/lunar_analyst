# Running the Pipeline Test in horizon_runner

## Overview

A new test mode (case 5) has been added to `horizon_runner/Program.cs` that demonstrates the patch-based pipeline API.

## Running the Test

### Command Line

```bash
# Run with default settings (10 patches)
dotnet run --project horizon_runner 5

# Or build and run the executable directly
cd horizon_runner\bin\Release\net9.0
horizon_runner.exe 5
```

### Environment Variables

Control the test behavior using environment variables:

#### `PIPELINE_PATCH_COUNT`
Number of patches to process (default: 10)

```bash
# PowerShell
$env:PIPELINE_PATCH_COUNT = "50"
dotnet run --project horizon_runner 5

# Bash/Linux
export PIPELINE_PATCH_COUNT=50
dotnet run --project horizon_runner 5
```

#### `QUADTREE_NEARFIELD_MERGE`
Enable near-field reference merge (default: false)

```bash
$env:QUADTREE_NEARFIELD_MERGE = "true"
dotnet run --project horizon_runner 5
```

#### `QUADTREE_NEARFIELD_METERS`
Near-field distance threshold in meters (default: 250)

```bash
$env:QUADTREE_NEARFIELD_METERS = "100"
dotnet run --project horizon_runner 5
```

## Configuration

### DEM Paths

The test uses the DEMs defined at the top of `Program.cs`:

```csharp
var dem_paths = new List<string>
{
    "D:/datasets/viper_v71_2024_medium/other/dem.tif",
    "D:/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif",
    //"D:/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif"
};
```

### Patch Selection

The default implementation processes the first N patches:

```csharp
int N = 10;  // Adjust this value
var patchesToProcess = allPatches.Take(N).ToList();
```

**To customize patch selection**, edit case 5 in `Program.cs`:

```csharp
// Example filters:

// First 100 patches
var patchesToProcess = allPatches.Take(100).ToList();

// Patches 100-199
var patchesToProcess = allPatches.Skip(100).Take(100).ToList();

// Every 10th patch (sampling)
var patchesToProcess = allPatches.Where((p, i) => i % 10 == 0).ToList();

// Specific region (top-left quadrant)
var patchesToProcess = allPatches.Where(p => p.PatchX < 20 && p.PatchY < 20).ToList();

// Random selection
var random = new Random(42);
var patchesToProcess = allPatches.OrderBy(x => random.Next()).Take(50).ToList();
```

## Output

### Console Output

```
Pipeline Mode: Generating horizons for patches
Total patches available: 1599
Processing 10 patches
Starting pipelined horizon generation for 10 patches
Primary DEM dimensions: 4992x5248
Stage 1: Building/loading pyramids for 2 DEMs...
Pyramids ready in 0.52s
Stage 2: Pre-calculating ray segments for 10 patches (parallel on CPU)...
Ray segments pre-calculated in 2.34s
Stage 3: Processing 10 patches on GPU and writing results...
Progress: 10/10 (100.0%) | Patch time: 5.31s | Avg: 5.25s/patch | ETA: 0s
GPU processing completed in 52.50s
Total pipeline time: 55.36s for 10 patches (5.54s per patch)
Horizon files written to: D:\projects\new_horizon\horizon_runner\bin\Release\net9.0\output_pipeline
Time taken: 55.89 sec
```

### Output Files

Horizon files are written to `./output_pipeline/` relative to the executable:

```
output_pipeline/
├── horizon_00000_00000_000.bin
├── horizon_00128_00000_000.bin
├── horizon_00256_00000_000.bin
└── ...
```

Each file is ~90 MB (128×128×1440×4 bytes).

## Comparison with Other Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| 0 | Legacy generators (disabled) | Historical |
| 1 | Horizon comparator | Validation |
| 2 | Single patch generation | Testing specific location |
| 3 | Debug single ray | Ray traversal debugging |
| 4 | Debug near-field | Near-field algorithm testing |
| **5** | **Pipeline batch generation** | **Production horizon generation** |

## Performance Expectations

### With Default Settings (10 patches)
- Stage 1 (Pyramids): ~0.5s (cached)
- Stage 2 (Ray segments): ~2-3s (parallel CPU)
- Stage 3 (GPU processing): ~50-60s (10 patches × 5s/patch)
- **Total**: ~55-65 seconds

### Scaling

| Patches | Estimated Time | Output Size |
|---------|---------------|-------------|
| 10 | ~1 minute | ~900 MB |
| 100 | ~8-10 minutes | ~9 GB |
| 1000 | ~90-100 minutes | ~90 GB |
| 1599 (all) | ~2.5-3 hours | ~144 GB |

*Times based on RTX 5090 Mobile with 24-core CPU*

## Tips

### Quick Validation Run
```bash
$env:PIPELINE_PATCH_COUNT = "4"
dotnet run --project horizon_runner 5
```
Processes just 4 patches (~20 seconds) to verify everything works.

### Extended Test Run
```bash
$env:PIPELINE_PATCH_COUNT = "100"
dotnet run --project horizon_runner 5
```
Processes 100 patches (~8 minutes) for more thorough validation.

### Full Production Run
```bash
$env:PIPELINE_PATCH_COUNT = "1599"
dotnet run --project horizon_runner 5
```
Processes all patches (~2-3 hours) for complete dataset.

### With Near-Field Merge
```bash
$env:QUADTREE_NEARFIELD_MERGE = "true"
$env:QUADTREE_NEARFIELD_METERS = "100"
$env:PIPELINE_PATCH_COUNT = "10"
dotnet run --project horizon_runner 5
```

## Logging

Logs are written to `log.txt` in the executable directory:
- Pipeline progress
- Timing information
- Error messages (if any)

## Troubleshooting

### "DEM file not found"
- Verify DEM paths in `Program.cs` line 27-32
- Update paths to match your system

### "DEM dimensions must be multiple of 128"
- The primary (inner) DEM must have dimensions divisible by 128
- VIPER DEM (4992×5248) is valid

### GPU out of memory
- Reduce `PIPELINE_PATCH_COUNT`
- Close other GPU-intensive applications

### Slow performance
- Check GPU utilization (should be near 100% during Stage 3)
- Verify DEMs are on fast storage (SSD recommended)
- Check pyramid cache files (.pyr.bin) are being used

## Summary

Case 5 provides a convenient way to test and run the pipelined horizon generation from the command line. Adjust `PIPELINE_PATCH_COUNT` to control how many patches are processed, and use LINQ filters in the code for more advanced selection patterns.
