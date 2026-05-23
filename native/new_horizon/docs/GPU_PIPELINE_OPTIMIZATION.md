# GPU Pipeline Optimization

## Overview

This document describes the implementation of a CPU/GPU pipeline optimization for the `QuadTreeHorizonGenerator` that significantly improves GPU utilization when processing multiple patches.

## Problem Statement

The original implementation processed patches sequentially:
1. Calculate ray segments for patch N on CPU
2. Process patch N on GPU (blocking synchronization)
3. Write results to disk
4. Repeat for patch N+1

This approach caused GPU idle time because the GPU would wait for CPU segment calculation, and the CPU would wait for GPU processing to complete.

## Solution: Producer-Consumer Pipeline

The new implementation uses a producer-consumer pattern with buffer pooling to overlap CPU and GPU work:

### Key Components

1. **Buffer Pool Management** (`BufferPool` class)
   - Manages reusable GPU memory buffers
   - Eliminates allocation/deallocation overhead
   - Thread-safe buffer checkout/return

2. **Asynchronous GPU Processing** (`LaunchRayCastingAsync` method)
   - Launches GPU kernels without blocking synchronization
   - Returns `Task<HorizonAngles>` for async coordination
   - Removes debug output for pipeline efficiency

3. **Producer-Consumer Channels** (using `System.Threading.Channels`)
   - **Producer**: Calculates ray segments on background thread
   - **Consumer**: Processes GPU work on background thread
   - **Writer**: Main thread saves results to disk

### Pipeline Stages

```
Producer Thread          Consumer Thread         Main Thread
================         ================       ===========
Calculate Segments 1 --> Process GPU 1      --> Write Results 1
Calculate Segments 2 --> Process GPU 2      --> Write Results 2
Calculate Segments 3 --> Process GPU 3      --> Write Results 3
     ...                      ...                    ...
```

## Implementation Details

### Buffer Pool Structure
```csharp
private class PipelineBuffers : IDisposable
{
    public MemoryBuffer1D<float, Stride1D.Dense> HorizonsAccum { get; set; }
    public MemoryBuffer1D<float, Stride1D.Dense> HorizonsPass { get; set; }
    public MemoryBuffer1D<RaySegment, Stride1D.Dense> GpuSegments { get; set; }
    public MemoryBuffer1D<float, Stride1D.Dense> Debug { get; set; }
    public bool InUse { get; set; }
}
```

### Pipeline Workflow
1. **Stage 1**: Build/load pyramids in parallel (unchanged)
2. **Stage 2**: Producer-consumer pipeline:
   - Producer calculates segments for all patches
   - Consumer gets buffer from pool, launches GPU work async
   - Writer saves completed results to disk
   - Buffers are returned to pool for reuse

### Async Method Changes

- `GenerateHorizonsForPatches()` → `async Task`
- `GenerateHorizonsForAllPatches()` → `async Task`
- Added `LaunchRayCastingAsync()` method
- Updated all callers to use `await`

## Performance Benefits

1. **Continuous GPU Utilization**: GPU processes patches while CPU calculates segments for upcoming patches
2. **Memory Efficiency**: Buffer pool eliminates repeated allocation/deallocation
3. **Scalable Parallelism**: Multiple buffer sets can be allocated based on available GPU memory
4. **Reduced Overhead**: Single-kernel approach vs multiple sequential launches

## API Changes

### Before
```csharp
generator.GenerateHorizonsForPatches(outputDir, dems, patches, observerElevation);
```

### After
```csharp
await generator.GenerateHorizonsForPatches(outputDir, dems, patches, observerElevation);
```

### Updated Files
- `moonlib/QuadTreeHorizonGenerator.cs`: Core pipeline implementation
- `horizon_runner/Program.cs`: Updated to use async/await
- `tests/HorizonGen.Tests/PipelineHorizonGeneratorTests.cs`: Updated test to be async

## Configuration

The pipeline uses the existing configuration constants:
- `DEFAULT_SUBPATCH_SIZE = 16`: Subpatch size for polynomial approximation
- Buffer pool automatically scales based on patch processing needs

## Error Handling

- Producer/Consumer tasks have proper exception handling
- Failed tasks are logged and propagated to main thread
- Buffer pool ensures proper resource cleanup on errors
- All GPU buffers are properly disposed

## Backwards Compatibility

The changes maintain full API compatibility:
- All existing parameters and behavior preserved
- Only method signatures changed to async
- Same output files and format
- Existing configuration options work unchanged

## Future Optimizations

Potential further improvements:
1. **Multi-GPU Support**: Distribute patches across multiple GPU devices
2. **Adaptive Buffer Pool**: Dynamically adjust buffer count based on GPU memory usage
3. **Batch Kernel Launches**: Process multiple patches in single kernel launch for small patches
4. **Async File I/O**: Overlap disk writes with GPU processing

## Validation

The implementation has been validated to:
- Produce identical results to original sequential version
- Build successfully with all existing tests
- Maintain proper resource cleanup and error handling
- Support all existing patch filtering and configuration options

This optimization should significantly improve performance for large batch processing while maintaining the accuracy and reliability of the original implementation.