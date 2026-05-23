# Meter Horizon Pipeline

## Goal

Establish real, per-stage metrics for the case 5 horizon-generation pipeline in `native/new_horizon/horizon_runner/Program.cs` and `native/new_horizon/moonlib/horizon/QuadTreeHorizonGenerator.cs` before changing concurrency, stream count, or kernel structure again.

The immediate problem is:

- CPU utilization is only moderate, around 45%.
- GPU utilization is poor and not sustained.
- Changing `MAX_CONCURRENT_GPU_OPS` alone is not producing the desired effect.
- Current timing signals are too coarse to explain whether the GPU is starved by CPU preparation, host-device transfers, stream retention, or file output.

This plan is intentionally limited to instrumentation and profiling guidance first.

Out of scope for the first pass:

- redesigning the quadtree kernel
- changing horizon file format
- changing `.NET` / ILGPU runtime architecture
- changing scientific behavior or horizon outputs

## Current Pipeline Summary

Case 5 uses `GenerateHorizonsForPatches(...)`, which currently looks like this:

1. Build or load DEM pyramids once.
2. Single producer computes subpatch ray segments patch-by-patch on CPU.
3. Patch work items are queued into a bounded channel.
4. GPU workers acquire reusable buffers and a stream.
5. For each patch:
   - initialize `HorizonsAccum`
   - upload segments
   - launch one kernel per DEM pass on the selected stream
   - synchronize the stream
   - copy the full horizon buffer back to the host
   - convert radians to degrees
   - compress and write the output file
6. The stream and buffers are returned only after the patch result is fully materialized on the CPU side.

This means the GPU work is interleaved with significant host work and large host-device transfers.

## Working Hypotheses

The likely reasons GPU utilization is low are:

1. The single producer cannot prepare patch work fast enough.
2. Large per-patch host-to-device and device-to-host copies dominate the wall time.
3. Streams are held too long because the stream is not returned until after copy-back and CPU-side result construction.
4. File compression and file writes are on the GPU-worker critical path.
5. More streams increase queue depth but do not fix the actual bottleneck.

These hypotheses need to be tested with metrics before changing behavior.

## First Instrumentation Pass

Add structured timing for the following stages, per patch and as aggregated totals:

1. `segment_generation`
2. `buffer_reset_horizons_accum`
3. `segment_upload`
4. `kernel_launch_total`
5. `stream_sync`
6. `output_copy_to_host`
7. `radians_to_degrees`
8. `compress_and_write`
9. `total_gpu_worker_patch_wall`

Also add queue and concurrency metrics:

1. patch enqueue wait time for producer
2. time waiting for a free stream
3. time waiting for reusable buffers
4. current patch queue depth when enqueuing and dequeuing
5. number of active GPU workers
6. number of active streams in use

Metrics should be emitted in two forms:

1. Per-patch structured log lines.
2. End-of-run aggregated totals and averages.

## Recommended Logging Shape

Use structured log fields so runs can be compared mechanically. A suggested format:

- `patch_index`
- `tile_x`
- `tile_y`
- `segment_generation_sec`
- `buffer_reset_sec`
- `segment_upload_sec`
- `kernel_launch_sec`
- `stream_sync_sec`
- `copy_back_sec`
- `convert_sec`
- `write_sec`
- `wait_stream_sec`
- `wait_buffer_sec`
- `gpu_worker_total_sec`

At end of run, emit:

- total time per stage
- average time per patch per stage
- p50 / p90 / p99 for key stages if practical
- total patches processed
- effective patches per second

## Specific Code Areas To Instrument

### Producer

Instrument inside the producer loop in `GenerateHorizonsForPatches(...)` around:

- `CalculateSubpatchRaySegments(...)`
- `patchWorkChannel.Writer.WriteAsync(...)`

Questions to answer:

- How long does segment generation take per patch?
- Is the producer blocking on a full queue?
- Is the producer the pacing stage?

### Buffer Acquisition

Instrument around:

- `_bufferPool.GetAvailableBuffers(...)`
- stream acquisition from `_streamPool`

Questions to answer:

- Are workers waiting on buffers?
- Are workers waiting on streams?
- Is `MAX_CONCURRENT_GPU_OPS = 16` actually usable, or are other resources constraining effective parallelism?

### LaunchPatchAsync

Instrument the internal stages separately:

1. filling or resetting `HorizonsAccum`
2. `buffers.GpuSegments.CopyFromCPU(allSegments)`
3. all kernel launches across DEM passes
4. `stream.Synchronize()`
5. `buffers.HorizonsAccum.GetAsArray1D()`
6. `HorizonAngles.FromRadians(...)`

Questions to answer:

- How much time is real kernel execution versus transfers?
- Is output copy-back larger than kernel time?
- Does CPU conversion materially delay stream return?

### File Output

Instrument around:

- compression
- temp-file write
- move/rename
- fallback write path

Questions to answer:

- Are GPU workers spending nontrivial time in compression and file I/O?
- Does output writing need its own downstream stage?

## Suggested Aggregation Class

Add a small internal metrics accumulator in `QuadTreeHorizonGenerator.cs` to collect:

- total elapsed ticks per stage
- count of samples per stage
- max observed time per stage

The accumulator should be thread-safe and low overhead. A practical design is:

- `ConcurrentDictionary<string, StageStats>` or a fixed internal struct keyed by enum
- `Interlocked` counters for totals and counts

Use this only for profiling runs. It can stay compiled in if the overhead is low, or be guarded behind an environment variable such as:

- `QUADTREE_PIPELINE_PROFILE=1`

## Measurement Sequence

Run the following sequence without changing kernel logic:

1. Baseline with current `MAX_CONCURRENT_GPU_OPS = 16`.
2. Repeat with `MAX_CONCURRENT_GPU_OPS = 8`.
3. Repeat with `MAX_CONCURRENT_GPU_OPS = 4`.
4. Repeat with `MAX_CONCURRENT_GPU_OPS = 2`.

For each run, capture:

- aggregate stage timings
- patches per second
- GPU utilization trace from `nvidia-smi dmon -s u`
- CPU utilization from `htop` or equivalent

The goal is to identify whether throughput improves, stalls, or regresses as stream count changes.

## Expected High-Value Follow-Ups After Measurement

These are not the first step, but they are the most likely improvements if the metrics confirm the hypotheses.

### 1. Remove CPU-to-GPU output initialization copy

Current code fills a CPU array with `-inf` and copies it into `HorizonsAccum` each patch.

If metrics show this is significant, replace it with:

- a device-side fill kernel, or
- an ILGPU fill primitive if available and efficient

Expected benefit:

- remove one full host-to-device copy per patch

### 2. Split GPU work from file output

If `compress_and_write` is significant, create a writer stage:

1. GPU worker completes stream sync and copy-back.
2. GPU worker enqueues host-side output to a writer queue.
3. GPU worker immediately returns stream and buffers.
4. Separate writer tasks handle compression and file output.

Expected benefit:

- higher GPU queue pressure
- shorter stream hold time

### 3. Move conversion off the stream critical path

If `HorizonAngles.FromRadians(...)` is nontrivial, move radians-to-degrees conversion into the writer stage or convert on the GPU before copy-back.

Expected benefit:

- stream returned sooner
- less CPU work on the GPU-worker hot path

### 4. Parallelize patch preparation

If `segment_generation` dominates, replace the single producer with a bounded CPU producer pool.

Expected benefit:

- reduce GPU starvation from patch-preparation lag

Risk:

- too many CPU producers can create memory pressure and hurt overall balance

### 5. Revisit stream count only after stage timing exists

If transfers dominate, more streams may hurt rather than help.

Expected benefit of delaying stream tuning:

- avoid optimizing the wrong level of the pipeline

## Acceptance Criteria For This Profiling Pass

- `docs/meter-horizon-pipeline.md` exists and describes the profiling plan.
- The pipeline has stage-level instrumentation for producer, GPU worker, transfer, synchronization, conversion, and file output.
- End-of-run metrics identify the dominant stage by wall time.
- We can answer whether GPU underutilization is caused primarily by:
  - producer starvation
  - transfer overhead
  - stream retention
  - file output on the critical path
  - poor stream-count selection

## Recommended Next Decision Gate

Do not make further structural performance changes until one run produces a clear breakdown of:

- percent of patch time in CPU segment generation
- percent of patch time in GPU kernel execution
- percent of patch time in host-device copies
- percent of patch time in result conversion
- percent of patch time in compression and file output

After that, choose exactly one of the following vertical slices:

1. device-side reset of `HorizonsAccum`
2. writer-stage decoupling
3. producer parallelization

Pick the slice that targets the largest measured bottleneck.
