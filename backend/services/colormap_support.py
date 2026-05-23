from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np


BUILTIN_RULES_FILE = "default_colormap_rules.json"
RULES_FILE_NAME = "colormap_rules.json"


def builtin_colormaps() -> list[dict[str, Any]]:
    return [
        {
            "id": "gray",
            "name": "Grayscale",
            "mode": "continuous",
            "stops": [
                {"value": 0.0, "color": [0, 0, 0, 1]},
                {"value": 1.0, "color": [255, 255, 255, 1]},
            ],
        },
        {
            "id": "viridis",
            "name": "Viridis",
            "mode": "continuous",
            "stops": [
                {"value": 0.0, "color": [68, 1, 84, 1]},
                {"value": 0.25, "color": [59, 82, 139, 1]},
                {"value": 0.5, "color": [33, 145, 140, 1]},
                {"value": 0.75, "color": [94, 201, 97, 1]},
                {"value": 1.0, "color": [253, 231, 37, 1]},
            ],
        },
        {
            "id": "magma",
            "name": "Magma",
            "mode": "continuous",
            "stops": [
                {"value": 0.0, "color": [0, 0, 4, 1]},
                {"value": 0.25, "color": [81, 18, 124, 1]},
                {"value": 0.5, "color": [182, 55, 121, 1]},
                {"value": 0.75, "color": [251, 140, 60, 1]},
                {"value": 1.0, "color": [252, 253, 191, 1]},
            ],
        },
        {
            "id": "inferno",
            "name": "Inferno",
            "mode": "continuous",
            "stops": [
                {"value": 0.0, "color": [0, 0, 4, 1]},
                {"value": 0.25, "color": [87, 15, 109, 1]},
                {"value": 0.5, "color": [187, 55, 84, 1]},
                {"value": 0.75, "color": [249, 142, 8, 1]},
                {"value": 1.0, "color": [252, 255, 164, 1]},
            ],
        },
        {
            "id": "plasma",
            "name": "Plasma",
            "mode": "continuous",
            "stops": [
                {"value": 0.0, "color": [13, 8, 135, 1]},
                {"value": 0.25, "color": [126, 3, 168, 1]},
                {"value": 0.5, "color": [203, 71, 120, 1]},
                {"value": 0.75, "color": [248, 149, 64, 1]},
                {"value": 1.0, "color": [240, 249, 33, 1]},
            ],
        },
    ]


def _normalize_colormap_stop(raw: dict[str, Any]) -> dict[str, Any] | None:
    value = raw.get("value")
    color = raw.get("color")
    if not isinstance(value, (int, float)):
        return None
    if not isinstance(color, list) or len(color) not in (3, 4):
        return None
    if not all(isinstance(ch, (int, float)) for ch in color):
        return None
    return {
        "value": max(0.0, min(1.0, float(value))),
        "color": [
            max(0.0, min(255.0, float(color[0]))),
            max(0.0, min(255.0, float(color[1]))),
            max(0.0, min(255.0, float(color[2]))),
            max(0.0, min(1.0, float(color[3]) if len(color) == 4 else 1.0)),
        ],
    }


def normalize_colormap(raw: dict[str, Any]) -> dict[str, Any] | None:
    cmap_id = str(raw.get("id", "")).strip()
    name = str(raw.get("name", "")).strip()
    if not cmap_id or not name:
        return None

    mode = str(raw.get("mode", "continuous")).strip().lower() or "continuous"
    if mode not in {"continuous", "discrete", "threshold", "cyclic"}:
        mode = "continuous"

    stops = raw.get("stops")
    if not isinstance(stops, list) or len(stops) < 2:
        return None

    normalized_stops: list[dict[str, Any]] = []
    for stop in stops:
        if not isinstance(stop, dict):
            return None
        normalized = _normalize_colormap_stop(stop)
        if normalized is None:
            return None
        normalized_stops.append(normalized)

    normalized_stops.sort(key=lambda s: s["value"])
    if normalized_stops[0]["value"] != 0.0:
        normalized_stops.insert(0, {"value": 0.0, "color": normalized_stops[0]["color"]})
    if normalized_stops[-1]["value"] != 1.0:
        normalized_stops.append({"value": 1.0, "color": normalized_stops[-1]["color"]})

    payload: dict[str, Any] = {
        "id": cmap_id,
        "name": name,
        "mode": mode,
        "stops": normalized_stops,
    }
    if isinstance(raw.get("parameters"), list):
        payload["parameters"] = raw["parameters"]
    if isinstance(raw.get("cyclic"), dict):
        payload["cyclic"] = raw["cyclic"]
    return payload


def read_colormap_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries = parsed.get("colormaps") if isinstance(parsed, dict) else None
    if not isinstance(entries, list):
        return []
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        normalized = normalize_colormap(entry)
        if normalized is not None:
            result.append(normalized)
    return result


def _read_rule_file(path: Path, *, known_colormaps: set[str]) -> list[dict[str, str]]:
    if not path.exists() or not path.is_file():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = parsed.get("rules") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        return []

    rules: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pattern = str(row.get("pattern", "")).strip()
        colormap = str(row.get("colormap", "")).strip()
        if not pattern or not colormap:
            continue
        try:
            re.compile(pattern)
        except re.error:
            continue
        if colormap not in known_colormaps:
            continue
        rules.append({"pattern": pattern, "colormap": colormap})
    return rules


def resolve_colormap_registry(
    *,
    repo_root: Path,
    config_path: Path,
    map_cfg: dict[str, Any],
    scenario_root: Path | None,
) -> dict[str, Any]:
    app_path_raw = map_cfg.get("colormap_app_path", "colormaps/map_colormaps.json")
    app_path = (
        (config_path.parent / str(app_path_raw)).resolve()
        if isinstance(app_path_raw, str) and app_path_raw.strip() and not Path(str(app_path_raw)).is_absolute()
        else Path(str(app_path_raw)).expanduser().resolve()
        if isinstance(app_path_raw, str) and app_path_raw.strip()
        else (repo_root / "config" / "colormaps" / "map_colormaps.json").resolve()
    )

    root_path_raw = map_cfg.get("colormap_scenario_root_path", map_cfg.get("colormap_scenario_path"))
    local_path_raw = map_cfg.get("colormap_scenario_local_path")
    if scenario_root is None:
        scenario_root_path = (repo_root / "scenarios" / "default" / "colormaps" / "map_colormaps.json").resolve()
        scenario_local_path = (repo_root / "scenarios" / "default" / "colormaps" / "local" / "map_colormaps.json").resolve()
    else:
        scenario_root_path = (
            (config_path.parent / str(root_path_raw)).resolve()
            if isinstance(root_path_raw, str) and root_path_raw.strip() and not Path(str(root_path_raw)).is_absolute()
            else Path(str(root_path_raw)).expanduser().resolve()
            if isinstance(root_path_raw, str) and root_path_raw.strip()
            else (scenario_root / "colormaps" / "map_colormaps.json").resolve()
        )
        scenario_local_path = (
            (config_path.parent / str(local_path_raw)).resolve()
            if isinstance(local_path_raw, str) and local_path_raw.strip() and not Path(str(local_path_raw)).is_absolute()
            else Path(str(local_path_raw)).expanduser().resolve()
            if isinstance(local_path_raw, str) and local_path_raw.strip()
            else (scenario_root / "colormaps" / "local" / "map_colormaps.json").resolve()
        )

    merged: dict[str, dict[str, Any]] = {c["id"]: c for c in builtin_colormaps()}
    low_to_high = [
        ("app", app_path),
        ("scenario_root", scenario_root_path),
        ("scenario_local", scenario_local_path),
    ]
    for _kind, path in low_to_high:
        for cmap in read_colormap_file(path):
            merged[cmap["id"]] = cmap

    default_colormap = str(map_cfg.get("default_colormap", "gray")).strip() or "gray"
    if default_colormap not in merged:
        default_colormap = "gray" if "gray" in merged else sorted(merged.keys())[0]

    known_colormaps = set(merged.keys())
    built_in_rules_path = (repo_root / "config" / "colormaps" / BUILTIN_RULES_FILE).resolve()
    rule_files = [
        ("scenario_local", scenario_local_path.parent / RULES_FILE_NAME),
        ("scenario_root", scenario_root_path.parent / RULES_FILE_NAME),
        ("app", app_path.parent / RULES_FILE_NAME),
        ("builtin", built_in_rules_path),
    ]
    rules: list[dict[str, str]] = []
    for _kind, path in rule_files:
        rules.extend(_read_rule_file(path, known_colormaps=known_colormaps))

    ordered = sorted(merged.values(), key=lambda c: str(c.get("name", "")).lower())
    return {
        "default": default_colormap,
        "colormaps": ordered,
        "rules": rules,
        "sources": {
            "scenario_local": str(scenario_local_path),
            "scenario_root": str(scenario_root_path),
            "app": str(app_path),
            "builtin": "builtin",
        },
        "rule_sources": {
            "scenario_local": str((scenario_local_path.parent / RULES_FILE_NAME).resolve()),
            "scenario_root": str((scenario_root_path.parent / RULES_FILE_NAME).resolve()),
            "app": str((app_path.parent / RULES_FILE_NAME).resolve()),
            "builtin": str(built_in_rules_path),
        },
    }


def resolve_default_colormap_for_name(
    *,
    file_name: str,
    colormaps: list[dict[str, Any]],
    rules: list[dict[str, str]],
    fallback_default: str,
) -> tuple[str, str | None]:
    colormap_ids = {str(c.get("id", "")).strip() for c in colormaps}
    stem = Path(file_name).stem
    for rule in rules:
        pattern = str(rule.get("pattern", "")).strip()
        colormap = str(rule.get("colormap", "")).strip()
        if not pattern or not colormap:
            continue
        if colormap not in colormap_ids:
            continue
        try:
            if re.search(pattern, stem):
                return colormap, pattern
        except re.error:
            continue
    if fallback_default in colormap_ids:
        return fallback_default, None
    if "gray" in colormap_ids:
        return "gray", None
    return sorted(colormap_ids)[0], None


def tone_map_rgb(rgb: np.ndarray, brightness: float, contrast: float) -> np.ndarray:
    # rgb: (..., 3) in 0..1
    out = ((rgb - 0.5) * float(contrast)) + 0.5 + float(brightness)
    return np.clip(out, 0.0, 1.0)


def _sample_continuous(stops: list[dict[str, Any]], normalized: np.ndarray) -> np.ndarray:
    values = np.array([float(s["value"]) for s in stops], dtype=np.float32)
    colors = np.array([s["color"] for s in stops], dtype=np.float32)
    out = np.zeros((normalized.size, 4), dtype=np.float32)
    out[:, 0] = np.interp(normalized, values, colors[:, 0])
    out[:, 1] = np.interp(normalized, values, colors[:, 1])
    out[:, 2] = np.interp(normalized, values, colors[:, 2])
    out[:, 3] = np.interp(normalized, values, colors[:, 3])
    return out


def _sample_discrete(stops: list[dict[str, Any]], normalized: np.ndarray) -> np.ndarray:
    values = np.array([float(s["value"]) for s in stops], dtype=np.float32)
    colors = np.array([s["color"] for s in stops], dtype=np.float32)
    idx = np.searchsorted(values, normalized, side="right") - 1
    idx = np.clip(idx, 0, len(values) - 1)
    return colors[idx]


def sample_colormap_rgba(
    *,
    colormap: dict[str, Any],
    raw_values: np.ndarray,
    value_min: float,
    value_max: float,
) -> np.ndarray:
    mode = str(colormap.get("mode", "continuous")).strip().lower() or "continuous"
    stops = colormap.get("stops")
    if not isinstance(stops, list) or len(stops) < 2:
        raise ValueError("Invalid colormap stops")

    if mode == "cyclic":
        cyclic = colormap.get("cyclic") if isinstance(colormap.get("cyclic"), dict) else {}
        period = float(cyclic.get("period", 360.0) or 360.0)
        domain_min = float(cyclic.get("domain_min", 0.0) or 0.0)
        if period <= 0:
            period = 360.0
        normalized = np.mod(raw_values - domain_min, period) / period
    else:
        span = value_max - value_min
        if not np.isfinite(span) or span <= 0:
            normalized = np.zeros_like(raw_values, dtype=np.float32)
        else:
            normalized = np.clip((raw_values - value_min) / span, 0.0, 1.0)

    flat = normalized.reshape(-1).astype(np.float32)
    if mode in {"discrete", "threshold"}:
        return _sample_discrete(stops, flat).reshape((*raw_values.shape, 4))
    return _sample_continuous(stops, flat).reshape((*raw_values.shape, 4))


def contour_rgba(
    *,
    raw_values: np.ndarray,
    interval: float,
    offset: float,
    line_color: list[float],
    line_width_value: float,
) -> np.ndarray:
    if interval <= 0:
        raise ValueError("Contour interval must be > 0")
    distance = np.mod(raw_values - offset, interval)
    distance = np.minimum(distance, interval - distance)
    threshold = max(1e-6, line_width_value * 0.5)
    mask = distance <= threshold

    rgba = np.zeros((*raw_values.shape, 4), dtype=np.float32)
    rgba[..., 0] = float(line_color[0])
    rgba[..., 1] = float(line_color[1])
    rgba[..., 2] = float(line_color[2])
    rgba[..., 3] = float(line_color[3])
    rgba[~mask, 3] = 0.0
    return rgba
