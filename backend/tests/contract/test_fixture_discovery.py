from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.tools.fixture_discovery import discover_fixture_manifests, load_fixture_manifest


def test_discover_fixture_manifests_recursive(tmp_path: Path) -> None:
    a = tmp_path / "fixtures" / "dem" / "small_dem.fixture.json"
    b = tmp_path / "fixtures" / "vector" / "craters.fixture.json"
    a.parent.mkdir(parents=True, exist_ok=True)
    b.parent.mkdir(parents=True, exist_ok=True)
    a.write_text("{}", encoding="utf-8")
    b.write_text("{}", encoding="utf-8")

    discovered = discover_fixture_manifests(tmp_path / "fixtures")
    assert discovered == sorted([a, b])


def test_load_fixture_manifest_validates_baseline_metadata(tmp_path: Path) -> None:
    manifest_path = tmp_path / "demo.fixture.json"
    manifest_path.write_text(
        json.dumps(
            {
                "fixture_id": "demo_dem_001",
                "category": "dem",
                "source_path": "fixtures/dem/demo_dem.tif",
                "expected": {"crs": "ESRI:103878", "width": 256, "height": 256},
            }
        ),
        encoding="utf-8",
    )

    manifest = load_fixture_manifest(manifest_path)
    assert manifest.fixture_id == "demo_dem_001"
    assert manifest.category == "dem"
    assert manifest.expected["crs"] == "ESRI:103878"


def test_load_fixture_manifest_rejects_invalid_metadata(tmp_path: Path) -> None:
    manifest_path = tmp_path / "bad.fixture.json"
    manifest_path.write_text(
        json.dumps(
            {
                "fixture_id": "BAD",
                "category": "unknown",
                "source_path": "fixtures/unknown/file.bin",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_fixture_manifest(manifest_path)
