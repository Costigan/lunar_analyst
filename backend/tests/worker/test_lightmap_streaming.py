from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backend.worker.lightmap_streaming import LightmapStreamRequestPy
from backend.worker.lightmap_streaming import LightmapStreamRequestV2Py
from backend.worker.lightmap_streaming import LightmapStreamingClient
from backend.worker.lightmap_streaming import TemporalSignalSpecPy
from backend.worker.lightmap_streaming import stream_tiles
from backend.worker.lightmap_streaming import stream_tiles_v2


class _FakeBridge:
    def __init__(self) -> None:
        self.register_calls: list[tuple[str, int, int, int]] = []
        self.release_calls: list[tuple[str, int]] = []
        self.cancel_calls: list[str] = []
        self.dispose_calls: list[str] = []
        self.poll_items: list[object | None] = []
        self.status = SimpleNamespace(
            JobId="job-1",
            State=SimpleNamespace(ToString=lambda: "Running"),
            Progress01=0.5,
            TilesProduced=2,
            TilesConsumed=1,
            ReadyQueueDepth=1,
            FreeBufferCount=3,
            Message="ok",
        )

    def StartLightmapStreaming(self, _request: object) -> str:
        return "job-1"

    def StartLightmapArrayStreamingV2(self, _request: object) -> str:
        return "job-1"

    def RegisterOutputBuffer(
        self,
        job_id: str,
        buffer_id: int,
        ptr: int,
        byte_length: int,
    ) -> bool:
        self.register_calls.append((job_id, buffer_id, ptr, byte_length))
        return True

    def RegisterOutputBufferV2(
        self,
        job_id: str,
        buffer_id: int,
        ptr: int,
        byte_length: int,
    ) -> bool:
        self.register_calls.append((job_id, buffer_id, ptr, byte_length))
        return True

    def TryGetNextTile(self, _job_id: str, _timeout_ms: int) -> object | None:
        if not self.poll_items:
            return None
        return self.poll_items.pop(0)

    def TryGetNextTileV2(self, _job_id: str, _timeout_ms: int) -> object | None:
        if not self.poll_items:
            return None
        return self.poll_items.pop(0)

    def ReleaseBuffer(self, job_id: str, buffer_id: int) -> bool:
        self.release_calls.append((job_id, buffer_id))
        return True

    def GetJobStatus(self, _job_id: str) -> object:
        return self.status

    def CancelJob(self, job_id: str) -> bool:
        self.cancel_calls.append(job_id)
        return True

    def DisposeJob(self, job_id: str) -> bool:
        self.dispose_calls.append(job_id)
        return True


def _make_request(**overrides: object) -> LightmapStreamRequestPy:
    kwargs = {
        "scenario_root_dir": Path("/d/tmp/scenario"),
        "dem_path": Path("/d/tmp/scenario/dem.tif"),
        "surrounding_dem_paths": [],
        "horizon_dir": Path("/d/tmp/scenario/horizons"),
        "start_utc": "2024-01-01T00:00:00Z",
        "stop_utc": "2024-01-01T00:00:00Z",
        "time_step_hours": 1.0,
        "observer_elevation_meters": 0.0,
        "patch_width": 4,
        "patch_height": 3,
    }
    kwargs.update(overrides)
    return LightmapStreamRequestPy(**kwargs)


def _make_request_v2(**overrides: object) -> LightmapStreamRequestV2Py:
    kwargs = {
        "scenario_root_dir": Path("/d/tmp/scenario"),
        "dem_path": Path("/d/tmp/scenario/dem.tif"),
        "surrounding_dem_paths": [],
        "horizon_dir": Path("/d/tmp/scenario/horizons"),
        "start_utc": "2024-01-01T00:00:00Z",
        "stop_utc": "2024-01-01T03:00:00Z",
        "time_step_hours": 1.0,
        "observer_elevation_meters": 0.0,
        "patch_width": 4,
        "patch_height": 3,
        "chunk_time_count": 2,
        "signals": [TemporalSignalSpecPy(signal="sun_fraction_u8")],
    }
    kwargs.update(overrides)
    return LightmapStreamRequestV2Py(**kwargs)


def test_request_time_count_parses_iso_and_validates_range() -> None:
    req = _make_request(stop_utc="2024-01-01T03:00:00Z")
    assert req.time_count() == 4

    bad = _make_request(
        start_utc="2024-01-02T00:00:00Z",
        stop_utc="2024-01-01T00:00:00Z",
    )
    with pytest.raises(ValueError, match="stop_utc"):
        bad.time_count()


def test_register_buffer_validates_dtype_and_contiguous() -> None:
    client = LightmapStreamingClient(bridge=_FakeBridge())
    bad_dtype = np.zeros((1, 2, 2), dtype=np.float32)
    with pytest.raises(ValueError, match="dtype=np.uint8"):
        client.register_buffer("job-1", 1, bad_dtype)

    bad_contiguous = np.zeros((1, 2, 2), dtype=np.uint8)[:, :, ::-1]
    with pytest.raises(ValueError, match="C-contiguous"):
        client.register_buffer("job-1", 1, bad_contiguous)


def test_register_buffer_forwards_pointer_and_size() -> None:
    bridge = _FakeBridge()
    client = LightmapStreamingClient(bridge=bridge)
    arr = np.zeros((1, 2, 3), dtype=np.uint8)
    ok = client.register_buffer("job-1", 7, arr)
    assert ok is True
    assert bridge.register_calls == [("job-1", 7, int(arr.ctypes.data), arr.nbytes)]


def test_register_buffer_v2_validates_dtype_and_rank() -> None:
    client = LightmapStreamingClient(bridge=_FakeBridge())
    bad_dtype = np.zeros((1, 1, 2, 2), dtype=np.int16)
    with pytest.raises(ValueError, match="dtype=np.uint8 or dtype=np.float32"):
        client.register_buffer_v2("job-1", 1, bad_dtype)

    bad_rank = np.zeros((2, 2), dtype=np.uint8)
    with pytest.raises(ValueError, match="shape"):
        client.register_buffer_v2("job-1", 1, bad_rank)


def test_poll_and_status_mapping() -> None:
    bridge = _FakeBridge()
    bridge.poll_items = [
        SimpleNamespace(
            JobId="job-1",
            TileId=12,
            BufferId=3,
            PatchRow=256,
            PatchCol=128,
            TimeCount=5,
            Width=128,
            Height=128,
            State=SimpleNamespace(ToString=lambda: "Ready"),
            Message=None,
        )
    ]
    client = LightmapStreamingClient(bridge=bridge)
    tile = client.poll_next_tile("job-1", 250)
    assert tile is not None
    assert tile.tile_id == 12
    assert tile.buffer_id == 3
    assert tile.state == "Ready"
    assert tile.message is None

    status = client.get_status("job-1")
    assert status.state == "Running"
    assert status.tiles_produced == 2
    assert status.tiles_consumed == 1


def test_stream_tiles_yields_ready_and_releases_buffer() -> None:
    bridge = _FakeBridge()
    bridge.poll_items = [
        SimpleNamespace(
            JobId="job-1",
            TileId=1,
            BufferId=0,
            PatchRow=0,
            PatchCol=0,
            TimeCount=1,
            Width=4,
            Height=3,
            State=SimpleNamespace(ToString=lambda: "Ready"),
            Message=None,
        ),
        SimpleNamespace(
            JobId="job-1",
            TileId=2,
            BufferId=-1,
            PatchRow=-1,
            PatchCol=-1,
            TimeCount=1,
            Width=4,
            Height=3,
            State=SimpleNamespace(ToString=lambda: "Terminal"),
            Message="done",
        ),
    ]
    client = LightmapStreamingClient(bridge=bridge)
    request = _make_request()

    observed = list(stream_tiles(client, request, buffer_count=1, poll_timeout_ms=1))

    assert len(observed) == 1
    meta, arr = observed[0]
    assert meta.tile_id == 1
    assert meta.buffer_id == 0
    assert arr.shape == (1, 3, 4)
    assert arr.dtype == np.uint8
    assert bridge.release_calls == [("job-1", 0)]
    assert bridge.cancel_calls == ["job-1"]
    assert bridge.dispose_calls == ["job-1"]
    assert bridge.register_calls[0][3] == arr.nbytes


def test_stream_tiles_v2_yields_chunked_signal_tiles_and_releases_buffer() -> None:
    bridge = _FakeBridge()
    bridge.poll_items = [
        SimpleNamespace(
            JobId="job-1",
            TileId=10,
            BufferId=0,
            PatchRow=0,
            PatchCol=0,
            Width=4,
            Height=3,
            State=SimpleNamespace(ToString=lambda: "Ready"),
            ScalarType=SimpleNamespace(ToString=lambda: "UInt8"),
            Rank=4,
            Dim0=2,
            Dim1=1,
            Dim2=3,
            Dim3=4,
            TimeOffset=0,
            TimeCount=2,
            ChannelCount=1,
            Message=None,
        ),
        SimpleNamespace(
            JobId="job-1",
            TileId=11,
            BufferId=0,
            PatchRow=0,
            PatchCol=0,
            Width=4,
            Height=3,
            State=SimpleNamespace(ToString=lambda: "Ready"),
            ScalarType=SimpleNamespace(ToString=lambda: "UInt8"),
            Rank=4,
            Dim0=2,
            Dim1=1,
            Dim2=3,
            Dim3=4,
            TimeOffset=2,
            TimeCount=2,
            ChannelCount=1,
            Message=None,
        ),
        SimpleNamespace(
            JobId="job-1",
            TileId=12,
            BufferId=-1,
            PatchRow=-1,
            PatchCol=-1,
            Width=4,
            Height=3,
            State=SimpleNamespace(ToString=lambda: "Terminal"),
            ScalarType=SimpleNamespace(ToString=lambda: "UInt8"),
            Rank=4,
            Dim0=0,
            Dim1=1,
            Dim2=3,
            Dim3=4,
            TimeOffset=0,
            TimeCount=0,
            ChannelCount=1,
            Message="done",
        ),
    ]
    client = LightmapStreamingClient(bridge=bridge)
    request = _make_request_v2()

    observed = list(stream_tiles_v2(client, request, buffer_count=1, poll_timeout_ms=1))

    assert len(observed) == 2
    meta0, arr0 = observed[0]
    meta1, arr1 = observed[1]
    assert meta0.time_offset == 0
    assert meta1.time_offset == 2
    assert meta0.scalar_type == "UInt8"
    assert arr0.shape == (2, 1, 3, 4)
    assert arr1.shape == (2, 1, 3, 4)
    assert arr0.dtype == np.uint8
    assert bridge.release_calls == [("job-1", 0), ("job-1", 0)]


def test_stream_tiles_v2_float32_signal_request_uses_float32_buffers() -> None:
    bridge = _FakeBridge()
    bridge.poll_items = [
        SimpleNamespace(
            JobId="job-1",
            TileId=20,
            BufferId=0,
            PatchRow=0,
            PatchCol=0,
            Width=4,
            Height=3,
            State=SimpleNamespace(ToString=lambda: "Ready"),
            ScalarType=SimpleNamespace(ToString=lambda: "Float32"),
            Rank=4,
            Dim0=2,
            Dim1=2,
            Dim2=3,
            Dim3=4,
            TimeOffset=0,
            TimeCount=2,
            ChannelCount=2,
            Message=None,
        ),
        SimpleNamespace(
            JobId="job-1",
            TileId=21,
            BufferId=-1,
            PatchRow=-1,
            PatchCol=-1,
            Width=4,
            Height=3,
            State=SimpleNamespace(ToString=lambda: "Terminal"),
            ScalarType=SimpleNamespace(ToString=lambda: "Float32"),
            Rank=4,
            Dim0=0,
            Dim1=2,
            Dim2=3,
            Dim3=4,
            TimeOffset=0,
            TimeCount=0,
            ChannelCount=2,
            Message="done",
        ),
    ]
    client = LightmapStreamingClient(bridge=bridge)
    request = _make_request_v2(
        signals=[
            TemporalSignalSpecPy(signal="sun_fraction_u8"),
            TemporalSignalSpecPy(signal="earth_center_margin_deg_f32"),
        ]
    )

    observed = list(stream_tiles_v2(client, request, buffer_count=1, poll_timeout_ms=1))

    assert len(observed) == 1
    meta, arr = observed[0]
    assert request.scalar_type() == "float32"
    assert request.signal_stream_buffer_shape() == (2, 2, 3, 4)
    assert meta.scalar_type == "Float32"
    assert arr.shape == (2, 2, 3, 4)
    assert arr.dtype == np.float32


def test_stream_tiles_v2_native_reduce_allocates_rank3_buffer_by_reducer_count() -> None:
    bridge = _FakeBridge()
    bridge.poll_items = [
        SimpleNamespace(
            JobId="job-1",
            TileId=30,
            BufferId=0,
            PatchRow=0,
            PatchCol=0,
            Width=4,
            Height=3,
            State=SimpleNamespace(ToString=lambda: "Ready"),
            ScalarType=SimpleNamespace(ToString=lambda: "Float32"),
            Rank=3,
            Dim0=2,
            Dim1=3,
            Dim2=4,
            Dim3=1,
            TimeOffset=0,
            TimeCount=0,
            ChannelCount=2,
            Message=None,
        ),
        SimpleNamespace(
            JobId="job-1",
            TileId=31,
            BufferId=-1,
            PatchRow=-1,
            PatchCol=-1,
            Width=4,
            Height=3,
            State=SimpleNamespace(ToString=lambda: "Terminal"),
            ScalarType=SimpleNamespace(ToString=lambda: "Float32"),
            Rank=3,
            Dim0=0,
            Dim1=3,
            Dim2=4,
            Dim3=1,
            TimeOffset=0,
            TimeCount=0,
            ChannelCount=2,
            Message="done",
        ),
    ]
    client = LightmapStreamingClient(bridge=bridge)
    request = _make_request_v2(
        mode="native_reduce",
        signals=None,
        reducers=[
            {"kind": "average_sun_fraction"},
            {"kind": "cumulative_duration_where", "sun_predicate": {"min_sun_fraction_u8": 1}},
        ],
    )

    observed = list(stream_tiles_v2(client, request, buffer_count=1, poll_timeout_ms=1))

    assert len(observed) == 1
    meta, arr = observed[0]
    assert request.native_reduce_channel_count() == 2
    assert request.native_reduce_buffer_shape() == (2, 3, 4)
    assert meta.rank == 3
    assert arr.shape == (2, 3, 4)
    assert arr.dtype == np.float32
