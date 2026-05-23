from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services.assistant.tool_registry import _tool_scenario_import_geotiff


class _ImportResult:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = dict(payload)

    def model_dump(self, mode: str = "json"):  # noqa: ANN201, ARG002
        return dict(self._payload)


class _ScenarioService:
    def __init__(self, *, scenario_root: Path) -> None:
        self._scenario_root = scenario_root
        self.import_calls: list[tuple[str, object]] = []

    def resolve_scenario_file(self, scenario_id: str, relative_path: str) -> Path:  # noqa: ANN001
        candidate = (self._scenario_root / relative_path).resolve()
        if not candidate.exists():
            raise FileNotFoundError(relative_path)
        return candidate

    def reconcile_scenario_filesystem(self, scenario_id: str, force: bool = False) -> bool:  # noqa: ANN001
        assert force is True
        return True

    def import_geotiff(self, scenario_id: str, request):  # noqa: ANN001, ANN201
        self.import_calls.append((scenario_id, request))
        return _ImportResult({"scenario_id": scenario_id, "source_path": str(getattr(request, "source_path", ""))})


class _LayerService:
    def __init__(self) -> None:
        self.updated: list[tuple[str, object]] = []
        self.created: list[object] = []
        self._layers: list[object] = []

    def list_layers(self, scenario_id: str):  # noqa: ANN001, ANN201
        return list(self._layers)

    def update_layer(self, layer_id: str, req):  # noqa: ANN001, ANN201
        self.updated.append((layer_id, req))
        return _ImportResult({"layer_id": layer_id, "visible": bool(getattr(req, "visible", False))})

    def create_layer(self, req):  # noqa: ANN001, ANN201
        self.created.append(req)
        return _ImportResult({"layer_id": "layer_created", "visible": bool(getattr(req, "visible", False))})


def _services(*, scenario_root: Path):
    scenario_service = _ScenarioService(scenario_root=scenario_root)
    layer_service = _LayerService()
    stores = SimpleNamespace(
        product_files={},
        workspace_root=scenario_root.parent,
    )
    return SimpleNamespace(
        scenario_service=scenario_service,
        layer_service=layer_service,
        stores=stores,
    )


def test_tool_scenario_import_geotiff_resolves_relative_source_path_before_import(tmp_path: Path) -> None:
    scenario_root = (tmp_path / "scn").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    rel_file = scenario_root / "slope.tif"
    rel_file.write_bytes(b"data")
    services = _services(scenario_root=scenario_root)

    result = _tool_scenario_import_geotiff(
        services,
        {"scenario_id": "scn_1", "source_path": "slope.tif"},
    )

    assert services.scenario_service.import_calls
    _scenario_id, request = services.scenario_service.import_calls[-1]
    assert str(getattr(request, "source_path", "")) == str(rel_file.resolve())
    assert result["scenario_id"] == "scn_1"


def test_tool_scenario_import_geotiff_rejects_missing_relative_file(tmp_path: Path) -> None:
    scenario_root = (tmp_path / "scn").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    services = _services(scenario_root=scenario_root)

    with pytest.raises(ValueError, match="Scenario file not found"):
        _tool_scenario_import_geotiff(
            services,
            {"scenario_id": "scn_1", "source_path": "missing.tif"},
        )


def test_tool_scenario_import_geotiff_reuses_existing_layer_without_creation(tmp_path: Path) -> None:
    scenario_root = (tmp_path / "scn").resolve()
    scenario_root.mkdir(parents=True, exist_ok=True)
    rel_file = scenario_root / "slope.tif"
    rel_file.write_bytes(b"data")
    services = _services(scenario_root=scenario_root)
    services.stores.product_files["file_1"] = SimpleNamespace(
        file_id="file_1",
        product_id="prod_1",
        scenario_id="scn_1",
        relative_path="slope.tif",
    )
    services.layer_service._layers = [  # type: ignore[attr-defined]
        SimpleNamespace(
            layer_id="layer_existing",
            title="Slope",
            source_file_id="file_1",
            z_index=2,
        )
    ]

    result = _tool_scenario_import_geotiff(
        services,
        {"scenario_id": "scn_1", "source_path": "slope.tif"},
    )

    assert result["mode"] == "existing_file_layer_updated"
    assert services.layer_service.updated
    assert not services.layer_service.created
    assert not services.scenario_service.import_calls
