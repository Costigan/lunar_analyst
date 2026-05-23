# Shadow Generation Optimizations

## Overview

This document details the performance optimizations applied to the shadow generation pipeline (`LightmapCommand.cs`) in the `build` project. The primary goal was to resolve a memory bandwidth bottleneck and improve CPU utilization during the calculation of lighting factors from pre-computed horizons.

## Optimizations Implemented

### 1. Loop Interchange (Pixel-Major Traversal)

**Previous Implementation:**
The algorithm iterated through **Time Steps** (outer loop) and then **Pixels** (inner loop).
```csharp
foreach (var time in times) {
    foreach (var pixel in pixels) {
        // Read horizon[pixel] (random access or stride)
        // Compute sun fraction
    }
}
```
**Problem:** The horizon data for a single patch is approximately 90 MB (128x128 pixels * 1440 samples * 4 bytes). With 360 time steps, the previous approach streamed this 90 MB array from main memory to the CPU cache 360 times, resulting in massive memory bandwidth consumption (~32 GB transferred per patch) and thrashing the CPU cache.

**New Implementation:**
The algorithm now iterates through **Pixels** (outer loop) and then **Time Steps** (inner loop).
```csharp
foreach (var pixel in pixels) {
    // Load horizon[pixel] into L1 Cache (~5.7 KB)
    foreach (var time in times) {
        // Compute sun fraction using cached data
    }
}
```
**Benefit:** The horizon data for a pixel is loaded into the L1/L2 cache once and reused for all 360 time steps. This reduces the memory bandwidth requirement by a factor of ~360x, shifting the workload from being memory-bound to CPU-bound.

### 2. FastSunFraction with Unsafe Pointers

**Previous Implementation:**
Used standard C# array indexing (`buffer[index]`) inside the hot loop.
**Problem:** C# performs bounds checking on every array access. In a tight loop executing billions of times, these checks add significant overhead.

**New Implementation:**
Implemented `FastSunFraction` using `unsafe` code and pointers (`float*`).
```csharp
fixed (float* ptr = horizons) {
    // Pointer arithmetic instead of array indexing
    float val = *(ptr + offset);
}
```
**Benefit:** Eliminates array bounds checks, reducing instruction count in the most critical execution path.

### 3. Early Exit Optimization

**New Feature:**
Before performing the expensive geometric integration of the sun disk against the horizon profile, the algorithm now checks if the sun is strictly above or below the local horizon sector.

```csharp
// Get min/max horizon height in the sun's azimuth sector
float maxH = Max(h0, h1, h2);
float minH = Min(h0, h1, h2);

// Early Exit: Sun fully above horizon
if ((el_deg - SunHalfAngle) > maxH) return 1.0f;

// Early Exit: Sun fully below horizon
if ((el_deg + SunHalfAngle) < minH) return 0.0f;
```

**Benefit:** Avoids expensive integration logic for the majority of cases where the sun is clearly visible or clearly occulted.

### 4. Buffered Output and Batch Writing

**Previous Implementation:**
Wrote results to GDAL datasets potentially frequently, involving repeated locking mechanism overhead.

**New Implementation:**
Results are buffered in memory (`byte[][]`) for all time steps during the calculation phase. Writing to the GDAL datasets occurs in a batch at the end of processing a patch.

**Benefit:**
*   Minimizes thread contention on GDAL dataset locks.
*   Separates the "Compute" phase from the "I/O" phase, allowing for cleaner execution.

## Performance Impact

The combination of these optimizations transforms the workload from a memory-bandwidth-starved process to an efficient compute-intensive one.

*   **Memory Bandwidth:** Reduced by ~99%.
*   **CPU Utilization:** Improved core saturation due to reduced pipeline stalls waiting for memory.
*   **Throughput:** significantly increased patch processing speed.
