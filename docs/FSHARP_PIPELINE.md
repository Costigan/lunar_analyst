# Design Document: F# Dynamic Reduction Pipeline

## 1. Objective
To optimize the Lunar Analyst lightmap streaming engine by moving temporal reductions (3D $ightarrow$ 2D) from the Python layer to the .NET native layer. This is achieved by allowing users to provide an F# code snippet that is compiled JIT (Just-In-Time) and executed per-pixel at native speed, drastically reducing memory bandwidth and IPC overhead.

## 2. Rationale
*   **Data Volume**: A 1-year lighting simulation with 1-hour steps generates ~8,760 frames. A single 128x128 tile (uint8) is ~140MB.
*   **Current Bottleneck**: Python must receive the full 140MB 3D tensor over the bridge, then perform a slow `np.mean()` or `np.max()`.
*   **Proposed Optimization**: .NET computes the 140MB in RAM, applies an F# reduction, and sends only the **16KB** 2D result to Python.
*   **Reduction Ratio**: ~9,000:1 improvement in data transfer volume.

---

## 3. .NET Implementation Plan (`moonlib`)

### 3.1 Dependencies
*   Add `FSharp.Compiler.Service` to `moonlib.csproj`.

### 3.2 Request Schema Changes
Update `LightmapArrayStreamRequest` in `LightmapArrayStreamingBridge.cs`:
```csharp
public sealed record LightmapArrayStreamRequest(
    // ... existing fields ...
    string? FSharpReductionSnippet = null, // The F# code string
    bool IsReducedOutput = false           // Hint to use 2D buffer registration
);
```

### 3.3 Dynamic Compiler Service
Create `moonlib.pipeline.streaming.ReductionCompiler`:
*   Uses `FSharpChecker` to compile the string into a `Func<float[], float>`.
*   The function signature expects `float[]` (the time series for one pixel) and returns a `float` (the reduced value).
*   Compilation happens **once** during `StartLightmapArrayStreaming`.

### 3.4 Buffer Registration & Loop Logic
*   **Registration**: If `IsReducedOutput` is true, `RegisterOutputBuffer` validates that `byteLength == 128 * 128`.
*   **The Loop**: Modify `WriteTileToRegisteredBuffer`:
    1.  Maintain a thread-local `float[] timeBuffer` of size `sunVectors.Count`.
    2.  For each pixel $(x, y)$:
        a. Compute `sunFraction` for all time steps.
        b. Store in `timeBuffer`.
        c. Call `compiledReductionFunc(timeBuffer)`.
        d. Write the single `byte` result to the 2D destination.

---

## 4. Python Implementation Plan (`backend`)

### 4.1 Worker Interop (`lightmap_streaming.py`)
*   Update `LightmapStreamRequestPy` to include `fsharp_reduction_snippet`.
*   Update `LightmapStreamingClient._build_dotnet_request` to map the new field.
*   Update `LightmapStreamRequestPy.tile_shape()`:
    *   If `fsharp_reduction_snippet` is present, it returns `(1, 128, 128)` (or effectively a 2D shape) to signal the smaller buffer allocation.

### 4.2 Notebook Helper (`notebook_helper.py`)
Update `run_lightmap_streaming_raster_job`:
*   Add an optional `fsharp_reduction: str` parameter.
*   **Logic Branching**:
    *   If `fsharp_reduction` is provided, skip the Python `tile_transform` step.
    *   The `stream_tiles` iterator will now yield 2D NumPy arrays instead of 3D.
    *   The `out_band.WriteArray` remains the same (writing a 2D tile).

---

## 5. Implementation Steps

| Step | Task | File(s) |
| :--- | :--- | :--- |
| 1 | Add F# Compiler Service Dependency | `moonlib.csproj` |
| 2 | Create `ReductionCompiler.cs` | `moonlib/pipeline/streaming/` |
| 3 | Update Request/Bridge for F# Snippets | `LightmapArrayStreamingBridge.cs` |
| 4 | Implement 2D Reduction Loop in C# | `LightmapArrayStreamingBridge.cs` |
| 5 | Update Python Request Models | `lightmap_streaming.py` |
| 6 | Add F# support to `run_lightmap_streaming_raster_job` | `notebook_helper.py` |

---

## 6. Example F# Snippets

### 6.1 Average Lighting (Mean)
```fsharp
"data |> Array.average"
```

### 6.2 Maximum Lighting (Boolean Mask)
```fsharp
"data |> Array.max"
```

### 6.3 Percent Lit (Threshold)
```fsharp
"let litCount = data |> Array.filter (fun x -> x > 0.5) |> Array.length
 (float litCount) / (float data.Length)"
```

### 6.4 Longest Continuous Period of Sun
```fsharp
"let folder (maxSeq, currentSeq) x =
    if x > 0.5 then (max maxSeq (currentSeq + 1), currentSeq + 1)
    else (maxSeq, 0)
 let (finalMax, _) = Array.fold folder (0, 0) data
 float finalMax"
```

### 6.5 Other scenarios

- Average sun fraction (over an interval of time sampled at a uniform time step)
- Cumulative time Earth is above the horizon (over an interval of time sampled at a uniform time step)
- Maximum contiguous time interval with Earth elevation_above_horizon >= D degrees (over an interval of time sampled at a uniform time step)
- Maximum contiguous time interval with sun fraction > N% (over an interval of time sampled at a uniform time step)
- Maximum contiguous time interval with sun fraction > N% and Earth elevation_above_horizon >= D degrees (over an interval of time sampled at a uniform time step)

## 7. Performance Targets
*   **Memory**: Reduce Python-side memory usage by **99%** for long simulations.
*   **Throughput**: Increase total area-per-second processing by **5-10x** due to reduced IPC and native execution.
*   **CPU**: Reduce Python CPU utilization to near-zero during the stream (leaving more for UI/API tasks).
