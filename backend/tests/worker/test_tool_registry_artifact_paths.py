from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services.assistant.tool_registry import _resolve_artifact_identity, _resolve_artifact_path


class _Stores:
    def __init__(self, *, workspace_root: Path, scenario_roots: dict[str, Path]) -> None:
        self.workspace_root = workspace_root
        self.scenario_roots = scenario_roots
        self.product_files: dict[str, object] = {}


class _Services:
    def __init__(self, stores: _Stores, *, scenario_service: object | None = None) -> None:
        self.stores = stores
        self.scenario_service = scenario_service


def test_resolve_artifact_path_allows_workspace_paths(tmp_path: Path) -> None:
    workspace_root = (tmp_path / "workspace").resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    candidate = (workspace_root / "outputs" / "artifact.tif").resolve()
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("data", encoding="utf-8")
    services = _Services(
        _Stores(workspace_root=workspace_root, scenario_roots={"scn_1": (workspace_root / "scn_1")})
    )

    resolved = _resolve_artifact_path(services, {"path": str(candidate)})

    assert resolved == candidate


def test_resolve_artifact_path_rejects_outside_workspace_and_scenario_roots(tmp_path: Path) -> None:
    workspace_root = (tmp_path / "workspace").resolve()
    scenario_root = (workspace_root / "scn_1").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    outside = (tmp_path / "outside.tif").resolve()
    outside.write_text("data", encoding="utf-8")
    services = _Services(
        _Stores(workspace_root=workspace_root, scenario_roots={"scn_1": scenario_root})
    )

    with pytest.raises(PermissionError):
        _resolve_artifact_path(services, {"path": str(outside)})


class _ScenarioService:
    def __init__(self, *, stores: _Stores, scenario_id: str, scenario_root: Path) -> None:
        self._stores = stores
        self._scenario_id = scenario_id
        self._scenario_root = scenario_root
        self.reconcile_calls = 0

    def get_scenario(self, scenario_id: str) -> SimpleNamespace:
        if scenario_id != self._scenario_id:
            raise KeyError(scenario_id)
        return SimpleNamespace(directory=str(self._scenario_root))

    def reconcile_scenario_filesystem(self, scenario_id: str, *, force: bool = False) -> bool:
        assert force is True
        if scenario_id != self._scenario_id:
            raise KeyError(scenario_id)
        self.reconcile_calls += 1
        self._stores.product_files["file_plot"] = SimpleNamespace(
            scenario_id=self._scenario_id,
            relative_path="slope_histogram.png",
        )
        return True


def test_resolve_artifact_identity_reconciles_scenario_file_for_generated_plot(tmp_path: Path) -> None:
    workspace_root = (tmp_path / "workspace").resolve()
    scenario_root = (workspace_root / "scn_1").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    candidate = (scenario_root / "slope_histogram.png").resolve()
    candidate.write_bytes(b"png")
    stores = _Stores(workspace_root=workspace_root, scenario_roots={"scn_1": scenario_root})
    scenario_service = _ScenarioService(stores=stores, scenario_id="scn_1", scenario_root=scenario_root)
    services = _Services(stores, scenario_service=scenario_service)

    resolved_path, file_id = _resolve_artifact_identity(
        services,
        {"scenario_id": "scn_1", "relative_path": "slope_histogram.png"},
    )

    assert resolved_path == candidate
    assert file_id == "file_plot"
    assert scenario_service.reconcile_calls == 1
