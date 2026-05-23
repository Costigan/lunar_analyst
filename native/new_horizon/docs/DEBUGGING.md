# GPU Kernel Performance Regression Analysis

## Executive Summary

The migration from `QuadTreeRayCastKernel` to `QuadTreeSubpatchRayCastKernel` has resulted in a **4× performance regression** (~2.5s → ~10s per 128×128 patch) that persists even when the subpatch kernel is configured to be functionally identical to the original (128×128 subpatches = 1 subpatch per patch).

## Kernel Comparison

### Original Kernel: `QuadTreeRayCastKernel`
- **Location**: `moonlib/QuadTreeHorizonGenerator.cs:2563`
- **Usage**: Called by `LaunchRayCasting()` method (line ~1966)
- **Memory Layout**: `[Azimuth][DEM]` = 1440×3 = **4,320 ray segments per patch**
- **Polynomial Fitting**: Single polynomial fit for entire 128×128 patch
- **Performance**: **~2.5-3.0 seconds per patch** ✅
- **Grid Convergence**: Uses tile-relative coordinates `(colInTile, rowInTile)`
- **Ray Calculation**: Two modes based on distance from poles:
  - **Compact Mode**: Single ray per azimuth at tile center (>50km from pole)
  - **Full Mode**: Unique ray per pixel near poles

### Subpatch Kernel: `QuadTreeSubpatchRayCastKernel`  
- **Location**: `moonlib/QuadTreeHorizonGenerator.cs:3194`
- **Usage**: Called by pipeline and subpatch methods
- **Memory Layout**: `[Azimuth][Subpatch][DEM]` = 1440×64×3 = **276,480 ray segments per patch** (16×16 subpatches)
- **Polynomial Fitting**: 64 localized polynomial fits per patch (one per 16×16 subpatch)
- **Performance**: **~10 seconds per patch** ❌ (4× slower)
- **Grid Convergence**: **FIXED BUG** - Now uses tile-relative coordinates like original
- **Ray Calculation**: Always uses subpatch-based approach

## Performance Test Results

### Baseline Confirmation
**Test**: `CompactModeValidationTests.CompactMode_Performance_Test`
- **Original kernel performance**: 2.5-3.0 seconds ✅
- **Expected subpatch performance**: Similar when using 128×128 subpatches
- **Actual subpatch performance**: ~10 seconds ❌

### GPU Profiling Analysis (NVIDIA RTX 5090)

#### CUDA Kernel Launch Configuration
```
Grid: (16, 1440, 1)
Block: (1024, 1, 1)
```

#### Key Metrics from `ncu` Profiler
| Metric | Value | Analysis |
|--------|--------|----------|
| **DRAM Throughput** | 0.03-0.05% | Very low - not memory bandwidth bound |
| **L1 Cache Hit Rate** | 98-99% | Excellent - memory access patterns are efficient |
| **SM Throughput** | 83-86% | High - compute bound, not memory bound |
| **Global Memory Requests** | 16M - 103M | **Highly variable between kernel launches** |
| **Warp Divergence** | 0.00% | No divergence issues |
| **Instructions Per Cycle** | 0.85-0.86 | Good utilization |

#### Critical Observation
**Memory request variance**: Some kernel launches show 6× more memory requests (103B vs 16M), suggesting:
- Inconsistent memory access patterns
- Possible cache thrashing between different launch configurations
- Algorithm differences causing scatter/gather patterns

## Root Cause Theories

### 1. **Algorithmic Differences** 🔍 **MOST LIKELY**
- **Subpatch overhead**: Even with 1 subpatch (128×128), the kernel may use different code paths
- **Loop unrolling**: Different compiler optimizations between kernels
- **Register pressure**: Subpatch kernel may use more registers, reducing occupancy
- **Memory stride patterns**: Different data access patterns even with same total memory

### 2. **Memory Layout Impact** 
- **Original**: Contiguous `[1440][3]` arrays per patch
- **Subpatch**: Sparse `[1440][64][3]` with only `[1440][1][3]` used when subpatchSize=128
- **Problem**: GPU may not optimize sparse access patterns efficiently
- **Cache efficiency**: Different stride patterns may cause cache line conflicts

### 3. **Grid Convergence Calculation Overhead**
- **Fixed bug**: Subpatch was using wrong coordinates for Grid Convergence
- **Current**: Both kernels now use tile-relative coordinates  
- **Remaining issue**: Subpatch kernel may still have more complex coordinate calculations
- **Per-subpatch overhead**: Even 1 subpatch may have computational overhead

### 4. **Compiler Optimization Differences**
- **ILGPU compilation**: Different kernel signatures may trigger different CUDA compiler paths
- **Register allocation**: Subpatch kernel may have worse register usage
- **Instruction scheduling**: Different instruction ordering affecting throughput
- **Branch prediction**: Even identical logic may compile differently

### 5. **GPU Occupancy Issues** 🔍 **INVESTIGATE**
- **Thread block configuration**: Same (1024,1,1) but different kernel characteristics
- **Shared memory usage**: Subpatch kernel may use more shared memory
- **Register count**: Higher register usage reduces active warps per SM
- **Memory coalescing**: Different access patterns affecting memory bandwidth

## Debugging Evidence

### What We Know ✅
1. **Baseline confirmed**: Original kernel = 2.5s (verified with test)
2. **Bug fixed**: Grid Convergence coordinate system corrected
3. **Memory efficient**: 98-99% cache hit rates rule out bandwidth issues
4. **Compute bound**: 85% SM throughput indicates algorithm, not memory bottleneck
5. **No divergence**: 0% warp divergence rules out branch efficiency issues

### What's Concerning ❌
1. **Persistent 4× slowdown**: Even 128×128 subpatches (functionally identical) are slow
2. **Variable memory requests**: 6× difference between kernel launches suggests instability
3. **No obvious bottleneck**: All major GPU metrics look reasonable
4. **Algorithm mystery**: Subpatch kernel should be identical when subpatchSize=128

## Next Steps - Debugging Strategy

### Phase 1: Deep GPU Analysis 🔬
1. **Occupancy profiling**: 
   ```bash
   ncu --metrics "sm__warps_active.avg.pct_of_peak_sustained_active,sm__maximum_warps_per_active_sm" horizon_runner.exe 4
   ```

2. **Register usage comparison**:
   ```bash
   ncu --metrics "smsp__inst_executed.avg.per_cycle_active,smsp__thread_inst_executed.avg.per_cycle_active" horizon_runner.exe 4
   ```

3. **Memory coalescing efficiency**:
   ```bash
   ncu --metrics "l1tex__data_pipe_lsu_wavefronts_mem_shared.sum,l1tex__data_pipe_lsu_wavefronts_mem_global.sum" horizon_runner.exe 4
   ```

### Phase 2: Code-Level Investigation 🔍
1. **ILGPU kernel inspection**: Generate PTX/SASS code for both kernels and compare
2. **Memory access patterns**: Add debug output to verify identical data access
3. **Polynomial calculation audit**: Verify subpatch=128 uses same math as original
4. **Coordinate system verification**: Double-check all coordinate transformations

### Phase 3: Controlled Experiments 🧪
1. **Hybrid kernel test**: Create version that uses original algorithm but subpatch infrastructure
2. **Memory layout test**: Test original kernel with subpatch memory layout
3. **Parameter sweep**: Test subpatch sizes 128→64→32→16 to find performance cliff
4. **Baseline restoration**: Temporarily revert to original kernel to confirm restoration

### Phase 4: Alternative Approaches 💡
If subpatch kernel can't be fixed:
1. **Dual-kernel approach**: Use original for 128×128, subpatch for smaller sizes
2. **Memory layout optimization**: Restructure subpatch data for better cache efficiency  
3. **Algorithmic hybrid**: Keep original polynomial fitting, add subpatch accuracy improvements
4. **GPU architecture specific**: Different approaches for different GPU generations

## Critical Questions to Answer

1. **Why do 128×128 subpatches perform differently than the original compact algorithm?**
   - Same input data, same output, but 4× performance difference

2. **What causes the 6× variance in memory requests between kernel launches?**
   - Suggests non-deterministic behavior or unstable optimization

3. **Is the subpatch kernel using a fundamentally different algorithm internally?**
   - Need to verify identical computational paths

4. **Are there hidden coordinate transformation overheads?**
   - Grid Convergence bug was fixed, but other coordinate calculations may differ

5. **Could ILGPU compiler be generating suboptimal code for the subpatch kernel?**
   - Different kernel signatures might trigger different optimization paths

## Success Criteria

1. **Performance parity**: 128×128 subpatch should match original kernel (~2.5s)
2. **Scalability**: Smaller subpatches (64, 32, 16) should show accuracy benefits
3. **Memory efficiency**: GPU memory usage should remain under 2GB for full pipeline
4. **Deterministic behavior**: Consistent performance across multiple runs

---

**Document Status**: Active investigation  
**Last Updated**: 2026-02-04  
**Next Review**: After Phase 1 profiling complete