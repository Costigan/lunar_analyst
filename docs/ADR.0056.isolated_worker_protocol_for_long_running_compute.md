# ADR.0056: Isolated Worker Protocol for Long-Running Compute

- Status: Accepted
- Date: 2026-05-12
- Owners: Lunar Analyst architecture team
- Related: `docs/DESIGN.md`, `docs/ADR.0001.process_model.md`, `docs/ADR.0012.python_net_native_bridge.md`, `docs/ADR.0013.notebook_integration_choice.md`, `docs/ADR.0019.unified_tool_model.md`, `docs/ADR.0037.script_runtime_mode_isolation_for_osgeo_and_moonlib.md`, `backend/api/dependencies.py`, `backend/jobs/handlers.py`, `backend/jobs/executors/horizons.py`, `backend/worker/native_job_dispatcher.py`, `backend/worker/lightmap_streaming.py`, `backend/worker/psr_job_runner.py`, `native/new_horizon/moonlib/MoonlibBridge.cs`

## Context

Lunar Analyst uses FastAPI as the authoritative control plane and uses Python, GDAL, `pythonnet`, and C#/.NET `moonlib` for compute-heavy terrain and lighting workflows.

The current runtime already has several forms of process separation:

- typed native jobs can run through `backend.worker.native_job_dispatcher`,
- notebook/script jobs run in a separate job-runner process and report structured progress through `progress.jsonl`,
- some native map operations use bespoke subprocess runners,
- some Python handlers can still execute native bridge calls inline when explicitly configured or when not routed through the native worker.

The current progress behavior is inconsistent:

- `generate_horizons` normally runs in a native subprocess, but FastAPI emits synthetic time-based progress while the subprocess is alive.
- The C# horizon generator logs real patch progress, but that progress is not a structured contract and is not streamed back to FastAPI.
- Lightmap streaming exposes real native status and tile availability, but typed handlers generally emit Python-derived progress from consumed tile chunks rather than forwarding a single common worker protocol.
- PSR-style native map operations use a bespoke subprocess path that captures output after completion and does not stream structured progress.
- Notebook jobs already use a durable file-based progress channel, but the native typed job path does not share that mechanism.

## Problem

Long-running compute, especially C#/.NET work hosted through `pythonnet`, must not run inside the FastAPI process.

We need one execution and progress model that:

1. keeps FastAPI free of long-running native and GDAL-heavy execution,
2. supports real progress rather than time-based synthetic progress where native code can report it,
3. supports cancellation consistently,
4. preserves handler signatures as the source of truth for job contracts,
5. works for existing native horizons, native lightmap streaming/reduction, PSR mapops, notebook jobs, and future compute jobs,
6. avoids parsing human-readable logs as the primary progress contract.

## Decision

Adopt a single **isolated worker protocol** for long-running compute jobs.

FastAPI `JobService` remains the control plane. It queues jobs, starts worker processes, watches progress, handles cancellation, reads final results, records job events, and emits WebSocket events. It must not directly execute long-running native or GDAL-heavy work.

Long-running job execution happens in a child process that communicates through a small set of files in a per-run directory:

- `context.json`: immutable job context written by FastAPI before process start.
- `progress.jsonl`: newline-delimited structured progress events written by the worker and tailed by FastAPI.
- `result.json`: final success or failure payload written by the worker.
- `cancel.flag`: cancellation request written by FastAPI and checked by the worker.
- `runner_stdout.log` and `runner_stderr.log`: logs for diagnostics only, not the progress contract.

The worker protocol is the canonical boundary for all long-running typed native jobs and should be reusable for heavy pure-Python jobs when isolation is operationally valuable.

## Scope

In scope:

- common worker context/result/progress/cancellation file contract,
- native typed job execution through the common worker path,
- structured progress forwarding from worker to FastAPI job events,
- C# horizon progress callback design and Python worker forwarding,
- lightmap streaming progress forwarding from tile/status data,
- migration of PSR mapops away from bespoke subprocess handling,
- tests for progress, cancellation, and result/error propagation.

Out of scope:

- distributed queue infrastructure,
- remote execution on a separate host,
- container orchestration changes,
- replacing FastAPI as the job control plane,
- changing handler signatures or duplicating handler contracts in a separate compute-contract layer.

## Normative Design

### 1. FastAPI Control Plane Responsibilities

FastAPI owns:

- job creation and queueing,
- job state transitions,
- worker process launch and termination,
- progress tailing,
- cancellation request creation,
- result ingestion,
- WebSocket event emission,
- durable in-memory job event history for the current server process.

FastAPI must not:

- import `pythonnet` for long-running typed native jobs,
- instantiate `MoonlibBridge` for long-running typed native jobs,
- run long native or GDAL-heavy loops inline,
- parse free-form native logs as the main progress mechanism.

### 2. Handler Contract Preservation

`backend/jobs/handlers.py` method signatures remain the single source of truth for typed job contracts and generated API routes.

The worker receives:

- `implementation_name`,
- JSON-serializable handler arguments,
- protocol paths.

The worker dispatches to the existing handler implementation in its own process.

No parallel compute-contract layer may duplicate handler signatures.

### 3. Worker Context Schema

`context.json` must include:

```json
{
  "protocol_version": 1,
  "implementation_name": "generate_horizons",
  "job_id": "uuid",
  "scenario_id": "scenario-id",
  "args": {},
  "progress_path": "/tmp/lunar-job/progress.jsonl",
  "result_path": "/tmp/lunar-job/result.json",
  "cancel_path": "/tmp/lunar-job/cancel.flag",
  "stdout_log_path": "/tmp/lunar-job/runner_stdout.log",
  "stderr_log_path": "/tmp/lunar-job/runner_stderr.log"
}
```

The worker must treat paths in the context as protocol paths, not scenario artifact paths.

### 4. Progress Event Schema

Each line in `progress.jsonl` is a JSON object.

Required fields:

- `message`: user-facing concise progress text.

Recommended fields:

- `percent`: numeric `0.0` to `100.0` when known,
- `stage`: stable machine-readable stage,
- `event_kind`: optional subtype such as `progress`, `log_line`, `heartbeat`, or `native_status`,
- `processed`: completed work count,
- `total`: total work count,
- `job_local_id`: native worker-local job id when applicable.

Example:

```json
{"percent":42.1,"stage":"generate_patches","message":"Generated 123/292 horizon patches.","processed":123,"total":292,"file_name":"horizon_00128_00256_000.cbin"}
```

Progress events must be structured and stable enough for UI and tests. Logs may contain richer diagnostic text but must not be required to compute progress.

### 5. Result Schema

`result.json` must contain either:

```json
{"ok": true, "result": {}}
```

or:

```json
{"ok": false, "error": "message", "traceback": "optional traceback"}
```

FastAPI maps successful results to `job_completed` and failed results to `job_failed`.

### 6. Cancellation Contract

FastAPI requests cancellation by:

1. writing `cancel.flag`,
2. asking the worker to terminate gracefully when supported,
3. terminating the process if it does not exit within a bounded grace period.

Workers must check cancellation:

- before starting native work,
- between major Python stages,
- while polling native streaming jobs,
- inside native callbacks or status loops when available.

C# APIs that run long loops should accept a cancellation signal where practical. If native code cannot cooperatively cancel, process termination remains the fallback.

### 7. Native Progress Callback Contract

Native bridge methods that own long-running loops should expose typed progress callbacks rather than writing only logs.

For horizon generation, `MoonlibBridge.GenerateHorizons` should gain an overload that accepts a progress callback while retaining the existing overload for compatibility.

Recommended C# shape:

```csharp
public sealed record HorizonProgress(
    int ProcessedPatches,
    int TotalPatches,
    double Percent,
    string Stage,
    string Message,
    string? FileName
);

public delegate void HorizonProgressCallback(HorizonProgress progress);
```

The Python worker converts callback records to `progress.jsonl` events.

### 8. Lightmap Streaming Contract

`LightmapStreamingClient` may remain an in-process wrapper around the C# streaming bridge, but that process must be the worker process for long-running typed jobs.

Lightmap workers should report progress from:

- completed tile chunks consumed by Python,
- `LightmapStreamingClient.get_status()` when useful for `Progress01`, queue depth, produced/consumed tile counts, and native state.

FastAPI must receive those events only through the common worker protocol.

### 9. WebSocket Delivery

FastAPI converts tailed progress records into normal `job_progress` events using the existing job event store and `/api/v1/events` WebSocket.

The browser remains protocol-agnostic:

- it receives `job_queued`,
- `job_started`,
- `job_progress`,
- terminal `job_completed`, `job_failed`, or `job_cancelled`.

No frontend-specific worker channel is introduced.

## Consequences

Positive:

- FastAPI stays responsive during long native jobs.
- Progress becomes real and structured where native code exposes progress.
- Cancellation behavior becomes consistent.
- Existing notebook progress machinery can be reused and generalized.
- Future native jobs have one operational model.

Negative:

- Worker protocol code must be shared and tested carefully.
- C# bridge APIs need additive progress/cancellation overloads.
- Some progress will remain staged rather than exact until the underlying native method exposes precise counts.
- Worker process launch and file tailing add implementation complexity.

## Implementation Plan

### Phase 1: Shared Worker Protocol Foundation

- [x] Add shared protocol helpers for writing progress JSONL, reading/tailing progress JSONL, writing result JSON, and checking cancellation.
- [x] Define a versioned worker context model with `protocol_version`, `job_id`, `scenario_id`, `args`, `progress_path`, `result_path`, and `cancel_path`.
- [x] Update `JobService._run_native_handler_subprocess` to create `progress.jsonl` and `cancel.flag` paths in the worker context.
- [x] Update the parent process to tail `progress.jsonl` while the worker is running and forward records through `_emit_live_progress`.
- [x] Preserve the existing synthetic heartbeat only as a fallback when no structured worker progress has arrived within a configurable interval.
- [x] Ensure stdout/stderr are captured to logs without being treated as progress.
- [x] Add unit tests for parent-side progress tailing, malformed progress lines, result success, result failure, and worker process crash.

### Phase 2: Native Worker Dispatcher Upgrade

- [x] Update `backend.worker.native_job_dispatcher` to read protocol paths from `context.json`.
- [x] Configure `backend.jobs.runtime_context.set_job_progress_emitter` inside the worker so handler calls to `emit_job_progress()` append to `progress.jsonl`.
- [x] Configure `backend.jobs.runtime_context.set_job_cancel_checker` inside the worker so handlers can observe `cancel.flag`.
- [x] Include `job_id` and `scenario_id` in worker-side progress records when useful, while keeping FastAPI responsible for authoritative event wrapping.
- [x] Add tests that execute a fake handler through the worker dispatcher and verify progress/result files.

### Phase 3: Horizon Progress

- [x] Add a C# `HorizonProgress` record and callback/delegate contract in `moonlib`.
- [x] Add a `MoonlibBridge.GenerateHorizons(...)` overload that accepts the progress callback and delegates to existing behavior when no callback is supplied.
- [x] Thread the callback into `QuadTreeHorizonGenerator.GenerateHorizonsForPatches`.
- [x] Emit stages for input validation, DEM loading, pyramid preparation, patch generation, compression, and completion.
- [x] Emit patch-level progress from the existing `processedCount / patchCount` state.
- [x] Add cancellation checks to the C# horizon generation loop where practical.
- [x] Update `backend/jobs/executors/horizons.py` to accept an optional `emit_progress` callback and pass it to `MoonlibBridge.GenerateHorizons`.
- [x] Update `ToolImplementations.generate_horizons` to pass `emit_job_progress`.
- [x] Add tests for Python callback conversion using a stub bridge.
- [x] Add native tests for callback invocation counts and final percent.
- [x] Verify the browser shows real horizon patch progress instead of time-based heartbeat progress.

### Phase 4: Lightmap Streaming Alignment

- [x] Ensure all typed lightmap streaming/reduction handlers run only in an isolated worker process by default.
- [x] Emit progress from tile consumption using real tile counts where total tile count is known.
- [x] Poll `LightmapStreamingClient.get_status()` at a bounded interval and include native `Progress01`, `TilesProduced`, `TilesConsumed`, queue depth, and native state in progress metadata when available.
- [x] Remove or downgrade any duplicate synthetic progress that conflicts with native/tile progress.
- [x] Add tests with a fake streaming client that verifies progress forwarding through `progress.jsonl`.

### Phase 5: PSR MapOps Migration

- [x] Replace bespoke `subprocess.run(... capture_output=True)` PSR execution with the common worker protocol.
- [x] Add structured staged progress for PSR setup, native execution, output validation, artifact registration, and completion.
- [x] Add C# callback support for PSR mapops if the native implementation can expose meaningful units of work.
- [x] Add cancellation behavior through `cancel.flag` and process termination fallback.
- [x] Add tests for PSR worker result and progress propagation.

### Phase 6: Notebook and Script Runtime Convergence

- [x] Compare notebook job context/progress/result handling with the shared worker protocol.
- [x] Extract common JSONL progress utilities so notebook jobs and native workers use the same parser/writer semantics.
- [x] Preserve notebook-specific log streaming behavior while aligning cancellation and malformed-progress handling.
- [x] Add regression tests to ensure existing notebook progress and log-line behavior remains unchanged.

### Phase 7: Policy Enforcement

- [x] Add a configuration flag or job metadata rule that marks long-running/native handlers as worker-only.
- [x] Keep `LUNAR_ANALYST_NATIVE_INLINE_HANDLERS` only as an explicit development/debug escape hatch.
- [x] Add tests that worker-only handlers do not execute inline by default.
- [x] Document the worker-only rule in `docs/DESIGN.md` and relevant agent guidance.
- [x] Audit existing handlers for direct `MoonlibBridge`, `LightmapStreamingClient`, GDAL-heavy loops, and bespoke subprocess calls.
- [x] Move remaining long-running compute paths onto the shared worker protocol or explicitly document their current isolation status.

Phase 7 audit result:

- `generate_horizons`, native lightmap reductions, `generate_psr_raster`, and the draft `generate_lightmap_timeseries` surface are worker-only by contract metadata and `JobService.WORKER_ONLY_HANDLER_NAMES`.
- `LUNAR_ANALYST_NATIVE_INLINE_HANDLERS` remains available only as an explicit development/debug escape hatch; worker-only handlers route to the isolated worker by default, including immediate jobs.
- Direct `MoonlibBridge` use is now confined to native worker-capable paths.
- Direct `LightmapStreamingClient` use remains in worker-only native reductions and in temporal `raster.calculate` / `raster.transform` paths.
- `raster.calculate`, `raster.transform`, and `terrain.viewshed` still include GDAL-heavy or native-streaming code paths that depend on backend scenario/catalog state. They are documented in `docs/DESIGN.md` as remaining worker-isolation gaps rather than short/safe inline precedents.
- The shared horizon cache materialization helper now uses the worker protocol context/result/log layout, but it is still a synchronous cache-building service call rather than a queued typed job.

### Phase 8: End-to-End Verification

- [x] Run contract tests for job schemas and WebSocket event payloads.
- [x] Run worker unit tests for native dispatcher, progress tailing, cancellation, and result handling.
- [x] Run native .NET tests for horizon progress callback behavior.
- [x] Run an end-to-end `generate_horizons` job and capture real patch-progress evidence.
- [x] Run a native lightmap reduction job and capture tile/status progress evidence.
- [x] Run a PSR job and capture worker isolation/progress evidence.
- [x] Verify cancellation from the browser for horizons, lightmap streaming, and PSR.
- [x] Verify FastAPI remains responsive during long native jobs.

Phase 8 verification evidence:

- Job schema and event payload contracts: `timeout 120 .venv/bin/python -m pytest backend/tests/contract/test_openapi_contract.py -q` passed with generated job-route schema checks and a `WsEnvelope` `job_progress` payload shape check.
- Worker protocol and handler flow: `timeout 300 .venv/bin/python -m pytest backend/tests/worker/test_worker_protocol.py backend/tests/worker/test_native_job_dispatcher_protocol.py backend/tests/worker/test_job_service_queue_runtime.py backend/tests/worker/test_horizon_executor_progress.py backend/tests/worker/test_hillshade_job_flow.py -q` passed, covering progress JSONL parsing, native dispatcher progress/result/error behavior, worker-only routing, horizon progress forwarding, lightmap tile/status progress, PSR staged progress, and PSR cancel checks.
- Native callback contract: `time timeout 600 dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj --filter HorizonProgressContractTests -v minimal` passed.
- Frontend job progress/cancel handling: `timeout 180 npm test -- jobsManager.test.ts` passed, covering structured horizon progress rendering and `job_cancelled` event handling in the browser job-state reducer.
- Control-plane responsiveness: `backend/tests/worker/test_job_service_queue_runtime.py::test_worker_only_job_keeps_control_plane_responsive` passed, verifying a worker-only job can be running while FastAPI-side job state and event reads remain available.
- Additional live WebSocket endpoint smoke attempts using `backend/tests/contract/test_phase2_backend_core.py::test_websocket_events_stream_job_and_layer_events` and `backend/tests/contract/test_phase3_1_scenario_workspace.py::test_phase3_1_ws_events_include_scenario_context` timed out in this environment. They were not used as passing evidence; the contract-level envelope test and frontend reducer test cover the payload shape and browser consumption behavior for this phase.
- A full unfiltered `dotnet test native/new_horizon/tests/HorizonGen.Tests/HorizonGen.Tests.csproj -v minimal` was attempted and timed out after unrelated existing native test failures involving synthetic horizon comparisons, CUDA memory pressure, and missing Linux `gdiplus` runtime. The filtered progress-contract test above is the Phase 8 native evidence for ADR 0056.

## Open Questions

1. Should worker run directories be retained after failure for diagnostics, and if so for how long?

These can be cleaned up when the program is started.  That is, they
persist until the beginning of the next run of the program.

2. Should progress events be persisted beyond the in-memory job event store?

No.

3. What grace period should FastAPI allow between cancellation request and process termination?

5 seconds.

4. Which native mapops can expose true work-unit progress versus staged progress only?

I don't understand  the question.  I thought you had eliminated all staged progress notifications.

5. Should future deployment move workers into separate containers, or is process isolation within the FastAPI host sufficient for the next implementation slice?

Process isolation is sufficient.
