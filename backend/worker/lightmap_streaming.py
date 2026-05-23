from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from backend.worker.native_bootstrap import import_moonlib

__all__ = [
    "LightmapStreamRequestPy",
    "TemporalSignalSpecPy",
    "LightmapStreamRequestV2Py",
    "LightmapStreamStatusPy",
    "StreamTileMetaPy",
    "StreamTileMetaV2Py",
    "LightmapStreamingClient",
    "stream_tiles",
    "stream_tiles_v2",
]

_CLR_GDAL_REGISTERED = False


def _parse_utc_timestamp(value: str) -> datetime:
    text = str(value).strip()
    if not text:
        raise ValueError("Timestamp is required.")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_state_name(value: Any) -> str:
    to_string = getattr(value, "ToString", None)
    if callable(to_string):
        try:
            return str(to_string())
        except Exception:
            pass
    return str(value)


def _to_dotnet_string_list(values: list[Path]) -> Any:
    try:
        from System import Array
        from System import String
    except Exception:
        return [str(item) for item in values]

    return Array[String]([String(str(item)) for item in values])


def _to_dotnet_datetime_utc(value: str) -> Any:
    parsed = _parse_utc_timestamp(value).astimezone(timezone.utc)
    try:
        from System import DateTime
        from System import DateTimeKind
    except Exception:
        return parsed.replace(tzinfo=None)

    dt = DateTime(
        parsed.year,
        parsed.month,
        parsed.day,
        parsed.hour,
        parsed.minute,
        parsed.second,
        int(parsed.microsecond / 1000),
        DateTimeKind.Utc,
    )
    extra_ticks = int(parsed.microsecond % 1000) * 10
    if extra_ticks:
        dt = dt.AddTicks(extra_ticks)
    return dt


def _to_dotnet_single(value: float) -> Any:
    try:
        from System import Single
    except Exception:
        return float(value)
    return Single(float(value))


def _ensure_clr_gdal_registered() -> None:
    global _CLR_GDAL_REGISTERED
    if _CLR_GDAL_REGISTERED:
        return
    try:
        from OSGeo.GDAL import Gdal
    except Exception as exc:
        raise RuntimeError(
            "Failed to import OSGeo.GDAL in CLR runtime while bootstrapping streaming."
        ) from exc
    Gdal.AllRegister()
    if int(Gdal.GetDriverCount()) <= 0:
        raise RuntimeError("CLR GDAL registration failed (driver_count=0).")
    _CLR_GDAL_REGISTERED = True


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
    max_read_parallelism: int = 4
    max_compute_parallelism: int = 24
    ready_queue_capacity: int = 64
    use_spice_sun_vectors: bool = True

    def time_count(self) -> int:
        start = _parse_utc_timestamp(self.start_utc)
        stop = _parse_utc_timestamp(self.stop_utc)
        if stop < start:
            raise ValueError("stop_utc must be >= start_utc.")
        step = timedelta(hours=float(self.time_step_hours))
        if step <= timedelta(0):
            raise ValueError("time_step_hours must be > 0.")

        count = 0
        current = start
        while current <= stop:
            count += 1
            current = current + step
            if count > 1_000_000:
                raise ValueError("time axis is too large.")
        return max(1, count)

    def tile_shape(self) -> tuple[int, int, int]:
        return (self.time_count(), int(self.patch_height), int(self.patch_width))


@dataclass(frozen=True)
class TemporalSignalSpecPy:
    signal: str


@dataclass(frozen=True)
class LightmapStreamRequestV2Py:
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
    max_read_parallelism: int = 4
    max_compute_parallelism: int = 24
    ready_queue_capacity: int = 64
    use_spice_sun_vectors: bool = True
    mode: str = "signal_stream"
    signals: list[TemporalSignalSpecPy] | None = None
    chunk_time_count: int = 256
    reducers: list[dict[str, Any]] | None = None
    use_spice_earth_vectors: bool = True

    def time_count(self) -> int:
        base = LightmapStreamRequestPy(
            scenario_root_dir=self.scenario_root_dir,
            dem_path=self.dem_path,
            surrounding_dem_paths=self.surrounding_dem_paths,
            horizon_dir=self.horizon_dir,
            start_utc=self.start_utc,
            stop_utc=self.stop_utc,
            time_step_hours=self.time_step_hours,
            observer_elevation_meters=self.observer_elevation_meters,
            patch_width=self.patch_width,
            patch_height=self.patch_height,
            max_read_parallelism=self.max_read_parallelism,
            max_compute_parallelism=self.max_compute_parallelism,
            ready_queue_capacity=self.ready_queue_capacity,
            use_spice_sun_vectors=self.use_spice_sun_vectors,
        )
        return base.time_count()

    def resolved_signals(self) -> list[TemporalSignalSpecPy]:
        if self.signals:
            return list(self.signals)
        return [TemporalSignalSpecPy(signal="sun_fraction_u8")]

    def scalar_type(self) -> str:
        if str(self.mode).strip().lower() == "native_reduce":
            return "float32"
        lowered = {s.signal.strip().lower() for s in self.resolved_signals()}
        if any(name.endswith("_f32") for name in lowered):
            return "float32"
        return "uint8"

    def signal_stream_buffer_shape(self) -> tuple[int, int, int, int]:
        channel_count = len(self.resolved_signals())
        return (
            int(max(1, self.chunk_time_count)),
            channel_count,
            int(self.patch_height),
            int(self.patch_width),
        )

    def native_reduce_channel_count(self) -> int:
        return max(1, len(self.reducers or []))

    def native_reduce_buffer_shape(self, channel_count: int | None = None) -> tuple[int, int, int]:
        count = self.native_reduce_channel_count() if channel_count is None else int(channel_count)
        if count < 1:
            count = 1
        return (count, int(self.patch_height), int(self.patch_width))


@dataclass(frozen=True)
class StreamTileMetaV2Py:
    job_id: str
    tile_id: int
    buffer_id: int
    patch_row: int
    patch_col: int
    width: int
    height: int
    state: str
    scalar_type: str
    rank: int
    dims: tuple[int, int, int, int]
    time_offset: int
    time_count: int
    channel_count: int
    message: str | None


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


@dataclass(frozen=True)
class LightmapStreamStatusPy:
    job_id: str
    state: str
    progress01: float
    tiles_produced: int
    tiles_consumed: int
    ready_queue_depth: int
    free_buffer_count: int
    message: str | None


class LightmapStreamingClient:
    def __init__(
        self,
        *,
        bridge: Any | None = None,
        moonlib_module: Any | None = None,
        force_bootstrap: bool = False,
        verify_bridge_smoke: bool = True,
    ) -> None:
        if bridge is not None:
            self._bridge = bridge
            self._streaming_ns = None
            return

        moonlib = moonlib_module or import_moonlib(
            force_bootstrap=force_bootstrap,
            verify_bridge_smoke=verify_bridge_smoke,
        )
        _ensure_clr_gdal_registered()
        streaming_ns = getattr(getattr(moonlib, "pipeline", None), "streaming", None)
        if streaming_ns is None:
            raise RuntimeError("moonlib.pipeline.streaming namespace is unavailable.")
        bridge_cls = getattr(streaming_ns, "LightmapArrayStreamingBridge", None)
        if bridge_cls is None:
            bridge_cls = getattr(streaming_ns, "LightmapStreamingBridge", None)
        if bridge_cls is None:
            raise RuntimeError(
                "moonlib.pipeline.streaming.LightmapArrayStreamingBridge is unavailable."
            )

        self._streaming_ns = streaming_ns
        self._bridge = bridge_cls()

    def _build_dotnet_request(self, request: LightmapStreamRequestPy) -> Any:
        if self._streaming_ns is None:
            raise RuntimeError("Cannot build .NET request without moonlib streaming namespace.")

        request_cls = getattr(self._streaming_ns, "LightmapArrayStreamRequest", None)
        if request_cls is None:
            request_cls = getattr(self._streaming_ns, "LightmapStreamRequest")
        dotnet_surrounding_dems = _to_dotnet_string_list(
            [Path(path).resolve() for path in request.surrounding_dem_paths]
        )
        args = (
            str(Path(request.scenario_root_dir).resolve()),
            str(Path(request.dem_path).resolve()),
            dotnet_surrounding_dems,
            str(Path(request.horizon_dir).resolve()),
            _to_dotnet_datetime_utc(request.start_utc),
            _to_dotnet_datetime_utc(request.stop_utc),
            float(request.time_step_hours),
            _to_dotnet_single(request.observer_elevation_meters),
            int(request.patch_width),
            int(request.patch_height),
            int(request.max_read_parallelism),
            int(request.max_compute_parallelism),
            int(request.ready_queue_capacity),
            bool(request.use_spice_sun_vectors),
        )
        try:
            return request_cls(*args)
        except TypeError:
            # Compatibility fallback for older bridges/runtime marshaling.
            fallback_args = (
                args[0],
                args[1],
                [str(path) for path in request.surrounding_dem_paths],
                args[3],
                _parse_utc_timestamp(request.start_utc).replace(tzinfo=None),
                _parse_utc_timestamp(request.stop_utc).replace(tzinfo=None),
                args[6],
                float(request.observer_elevation_meters),
                args[8],
                args[9],
                args[10],
                args[11],
                args[12],
                args[13],
            )
            return request_cls(*fallback_args)

    def _build_dotnet_request_v2(self, request: LightmapStreamRequestV2Py) -> Any:
        if self._streaming_ns is None:
            raise RuntimeError("Cannot build .NET V2 request without moonlib streaming namespace.")

        request_cls = getattr(self._streaming_ns, "LightmapArrayStreamRequestV2", None)
        if request_cls is None:
            raise RuntimeError(
                "moonlib.pipeline.streaming.LightmapArrayStreamRequestV2 is unavailable."
            )

        mode_name = str(request.mode).strip().lower()
        mode_enum_cls = getattr(self._streaming_ns, "LightmapStreamMode")
        if mode_name == "signal_stream":
            dotnet_mode = mode_enum_cls.SignalStream
        elif mode_name == "native_reduce":
            dotnet_mode = mode_enum_cls.NativeReduce
        else:
            raise ValueError(f"Unsupported V2 mode: {request.mode!r}")

        signal_spec_cls = getattr(self._streaming_ns, "TemporalSignalSpec")
        signal_kind_enum_cls = getattr(self._streaming_ns, "TemporalSignalKind")
        threshold_ref_enum_cls = getattr(self._streaming_ns, "TemporalThresholdReference")
        reducer_output_type_enum_cls = getattr(self._streaming_ns, "ReducedTileOutputType")
        duration_unit_enum_cls = getattr(self._streaming_ns, "DurationOutputUnit")

        def _signal_kind_from_name(name: str) -> Any:
            lowered = name.strip().lower()
            if lowered == "sun_fraction_u8":
                return signal_kind_enum_cls.SunFractionU8
            if lowered == "sun_center_margin_deg_f32":
                return signal_kind_enum_cls.SunCenterMarginDegF32
            if lowered == "earth_center_margin_deg_f32":
                return signal_kind_enum_cls.EarthCenterMarginDegF32
            raise ValueError(f"Unsupported temporal signal: {name!r}")

        resolved_signals = request.resolved_signals()
        dotnet_signal_specs_py: list[Any] = []
        if mode_name == "signal_stream":
            for idx, spec in enumerate(resolved_signals):
                dotnet_signal_specs_py.append(
                    signal_spec_cls(_signal_kind_from_name(spec.signal), int(idx), True)
                )

        layout_cls = getattr(self._streaming_ns, "TemporalSignalStreamLayout")
        dotnet_layout = layout_cls(int(request.chunk_time_count), False)

        try:
            from System import Array

            dotnet_signal_specs = (
                Array[signal_spec_cls](dotnet_signal_specs_py)
                if dotnet_signal_specs_py
                else None
            )
        except Exception:
            dotnet_signal_specs = dotnet_signal_specs_py or None

        reducers_py: list[Any] = []
        if mode_name == "native_reduce":
            threshold_predicate_cls = getattr(self._streaming_ns, "ThresholdPredicateSpec")
            sun_predicate_cls = getattr(self._streaming_ns, "SunFractionPredicateSpec")
            avg_reducer_cls = getattr(self._streaming_ns, "AverageSunFractionReducerSpec")
            cumulative_reducer_cls = getattr(self._streaming_ns, "CumulativeDurationWhereReducerSpec")
            max_contig_reducer_cls = getattr(self._streaming_ns, "MaxContiguousDurationWhereReducerSpec")
            combined_reducer_cls = getattr(self._streaming_ns, "CombinedSunEarthContiguousDurationReducerSpec")
            native_reducer_base_cls = getattr(self._streaming_ns, "NativeReducerSpec")

            def _threshold_ref_from_name(name: str | None) -> Any:
                lowered = (name or "center_margin").strip().lower()
                if lowered == "center_margin":
                    return threshold_ref_enum_cls.CenterMargin
                if lowered == "lower_limb_margin":
                    return threshold_ref_enum_cls.LowerLimbMargin
                if lowered == "upper_limb_margin":
                    return threshold_ref_enum_cls.UpperLimbMargin
                raise ValueError(f"Unsupported threshold reference: {name!r}")

            def _duration_unit_from_name(name: str | None) -> Any:
                lowered = (name or "hours").strip().lower()
                if lowered == "hours":
                    return duration_unit_enum_cls.Hours
                if lowered == "samples":
                    return duration_unit_enum_cls.Samples
                raise ValueError(f"Unsupported duration unit: {name!r}")

            def _output_type_from_name(name: str | None) -> Any:
                lowered = (name or "float32").strip().lower()
                if lowered == "float32":
                    return reducer_output_type_enum_cls.Float32
                if lowered == "uint8":
                    return reducer_output_type_enum_cls.UInt8
                if lowered == "uint16":
                    return reducer_output_type_enum_cls.UInt16
                raise ValueError(f"Unsupported reduced output type: {name!r}")

            def _build_margin_predicate(payload: dict[str, Any] | None) -> Any | None:
                if not payload:
                    return None
                radius_override = payload.get("body_radius_deg_override", float("nan"))
                if radius_override is None:
                    radius_override = float("nan")
                return threshold_predicate_cls(
                    _signal_kind_from_name(str(payload.get("signal", ""))),
                    _threshold_ref_from_name(payload.get("reference")),
                    float(payload.get("threshold_value", 0.0)),
                    bool(payload.get("greater_than_or_equal", True)),
                    float(radius_override),
                )

            def _build_sun_predicate(payload: dict[str, Any] | None) -> Any | None:
                if not payload:
                    return None
                return sun_predicate_cls(
                    int(payload.get("min_sun_fraction_u8", 1)),
                    bool(payload.get("greater_than_or_equal", True)),
                )

            for reducer in (request.reducers or []):
                if not isinstance(reducer, dict):
                    raise ValueError("V2 reducers entries must be dict objects.")
                kind = str(reducer.get("kind", "")).strip().lower()
                output_type = _output_type_from_name(reducer.get("output_type"))
                if kind == "average_sun_fraction":
                    reducers_py.append(
                        avg_reducer_cls(
                            bool(reducer.get("output_normalized_01", True)),
                            output_type,
                        )
                    )
                elif kind == "cumulative_duration_where":
                    reducers_py.append(
                        cumulative_reducer_cls(
                            _build_margin_predicate(reducer.get("margin_predicate")),
                            _build_sun_predicate(reducer.get("sun_predicate")),
                            _duration_unit_from_name(reducer.get("unit")),
                            output_type,
                        )
                    )
                elif kind == "max_contiguous_duration_where":
                    reducers_py.append(
                        max_contig_reducer_cls(
                            _build_margin_predicate(reducer.get("margin_predicate")),
                            _build_sun_predicate(reducer.get("sun_predicate")),
                            _duration_unit_from_name(reducer.get("unit")),
                            output_type,
                        )
                    )
                elif kind == "combined_sun_earth_contiguous_duration":
                    reducers_py.append(
                        combined_reducer_cls(
                            _build_sun_predicate(reducer.get("sun_predicate")),
                            _build_margin_predicate(reducer.get("earth_margin_predicate")),
                            _duration_unit_from_name(reducer.get("unit")),
                            output_type,
                        )
                    )
                else:
                    raise ValueError(f"Unsupported V2 reducer kind: {kind!r}")

            try:
                from System import Array

                dotnet_reducers = (
                    Array[native_reducer_base_cls](reducers_py) if reducers_py else None
                )
            except Exception:
                dotnet_reducers = reducers_py or None
        else:
            dotnet_reducers = None

        dotnet_surrounding_dems = _to_dotnet_string_list(
            [Path(path).resolve() for path in request.surrounding_dem_paths]
        )

        args = (
            str(Path(request.scenario_root_dir).resolve()),
            str(Path(request.dem_path).resolve()),
            dotnet_surrounding_dems,
            str(Path(request.horizon_dir).resolve()),
            _to_dotnet_datetime_utc(request.start_utc),
            _to_dotnet_datetime_utc(request.stop_utc),
            float(request.time_step_hours),
            _to_dotnet_single(request.observer_elevation_meters),
            int(request.patch_width),
            int(request.patch_height),
            int(request.max_read_parallelism),
            int(request.max_compute_parallelism),
            int(request.ready_queue_capacity),
            bool(request.use_spice_sun_vectors),
            dotnet_mode,
            dotnet_signal_specs if mode_name == "signal_stream" else None,
            dotnet_layout,
            dotnet_reducers,
            bool(request.use_spice_earth_vectors),
        )
        return request_cls(*args)

    def start(self, request: LightmapStreamRequestPy) -> str:
        start_method = getattr(self._bridge, "StartLightmapArrayStreaming", None)
        if start_method is None:
            start_method = getattr(self._bridge, "StartLightmapStreaming")
        if self._streaming_ns is None:
            return str(start_method(request))
        dotnet_request = self._build_dotnet_request(request)
        return str(start_method(dotnet_request))

    def start_v2(self, request: LightmapStreamRequestV2Py) -> str:
        start_method = getattr(self._bridge, "StartLightmapArrayStreamingV2", None)
        if start_method is None:
            raise RuntimeError("Bridge does not support StartLightmapArrayStreamingV2.")
        if self._streaming_ns is None:
            return str(start_method(request))
        dotnet_request = self._build_dotnet_request_v2(request)
        return str(start_method(dotnet_request))

    def register_buffer(self, job_id: str, buffer_id: int, arr: np.ndarray) -> bool:
        if arr.dtype != np.uint8:
            raise ValueError("Output buffer must use dtype=np.uint8.")
        if arr.ndim != 3:
            raise ValueError("Output buffer must use shape (time_count, height, width).")
        if not arr.flags["C_CONTIGUOUS"]:
            raise ValueError("Output buffer must be C-contiguous.")
        ptr = int(arr.ctypes.data)
        return bool(
            self._bridge.RegisterOutputBuffer(
                str(job_id), int(buffer_id), ptr, int(arr.nbytes)
            )
        )

    def register_buffer_v2(self, job_id: str, buffer_id: int, arr: np.ndarray) -> bool:
        if arr.dtype not in (np.uint8, np.float32):
            raise ValueError("V2 output buffer must use dtype=np.uint8 or dtype=np.float32.")
        if arr.ndim not in (3, 4):
            raise ValueError("V2 output buffer must use shape (t,c,h,w) or (c,h,w).")
        if not arr.flags["C_CONTIGUOUS"]:
            raise ValueError("Output buffer must be C-contiguous.")
        ptr = int(arr.ctypes.data)
        method = getattr(self._bridge, "RegisterOutputBufferV2", None)
        if method is None:
            raise RuntimeError("Bridge does not support RegisterOutputBufferV2.")
        return bool(method(str(job_id), int(buffer_id), ptr, int(arr.nbytes)))

    def poll_next_tile(self, job_id: str, timeout_ms: int) -> StreamTileMetaPy | None:
        raw = self._bridge.TryGetNextTile(str(job_id), int(timeout_ms))
        if raw is None:
            return None
        return StreamTileMetaPy(
            job_id=str(raw.JobId),
            tile_id=int(raw.TileId),
            buffer_id=int(raw.BufferId),
            patch_row=int(raw.PatchRow),
            patch_col=int(raw.PatchCol),
            time_count=int(raw.TimeCount),
            width=int(raw.Width),
            height=int(raw.Height),
            state=_to_state_name(raw.State),
            message=None if raw.Message is None else str(raw.Message),
        )

    def poll_next_tile_v2(self, job_id: str, timeout_ms: int) -> StreamTileMetaV2Py | None:
        method = getattr(self._bridge, "TryGetNextTileV2", None)
        if method is None:
            raise RuntimeError("Bridge does not support TryGetNextTileV2.")
        raw = method(str(job_id), int(timeout_ms))
        if raw is None:
            return None
        return StreamTileMetaV2Py(
            job_id=str(raw.JobId),
            tile_id=int(raw.TileId),
            buffer_id=int(raw.BufferId),
            patch_row=int(raw.PatchRow),
            patch_col=int(raw.PatchCol),
            width=int(raw.Width),
            height=int(raw.Height),
            state=_to_state_name(raw.State),
            scalar_type=_to_state_name(raw.ScalarType),
            rank=int(raw.Rank),
            dims=(int(raw.Dim0), int(raw.Dim1), int(raw.Dim2), int(raw.Dim3)),
            time_offset=int(raw.TimeOffset),
            time_count=int(raw.TimeCount),
            channel_count=int(raw.ChannelCount),
            message=None if raw.Message is None else str(raw.Message),
        )

    def release_buffer(self, job_id: str, buffer_id: int) -> bool:
        return bool(self._bridge.ReleaseBuffer(str(job_id), int(buffer_id)))

    def get_status(self, job_id: str) -> LightmapStreamStatusPy:
        raw = self._bridge.GetJobStatus(str(job_id))
        return LightmapStreamStatusPy(
            job_id=str(raw.JobId),
            state=_to_state_name(raw.State),
            progress01=float(raw.Progress01),
            tiles_produced=int(raw.TilesProduced),
            tiles_consumed=int(raw.TilesConsumed),
            ready_queue_depth=int(raw.ReadyQueueDepth),
            free_buffer_count=int(raw.FreeBufferCount),
            message=None if raw.Message is None else str(raw.Message),
        )

    def cancel(self, job_id: str) -> bool:
        return bool(self._bridge.CancelJob(str(job_id)))

    def dispose(self, job_id: str) -> bool:
        return bool(self._bridge.DisposeJob(str(job_id)))


def stream_tiles(
    client: LightmapStreamingClient,
    request: LightmapStreamRequestPy,
    *,
    buffer_count: int = 8,
    poll_timeout_ms: int = 250,
) -> Iterator[tuple[StreamTileMetaPy, np.ndarray]]:
    if buffer_count < 1:
        raise ValueError("buffer_count must be >= 1.")

    time_count, height, width = request.tile_shape()
    buffers = {
        idx: np.zeros((time_count, height, width), dtype=np.uint8)
        for idx in range(buffer_count)
    }

    job_id = ""
    try:
        job_id = client.start(request)
        for buffer_id, arr in buffers.items():
            if not client.register_buffer(job_id, buffer_id, arr):
                raise RuntimeError(
                    f"Failed to register output buffer {buffer_id} for job {job_id}."
                )

        while True:
            tile = client.poll_next_tile(job_id, poll_timeout_ms)
            if tile is None:
                continue

            state = tile.state.lower()
            if state == "terminal":
                break
            if state == "error":
                raise RuntimeError(tile.message or "Streaming tile reported an error.")

            if tile.buffer_id not in buffers:
                raise RuntimeError(
                    f"Tile referenced unknown buffer id {tile.buffer_id} for job {job_id}."
                )

            yield tile, buffers[tile.buffer_id]
            if not client.release_buffer(job_id, tile.buffer_id):
                raise RuntimeError(
                    f"Failed to release buffer {tile.buffer_id} for job {job_id}."
                )
    finally:
        if job_id:
            try:
                client.cancel(job_id)
            except Exception:
                pass
            try:
                client.dispose(job_id)
            except Exception:
                pass


def stream_tiles_v2(
    client: LightmapStreamingClient,
    request: LightmapStreamRequestV2Py,
    *,
    buffer_count: int = 8,
    poll_timeout_ms: int = 250,
) -> Iterator[tuple[StreamTileMetaV2Py, np.ndarray]]:
    if buffer_count < 1:
        raise ValueError("buffer_count must be >= 1.")

    mode = str(request.mode).strip().lower()
    if mode == "signal_stream":
        dtype = np.uint8 if request.scalar_type() == "uint8" else np.float32
        buffer_shape = request.signal_stream_buffer_shape()
    elif mode == "native_reduce":
        dtype = np.float32
        buffer_shape = request.native_reduce_buffer_shape()
    else:
        raise ValueError(f"Unsupported V2 mode: {request.mode!r}")

    buffers = {idx: np.zeros(buffer_shape, dtype=dtype) for idx in range(buffer_count)}

    job_id = ""
    try:
        job_id = client.start_v2(request)
        for buffer_id, arr in buffers.items():
            if not client.register_buffer_v2(job_id, buffer_id, arr):
                raise RuntimeError(
                    f"Failed to register V2 output buffer {buffer_id} for job {job_id}."
                )

        while True:
            tile = client.poll_next_tile_v2(job_id, poll_timeout_ms)
            if tile is None:
                continue

            state = tile.state.lower()
            if state == "terminal":
                break
            if state == "error":
                raise RuntimeError(tile.message or "Streaming V2 tile reported an error.")

            if tile.buffer_id not in buffers:
                raise RuntimeError(
                    f"V2 tile referenced unknown buffer id {tile.buffer_id} for job {job_id}."
                )

            arr = buffers[tile.buffer_id]
            if tile.rank == 4:
                d0, d1, d2, d3 = tile.dims
                view = arr[:d0, :d1, :d2, :d3]
            elif tile.rank == 3:
                d0, d1, d2, _ = tile.dims
                view = arr[:d0, :d1, :d2]
            else:
                raise RuntimeError(f"Unsupported V2 tile rank: {tile.rank}")

            yield tile, view
            if not client.release_buffer(job_id, tile.buffer_id):
                raise RuntimeError(
                    f"Failed to release V2 buffer {tile.buffer_id} for job {job_id}."
                )
    finally:
        if job_id:
            try:
                client.cancel(job_id)
            except Exception:
                pass
            try:
                client.dispose(job_id)
            except Exception:
                pass
