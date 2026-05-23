from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.services.assistant.product_type_dictionary import PRODUCT_TYPE_DICT
from backend.services.assistant.prompt_classifier import SegmentClassification
from backend.services.assistant.prompt_segmenter import PromptSegment


@dataclass(frozen=True)
class ExecutionPlanStepRecord:
    step_id: str
    kind: str
    tool_name: str | None
    action_id: str | None
    status: str = "pending"


@dataclass(frozen=True)
class ExecutionPlanSegmentRecord:
    segment_id: str
    text: str
    start_char: int
    end_char: int
    classification: SegmentClassification
    execution_mode: str
    dependencies: list[str] = field(default_factory=list)
    planned_steps: list[ExecutionPlanStepRecord] = field(default_factory=list)
    required_inputs: list[str] = field(default_factory=list)
    expected_postconditions: list[str] = field(default_factory=list)
    requested_product_type: str | None = None
    selected_recipe_id: str | None = None
    prerequisite_count: int = 0
    required: bool = True
    status: str = "pending"


@dataclass(frozen=True)
class TurnExecutionPlanDocument:
    schema_version: str
    turn_id: str
    session_id: str
    prompt_text: str
    segments: list[ExecutionPlanSegmentRecord]
    execution_policy: dict[str, Any]
    runtime_state_seed: dict[str, Any]
    execution_plan_status: str = "planned"


class TurnExecutionPlanBuilder:
    def build(
        self,
        *,
        turn_id: str,
        session_id: str,
        prompt_text: str,
        segments: list[PromptSegment],
        classifications: list[SegmentClassification],
        runtime_state_seed: dict[str, Any],
    ) -> TurnExecutionPlanDocument:
        class_by_id = {item.segment_id: item for item in classifications}
        plan_segments: list[ExecutionPlanSegmentRecord] = []
        for segment in segments:
            classification = class_by_id.get(segment.segment_id)
            if classification is None:
                continue
            if classification.segment_class in {"command", "create_product", "intent_family"}:
                execution_mode = "deterministic"
            else:
                execution_mode = "llm"
            requested_product_type = classification.product_type if classification.segment_class == "create_product" else None
            selected_recipe_id: str | None = None
            prerequisite_count = 0
            if requested_product_type:
                spec = PRODUCT_TYPE_DICT.get(str(requested_product_type).strip())
                if spec is not None:
                    selected_recipe_id = spec.canonical_recipe_ids[0] if spec.canonical_recipe_ids else None
                    prerequisite_count = len(spec.precursor_requirements)
            planned_steps: list[ExecutionPlanStepRecord] = []
            for idx, action_id in enumerate(classification.matched_action_ids, start=1):
                planned_steps.append(
                    ExecutionPlanStepRecord(
                        step_id=f"{segment.segment_id}.step{idx}",
                        kind="tool_call",
                        tool_name=None,
                        action_id=action_id,
                    )
                )
            plan_segments.append(
                ExecutionPlanSegmentRecord(
                    segment_id=segment.segment_id,
                    text=segment.text,
                    start_char=segment.start_char,
                    end_char=segment.end_char,
                    classification=classification,
                    execution_mode=execution_mode,
                    planned_steps=planned_steps,
                    requested_product_type=requested_product_type,
                    selected_recipe_id=selected_recipe_id,
                    prerequisite_count=prerequisite_count,
                )
            )

        document = TurnExecutionPlanDocument(
            schema_version="1.0",
            turn_id=turn_id,
            session_id=session_id,
            prompt_text=prompt_text,
            segments=plan_segments,
            execution_policy={
                "max_deterministic_steps": 12,
                "allow_partial_deterministic_execution": True,
                "allow_model_continuation": True,
                "stop_on_hard_failure": False,
                "mutating_requires_confirmation": True,
            },
            runtime_state_seed=dict(runtime_state_seed),
        )
        self.validate(document)
        return document

    @staticmethod
    def validate(document: TurnExecutionPlanDocument) -> None:
        if document.schema_version != "1.0":
            raise ValueError("turn_execution_plan_invalid_schema")
        seen: set[str] = set()
        last_end = -1
        for segment in document.segments:
            if segment.segment_id in seen:
                raise ValueError("turn_execution_plan_invalid_schema")
            seen.add(segment.segment_id)
            if segment.start_char < last_end:
                raise ValueError("turn_execution_plan_invalid_dependency")
            last_end = segment.end_char
            if segment.execution_mode not in {"deterministic", "llm", "blocked"}:
                raise ValueError("turn_execution_plan_invalid_schema")
