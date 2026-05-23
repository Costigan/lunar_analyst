# Plan: Extending `LightmapArrayStreamingBridge` for Terrain-Relative Sun/Earth Temporal Analytics

## 1. Goal

Extend `native/new_horizon/moonlib/pipeline/streaming/LightmapArrayStreamingBridge.cs` so it can support:

- High-fidelity native computation of terrain-relative Sun and Earth signals per pixel over time.
- A **signal streaming mode** for custom Python reductions (including LLM-written Python code).
- A **native reduction mode** for common high-volume temporal reductions.
- Backward compatibility with the current `(time, 128, 128)` `uint8` `sun_fraction` streaming behavior.

This plan is intentionally scoped to bridge + worker interop + notebook helper integration. It does not introduce an expression DSL or F# runtime compilation.

## 2. Non-Goals (for this plan)

- Arbitrary user-defined native code execution (F#, C#, DSL plugins).
- Full generalized reducer language.
- Replacing `sun_fraction` with an approximate generic visible-fraction model.
- Large refactors of the existing `JobHandlers` pipeline in one step.

## 3. Design Constraints and Invariants

- Preserve existing `LightmapArrayStreamingBridge` behavior for current callers.
- Maintain existing backpressure, cancellation, and progress semantics.
- Keep `JobHandlers`-centered compute contract as the long-term integration target (`backend/jobs/handlers.py`).
- Explicitly define signal semantics and units.
- Avoid silent dtype coercion across native/Python boundaries.

## 4. Canonical Signal Model

The bridge should expose a small canonical set of **native-computed temporal signals** from which the `6.5` scenarios can be built.

### 4.1 First-Class Signals

- `sun_fraction_u8`
  - Range: `0..255`
  - Semantics: Existing high-fidelity terrain-aware Sun visible fraction scaling (`255 * sun_fraction`).
  - Storage: `uint8`
  - Notes: Remains the preferred signal for Sun illumination-style reductions.

- `sun_center_margin_deg_f32`
  - Range: float32 degrees
  - Semantics: `sun_center_elevation_deg - terrain_horizon_elevation_deg_at_sun_azimuth`
  - Storage: `float32`
  - Notes: Needed when explicit angle thresholds/outputs are required.

- `earth_center_margin_deg_f32`
  - Range: float32 degrees
  - Semantics: `earth_center_elevation_deg - terrain_horizon_elevation_deg_at_earth_azimuth`
  - Storage: `float32`
  - Notes: Primary Earth terrain-relative signal for threshold logic.

### 4.2 Derived (Not Required to Stream Explicitly)

These can be derived in native reducers or Python from center margin + body angular radius:

- `body_lower_limb_margin_deg = body_center_margin_deg - body_radius_deg`
- `body_upper_limb_margin_deg = body_center_margin_deg + body_radius_deg`

### 4.3 Optional Later Signal

- `earth_visible_fraction_f32` (or `u8`)
  - Derived from center margin + body radius using a lookup/analytic approximation.
  - Deferred unless a concrete use case requires it.

### 4.4 Why This Covers the 6.5 Scenarios

- Average lighting: use `sun_fraction_u8` (or convert to `[0,1]` float during reduction).
- Earth above terrain time: threshold `earth_center_margin_deg_f32` / lower-limb margin and accumulate time.
- Max contiguous intervals: run-length state machine over predicates from `sun_fraction_u8` and/or Earth margin.
- Combined Sun + Earth constraints: combine predicates per time step before accumulation.

## 5. Extension Strategy (Backward-Compatible)

Use a **dual-path extension**:

1. Keep the existing API path unchanged:
   - `StartLightmapArrayStreaming(...)`
   - Existing request schema
   - Existing output contract `(time, height, width)` `uint8` `sun_fraction`

2. Add a new extended API path in the same class:
   - New request/response/envelope types
   - Supports `SignalStream` and `NativeReduce` modes
   - Supports multiple dtypes and payload layouts

This reduces regression risk while still reusing the same backpressure/cancellation design and much of the existing pipeline code.

## 6. Proposed Native Schema (`moonlib`)

### 6.1 Enums

```csharp
namespace moonlib.pipeline.streaming
{
    public enum LightmapStreamMode
    {
        SignalStream = 1,   // stream temporal signal chunks to Python
        NativeReduce = 2    // stream 2D reduced tiles
    }

    public enum TemporalSignalKind
    {
        SunFractionU8 = 1,
        SunCenterMarginDegF32 = 2,
        EarthCenterMarginDegF32 = 3
    }

    public enum StreamScalarType
    {
        UInt8 = 1,
        Float32 = 2
    }

    public enum TemporalThresholdReference
    {
        CenterMargin = 1,
        LowerLimbMargin = 2,
        UpperLimbMargin = 3
    }

    public enum NativeReducerKind
    {
        AverageSunFraction = 1,
        CumulativeDurationWhere = 2,
        MaxContiguousDurationWhere = 3,
        CombinedSunEarthContiguousDuration = 4
    }

    public enum DurationOutputUnit
    {
        Samples = 1,
        Hours = 2
    }

    public enum ReducedTileOutputType
    {
        UInt8 = 1,      // rare; mainly threshold masks or compact outputs
        UInt16 = 2,     // optional for sample counts when bounded
        Float32 = 3     // preferred for averages/durations
    }
}
```

### 6.2 Signal Selection and Layout

```csharp
namespace moonlib.pipeline.streaming
{
    public sealed record TemporalSignalSpec(
        TemporalSignalKind Signal,
        int ChannelIndex,              // output channel position for SignalStream mode
        bool Enabled = true
    );

    public sealed record TemporalSignalStreamLayout(
        int ChunkTimeCount = 256,      // time chunk size in SignalStream mode
        bool InterleaveChannels = false
        // false => [time, channel, height, width]
        // true  => [channel, time, height, width] (optional; start with false only)
    );
}
```

Recommendation: implement only `[time, channel, height, width]` initially and remove `InterleaveChannels` until needed.

### 6.3 Predicate Specs for Native Reduction

```csharp
namespace moonlib.pipeline.streaming
{
    public sealed record ThresholdPredicateSpec(
        TemporalSignalKind Signal,
        TemporalThresholdReference Reference = TemporalThresholdReference.CenterMargin,
        float ThresholdValue = 0f,
        bool GreaterThanOrEqual = true,
        float BodyRadiusDegOverride = float.NaN
        // If NaN, use built-in body radius for Sun/Earth.
    );

    public sealed record SunFractionPredicateSpec(
        byte MinSunFractionU8 = 1, // e.g. > 0 means any light
        bool GreaterThanOrEqual = true
    );
}
```

### 6.4 Native Reducer Specs

```csharp
namespace moonlib.pipeline.streaming
{
    public abstract record NativeReducerSpec(
        NativeReducerKind Kind,
        ReducedTileOutputType OutputType = ReducedTileOutputType.Float32);

    public sealed record AverageSunFractionReducerSpec(
        bool OutputNormalized01 = true,             // true => float32 [0,1], false => uint8/float32 [0,255]
        ReducedTileOutputType OutputType = ReducedTileOutputType.Float32
    ) : NativeReducerSpec(NativeReducerKind.AverageSunFraction, OutputType);

    public sealed record CumulativeDurationWhereReducerSpec(
        ThresholdPredicateSpec? MarginPredicate = null,
        SunFractionPredicateSpec? SunPredicate = null,
        DurationOutputUnit Unit = DurationOutputUnit.Hours,
        ReducedTileOutputType OutputType = ReducedTileOutputType.Float32
    ) : NativeReducerSpec(NativeReducerKind.CumulativeDurationWhere, OutputType);

    public sealed record MaxContiguousDurationWhereReducerSpec(
        ThresholdPredicateSpec? MarginPredicate = null,
        SunFractionPredicateSpec? SunPredicate = null,
        DurationOutputUnit Unit = DurationOutputUnit.Hours,
        ReducedTileOutputType OutputType = ReducedTileOutputType.Float32
    ) : NativeReducerSpec(NativeReducerKind.MaxContiguousDurationWhere, OutputType);

    public sealed record CombinedSunEarthContiguousDurationReducerSpec(
        SunFractionPredicateSpec SunPredicate,
        ThresholdPredicateSpec EarthMarginPredicate,
        DurationOutputUnit Unit = DurationOutputUnit.Hours,
        ReducedTileOutputType OutputType = ReducedTileOutputType.Float32
    ) : NativeReducerSpec(NativeReducerKind.CombinedSunEarthContiguousDuration, OutputType);
}
```

Notes:
- This is intentionally explicit and limited.
- Reducers should reject invalid combinations (e.g., missing predicate for a reducer that requires one).

### 6.5 Extended Request

```csharp
namespace moonlib.pipeline.streaming
{
    public sealed record LightmapArrayStreamRequestV2(
        string ScenarioRootDir,
        string DemPath,
        IReadOnlyList<string>? SurroundingDemPaths,
        string HorizonDir,
        DateTime StartUtc,
        DateTime StopUtc,
        double TimeStepHours,
        float ObserverElevationMeters,
        int PatchWidth = 128,
        int PatchHeight = 128,
        int MaxReadParallelism = 4,
        int MaxComputeParallelism = 24,
        int ReadyQueueCapacity = 64,
        bool UseSpiceSunVectors = true,

        // New fields
        LightmapStreamMode Mode = LightmapStreamMode.SignalStream,
        IReadOnlyList<TemporalSignalSpec>? Signals = null,
        TemporalSignalStreamLayout? SignalLayout = null,
        IReadOnlyList<NativeReducerSpec>? Reducers = null,

        // Earth support
        bool UseSpiceEarthVectors = true
    );
}
```

Validation rules:
- `PatchWidth/PatchHeight` remain `128x128` in Phase 1.
- `Mode == SignalStream` requires `Signals` non-empty and `Reducers == null || empty`.
- `Mode == NativeReduce` requires `Reducers` non-empty.
- `SignalStream` Phase 1: all selected signals must share one scalar type per request (see payload constraints below), or split into separate requests.

### 6.6 Extended Tile Envelope / Payload Metadata

The current `TileEnvelope` is insufficient because it assumes one fixed payload shape.

Add a V2 envelope that describes payload shape/dtype explicitly:

```csharp
namespace moonlib.pipeline.streaming
{
    public sealed record TileEnvelopeV2(
        string JobId,
        long TileId,
        int BufferId,
        int PatchRow,
        int PatchCol,
        int Width,
        int Height,
        StreamTileState State,

        // Payload metadata
        StreamScalarType ScalarType,
        int Rank,
        int Dim0,
        int Dim1,
        int Dim2,
        int Dim3,
        int TimeOffset,       // SignalStream mode: start index of chunk in full time axis
        int TimeCount,        // SignalStream mode: chunk length; NativeReduce mode: full time count or 0
        int ChannelCount,     // SignalStream mode: number of streamed channels; NativeReduce mode: #reduced outputs

        string? Message = null
    );
}
```

Conventions:
- `Rank` is `4` for SignalStream (`[time, channel, h, w]`).
- `Rank` is `3` for NativeReduce (`[channel, h, w]`) to allow multiple reducers in one pass.
- Unused dims set to `1`.
- `ScalarType` applies to the whole payload.

## 7. Payload / Buffer Contract

### 7.1 Buffer Registration

Add V2 registration methods:

- `RegisterOutputBufferV2(string jobId, int bufferId, long ptr, int byteLength)`
- `TryGetNextTileV2(string jobId, int timeoutMs) -> TileEnvelopeV2`

`RegisterOutputBufferV2` validates `byteLength` against the job’s expected payload size for the selected mode.

### 7.2 Dtype Rules (per user requirement)

- `sun_fraction` values can be represented as bytes (`0..255`) and should use `uint8`.
- Cases where explicit elevation angles must be returned use `float32`.

Implications:
- SignalStream requests that include any explicit angle signal (`*_margin_deg_f32`) must use `float32` payloads for the whole request in Phase 1.
- `sun_fraction_u8` in a float32 payload is converted/scaled on the native side to `float32` (prefer `[0,255]` raw scale for exact round-trip, with metadata noting scale).
- To preserve compact transfer for pure lighting workflows, allow pure `sun_fraction_u8` SignalStream requests with `uint8` payload.

### 7.3 Recommended Phase 1 Simplification

To minimize complexity, support these request classes first:

- `SignalStream` + `uint8` payload: `sun_fraction_u8` only
- `SignalStream` + `float32` payload: one or more angle signals and optionally `sun_fraction` cast to float32
- `NativeReduce` + `float32` reduced outputs (default and preferred)

Defer mixed packed payloads (e.g., separate `uint8` + `float32` subbuffers in one tile).

## 8. Native Computation Details

### 8.1 Reuse Existing Sun Path

Continue using:

- `dem.GetAzEl(...)`
- `LightmapGenerator.BuilderSunFraction(...)`

This preserves current Sun accuracy.

### 8.2 Add a Terrain Horizon Sampling Helper

Add a helper in `LightmapArrayStreamingBridge` or a shared utility:

```csharp
private static float SampleHorizonElevationDeg(float[] horizons, int horizonBase, float azimuthDeg)
```

Requirements:
- Match azimuth wrapping conventions used by `BuilderSunFraction`.
- Use interpolation consistent with horizon bucket interpretation.
- Return terrain horizon elevation at the requested azimuth (degrees).

This is needed for margin calculations for both Sun and Earth.

### 8.3 Earth Vectors

Build `earthVectors` alongside `sunVectors`:

- `SpiceManager.EarthPosition(time)` when `UseSpiceEarthVectors == true`
- Synthetic fallback only if scientifically acceptable; otherwise require SPICE for Earth signals

Recommendation:
- If Earth-dependent signals/reducers are requested and SPICE Earth vectors are unavailable, fail fast with a clear error.

### 8.4 Per-Pixel Signal Evaluation API (Internal)

Create an internal method that computes selected signals for one `(pixel, time)` sample:

```csharp
private static void ComputeSignalsForSample(
    /* cached per-pixel geometry, horizons, vectors, requested signal flags */,
    out byte sunFractionU8,
    out float sunCenterMarginDeg,
    out float earthCenterMarginDeg)
```

Implementation notes:
- Compute only requested signals for the job to avoid unnecessary work.
- Cache `Matrix4d` per pixel per tile (already done).
- For margin signals:
  - compute body center az/el
  - sample terrain horizon at azimuth
  - subtract to get margin

### 8.5 Native Reduction Engine (Built-In Reducers)

Add reducer state structs/classes that update per time step and finalize per pixel.

Examples:

- `AverageSunFractionReducerState`
  - Running sum of `sun_fraction_u8` (or normalized float)
  - Count

- `CumulativeDurationPredicateReducerState`
  - Running cumulative duration when predicate true

- `MaxContiguousPredicateReducerState`
  - Current run duration
  - Max run duration

Predicates should be evaluated from canonical signals:
- Sun predicate from `sun_fraction_u8`
- Margin predicates from center margin + threshold reference (center/lower/upper) and body radius

### 8.6 Body Radius Constants

Define explicit constants (degrees) for threshold-reference conversions:

- `SunAngularRadiusDeg` (constant or configurable)
- `EarthAngularRadiusDeg` (constant or configurable default)

Recommendation:
- Start with configurable defaults in request or reducer spec override (`BodyRadiusDegOverride`).
- Document the default values and assumptions.

## 9. Python Worker Schema and API Plan (`backend/worker/lightmap_streaming.py`)

### 9.1 Preserve Existing Python API

Keep existing:

- `LightmapStreamRequestPy`
- `stream_tiles(...)` returning `(tile_meta, np.ndarray)` with 3D `uint8`

### 9.2 Add V2 Python Dataclasses

Add parallel types instead of mutating the existing dataclasses:

```python
@dataclass(frozen=True)
class TemporalSignalSpecPy:
    signal: str  # "sun_fraction_u8" | "sun_center_margin_deg_f32" | "earth_center_margin_deg_f32"

@dataclass(frozen=True)
class LightmapStreamRequestV2Py:
    # common fields (same as v1)
    ...
    mode: str  # "signal_stream" | "native_reduce"
    signals: list[TemporalSignalSpecPy] | None = None
    chunk_time_count: int = 256
    reducers: list[dict[str, Any]] | None = None
    use_spice_earth_vectors: bool = True

@dataclass(frozen=True)
class StreamTileMetaV2Py:
    ...
    scalar_type: str
    rank: int
    dims: tuple[int, int, int, int]
    time_offset: int
    time_count: int
    channel_count: int
```

### 9.3 Python Buffer Allocation Rules

`stream_tiles_v2(...)` allocates based on request mode:

- `SignalStream`:
  - `uint8`: `np.zeros((chunk_t, channel, h, w), dtype=np.uint8)`
  - `float32`: `np.zeros((chunk_t, channel, h, w), dtype=np.float32)`

- `NativeReduce`:
  - default `float32`: `np.zeros((channel, h, w), dtype=np.float32)`

### 9.4 Chunked Streaming for Custom Python

Add a chunked iterator API:

- `stream_tiles_v2(...)` yields chunks for each tile with `time_offset`
- Python reductions can maintain per-tile or global per-pixel carry state across chunks

This is the key path for LLM-authored custom reductions without needing native codegen.

## 10. Notebook Helper Plan (`backend/notebook/notebook_helper.py`)

### 10.1 Preserve Existing `run_lightmap_streaming_raster_job(...)`

No behavior change for current jobs that depend on `tile_transform(tile_3d)`.

### 10.2 Add New Helper(s) for V2

Add one or both:

- `run_lightmap_signal_streaming_raster_job(...)`
  - Consumes V2 `SignalStream` chunks
  - Accepts Python reducer callbacks with chunk/state protocol

- `run_lightmap_native_reduction_raster_job(...)`
  - Uses V2 `NativeReduce`
  - Writes reduced 2D outputs directly

Recommendation:
- Start with `run_lightmap_native_reduction_raster_job(...)` for common scenarios.
- Then add chunked custom reducer helper once V2 streaming is stable.

### 10.3 Python Reducer Contract for `SignalStream`

Define a reducer protocol suitable for LLM-generated code:

```python
class ChunkedTemporalReducer(Protocol):
    def init_tile_state(self, tile_meta: StreamTileMetaV2Py) -> Any: ...
    def update(self, state: Any, tile_chunk: np.ndarray, tile_meta: StreamTileMetaV2Py) -> Any: ...
    def finalize(self, state: Any, tile_meta: StreamTileMetaV2Py) -> np.ndarray: ...
```

Notes:
- `tile_chunk` shape is `[time, channel, h, w]`
- `finalize(...)` returns `[h, w]` (or multiple bands if extended later)

This preserves the “custom Python” goal while keeping memory bounded.

## 11. JobHandlers / Backend Integration Plan

Per project invariant, production job contracts should ultimately be driven from `backend/jobs/handlers.py`.

Phased approach:

1. Prototype V2 in worker + notebook helper (fast feedback).
2. Add `JobHandlers` methods for:
   - native average sun fraction raster
   - native Earth-duration raster
   - native combined Sun+Earth contiguous-duration raster
3. Export/update contract schemas and notebook job discovery metadata as needed.

Do not create a parallel long-term compute contract layer outside `JobHandlers`.

## 12. Implementation Phases

### Phase 0: Semantics and Schema Finalization (Design-only)

- Finalize canonical signal semantics (especially Earth threshold reference semantics).
- Finalize default body radii and units.
- Finalize V2 request/envelope schema.

Deliverable:
- Agreed schema and semantics (this document + code comments in implementation).

### Phase 1: Native V2 `SignalStream` (Sun only, `sun_fraction_u8`)

Scope:
- Add V2 request/envelope/register/poll methods.
- Implement `SignalStream` mode for `sun_fraction_u8` only.
- Chunking support (`ChunkTimeCount`) and envelope metadata (`time_offset`, dims).
- Python `stream_tiles_v2(...)` support for `uint8`.

Why first:
- Reuses existing Sun computation.
- Validates V2 payload metadata and chunking without Earth or float32 complexity.

Acceptance:
- Existing v1 tests still pass.
- V2 stream chunk reassembly reproduces v1 `sun_fraction` tensors exactly for same tiles/times.

### Phase 2: Native V2 `SignalStream` (Angle Signals, `float32`, Earth support)

Scope:
- Add `SampleHorizonElevationDeg`.
- Add `sun_center_margin_deg_f32`.
- Add `earth_center_margin_deg_f32`.
- Add `float32` payload support and Python buffer allocation.
- Fail-fast behavior when Earth signals requested but Earth vectors unavailable.

Acceptance:
- Numerical spot-checks against Python reference implementation for margins.
- Mixed signal requests in `float32` mode work (`sun_fraction` cast + Earth margin).

### Phase 3: Native V2 `NativeReduce` (Built-in Reducers for 6.5 Core Scenarios)

Scope:
- Implement built-in reducers:
  - `AverageSunFraction`
  - `CumulativeDurationWhere`
  - `MaxContiguousDurationWhere`
  - `CombinedSunEarthContiguousDuration`
- `float32` reduced outputs (`[channel, h, w]`)
- Notebook helper for direct reduced raster writes

Acceptance:
- Results match Python chunked reference reducers within tolerance.
- Large time-range jobs show significant memory/IPC reduction versus v1.

### Phase 4: `JobHandlers` Integration and Contracts

Scope:
- Add/extend `backend/jobs/handlers.py` methods for production use cases.
- Add API/job contract schemas and tests.
- Progress/cancellation observability verification.

Acceptance:
- End-to-end job runs through backend APIs with structured progress/cancellation.

## 13. Test Plan

### 13.1 Native (`moonlib`) Unit / Integration Tests

- `ComputeTimeCount` + chunk partitioning correctness
- V2 request validation matrix
- Buffer byte-length validation by mode/dtype
- `SampleHorizonElevationDeg` interpolation and wraparound tests
- Sun margin and Earth margin spot tests on synthetic horizons
- Reducer state machine tests:
  - cumulative duration
  - max contiguous duration
  - combined predicates

### 13.2 Python Worker Tests

- V2 request marshaling to .NET request
- V2 envelope parsing into `StreamTileMetaV2Py`
- Buffer allocation by dtype/mode
- Chunked reducer protocol tests
- Error propagation from V2 `TileEnvelopeV2` error states

### 13.3 Regression Tests

- Existing v1 streaming tests remain unchanged and passing:
  - `backend/tests/worker/test_lightmap_streaming.py`
  - `backend/tests/worker/test_notebook_helper.py`
- Add a “fails-before / passes-after” test for at least one 6.5 scenario (e.g., max contiguous Sun+Earth predicate duration).

### 13.4 Validation Against Python Reference

Build a reference reducer harness in Python that:

- Consumes V2 `SignalStream` chunks
- Computes same outputs as native reducers
- Compares tile outputs with tolerances:
  - exact for `sun_fraction_u8` sums/count-derived outputs when using sample units
  - tolerance for float32 duration/angle-based outputs

## 14. Performance and Memory Expectations

### 14.1 `SignalStream` Mode

- Improves memory footprint via chunking (bounded `chunk_t`) even when Python does custom reductions.
- IPC reduction depends on selected signals:
  - `sun_fraction_u8` only: compact
  - float32 angle signals: larger, but still bounded with chunking

### 14.2 `NativeReduce` Mode

- Minimal IPC: 2D reduced outputs only.
- Best path for long time ranges and repeated standard analyses.

## 15. Risks and Mitigations

- Risk: Schema complexity / marshaling friction with pythonnet and record inheritance
  - Mitigation: Prefer simple concrete record types if inheritance becomes brittle.

- Risk: Scientific ambiguity in Earth threshold semantics
  - Mitigation: Encode threshold reference explicitly (`center/lower/upper`) and document defaults.

- Risk: Mixed dtype signal streaming complexity
  - Mitigation: Phase 1/2 restrict to one scalar type per request; cast `sun_fraction` to float32 when needed.

- Risk: Regression in v1 streaming behavior
  - Mitigation: Separate V2 methods/types; do not modify v1 path logic initially.

- Risk: Performance loss from recomputing unnecessary signals
  - Mitigation: Compile/request-time signal flags; compute only requested signals/reducer inputs.

## 16. Rollback Plan

- Keep v1 bridge and Python path untouched and default.
- Gate V2 usage behind explicit caller path (new methods / new request types).
- If V2 fails or is unstable, disable V2 callers and continue using v1 without reverting native code immediately.

## 17. Recommended First Slice (1-hour-ish vertical task)

Implement **Phase 1 skeleton only**:

- Add V2 request/envelope types and bridge methods (`Start...V2`, `Register...V2`, `TryGetNextTileV2`)
- Add `SignalStream` `sun_fraction_u8` chunking for one signal
- Add Python `stream_tiles_v2(...)` for `uint8`
- Add one reassembly test proving equivalence to v1

This validates the extension architecture before adding Earth signals, float32, or native reducers.

