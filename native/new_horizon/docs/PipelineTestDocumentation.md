# Pipeline Test Documentation

## Test File: PipelineHorizonGeneratorTests.cs

This test file validates the `GenerateHorizonsForAllPatches` method using both real VIPER DEMs and synthetic test data.

## Test Cases

### 1. GenerateHorizonsForAllPatches_VIPER_DEMs

**Purpose**: Integration test using real VIPER flight-scale DEMs

**DEM Configuration**:
- Inner DEM: `D:/datasets/viper_v71_2024_medium/other/dem.tif` (4992 × 5248)
- Middle DEM: `D:/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif`
- Outer DEM: `D:/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif`

**Patch Calculation**:
- Patch size: 128 × 128 pixels
- Number of patches in X: 4992 / 128 = 39
- Number of patches in Y: 5248 / 128 = 41
- **Total patches**: 39 × 41 = **1599 patches**

**Adjustable Parameter N**:
The test has a variable `N` that controls how many patches to process:
```csharp
int N = totalPatches;  // Start with 1599, adjust as needed
```

**To adjust N for faster testing**:
1. Open `PipelineHorizonGeneratorTests.cs`
2. Find line ~51: `int N = totalPatches;`
3. Change to desired value, e.g.: `int N = 10;` for quick testing

**Test Behavior**:
- If `N == totalPatches`: Uses the efficient `GenerateHorizonsForAllPatches()` pipeline
- If `N < totalPatches`: Processes first N patches manually using the single-patch API

**Verifications**:
- ✅ Correct number of files generated (N files)
- ✅ First file exists with correct name format
- ✅ File size is correct (94,371,840 bytes = 128 × 128 × 1440 × 4)
- ✅ All horizon values are finite (no NaN or Inf)

**Categories**: `[Integration]`, `[Pipeline]`

**Expected Runtime** (with full 1599 patches):
- With RTX 5090 Mobile: ~2-3 hours
- Average: ~4-6 seconds per patch

**Suggested N values for testing**:
- Quick smoke test: `N = 4` (~20 seconds)
- Development testing: `N = 10` (~50 seconds)
- Validation run: `N = 100` (~8 minutes)
- Full production: `N = 1599` (~2-3 hours)

---

### 2. GenerateHorizonsForAllPatches_ValidatesDimensions

**Purpose**: Unit test validating DEM dimension requirements

**Test DEM**: 1000 × 1000 synthetic DEM (invalid - not divisible by 128)

**Expected Behavior**: Should throw `ArgumentException` with message containing "multiple of 128"

**Verifications**:
- ✅ Exception is thrown
- ✅ Exception message mentions "multiple of 128" requirement

**Categories**: `[Integration]`, `[Pipeline]`

**Expected Runtime**: < 1 second (fast)

---

### 3. GenerateHorizonsForAllPatches_ValidDimensions_SmallDEM

**Purpose**: Fast integration test with valid small synthetic DEM

**Test DEM**: 256 × 256 synthetic flat terrain (valid - 2×2 patches)

**Expected Output**: 4 horizon files
- `horizon_00000_00000_020.bin`
- `horizon_00128_00000_020.bin`
- `horizon_00000_00128_020.bin`
- `horizon_00128_00128_020.bin`

**Verifications**:
- ✅ Exactly 4 files generated
- ✅ All expected filenames exist
- ✅ No errors during processing

**Categories**: `[Fast]`, `[Pipeline]`

**Expected Runtime**: < 5 seconds

---

## Running the Tests

### Run all pipeline tests:
```bash
dotnet test --filter "TestCategory=Pipeline"
```

### Run only fast tests:
```bash
dotnet test --filter "TestCategory=Fast&TestCategory=Pipeline"
```

### Run only the VIPER DEM test:
```bash
dotnet test --filter "FullyQualifiedName~GenerateHorizonsForAllPatches_VIPER_DEMs"
```

### Run with verbose output:
```bash
dotnet test --filter "TestCategory=Pipeline" --logger "console;verbosity=detailed"
```

---

## Test Output

### Console Output Example:
```
Total patches available: 1599 (39x41)
Processing first N patches: 10
Processing patch 1/10 (10.0%)
Generated 10 horizon files in 52.34 seconds
Average time per patch: 5.23 seconds
Test completed successfully. Output directory: C:\Users\...\TestResults\PipelineTest_VIPER
```

### Generated Files:
Files are written to: `{TestRunDirectory}/PipelineTest_VIPER/`

Example files:
```
horizon_00000_00000_020.bin  (94,371,840 bytes)
horizon_00128_00000_020.bin  (94,371,840 bytes)
horizon_00256_00000_020.bin  (94,371,840 bytes)
...
```

---

## Adjusting Test Parameters

### To test with fewer patches:

Edit line ~51 in `PipelineHorizonGeneratorTests.cs`:

**Original**:
```csharp
int N = totalPatches;  // 1599 patches
```

**For quick testing**:
```csharp
int N = 10;  // Only process first 10 patches
```

**For validation**:
```csharp
int N = 100;  // Process first 100 patches
```

### To change observer elevation:

Edit line ~67:
```csharp
float observerElevation = 2.0f;  // Change to desired height in meters
```

### To change output directory:

Edit line ~59:
```csharp
var outputDir = Path.Combine(TestContext.TestRunDirectory ?? Path.GetTempPath(), "PipelineTest_VIPER");
```

---

## Test Maintenance Notes

### If DEM paths change:
Update the constants at the top of the test class:
```csharp
private const string InnerDemPath = @"D:/datasets/viper_v71_2024_medium/other/dem.tif";
private const string MiddleDemPath = @"D:/viper/maps/gsfc/site_20v2/Site20v2_final_adj_5mpp_surf.tif";
private const string OuterDemPath = @"D:/viper/maps/lola/LDEM_80S_20M-2017-06-15-processed.tif";
```

### If DEMs are not available:
The test will be marked as **Inconclusive** (not failed) with a message:
```
Assert.Inconclusive: Inner DEM not found: D:/datasets/viper_v71_2024_medium/other/dem.tif
```

This allows the test suite to run without external DEMs while still validating the implementation with synthetic data.

---

## Performance Benchmarking

Use the VIPER DEM test for performance benchmarking:

1. Set `N = 100` for consistent measurements
2. Run multiple times to account for variability
3. Monitor GPU utilization during test
4. Check log output for stage-by-stage timings

Expected results with RTX 5090 Mobile:
- Stage 1 (Pyramids): 0.5-1s (cached)
- Stage 2 (Ray segments): 15-25s (CPU parallel)
- Stage 3 (GPU + I/O): 400-500s for 100 patches

Total: ~7-9 minutes for 100 patches

---

## Troubleshooting

### Test fails with "DEM not found":
- Verify DEM paths exist
- Or mark test as Inconclusive (expected behavior)

### Test fails with "not an even multiple of 128":
- The VIPER inner DEM (4992×5248) is valid
- If using different DEMs, verify dimensions

### Test hangs during GPU processing:
- Check GPU is available and accessible
- Monitor GPU memory usage
- Try reducing N to isolate issue

### Files not generated:
- Check output directory permissions
- Verify disk space available (each file is ~94 MB)
- Check test logs for exceptions

---

## Integration with CI/CD

### Recommended CI test configuration:
```yaml
# Fast validation (runs on every commit)
- dotnet test --filter "TestCategory=Fast&TestCategory=Pipeline"

# Extended validation (runs nightly with N=100)
- dotnet test --filter "FullyQualifiedName~VIPER_DEMs" 
  # Requires: Manual edit to set N=100 before CI run
```

### Storage requirements:
- Fast tests: ~400 MB (4 files)
- N=10: ~950 MB (10 files)
- N=100: ~9.5 GB (100 files)
- Full N=1599: ~150 GB (1599 files)

Plan disk space accordingly in CI environments.
