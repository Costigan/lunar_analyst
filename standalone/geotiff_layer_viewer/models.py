from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import bisect
import re
import uuid

_TIMESTAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2}(?:T|-)\d{2}-\d{2}-\d{2})")


def parse_timestamp_from_name(path: str | Path) -> datetime | None:
    name = Path(path).name
    match = _TIMESTAMP_RE.search(name)
    if not match:
        return None
    token = match.group(1)
    for fmt in ("%Y-%m-%dT%H-%M-%S", "%Y-%m-%d-%H-%M-%S"):
        try:
            return datetime.strptime(token, fmt)
        except ValueError:
            continue
    return None


def infer_series_prefix(path: str | Path) -> str:
    name = Path(path).name
    match = _TIMESTAMP_RE.search(name)
    if not match:
        return Path(path).stem
    return name[: match.start()]


@dataclass(frozen=True)
class RasterSignature:
    width: int
    height: int
    count: int


@dataclass(frozen=True)
class TimeSeriesFrame:
    timestamp: datetime
    path: str


@dataclass
class LayerBase:
    layer_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    opacity: float = 1.0
    visible: bool = True


@dataclass
class SingleImageLayer(LayerBase):
    path: str = ""


@dataclass
class TimeSeriesLayer(LayerBase):
    series_name: str = ""
    frames: list[TimeSeriesFrame] = field(default_factory=list)

    def sorted_timestamps(self) -> list[datetime]:
        return [f.timestamp for f in self.frames]

    def frame_at_or_before(self, target: datetime) -> TimeSeriesFrame | None:
        ts = self.sorted_timestamps()
        idx = bisect.bisect_right(ts, target) - 1
        if idx < 0:
            return None
        return self.frames[idx]


Layer = SingleImageLayer | TimeSeriesLayer


def build_timeseries_from_paths(paths: list[str]) -> tuple[str, list[TimeSeriesFrame], list[str]]:
    valid: list[TimeSeriesFrame] = []
    skipped: list[str] = []
    for p in paths:
        dt = parse_timestamp_from_name(p)
        if dt is None:
            skipped.append(p)
            continue
        valid.append(TimeSeriesFrame(timestamp=dt, path=str(Path(p).resolve())))
    valid.sort(key=lambda f: f.timestamp)
    if valid:
        series_name = infer_series_prefix(valid[0].path) or Path(valid[0].path).stem
    else:
        series_name = "time_series"
    return series_name, valid, skipped
