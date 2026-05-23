from __future__ import annotations

import pytest

from backend.services.assistant.prompt_classifier import SegmentClassification, SegmentOffsets
from backend.services.assistant.prompt_segmenter import PromptSegment
from backend.services.assistant.turn_execution_plan import (
    ExecutionPlanSegmentRecord,
    TurnExecutionPlanBuilder,
    TurnExecutionPlanDocument,
)


def test_turn_execution_plan_builds_ordered_execution_modes() -> None:
    builder = TurnExecutionPlanBuilder()
    segments = [
        PromptSegment(
            segment_id="s1",
            text="Switch to Shackleton",
            start_char=0,
            end_char=20,
            is_imperative_candidate=True,
            has_complexity_guard=False,
            segmentation_confidence=0.9,
        ),
        PromptSegment(
            segment_id="s2",
            text="Create a slope raster",
            start_char=22,
            end_char=43,
            is_imperative_candidate=False,
            has_complexity_guard=False,
            segmentation_confidence=0.82,
        ),
        PromptSegment(
            segment_id="s3",
            text="Explain tradeoffs",
            start_char=45,
            end_char=61,
            is_imperative_candidate=False,
            has_complexity_guard=True,
            segmentation_confidence=0.8,
        ),
    ]
    classifications = [
        SegmentClassification(
            segment_id="s1",
            text="Switch to Shackleton",
            offsets=SegmentOffsets(start=0, stop=20),
            segment_class="command",
            confidence=0.95,
            classification_origin="deterministic_command",
            command="scenario.switch",
            matched_action_ids=["scenario.switch"],
        ),
        SegmentClassification(
            segment_id="s2",
            text="Create a slope raster",
            offsets=SegmentOffsets(start=22, stop=43),
            segment_class="create_product",
            confidence=0.84,
            classification_origin="heuristic_create_product",
            product_type="slope_raster",
            pixel_type="float",
        ),
        SegmentClassification(
            segment_id="s3",
            text="Explain tradeoffs",
            offsets=SegmentOffsets(start=45, stop=61),
            segment_class="other",
            confidence=0.8,
            classification_origin="fallback_other",
        ),
    ]

    doc = builder.build(
        turn_id="turn_1",
        session_id="sess_1",
        prompt_text="Switch to Shackleton. Create a slope raster. Explain tradeoffs.",
        segments=segments,
        classifications=classifications,
        runtime_state_seed={"active_scenario_id": "scn_1"},
    )
    assert doc.schema_version == "1.0"
    assert [item.execution_mode for item in doc.segments] == ["deterministic", "deterministic", "llm"]
    assert doc.segments[1].requested_product_type == "slope_raster"
    assert doc.segments[1].selected_recipe_id == "slope_from_dem_v1"
    assert doc.segments[1].prerequisite_count == 1
    assert doc.segments[0].requested_product_type is None


def test_turn_execution_plan_validate_rejects_invalid_schema_version() -> None:
    with pytest.raises(ValueError):
        TurnExecutionPlanBuilder.validate(
            TurnExecutionPlanDocument(
                schema_version="2.0",
                turn_id="turn_1",
                session_id="sess_1",
                prompt_text="x",
                segments=[],
                execution_policy={},
                runtime_state_seed={},
            )
        )


def test_turn_execution_plan_validate_rejects_overlapping_offsets() -> None:
    doc = TurnExecutionPlanDocument(
        schema_version="1.0",
        turn_id="turn_1",
        session_id="sess_1",
        prompt_text="x",
        segments=[
            ExecutionPlanSegmentRecord(
                segment_id="s1",
                text="a",
                start_char=0,
                end_char=10,
                classification=SegmentClassification(
                    segment_id="s1",
                    text="a",
                    offsets=SegmentOffsets(start=0, stop=10),
                    segment_class="command",
                    confidence=0.9,
                    classification_origin="deterministic_command",
                ),
                execution_mode="deterministic",
            ),
            ExecutionPlanSegmentRecord(
                segment_id="s2",
                text="b",
                start_char=9,
                end_char=12,
                classification=SegmentClassification(
                    segment_id="s2",
                    text="b",
                    offsets=SegmentOffsets(start=9, stop=12),
                    segment_class="other",
                    confidence=0.7,
                    classification_origin="fallback_other",
                ),
                execution_mode="llm",
            ),
        ],
        execution_policy={},
        runtime_state_seed={},
    )
    with pytest.raises(ValueError):
        TurnExecutionPlanBuilder.validate(doc)
