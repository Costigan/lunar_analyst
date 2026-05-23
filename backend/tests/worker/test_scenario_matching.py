from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import backend.services.assistant.tool_registry as tool_registry
from backend.services.assistant.tool_registry import _match_scenario


class _FootprintStub:
    def model_dump(self, *, mode: str = "json"):  # noqa: ANN001
        del mode
        return {"type": "Polygon", "coordinates": [[[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0], [-1.0, -1.0]]]}


@dataclass
class _ScenarioStub:
    scenario_id: str
    name: str
    scenario_root: str
    directory: str
    primary_dem_footprint: _FootprintStub
    primary_dem_path: str = ""


def _services_with_mons_malapert():  # noqa: ANN202
    scenarios = [
        _ScenarioStub(
            scenario_id="scn_mons-malapert",
            name="mons-malapert",
            scenario_root="mons-malapert",
            directory="/e/projects/lunar_analyst/scenarios/mons-malapert",
            primary_dem_footprint=_FootprintStub(),
        )
    ]
    return SimpleNamespace(scenario_service=SimpleNamespace(list_scenarios=lambda: scenarios))


def test_match_scenario_accepts_underscore_and_trailing_scenario() -> None:
    services = _services_with_mons_malapert()
    result = _match_scenario(services, "mons_malapert scenario")
    assert result["status"] == "selected"
    assert result["scenario"]["scenario_id"] == "scn_mons-malapert"


def test_match_scenario_accepts_space_separated_name() -> None:
    services = _services_with_mons_malapert()
    result = _match_scenario(services, "mons malapert")
    assert result["status"] == "selected"
    assert result["scenario"]["scenario_id"] == "scn_mons-malapert"


def test_match_scenario_uses_scenario_directory_for_relative_dem_path(monkeypatch) -> None:  # noqa: ANN001
    captured: dict[str, str] = {}

    def _fake_dem_extent(*, path_text: str, scenario_directory: str):  # noqa: ANN001, ANN202
        captured["path_text"] = path_text
        captured["scenario_directory"] = scenario_directory
        return [10.0, 20.0, 30.0, 40.0]

    monkeypatch.setattr(tool_registry, "_dem_extent_from_primary_dem_path", _fake_dem_extent)
    scenarios = [
        _ScenarioStub(
            scenario_id="scn_mons-mouton",
            name="mons-mouton",
            scenario_root="mons-mouton",
            directory="/e/lunar_analyst_scenarios/mons-mouton",
            primary_dem_path="dem.tif",
            primary_dem_footprint=_FootprintStub(),
        )
    ]
    services = SimpleNamespace(scenario_service=SimpleNamespace(list_scenarios=lambda: scenarios))

    result = _match_scenario(services, "mons-mouton")
    assert result["status"] == "selected"
    assert result["scenario"]["dem_extent"] == [10.0, 20.0, 30.0, 40.0]
    assert captured["path_text"] == "dem.tif"
    assert captured["scenario_directory"] == "/e/lunar_analyst_scenarios/mons-mouton"


def test_dem_extent_fallback_reprojects_to_esri_103878(monkeypatch) -> None:  # noqa: ANN001
    class _Bounds:
        left = -2680.5
        bottom = -2248.5
        right = 2311.5
        top = 2999.5

    class _Dataset:
        crs = "LOCAL_CRS"
        bounds = _Bounds()

        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001, ANN204
            return False

    class _TargetCrs:
        @staticmethod
        def equals(other):  # noqa: ANN001, ANN204
            return other == "ESRI_103878"

    class _FakeRasterio:
        class crs:  # noqa: N801
            class CRS:  # noqa: N801
                @staticmethod
                def from_wkt(_wkt):  # noqa: ANN001, ANN204
                    return _TargetCrs()

        class warp:  # noqa: N801
            @staticmethod
            def transform_bounds(src, dst, left, bottom, right, top, densify_pts=21):  # noqa: ANN001, ANN204
                assert src == "LOCAL_CRS"
                assert isinstance(dst, _TargetCrs)
                assert densify_pts == 21
                return (left + 1000.0, bottom + 1000.0, right + 1000.0, top + 1000.0)

        @staticmethod
        def open(_path):  # noqa: ANN001, ANN204
            return _Dataset()

    monkeypatch.setattr(
        "backend.worker.gdal_runtime.import_rasterio",
        lambda: _FakeRasterio(),
    )
    extent = tool_registry._dem_extent_from_primary_dem_path(
        path_text="dem.tif",
        scenario_directory="/e/lunar_analyst_scenarios/mons-mouton",
    )
    assert extent == [-1680.5, -1248.5, 3311.5, 3999.5]
