from __future__ import annotations

from backend.services.assistant.command_router import HybridCommandRouter, ScenarioCommandContext


def test_hybrid_router_plans_multi_intent_switch_then_turn_on_layer() -> None:
    router = HybridCommandRouter(enabled=True)
    plan = router.plan(
        prompt="Switch to test_scenario, then turn on slope.",
        scenario_id="scn_current",
    )
    assert plan.is_fully_matched is True
    assert [action.action_id for action in plan.actions] == [
        "scenario.switch",
        "layer.set_visible_by_name",
    ]
    assert plan.actions[0].steps[0].tool_name == "scenario.set_current"
    assert plan.actions[0].steps[0].arguments == {"scenario_ref": "test_scenario"}
    assert plan.actions[1].steps[0].tool_name == "layer.update_state"
    assert plan.actions[1].steps[0].arguments == {
        "layer_name": "slope",
        "visible": True,
    }


def test_hybrid_router_normalizes_switch_target_with_trailing_scenario_word() -> None:
    router = HybridCommandRouter(enabled=True)
    plan = router.plan(
        prompt="switch to the mons_malapert scenario.",
        scenario_id="scn_current",
    )
    assert plan.is_fully_matched is True
    assert [action.action_id for action in plan.actions] == ["scenario.switch"]
    assert plan.actions[0].steps[0].tool_name == "scenario.set_current"
    assert plan.actions[0].steps[0].arguments == {"scenario_ref": "mons_malapert"}


def test_hybrid_router_only_matches_show_hide_when_layer_keyword_present() -> None:
    router = HybridCommandRouter(enabled=True)
    non_layer = router.plan(prompt="Show that csv file as a table.", scenario_id="scn_1")
    assert non_layer.actions == []
    assert non_layer.unmatched_segments == ["Show that csv file as a table."]

    layer = router.plan(prompt="Show slope.", scenario_id="scn_1")
    assert layer.is_fully_matched is True
    assert layer.actions[0].action_id == "layer.set_visible_by_name"


def test_hybrid_router_drops_complex_conditional_to_model_loop() -> None:
    router = HybridCommandRouter(enabled=True)
    plan = router.plan(
        prompt="Show the slope layer only if sun elevation is above 10 degrees.",
        scenario_id="scn_1",
    )
    assert plan.actions == []
    assert plan.unmatched_segments == ["Show the slope layer only if sun elevation is above 10 degrees."]


def test_hybrid_router_supports_partial_plan() -> None:
    router = HybridCommandRouter(enabled=True)
    plan = router.plan(
        prompt="Switch to test_scenario, then explain what layers are most relevant.",
        scenario_id="scn_1",
    )
    assert len(plan.actions) == 1
    assert plan.actions[0].action_id == "scenario.switch"
    assert plan.unmatched_segments == ["explain what layers are most relevant."]


def test_hybrid_router_plans_run_predefined_job_with_params() -> None:
    router = HybridCommandRouter(enabled=True)
    plan = router.plan(
        prompt='run predefined job ping {"message":"hello"}',
        scenario_id="scn_1",
    )
    assert plan.is_fully_matched is True
    assert len(plan.actions) == 1
    step = plan.actions[0].steps[0]
    assert step.tool_name == "jobs.run_predefined"
    assert step.arguments == {"implementation_name": "ping", "params": {"message": "hello"}}


def test_hybrid_router_plans_run_script_in_current_scenario() -> None:
    router = HybridCommandRouter(enabled=True)
    plan = router.plan(prompt='run script "scripts/test.py"', scenario_id="scn_1")
    assert plan.is_fully_matched is True
    step = plan.actions[0].steps[0]
    assert step.tool_name == "scenario.run_script"
    assert step.arguments == {"relative_path": "scripts/test.py", "scenario_id": "scn_1"}


def test_hybrid_router_does_not_plan_scenario_bound_action_without_scenario() -> None:
    router = HybridCommandRouter(enabled=True)
    plan = router.plan(prompt="list scripts", scenario_id=None)
    assert plan.actions == []
    assert plan.unmatched_segments == ["list scripts"]


def test_hybrid_router_agent_step_action_requires_feature_flag() -> None:
    disabled_router = HybridCommandRouter(enabled=True, enable_agent_substeps=False)
    disabled_plan = disabled_router.plan(
        prompt="set visibility for layer slope to on",
        scenario_id="scn_1",
    )
    assert disabled_plan.actions == []

    enabled_router = HybridCommandRouter(enabled=True, enable_agent_substeps=True)
    enabled_plan = enabled_router.plan(
        prompt="set visibility for layer slope to on",
        scenario_id="scn_1",
    )
    assert enabled_plan.is_fully_matched is True
    assert enabled_plan.actions[0].action_id == "layer.resolve_visibility_with_agent"


def test_hybrid_router_plans_slope_highlight_binary_mask() -> None:
    router = HybridCommandRouter(enabled=True)
    plan = router.plan(
        prompt="Highlight pixels where the slope is 5 degrees or less.",
        scenario_id="scn_1",
    )
    assert plan.is_fully_matched is True
    assert plan.actions[0].action_id == "raster.calculate.slope_threshold_mask_binary"
    step = plan.actions[0].steps[0]
    assert step.tool_name == "raster.calculate"
    assert step.arguments == {
        "scenario_id": "scn_1",
        "expression": "slope <= 5.0",
        "inputs": {"slope": {"relative_path": "slope.tif"}},
        "output_relative_path": "slope_le_5p0deg_mask.tif",
        "overwrite_mode": "always",
    }


def test_hybrid_router_plans_slope_highlight_transparent_with_output_name() -> None:
    router = HybridCommandRouter(enabled=True)
    plan = router.plan(
        prompt=(
            "Generate a new geotiff called landing_sites2.tif that highlights pixels where "
            "the slope is <= 5 degrees. Make other pixels transparent."
        ),
        scenario_id="scn_1",
    )
    assert plan.is_fully_matched is True
    assert plan.actions[0].action_id == "raster.calculate.slope_threshold_mask_transparent"
    step = plan.actions[0].steps[0]
    assert step.tool_name == "raster.calculate"
    assert step.arguments == {
        "scenario_id": "scn_1",
        "expression": "where(slope <= 5.0, 1, nodata())",
        "inputs": {"slope": {"relative_path": "slope.tif"}},
        "output_relative_path": "landing_sites2.tif",
        "overwrite_mode": "always",
    }


def test_hybrid_router_context_rejects_invalid_layer_name_slot() -> None:
    context = ScenarioCommandContext(
        scenario_refs={"scn_1", "test_scenario"},
        layer_names={"slope", "hillshade", "aspect"},
        layer_ids=set(),
    )
    router = HybridCommandRouter(
        enabled=True,
        scenario_context_resolver=lambda _sid: context,
    )
    plan = router.plan(
        prompt="Show me the south pole of the moon.",
        scenario_id="scn_1",
    )
    assert plan.actions == []
    assert plan.unmatched_segments == ["Show me the south pole of the moon."]


def test_hybrid_router_context_rejects_invalid_scenario_ref_slot() -> None:
    context = ScenarioCommandContext(
        scenario_refs={"scn_test", "test_scenario", "mons_malapert"},
        layer_names={"slope"},
        layer_ids=set(),
    )
    router = HybridCommandRouter(
        enabled=True,
        scenario_context_resolver=lambda _sid: context,
    )
    plan = router.plan(
        prompt=(
            "Switch to an operations color map with a landing threshold of 10 degrees "
            "and a movement threshold of 15 degrees."
        ),
        scenario_id="scn_test",
    )
    assert plan.actions == []
    assert len(plan.unmatched_segments) > 0
