from __future__ import annotations

from backend.services.assistant.tool_argument_repair import ToolArgumentRepairer


def test_argument_repair_normalizes_aliases_and_defaults() -> None:
    repairer = ToolArgumentRepairer(enabled=True)
    repaired, outcome = repairer.repair(
        tool_name="raster.calculate",
        arguments={"handler_name": "raster_calculate", "output_relative_path": "results\\mask.tif"},
        scenario_id="scn_1",
        schema={},
    )

    assert repaired["implementation_name"] == "raster_calculate"
    assert repaired["overwrite_mode"] == "ask"
    assert repaired["output_relative_path"] == "results/mask.tif"
    assert outcome.repair_applied is True
    assert outcome.repair_status == "revalidated"


def test_argument_repair_blocks_path_escape() -> None:
    repairer = ToolArgumentRepairer(enabled=True)
    repaired, outcome = repairer.repair(
        tool_name="scenario.write_script",
        arguments={"relative_path": "../escape.py"},
        scenario_id="scn_1",
        schema={},
    )

    assert repaired["relative_path"] == "../escape.py"
    assert outcome.repair_status == "blocked_requires_clarification"
    assert "policy_out_of_root_path" in outcome.repair_warning_codes


def test_argument_repair_defaults_overwrite_mode_for_raster_transform() -> None:
    repairer = ToolArgumentRepairer(enabled=True)
    repaired, outcome = repairer.repair(
        tool_name="raster.transform",
        arguments={"script": "result = dem", "inputs": {"dem": {"relative_path": "dem.tif"}}},
        scenario_id="scn_1",
        schema={},
    )

    assert repaired["overwrite_mode"] == "ask"
    assert outcome.repair_applied is True
