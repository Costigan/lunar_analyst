# ADR.0059: Multi-GPU Patch-Distributed Horizon Generation

- Status: Proposed
- Date: 2026-05-28
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0041.parallel_popos_and_ubuntu_container_development.md`, `docs/ADR.0058.partitioned_horizon_tile_store.md`, `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs`, `native/new_horizon/horizon/Program.cs`, `native/new_horizon/moonlib/MoonlibBridge.cs`

## Context

`QuadTreeHorizonGenerator` generates horizon products by processing independent 128x128 DEM patches. The current pipeline already separates CPU segment generation from GPU ray processing:

1. The CPU producer computes `PatchWorkItem` instances containing ray segment data for each patch.
2. A bounded channel applies backpressure between segment generation and GPU processing.
3. A pool of GPU workers consumes patch work items.
4. Each worker launches the existing ILGPU subpatch ray-casting path and writes one horizon file per completed patch.

The current implementation uses one selected ILGPU `Accelerator`. It may run multiple streams and multiple worker tasks against that single accelerator, but all GPU-owned state belongs to that device:

- the selected `Accelerator`,
- compiled kernel delegates,
- stream pool,
- `BufferPool`,
- uploaded DEM pyramid buffers,
- per-patch GPU buffers.

Production-scale horizon generation on the National Research Platform (NRP) Kubernetes cluster is constrained by both runtime and schedulability. Single pods requesting large memory and CPU allocations are harder to schedule, and single-GPU jobs can take many hours for large scenarios. Recent tuning work added controls for single-GPU stream count and bounded patch queue depth, but it does not let one job use multiple visible GPUs.

## Problem

Large NRP horizon generation jobs need a way to improve throughput without requiring one pod to request excessive CPU and memory.

The specific problems this ADR addresses are:

1. **Performance:** A single GPU can leave total runtime high for large patch sets.
2. **Schedulability:** Increasing single-pod CPU and memory requests to compensate makes pods harder to place on NRP nodes.
3. **Resource matching:** Logs show CPU segment generation and GPU ray processing have different costs; the desired deployment shape is to tune CPU cores, patch queue depth, and GPU count independently.
4. **Code structure:** The current generator has single-accelerator state embedded directly in `QuadTreeHorizonGenerator`, making multi-GPU use unsafe unless accelerator-owned resources are explicitly separated.

The workload is well suited to patch-level distribution because horizon patches are independent output units. A multi-GPU refactor should therefore distribute complete patches across GPUs and keep the processing of an individual patch very close to the current implementation.

## Decision

Refactor `QuadTreeHorizonGenerator` to support multiple ILGPU accelerators by introducing per-GPU execution lanes and distributing complete patch work items across those lanes.

The generator must not split an individual patch across GPUs. Each patch is assigned to exactly one GPU lane, processed with the same ray-casting and horizon merge logic used today, and written as the same horizon tile output.

Each GPU lane will own all accelerator-specific resources:

- `Accelerator`
- compiled subpatch kernel delegate
- stream pool
- `BufferPool`
- device copies of DEM pyramid buffers
- per-patch temporary GPU buffers

Static or CPU-side patch metadata may be shared. GPU buffers and ILGPU objects must not be shared across accelerators.

The first implementation should keep the current producer-consumer architecture:

```text
CPU segment producer(s)
        |
        v
bounded PatchWorkItem channel
        |
        v
GPU lane 0 worker(s) -> GPU 0 -> horizon files
GPU lane 1 worker(s) -> GPU 1 -> horizon files
GPU lane N worker(s) -> GPU N -> horizon files
```

The initial scheduling policy should be simple dynamic work sharing: all GPU lane workers consume from the same bounded patch channel. This naturally load-balances uneven patches without requiring a static partitioning plan.

## Scope

In scope:

- Refactor `QuadTreeHorizonGenerator` so accelerator-owned state is isolated per GPU.
- Add GPU device selection and multi-GPU configuration to the native generator API.
- Add CLI parameters in `native/new_horizon/horizon/Program.cs` for selecting GPU devices and per-device stream count.
- Replicate DEM pyramid buffers on each selected GPU.
- Preserve patch-level output semantics and existing horizon file naming/layout behavior.
- Preserve current single-GPU behavior as the default.
- Add logging and profiling that identifies the GPU lane/device used for each processed patch.
- Add tests for device selection, single-GPU compatibility, scheduling behavior, and fallback behavior.

Out of scope:

- Splitting one patch across multiple GPUs.
- Peer-to-peer GPU memory sharing.
- Unified memory or cross-device buffer sharing.
- Changing horizon numeric algorithms, patch size, azimuth count, horizon file format, or output CRS.
- Requiring multiple GPUs for normal local development.
- Replacing ILGPU.
- Kubernetes manifest changes beyond documenting the resource implications.

## Normative Design

### 1. Preserve Patch-Level Processing

The current per-patch execution path should remain the unit of correctness. A patch assigned to GPU 0 and the same patch assigned to GPU 1 must produce equivalent output within the existing numeric tolerance.

The refactor should move current single-GPU operations into per-lane methods rather than rewrite the ray-casting algorithm.

The following behavior must remain unchanged:

- patch list generation,
- completed-patch skipping,
- segment generation math,
- subpatch ray segment layout,
- kernel computation for one patch,
- horizon compression and write semantics,
- progress and cancellation semantics.

### 2. Per-GPU Execution Lane

Introduce an internal type similar to:

```csharp
private sealed class GpuExecutionLane : IDisposable
{
    public int LaneIndex { get; }
    public Device Device { get; }
    public Accelerator Accelerator { get; }
    public BufferPool BufferPool { get; }
    public ConcurrentStack<AcceleratorStream> StreamPool { get; }
    public int StreamsPerDevice { get; }

    // Compiled for this lane's Accelerator.
    public SubpatchKernelDelegate SubpatchKernel { get; }

    // Device-local pyramid copies for this lane.
    public IReadOnlyList<Pyramid> Pyramids { get; }
}
```

The exact delegate type may differ because the current code has compile-time variants for traversal profiling. The important rule is that the compiled kernel delegate is owned by the accelerator that will launch it.

All lane-owned resources must be disposed by the lane. `QuadTreeHorizonGenerator.Dispose()` should dispose all lanes, then the shared ILGPU `Context`.

### 3. Device Discovery and Selection

The generator constructor should accept explicit multi-GPU options while preserving existing defaults. Suggested API:

```csharp
public sealed record GpuSchedulingOptions(
    GpuSelectionMode SelectionMode = GpuSelectionMode.LegacyPreferredSingleDevice,
    GpuAcceleratorKind GpuKind = GpuAcceleratorKind.Cuda,
    string? NameContains = null,
    int? MinGpuCount = null,
    int? MaxGpuCount = null,
    IReadOnlyList<int>? ExplicitDeviceOrdinals = null,
    int GpuStreamsPerDevice = QuadTreeHorizonGenerator.DefaultMaxConcurrentGpuOps,
    int MaxSegmentQueueSize = QuadTreeHorizonGenerator.DefaultSegmentQueueSize);
```

Alternatively, the existing constructor may add optional parameters directly, but configuration should stay explicit and testable.

Device selection rules:

1. If no multi-GPU selection flags are provided, preserve current behavior and select one preferred device.
2. In automatic multi-GPU mode, select from accelerators visible inside the process after container runtime filtering such as `CUDA_VISIBLE_DEVICES`.
3. Kubernetes device assignment is authoritative. The application does not discover or select GPUs hidden by the Kubernetes/NVIDIA device plugin.
4. Automatic multi-GPU mode defaults to visible CUDA accelerators. If no acceptable CUDA accelerator is visible, fail fast unless the user explicitly requests another supported kind.
5. Explicit device ordinals remain available only as an expert/debug override.
6. Explicit ordinals are interpreted within the filtered ILGPU discovery list visible to the process, not as physical node PCI IDs or Kubernetes resource names.
7. Invalid or duplicate ordinals should fail fast with actionable errors.
8. OpenCL and CPU accelerators may remain supported for single-device fallback but should not be advertised as the target multi-GPU path until tested.

The CLI should expose this without requiring code changes:

```bash
horizon make <horizons_directory> <offset> <stride> \
  [--segment-queue <size>] \
  [--gpu-selection auto|explicit] \
  [--gpu-kind cuda] \
  [--gpu-name-contains <pattern[,pattern...]>] \
  [--gpu-min-count <count>] \
  [--gpu-max-count <count>] \
  [--gpu-devices <ordinals>] \
  [--gpu-streams-per-device <count>] \
  <dem_filenames ...>
```

Examples:

```bash
horizon make /workspace/scenario/horizons 0 1 \
  --gpu-selection auto \
  --gpu-min-count 2 \
  --gpu-max-count 2 \
  --gpu-streams-per-device 1 \
  --segment-queue 4 \
  /workspace/scenario/dems/primary.tif
```

For heterogeneous but explicitly acceptable NRP nodes:

```bash
horizon make /workspace/scenario/horizons 0 1 \
  --gpu-selection auto \
  --gpu-name-contains A100,H100 \
  --gpu-min-count 1 \
  --gpu-max-count 2 \
  --gpu-streams-per-device 1 \
  /workspace/scenario/dems/primary.tif
```

#### Selection Modes

`--gpu-selection auto` selects acceptable devices from visible ILGPU accelerators. With no min/max count, it selects all visible acceptable CUDA devices. If a maximum count is provided and more devices match, devices are selected in deterministic ILGPU discovery order after filtering.

`--gpu-selection explicit` selects only the ordinals supplied by `--gpu-devices`. This mode is intended for debugging and controlled hardware validation, not as the normal Kubernetes user interface.

No `--gpu-selection` flag preserves the existing single-preferred-device behavior for backward compatibility.

#### GPU Count Bounds

`--gpu-min-count` and `--gpu-max-count` define an acceptable selected-device range:

- `--gpu-min-count` is the minimum acceptable number of matching GPUs. If fewer are visible, fail fast.
- `--gpu-max-count` is the maximum number of matching GPUs to use. If more are visible, select only this many.
- If both are set to the same value, selection is exact-count.
- If only `--gpu-max-count` is set, use up to that many matching GPUs.
- If only `--gpu-min-count` is set, use all matching visible GPUs but require at least that many.
- `--gpu-min-count > --gpu-max-count` is an argument validation error.

For NRP jobs that request two GPUs from Kubernetes, the recommended exact validation is:

```bash
--gpu-selection auto --gpu-min-count 2 --gpu-max-count 2
```

#### GPU Name Filtering

`--gpu-name-contains` is a single optional argument. If present, it contains one or more comma-separated substring patterns. The patterns form a disjunction:

```bash
--gpu-name-contains A100,H100
```

means:

```text
ILGPU device name contains "A100" OR ILGPU device name contains "H100"
```

Rules:

- Matching is case-insensitive.
- The matched value is ILGPU `Device.Name`.
- Commas split alternatives.
- Whitespace around each alternative is trimmed.
- Empty alternatives are rejected.
- Duplicate alternatives may be normalized away for diagnostics.
- No regex or glob syntax is supported in the first implementation.
- If omitted, no name filtering is applied.

If heterogeneous devices match the accepted name filter, the first implementation may use them together. Dynamic patch scheduling should naturally balance work, and per-lane profiling must report throughput by device so operators can evaluate mixed hardware runs.

#### Stream Count

`--gpu-streams-per-device` controls GPU worker streams per selected GPU:

```text
total_gpu_workers = selected_gpu_count * gpu_streams_per_device
```

The recommended initial NRP value is `1` per selected GPU. Higher values should be treated as a tuning parameter and justified by profiling.

The proposed multi-GPU CLI should not include `--gpu-concurrency`. That name is a legacy single-device concept and is ambiguous once device count is independently configurable.

#### Diagnostics

Startup logs must include visible accelerators, selection filters, selected accelerators, and rejected accelerators with reasons.

Example:

```text
Visible accelerators:
  id=0 type=Cuda name="NVIDIA A100-SXM4-40GB"
  id=1 type=Cuda name="NVIDIA A100-SXM4-40GB"
  id=2 type=CPU  name="CPU Accelerator"

GPU selection:
  mode=auto kind=cuda name_contains=A100,H100 min=2 max=2
  selected id=0 type=Cuda name="NVIDIA A100-SXM4-40GB"
  selected id=1 type=Cuda name="NVIDIA A100-SXM4-40GB"
  rejected id=2 reason="type CPU does not match cuda"
```

If lane initialization fails because a selected GPU lacks sufficient memory or cannot create required buffers, the job must fail before patch processing starts whenever possible.

### 4. Pyramid Replication

Each selected GPU must receive its own uploaded copy of the DEM pyramid data and metadata.

The CPU-side pyramid preparation may be shared where safe, but ILGPU memory buffers must be device-local. A pyramid object must never contain buffers from one accelerator and then be passed to a kernel launched on another accelerator.

Recommended shape:

1. Load DEMs once on CPU.
2. Build or collect CPU pyramid source data once where practical.
3. For each `GpuExecutionLane`, upload device-local pyramid buffers.
4. Store those lane-local `Pyramid` instances on the lane.

Memory consequence:

```text
total_gpu_memory_for_pyramids ~= per_gpu_pyramid_memory * selected_gpu_count
```

This is intentional. Replication avoids cross-device access complexity and keeps patch processing close to the current implementation.

### 5. Patch Scheduling

Use dynamic patch scheduling through a shared bounded channel:

```csharp
var patchWorkChannel = Channel.CreateBounded<PatchWorkItem>(...);
```

All lane workers read from the same channel. Each lane starts `GpuStreamsPerDevice` worker tasks. A worker uses only its lane's buffers, streams, kernel delegate, and pyramid views.

Dynamic scheduling is preferred over static partitioning because:

- patch processing time can vary,
- GPU devices may not be identical,
- file write/compression time can vary,
- dynamic consumption avoids stranding work on a slow lane.

Progress counting must remain global and thread-safe.

### 6. Queue Depth and CPU Producer Behavior

The bounded queue capacity remains a global memory control. In multi-GPU mode, the default queue size should be interpreted as total queued patch work items, not per-GPU queued items.

Operators should tune:

- selected GPU count,
- `GpuStreamsPerDevice`,
- global patch queue capacity,
- Kubernetes CPU request,
- Kubernetes memory request.

If multi-GPU throughput causes GPUs to wait for segment generation, a later phase may parallelize CPU segment generation. That should be implemented only after profiling shows the single producer is the bottleneck.

If added, CPU segment generation parallelism must have its own bounded output channel and memory controls. It must not precompute an unbounded patch list of segment arrays.

For utilization, the queue size should usually be at least the selected GPU count, but this is a tuning recommendation rather than a hard validation rule.

### 7. Cancellation and Failure Semantics

Cancellation must stop:

- CPU segment generation,
- queued patch processing,
- all lane workers,
- in-flight file writes after their current safe checkpoint.

If one GPU lane fails, the initial implementation should fail the whole generation job rather than continue with fewer GPUs. Continuing after partial device failure is operationally attractive but introduces complex retry semantics and should be a later decision.

When the job fails, already completed horizon files remain valid resumable outputs under the existing completed-patch skipping policy.

### 8. Observability

Logs should include lane and device identity for GPU work:

```text
GPU lane=1 device="NVIDIA A100..." patch=12357 processed rays sec=7.446
```

Progress logs should continue to report global completion. Profiling output should include:

- selected device count,
- device names and accelerator types,
- per-lane completed patch count,
- per-lane average GPU wall time,
- per-lane stream wait time,
- buffer acquire time,
- queue wait time,
- global throughput patches/sec.

This is required for NRP tuning because schedulability depends on real ratios among CPU work, GPU work, memory pressure, and file I/O.

## Consequences

Positive consequences:

- Large horizon generation jobs can use multiple GPUs in one pod when NRP can schedule such nodes.
- Patch-level distribution keeps numerical behavior close to current implementation.
- Per-device state isolation reduces the risk of invalid ILGPU cross-device buffer use.
- Dynamic scheduling supports uneven patch cost and heterogeneous GPUs.
- Operators can tune GPU count, per-GPU stream count, and queue depth independently.

Negative consequences:

- DEM pyramid GPU memory is replicated once per selected GPU.
- The generator becomes more complex because accelerator-owned state moves into lanes.
- Single-pod multi-GPU requests may still be hard to schedule on NRP if many users compete for multi-GPU nodes.
- CPU segment generation may become the bottleneck after enough GPUs are added.
- More detailed tests and hardware validation are required because CI may not have multiple GPUs.

## Implementation Plan

### Phase A: Extract Single-GPU Lane Without Behavior Change

- [ ] Introduce `GpuExecutionLane` as an internal implementation detail.
- [ ] Move the current `_accelerator`, `_bufferPool`, `_streamPool`, and compiled kernel delegate into the lane.
- [ ] Keep constructing exactly one lane by default.
- [ ] Keep the existing public constructor behavior unchanged.
- [ ] Update `LaunchPatchAsync` and related helpers so they accept a lane or lane-owned resources explicitly.
- [ ] Ensure all ILGPU memory buffers used by a patch come from the same lane.
- [ ] Preserve existing profiling and progress logs.
- [ ] Run the existing horizon test suite and a representative single-GPU generation smoke test.

Acceptance for Phase A:

- No intentional behavior change.
- Single-GPU output matches the pre-refactor baseline.
- No increase in warnings.
- Disposal releases all streams, buffers, accelerators, and context cleanly.

### Phase B: Add Device Selection

- [ ] Add internal device discovery that can enumerate eligible ILGPU devices.
- [ ] Add constructor options for automatic selection mode, accelerator kind, name filter, min/max GPU counts, and explicit debug ordinals.
- [ ] Preserve current preferred-device selection when no multi-GPU selection flags are supplied.
- [ ] Validate duplicate, invalid, and unsupported explicit ordinals.
- [ ] Validate name-filter parsing and GPU count bounds.
- [ ] Log visible, selected, and rejected devices at startup.
- [ ] Add tests for parsing and validation using injectable/device-discovery seams where physical GPUs are not available.

Acceptance for Phase B:

- Default single-device behavior is unchanged.
- Invalid device selections, empty matches, and insufficient selected GPU counts fail before any generation work starts.
- Logs clearly identify visible accelerators, selection filters, selected accelerators, and rejection reasons.

### Phase C: Replicate Pyramid Upload Per Lane

- [ ] Split CPU DEM/pyramid preparation from GPU buffer upload if needed.
- [ ] Upload lane-local pyramid buffers for each selected GPU.
- [ ] Store lane-local `PyramidView` instances or equivalent views.
- [ ] Ensure no `MemoryBuffer` from one lane can be passed to another lane.
- [ ] Add debug assertions or helper types that make cross-lane misuse difficult.

Acceptance for Phase C:

- Single-lane output remains unchanged.
- Two-lane runs duplicate GPU pyramid buffers and process patches without cross-device buffer errors.
- Disposal works after successful runs, cancellation, and exceptions.

### Phase D: Multi-GPU Worker Scheduling

- [ ] Start `GpuStreamsPerDevice` worker tasks per lane.
- [ ] Have all lane workers consume from the shared bounded `PatchWorkItem` channel.
- [ ] Keep progress counting global and protected by existing synchronization or a clearer lock.
- [ ] Add per-lane counters and profiling aggregates.
- [ ] Include lane/device identity in GPU processing logs.
- [ ] Fail the whole job if any lane worker throws.

Acceptance for Phase D:

- Patch outputs are complete and non-duplicated.
- Dynamic scheduling uses all selected lanes when work is available.
- Cancellation stops all lane workers.
- A thrown exception from one lane fails the job and leaves completed tiles resumable.

### Phase E: CLI and Bridge Wiring

- [ ] Add CLI parsing in `native/new_horizon/horizon/Program.cs` for `--gpu-selection`, `--gpu-kind`, `--gpu-name-contains`, `--gpu-min-count`, `--gpu-max-count`, `--gpu-devices`, and `--gpu-streams-per-device`.
- [ ] Remove `--gpu-concurrency` from the proposed multi-GPU command surface.
- [ ] Update usage text and examples.
- [ ] Decide whether `MoonlibBridge` needs corresponding parameters for worker-driven production runs.
- [ ] If worker-driven NRP runs use `MoonlibBridge`, thread these options through the Python/.NET bridge and job request/config path in a separate, explicit change.

Acceptance for Phase E:

- Existing CLI invocations still work.
- Multi-GPU CLI invocations select the requested devices.
- The active NRP execution path can actually supply the new options, either through CLI flags or bridge/job configuration.

### Phase F: Optional CPU Segment Generation Parallelism

This phase should be deferred until profiling shows GPUs waiting on segment generation.

- [ ] Add a configurable CPU segment producer count.
- [ ] Keep the output channel bounded.
- [ ] Preserve deterministic patch identity and output filenames.
- [ ] Ensure memory growth remains bounded by queue capacity and active producer count.
- [ ] Measure whether additional CPU producers improve throughput on representative NRP nodes.

Acceptance for Phase F:

- Parallel CPU segment generation improves measured throughput for multi-GPU runs.
- Memory remains within configured pod limits.
- Cancellation remains responsive.

## Testing Plan

### Unit Tests

- Device ordinal parser:
  - empty device list,
  - `0`,
  - `0,1`,
  - whitespace,
  - duplicates,
  - invalid integers,
  - negative ordinals.
- GPU name pattern parser:
  - single pattern,
  - comma-separated disjunction,
  - whitespace trimming,
  - case-insensitive matching,
  - empty alternative rejection,
  - duplicate normalization.
- GPU count bounds:
  - min only,
  - max only,
  - exact min/max,
  - min greater than max rejected,
  - selected count below min rejected.
- Constructor validation:
  - no device list preserves default single-device behavior,
  - invalid device ordinals fail fast,
  - `GpuStreamsPerDevice <= 0` fails,
  - `MaxSegmentQueueSize <= 0` fails.
- Scheduling helpers:
  - patch work item consumed once,
  - per-lane counters update correctly,
  - global progress reaches total exactly once.

### Single-GPU Regression Tests

- Existing `HorizonGen.Tests` must pass.
- Add or preserve a small deterministic DEM test comparing horizon output before and after Phase A.
- Verify compressed and uncompressed write paths still produce valid files.
- Verify cancellation behavior remains responsive.

### Multi-GPU Integration Tests

These tests require hardware or a dedicated NRP validation job and may be excluded from normal CI unless a multi-GPU runner is available.

- Run a small DEM with two selected GPUs and enough patches to exercise both lanes.
- Assert every expected patch output exists exactly once.
- Compare outputs from:
  - single GPU device 0,
  - single GPU device 1,
  - two GPUs together.
- Validate per-lane logs show work on both devices.
- Validate cancellation during an active two-GPU run exits cleanly.

### Performance Tests

Benchmark representative scenarios on NRP:

- one GPU, one stream per device,
- one GPU, two streams per device,
- two GPUs, one stream per device,
- two GPUs, two streams per device,
- queue sizes `1`, `2`, `4`, `6`, and current default.

Collect:

- patches/sec,
- per-patch GPU wall time,
- CPU segment generation time,
- queue wait time,
- stream wait time,
- file write/compression time,
- peak process memory,
- GPU memory per device,
- CPU utilization,
- pod scheduling time and failure rate for requested resources.

The tuning goal is not only fastest runtime. The recommended NRP profile should balance throughput against schedulability and memory pressure.

### Manual NRP Validation

- Submit a single-GPU job using the current production profile and record baseline runtime/resource behavior.
- Submit a two-GPU job with reduced CPU request and bounded queue size.
- Confirm the pod schedules reliably in the target namespace.
- Confirm both GPUs are visible to ILGPU and selected by automatic policy without requiring physical device ordinals.
- Confirm outputs are resumable after cancellation or preemption.
- Confirm no unexpected memory growth occurs as patch count increases.

## Rollback Plan

The refactor must preserve the single-GPU default path. If multi-GPU behavior is unstable:

1. Deploy with no multi-GPU selection flags and use the single-device default.
2. Set per-device stream count and queue size to the known-good single-GPU values.
3. Disable any optional CPU segment producer parallelism.
4. Resume generation from already completed horizon files using existing skip logic.

No horizon file format migration is part of this ADR, so rollback does not require data conversion.

## Open Questions

1. Should `MoonlibBridge` expose multi-GPU options immediately, or only after the standalone CLI path is validated on NRP?
2. Should CPU segment generation parallelism be included in the first multi-GPU implementation, or remain strictly phase-gated by profiling?
3. What NRP node shapes are common enough to target: one GPU per pod, two GPUs per pod, or larger multi-GPU pods?
4. Is ILGPU-exposed GPU memory information reliable enough to add a future `--gpu-min-memory-gb` filter?
