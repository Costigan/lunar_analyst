# Pipelined Horizon Generation Algorithm

## Overview

This document describes the algorithm implemented in `GenerateHorizonsForAllPatches` for efficiently generating horizon files for all 128×128 pixel patches within a nested DEM configuration.

## Design Goals

1. **Minimize Total Execution Time**: Overlap CPU and GPU work to maximize throughput
2. **Efficient Resource Usage**: Leverage multi-core CPU (24 logical cores) and GPU (RTX 5090 Mobile)
3. **Memory Efficiency**: Reuse GPU buffers, avoid unnecessary allocations
4. **Maintainability**: Reuse existing validated code paths

## Algorithm Stages

### Stage 1: Load and Prepare DEMs (Once per Batch)

**Input**: List of nested elevation maps (DEMs), with primary (inner) DEM first

**Operations**:
1. **Validate DEM Dimensions**: Verify that primary DEM width and height are even multiples of 128
   - Throw `ArgumentException` immediately if validation fails
   - This ensures clean patch boundaries with no partial patches

2. **Calculate Patch Grid**: 
   - `numPatchesX = demWidth / 128`
   - `numPatchesY = demHeight / 128`
   - `totalPatches = numPatchesX × numPatchesY`

3. **Build/Load Pyramids** (Min-Max Quadtrees):
   - For each DEM, call `BuildOrLoadPyramid(dem)`
   - Pyramids are multi-level data structures used for hierarchical ray traversal
   - **Caching**: Pyramids are cached to filesystem as `.pyr.bin` files
   - **Cost**: O(pixels) to build, O(1) to load from cache
   - **Memory**: All pyramids loaded to GPU memory simultaneously

**Output**: List of GPU-resident pyramids, one per DEM

**Performance**: 
- First run: Builds pyramids (seconds to minutes depending on DEM size)
- Subsequent runs: Fast (milliseconds to seconds) due to filesystem caching

---

### Stage 2: Pre-Calculate Ray Segments (Parallel, CPU)

**Purpose**: Overlap CPU work across all patches before GPU processing begins

**Operations**:
1. **Create Primary Pyramid View**: Reference to the primary DEM's pyramid for observer coordinates

2. **Parallel Ray Segment Calculation**:
   ```
   Parallel.For(0, totalPatches, patchIndex =>
   {
       Calculate tileX, tileY from patchIndex (row-major order)
       Call CalculateRaySegments() for this 128×128 patch
       Store result in patchData[patchIndex]
   })
   ```

3. **Ray Segment Details**:
   - **Azimuth Count**: 1440 rays (0.25° angular resolution)
   - **Ray Representation**: 4th-order polynomial in pixel-space coordinates
   - **Coordinate System**: Double-precision geodetic math (CPU), cast to float32 for GPU
   - **Mode Selection**: Compact (far from pole) or Full (near pole)
   - **Per-DEM Segments**: Segments calculated for each nested DEM

**Computational Complexity**:
- **Per Patch**: O(azimuths × DEMs × samples) ≈ O(1440 × numDEMs × 20) floating-point operations
- **Total**: O(totalPatches × 1440 × numDEMs)
- **Parallelism**: Embarrassingly parallel across patches (no shared state)

**Memory Layout**:
- **Compact Mode**: `[Azimuth][DEM]` - One ray per azimuth, translated per pixel
- **Full Mode**: `[Azimuth][Pixel][DEM]` - Unique ray per pixel (near poles)

**Output**: Array of `PatchSegmentData` structs containing pre-calculated segments for each patch

**Performance**: 
- With 24 cores, processes 24 patches simultaneously
- Typical time: 0.1-0.5 seconds per patch (varies with proximity to pole)

---

### Stage 3: Process Patches on GPU (Sequential)

**Purpose**: Execute GPU kernels and write results to disk

**Strategy**: Sequential processing avoids GPU resource contention and simplifies memory management

**For Each Patch (Row-Major Order)**:

1. **Launch Ray Casting**:
   - Call `LaunchRayCasting()` with pre-calculated segments
   - **Multi-Pass Execution**: One GPU kernel pass per DEM
   - **Hierarchical Traversal**: Min-max pyramid culling for efficiency
   - **Output**: Horizon angles (1440 azimuths × 128×128 pixels)

2. **Near-Field Merge** (if enabled):
   - Call `ComputeNearFieldBlock()` for high-fidelity near-field horizons
   - Merge with far-field results: `result[i] = max(farField[i], nearField[i])`

3. **Write to File**:
   - Generate filename: `horizon_{tileX:D5}_{tileY:D5}_{obsElevation*10:D3}.bin`
   - Write binary array: 128 × 128 × 1440 × 4 bytes = 94,371,840 bytes per file
   - **Format**: Raw float32 arrays (angles in degrees)

**GPU Kernel Details**:
- **Kernel**: `QuadTreeRayCastKernel`
- **Parallelism**: 2D grid (pixels × azimuths)
- **Per-Thread Work**: Traverse one ray through one DEM's pyramid
- **Accumulation**: Max across all DEM passes

**Output**: Horizon files written to output directory

**Performance**:
- GPU kernel: ~5-50ms per patch per DEM pass (varies with hierarchy effectiveness)
- File write: ~10-50ms per file (depends on disk I/O)
- Progress logged every 10 patches

---

## Pipeline Characteristics

### Parallelism Strategy

| Stage | Parallelism | Hardware | Dependencies |
|-------|-------------|----------|--------------|
| 1. Pyramid Build | Sequential | CPU + GPU | None |
| 2. Ray Segments | Parallel (24-way) | CPU | Pyramids from Stage 1 |
| 3. GPU Processing | Sequential | GPU | Segments from Stage 2 |

### Why Sequential GPU Processing?

**Alternative Considered**: Process multiple patches on GPU simultaneously

**Decision**: Sequential processing chosen for:
1. **Simplicity**: Single set of GPU buffers, no synchronization complexity
2. **Memory Management**: Fixed memory footprint, predictable behavior
3. **GPU Saturation**: Each patch uses full GPU (128×128×1440 = 23.6M threads)
4. **Minimal Overhead**: GPU processing is fast relative to total pipeline time

### CPU/GPU Overlap

The pipeline achieves overlap through **Stage 2** pre-calculation:
- While GPU processes patches 1-N, all ray segments are already calculated
- CPU only needs to issue kernel launches and write files (minimal overhead)
- GPU becomes the bottleneck, which is optimal

### Memory Footprint

**GPU Memory**:
- Pyramids: ~1-5 GB total (varies with DEM count and resolution)
- Working buffers: ~400 MB (horizon accum, horizon pass, segments, debug)
- **Total**: ~2-6 GB (well within modern GPU capacity)

**System Memory**:
- Pre-calculated segments: ~100 KB per patch × totalPatches
- Example: 1000 patches = ~100 MB
- **Total**: <1 GB for typical workloads

---

## Algorithmic Complexity

Let:
- P = total patches = (demWidth / 128) × (demHeight / 128)
- A = azimuths = 1440
- D = number of DEMs
- N = 128 × 128 = 16,384 pixels per patch

**Time Complexity**:
- Stage 1: O(pixels × D) - Pyramid build (cached, amortized O(1))
- Stage 2: O(P × A × D) - Ray segment calculation (parallel)
- Stage 3: O(P × N × A × D) - GPU ray casting (sequential patches)

**Space Complexity**:
- GPU: O(pixels × D) - Pyramids and working buffers
- CPU: O(P × A × D) - Pre-calculated segments

---

## Error Handling

1. **Invalid DEM Dimensions**: 
   - Check: Width % 128 == 0 and Height % 128 == 0
   - Action: Throw `ArgumentException` immediately

2. **Missing DEMs**: 
   - Check: dems.Count > 0
   - Action: Throw `ArgumentException`

3. **Directory Creation**: 
   - Action: `Directory.CreateDirectory()` (creates if needed)

4. **File Write Failures**: 
   - Handled by `Utilities.WriteBinaryArray()`

---

## Integration with Existing Code

The new method **reuses existing validated components**:
- `BuildOrLoadPyramid()` - Pyramid construction/caching
- `CalculateRaySegments()` - Double-precision geodetic ray calculation
- `LaunchRayCasting()` - Multi-pass GPU kernel execution
- `ComputeNearFieldBlock()` - Near-field reference merging
- `BuildDefaultFileName()` - Filename generation

**Existing API Preserved**:
- `GenerateHorizons()` methods remain unchanged
- Tests continue to work without modification
- New method is additive, not breaking

---

## Performance Expectations

**Example Scenario**: 4096×4096 primary DEM, 3 nested DEMs

- Patches: 32 × 32 = 1024 patches
- Stage 1: ~5s (first run) or ~0.5s (cached)
- Stage 2: ~10-30s (parallel on 24 cores)
- Stage 3: ~30-100s (GPU + file I/O)
- **Total**: ~45-135 seconds for 1024 patches

**Scaling**:
- Linear in number of patches: O(P)
- Linear in number of DEMs: O(D)
- Constant per patch (independent)

---

## Future Optimizations

Potential improvements not implemented in this version:

1. **Asynchronous File I/O**: Write files on background threads while GPU processes next patch
2. **GPU Pipeline Depth**: Process 2-3 patches on GPU simultaneously with careful buffer management
3. **Adaptive Batch Sizing**: Group small patches for better GPU utilization
4. **Distributed Processing**: Split patches across multiple machines/GPUs

These optimizations add significant complexity and were deemed unnecessary given the current performance characteristics.

---

## Testing Strategy

**Unit Tests** (should be added):
1. Test DEM dimension validation (should throw for non-multiples of 128)
2. Test patch iteration order (verify row-major)
3. Test filename generation (verify correct format)

**Integration Tests**:
1. Run on small synthetic DEM (256×256, 4 patches)
2. Compare output files to single-patch `GenerateHorizons()` output
3. Verify file count matches expected patch count

**Performance Tests**:
1. Measure scaling with patch count
2. Measure CPU vs GPU time breakdown
3. Profile memory usage

---

## Conclusion

The pipelined algorithm efficiently processes large DEM datasets by:
1. **Front-loading CPU work** (Stage 2 parallel ray calculation)
2. **Maximizing GPU utilization** (Stage 3 sequential processing)
3. **Minimizing memory overhead** (reuse buffers, cache pyramids)
4. **Maintaining code quality** (reuse existing validated components)

The design is optimal for the target hardware (24-core CPU, high-end GPU) and scales linearly with dataset size.
