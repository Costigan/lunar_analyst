# Streaming Tiles from C# to Python NumPy Buffers

## Status
Proposed implementation plan for a future coding session.

## Goal
Enable high-throughput streaming of computed lightmap tile data from `moonlib` (C#) to Python using pythonnet, while keeping C# compute parallel and interop handoff safe and predictable.

Target use case:
1. Python starts analysis with setup parameters.
2. C# runs a pipeline over horizon tiles:
   1. Read one tile horizon file (`128 x 128 x 1440` horizons).
   2. Compute lighting over all requested timestamps for that tile.
   3. Produce `uint8` lighting samples (`0..255`) for the full tile time series.
3. Python receives results as NumPy arrays for downstream processing.

## Context and Constraints

### Existing architecture constraints
- `docs/PLAN.md` defines the worker process as the only CLR host.
- Worker may allocate NumPy arrays and pass them to C# for filling (`docs/PLAN.md`, section 2.2).
- Large outputs should not be sent as large REST payloads.

### Interop risk constraints
- `docs/PHASE4_5_HORIZON_SHARED_STORE_PLAN.md` explicitly prefers polling over Python callbacks into C# to avoid callback/GIL complexity.
- We should not call back into Python from C# worker threads.

### Current code context
- Existing general dataflow pipeline: `native/new_horizon/moonlib/pipeline/Pipeline.cs`.
- Lightmap pipeline today writes GeoTIFF outputs directly: `native/new_horizon/moonlib/pipeline/LightmapPipeline.cs`.
- Current bridge surface is minimal and synchronous for horizons: `native/new_horizon/moonlib/MoonlibBridge.cs`.

## Decision Summary
Use a **Python-owned ring buffer** with **polling**.

- Python preallocates a fixed number of NumPy arrays.
- Python registers each buffer pointer with C#.
- C# fills only registered buffers and enqueues metadata for completed tiles.
- Python polls completed tile metadata, reads array data directly, then releases the buffer back to C#.
- No C# callbacks into Python.

## Data Model and Contracts

### Fixed tile geometry
- `tile_width = 128`
- `tile_height = 128`
- `dtype = uint8`

### Time axis policy
No time-chunking. Each output tile includes the full requested time series.

- `N = count(start_time..stop_time, time_step_hours)`
- buffer shape: `(N, 128, 128)` (canonical)
- `bytes_per_tile = N * 128 * 128`

Tile metadata includes:
- `job_id`
- `tile_id` (monotonic)
- `buffer_id`
- `patch_row`
- `patch_col`
- `time_count` (`N`)
- `width`
- `height`
- `status` (`ready`, `error`, `terminal`)
- `message` (optional)

## C# Bridge Surface (Proposed)
Add a streaming bridge class in `moonlib` (name tentative: `LightmapStreamingBridge`).

### Methods
1. `StartLightmapStreaming(LightmapStreamRequest request) -> string jobId`
2. `RegisterOutputBuffer(string jobId, int bufferId, long ptr, int byteLength) -> bool`
3. `TryGetNextTile(string jobId, int timeoutMs) -> TileEnvelope?`
4. `ReleaseBuffer(string jobId, int bufferId) -> bool`
5. `GetJobStatus(string jobId) -> LightmapStreamStatus`
6. `CancelJob(string jobId) -> bool`
7. `DisposeJob(string jobId) -> bool`

### DTOs
- `LightmapStreamRequest`: paths, time range, step size, observer elevation, optional parallelism knobs.
- `TileEnvelope`: metadata only (no embedded payload).
- `LightmapStreamStatus`: state, progress, counts, warnings/errors.

### Method signatures (draft)
```csharp
namespace moonlib.pipeline.streaming;

public sealed class LightmapStreamingBridge
{
    public string StartLightmapStreaming(LightmapStreamRequest request);

    // Register a Python-owned NumPy buffer pointer for direct write.
    public bool RegisterOutputBuffer(string jobId, int bufferId, long ptr, int byteLength);

    // Poll next completed tile metadata. Null when timeout elapses with no tile.
    public TileEnvelope? TryGetNextTile(string jobId, int timeoutMs);

    // Return a consumed buffer to the producer-side free pool.
    public bool ReleaseBuffer(string jobId, int bufferId);

    public LightmapStreamStatus GetJobStatus(string jobId);
    public bool CancelJob(string jobId);
    public bool DisposeJob(string jobId);
}

public sealed record LightmapStreamRequest(
    string ScenarioRootDir,
    string DemPath,
    IReadOnlyList<string> SurroundingDemPaths,
    string HorizonDir,
    DateTime StartUtc,
    DateTime StopUtc,
    double TimeStepHours,
    float ObserverElevationMeters,
    int PatchWidth = 128,
    int PatchHeight = 128,
    int MaxReadParallelism = 4,
    int MaxComputeParallelism = 24,
    int ReadyQueueCapacity = 64
);

public enum StreamTileState
{
    Ready,
    Error,
    Terminal
}

public sealed record TileEnvelope(
    string JobId,
    long TileId,
    int BufferId,
    int PatchRow,
    int PatchCol,
    int TimeCount,
    int Width,
    int Height,
    StreamTileState State,
    string? Message = null
);

public enum StreamJobState
{
    Queued,
    Running,
    Cancelling,
    Cancelled,
    Completed,
    Failed
}

public sealed record LightmapStreamStatus(
    string JobId,
    StreamJobState State,
    double Progress01,
    long TilesProduced,
    long TilesConsumed,
    int ReadyQueueDepth,
    int FreeBufferCount,
    string? Message = null
);
```

### Python adapter signatures (draft)
```python
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
import numpy as np

@dataclass(frozen=True)
class LightmapStreamRequestPy:
    scenario_root_dir: Path
    dem_path: Path
    surrounding_dem_paths: list[Path]
    horizon_dir: Path
    start_utc: str
    stop_utc: str
    time_step_hours: float
    observer_elevation_meters: float
    patch_width: int = 128
    patch_height: int = 128

@dataclass(frozen=True)
class StreamTileMetaPy:
    job_id: str
    tile_id: int
    buffer_id: int
    patch_row: int
    patch_col: int
    time_count: int
    width: int
    height: int
    state: str
    message: str | None

class LightmapStreamingClient:
    def start(self, request: LightmapStreamRequestPy) -> str: ...
    def register_buffer(self, job_id: str, buffer_id: int, arr: np.ndarray) -> bool: ...
    def poll_next_tile(self, job_id: str, timeout_ms: int) -> StreamTileMetaPy | None: ...
    def release_buffer(self, job_id: str, buffer_id: int) -> bool: ...
    def get_status(self, job_id: str): ...
    def cancel(self, job_id: str) -> bool: ...
    def dispose(self, job_id: str) -> bool: ...

def stream_tiles(client: LightmapStreamingClient, request: LightmapStreamRequestPy) -> Iterator[tuple[StreamTileMetaPy, np.ndarray]]:
    ...
```

## Memory Ownership and Lifetime

### Ownership rules
- Python owns all output array memory.
- C# only writes into pointers registered by Python.
- A buffer is writable by C# only while it is marked `in_use`.
- Python reads a buffer only after `TryGetNextTile` reports it `ready`.
- Python must call `ReleaseBuffer` when done.

### Python-side invariants
- Arrays must be `np.uint8`, C-contiguous, and fixed-size.
- Keep strong Python references for full job lifetime.
- Do not resize/reallocate registered arrays.

### C# safety checks
- Validate `byteLength == expected_tile_bytes`.
- Never write beyond registered length.
- Reject unknown/duplicate `buffer_id` registrations.
- On cancel/failure, return all `in_use` buffers to a terminal-safe state.

## Threading and Pipeline Model

### High-level flow
1. Producer enumerates horizon tile files.
2. Compute workers run full-time-series lighting compute per tile in parallel.
3. Before writeout, worker acquires one free registered buffer.
4. Worker writes bytes directly into that buffer (`unsafe` pointer write).
5. Worker enqueues `TileEnvelope` into `ready_queue`.
6. Python poll loop drains `ready_queue`, consumes data, releases buffer.

### Concurrency primitives (C#)
- `ConcurrentDictionary<int, RegisteredBuffer>` for registered buffers.
- `ConcurrentQueue<int>` or `Channel<int>` for free buffer IDs.
- `Channel<TileEnvelope>` for ready tiles (bounded).
- `CancellationTokenSource` per job.
- Optional `SemaphoreSlim` for limits.

### Backpressure
- Bounded ready queue and finite free-buffer pool naturally throttle producers.
- If Python stops polling, C# compute blocks on buffer availability rather than unbounded memory growth.

## Interop Implementation Notes

### Pointer handling
- Python passes pointer as `int64` (`arr.ctypes.data`).
- C# stores as `IntPtr`.
- Write with `unsafe` + span/pointer arithmetic.

### Ordering
- Default: completion order (fastest first).
- Optional: deterministic order by `tile_id` if requested; this costs throughput.

### Error propagation
- Per-tile recoverable errors can emit `TileEnvelope(status=error, message=...)`.
- Fatal errors set terminal job state and stop production.

## Python Worker Integration Plan

### New Python adapter (backend worker side)
Add a Python-side manager (tentative file: `backend/worker/lightmap_streaming.py`) that:
1. Builds `LightmapStreamRequest`.
2. Allocates ring buffers (`K` arrays).
3. Registers buffers with C#.
4. Polls `TryGetNextTile`.
5. Converts buffer view to expected tensor orientation if needed.
6. Performs downstream steps (aggregation, write-to-disk, product registration).
7. Releases each buffer.
8. Finalizes via `GetJobStatus`/`DisposeJob`.

### Suggested initial defaults
- `K = 8` buffers
- Poll timeout: `100-250 ms`

## Phased Implementation Tasks

### Phase 1: Contract and scaffolding
- Add C# DTOs and bridge surface without full compute.
- Add Python wrapper with mocked polling loop.
- Add contract tests for method behavior and state transitions.

### Phase 2: Core compute wiring
- Wire existing lightmap patch compute into streaming job executor.
- Implement direct writes into registered buffers.
- Implement cancel/status/progress.

### Phase 3: Worker integration
- Integrate with backend job handler(s).
- Ensure progress and cancellation events map to existing job event schema.
- Persist outputs/artifacts expected by scenario workflows.

### Phase 4: Hardening
- Tune buffer count and parallelism.
- Add stress tests and long-run leak checks.
- Document operational defaults.

## Test Plan

### C# unit/integration tests
- Buffer registration validation.
- Free/in-use/ready state transitions.
- Bounds safety (reject wrong lengths).
- Cancel while workers are active.
- Dispose semantics.

### Python integration tests
- End-to-end stream with small synthetic horizon set.
- Verify no callback path is required.
- Verify cancellation responsiveness.
- Verify no unbounded memory growth under slow consumer.

### Cross-boundary performance checks
- Throughput (MB/s and tiles/s).
- CPU utilization and scaling with cores.
- Memory profile over long runs (no leak/regression).

## Risks and Mitigations
- Pointer misuse can corrupt memory.
  - Mitigation: strict validation, fixed contracts, exhaustive tests, guarded unsafe code.
- Python consumer stalls can block compute.
  - Mitigation: bounded queues and explicit backpressure metrics.
- Cancellation races can leave buffers stranded.
  - Mitigation: terminal cleanup pass and idempotent `ReleaseBuffer`/`DisposeJob`.
- Contract drift between Python and C#.
  - Mitigation: shared DTO tests and adapter-level schema assertions.

## Rollback Strategy
- Keep existing non-streaming lightmap path intact during rollout.
- Feature-flag streaming path in worker job handler.
- If instability appears, switch flag off and continue with file-based output flow.

## Open Questions for Implementation Session
1. Should tile tensor layout be `(N, H, W)` or `(H, W, N)` for downstream consumers?
2. Do we need strict in-order tile delivery, or is completion order acceptable?
3. Should C# compute directly into registered buffers or via intermediate pooled buffers for better scheduling?
4. Which job handler should first consume this path (new handler vs extension of existing lightmap flow)?
5. What minimum telemetry fields are required for production observability?

## Definition of Done for This Feature
- Streaming job runs end-to-end with Python-owned NumPy buffers and no callbacks.
- C# compute remains parallel across tiles.
- Data handoff is polling-based and bounded-memory.
- Cancellation and progress work under load.
- Integration tests cover success, failure, and cancellation paths.
