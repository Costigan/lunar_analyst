from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field

from .models import StrictModel
from .types import UtcTimestamp


class AssistantRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class AssistantTurnStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CONFIRMATION_REQUIRED = "confirmation_required"


class AssistantConfirmationActionType(str, Enum):
    LAUNCH_JOB = "launch_job"
    IMPORT_FILE = "import_file"
    MOVE_PATH = "move_path"
    UPDATE_LAYER_STATE = "update_layer_state"
    DELETE_ARTIFACT = "delete_artifact"
    WRITE_NOTEBOOK = "write_notebook"


class AssistantConfirmationDecision(str, Enum):
    ALLOW_ONCE = "allow_once"
    ALWAYS_ALLOW_ACTION_TYPE = "always_allow_action_type"
    DENY_ONCE = "deny_once"


class AssistantAccessMode(str, Enum):
    MCP_ONLY = "mcp_only"
    SCENARIO_ROOT = "scenario_root"


class AssistantOutput(StrictModel):
    output_id: str
    kind: Literal["image", "table", "plot", "artifact_card", "map_view"]
    mime_type: str
    storage: Literal["inline", "file"]
    title: str | None = None
    caption: str | None = None
    file_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssistantPolicy(StrictModel):
    always_allow_action_types: list[AssistantConfirmationActionType] = Field(default_factory=list)


class AssistantSession(StrictModel):
    session_id: str
    title: str
    created_at_utc: UtcTimestamp
    updated_at_utc: UtcTimestamp
    last_message_at_utc: UtcTimestamp | None = None
    policy: AssistantPolicy = Field(default_factory=AssistantPolicy)


class AssistantMessage(StrictModel):
    message_id: str
    session_id: str
    role: AssistantRole
    content: str
    created_at_utc: UtcTimestamp
    turn_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    outputs: list[AssistantOutput] = Field(default_factory=list)


class AssistantToolCall(StrictModel):
    tool_call_id: str
    session_id: str
    turn_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at_utc: UtcTimestamp
    completed_at_utc: UtcTimestamp | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    action_type: AssistantConfirmationActionType | None = None
    outputs: list[AssistantOutput] = Field(default_factory=list)


class AssistantConfirmation(StrictModel):
    confirmation_id: str
    session_id: str
    turn_id: str
    action_type: AssistantConfirmationActionType
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: str
    requested_at_utc: UtcTimestamp
    resolved_at_utc: UtcTimestamp | None = None
    resolution: AssistantConfirmationDecision | None = None


class AssistantTurn(StrictModel):
    turn_id: str
    session_id: str
    user_message_id: str
    status: AssistantTurnStatus
    provider_id: str | None = None
    model_id: str | None = None
    created_at_utc: UtcTimestamp
    updated_at_utc: UtcTimestamp
    error: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


class CreateAssistantSessionRequest(StrictModel):
    title: str = Field(min_length=1, max_length=128)


class ListAssistantSessionsResponse(StrictModel):
    sessions: list[AssistantSession] = Field(default_factory=list)


class AssistantSessionDetailResponse(StrictModel):
    session: AssistantSession
    recent_messages: list[AssistantMessage] = Field(default_factory=list)


class CreateAssistantTurnRequest(StrictModel):
    prompt: str = Field(min_length=1)
    scenario_id: str | None = None
    constraints: str | None = None
    base_layer_visible: bool | None = None
    provider_id: str | None = None
    model_id: str | None = None
    access_mode: AssistantAccessMode | None = None
    thinking: bool | Literal["low", "medium", "high"] | None = None


class CreateAssistantTurnResponse(StrictModel):
    turn: AssistantTurn
    assistant_message: AssistantMessage | None = None
    confirmation: AssistantConfirmation | None = None
    tool_calls: list[AssistantToolCall] = Field(default_factory=list)


class ListAssistantMessagesResponse(StrictModel):
    messages: list[AssistantMessage] = Field(default_factory=list)


class AssistantConfirmationDecisionRequest(StrictModel):
    decision: AssistantConfirmationDecision


class AssistantConfirmationDecisionResponse(StrictModel):
    confirmation: AssistantConfirmation
    turn: AssistantTurn
    assistant_message: AssistantMessage | None = None
    tool_calls: list[AssistantToolCall] = Field(default_factory=list)


class UpdateAssistantPolicyRequest(StrictModel):
    always_allow_action_types: list[AssistantConfirmationActionType] = Field(default_factory=list)


class CompactAssistantSessionRequest(StrictModel):
    max_messages_to_compact: int = Field(default=80, ge=1, le=2000)


class CompactAssistantSessionResponse(StrictModel):
    session_id: str
    compacted_message_count: int = Field(ge=0)
    summary_message_id: str | None = None


class AssistantBugReportProgramState(StrictModel):
    active_scenario_id: str | None = None
    active_assistant_session_id: str | None = None
    active_assistant_turn_id: str | None = None
    active_provider_id: str | None = None
    active_model_id: str | None = None
    active_panel: str | None = None
    assistant_prompt_draft: str | None = None
    workspace_state: dict[str, Any] = Field(default_factory=dict)


class AssistantBugReportRequest(StrictModel):
    report_text: str = Field(min_length=1, max_length=4000)
    program_state: AssistantBugReportProgramState = Field(default_factory=AssistantBugReportProgramState)


class AssistantBugReport(StrictModel):
    bug_report_id: str
    created_at_utc: UtcTimestamp
    report_text: str
    assistant_session_id: str | None = None
    assistant_turn_id: str | None = None
    assistant_provider_id: str | None = None
    assistant_model_id: str | None = None
    provider_request_id: str | None = None
    model_tool_schema: dict[str, Any] | None = Field(default=None)
    model_tool_names: list[str] = Field(default_factory=list)
    scenario_id: str | None = None
    assistant_context: dict[str, Any] = Field(default_factory=dict)
    program_state: AssistantBugReportProgramState = Field(default_factory=AssistantBugReportProgramState)
    log_excerpt: list[str] = Field(default_factory=list)
    redactions_applied: bool = True


class AssistantBugReportResponse(StrictModel):
    bug_report: AssistantBugReport
    bundle_path: str


class AssistantBugReportSummary(StrictModel):
    bug_report_id: str
    created_at_utc: UtcTimestamp
    report_text: str
    assistant_session_id: str | None = None
    assistant_turn_id: str | None = None
    scenario_id: str | None = None
    bundle_path: str


class ListAssistantBugReportsResponse(StrictModel):
    bug_reports: list[AssistantBugReportSummary] = Field(default_factory=list)


class AssistantModelMetadata(StrictModel):
    capabilities: list[str] = Field(default_factory=list)
    thinking_mode: Literal["none", "boolean", "level"] = "none"


class AssistantProviderInfo(StrictModel):
    provider_id: str
    kind: str
    execution_mode: str = "tool_loop"
    access_mode: str | None = None
    available: bool
    default_model: str | None = None
    models: list[str] = Field(default_factory=list)
    model_metadata: dict[str, AssistantModelMetadata] = Field(default_factory=dict)
    notes: str = ""


class AssistantProviderCatalogResponse(StrictModel):
    default_provider_id: str | None = None
    default_model_id: str | None = None
    providers: list[AssistantProviderInfo] = Field(default_factory=list)
