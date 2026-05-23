from __future__ import annotations

from pathlib import Path

import numpy as np

from backend.services.colormap_support import (
    resolve_colormap_registry,
    resolve_default_colormap_for_name,
    sample_colormap_rgba,
    tone_map_rgb,
)


def test_colormap_registry_precedence_local_overrides_root_and_app(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    config_dir = repo_root / "config"
    (config_dir / "colormaps").mkdir(parents=True)
    config_path = config_dir / "lunar_analyst.toml"
    config_path.write_text("[backend.lunar_analyst]\n", encoding="utf-8")

    app = tmp_path / "app_colormaps.json"
    app.write_text(
        '{"colormaps":[{"id":"gray","name":"gray-app","stops":[{"value":0,"color":[0,0,0,1]},{"value":1,"color":[255,255,255,1]}]}]}',
        encoding="utf-8",
    )

    scenario_root = tmp_path / "scenario"
    (scenario_root / "colormaps" / "local").mkdir(parents=True)
    (scenario_root / "colormaps" / "map_colormaps.json").write_text(
        '{"colormaps":[{"id":"gray","name":"gray-root","stops":[{"value":0,"color":[0,0,0,1]},{"value":1,"color":[255,255,255,1]}]}]}',
        encoding="utf-8",
    )
    (scenario_root / "colormaps" / "local" / "map_colormaps.json").write_text(
        '{"colormaps":[{"id":"gray","name":"gray-local","stops":[{"value":0,"color":[0,0,0,1]},{"value":1,"color":[255,255,255,1]}]}]}',
        encoding="utf-8",
    )

    payload = resolve_colormap_registry(
        repo_root=repo_root,
        config_path=config_path,
        map_cfg={"colormap_app_path": str(app)},
        scenario_root=scenario_root,
    )
    by_id = {item["id"]: item for item in payload["colormaps"]}
    assert by_id["gray"]["name"] == "gray-local"


def test_default_colormap_rules_first_match_on_stem() -> None:
    colormap_id, matched = resolve_default_colormap_for_name(
        file_name="hazard_map.tif",
        colormaps=[
            {"id": "gray", "name": "Gray", "stops": [{"value": 0, "color": [0, 0, 0, 1]}, {"value": 1, "color": [255, 255, 255, 1]}]},
            {"id": "inferno", "name": "Inferno", "stops": [{"value": 0, "color": [0, 0, 0, 1]}, {"value": 1, "color": [255, 255, 255, 1]}]},
        ],
        rules=[
            {"pattern": "(?i)^hazard_", "colormap": "inferno"},
            {"pattern": ".*", "colormap": "gray"},
        ],
        fallback_default="gray",
    )
    assert colormap_id == "inferno"
    assert matched == "(?i)^hazard_"


def test_tone_math_is_post_colormap_formula() -> None:
    rgb = np.array([[[0.2, 0.4, 0.6]]], dtype=np.float32)
    out = tone_map_rgb(rgb, brightness=0.1, contrast=2.0)
    expected = np.clip(((rgb - 0.5) * 2.0) + 0.5 + 0.1, 0.0, 1.0)
    assert np.allclose(out, expected)


def test_sample_colormap_supports_cyclic() -> None:
    cmap = {
        "id": "phase",
        "name": "phase",
        "mode": "cyclic",
        "cyclic": {"period": 360.0, "domain_min": 0.0},
        "stops": [
            {"value": 0.0, "color": [255, 0, 0, 1]},
            {"value": 1.0, "color": [255, 0, 0, 1]},
        ],
    }
    rgba = sample_colormap_rgba(
        colormap=cmap,
        raw_values=np.array([[0.0, 360.0]], dtype=np.float32),
        value_min=0.0,
        value_max=1.0,
    )
    assert rgba.shape == (1, 2, 4)
    assert np.allclose(rgba[0, 0], rgba[0, 1])
