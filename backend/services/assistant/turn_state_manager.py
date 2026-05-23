from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.services.assistant.turn_execution_plan import TurnExecutionPlanDocument


@dataclass
class SegmentState:
    segment_id: str
    execution_mode: str
    text: str
    status: str = "pending"
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    state_effects: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    requires_user_input: bool = False


@dataclass
class TurnState:
    schema_version: str
    turn_id: str
    session_id: str
    segments: list[SegmentState]
    global_effects: dict[str, Any] = field(default_factory=dict)
    handoff_to_llm: dict[str, Any] = field(default_factory=dict)
    final_merge: dict[str, Any] = field(default_factory=dict)
    state_status: str = "in_progress"


class TurnStateManager:
    def create(self, *, execution_plan: TurnExecutionPlanDocument) -> TurnState:
        return TurnState(
            schema_version="1.0",
            turn_id=execution_plan.turn_id,
            session_id=execution_plan.session_id,
            segments=[
                SegmentState(
                    segment_id=item.segment_id,
                    execution_mode=item.execution_mode,
                    text=item.text,
                    status="blocked" if item.execution_mode == "blocked" else "pending",
                    requires_user_input=bool(item.execution_mode == "blocked"),
                )
                for item in execution_plan.segments
            ],
        )

    @staticmethod
    def mark_deterministic_complete(turn_state: TurnState, *, action_segment_text: str, tool_call: dict[str, Any]) -> None:
        lowered = action_segment_text.strip().lower()
        for segment in turn_state.segments:
            if segment.text.strip().lower() == lowered and segment.execution_mode == "deterministic":
                segment.status = "completed"
                segment.tool_calls.append(dict(tool_call))
                return

    @staticmethod
    def mark_model_segments_complete(turn_state: TurnState) -> None:
        for segment in turn_state.segments:
            if segment.execution_mode == "llm" and segment.status == "pending":
                segment.status = "completed"

    @staticmethod
    def mark_failed(turn_state: TurnState, *, error_code: str, message: str) -> None:
        for segment in turn_state.segments:
            if segment.status in {"pending", "running"}:
                segment.status = "failed"
                segment.error = {
                    "code": error_code,
                    "message": message,
                    "recoverable": True,
                    "suggested_recovery": "retry_or_clarify",
                }

    @staticmethod
    def build_handoff(turn_state: TurnState, *, active_scenario_id: str | None, active_scenario_directory: str | None) -> dict[str, Any]:
        unresolved = [item.segment_id for item in turn_state.segments if item.execution_mode == "llm" and item.status != "completed"]
        deterministic_summary: list[dict[str, Any]] = []
        for segment in turn_state.segments:
            if segment.execution_mode != "deterministic":
                continue
            deterministic_summary.append(
                {
                    "segment_id": segment.segment_id,
                    "result_kind": "state_change" if segment.status == "completed" else "failed",
                    "details": {"status": segment.status, "tool_calls": len(segment.tool_calls)},
                }
            )
        blocked_segments = [item.segment_id for item in turn_state.segments if item.status == "blocked"]
        handoff = {
            "unresolved_segment_ids": unresolved,
            "deterministic_summary": deterministic_summary,
            "active_scenario_id": active_scenario_id,
            "active_scenario_directory": active_scenario_directory,
            "artifact_refs": [],
            "blocked_segments": blocked_segments,
        }
        turn_state.handoff_to_llm = handoff
        return handoff

    @staticmethod
    def build_merge(turn_state: TurnState) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for segment in turn_state.segments:
            recipe_summary = None
            if segment.state_effects.get("recipe_id"):
                recipe_summary = {
                    "recipe_id": segment.state_effects.get("recipe_id"),
                    "requested_product_type": segment.state_effects.get("requested_product_type"),
                    "prerequisite_count": segment.state_effects.get("prerequisite_count"),
                }
            entries.append(
                {
                    "segment_id": segment.segment_id,
                    "text": segment.text,
                    "execution_mode": segment.execution_mode,
                    "status": segment.status,
                    "summary": f"{segment.execution_mode}:{segment.status}",
                    "artifact_refs": list(segment.artifacts),
                    "error_code": (segment.error or {}).get("code"),
                    "recipe_summary": recipe_summary,
                    "prerequisite_outcomes": segment.state_effects.get("prerequisite_outcomes", []),
                }
            )
        turn_state.final_merge = {"segments": entries}
        return turn_state.final_merge
