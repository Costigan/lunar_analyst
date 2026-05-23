from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from .models import SingleImageLayer, TimeSeriesFrame, TimeSeriesLayer, Layer


@dataclass
class AppState:
    layers: list[Layer]
    slider_time: datetime | None = None
    window_geometry_hex: str | None = None


def _layer_to_dict(layer: Layer) -> dict:
    if isinstance(layer, SingleImageLayer):
        return {
            "kind": "single",
            "layer_id": layer.layer_id,
            "name": layer.name,
            "opacity": layer.opacity,
            "visible": layer.visible,
            "path": layer.path,
        }
    return {
        "kind": "timeseries",
        "layer_id": layer.layer_id,
        "name": layer.name,
        "opacity": layer.opacity,
        "visible": layer.visible,
        "series_name": layer.series_name,
        "frames": [
            {"timestamp": f.timestamp.isoformat(), "path": f.path}
            for f in layer.frames
        ],
    }


def _layer_from_dict(data: dict) -> Layer:
    common = {
        "layer_id": data.get("layer_id", ""),
        "name": data.get("name", ""),
        "opacity": float(data.get("opacity", 1.0)),
        "visible": bool(data.get("visible", True)),
    }
    if data.get("kind") == "single":
        return SingleImageLayer(path=str(data.get("path", "")), **common)
    frames = [
        TimeSeriesFrame(timestamp=datetime.fromisoformat(f["timestamp"]), path=str(f["path"]))
        for f in data.get("frames", [])
    ]
    frames.sort(key=lambda x: x.timestamp)
    return TimeSeriesLayer(series_name=str(data.get("series_name", "")), frames=frames, **common)


def save_state(path: Path, state: AppState) -> None:
    payload = {
        "layers": [_layer_to_dict(l) for l in state.layers],
        "slider_time": state.slider_time.isoformat() if state.slider_time else None,
        "window_geometry_hex": state.window_geometry_hex,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_state(path: Path) -> AppState:
    if not path.exists():
        return AppState(layers=[])
    data = json.loads(path.read_text(encoding="utf-8"))
    layers = [_layer_from_dict(item) for item in data.get("layers", [])]
    slider_time = data.get("slider_time")
    return AppState(
        layers=layers,
        slider_time=datetime.fromisoformat(slider_time) if slider_time else None,
        window_geometry_hex=data.get("window_geometry_hex"),
    )
