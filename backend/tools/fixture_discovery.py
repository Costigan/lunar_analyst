from __future__ import annotations

import json
from pathlib import Path

from backend.contracts.fixtures import FixtureManifest


FIXTURE_MANIFEST_SUFFIX = ".fixture.json"


def discover_fixture_manifests(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob(f"*{FIXTURE_MANIFEST_SUFFIX}")
        if path.is_file()
    )


def load_fixture_manifest(path: Path) -> FixtureManifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    return FixtureManifest.model_validate(data)
