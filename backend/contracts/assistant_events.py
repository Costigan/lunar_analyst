from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import ConfigDict

from .models import StrictModel
from .types import UtcTimestamp


class AssistantEventName(str, Enum):
    TURN_STARTED = "assistant_turn_started"
    DELTA = "assistant_delta"
    TURN_COMPLETED = "assistant_turn_completed"
    SCENARIO_CHANGED = "assistant_scenario_changed"
    TOOL_CALL_PROPOSED = "assistant_tool_call_proposed"
    TOOL_CALL_STARTED = "assistant_tool_call_started"
    TOOL_CALL_COMPLETED = "assistant_tool_call_completed"
    CONFIRMATION_REQUIRED = "assistant_confirmation_required"
    CONFIRMATION_RESOLVED = "assistant_confirmation_resolved"
    ERROR = "assistant_error"
    PROMPT_SEGMENTATION_COMPLETED = "prompt_segmentation_completed"
    PROMPT_CLASSIFICATION_COMPLETED = "prompt_classification_completed"
    TURN_EXECUTION_PLAN_BUILT = "turn_execution_plan_built"
    TURN_EXECUTION_PLAN_VALIDATION_FAILED = "turn_execution_plan_validation_failed"
    SEGMENT_EXECUTION_STARTED = "segment_execution_started"
    SEGMENT_EXECUTION_FINISHED = "segment_execution_finished"
    DETERMINISTIC_HANDOFF_BUILT = "deterministic_handoff_built"
    TURN_MERGE_COMPLETED = "turn_merge_completed"
    TURN_STATUS_FINALIZED = "turn_status_finalized"


ASSISTANT_WS_EVENT_NAMES: tuple[str, ...] = tuple(e.value for e in AssistantEventName)


class AssistantWsEnvelope(StrictModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = "1.1"
    event: AssistantEventName
    session_id: str
    turn_id: str | None = None
    timestamp_utc: UtcTimestamp
    data: dict[str, Any]
