from __future__ import annotations

from backend.services.assistant.entity_reference_resolver import SegmentEntityResolution
from backend.services.assistant.intent_to_tool_planner import IntentToToolPlanner
from backend.services.assistant.prompt_classifier import SegmentClassification, SegmentOffsets
from backend.services.assistant.verb_normalizer import VerbNormalizationResult


def _classification(*, family: str, properties: dict) -> SegmentClassification:
    return SegmentClassification(
        segment_id="s1",
        text="test",
        offsets=SegmentOffsets(start=0, stop=4),
        segment_class="intent_family",
        confidence=0.8,
        classification_origin="extractor_semantic_family",
        intent_family=family,
        intent_properties=properties,
    )


def _resolution(
    *,
    canonical_operation: str,
    target_kind: str,
    target_mention: str,
    target_resolved_id: str | None,
) -> SegmentEntityResolution:
    return SegmentEntityResolution(
        segment_id="s1",
        canonical_operation=canonical_operation,
        verb_normalization=VerbNormalizationResult(
            canonical_operation=canonical_operation,
            normalized_input_operation=canonical_operation,
            source="test",
        ),
        direct_object_candidate=target_mention,
        target_kind=target_kind,
        target_mention=target_mention,
        target_resolved_id=target_resolved_id,
    )


def test_intent_to_tool_planner_maps_colormap_apply() -> None:
    mapper = IntentToToolPlanner()
    result = mapper.map(
        classification=_classification(
            family="layer_style_update",
            properties={
                "operation": "apply",
                "target": {"layer_ref": "slope"},
                "style": {"kind": "colormap", "colormap_ref": "magma"},
            },
        ),
        scenario_id="scn_1",
    )
    assert result is not None
    assert result.requires_clarification is False
    assert result.tool_steps[0].tool_name == "layer.apply_colormap"
    assert result.tool_steps[0].arguments == {
        "scenario_id": "scn_1",
        "layer_name": "slope",
        "colormap": "magma",
    }


def test_intent_to_tool_planner_requires_clarification_for_toggle_visibility() -> None:
    mapper = IntentToToolPlanner()
    result = mapper.map(
        classification=_classification(
            family="layer_visibility_update",
            properties={"operation": "toggle", "target": {"layer_ref": "slope"}},
        ),
        scenario_id="scn_1",
    )
    assert result is not None
    assert result.requires_clarification is True
    assert result.blocking_reason_code == "toggle_requires_explicit_state"


def test_intent_to_tool_planner_maps_compute_job_logs() -> None:
    mapper = IntentToToolPlanner()
    result = mapper.map(
        classification=_classification(
            family="compute_job_control",
            properties={
                "operation": "logs",
                "job_ref": {"job_id": "job_1"},
                "log_options": {"tail_lines": 80, "stream": "combined"},
            },
        ),
        scenario_id="scn_1",
    )
    assert result is not None
    assert result.requires_clarification is False
    assert result.tool_steps[0].tool_name == "runs.get_logs"
    assert result.tool_steps[0].arguments == {
        "job_id": "job_1",
        "tail_lines": 80,
        "stream": "combined",
    }


def test_intent_to_tool_planner_maps_list_colormaps_in_layer_style_family() -> None:
    mapper = IntentToToolPlanner()
    result = mapper.map(
        classification=_classification(
            family="layer_style_update",
            properties={"operation": "list_colormaps"},
        ),
        scenario_id="scn_1",
    )
    assert result is not None
    assert result.requires_clarification is False
    assert result.tool_steps[0].tool_name == "colormap.list"
    assert result.tool_steps[0].arguments == {"scenario_id": "scn_1"}


def test_intent_to_tool_planner_maps_lunar_environment_reasoning_to_guarded_handoff() -> None:
    mapper = IntentToToolPlanner()
    result = mapper.map(
        classification=_classification(
            family="lunar_environment_reasoning",
            properties={"question_type": "interpretation"},
        ),
        scenario_id="scn_1",
    )
    assert result is not None
    assert result.requires_clarification is False
    assert result.tool_steps == []
    assert "evidence-backed lunar environment analysis" in str(result.model_handoff_prompt or "").lower()
    assert result.response_guardrails.get("evidence_required") is True
    assert result.response_guardrails.get("uncertainty_required") is True


def test_intent_to_tool_planner_maps_surface_route_planning_to_mixed_outcome() -> None:
    mapper = IntentToToolPlanner()
    result = mapper.map(
        classification=_classification(
            family="surface_route_planning",
            properties={
                "operation": "plan",
                "origin_ref": "lander_site",
                "destination_ref": "sample_region_1",
            },
        ),
        scenario_id="scn_1",
    )
    assert result is not None
    assert result.requires_clarification is False
    assert len(result.tool_steps) == 1
    assert result.tool_steps[0].tool_name == "tools.search"
    assert result.response_guardrails.get("requires_alternatives") is True
    assert result.response_guardrails.get("evidence_required") is True


def test_intent_to_tool_planner_requires_scope_for_evidence_packaging() -> None:
    mapper = IntentToToolPlanner()
    result = mapper.map(
        classification=_classification(
            family="evidence_packaging",
            properties={"operation": "export"},
        ),
        scenario_id="scn_1",
    )
    assert result is not None
    assert result.requires_clarification is True
    assert result.blocking_reason_code == "missing_evidence_scope"


def test_intent_to_tool_planner_maps_location_navigation_goto() -> None:
    mapper = IntentToToolPlanner()
    result = mapper.map(
        classification=_classification(
            family="location_navigation",
            properties={
                "operation": "goto",
                "feature_ref": "Shackleton",
                "context_filter": "Crater",
            },
        ),
        scenario_id="scn_1",
    )
    assert result is not None
    assert result.requires_clarification is False
    assert result.tool_steps[0].tool_name == "location.goto"
    assert result.tool_steps[0].arguments == {
        "scenario_id": "scn_1",
        "name": "Shackleton",
        "feature_type": "Crater",
    }


def test_intent_to_tool_planner_maps_location_navigation_identify() -> None:
    mapper = IntentToToolPlanner()
    result = mapper.map(
        classification=_classification(
            family="location_navigation",
            properties={
                "operation": "identify",
                "point": {"x": 10.0, "y": 20.0},
                "radius_m": 2500.0,
            },
        ),
        scenario_id="scn_1",
    )
    assert result is not None
    assert result.requires_clarification is False
    assert result.tool_steps[0].tool_name == "location.identify"
    assert result.tool_steps[0].arguments == {"x": 10.0, "y": 20.0, "radius_m": 2500.0}


def test_intent_to_tool_planner_entity_kind_routing_show_feature_to_location_goto() -> None:
    mapper = IntentToToolPlanner()
    result = mapper.map(
        classification=_classification(
            family="layer_visibility_update",
            properties={"operation": "show"},
        ),
        scenario_id="scn_1",
        entity_resolution=_resolution(
            canonical_operation="show",
            target_kind="feature",
            target_mention="Mons Mouton",
            target_resolved_id="feature:42",
        ),
    )
    assert result is not None
    assert result.requires_clarification is False
    assert result.tool_steps[0].tool_name == "location.goto"
    assert result.tool_steps[0].arguments == {
        "scenario_id": "scn_1",
        "name": "Mons Mouton",
        "feature_id": "42",
    }


def test_intent_to_tool_planner_entity_kind_routing_show_layer_updates_visibility() -> None:
    mapper = IntentToToolPlanner()
    result = mapper.map(
        classification=_classification(
            family="layer_visibility_update",
            properties={"operation": "show"},
        ),
        scenario_id="scn_1",
        entity_resolution=_resolution(
            canonical_operation="show",
            target_kind="layer",
            target_mention="slope",
            target_resolved_id="layer:layer_slope",
        ),
    )
    assert result is not None
    assert result.requires_clarification is False
    assert result.tool_steps[0].tool_name == "layer.update_state"
    assert result.tool_steps[0].arguments == {
        "scenario_id": "scn_1",
        "layer_name": "layer_slope",
        "visible": True,
    }


def test_intent_to_tool_planner_entity_kind_routing_show_file_uses_import_geotiff() -> None:
    mapper = IntentToToolPlanner()
    result = mapper.map(
        classification=_classification(
            family="layer_visibility_update",
            properties={"operation": "show"},
        ),
        scenario_id="scn_1",
        entity_resolution=_resolution(
            canonical_operation="show",
            target_kind="file",
            target_mention="slope.tif",
            target_resolved_id="file:slope.tif",
        ),
    )
    assert result is not None
    assert result.requires_clarification is False
    assert result.tool_steps[0].tool_name == "scenario.import_geotiff"
    assert result.tool_steps[0].arguments == {
        "scenario_id": "scn_1",
        "source_path": "slope.tif",
    }


def test_intent_to_tool_planner_entity_kind_routing_show_ambiguous_layer_file_requires_clarification() -> None:
    mapper = IntentToToolPlanner()
    result = mapper.map(
        classification=_classification(
            family="layer_visibility_update",
            properties={"operation": "show"},
        ),
        scenario_id="scn_1",
        entity_resolution=_resolution(
            canonical_operation="show",
            target_kind="ambiguous_layer_or_file",
            target_mention="slope",
            target_resolved_id=None,
        ),
    )
    assert result is not None
    assert result.requires_clarification is True
    assert result.blocking_reason_code == "ambiguous_target_layer_or_file"
