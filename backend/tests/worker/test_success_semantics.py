from __future__ import annotations

from types import SimpleNamespace

from backend.services.assistant.success_semantics import compute_success_semantics


def test_success_semantics_treats_list_colormaps_intent_as_read_only() -> None:
    aggregate, outcomes = compute_success_semantics(
        execution_plan_segments=[
            {
                "segment_id": "s1",
                "execution_mode": "deterministic",
                "required": True,
                "classification": {
                    "label": "intent_family",
                    "intent_family": "layer_style_update",
                    "intent_properties": {"operation": "list_colormaps"},
                },
            }
        ],
        tool_calls=[
            SimpleNamespace(tool_name="colormap.list", status="completed", result={"colormaps": []}),
        ],
        current_scenario_id="scn_1",
    )
    assert aggregate == "success"
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.prompt_class == "read_only"
    assert outcome.status == "completed"
    assert outcome.postcondition_checked is False
    assert outcome.postcondition_passed is None


def test_success_semantics_checks_layer_apply_colormap_postcondition() -> None:
    aggregate, outcomes = compute_success_semantics(
        execution_plan_segments=[
            {
                "segment_id": "s1",
                "execution_mode": "deterministic",
                "required": True,
                "classification": {
                    "label": "intent_family",
                    "intent_family": "layer_style_update",
                    "intent_properties": {"operation": "apply"},
                },
            }
        ],
        tool_calls=[
            SimpleNamespace(tool_name="layer.apply_colormap", status="completed", result={"layer_id": "lyr_1"}),
        ],
        current_scenario_id="scn_1",
    )
    assert aggregate == "success"
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.prompt_class == "mutating"
    assert outcome.status == "completed"
    assert outcome.postcondition_checked is True
    assert outcome.postcondition_passed is True

