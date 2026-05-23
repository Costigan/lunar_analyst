from __future__ import annotations

import json
import jsonschema
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from backend.contracts.assistant_events import AssistantEventName, AssistantWsEnvelope
from backend.contracts.assistant_models import (
    AssistantBugReportRequest,
    AssistantBugReportResponse,
    AssistantConfirmation,
    AssistantConfirmationActionType,
    AssistantConfirmationDecision,
    AssistantConfirmationDecisionRequest,
    AssistantConfirmationDecisionResponse,
    AssistantMessage,
    AssistantOutput,
    AssistantRole,
    AssistantSession,
    AssistantSessionDetailResponse,
    AssistantToolCall,
    AssistantTurnStatus,
    CompactAssistantSessionRequest,
    CompactAssistantSessionResponse,
    CreateAssistantSessionRequest,
    CreateAssistantTurnRequest,
    CreateAssistantTurnResponse,
    ListAssistantBugReportsResponse,
    ListAssistantMessagesResponse,
    ListAssistantSessionsResponse,
    UpdateAssistantPolicyRequest,
)
from backend.api.errors import ApiError
from backend.core.config import load_app_config
from backend.services.assistant.context_builder import (
    SYSTEM_PROMPT_PATH,
    build_conversation,
    build_system_prompt,
    compact_tool_result_for_model_context,
    summarize_tool_result,
)
from backend.services.assistant.context_compactor import compact_messages
from backend.services.assistant.policy_service import AssistantPolicyService
from backend.services.assistant.provider_registry import (
    AssistantProviderInitializationError,
    AssistantProviderRegistry,
)
from backend.services.assistant.providers.base import ProviderCompletion, ProviderToolCall
from backend.services.assistant.session_store import AssistantSessionStore
from backend.services.assistant.scenario_ref_normalization import normalize_scenario_reference
from backend.services.assistant.token_cache import build_cache_context
from backend.services.assistant.tool_registry import (
    action_type_for_tool,
    capabilities_text,
    execute_tool,
    list_tools_for_model,
    list_tools_schema,
    list_tools_schema_filtered,
    select_tool_names_for_prompt,
    tool_argument_schema_for_model,
    tool_argument_schema_for_tool,
)
from backend.services.assistant.action_router_config import load_entity_kind_routing_rules
from backend.services.assistant.command_router import (
    CommandPlan,
    HybridCommandRouter,
    PlannedAction,
    PlannedAgentStep,
    PlannedToolStep,
)
from backend.services.assistant.create_product_planner import (
    AvailableProduct,
    CreateProductBlock,
    CreateProductPlan,
    CreateProductPlanner,
    CreateProductReuse,
)
from backend.services.assistant.prompt_classifier import PromptClassifier, SegmentClassification
from backend.services.assistant.prompt_segmenter import PromptSegment, PromptSegmenter
from backend.services.assistant.intent_to_tool_planner import (
    INTENT_TO_TOOL_PLANNABLE_FAMILIES,
    IntentToToolPlanner,
)
from backend.services.assistant.entity_reference_resolver import EntityReferenceResolver, SegmentEntityResolution
from backend.services.assistant.deterministic_recognizer import (
    UnifiedDeterministicRecognizer,
)
from backend.services.assistant.verb_normalizer import VerbNormalizer
from backend.services.assistant.success_semantics import compute_success_semantics
from backend.services.assistant.telemetry_codes import (
    ERROR_SEGMENTATION_INVALID,
    ERROR_TOOL_EXECUTION_FAILED,
)
from backend.services.assistant.tool_argument_repair import RepairOutcome, ToolArgumentRepairer
from backend.services.assistant.bug_report_service import (
    bug_report_dir,
    build_bug_report_bundle,
    list_bug_report_summaries,
    load_bug_report_bundle,
    save_bug_report_bundle,
)
from backend.services.assistant.turn_execution_plan import (
    TurnExecutionPlanBuilder,
    TurnExecutionPlanDocument,
)
from backend.services.assistant.turn_state_manager import TurnState, TurnStateManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolExecutionServices:
    scenario_service: Any
    product_service: Any
    layer_service: Any
    job_service: Any
    notebook_job_service: Any
    stores: Any


@dataclass
class SessionRuntimeState:
    current_scenario_id: str | None = None
    base_layer_visible: bool = True
    script_run_allowlist: set[str] = field(default_factory=set)
    overwrite_allowlist: set[str] = field(default_factory=set)
    created_scripts: set[str] = field(default_factory=set)
    last_provider_id: str | None = None
    constraints_text: str = ""


@dataclass
class TurnTelemetry:
    started_at: float
    first_event_ms: int | None = None

    def mark_first_event(self) -> None:
        if self.first_event_ms is None:
            self.first_event_ms = int((time.perf_counter() - self.started_at) * 1000)

    def total_ms(self) -> int:
        return int((time.perf_counter() - self.started_at) * 1000)


@dataclass
class TurnHybridContext:
    prompt_text: str = ""
    segments: list[PromptSegment] = field(default_factory=list)
    classifications: list[SegmentClassification] = field(default_factory=list)
    entity_resolution: dict[str, SegmentEntityResolution] = field(default_factory=dict)
    deterministic_recognition: dict[str, dict[str, Any]] = field(default_factory=dict)
    segment_dispatches: dict[str, dict[str, Any]] = field(default_factory=dict)
    execution_plan: TurnExecutionPlanDocument | None = None
    turn_state: TurnState | None = None
    latencies_ms: dict[str, int] = field(default_factory=dict)


@dataclass
class OrderedOtherSegmentResult:
    text: str
    new_calls: list[AssistantToolCall]
    terminal_response: CreateAssistantTurnResponse | None
    provider_id: str
    model_id: str
    completion_usage: dict[str, int] = field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "cached_prompt_tokens": 0}
    )
    cache_attempted: bool = False
    cache_applied: bool = False
    fallback_used: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class AssistantService:
    def __init__(
        self,
        *,
        store: AssistantSessionStore,
        policy_service: AssistantPolicyService,
        provider_registry: AssistantProviderRegistry,
        tool_services: ToolExecutionServices,
        assistant_ws_events: Any,
        hybrid_command_router_enabled: bool = True,
        legacy_parser_enabled: bool = False,
        action_router_spec_path: str | None = None,
        deterministic_agent_substeps_enabled: bool = False,
        prompt_segmentation_enabled: bool = True,
        prompt_segmentation_model: str = "en_core_web_sm",
        prompt_classification_contract_enabled: bool = True,
        turn_execution_plan_contract_enabled: bool = True,
        segment_state_merge_policy_enabled: bool = True,
        argument_repair_enabled: bool = True,
        success_semantics_policy_enabled: bool = True,
        observability_contract_enabled: bool = True,
        create_product_recipe_catalog_enabled: bool = True,
    ) -> None:
        self._store = store
        self._policy = policy_service
        self._providers = provider_registry
        self._tool_services = tool_services
        self._assistant_ws_events = assistant_ws_events
        # Legacy constructor toggles are intentionally ignored per ADR.0054.
        _ = (
            hybrid_command_router_enabled,
            legacy_parser_enabled,
            deterministic_agent_substeps_enabled,
            prompt_segmentation_model,
            create_product_recipe_catalog_enabled,
        )
        self._command_router = HybridCommandRouter(
            enabled=True,
            spec_path=action_router_spec_path,
            enable_agent_substeps=False,
        )
        self._prompt_segmentation_enabled = bool(prompt_segmentation_enabled)
        self._prompt_classification_contract_enabled = bool(prompt_classification_contract_enabled)
        self._turn_execution_plan_contract_enabled = bool(turn_execution_plan_contract_enabled)
        self._segment_state_merge_policy_enabled = True
        self._argument_repair_enabled = True
        self._success_semantics_policy_enabled = True
        self._observability_contract_enabled = True
        self._prompt_segmenter = PromptSegmenter(model_name="en_core_web_sm")
        self._prompt_classifier = PromptClassifier()
        self._intent_to_tool_planner = IntentToToolPlanner()
        self._verb_normalizer = VerbNormalizer(spec_path=action_router_spec_path)
        self._entity_resolver = EntityReferenceResolver(
            tool_services=tool_services,
            scenario_directory_resolver=self._resolve_active_scenario_directory,
            verb_normalizer=self._verb_normalizer,
        )
        self._entity_kind_routing_rules = load_entity_kind_routing_rules(spec_path=action_router_spec_path)
        app_cfg = load_app_config(strict=False)
        backend_cfg = app_cfg.get("backend", {}) if isinstance(app_cfg, dict) else {}
        assistant_cfg = backend_cfg.get("assistant", {}) if isinstance(backend_cfg, dict) else {}
        semantic_cfg = assistant_cfg.get("semantic_intent_families", {}) if isinstance(assistant_cfg, dict) else {}
        self._semantic_intent_families_enabled = bool(semantic_cfg.get("enabled", True))
        configured_families = semantic_cfg.get("enabled_families")
        if isinstance(configured_families, list):
            self._enabled_intent_families = {
                str(item).strip()
                for item in configured_families
                if str(item).strip()
            }
        else:
            self._enabled_intent_families = set(INTENT_TO_TOOL_PLANNABLE_FAMILIES)
        self._create_product_planner = CreateProductPlanner()
        self._turn_execution_plan = TurnExecutionPlanBuilder()
        self._turn_state_manager = TurnStateManager()
        self._argument_repairer = ToolArgumentRepairer(enabled=True, max_repairs_per_call=1)
        self._runtime_state: dict[str, SessionRuntimeState] = {}
        self._turn_hybrid_context: dict[str, TurnHybridContext] = {}
        self._idle_cleanup_thread: threading.Thread | None = None
        self._idle_cleanup_lock = threading.Lock()
        self._idle_cleanup_stop = threading.Event()

    def create_session(self, request: CreateAssistantSessionRequest) -> AssistantSession:
        session = self._store.create_session(request.title.strip())
        self._runtime_state[session.session_id] = SessionRuntimeState()
        return session

    def list_sessions(self) -> ListAssistantSessionsResponse:
        return ListAssistantSessionsResponse(sessions=self._store.list_sessions())

    def get_session_detail(self, session_id: str) -> AssistantSessionDetailResponse:
        session = self._store.get_session(session_id)
        messages = self._store.list_messages(session_id, limit=120)
        return AssistantSessionDetailResponse(session=session, recent_messages=messages)

    def list_messages(self, session_id: str) -> ListAssistantMessagesResponse:
        self._store.get_session(session_id)
        return ListAssistantMessagesResponse(messages=self._store.list_messages(session_id))

    def list_bug_reports(self) -> ListAssistantBugReportsResponse:
        workspace_root = Path(str(getattr(self._tool_services.stores, "workspace_root", "")).strip() or ".").resolve()
        return ListAssistantBugReportsResponse(bug_reports=list_bug_report_summaries(workspace_root))

    def get_bug_report(self, bug_report_id: str) -> AssistantBugReportResponse:
        workspace_root = Path(str(getattr(self._tool_services.stores, "workspace_root", "")).strip() or ".").resolve()
        bug_report = load_bug_report_bundle(workspace_root, bug_report_id)
        bundle_path = bug_report_dir(workspace_root, bug_report_id) / "bug-report.json"
        return AssistantBugReportResponse(bug_report=bug_report, bundle_path=str(bundle_path))

    def capture_bug_report(
        self,
        session_id: str,
        request: AssistantBugReportRequest,
    ) -> AssistantBugReportResponse:
        workspace_root = Path(str(getattr(self._tool_services.stores, "workspace_root", "")).strip() or ".").resolve()
        bug_report = build_bug_report_bundle(
            workspace_root=workspace_root,
            session_store=self._store,
            session_id=session_id,
            report_text=request.report_text,
            program_state=request.program_state,
            turn_id=request.program_state.active_assistant_turn_id,
        )
        bundle_path = save_bug_report_bundle(workspace_root, bug_report)
        logger.info(
            "assistant bug report captured session_id=%s bug_report_id=%s bundle_path=%s",
            session_id,
            bug_report.bug_report_id,
            bundle_path,
        )
        return AssistantBugReportResponse(bug_report=bug_report, bundle_path=str(bundle_path))

    def _runtime(self, session_id: str) -> SessionRuntimeState:
        return self._runtime_state.setdefault(session_id, SessionRuntimeState())

    def _maybe_isolate_provider_switch(self, *, session_id: str, selected_provider_id: str | None) -> None:
        runtime = self._runtime(session_id)
        selected = str(selected_provider_id or "").strip() or None
        previous = str(runtime.last_provider_id or "").strip() or None
        if selected is None:
            return
        if previous is None:
            runtime.last_provider_id = selected
            return
        if previous == selected:
            return

        logger.info(
            "assistant provider switch isolation session_id=%s from_provider=%s to_provider=%s",
            session_id,
            previous,
            selected,
        )
        compacted_ok = False
        try:
            compacted = self.compact_session(
                session_id,
                CompactAssistantSessionRequest(max_messages_to_compact=80),
            )
            compacted_ok = True
            logger.info(
                "assistant provider switch compacted session_id=%s compacted_messages=%s summary_message_id=%s",
                session_id,
                compacted.compacted_message_count,
                compacted.summary_message_id,
            )
        except Exception as exc:
            logger.warning("assistant provider switch compaction failed session_id=%s error=%s", session_id, exc)
        if not compacted_ok:
            try:
                self._providers.reset_session(session_id)
            except Exception as exc:
                logger.warning("assistant provider switch external reset failed session_id=%s error=%s", session_id, exc)
        self._store.add_message(
            session_id=session_id,
            role=AssistantRole.SYSTEM,
            content=f"Provider switched from {previous} to {selected}. Session context was compacted and reset.",
            metadata={"kind": "provider_switch", "from_provider": previous, "to_provider": selected},
        )
        runtime.last_provider_id = selected

    def update_policy(self, session_id: str, request: UpdateAssistantPolicyRequest):
        session = self._store.get_session(session_id)
        next_policy = session.policy.model_copy(
            update={"always_allow_action_types": list(request.always_allow_action_types)}
        )
        return self._store.update_policy(session_id, next_policy)

    def provider_catalog(self):
        try:
            self._ensure_provider_registry_initialized()
        except AssistantProviderInitializationError as exc:
            raise ApiError(
                status_code=503,
                code="assistant_provider_initialization_failed",
                message=str(exc),
                details={"component": "assistant_provider_registry"},
            ) from exc
        return self._providers.catalog()

    def start_idle_cleanup_task(self, interval_seconds: float = 60.0) -> None:
        interval = max(1.0, float(interval_seconds))
        with self._idle_cleanup_lock:
            if self._idle_cleanup_thread is not None and self._idle_cleanup_thread.is_alive():
                return
            self._idle_cleanup_stop.clear()

            def _cleanup_loop() -> None:
                logger.info("assistant idle cleanup loop started interval_seconds=%s", interval)
                while not self._idle_cleanup_stop.is_set():
                    try:
                        self._providers.cleanup_idle_processes()
                    except Exception as exc:
                        logger.warning("assistant idle cleanup failed: %s", exc)
                    self._idle_cleanup_stop.wait(interval)

            self._idle_cleanup_thread = threading.Thread(
                target=_cleanup_loop,
                name="assistant-idle-cleanup",
                daemon=True,
            )
            self._idle_cleanup_thread.start()

    def refresh_rag_indexes_on_startup(self) -> None:
        refresh = getattr(self._providers, "refresh_rag_indexes_on_startup", None)
        if not callable(refresh):
            return
        try:
            refresh()
        except Exception as exc:
            logger.warning("assistant rag startup refresh failed: %s", exc)

    def shutdown(self) -> None:
        self._idle_cleanup_stop.set()
        thread = self._idle_cleanup_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        try:
            self._providers.shutdown()
        except Exception as exc:
            logger.warning("assistant provider shutdown failed: %s", exc)

    def _ensure_provider_registry_initialized(self) -> None:
        ensure_initialized = getattr(self._providers, "ensure_initialized", None)
        if not callable(ensure_initialized):
            return
        ensure_initialized()

    def _create_initialization_failure_response(
        self,
        *,
        session_id: str,
        prompt: str,
        request: CreateAssistantTurnRequest,
        resolved_scenario_id: str | None,
        telemetry: TurnTelemetry,
        error: Exception,
    ) -> CreateAssistantTurnResponse:
        user_metadata: dict[str, Any] = {}
        if resolved_scenario_id:
            user_metadata["scenario_id"] = resolved_scenario_id
        if request.constraints is not None:
            user_metadata["constraints"] = str(request.constraints).strip()
        if request.base_layer_visible is not None:
            user_metadata["base_layer_visible"] = bool(request.base_layer_visible)
        if request.access_mode is not None:
            user_metadata["access_mode"] = request.access_mode.value
        user_message = self._store.add_message(
            session_id=session_id,
            role=AssistantRole.USER,
            content=prompt,
            metadata=user_metadata,
        )
        turn = self._store.create_turn(
            session_id=session_id,
            user_message_id=user_message.message_id,
            provider_id=request.provider_id,
            model_id=request.model_id,
        )
        usage = self._build_usage(
            provider_id=request.provider_id,
            model_id=request.model_id,
            turn_handling_mode="assistant_initialization_failed",
            cache_attempted=False,
            cache_applied=False,
            completion_usage={},
            tool_call_count=0,
            telemetry=telemetry,
        )
        failed_turn = self._store.update_turn(
            turn.turn_id,
            status=AssistantTurnStatus.FAILED,
            error=str(error),
            usage=usage,
        )
        self._emit_event(
            AssistantEventName.ERROR,
            session_id=session_id,
            turn_id=turn.turn_id,
            data={"error": str(error)},
        )
        return CreateAssistantTurnResponse(
            turn=failed_turn,
            assistant_message=None,
            confirmation=None,
            tool_calls=[],
        )

    def create_turn(self, session_id: str, request: CreateAssistantTurnRequest) -> CreateAssistantTurnResponse:
        session = self._store.get_session(session_id)
        runtime = self._runtime(session_id)
        resolved_scenario_id = request.scenario_id or runtime.current_scenario_id
        if request.scenario_id:
            runtime.current_scenario_id = request.scenario_id
        if request.base_layer_visible is not None:
            runtime.base_layer_visible = bool(request.base_layer_visible)
        if request.constraints is not None:
            runtime.constraints_text = str(request.constraints).strip()
        prompt = request.prompt.strip()
        auto_confirmation = self._maybe_resolve_pending_confirmation_from_prompt(
            session_id=session_id,
            prompt=prompt,
        )
        if auto_confirmation is not None:
            return auto_confirmation
        logger.info(
            "assistant turn prompt\n"
            "  session_id=%s\n"
            "  scenario_id=%s\n"
            "  prompt=%s",
            session_id,
            resolved_scenario_id or "",
            self._json_for_log(prompt),
        )
        is_command_turn = self._is_command_prompt(prompt)
        requested_access_mode = request.access_mode.value if request.access_mode is not None else None
        explicit_tool_calls = self._parse_explicit_tool_call_lines(prompt)
        action_plan = (
            CommandPlan(actions=[], unmatched_segments=[])
            if explicit_tool_calls
            else self._command_router.plan(prompt=prompt, scenario_id=resolved_scenario_id)
        )
        if action_plan.actions:
            logger.info(
                "assistant action planner result\n"
                "  session_id=%s\n"
                "  action_count=%s\n"
                "  actions=%s\n"
                "  unmatched=%s",
                session_id,
                len(action_plan.actions),
                self._json_for_log([action.action_id for action in action_plan.actions]),
                self._json_for_log(action_plan.unmatched_segments),
            )
        tool_name: str | None = None
        tool_args: dict[str, Any] = {}
        fast_path_hint = ""
        if action_plan.is_fully_matched:
            fast_path_hint = f"action_plan:{len(action_plan.actions)}"
        elif action_plan.actions:
            fast_path_hint = f"action_plan_partial:{len(action_plan.actions)}"
        elif tool_name:
            fast_path_hint = tool_name
        explicit_tool_sequence_supported = bool(
            explicit_tool_calls
            and all(action_type_for_tool(explicit_tool_name) is None for explicit_tool_name, _raw in explicit_tool_calls)
        )
        provider_selection_required = self._provider_selection_needed_for_turn(
            action_plan=action_plan,
            tool_name=tool_name,
            explicit_tool_sequence_supported=explicit_tool_sequence_supported,
        )
        selected_provider_id = request.provider_id
        selected_model_id = request.model_id
        selected_execution_mode = "tool_loop"
        selected_thinking: bool | str | None = None
        provider_selection_applied = False
        telemetry = TurnTelemetry(started_at=time.perf_counter())
        if provider_selection_required:
            try:
                self._ensure_provider_registry_initialized()
                selection = self._providers.select_for_prompt(
                    provider_id=request.provider_id,
                    model_id=request.model_id,
                    is_command_turn=is_command_turn,
                )
                selected_provider_id = selection.provider_id
                selected_model_id = selection.model_id
                selected_execution_mode = selection.execution_mode
                provider_selection_applied = True
                normalize_thinking = getattr(self._providers, "normalize_thinking_setting", None)
                if callable(normalize_thinking):
                    selected_thinking = normalize_thinking(
                        provider_id=selected_provider_id,
                        model_id=selected_model_id,
                        thinking=request.thinking,
                    )
            except AssistantProviderInitializationError as exc:
                return self._return_with_turn_path_log(
                    session_id=session_id,
                    response=self._create_initialization_failure_response(
                        session_id=session_id,
                        prompt=prompt,
                        request=request,
                        resolved_scenario_id=resolved_scenario_id,
                        telemetry=telemetry,
                        error=exc,
                    ),
                )
            except RuntimeError:
                if (
                    not action_plan.actions
                    and tool_name is None
                    and not explicit_tool_calls
                ):
                    fallback_tool, fallback_args = self._plan_tool_call(
                        prompt=prompt,
                        scenario_id=resolved_scenario_id,
                    )
                    if fallback_tool is None:
                        raise
                    tool_name, tool_args = fallback_tool, fallback_args
                    provider_selection_required = False
                elif self._provider_selection_is_optional_for_turn(
                    action_plan=action_plan,
                    tool_name=tool_name,
                    explicit_tool_sequence_supported=explicit_tool_sequence_supported,
                ):
                    provider_selection_required = False
                else:
                    raise

        if provider_selection_applied:
            self._maybe_isolate_provider_switch(session_id=session_id, selected_provider_id=selected_provider_id)
        user_metadata: dict[str, Any] = {}
        if resolved_scenario_id:
            user_metadata["scenario_id"] = resolved_scenario_id
        if request.base_layer_visible is not None:
            user_metadata["base_layer_visible"] = bool(request.base_layer_visible)
        if request.constraints is not None:
            user_metadata["constraints"] = str(request.constraints).strip()
        if requested_access_mode:
            user_metadata["access_mode"] = requested_access_mode
        if selected_thinking is None:
            selected_thinking = self._coerce_requested_thinking_metadata(request.thinking)
        if selected_thinking is not None:
            user_metadata["thinking"] = selected_thinking
        user_message = self._store.add_message(
            session_id=session_id,
            role=AssistantRole.USER,
            content=prompt,
            metadata=user_metadata,
        )
        turn = self._store.create_turn(
            session_id=session_id,
            user_message_id=user_message.message_id,
            provider_id=selected_provider_id,
            model_id=selected_model_id,
        )
        logger.info(
            "assistant turn planned\n"
            "  session_id=%s\n"
            "  turn_id=%s\n"
            "  request_provider=%s\n"
            "  request_model=%s\n"
            "  selected_provider=%s\n"
            "  selected_model=%s\n"
            "  execution_mode=%s\n"
            "  is_command_turn=%s\n"
            "  tool_fast_path=%s\n"
            "  scenario_id=%s\n"
            "  thinking=%s",
            session_id,
            turn.turn_id,
            request.provider_id,
            request.model_id,
            selected_provider_id,
            selected_model_id,
            selected_execution_mode,
            is_command_turn,
            fast_path_hint,
            resolved_scenario_id or "",
            selected_thinking,
        )
        logger.info(
            "assistant turn started session_id=%s turn_id=%s scenario_id=%s provider=%s model=%s prompt=%s",
            session_id,
            turn.turn_id,
            resolved_scenario_id or "",
            selected_provider_id or "",
            selected_model_id or "",
            self._json_for_log(prompt),
        )
        turn = self._store.update_turn(turn.turn_id, status=AssistantTurnStatus.RUNNING)
        self._emit_event(
            AssistantEventName.TURN_STARTED,
            session_id=session_id,
            turn_id=turn.turn_id,
            data={"message_id": user_message.message_id},
        )
        self._initialize_turn_hybrid_context(
            turn_id=turn.turn_id,
            session_id=session_id,
            prompt=prompt,
            resolved_scenario_id=resolved_scenario_id,
        )

        try:
            if (
                selected_execution_mode != "external_mcp_agent"
                and (
                    self._ordered_turn_contains_create_product_segment(turn.turn_id)
                    or self._ordered_turn_contains_intent_family_segment(turn.turn_id)
                )
            ):
                ordered_deterministic_response = self._maybe_execute_ordered_deterministic_segments_turn(
                    session_id=session_id,
                    turn_id=turn.turn_id,
                    resolved_scenario_id=resolved_scenario_id,
                    provider_id=selected_provider_id or "local",
                    model_id=selected_model_id or "",
                    execution_mode=selected_execution_mode,
                    telemetry=telemetry,
                    policy=session.policy,
                    thinking=selected_thinking,
                )
                if ordered_deterministic_response is not None:
                    logger.info(
                        "assistant turn dispatch ordered_deterministic_segments session_id=%s turn_id=%s provider=%s model=%s",
                        session_id,
                        turn.turn_id,
                        selected_provider_id,
                        selected_model_id,
                    )
                    return self._return_with_turn_path_log(
                        session_id=session_id,
                        response=ordered_deterministic_response,
                    )
            if selected_execution_mode == "external_mcp_agent":
                logger.info(
                    "assistant turn dispatch external_agent session_id=%s turn_id=%s provider=%s model=%s",
                    session_id,
                    turn.turn_id,
                    selected_provider_id,
                    selected_model_id,
                )
                return self._return_with_turn_path_log(
                    session_id=session_id,
                    response=self._run_external_agent_turn(
                        session_id=session_id,
                        turn_id=turn.turn_id,
                        prompt=prompt,
                        scenario_id=resolved_scenario_id,
                        provider_id=selected_provider_id or "",
                        model_id=selected_model_id or "",
                        telemetry=telemetry,
                        is_command_turn=is_command_turn,
                        access_mode=requested_access_mode,
                        thinking=selected_thinking,
                    ),
                )
            if action_plan.is_fully_matched:
                logger.info(
                    "assistant turn dispatch action_plan_fast_path\n"
                    "  session_id=%s\n"
                    "  turn_id=%s\n"
                    "  provider=%s\n"
                    "  model=%s\n"
                    "  action_count=%s\n"
                    "  actions=%s",
                    session_id,
                    turn.turn_id,
                    selected_provider_id,
                    selected_model_id,
                    len(action_plan.actions),
                    self._json_for_log([action.action_id for action in action_plan.actions]),
                )
                return self._return_with_turn_path_log(
                    session_id=session_id,
                    response=self._execute_action_plan_turn(
                        session_id=session_id,
                        turn_id=turn.turn_id,
                        actions=action_plan.actions,
                        resolved_scenario_id=resolved_scenario_id,
                        policy=session.policy,
                        provider_id=selected_provider_id or "local",
                        model_id=selected_model_id or "",
                        telemetry=telemetry,
                        thinking=selected_thinking,
                    ),
                )
            if action_plan.actions:
                logger.info(
                    "assistant turn dispatch action_plan_partial session_id=%s turn_id=%s provider=%s model=%s action_count=%s unmatched=%s",
                    session_id,
                    turn.turn_id,
                    selected_provider_id,
                    selected_model_id,
                    len(action_plan.actions),
                    action_plan.unmatched_segments,
                )
                followup_prompt = self._build_partial_followup_prompt(action_plan)
                can_continue_with_model = bool(
                    followup_prompt
                    and selected_provider_id
                    and selected_model_id
                    and selected_execution_mode == "tool_loop"
                )
                return self._return_with_turn_path_log(
                    session_id=session_id,
                    response=self._execute_action_plan_turn(
                        session_id=session_id,
                        turn_id=turn.turn_id,
                        actions=action_plan.actions,
                        resolved_scenario_id=resolved_scenario_id,
                        policy=session.policy,
                        provider_id=selected_provider_id or "local",
                        model_id=selected_model_id or "",
                        telemetry=telemetry,
                        thinking=selected_thinking,
                        followup_prompt=followup_prompt if can_continue_with_model else None,
                    ),
                )
            if tool_name:
                logger.info(
                    "assistant turn dispatch parser_fast_path session_id=%s turn_id=%s provider=%s model=%s tool=%s",
                    session_id,
                    turn.turn_id,
                    selected_provider_id,
                    selected_model_id,
                    tool_name,
                )
                return self._return_with_turn_path_log(
                    session_id=session_id,
                    response=self._execute_single_tool_turn(
                        session_id=session_id,
                        turn_id=turn.turn_id,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        resolved_scenario_id=resolved_scenario_id,
                        policy=session.policy,
                        turn_handling_mode="parser_fast_path",
                        provider_id=selected_provider_id or "local",
                        model_id=selected_model_id or "",
                        telemetry=telemetry,
                        thinking=selected_thinking,
                    ),
                )

            if explicit_tool_sequence_supported:
                logger.info(
                    "assistant turn dispatch explicit_tool_sequence session_id=%s turn_id=%s provider=%s model=%s call_count=%s",
                    session_id,
                    turn.turn_id,
                    selected_provider_id,
                    selected_model_id,
                    len(explicit_tool_calls),
                )
                return self._return_with_turn_path_log(
                    session_id=session_id,
                    response=self._execute_explicit_tool_sequence_turn(
                        session_id=session_id,
                        turn_id=turn.turn_id,
                        parsed_calls=explicit_tool_calls,
                        resolved_scenario_id=resolved_scenario_id,
                        provider_id=selected_provider_id or "local",
                        model_id=selected_model_id or "",
                        telemetry=telemetry,
                    ),
                )

            ordered_deterministic_response = self._maybe_execute_ordered_deterministic_segments_turn(
                session_id=session_id,
                turn_id=turn.turn_id,
                resolved_scenario_id=resolved_scenario_id,
                provider_id=selected_provider_id or "local",
                model_id=selected_model_id or "",
                execution_mode=selected_execution_mode,
                telemetry=telemetry,
                policy=session.policy,
                thinking=selected_thinking,
            )
            if ordered_deterministic_response is not None:
                logger.info(
                    "assistant turn dispatch ordered_deterministic_segments session_id=%s turn_id=%s provider=%s model=%s",
                    session_id,
                    turn.turn_id,
                    selected_provider_id,
                    selected_model_id,
                )
                return self._return_with_turn_path_log(
                    session_id=session_id,
                    response=ordered_deterministic_response,
                )

            logger.info(
                "assistant turn dispatch model_tool_loop session_id=%s turn_id=%s provider=%s model=%s",
                session_id,
                turn.turn_id,
                selected_provider_id,
                selected_model_id,
            )
            return self._return_with_turn_path_log(
                session_id=session_id,
                response=self._run_model_tool_loop(
                    session_id=session_id,
                    turn_id=turn.turn_id,
                    prompt=prompt,
                    scenario_id=resolved_scenario_id,
                    provider_id=selected_provider_id or "",
                    model_id=selected_model_id or "",
                    policy=session.policy,
                    telemetry=telemetry,
                    is_command_turn=is_command_turn,
                    thinking=selected_thinking,
                ),
            )
        except Exception as exc:
            usage = self._build_usage(
                provider_id=selected_provider_id,
                model_id=selected_model_id,
                turn_handling_mode=(
                    "external_mcp_agent"
                    if selected_execution_mode == "external_mcp_agent"
                    else (
                        "parser_fast_path"
                        if tool_name
                        else ("explicit_tool_sequence" if explicit_tool_calls else "model_tool_loop")
                    )
                ),
                cache_attempted=False,
                cache_applied=False,
                completion_usage={},
                tool_call_count=len(self._store.list_turn_tool_calls(turn.turn_id)),
                telemetry=telemetry,
            )
            failed_turn = self._store.update_turn(
                turn.turn_id,
                status=AssistantTurnStatus.FAILED,
                error=str(exc),
                usage=usage,
            )
            self._emit_event(
                AssistantEventName.ERROR,
                session_id=session_id,
                turn_id=turn.turn_id,
                data={"error": str(exc)},
            )
            return self._return_with_turn_path_log(
                session_id=session_id,
                response=CreateAssistantTurnResponse(
                    turn=failed_turn,
                    assistant_message=None,
                    confirmation=None,
                    tool_calls=self._store.list_turn_tool_calls(turn.turn_id),
                ),
            )

    def resolve_confirmation(
        self,
        session_id: str,
        confirmation_id: str,
        request: AssistantConfirmationDecisionRequest,
    ) -> AssistantConfirmationDecisionResponse:
        session = self._store.get_session(session_id)
        confirmation = self._store.get_confirmation(confirmation_id)
        if confirmation.session_id != session_id:
            raise KeyError(f"Confirmation not found in session: {confirmation_id}")
        turn = self._store.get_turn(confirmation.turn_id)
        if confirmation.status != "pending":
            return self._return_with_turn_path_log(
                session_id=session_id,
                response=AssistantConfirmationDecisionResponse(
                    confirmation=confirmation,
                    turn=turn,
                    assistant_message=None,
                    tool_calls=self._store.list_turn_tool_calls(turn.turn_id),
                ),
            )

        if request.decision == AssistantConfirmationDecision.DENY_ONCE:
            confirmation = self._store.resolve_confirmation(
                confirmation_id,
                decision=request.decision,
                status="denied",
            )
            denial_message = self._store.add_message(
                session_id=session_id,
                role=AssistantRole.ASSISTANT,
                turn_id=turn.turn_id,
                content=(
                    f"Action denied for `{confirmation.tool_name}` "
                    f"({confirmation.action_type.value}). No changes were made."
                ),
                metadata={"confirmation_id": confirmation.confirmation_id},
            )
            turn = self._store.update_turn(turn.turn_id, status=AssistantTurnStatus.COMPLETED)
            self._emit_event(
                AssistantEventName.CONFIRMATION_RESOLVED,
                session_id=session_id,
                turn_id=turn.turn_id,
                data=confirmation.model_dump(mode="json"),
            )
            self._emit_event(
                AssistantEventName.TURN_COMPLETED,
                session_id=session_id,
                turn_id=turn.turn_id,
                data={"status": turn.status.value},
            )
            return self._return_with_turn_path_log(
                session_id=session_id,
                response=AssistantConfirmationDecisionResponse(
                    confirmation=confirmation,
                    turn=turn,
                    assistant_message=denial_message,
                    tool_calls=self._store.list_turn_tool_calls(turn.turn_id),
                ),
            )

        if request.decision == AssistantConfirmationDecision.ALWAYS_ALLOW_ACTION_TYPE:
            if confirmation.tool_name in {"scenario.run_script", "scenario.run_marimo_notebook"}:
                script_key = self._script_key_from_arguments(confirmation.arguments)
                if script_key:
                    self._runtime(session_id).script_run_allowlist.add(script_key)
            elif confirmation.tool_name == "scenario.write_run_script":
                script_key = self._script_key_from_arguments(confirmation.arguments)
                if script_key:
                    if confirmation.action_type == AssistantConfirmationActionType.WRITE_NOTEBOOK:
                        self._runtime(session_id).overwrite_allowlist.add(script_key)
                    else:
                        self._runtime(session_id).script_run_allowlist.add(script_key)
            elif confirmation.tool_name == "scenario.write_script":
                script_key = self._script_key_from_arguments(confirmation.arguments)
                if script_key:
                    self._runtime(session_id).overwrite_allowlist.add(script_key)
            else:
                next_policy = self._policy.apply_decision(
                    action_type=confirmation.action_type,
                    decision=request.decision,
                    policy=session.policy,
                )
                self._store.update_policy(session_id, next_policy)
        elif confirmation.tool_name in {"scenario.write_script", "scenario.write_run_script"}:
            script_key = self._script_key_from_arguments(confirmation.arguments)
            if script_key:
                if confirmation.action_type == AssistantConfirmationActionType.WRITE_NOTEBOOK:
                    # First approved overwrite is remembered for the rest of the session.
                    self._runtime(session_id).overwrite_allowlist.add(script_key)

        confirmation = self._store.resolve_confirmation(
            confirmation_id,
            decision=request.decision,
            status="approved",
        )
        approved_arguments = self._apply_confirmation_argument_overrides(
            tool_name=confirmation.tool_name,
            arguments=confirmation.arguments,
        )
        pending_calls = self._store.list_turn_tool_calls(turn.turn_id)
        pending_call = next(
            (item for item in pending_calls if item.tool_name == confirmation.tool_name and item.status == "confirmation_required"),
            None,
        )
        if pending_call is None:
            pending_call = self._store.create_tool_call(
                session_id=session_id,
                turn_id=turn.turn_id,
                tool_name=confirmation.tool_name,
                arguments=confirmation.arguments,
                status="started",
                action_type=confirmation.action_type,
            )
        else:
            pending_call = self._store.complete_tool_call(
                pending_call.tool_call_id,
                status="started",
                result={},
            )
        turn_handling_mode = str(turn.usage.get("turn_handling_mode", "parser_fast_path"))
        if turn_handling_mode == "model_tool_loop" or (
            turn_handling_mode == "ordered_segment_execution"
            and self._ordered_turn_contains_other_segment(turn.turn_id)
        ):
            response = self._resume_confirmed_model_tool_loop(
                session_id=session_id,
                turn=turn,
                tool_call=pending_call,
                execution_arguments=approved_arguments,
                policy=session.policy,
            )
        else:
            history = self._store.list_messages(session_id, limit=400)
            user_message = next((item for item in history if item.message_id == turn.user_message_id), None)
            thinking = user_message.metadata.get("thinking") if user_message is not None else None
            response = self._execute_tool_for_turn(
                session_id=session_id,
                turn_id=turn.turn_id,
                tool_call=pending_call,
                execution_arguments=approved_arguments,
                turn_handling_mode=turn_handling_mode,
                provider_id=turn.provider_id,
                model_id=turn.model_id,
                thinking=thinking if isinstance(thinking, (bool, str)) else None,
            )
        self._emit_event(
            AssistantEventName.CONFIRMATION_RESOLVED,
            session_id=session_id,
            turn_id=turn.turn_id,
            data=confirmation.model_dump(mode="json"),
        )
        return self._return_with_turn_path_log(
            session_id=session_id,
            response=AssistantConfirmationDecisionResponse(
                confirmation=confirmation,
                turn=response.turn,
                assistant_message=response.assistant_message,
                tool_calls=response.tool_calls,
            ),
        )

    def _resume_confirmed_model_tool_loop(
        self,
        *,
        session_id: str,
        turn: Any,
        tool_call: AssistantToolCall,
        execution_arguments: dict[str, Any] | None,
        policy: Any,
    ) -> CreateAssistantTurnResponse:
        telemetry = TurnTelemetry(started_at=time.perf_counter())
        resume_failures: list[dict[str, Any]] = []
        resume_calls: list[AssistantToolCall] = []
        failed_call_record: AssistantToolCall | None = None
        failure_message = ""
        execute_args = dict(execution_arguments) if execution_arguments is not None else dict(tool_call.arguments)
        try:
            completed_call, _result = self._execute_tool_call(
                session_id=session_id,
                turn_id=turn.turn_id,
                tool_call=tool_call,
                execution_arguments=execution_arguments,
                telemetry=telemetry,
            )
            resume_calls.append(completed_call)
        except Exception as exc:
            failed_call_record = self._store.complete_tool_call(
                tool_call.tool_call_id,
                status="failed",
                result={},
                error=str(exc),
            )
            failure_message = self._format_tool_execution_error_message(
                tool_name=tool_call.tool_name,
                arguments=execute_args,
                error=exc,
            )
            self._emit_event(
                AssistantEventName.ERROR,
                session_id=session_id,
                turn_id=turn.turn_id,
                data={
                    "error": str(exc),
                    "tool_call_id": failed_call_record.tool_call_id,
                    "assistant_message": failure_message,
                    "error_code": ERROR_TOOL_EXECUTION_FAILED,
                },
            )
            resume_failures.append(
                {
                    "tool_name": tool_call.tool_name,
                    "arguments": execute_args,
                    "error_type": "tool_execution_error",
                    "message": str(exc).strip() or failure_message,
                }
            )
        history = self._store.list_messages(session_id, limit=400)
        user_message = next((item for item in history if item.message_id == turn.user_message_id), None)
        if user_message is None:
            if resume_failures:
                usage = self._build_usage(
                    provider_id=turn.provider_id,
                    model_id=turn.model_id,
                    turn_handling_mode="model_tool_loop",
                    cache_attempted=False,
                    cache_applied=False,
                    completion_usage={},
                    tool_call_count=1,
                    telemetry=telemetry,
                )
                turn = self._store.update_turn(
                    turn.turn_id,
                    status=AssistantTurnStatus.FAILED,
                    error=str(resume_failures[0].get("message", "")).strip() or "Tool execution failed.",
                    usage=usage,
                )
                assistant_message = self._store.add_message(
                    session_id=session_id,
                    role=AssistantRole.ASSISTANT,
                    content=failure_message or str(resume_failures[0].get("message", "")).strip() or "Tool execution failed.",
                    turn_id=turn.turn_id,
                    metadata={
                        "provider_id": turn.provider_id,
                        "model_id": turn.model_id,
                        "usage": usage,
                        "execution_origin": "model_reasoned",
                        "tool_error": True,
                        "tool_name": str(resume_failures[0].get("tool_name", "")),
                    },
                )
                return CreateAssistantTurnResponse(
                    turn=turn,
                    assistant_message=assistant_message,
                    confirmation=None,
                    tool_calls=[failed_call_record] if failed_call_record is not None else [],
                )
            usage = self._build_usage(
                provider_id=turn.provider_id,
                model_id=turn.model_id,
                turn_handling_mode="model_tool_loop",
                cache_attempted=False,
                cache_applied=False,
                completion_usage={},
                tool_call_count=1,
                telemetry=telemetry,
            )
            assistant_message, updated_turn = self._finalize_turn_with_message(
                session_id=session_id,
                turn_id=turn.turn_id,
                text=summarize_tool_result(resume_calls[0].tool_name, resume_calls[0].result),
                usage=usage,
                metadata={"tool_call_id": resume_calls[0].tool_call_id},
                outputs=[item.model_dump(mode="json") for item in resume_calls[0].outputs],
                telemetry=telemetry,
            )
            return CreateAssistantTurnResponse(
                turn=updated_turn,
                assistant_message=assistant_message,
                confirmation=None,
                tool_calls=resume_calls,
            )
        scenario_id = str(user_message.metadata.get("scenario_id", "")).strip() or self._runtime(session_id).current_scenario_id
        thinking = user_message.metadata.get("thinking")
        resume_calls = [
            item for item in self._store.list_turn_tool_calls(turn.turn_id) if item.status == "completed"
        ]
        return self._run_model_tool_loop(
            session_id=session_id,
            turn_id=turn.turn_id,
            prompt=user_message.content,
            scenario_id=scenario_id,
            provider_id=str(turn.provider_id or ""),
            model_id=str(turn.model_id or ""),
            policy=policy,
            telemetry=telemetry,
            is_command_turn=self._is_command_prompt(user_message.content),
            resume_tool_calls=resume_calls,
            resume_tool_failures=resume_failures,
            thinking=thinking if isinstance(thinking, (bool, str)) else None,
        )

    def _ordered_turn_contains_other_segment(self, turn_id: str) -> bool:
        turn_ctx = self._turn_hybrid_context.get(turn_id)
        if turn_ctx is None:
            return False
        return any(item.segment_class == "other" for item in turn_ctx.classifications)

    def _ordered_turn_contains_create_product_segment(self, turn_id: str) -> bool:
        turn_ctx = self._turn_hybrid_context.get(turn_id)
        if turn_ctx is None:
            return False
        return any(item.segment_class == "create_product" for item in turn_ctx.classifications)

    def _ordered_turn_contains_intent_family_segment(self, turn_id: str) -> bool:
        turn_ctx = self._turn_hybrid_context.get(turn_id)
        if turn_ctx is None:
            return False
        return any(item.segment_class == "intent_family" for item in turn_ctx.classifications)

    def _segment_entity_resolution(self, *, turn_id: str, segment_id: str) -> SegmentEntityResolution | None:
        turn_ctx = self._turn_hybrid_context.get(turn_id)
        if turn_ctx is None:
            return None
        return turn_ctx.entity_resolution.get(segment_id)

    def _record_segment_dispatch(
        self,
        *,
        turn_id: str,
        segment_id: str,
        handler: str,
        dispatch_mode: str | None = None,
        reason: str | None = None,
        planned_tools: list[str] | None = None,
        action_ids: list[str] | None = None,
        continuation_handler: str | None = None,
        recipe_id: str | None = None,
        reuse_product_id: str | None = None,
    ) -> None:
        turn_ctx = self._turn_hybrid_context.get(turn_id)
        if turn_ctx is None:
            return
        dispatch = dict(turn_ctx.segment_dispatches.get(segment_id, {}))
        dispatch["handler"] = handler
        if dispatch_mode is not None:
            dispatch["dispatch_mode"] = dispatch_mode
        if reason is not None:
            dispatch["reason"] = reason
        if planned_tools is not None:
            dispatch["planned_tools"] = list(planned_tools)
        if action_ids is not None:
            dispatch["action_ids"] = list(action_ids)
        if continuation_handler is not None:
            dispatch["continuation_handler"] = continuation_handler
        if recipe_id is not None:
            dispatch["recipe_id"] = recipe_id
        if reuse_product_id is not None:
            dispatch["reuse_product_id"] = reuse_product_id
        dispatch.setdefault("executed_tools", [])
        turn_ctx.segment_dispatches[segment_id] = dispatch

    def _append_segment_executed_tool(self, *, turn_id: str, segment_id: str, tool_name: str) -> None:
        turn_ctx = self._turn_hybrid_context.get(turn_id)
        if turn_ctx is None:
            return
        dispatch = dict(turn_ctx.segment_dispatches.get(segment_id, {}))
        executed = list(dispatch.get("executed_tools", []))
        executed.append(tool_name)
        dispatch["executed_tools"] = executed
        turn_ctx.segment_dispatches[segment_id] = dispatch

    @staticmethod
    def _truncate_log_text(text: str, *, max_chars: int = 1200) -> str:
        cleaned = str(text or "").strip()
        if len(cleaned) <= max_chars:
            return cleaned
        return f"{cleaned[:max_chars]}... [truncated {len(cleaned) - max_chars} chars]"

    @staticmethod
    def _segment_classification_summary(classification: SegmentClassification | None) -> dict[str, Any]:
        if classification is None:
            return {
                "segment_class": "",
                "intent_family": "",
                "command": "",
                "operation": "",
            }
        return {
            "segment_class": str(classification.segment_class),
            "intent_family": str(classification.intent_family or ""),
            "command": str(classification.command or ""),
            "operation": str(classification.intent_properties.get("operation", "")).strip(),
            "classification_origin": str(classification.classification_origin or ""),
            "validation_status": str(classification.validation_status or ""),
            "requires_clarification": bool(classification.requires_clarification),
            "candidate_product_types": list(classification.candidate_product_types or []),
        }

    @staticmethod
    def _segment_resolution_summary(resolution: SegmentEntityResolution | None) -> dict[str, Any]:
        if resolution is None:
            return {}
        return resolution.as_dict()

    def _segment_tool_calls_for_log(self, tool_calls: list[AssistantToolCall]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for call in tool_calls:
            payload.append(
                {
                    "tool_name": str(call.tool_name or ""),
                    "status": str(call.status or ""),
                    "error": self._truncate_log_text(str(call.error or ""), max_chars=600) if call.error else "",
                    "result": compact_tool_result_for_model_context(call.tool_name, call.result),
                }
            )
        return payload

    def _segment_response_text_for_log(self, *, response_text: str, fallback_message: str) -> str:
        chosen = str(response_text or "").strip()
        if not chosen:
            chosen = str(fallback_message or "").strip()
        return self._truncate_log_text(chosen, max_chars=1200)

    @staticmethod
    def _segment_outcome_status_from_turn(turn: Any) -> str:
        status = str(getattr(getattr(turn, "status", None), "value", getattr(turn, "status", "")) or "").strip().lower()
        if status:
            return status
        return "unknown"

    @staticmethod
    def _is_terminal_turn_status(status: Any) -> bool:
        normalized = str(getattr(status, "value", status) or "").strip().lower()
        return normalized in {"completed", "failed"}

    def _classify_resolve_with_unified_deterministic_recognizer(
        self,
        *,
        segments: list[PromptSegment],
        scenario_id: str | None,
        constraints_text: str | None,
        known_products: list[str],
    ) -> tuple[list[SegmentClassification], dict[str, SegmentEntityResolution], dict[str, dict[str, Any]]]:
        provisional = self._prompt_classifier.classify(
            segments=segments,
            scenario_id=scenario_id,
            router=self._command_router,
            constraints_text=constraints_text,
            known_products=known_products,
            deterministic_command_classification_enabled=False,
        )
        resolution = self._entity_resolver.resolve_segments(
            classifications=provisional,
            scenario_id=scenario_id,
        )
        recognizer = UnifiedDeterministicRecognizer(
            command_router=self._command_router,
            entity_kind_rules=self._entity_kind_routing_rules,
        )
        promoted, traces, _fallback_segment_ids = recognizer.promote(
            classifications=provisional,
            resolutions=resolution,
            scenario_id=scenario_id,
        )

        trace_payload = {segment_id: trace.as_dict() for segment_id, trace in traces.items()}
        return promoted, resolution, trace_payload

    def _prompt_text_for_turn(self, turn: Any, *, turn_ctx: TurnHybridContext | None = None) -> str:
        if turn_ctx is not None and str(turn_ctx.prompt_text or "").strip():
            return str(turn_ctx.prompt_text or "")
        try:
            history = self._store.list_messages(turn.session_id, limit=400)
        except Exception:
            return ""
        user_message = next((item for item in history if item.message_id == turn.user_message_id), None)
        if user_message is None:
            return ""
        return str(user_message.content or "")

    def _log_segment_processing_summary(
        self,
        *,
        session_id: str,
        turn_id: str,
        segment: PromptSegment,
        status: str,
        response_text: str,
        segment_tool_calls: list[AssistantToolCall],
        error: str | None = None,
        elapsed_ms: int | None = None,
        source: str = "segment_execution",
    ) -> None:
        turn_ctx = self._turn_hybrid_context.get(turn_id)
        classification = None
        dispatch: dict[str, Any] = {}
        execution_mode = ""
        resolution = self._segment_entity_resolution(turn_id=turn_id, segment_id=segment.segment_id)
        if turn_ctx is not None:
            classifications = {item.segment_id: item for item in turn_ctx.classifications}
            classification = classifications.get(segment.segment_id)
            dispatch = dict(turn_ctx.segment_dispatches.get(segment.segment_id, {}))
            if turn_ctx.execution_plan is not None:
                plan_segment = next(
                    (item for item in turn_ctx.execution_plan.segments if item.segment_id == segment.segment_id),
                    None,
                )
                if plan_segment is not None:
                    execution_mode = str(plan_segment.execution_mode or "")
        response_summary = self._segment_response_text_for_log(
            response_text=response_text,
            fallback_message="",
        )
        summary: dict[str, Any] = {
            "source": source,
            "segment_id": segment.segment_id,
            "text": segment.text,
            "start_char": segment.start_char,
            "end_char": segment.end_char,
            "classification": self._segment_classification_summary(classification),
            "entity_resolution": self._segment_resolution_summary(resolution),
            "execution_mode": execution_mode,
            "dispatch": dispatch,
            "status": str(status or ""),
            "response": response_summary,
            "tool_calls": self._segment_tool_calls_for_log(segment_tool_calls),
            "error": self._truncate_log_text(str(error or ""), max_chars=1200) if error else "",
        }
        if elapsed_ms is not None:
            summary["elapsed_ms"] = int(elapsed_ms)
        logger.info(
            "assistant segment processed session_id=%s turn_id=%s segment_id=%s summary=%s",
            session_id,
            turn_id,
            segment.segment_id,
            self._json_for_log_pretty(summary),
        )
        if turn_ctx is not None:
            next_dispatch = dict(turn_ctx.segment_dispatches.get(segment.segment_id, {}))
            next_dispatch["outcome"] = {
                "status": str(status or ""),
                "error": str(error or ""),
                "response_excerpt": response_summary,
                "tool_call_count": len(segment_tool_calls),
                "source": source,
            }
            next_dispatch["outcome_logged"] = True
            turn_ctx.segment_dispatches[segment.segment_id] = next_dispatch

    def _ensure_all_segments_logged(self, *, session_id: str, response: Any) -> None:
        turn = getattr(response, "turn", None)
        if turn is None:
            return
        turn_ctx = self._turn_hybrid_context.get(turn.turn_id)
        if turn_ctx is None:
            return
        assistant_message = getattr(response, "assistant_message", None)
        default_response_text = str(getattr(assistant_message, "content", "") or "")
        all_tool_calls = list(getattr(response, "tool_calls", []) or [])
        outcome_status = self._segment_outcome_status_from_turn(turn)
        turn_error = str(getattr(turn, "error", "") or "").strip()
        for segment in turn_ctx.segments:
            dispatch = dict(turn_ctx.segment_dispatches.get(segment.segment_id, {}))
            if bool(dispatch.get("outcome_logged")):
                continue
            self._log_segment_processing_summary(
                session_id=session_id,
                turn_id=turn.turn_id,
                segment=segment,
                status=outcome_status,
                response_text=default_response_text,
                segment_tool_calls=all_tool_calls,
                error=turn_error if turn_error else None,
                source="turn_rollup",
            )

    def _log_turn_path_summary(self, *, session_id: str, turn: Any, tool_calls: list[AssistantToolCall]) -> None:
        turn_ctx = self._turn_hybrid_context.get(turn.turn_id)
        turn_status = str(getattr(turn.status, "value", str(turn.status)))
        turn_error = str(getattr(turn, "error", "") or "").strip()
        failed_tool_calls = [call for call in tool_calls if str(call.status or "").strip().lower() == "failed"]
        prompt_text = self._prompt_text_for_turn(turn, turn_ctx=turn_ctx)
        summary: dict[str, Any] = {
            "status": turn_status,
            "turn_handling_mode": str(getattr(turn, "usage", {}).get("turn_handling_mode", "")),
            "provider_id": turn.provider_id,
            "model_id": turn.model_id,
            "prompt": self._truncate_log_text(prompt_text, max_chars=2000),
            "tool_call_count": len(tool_calls),
            "failed_tool_call_count": len(failed_tool_calls),
            "error": self._truncate_log_text(turn_error, max_chars=1200) if turn_error else "",
            "has_errors": bool(turn_error or failed_tool_calls or turn_status.lower() == "failed"),
            "tool_calls": [
                {
                    "tool_name": call.tool_name,
                    "status": call.status,
                }
                for call in tool_calls
            ],
        }
        if turn_ctx is not None:
            classifications = {item.segment_id: item for item in turn_ctx.classifications}
            execution_plan = (
                {item.segment_id: item for item in turn_ctx.execution_plan.segments}
                if turn_ctx.execution_plan is not None
                else {}
            )
            summary["segments"] = []
            for segment in turn_ctx.segments:
                classification = classifications.get(segment.segment_id)
                dispatch = dict(turn_ctx.segment_dispatches.get(segment.segment_id, {}))
                plan_segment = execution_plan.get(segment.segment_id)
                outcome = dict(dispatch.get("outcome", {})) if isinstance(dispatch.get("outcome"), dict) else {}
                summary["segments"].append(
                    {
                        "segment_id": segment.segment_id,
                        "text": self._truncate_log_text(segment.text, max_chars=240),
                        "classification": self._segment_classification_summary(classification),
                        "execution_mode": str(plan_segment.execution_mode) if plan_segment is not None else "",
                        "dispatch_mode": str(dispatch.get("dispatch_mode", "") or ""),
                        "handler": str(dispatch.get("handler", "") or ""),
                        "status": str(outcome.get("status", "") or ""),
                        "error": self._truncate_log_text(str(outcome.get("error", "") or ""), max_chars=600),
                    }
                )
            summary["segment_count"] = len(turn_ctx.segments)
        logger.info(
            "assistant turn processing summary session_id=%s turn_id=%s summary=%s",
            session_id,
            turn.turn_id,
            self._json_for_log_pretty(summary),
        )

    def _return_with_turn_path_log(self, *, session_id: str, response: Any) -> Any:
        turn = getattr(response, "turn", None)
        turn_id = str(getattr(turn, "turn_id", "") or "")
        try:
            self._ensure_all_segments_logged(session_id=session_id, response=response)
            self._log_turn_path_summary(
                session_id=session_id,
                turn=response.turn,
                tool_calls=list(getattr(response, "tool_calls", []) or []),
            )
        except Exception as exc:
            logger.warning(
                "assistant turn path summary failed session_id=%s turn_id=%s error=%s",
                session_id,
                turn_id,
                exc,
            )
        finally:
            if turn is not None and turn_id and self._is_terminal_turn_status(getattr(turn, "status", None)):
                self._turn_hybrid_context.pop(turn_id, None)
        return response

    def _required_entity_kinds_for_dispatch(
        self,
        *,
        classification: SegmentClassification,
        canonical_operation: str | None,
    ) -> set[str]:
        if classification.segment_class == "command":
            # Command-router deterministic matching already performs pattern and context checks.
            # Keep entity-kind gating focused on intent-family dispatch to avoid duplicate blocking.
            return set()

        if classification.segment_class != "intent_family":
            return set()
        family = str(classification.intent_family or "").strip()
        op = str(canonical_operation or "").strip().lower()
        if self._entity_kind_routing_rules:
            required: set[str] = set()
            for rule in self._entity_kind_routing_rules:
                if op not in set(rule.required_verbs):
                    continue
                if rule.intent_families and family not in set(rule.intent_families):
                    continue
                required.update(set(rule.required_entity_kinds))
            if required:
                return required
        if family == "location_navigation":
            if op in {"goto", "search"}:
                return {"feature"}
            return set()
        if family == "scenario_context_management" and op in {"set_current"}:
            return {"scenario"}
        if family == "layer_visibility_update" and op in {"show", "hide"}:
            return {"layer"}
        if family == "layer_style_update" and op in {"apply"}:
            return {"layer", "colormap"}
        if family == "artifact_inspection":
            return set()
        return set()

    @staticmethod
    def _resolved_entity_kinds(resolution: SegmentEntityResolution | None) -> set[str]:
        if resolution is None:
            return set()
        resolved = {
            str(item.kind).strip()
            for item in resolution.mentions
            if str(item.kind).strip() and str(item.resolved_id or "").strip()
        }
        target_kind = str(getattr(resolution, "target_kind", "") or "").strip()
        target_resolved_id = str(getattr(resolution, "target_resolved_id", "") or "").strip()
        if target_kind and target_resolved_id:
            resolved.add(target_kind)
        return resolved

    def _deterministic_dispatch_block_reason(
        self,
        *,
        classification: SegmentClassification,
        resolution: SegmentEntityResolution | None,
    ) -> str | None:
        if resolution is None:
            return None
        if resolution.verb_normalization.ambiguous:
            chosen_operation = str(classification.intent_properties.get("operation", "")).strip().lower()
            operation_candidates = {
                str(item).strip().lower()
                for item in list(
                    resolution.verb_normalization.operation_candidates
                    or resolution.verb_normalization.candidates
                    or []
                )
                if str(item).strip()
            }
            if not chosen_operation or (operation_candidates and chosen_operation not in operation_candidates):
                return "ambiguous_operation"
        if str(resolution.target_kind or "").strip() == "ambiguous_layer_or_file":
            return "entity_ambiguity:layer_or_file"
        required_kinds = self._required_entity_kinds_for_dispatch(
            classification=classification,
            canonical_operation=resolution.canonical_operation,
        )
        if not required_kinds:
            return None
        resolved_kinds = self._resolved_entity_kinds(resolution)
        if required_kinds == {"layer", "colormap"} and "layer" in resolved_kinds:
            # Allow default-colormap behavior when only layer reference is explicit.
            return None
        missing = sorted([item for item in required_kinds if item not in resolved_kinds])
        if missing:
            return "missing_required_entity_kind:" + ",".join(missing)
        if resolution.ambiguities:
            return "entity_ambiguity"
        return None

    def compact_session(
        self,
        session_id: str,
        request: CompactAssistantSessionRequest,
    ) -> CompactAssistantSessionResponse:
        self._store.get_session(session_id)
        messages = self._store.list_messages(session_id, limit=max(1000, request.max_messages_to_compact + 40))
        summary, compacted_count = compact_messages(
            messages,
            max_messages_to_compact=request.max_messages_to_compact,
        )
        if compacted_count <= 0 or not summary.strip():
            return CompactAssistantSessionResponse(
                session_id=session_id,
                compacted_message_count=0,
                summary_message_id=None,
            )
        summary_msg = self._store.add_message(
            session_id=session_id,
            role=AssistantRole.SYSTEM,
            content=summary,
            metadata={"kind": "compaction_summary", "compacted_message_count": compacted_count},
        )
        self._store.record_compaction(
            session_id=session_id,
            summary_message_id=summary_msg.message_id,
            compacted_count=compacted_count,
        )
        try:
            self._providers.reset_session(session_id)
        except Exception as exc:
            logger.warning("assistant external-session reset failed session_id=%s error=%s", session_id, exc)
        return CompactAssistantSessionResponse(
            session_id=session_id,
            compacted_message_count=compacted_count,
            summary_message_id=summary_msg.message_id,
        )

    def _execute_tool_for_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_call: AssistantToolCall,
        execution_arguments: dict[str, Any] | None = None,
        turn_handling_mode: str = "parser_fast_path",
        provider_id: str | None = None,
        model_id: str | None = None,
        telemetry: TurnTelemetry | None = None,
        thinking: bool | str | None = None,
    ) -> CreateAssistantTurnResponse:
        if provider_id is None or model_id is None:
            turn_record = self._store.get_turn(turn_id)
            provider_id = provider_id or turn_record.provider_id
            model_id = model_id or turn_record.model_id
        telemetry = telemetry or TurnTelemetry(started_at=time.perf_counter())
        try:
            completed_call, result = self._execute_tool_call(
                session_id=session_id,
                turn_id=turn_id,
                tool_call=tool_call,
                execution_arguments=execution_arguments,
                telemetry=telemetry,
            )
            text = summarize_tool_result(completed_call.tool_name, result)
            completion_usage: dict[str, int] = {}
            cache_attempted = False
            cache_applied = False
            followup = self._parser_tool_followup_completion(
                turn_handling_mode=turn_handling_mode,
                provider_id=provider_id,
                model_id=model_id,
                tool_name=completed_call.tool_name,
                tool_result=result,
                thinking=thinking,
            )
            if followup is not None:
                completion_usage = dict(followup.usage)
                cache_attempted = bool(followup.cache_attempted)
                cache_applied = bool(followup.cache_applied)
                if followup.text.strip():
                    text = followup.text.strip()
            usage = self._build_usage(
                provider_id=provider_id,
                model_id=model_id,
                turn_handling_mode=turn_handling_mode,
                cache_attempted=cache_attempted,
                cache_applied=cache_applied,
                completion_usage=completion_usage,
                tool_call_count=1,
                telemetry=telemetry,
            )
            assistant_message, turn = self._finalize_turn_with_message(
                session_id=session_id,
                turn_id=turn_id,
                text=text,
                usage=usage,
                metadata={
                    "tool_call_id": completed_call.tool_call_id,
                    "execution_origin": "deterministic" if turn_handling_mode != "model_tool_loop" else "model_reasoned",
                },
                outputs=[item.model_dump(mode="json") for item in completed_call.outputs],
                telemetry=telemetry,
            )
            return CreateAssistantTurnResponse(
                turn=turn,
                assistant_message=assistant_message,
                confirmation=None,
                tool_calls=[completed_call],
            )
        except Exception as exc:
            failed_call = self._store.complete_tool_call(
                tool_call.tool_call_id,
                status="failed",
                result={},
                error=str(exc),
            )
            usage = self._build_usage(
                provider_id=provider_id,
                model_id=model_id,
                turn_handling_mode=turn_handling_mode,
                cache_attempted=False,
                cache_applied=False,
                completion_usage={},
                tool_call_count=1,
                telemetry=telemetry,
            )
            turn = self._store.update_turn(
                turn_id,
                status=AssistantTurnStatus.FAILED,
                error=str(exc),
                usage=usage,
            )
            self._emit_event(
                AssistantEventName.ERROR,
                session_id=session_id,
                turn_id=turn_id,
                data={"error": str(exc), "tool_call_id": failed_call.tool_call_id},
            )
            return CreateAssistantTurnResponse(
                turn=turn,
                assistant_message=None,
                confirmation=None,
                tool_calls=[failed_call],
            )

    def _parser_tool_followup_completion(
        self,
        *,
        turn_handling_mode: str,
        provider_id: str | None,
        model_id: str | None,
        tool_name: str,
        tool_result: dict[str, Any],
        thinking: bool | str | None,
    ) -> ProviderCompletion | None:
        if turn_handling_mode != "parser_fast_path":
            return None
        if tool_name not in {
            "capabilities.describe",
            "artifact.describe_geotiff",
            "artifact.preview_geotiff",
            "artifact.stats_geotiff",
            "artifact.describe_table",
            "artifact.describe_plot",
        }:
            return None
        provider = str(provider_id or "").strip()
        model = str(model_id or "").strip()
        if not provider or not model:
            return None
        compact_result = compact_tool_result_for_model_context(tool_name, tool_result)
        result_json = json.dumps(compact_result, ensure_ascii=True, default=str)
        prompt = (
            f"Tool `{tool_name}` returned this JSON:\n{result_json}\n\n"
            "Answer the user directly in plain language. "
            "Use `summary_text` and include key facts from `key_stats` and `warnings` when present."
        )
        try:
            return self._providers.complete(
                provider_id=provider,
                model_id=model,
                system_prompt=(
                    "You are the Lunar Analyst assistant. "
                    "Convert trusted tool output JSON into a concise user-facing response. "
                    "Do not invent values not present in the JSON."
                ),
                conversation=[{"role": "user", "content": prompt}],
                cache_context=None,
                tool_schema=[],
                max_output_tokens=max(128, self._completion_token_budget(prompt=prompt, is_command_turn=False)),
                thinking=thinking,
            )
        except Exception as exc:
            logger.warning(
                "Parser fast-path follow-up completion failed for %s: %s",
                tool_name,
                exc,
            )
            return None

    def _execute_single_tool_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        resolved_scenario_id: str | None,
        policy: Any,
        turn_handling_mode: str,
        provider_id: str,
        model_id: str,
        telemetry: TurnTelemetry,
        thinking: bool | str | None,
    ) -> CreateAssistantTurnResponse:
        normalized = self._normalize_tool_arguments(
            session_id=session_id,
            tool_name=tool_name,
            tool_args=tool_args,
            resolved_scenario_id=resolved_scenario_id,
        )
        action_type = action_type_for_tool(tool_name)
        tool_call = self._store.create_tool_call(
            session_id=session_id,
            turn_id=turn_id,
            tool_name=tool_name,
            arguments=normalized,
            status="proposed",
            action_type=action_type,
        )
        self._emit_event(
            AssistantEventName.TOOL_CALL_PROPOSED,
            session_id=session_id,
            turn_id=turn_id,
            data={"tool_call_id": tool_call.tool_call_id, "tool_name": tool_name, "arguments": normalized},
        )
        telemetry.mark_first_event()
        needs_confirmation, confirmation_action_type = self._needs_confirmation_for_tool(
            session_id=session_id,
            tool_name=tool_name,
            tool_args=normalized,
            fallback_action_type=action_type,
            policy=policy,
        )
        if needs_confirmation and confirmation_action_type is not None:
            confirmation = self._store.create_confirmation(
                session_id=session_id,
                turn_id=turn_id,
                action_type=confirmation_action_type,
                tool_name=tool_name,
                arguments=normalized,
            )
            self._store.complete_tool_call(
                tool_call.tool_call_id,
                status="confirmation_required",
                result={},
            )
            usage = self._build_usage(
                provider_id=provider_id,
                model_id=model_id,
                turn_handling_mode=turn_handling_mode,
                cache_attempted=False,
                cache_applied=False,
                completion_usage={},
                tool_call_count=1,
                telemetry=telemetry,
            )
            turn = self._store.update_turn(
                turn_id,
                status=AssistantTurnStatus.CONFIRMATION_REQUIRED,
                usage=usage,
            )
            self._emit_event(
                AssistantEventName.CONFIRMATION_REQUIRED,
                session_id=session_id,
                turn_id=turn_id,
                data=confirmation.model_dump(mode="json"),
            )
            assistant_message = self._record_confirmation_required_message(
                session_id=session_id,
                turn_id=turn_id,
                tool_name=tool_name,
                action_type=confirmation_action_type,
                confirmation=confirmation,
                arguments=normalized,
            )
            return CreateAssistantTurnResponse(
                turn=turn,
                assistant_message=assistant_message,
                confirmation=confirmation,
                tool_calls=[self._store.get_tool_call(tool_call.tool_call_id)],
            )
        return self._execute_tool_for_turn(
            session_id=session_id,
            turn_id=turn_id,
            tool_call=tool_call,
            turn_handling_mode=turn_handling_mode,
            provider_id=provider_id,
            model_id=model_id,
            telemetry=telemetry,
            thinking=thinking,
        )

    def _initialize_turn_hybrid_context(
        self,
        *,
        turn_id: str,
        session_id: str,
        prompt: str,
        resolved_scenario_id: str | None,
    ) -> None:
        context = TurnHybridContext()
        context.prompt_text = str(prompt or "")
        segmentation_started = time.perf_counter()
        try:
            segments = self._prompt_segmenter.segment(prompt)
        except Exception as exc:
            logger.warning("assistant prompt segmentation failed turn_id=%s error=%s", turn_id, exc)
            segments = []
            self._emit_event(
                AssistantEventName.ERROR,
                session_id=session_id,
                turn_id=turn_id,
                data={"error": str(exc), "error_code": ERROR_SEGMENTATION_INVALID},
            )
        if not segments:
            segments = [
                PromptSegment(
                    segment_id="s1",
                    text=prompt.strip(),
                    start_char=0,
                    end_char=len(prompt.strip()),
                    is_imperative_candidate=self._is_command_prompt(prompt),
                    has_complexity_guard=False,
                    segmentation_confidence=0.5,
                )
            ]
        context.segments = segments
        context.latencies_ms["latency_segmentation_ms"] = int((time.perf_counter() - segmentation_started) * 1000)
        logger.info(
            "assistant pipeline segmentation\n"
            "  turn_id=%s\n"
            "  session_id=%s\n"
            "  segment_count=%s\n"
            "  segments=\n%s",
            turn_id,
            session_id,
            len(segments),
            self._json_for_log_pretty(
                [
                    {
                        "segment_id": item.segment_id,
                        "text": item.text[:240],
                        "start_char": item.start_char,
                        "end_char": item.end_char,
                        "confidence": round(float(item.segmentation_confidence), 3),
                        "is_imperative_candidate": bool(item.is_imperative_candidate),
                        "has_complexity_guard": bool(item.has_complexity_guard),
                    }
                    for item in segments
                ]
            ),
        )
        self._emit_event(
            AssistantEventName.PROMPT_SEGMENTATION_COMPLETED,
            session_id=session_id,
            turn_id=turn_id,
            data={
                "segment_count": len(segments),
                "latency_segmentation_ms": context.latencies_ms["latency_segmentation_ms"],
            },
        )

        classification_started = time.perf_counter()
        resolution_started = classification_started
        classifications, entity_resolution, deterministic_trace = self._classify_resolve_with_unified_deterministic_recognizer(
            segments=segments,
            scenario_id=resolved_scenario_id,
            constraints_text=self._runtime(session_id).constraints_text,
            known_products=self._known_product_references(resolved_scenario_id),
        )
        class_counts: dict[str, int] = {}
        family_counts: dict[str, int] = {}
        for item in classifications:
            class_counts[item.segment_class] = class_counts.get(item.segment_class, 0) + 1
            family = str(item.intent_family or "").strip()
            if not family:
                continue
            family_counts[family] = family_counts.get(family, 0) + 1
        context.classifications = classifications
        context.entity_resolution = dict(entity_resolution)
        context.deterministic_recognition = dict(deterministic_trace)
        context.latencies_ms["latency_classification_ms"] = int((time.perf_counter() - classification_started) * 1000)
        context.latencies_ms["latency_entity_resolution_ms"] = int((time.perf_counter() - resolution_started) * 1000)
        entity_ambiguity_count = sum(len(item.ambiguities) for item in entity_resolution.values())
        resolved_mentions_count = sum(
            len([mention for mention in item.mentions if mention.resolved_id])
            for item in entity_resolution.values()
        )
        logger.info(
            "assistant pipeline classification\n"
            "  turn_id=%s\n"
            "  session_id=%s\n"
            "  labels=%s\n"
            "  classifications=\n%s\n"
            "  entity_resolution=\n%s",
            turn_id,
            session_id,
            self._json_for_log([item.segment_class for item in classifications]),
            self._json_for_log_pretty(
                [
                    {
                        "segment_id": item.segment_id,
                        "class": item.segment_class,
                        "confidence": round(float(item.confidence), 3),
                        "command": item.command,
                        "args": [{"name": arg.name, "value": arg.value} for arg in item.args],
                        "product_type": item.product_type,
                        "intent_family": item.intent_family,
                        "intent_properties": dict(item.intent_properties),
                        "pixel_type": item.pixel_type,
                        "semantics": item.semantics,
                        "sources": list(item.sources),
                        "matched_action_ids": list(item.matched_action_ids),
                        "missing_required_slots": list(item.missing_required_slots),
                        "blocking_reason_code": item.blocking_reason_code,
                        "requires_clarification": bool(item.requires_clarification),
                        "classification_origin": item.classification_origin,
                        "validation_status": item.validation_status,
                        "downgrade_reason": item.downgrade_reason,
                        "candidate_product_types": list(item.candidate_product_types),
                    }
                    for item in classifications
                ]
            ),
            self._json_for_log_pretty(
                [item.as_dict() for item in entity_resolution.values()]
            ),
        )
        self._emit_event(
            AssistantEventName.PROMPT_CLASSIFICATION_COMPLETED,
            session_id=session_id,
            turn_id=turn_id,
            data={
                "labels": [item.segment_class for item in classifications],
                "latency_classification_ms": context.latencies_ms["latency_classification_ms"],
                "deterministic_classification": {
                    "class_counts": class_counts,
                    "intent_family_counts": family_counts,
                },
                "entity_resolution": {
                    "segment_count": len(entity_resolution),
                    "resolved_mentions": resolved_mentions_count,
                    "ambiguities": entity_ambiguity_count,
                    "latency_entity_resolution_ms": context.latencies_ms["latency_entity_resolution_ms"],
                },
            },
        )

        execution_plan_started = time.perf_counter()
        if self._turn_execution_plan_contract_enabled:
            try:
                execution_plan = self._turn_execution_plan.build(
                    turn_id=turn_id,
                    session_id=session_id,
                    prompt_text=prompt,
                    segments=segments,
                    classifications=classifications,
                    runtime_state_seed={
                        "active_scenario_id": resolved_scenario_id,
                        "active_scenario_directory": (
                            str(self._tool_services.scenario_service.get_scenario(resolved_scenario_id).directory)
                            if resolved_scenario_id
                            else None
                        ),
                    },
                )
                context.execution_plan = execution_plan
                context.turn_state = self._turn_state_manager.create(execution_plan=execution_plan)
                self._emit_event(
                    AssistantEventName.TURN_EXECUTION_PLAN_BUILT,
                    session_id=session_id,
                    turn_id=turn_id,
                    data={"segment_count": len(execution_plan.segments)},
                )
            except Exception as exc:
                logger.warning("assistant turn execution-plan build failed turn_id=%s error=%s", turn_id, exc)
                self._emit_event(
                    AssistantEventName.TURN_EXECUTION_PLAN_VALIDATION_FAILED,
                    session_id=session_id,
                    turn_id=turn_id,
                    data={"error": str(exc)},
                )
        context.latencies_ms["latency_execution_plan_ms"] = int((time.perf_counter() - execution_plan_started) * 1000)
        self._turn_hybrid_context[turn_id] = context

    def _maybe_execute_ordered_deterministic_segments_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        resolved_scenario_id: str | None,
        provider_id: str,
        model_id: str,
        execution_mode: str,
        telemetry: TurnTelemetry,
        policy: Any,
        thinking: bool | str | None,
    ) -> CreateAssistantTurnResponse | None:
        turn_ctx = self._turn_hybrid_context.get(turn_id)
        if turn_ctx is None:
            return None
        classifications = {item.segment_id: item for item in turn_ctx.classifications}
        if not classifications:
            return None
        if any(item.segment_class == "other" for item in turn_ctx.classifications) and execution_mode != "tool_loop":
            return None
        executed_calls: list[AssistantToolCall] = []
        segment_messages: list[str] = []
        active_scenario_id = resolved_scenario_id
        aggregate_completion_usage = {"prompt_tokens": 0, "completion_tokens": 0, "cached_prompt_tokens": 0}
        aggregate_cache_attempted = False
        aggregate_cache_applied = False
        aggregate_fallback_used = False
        final_provider_id = provider_id
        final_model_id = model_id
        ordered_metadata: dict[str, Any] = {}
        if not active_scenario_id and any(item.segment_class == "create_product" for item in turn_ctx.classifications):
            # Create-product deterministic recipes are scenario-scoped; without an active
            # scenario, preserve legacy model-tool-loop behavior.
            return None
        for segment in turn_ctx.segments:
            segment_started = time.perf_counter()
            calls_before = len(executed_calls)
            messages_before = len(segment_messages)
            segment_log_emitted = False

            def _emit_segment_log(
                *,
                status: str,
                response_text: str = "",
                error: str | None = None,
                terminal_tool_calls: list[AssistantToolCall] | None = None,
            ) -> None:
                nonlocal segment_log_emitted
                if segment_log_emitted:
                    return
                segment_log_emitted = True
                segment_tool_calls = list(executed_calls[calls_before:])
                if terminal_tool_calls is not None and len(terminal_tool_calls) >= calls_before:
                    segment_tool_calls = list(terminal_tool_calls[calls_before:])
                message_delta = "\n".join(item for item in segment_messages[messages_before:] if str(item).strip()).strip()
                self._log_segment_processing_summary(
                    session_id=session_id,
                    turn_id=turn_id,
                    segment=segment,
                    status=status,
                    response_text=str(response_text or "").strip() or message_delta,
                    segment_tool_calls=segment_tool_calls,
                    error=error,
                    elapsed_ms=int((time.perf_counter() - segment_started) * 1000),
                )

            classification = classifications.get(segment.segment_id)
            if classification is None:
                _emit_segment_log(status="skipped_no_classification")
                continue
            segment_resolution = self._segment_entity_resolution(
                turn_id=turn_id,
                segment_id=segment.segment_id,
            )
            block_reason = self._deterministic_dispatch_block_reason(
                classification=classification,
                resolution=segment_resolution,
            )
            if block_reason is not None and classification.segment_class in {"command", "intent_family"}:
                self._record_segment_dispatch(
                    turn_id=turn_id,
                    segment_id=classification.segment_id,
                    handler="deterministic_block",
                    dispatch_mode="blocked",
                    reason=block_reason,
                )
                self._set_execution_mode_for_segment(
                    turn_id=turn_id,
                    segment_id=classification.segment_id,
                    execution_mode="blocked",
                )
                clarification_text = (
                    "Need clarification before deterministic execution: "
                    f"{block_reason}. "
                    "Please specify the exact target entity."
                )
                usage = self._build_usage(
                    provider_id=provider_id,
                    model_id=model_id,
                    turn_handling_mode="ordered_segment_execution",
                    cache_attempted=False,
                    cache_applied=False,
                    completion_usage={},
                    tool_call_count=len(executed_calls),
                    telemetry=telemetry,
                )
                assistant_message, turn = self._finalize_turn_with_message(
                    session_id=session_id,
                    turn_id=turn_id,
                    text=clarification_text,
                    usage=usage,
                    metadata={
                        "execution_origin": "deterministic",
                        "ordered_segment_execution": True,
                        "clarification_required": True,
                        "clarification_code": "entity_resolution_blocked",
                        "blocking_reason_code": block_reason,
                        "segment_id": classification.segment_id,
                    },
                    outputs=self._collect_tool_outputs(executed_calls),
                    telemetry=telemetry,
                )
                _emit_segment_log(
                    status="blocked_clarification_required",
                    response_text=clarification_text,
                    terminal_tool_calls=executed_calls,
                )
                return CreateAssistantTurnResponse(
                    turn=turn,
                    assistant_message=assistant_message,
                    confirmation=None,
                    tool_calls=executed_calls,
                )
            if classification.segment_class == "command":
                planned = PromptClassifier._plan_segment(  # noqa: SLF001
                    router=self._command_router,
                    text=segment.text,
                    scenario_id=active_scenario_id,
                )
                if planned is None or any(isinstance(step, PlannedAgentStep) for step in planned.steps):
                    _emit_segment_log(status="deferred_to_model_tool_loop")
                    return None
                self._record_segment_dispatch(
                    turn_id=turn_id,
                    segment_id=classification.segment_id,
                    handler="command_router",
                    dispatch_mode="deterministic",
                    planned_tools=[step.tool_name for step in planned.steps if isinstance(step, PlannedToolStep)],
                    action_ids=list(classification.matched_action_ids),
                )
                for step in planned.steps:
                    assert isinstance(step, PlannedToolStep)
                    tool_args = self._render_action_step_template(
                        template=dict(step.arguments_template),
                        slots=planned.slots,
                    )
                    completed_call, result, terminal_response = self._execute_deterministic_tool_step(
                        session_id=session_id,
                        turn_id=turn_id,
                        tool_name=step.tool_name,
                        tool_args=tool_args,
                        resolved_scenario_id=active_scenario_id,
                        policy=policy,
                        turn_handling_mode="ordered_segment_execution",
                        provider_id=provider_id,
                        model_id=model_id,
                        telemetry=telemetry,
                        prior_calls=executed_calls,
                    )
                    if terminal_response is not None:
                        terminal_message = str(
                            getattr(getattr(terminal_response, "assistant_message", None), "content", "") or ""
                        )
                        _emit_segment_log(
                            status=self._segment_outcome_status_from_turn(terminal_response.turn),
                            response_text=terminal_message,
                            error=str(getattr(terminal_response.turn, "error", "") or "").strip() or None,
                            terminal_tool_calls=list(getattr(terminal_response, "tool_calls", []) or []),
                        )
                        return terminal_response
                    assert completed_call is not None
                    assert result is not None
                    executed_calls.append(completed_call)
                    self._append_segment_executed_tool(
                        turn_id=turn_id,
                        segment_id=classification.segment_id,
                        tool_name=completed_call.tool_name,
                    )
                    segment_messages.append(summarize_tool_result(completed_call.tool_name, result))
                    if completed_call.tool_name == "scenario.set_current":
                        next_scenario_id = str(result.get("scenario", {}).get("scenario_id", "")).strip()
                        if next_scenario_id:
                            active_scenario_id = next_scenario_id
                _emit_segment_log(status="completed")
                continue
            if classification.segment_class == "intent_family":
                resolution = self._segment_entity_resolution(
                    turn_id=turn_id,
                    segment_id=classification.segment_id,
                )
                intent_family = str(classification.intent_family or "").strip()
                classified_operation = str(classification.intent_properties.get("operation", "")).strip().lower() or None
                canonical_operation = (
                    str(resolution.canonical_operation).strip().lower() or None
                    if resolution is not None and resolution.canonical_operation is not None
                    else None
                )
                verb_source = (
                    str(resolution.verb_normalization.source).strip() or None
                    if resolution is not None
                    else None
                )
                verb_ambiguous = bool(resolution.verb_normalization.ambiguous) if resolution is not None else False
                effective_operation = classified_operation
                if intent_family == "location_navigation":
                    logger.info(
                        "assistant segment operation reconciliation "
                        "session_id=%s turn_id=%s segment_id=%s intent_family=%s "
                        "classified_operation=%s canonical_operation=%s effective_operation=%s "
                        "source=%s ambiguous=%s",
                        session_id,
                        turn_id,
                        classification.segment_id,
                        intent_family,
                        classified_operation,
                        canonical_operation,
                        effective_operation,
                        verb_source,
                        verb_ambiguous,
                    )
                if not self._semantic_intent_families_enabled or intent_family not in self._enabled_intent_families:
                    # Let normal fast-path/model-loop dispatch handle the turn when
                    # semantic intent execution is disabled for this family.
                    _emit_segment_log(status="deferred_to_model_tool_loop")
                    return None
                if segment.has_complexity_guard or isinstance(classification.intent_properties.get("constraints"), dict):
                    self._record_segment_dispatch(
                        turn_id=turn_id,
                        segment_id=classification.segment_id,
                        handler="model_tool_loop",
                        dispatch_mode="llm",
                        reason="complexity_guard",
                    )
                    other_result = self._execute_other_segment_turn_fragment(
                        session_id=session_id,
                        turn_id=turn_id,
                        prompt=segment.text,
                        scenario_id=active_scenario_id,
                        provider_id=provider_id,
                        model_id=model_id,
                        policy=policy,
                        telemetry=telemetry,
                        thinking=thinking,
                        prior_segment_messages=segment_messages,
                        prior_calls=executed_calls,
                        segment_id=segment.segment_id,
                    )
                    if other_result.terminal_response is not None:
                        terminal_message = str(
                            getattr(getattr(other_result.terminal_response, "assistant_message", None), "content", "") or ""
                        )
                        _emit_segment_log(
                            status=self._segment_outcome_status_from_turn(other_result.terminal_response.turn),
                            response_text=str(other_result.text or "").strip() or terminal_message,
                            error=str(getattr(other_result.terminal_response.turn, "error", "") or "").strip() or None,
                            terminal_tool_calls=list(getattr(other_result.terminal_response, "tool_calls", []) or []),
                        )
                        return other_result.terminal_response
                    executed_calls.extend(other_result.new_calls)
                    if other_result.text.strip():
                        segment_messages.append(other_result.text.strip())
                    aggregate_completion_usage = self._sum_usage(aggregate_completion_usage, other_result.completion_usage)
                    aggregate_cache_attempted = aggregate_cache_attempted or other_result.cache_attempted
                    aggregate_cache_applied = aggregate_cache_applied or other_result.cache_applied
                    aggregate_fallback_used = aggregate_fallback_used or other_result.fallback_used
                    final_provider_id = other_result.provider_id
                    final_model_id = other_result.model_id
                    ordered_metadata.update(other_result.metadata)
                    _emit_segment_log(status="completed", response_text=other_result.text)
                    continue
                mapped = None
                if self._semantic_intent_families_enabled and intent_family in self._enabled_intent_families:
                    mapped = self._intent_to_tool_planner.map(
                        classification=classification,
                        scenario_id=active_scenario_id,
                        entity_resolution=resolution,
                    )
                if mapped is None:
                    self._record_segment_dispatch(
                        turn_id=turn_id,
                        segment_id=classification.segment_id,
                        handler="model_tool_loop",
                        dispatch_mode="llm",
                        reason="intent_family_unmapped",
                    )
                    other_result = self._execute_other_segment_turn_fragment(
                        session_id=session_id,
                        turn_id=turn_id,
                        prompt=segment.text,
                        scenario_id=active_scenario_id,
                        provider_id=provider_id,
                        model_id=model_id,
                        policy=policy,
                        telemetry=telemetry,
                        thinking=thinking,
                        prior_segment_messages=segment_messages,
                        prior_calls=executed_calls,
                        segment_id=segment.segment_id,
                    )
                    if other_result.terminal_response is not None:
                        terminal_message = str(
                            getattr(getattr(other_result.terminal_response, "assistant_message", None), "content", "") or ""
                        )
                        _emit_segment_log(
                            status=self._segment_outcome_status_from_turn(other_result.terminal_response.turn),
                            response_text=str(other_result.text or "").strip() or terminal_message,
                            error=str(getattr(other_result.terminal_response.turn, "error", "") or "").strip() or None,
                            terminal_tool_calls=list(getattr(other_result.terminal_response, "tool_calls", []) or []),
                        )
                        return other_result.terminal_response
                    executed_calls.extend(other_result.new_calls)
                    if other_result.text.strip():
                        segment_messages.append(other_result.text.strip())
                    aggregate_completion_usage = self._sum_usage(aggregate_completion_usage, other_result.completion_usage)
                    aggregate_cache_attempted = aggregate_cache_attempted or other_result.cache_attempted
                    aggregate_cache_applied = aggregate_cache_applied or other_result.cache_applied
                    aggregate_fallback_used = aggregate_fallback_used or other_result.fallback_used
                    final_provider_id = other_result.provider_id
                    final_model_id = other_result.model_id
                    ordered_metadata.update(other_result.metadata)
                    _emit_segment_log(status="completed", response_text=other_result.text)
                    continue

                if mapped.requires_clarification:
                    self._record_segment_dispatch(
                        turn_id=turn_id,
                        segment_id=classification.segment_id,
                        handler="intent_to_tool_planner",
                        dispatch_mode="blocked",
                        reason=str(mapped.blocking_reason_code or "clarification_required"),
                    )
                    self._set_execution_mode_for_segment(
                        turn_id=turn_id,
                        segment_id=classification.segment_id,
                        execution_mode="blocked",
                    )
                    usage = self._build_usage(
                        provider_id=provider_id,
                        model_id=model_id,
                        turn_handling_mode="ordered_segment_execution",
                        cache_attempted=False,
                        cache_applied=False,
                        completion_usage={},
                        tool_call_count=len(executed_calls),
                        telemetry=telemetry,
                    )
                    assistant_message, turn = self._finalize_turn_with_message(
                        session_id=session_id,
                        turn_id=turn_id,
                        text=str(mapped.clarification_message or "More details are required to continue."),
                        usage=usage,
                        metadata={
                            "execution_origin": "deterministic",
                            "ordered_segment_execution": True,
                            "intent_family_status": "clarification_required",
                            "intent_family": mapped.intent_family,
                            "blocking_reason_code": mapped.blocking_reason_code,
                        },
                        outputs=self._collect_tool_outputs(executed_calls),
                        telemetry=telemetry,
                    )
                    _emit_segment_log(
                        status="blocked_clarification_required",
                        response_text=str(mapped.clarification_message or "More details are required to continue."),
                        terminal_tool_calls=executed_calls,
                    )
                    return CreateAssistantTurnResponse(
                        turn=turn,
                        assistant_message=assistant_message,
                        confirmation=None,
                        tool_calls=executed_calls,
                    )
                self._record_segment_dispatch(
                    turn_id=turn_id,
                    segment_id=classification.segment_id,
                    handler="intent_to_tool_planner",
                    dispatch_mode="deterministic",
                    planned_tools=[step.tool_name for step in mapped.tool_steps],
                    continuation_handler="model_tool_loop" if str(mapped.model_handoff_prompt or "").strip() else None,
                )
                for step in mapped.tool_steps:
                    completed_call, result, terminal_response = self._execute_deterministic_tool_step(
                        session_id=session_id,
                        turn_id=turn_id,
                        tool_name=step.tool_name,
                        tool_args=step.arguments,
                        resolved_scenario_id=active_scenario_id,
                        policy=policy,
                        turn_handling_mode="ordered_segment_execution",
                        provider_id=provider_id,
                        model_id=model_id,
                        telemetry=telemetry,
                        prior_calls=executed_calls,
                    )
                    if terminal_response is not None:
                        terminal_message = str(
                            getattr(getattr(terminal_response, "assistant_message", None), "content", "") or ""
                        )
                        _emit_segment_log(
                            status=self._segment_outcome_status_from_turn(terminal_response.turn),
                            response_text=terminal_message,
                            error=str(getattr(terminal_response.turn, "error", "") or "").strip() or None,
                            terminal_tool_calls=list(getattr(terminal_response, "tool_calls", []) or []),
                        )
                        return terminal_response
                    assert completed_call is not None
                    assert result is not None
                    executed_calls.append(completed_call)
                    self._append_segment_executed_tool(
                        turn_id=turn_id,
                        segment_id=classification.segment_id,
                        tool_name=completed_call.tool_name,
                    )
                    segment_messages.append(summarize_tool_result(completed_call.tool_name, result))
                    if completed_call.tool_name == "scenario.set_current":
                        next_scenario_id = str(result.get("scenario", {}).get("scenario_id", "")).strip()
                        if next_scenario_id:
                            active_scenario_id = next_scenario_id
                if str(mapped.model_handoff_prompt or "").strip():
                    handoff_prompt = self._build_intent_family_handoff_prompt(
                        user_segment_text=segment.text,
                        model_handoff_prompt=str(mapped.model_handoff_prompt or ""),
                    )
                    other_result = self._execute_other_segment_turn_fragment(
                        session_id=session_id,
                        turn_id=turn_id,
                        prompt=handoff_prompt,
                        scenario_id=active_scenario_id,
                        provider_id=provider_id,
                        model_id=model_id,
                        policy=policy,
                        telemetry=telemetry,
                        thinking=thinking,
                        prior_segment_messages=segment_messages,
                        prior_calls=executed_calls,
                        segment_id=segment.segment_id,
                    )
                    if other_result.terminal_response is not None:
                        terminal_message = str(
                            getattr(getattr(other_result.terminal_response, "assistant_message", None), "content", "") or ""
                        )
                        _emit_segment_log(
                            status=self._segment_outcome_status_from_turn(other_result.terminal_response.turn),
                            response_text=str(other_result.text or "").strip() or terminal_message,
                            error=str(getattr(other_result.terminal_response.turn, "error", "") or "").strip() or None,
                            terminal_tool_calls=list(getattr(other_result.terminal_response, "tool_calls", []) or []),
                        )
                        return other_result.terminal_response
                    executed_calls.extend(other_result.new_calls)
                    guarded_text = self._apply_intent_family_response_guardrails(
                        text=other_result.text,
                        guardrails=dict(mapped.response_guardrails or {}),
                    )
                    if guarded_text.strip():
                        segment_messages.append(guarded_text.strip())
                    aggregate_completion_usage = self._sum_usage(aggregate_completion_usage, other_result.completion_usage)
                    aggregate_cache_attempted = aggregate_cache_attempted or other_result.cache_attempted
                    aggregate_cache_applied = aggregate_cache_applied or other_result.cache_applied
                    aggregate_fallback_used = aggregate_fallback_used or other_result.fallback_used
                    final_provider_id = other_result.provider_id
                    final_model_id = other_result.model_id
                    ordered_metadata.update(other_result.metadata)
                    ordered_metadata["intent_family_status"] = "model_handoff"
                    ordered_metadata["intent_family"] = mapped.intent_family
                _emit_segment_log(status="completed")
                continue
            if classification.segment_class == "other":
                self._record_segment_dispatch(
                    turn_id=turn_id,
                    segment_id=classification.segment_id,
                    handler="model_tool_loop",
                    dispatch_mode="llm",
                )
                other_result = self._execute_other_segment_turn_fragment(
                    session_id=session_id,
                    turn_id=turn_id,
                    prompt=segment.text,
                    scenario_id=active_scenario_id,
                    provider_id=provider_id,
                    model_id=model_id,
                    policy=policy,
                    telemetry=telemetry,
                    thinking=thinking,
                    prior_segment_messages=segment_messages,
                    prior_calls=executed_calls,
                    segment_id=segment.segment_id,
                )
                if other_result.terminal_response is not None:
                    terminal_message = str(
                        getattr(getattr(other_result.terminal_response, "assistant_message", None), "content", "") or ""
                    )
                    _emit_segment_log(
                        status=self._segment_outcome_status_from_turn(other_result.terminal_response.turn),
                        response_text=str(other_result.text or "").strip() or terminal_message,
                        error=str(getattr(other_result.terminal_response.turn, "error", "") or "").strip() or None,
                        terminal_tool_calls=list(getattr(other_result.terminal_response, "tool_calls", []) or []),
                    )
                    return other_result.terminal_response
                executed_calls.extend(other_result.new_calls)
                if other_result.text.strip():
                    segment_messages.append(other_result.text.strip())
                aggregate_completion_usage = self._sum_usage(aggregate_completion_usage, other_result.completion_usage)
                aggregate_cache_attempted = aggregate_cache_attempted or other_result.cache_attempted
                aggregate_cache_applied = aggregate_cache_applied or other_result.cache_applied
                aggregate_fallback_used = aggregate_fallback_used or other_result.fallback_used
                final_provider_id = other_result.provider_id
                final_model_id = other_result.model_id
                ordered_metadata.update(other_result.metadata)
                _emit_segment_log(status="completed", response_text=other_result.text)
                continue

            inventory = self._scenario_product_inventory(active_scenario_id)
            outcome = self._create_product_planner.plan(
                classification=classification,
                scenario_id=active_scenario_id,
                available_products=inventory,
            )
            if isinstance(outcome, CreateProductPlan):
                self._record_segment_dispatch(
                    turn_id=turn_id,
                    segment_id=classification.segment_id,
                    handler="create_product_planner",
                    dispatch_mode="deterministic",
                    planned_tools=[step.tool_name for step in outcome.steps],
                    recipe_id=outcome.recipe_id,
                )
                self._set_create_product_plan_metadata(
                    turn_id=turn_id,
                    segment_id=classification.segment_id,
                    selected_recipe_id=outcome.recipe_id,
                    prerequisite_count=outcome.prerequisite_count,
                )
                self._set_turn_state_effect_for_segment(
                    turn_id=turn_id,
                    segment_id=classification.segment_id,
                    effects={
                        "recipe_id": outcome.recipe_id,
                        "requested_product_type": outcome.requested_product_type,
                        "prerequisite_count": outcome.prerequisite_count,
                    },
                )
                terminal_response = self._execute_create_product_recipe(
                    session_id=session_id,
                    turn_id=turn_id,
                    active_scenario_id=active_scenario_id,
                    provider_id=provider_id,
                    model_id=model_id,
                    policy=policy,
                    telemetry=telemetry,
                    prior_calls=executed_calls,
                    segment_messages=segment_messages,
                    segment_id=classification.segment_id,
                    outcome=outcome,
                )
                if terminal_response is not None:
                    terminal_message = str(
                        getattr(getattr(terminal_response, "assistant_message", None), "content", "") or ""
                    )
                    _emit_segment_log(
                        status=self._segment_outcome_status_from_turn(terminal_response.turn),
                        response_text=terminal_message,
                        error=str(getattr(terminal_response.turn, "error", "") or "").strip() or None,
                        terminal_tool_calls=list(getattr(terminal_response, "tool_calls", []) or []),
                    )
                    return terminal_response
                _emit_segment_log(status="completed")
                continue
            if isinstance(outcome, CreateProductReuse):
                self._record_segment_dispatch(
                    turn_id=turn_id,
                    segment_id=classification.segment_id,
                    handler="create_product_planner",
                    dispatch_mode="deterministic",
                    reason="reuse_existing_output",
                    reuse_product_id=outcome.product_id,
                )
                self._set_turn_state_effect_for_segment(
                    turn_id=turn_id,
                    segment_id=classification.segment_id,
                    effects={"reuse": {"product_id": outcome.product_id, "output_relative_path": outcome.output_relative_path}},
                )
                segment_messages.append(outcome.message)
                _emit_segment_log(status="completed", response_text=outcome.message)
                continue
            if isinstance(outcome, CreateProductBlock):
                self._record_segment_dispatch(
                    turn_id=turn_id,
                    segment_id=classification.segment_id,
                    handler="create_product_planner",
                    dispatch_mode="blocked",
                    reason=outcome.reason_code,
                )
                self._set_execution_mode_for_segment(
                    turn_id=turn_id,
                    segment_id=classification.segment_id,
                    execution_mode="blocked",
                )
                usage = self._build_usage(
                    provider_id=provider_id,
                    model_id=model_id,
                    turn_handling_mode="ordered_segment_execution",
                    cache_attempted=False,
                    cache_applied=False,
                    completion_usage={},
                    tool_call_count=len(executed_calls),
                    telemetry=telemetry,
                )
                assistant_message, turn = self._finalize_turn_with_message(
                    session_id=session_id,
                    turn_id=turn_id,
                    text=outcome.message,
                    usage=usage,
                    metadata={
                        "execution_origin": "deterministic",
                        "ordered_segment_execution": True,
                        "create_product_status": "blocked",
                        "blocking_reason_code": outcome.reason_code,
                        "product_type": classification.product_type,
                        "blocking_details": dict(outcome.details),
                    },
                    outputs=self._collect_tool_outputs(executed_calls),
                    telemetry=telemetry,
                )
                _emit_segment_log(
                    status="blocked_clarification_required",
                    response_text=outcome.message,
                    terminal_tool_calls=executed_calls,
                )
                return CreateAssistantTurnResponse(
                    turn=turn,
                    assistant_message=assistant_message,
                    confirmation=None,
                    tool_calls=executed_calls,
                )
            _emit_segment_log(status="deferred_to_model_tool_loop")
            return None

        response_text = "\n".join(item for item in segment_messages if str(item).strip()).strip()
        if not response_text:
            response_text = "Deterministic segment execution completed."
        usage = self._build_usage(
            provider_id=final_provider_id,
            model_id=final_model_id,
            turn_handling_mode="ordered_segment_execution",
            cache_attempted=aggregate_cache_attempted,
            cache_applied=aggregate_cache_applied,
            completion_usage=aggregate_completion_usage,
            tool_call_count=len(executed_calls),
            telemetry=telemetry,
            fallback_used=aggregate_fallback_used,
        )
        final_metadata = {
            "execution_origin": "deterministic",
            "ordered_segment_execution": True,
            "segment_classes": [item.segment_class for item in turn_ctx.classifications],
        }
        final_metadata.update(ordered_metadata)
        assistant_message, turn = self._finalize_turn_with_message(
            session_id=session_id,
            turn_id=turn_id,
            text=response_text,
            usage=usage,
            metadata=final_metadata,
            outputs=self._collect_tool_outputs(executed_calls),
            telemetry=telemetry,
        )
        return CreateAssistantTurnResponse(
            turn=turn,
            assistant_message=assistant_message,
            confirmation=None,
            tool_calls=executed_calls,
        )

    def _scenario_product_inventory(self, scenario_id: str | None) -> list[AvailableProduct]:
        scenario_key = str(scenario_id or "").strip()
        if not scenario_key:
            return []
        try:
            raw_products = list(self._tool_services.product_service.list_products(scenario_key))
        except Exception as exc:
            logger.warning("assistant product inventory unavailable scenario_id=%s error=%s", scenario_key, exc)
            return []
        inventory: list[AvailableProduct] = []
        for product in raw_products:
            product_id = str(getattr(product, "product_id", "")).strip()
            if not product_id:
                continue
            relative_paths = self._product_relative_paths(product_id=product_id, scenario_id=scenario_key)
            filename = Path(relative_paths[0]).name if relative_paths else None
            references: set[str] = {
                product_id,
                str(getattr(product, "kind", "")).strip(),
                str(getattr(product, "subkind", "")).strip(),
                str(getattr(product, "name", "")).strip(),
                str(getattr(product, "title", "")).strip(),
                str(getattr(product, "label", "")).strip(),
            }
            inventory.append(
                AvailableProduct(
                    product_id=product_id,
                    kind=str(getattr(product, "kind", "")).strip(),
                    subkind=str(getattr(product, "subkind", "")).strip(),
                    filename=filename,
                    references=tuple(sorted(item for item in references if item)),
                    relative_paths=tuple(relative_paths),
                )
            )
        return inventory

    def _product_relative_paths(self, *, product_id: str, scenario_id: str) -> list[str]:
        product_service = self._tool_services.product_service
        list_files = getattr(product_service, "list_product_files", None)
        if callable(list_files):
            try:
                records = list(list_files(product_id))
                values = [
                    str(getattr(record, "relative_path", "")).strip()
                    for record in records
                    if str(getattr(record, "relative_path", "")).strip()
                ]
                if values:
                    return values
            except Exception as exc:
                logger.warning(
                    "assistant product file listing failed scenario_id=%s product_id=%s error=%s",
                    scenario_id,
                    product_id,
                    exc,
                )
        stores = getattr(self._tool_services, "stores", None)
        product_files = getattr(stores, "product_files", None)
        if isinstance(product_files, dict):
            values = [
                str(getattr(record, "relative_path", "")).strip()
                for record in product_files.values()
                if str(getattr(record, "product_id", "")).strip() == product_id
                and str(getattr(record, "scenario_id", "")).strip() == scenario_id
                and str(getattr(record, "relative_path", "")).strip()
            ]
            if values:
                return values
        return []

    def _set_execution_mode_for_segment(self, *, turn_id: str, segment_id: str, execution_mode: str) -> None:
        turn_ctx = self._turn_hybrid_context.get(turn_id)
        if turn_ctx is None or turn_ctx.execution_plan is None:
            return
        updated_segments = [
            replace(item, execution_mode=execution_mode)
            if item.segment_id == segment_id
            else item
            for item in turn_ctx.execution_plan.segments
        ]
        turn_ctx.execution_plan = replace(turn_ctx.execution_plan, segments=updated_segments)

    def _set_create_product_plan_metadata(
        self,
        *,
        turn_id: str,
        segment_id: str,
        selected_recipe_id: str | None,
        prerequisite_count: int,
    ) -> None:
        turn_ctx = self._turn_hybrid_context.get(turn_id)
        if turn_ctx is None or turn_ctx.execution_plan is None:
            return
        updated_segments = []
        for item in turn_ctx.execution_plan.segments:
            if item.segment_id == segment_id:
                updated_segments.append(
                    replace(
                        item,
                        selected_recipe_id=selected_recipe_id,
                        prerequisite_count=max(0, int(prerequisite_count)),
                    )
                )
            else:
                updated_segments.append(item)
        turn_ctx.execution_plan = replace(turn_ctx.execution_plan, segments=updated_segments)

    def _set_turn_state_effect_for_segment(self, *, turn_id: str, segment_id: str, effects: dict[str, Any]) -> None:
        turn_ctx = self._turn_hybrid_context.get(turn_id)
        if turn_ctx is None or turn_ctx.turn_state is None:
            return
        for segment in turn_ctx.turn_state.segments:
            if segment.segment_id != segment_id:
                continue
            segment.state_effects.update(dict(effects))
            break

    def _execute_create_product_recipe(
        self,
        *,
        session_id: str,
        turn_id: str,
        active_scenario_id: str | None,
        provider_id: str,
        model_id: str,
        policy: Any,
        telemetry: TurnTelemetry,
        prior_calls: list[AssistantToolCall],
        segment_messages: list[str],
        segment_id: str,
        outcome: CreateProductPlan,
    ) -> CreateAssistantTurnResponse | None:
        for step in outcome.steps:
            terminal_response = self._execute_recipe_step(
                session_id=session_id,
                turn_id=turn_id,
                active_scenario_id=active_scenario_id,
                provider_id=provider_id,
                model_id=model_id,
                policy=policy,
                telemetry=telemetry,
                prior_calls=prior_calls,
                segment_messages=segment_messages,
                segment_id=segment_id,
                step_tool_name=step.tool_name,
                step_tool_args=step.tool_args,
            )
            if terminal_response is not None:
                return terminal_response
        return None

    def _execute_recipe_step(
        self,
        *,
        session_id: str,
        turn_id: str,
        active_scenario_id: str | None,
        provider_id: str,
        model_id: str,
        policy: Any,
        telemetry: TurnTelemetry,
        prior_calls: list[AssistantToolCall],
        segment_messages: list[str],
        segment_id: str,
        step_tool_name: str,
        step_tool_args: dict[str, Any],
    ) -> CreateAssistantTurnResponse | None:
        completed_call, result, terminal_response = self._execute_deterministic_tool_step(
            session_id=session_id,
            turn_id=turn_id,
            tool_name=step_tool_name,
            tool_args=step_tool_args,
            resolved_scenario_id=active_scenario_id,
            policy=policy,
            turn_handling_mode="ordered_segment_execution",
            provider_id=provider_id,
            model_id=model_id,
            telemetry=telemetry,
            prior_calls=prior_calls,
        )
        if terminal_response is not None:
            return terminal_response
        assert completed_call is not None
        assert result is not None
        prior_calls.append(completed_call)
        self._append_segment_executed_tool(
            turn_id=turn_id,
            segment_id=segment_id,
            tool_name=completed_call.tool_name,
        )
        segment_messages.append(summarize_tool_result(completed_call.tool_name, result))
        return None

    def _execute_deterministic_tool_step(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        resolved_scenario_id: str | None,
        policy: Any,
        turn_handling_mode: str,
        provider_id: str,
        model_id: str,
        telemetry: TurnTelemetry,
        prior_calls: list[AssistantToolCall],
    ) -> tuple[AssistantToolCall | None, dict[str, Any] | None, CreateAssistantTurnResponse | None]:
        normalized = self._normalize_tool_arguments(
            session_id=session_id,
            tool_name=tool_name,
            tool_args=tool_args,
            resolved_scenario_id=resolved_scenario_id,
        )
        attempted_overwrite_retry = False
        while True:
            action_type = action_type_for_tool(tool_name)
            tool_call = self._store.create_tool_call(
                session_id=session_id,
                turn_id=turn_id,
                tool_name=tool_name,
                arguments=normalized,
                status="proposed",
                action_type=action_type,
            )
            self._emit_event(
                AssistantEventName.TOOL_CALL_PROPOSED,
                session_id=session_id,
                turn_id=turn_id,
                data={"tool_call_id": tool_call.tool_call_id, "tool_name": tool_name, "arguments": normalized},
            )
            telemetry.mark_first_event()
            needs_confirmation, confirmation_action_type = self._needs_confirmation_for_tool(
                session_id=session_id,
                tool_name=tool_name,
                tool_args=normalized,
                fallback_action_type=action_type,
                policy=policy,
            )
            if needs_confirmation and confirmation_action_type is not None:
                confirmation = self._store.create_confirmation(
                    session_id=session_id,
                    turn_id=turn_id,
                    action_type=confirmation_action_type,
                    tool_name=tool_name,
                    arguments=normalized,
                )
                pending_call = self._store.complete_tool_call(
                    tool_call.tool_call_id,
                    status="confirmation_required",
                    result={},
                )
                usage = self._build_usage(
                    provider_id=provider_id,
                    model_id=model_id,
                    turn_handling_mode=turn_handling_mode,
                    cache_attempted=False,
                    cache_applied=False,
                    completion_usage={},
                    tool_call_count=len(prior_calls) + 1,
                    telemetry=telemetry,
                )
                turn = self._store.update_turn(
                    turn_id,
                    status=AssistantTurnStatus.CONFIRMATION_REQUIRED,
                    usage=usage,
                )
                self._emit_event(
                    AssistantEventName.CONFIRMATION_REQUIRED,
                    session_id=session_id,
                    turn_id=turn_id,
                    data=confirmation.model_dump(mode="json"),
                )
                assistant_message = self._record_confirmation_required_message(
                    session_id=session_id,
                    turn_id=turn_id,
                    tool_name=tool_name,
                    action_type=confirmation_action_type,
                    confirmation=confirmation,
                    arguments=normalized,
                )
                return None, None, CreateAssistantTurnResponse(
                    turn=turn,
                    assistant_message=assistant_message,
                    confirmation=confirmation,
                    tool_calls=[*prior_calls, pending_call],
                )
            try:
                completed_call, result = self._execute_tool_call(
                    session_id=session_id,
                    turn_id=turn_id,
                    tool_call=tool_call,
                    telemetry=telemetry,
                )
                return completed_call, result, None
            except Exception as exc:
                failed_call = self._store.complete_tool_call(
                    tool_call.tool_call_id,
                    status="failed",
                    result={},
                    error=str(exc),
                )
                if (
                    isinstance(exc, ApiError)
                    and not attempted_overwrite_retry
                    and exc.code in {"map_algebra_output_exists", "raster_transform_output_exists"}
                    and tool_name in {"raster.calculate", "raster.transform"}
                ):
                    normalized = self._apply_confirmation_argument_overrides(
                        tool_name=tool_name,
                        arguments=normalized,
                    )
                    attempted_overwrite_retry = True
                    continue
                usage = self._build_usage(
                    provider_id=provider_id,
                    model_id=model_id,
                    turn_handling_mode=turn_handling_mode,
                    cache_attempted=False,
                    cache_applied=False,
                    completion_usage={},
                    tool_call_count=len(prior_calls) + 1,
                    telemetry=telemetry,
                )
                turn = self._store.update_turn(
                    turn_id,
                    status=AssistantTurnStatus.FAILED,
                    error=str(exc),
                    usage=usage,
                )
                self._emit_event(
                    AssistantEventName.ERROR,
                    session_id=session_id,
                    turn_id=turn_id,
                    data={"error": str(exc), "tool_call_id": failed_call.tool_call_id, "error_code": ERROR_TOOL_EXECUTION_FAILED},
                )
                return None, None, CreateAssistantTurnResponse(
                    turn=turn,
                    assistant_message=None,
                    confirmation=None,
                    tool_calls=[*prior_calls, failed_call],
                )

    def build_handoff_context(
        self,
        *,
        prompt: str,
        scenario_id: str | None,
        prior_segment_messages: list[str],
        resolution: Any | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Builds the exact context (system prompt and conversation) that would be sent to the LLM
        for a model loop handoff. Used for visualization and debugging.
        """
        history: list[AssistantMessage] = []
        compacted_summary = None
        persistent_constraints = None
        
        if session_id:
            try:
                history = self._store.list_messages(session_id, limit=120)
                compacted_summary = self._latest_compacted_summary(history)
                persistent_constraints = self._runtime(session_id).constraints_text
            except Exception:
                pass

        conversation = build_conversation(history)
        for message in prior_segment_messages:
            cleaned = str(message or "").strip()
            if cleaned:
                conversation.append({"role": "assistant", "content": cleaned})

        domain_context = self._build_domain_entity_context_payload(
            resolution=resolution,
            turn_id=turn_id,
        )
        conversation.append(
            {
                "role": "user",
                "content": self._domain_context_wrapped_user_query(
                    user_query=prompt,
                    domain_context=domain_context,
                ),
            }
        )

        system_prompt = build_system_prompt(
            scenario_id=scenario_id,
            scenario_directory=self._resolve_active_scenario_directory(scenario_id),
            capabilities_text=capabilities_text(),
            compacted_summary=compacted_summary,
            persistent_constraints=persistent_constraints,
        )

        return {
            "system_prompt": system_prompt,
            "system_prompt_path": str(SYSTEM_PROMPT_PATH),
            "conversation": conversation,
            "compacted_summary": compacted_summary,
        }

    @staticmethod
    def _bounded_ambiguity_candidates(ambiguity: dict[str, Any], *, max_candidates: int = 3) -> list[dict[str, Any]]:
        candidates = ambiguity.get("candidates")
        if not isinstance(candidates, list):
            return []
        bounded: list[dict[str, Any]] = []
        for item in candidates[:max_candidates]:
            if not isinstance(item, dict):
                continue
            bounded.append(
                {
                    "kind": str(item.get("kind", "")).strip(),
                    "resolved_id": str(item.get("resolved_id", "")).strip() or None,
                    "label": str(item.get("label", "")).strip(),
                    "score": float(item.get("score", 0.0) or 0.0),
                }
            )
        return bounded

    def _build_domain_entity_context_payload(
        self,
        *,
        resolution: SegmentEntityResolution | None,
        turn_id: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"segments": []}
        source_resolutions: list[SegmentEntityResolution] = []
        classification_by_segment: dict[str, SegmentClassification] = {}
        if resolution is not None:
            source_resolutions = [resolution]
        elif turn_id:
            turn_ctx = self._turn_hybrid_context.get(turn_id)
            if turn_ctx is not None:
                source_resolutions = list(turn_ctx.entity_resolution.values())
                classification_by_segment = {
                    item.segment_id: item
                    for item in turn_ctx.classifications
                }

        for item in source_resolutions[:4]:
            segment_payload: dict[str, Any] = {
                "segment_id": item.segment_id,
                "canonical_operation": item.canonical_operation,
                "target_kind": item.target_kind,
                "target_mention": item.target_mention,
                "target_resolved_id": item.target_resolved_id,
                "segment_class": "",
                "classification_origin": "",
                "candidate_product_types": [],
                "mentions": [],
                "ambiguities": [],
            }
            classification = classification_by_segment.get(item.segment_id)
            if classification is not None:
                segment_payload["segment_class"] = str(classification.segment_class or "")
                segment_payload["classification_origin"] = str(classification.classification_origin or "")
                segment_payload["candidate_product_types"] = list(classification.candidate_product_types or [])
            mention_rows: list[dict[str, Any]] = []
            for mention in item.mentions:
                mention_rows.append(
                    {
                        "mention_text": mention.mention_text,
                        "kind": mention.kind,
                        "resolved_id": mention.resolved_id,
                        "confidence": round(float(mention.confidence), 4),
                        "reason_code": mention.reason_code,
                    }
                )
            segment_payload["mentions"] = mention_rows[:8]
            segment_payload["ambiguities"] = [
                {
                    "mention_text": str(amb.get("mention_text", "")).strip(),
                    "kind": str(amb.get("kind", "")).strip(),
                    "reason_code": str(amb.get("reason_code", "")).strip(),
                    "candidates": self._bounded_ambiguity_candidates(amb, max_candidates=3),
                }
                for amb in item.ambiguities[:3]
                if isinstance(amb, dict)
            ]
            payload["segments"].append(segment_payload)
        payload["segment_count"] = len(payload["segments"])
        return payload

    @staticmethod
    def _domain_context_wrapped_user_query(*, user_query: str, domain_context: dict[str, Any]) -> str:
        context_json = json.dumps(domain_context, ensure_ascii=True, default=str)
        return (
            "<DOMAIN_ENTITY_CONTEXT>\n"
            f"{context_json}\n"
            "</DOMAIN_ENTITY_CONTEXT>\n"
            "<USER_QUERY>\n"
            f"{user_query}\n"
            "</USER_QUERY>"
        )

    def _inject_domain_context_into_conversation(
        self,
        *,
        conversation: list[dict[str, str]],
        prompt: str,
        turn_id: str,
    ) -> list[dict[str, str]]:
        domain_context = self._build_domain_entity_context_payload(resolution=None, turn_id=turn_id)
        wrapped = self._domain_context_wrapped_user_query(user_query=prompt, domain_context=domain_context)
        updated = list(conversation)
        for idx in range(len(updated) - 1, -1, -1):
            row = updated[idx]
            if str(row.get("role", "")).strip() != "user":
                continue
            if str(row.get("content", "")) == prompt:
                updated[idx] = {"role": "user", "content": wrapped}
                return updated
        updated.append({"role": "user", "content": wrapped})
        return updated

    def _execute_other_segment_turn_fragment(
        self,
        *,
        session_id: str,
        turn_id: str,
        prompt: str,
        scenario_id: str | None,
        provider_id: str,
        model_id: str,
        policy: Any,
        telemetry: TurnTelemetry,
        thinking: bool | str | None,
        prior_segment_messages: list[str],
        prior_calls: list[AssistantToolCall],
        segment_id: str | None = None,
    ) -> OrderedOtherSegmentResult:
        resolution = None
        if segment_id:
            resolution = self._segment_entity_resolution(turn_id=turn_id, segment_id=segment_id)
            
        handoff = self.build_handoff_context(
            prompt=prompt,
            scenario_id=scenario_id,
            prior_segment_messages=prior_segment_messages,
            resolution=resolution,
            session_id=session_id,
            turn_id=turn_id,
        )
        
        system_prompt = handoff["system_prompt"]
        conversation = handoff["conversation"]
        compacted_summary = handoff.get("compacted_summary")

        is_command_turn = self._is_command_prompt(prompt)
        expose_tools = self._should_expose_tools_for_prompt(prompt=prompt, is_command_turn=is_command_turn)
        selected_tool_names = select_tool_names_for_prompt(prompt=prompt, max_tools=22) if expose_tools else set()
        selected_schema = list_tools_schema_filtered(selected_tool_names=selected_tool_names) if expose_tools else []
        cache_context = build_cache_context(
            provider_id=provider_id,
            model_id=model_id,
            system_prompt=system_prompt,
            tool_schema=selected_schema,
            scenario_id=scenario_id,
            compacted_summary=compacted_summary,
        )
        tool_schema = list_tools_for_model(selected_tool_names=selected_tool_names) if expose_tools else []
        allowed_tool_names = {
            str(tool.get("function", {}).get("name", "")).strip()
            for tool in tool_schema
            if isinstance(tool, dict)
        }
        perf = self._providers.performance()
        max_iterations = max(1, perf.max_tool_iterations_per_turn)
        max_tool_calls = max(1, perf.max_tool_calls_per_iteration)
        adaptive_max_output_tokens = self._completion_token_budget(prompt=prompt, is_command_turn=is_command_turn)
        completion_usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "cached_prompt_tokens": 0}
        cache_attempted = False
        cache_applied = False
        fallback_used = False
        tool_argument_retry_used = False
        executed_calls: list[AssistantToolCall] = []
        final_text = ""
        active_provider_id = provider_id
        active_model_id = model_id
        source_references: list[dict[str, Any]] = []
        capture_rag_context = self._capture_eval_rag_context_enabled()
        rag_context_captures: list[dict[str, Any]] = []
        attempted_provider_models: set[tuple[str, str]] = set()
        attempted_provider_models_ordered: list[tuple[str, str]] = []
        fallback_chain: list[dict[str, Any]] = []

        def _record_attempt(candidate_provider_id: str, candidate_model_id: str) -> None:
            key = (str(candidate_provider_id), str(candidate_model_id))
            if key in attempted_provider_models:
                return
            attempted_provider_models.add(key)
            attempted_provider_models_ordered.append(key)

        _record_attempt(active_provider_id, active_model_id)
        seen_tool_signatures: set[str] = {
            self._tool_call_signature(call.tool_name, call.arguments)
            for call in prior_calls
        }
        mutation_intent = self._classify_mutation_intent(prompt)
        mutation_satisfied = any(
            call.tool_name in {"layer.update_state", "scenario.import_geotiff", "scenario.move_path"}
            for call in prior_calls
            if call.status == "completed"
        )

        for iteration in range(max_iterations):
            try:
                completion = self._providers.complete(
                    provider_id=active_provider_id,
                    model_id=active_model_id,
                    system_prompt=system_prompt,
                    conversation=conversation,
                    cache_context=cache_context,
                    tool_schema=tool_schema,
                    max_output_tokens=adaptive_max_output_tokens,
                    thinking=thinking,
                )
            except Exception as exc:
                recovered = False
                for candidate_provider_id, candidate_model_id in self._provider_fallback_pairs(
                    provider_id=active_provider_id,
                    model_id=active_model_id,
                ):
                    candidate_key = (candidate_provider_id, candidate_model_id)
                    if candidate_key in attempted_provider_models:
                        continue
                    _record_attempt(candidate_provider_id, candidate_model_id)
                    try:
                        prior_provider_id = active_provider_id
                        prior_model_id = active_model_id
                        completion = self._providers.complete(
                            provider_id=candidate_provider_id,
                            model_id=candidate_model_id,
                            system_prompt=system_prompt,
                            conversation=conversation,
                            cache_context=cache_context,
                            tool_schema=tool_schema,
                            max_output_tokens=adaptive_max_output_tokens,
                            thinking=thinking,
                        )
                        active_provider_id = candidate_provider_id
                        active_model_id = candidate_model_id
                        fallback_used = True
                        recovered = True
                        fallback_chain.append(
                            {
                                "from_provider_id": prior_provider_id,
                                "from_model_id": prior_model_id,
                                "to_provider_id": candidate_provider_id,
                                "to_model_id": candidate_model_id,
                                "reason": "provider_exception",
                                "error": str(exc),
                            }
                        )
                        break
                    except Exception:
                        continue
                if not recovered:
                    raise
            completion_usage_totals = self._sum_usage(completion_usage_totals, completion.usage)
            cache_attempted = cache_attempted or completion.cache_attempted
            cache_applied = cache_applied or completion.cache_applied
            completion_metadata = dict(getattr(completion, "metadata", {}) or {})
            if capture_rag_context:
                rag_context_text = str(completion_metadata.get("rag_context_text", "") or "")
                if rag_context_text.strip():
                    rag_context_captures.append(
                        {
                            "iteration": iteration + 1,
                            "provider_id": active_provider_id,
                            "model_id": active_model_id,
                            "context_chars": len(rag_context_text),
                            "context_text": rag_context_text,
                        }
                    )
            for item in completion.references:
                if not isinstance(item, dict):
                    continue
                rel = str(item.get("relative_path", "")).strip()
                chunk_id = str(item.get("chunk_id", "")).strip()
                if not rel or not chunk_id:
                    continue
                source_references.append(dict(item))
            if completion.text.strip():
                final_text = completion.text.strip()
            provider_tool_calls = list(completion.tool_calls)
            if not provider_tool_calls:
                fallback_tool_call = _extract_text_tool_call(completion.text, allowed_tool_names=allowed_tool_names)
                if fallback_tool_call is not None:
                    provider_tool_calls = [fallback_tool_call]
                    final_text = ""
            if not provider_tool_calls:
                if str(completion.finish_reason).strip().lower() == "length":
                    retry_cap = max(256, int(perf.empty_completion_retry_max_output_tokens))
                    next_budget = min(
                        retry_cap,
                        max(adaptive_max_output_tokens + 128, adaptive_max_output_tokens * 2),
                    )
                    if next_budget > adaptive_max_output_tokens:
                        adaptive_max_output_tokens = next_budget
                        continue
                break
            retry_after_invalid_arguments = False
            for provider_call in provider_tool_calls[:max_tool_calls]:
                normalized = self._normalize_tool_arguments(
                    session_id=session_id,
                    tool_name=provider_call.name,
                    tool_args={str(k): v for k, v in provider_call.arguments.items()},
                    resolved_scenario_id=scenario_id,
                )
                normalized, validation_error = self._validate_or_repair_tool_arguments(
                    tool_name=provider_call.name,
                    arguments=normalized,
                )
                if validation_error is not None:
                    if not tool_argument_retry_used:
                        tool_argument_retry_used = True
                        schema_hint = tool_argument_schema_for_model(provider_call.name)
                        conversation.append(
                            {
                                "role": "assistant",
                                "content": (
                                    f"Tool call `{provider_call.name}` was invalid: {validation_error}. "
                                    "Retry with one corrected call only."
                                ),
                            }
                        )
                        conversation.append(
                            {
                                "role": "user",
                                "content": (
                                    "Return a corrected tool call now. "
                                    "Use only allowed fields, omit unsupported/default fields, "
                                    f"and satisfy this schema: {json.dumps(schema_hint, ensure_ascii=True)}"
                                ),
                            }
                        )
                        retry_after_invalid_arguments = True
                        break
                    usage = self._build_usage(
                        provider_id=active_provider_id,
                        model_id=active_model_id,
                        turn_handling_mode="ordered_segment_execution",
                        cache_attempted=cache_attempted,
                        cache_applied=cache_applied,
                        completion_usage=completion_usage_totals,
                        tool_call_count=len(prior_calls) + len(executed_calls),
                        telemetry=telemetry,
                        fallback_used=fallback_used,
                    )
                    clarification = (
                        f"Need clarification before running `{provider_call.name}`: {validation_error}. "
                        "Please restate with correctly shaped tool arguments."
                    )
                    assistant_message, turn = self._finalize_turn_with_message(
                        session_id=session_id,
                        turn_id=turn_id,
                        text=clarification,
                        usage=usage,
                        metadata={
                            "provider_id": active_provider_id,
                            "model_id": active_model_id,
                            "usage": usage,
                            "clarification_required": True,
                            "clarification_code": "tool_arguments_invalid",
                            "tool_name": provider_call.name,
                        },
                        outputs=self._collect_tool_outputs([*prior_calls, *executed_calls]),
                        telemetry=telemetry,
                    )
                    return OrderedOtherSegmentResult(
                        text="",
                        new_calls=executed_calls,
                        terminal_response=CreateAssistantTurnResponse(
                        turn=turn,
                        assistant_message=assistant_message,
                        confirmation=None,
                        tool_calls=[*prior_calls, *executed_calls],
                        ),
                        provider_id=active_provider_id,
                        model_id=active_model_id,
                        completion_usage=completion_usage_totals,
                        cache_attempted=cache_attempted,
                        cache_applied=cache_applied,
                        fallback_used=fallback_used,
                    )
                tool_signature = self._tool_call_signature(provider_call.name, normalized)
                if tool_signature in seen_tool_signatures:
                    matching_call = next(
                        (
                            call
                            for call in reversed([*prior_calls, *executed_calls])
                            if self._tool_call_signature(call.tool_name, call.arguments) == tool_signature
                        ),
                        None,
                    )
                    if matching_call is not None:
                        final_text = summarize_tool_result(matching_call.tool_name, matching_call.result)
                    elif not final_text:
                        final_text = f"Tool `{provider_call.name}` already returned the requested result."
                    provider_tool_calls = []
                    break
                completed_call, result, terminal_response = self._execute_deterministic_tool_step(
                    session_id=session_id,
                    turn_id=turn_id,
                    tool_name=provider_call.name,
                    tool_args=normalized,
                    resolved_scenario_id=scenario_id,
                    policy=policy,
                    turn_handling_mode="ordered_segment_execution",
                    provider_id=active_provider_id,
                    model_id=active_model_id,
                    telemetry=telemetry,
                    prior_calls=[*prior_calls, *executed_calls],
                )
                if terminal_response is not None:
                    return OrderedOtherSegmentResult(
                        text="",
                        new_calls=executed_calls,
                        terminal_response=terminal_response,
                        provider_id=active_provider_id,
                        model_id=active_model_id,
                        completion_usage=completion_usage_totals,
                        cache_attempted=cache_attempted,
                        cache_applied=cache_applied,
                        fallback_used=fallback_used,
                    )
                assert completed_call is not None
                assert result is not None
                executed_calls.append(completed_call)
                if segment_id is not None:
                    self._append_segment_executed_tool(
                        turn_id=turn_id,
                        segment_id=segment_id,
                        tool_name=completed_call.tool_name,
                    )
                seen_tool_signatures.add(tool_signature)
                if completed_call.tool_name in {"layer.update_state", "scenario.import_geotiff", "scenario.move_path"}:
                    mutation_satisfied = True
                conversation.append(
                    {
                        "role": "assistant",
                        "content": (
                            f"Tool `{completed_call.tool_name}` result:\n"
                            f"{json.dumps(compact_tool_result_for_model_context(completed_call.tool_name, result), ensure_ascii=True, default=str)}"
                        ),
                    }
                )
            if retry_after_invalid_arguments:
                continue
            if not provider_tool_calls:
                break

        if not final_text:
            if executed_calls:
                final_text = "\n".join(summarize_tool_result(call.tool_name, call.result) for call in executed_calls[-3:])
            else:
                final_text = "No response returned by provider."
        if mutation_intent and not mutation_satisfied:
            final_text = (
                "I could not complete the requested state-changing action. "
                "Please specify the exact target (for example `turn on slope.tif`)."
            )
        metadata: dict[str, Any] = {
            "requested_provider_id": provider_id,
            "requested_model_id": model_id,
            "final_provider_id": active_provider_id,
            "final_model_id": active_model_id,
            "fallback_used": bool(fallback_used),
            "attempted_models": [
                {"provider_id": attempted_provider_id, "model_id": attempted_model_id}
                for attempted_provider_id, attempted_model_id in attempted_provider_models_ordered
            ],
            "fallback_chain": list(fallback_chain),
        }
        deduped_source_references: list[dict[str, Any]] = []
        seen_refs: set[tuple[str, str]] = set()
        for item in source_references:
            rel = str(item.get("relative_path", "")).strip()
            chunk_id = str(item.get("chunk_id", "")).strip()
            key = (rel, chunk_id)
            if not rel or not chunk_id or key in seen_refs:
                continue
            seen_refs.add(key)
            deduped_source_references.append(dict(item))
        if deduped_source_references:
            metadata["source_references"] = deduped_source_references
        if capture_rag_context and rag_context_captures:
            final_capture = rag_context_captures[-1]
            metadata["rag_context_text"] = str(final_capture.get("context_text", "") or "")
            metadata["rag_context_chars"] = int(final_capture.get("context_chars", 0) or 0)
            metadata["rag_context_capture_count"] = len(rag_context_captures)
            metadata["rag_context_captures"] = list(rag_context_captures)
        if mutation_intent and not mutation_satisfied:
            metadata["mutation_unsatisfied"] = True
            metadata["mutation_intent"] = mutation_intent
        return OrderedOtherSegmentResult(
            text=final_text,
            new_calls=executed_calls,
            terminal_response=None,
            provider_id=active_provider_id,
            model_id=active_model_id,
            completion_usage=completion_usage_totals,
            cache_attempted=cache_attempted,
            cache_applied=cache_applied,
            fallback_used=fallback_used,
            metadata=metadata,
        )

    @staticmethod
    def _build_intent_family_handoff_prompt(*, user_segment_text: str, model_handoff_prompt: str) -> str:
        guidance = str(model_handoff_prompt or "").strip()
        user_text = str(user_segment_text or "").strip()
        if not guidance:
            return user_text
        return (
            "Deterministic intent-family handoff instructions:\n"
            f"{guidance}\n\n"
            "User request:\n"
            f"{user_text}"
        )

    @staticmethod
    def _apply_intent_family_response_guardrails(*, text: str, guardrails: dict[str, Any]) -> str:
        rendered = str(text or "").strip()
        if not rendered:
            rendered = "No narrative response was produced."
        lowered = rendered.lower()
        lines: list[str] = []
        if bool(guardrails.get("evidence_required")):
            if not any(token in lowered for token in ("evidence", "source", "reference", "citation", "provenance")):
                lines.append("Evidence: Additional cited sources are required to substantiate this assessment.")
        if bool(guardrails.get("uncertainty_required")):
            if "uncert" not in lowered and "assumption" not in lowered:
                lines.append("Uncertainty: This assessment is subject to uncertainty due to incomplete constraints or evidence.")
        if bool(guardrails.get("requires_alternatives")):
            if "alternative" not in lowered and "option" not in lowered:
                lines.append("Alternatives: At least one alternative plan should be compared before execution.")
        if bool(guardrails.get("underconstrained")):
            if "underconstrained" not in lowered and "insufficient" not in lowered:
                lines.append("Underconstrained: The request lacks constraints needed for a definitive recommendation.")
        if bool(guardrails.get("provenance_required")):
            if "provenance" not in lowered and "lineage" not in lowered:
                lines.append("Provenance: Include artifact lineage and generation context for each cited output.")
        if not lines:
            return rendered
        return rendered + "\n\n" + "\n".join(lines)

    def _execute_action_plan_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        actions: list[PlannedAction],
        resolved_scenario_id: str | None,
        policy: Any,
        provider_id: str,
        model_id: str,
        telemetry: TurnTelemetry,
        thinking: bool | str | None,
        followup_prompt: str | None = None,
    ) -> CreateAssistantTurnResponse:
        outputs: list[dict[str, Any]] = []
        executed_calls: list[AssistantToolCall] = []
        step_index = 0
        for action in actions:
            for step in action.steps:
                step_index += 1
                if isinstance(step, PlannedAgentStep):
                    raise RuntimeError(
                        f"Deterministic agent sub-steps are not supported for action {action.action_id}."
                    )
                    logger.info(
                        "assistant action plan agent step session_id=%s turn_id=%s action=%s index=%s allowed_tools=%s objective=%s",
                        session_id,
                        turn_id,
                        action.action_id,
                        step_index,
                        step.allowed_tools,
                        self._json_for_log(step.objective),
                    )
                    try:
                        agent_result = self._execute_action_plan_agent_step(
                            session_id=session_id,
                            turn_id=turn_id,
                            step=step,
                            scenario_id=resolved_scenario_id,
                            provider_id=provider_id,
                            model_id=model_id,
                        )
                    except Exception as exc:
                        usage = self._build_usage(
                            provider_id=provider_id,
                            model_id=model_id,
                            turn_handling_mode="action_plan_fast_path",
                            cache_attempted=False,
                            cache_applied=False,
                            completion_usage={},
                            tool_call_count=len(executed_calls),
                            telemetry=telemetry,
                        )
                        turn = self._store.update_turn(
                            turn_id,
                            status=AssistantTurnStatus.FAILED,
                            error=str(exc),
                            usage=usage,
                        )
                        self._emit_event(
                            AssistantEventName.ERROR,
                            session_id=session_id,
                            turn_id=turn_id,
                            data={"error": str(exc), "action_id": action.action_id, "error_code": ERROR_TOOL_EXECUTION_FAILED},
                        )
                        return CreateAssistantTurnResponse(
                            turn=turn,
                            assistant_message=None,
                            confirmation=None,
                            tool_calls=executed_calls,
                        )
                    action.slots.update(agent_result)
                    outputs.append(
                        {
                            "action_id": action.action_id,
                            "segment": action.segment,
                            "agent_step": True,
                            "output": agent_result,
                        }
                    )
                    continue

                normalized = self._normalize_tool_arguments(
                    session_id=session_id,
                    tool_name=step.tool_name,
                    tool_args=self._render_action_step_template(
                        template=dict(step.arguments_template),
                        slots=action.slots,
                    ),
                    resolved_scenario_id=resolved_scenario_id,
                )
                action_type = action_type_for_tool(step.tool_name)
                logger.info(
                    "assistant action plan step\n"
                    "  session_id=%s\n"
                    "  turn_id=%s\n"
                    "  action=%s\n"
                    "  index=%s\n"
                    "  tool=%s\n"
                    "  arguments=%s",
                    session_id,
                    turn_id,
                    action.action_id,
                    step_index,
                    step.tool_name,
                    self._json_for_log(normalized),
                )
                tool_call = self._store.create_tool_call(
                    session_id=session_id,
                    turn_id=turn_id,
                    tool_name=step.tool_name,
                    arguments=normalized,
                    status="proposed",
                    action_type=action_type,
                )
                self._emit_event(
                    AssistantEventName.TOOL_CALL_PROPOSED,
                    session_id=session_id,
                    turn_id=turn_id,
                    data={"tool_call_id": tool_call.tool_call_id, "tool_name": step.tool_name, "arguments": normalized},
                )
                telemetry.mark_first_event()
                needs_confirmation, confirmation_action_type = self._needs_confirmation_for_tool(
                    session_id=session_id,
                    tool_name=step.tool_name,
                    tool_args=normalized,
                    fallback_action_type=action_type,
                    policy=policy,
                )
                if needs_confirmation and confirmation_action_type is not None:
                    confirmation = self._store.create_confirmation(
                        session_id=session_id,
                        turn_id=turn_id,
                        action_type=confirmation_action_type,
                        tool_name=step.tool_name,
                        arguments=normalized,
                    )
                    pending_call = self._store.complete_tool_call(
                        tool_call.tool_call_id,
                        status="confirmation_required",
                        result={},
                    )
                    usage = self._build_usage(
                        provider_id=provider_id,
                        model_id=model_id,
                        turn_handling_mode="action_plan_fast_path",
                        cache_attempted=False,
                        cache_applied=False,
                        completion_usage={},
                        tool_call_count=len(executed_calls) + 1,
                        telemetry=telemetry,
                    )
                    turn = self._store.update_turn(
                        turn_id,
                        status=AssistantTurnStatus.CONFIRMATION_REQUIRED,
                        usage=usage,
                    )
                    self._emit_event(
                        AssistantEventName.CONFIRMATION_REQUIRED,
                        session_id=session_id,
                        turn_id=turn_id,
                        data=confirmation.model_dump(mode="json"),
                    )
                    assistant_message = self._record_confirmation_required_message(
                        session_id=session_id,
                        turn_id=turn_id,
                        tool_name=step.tool_name,
                        action_type=confirmation_action_type,
                        confirmation=confirmation,
                        arguments=normalized,
                    )
                    return CreateAssistantTurnResponse(
                        turn=turn,
                        assistant_message=assistant_message,
                        confirmation=confirmation,
                        tool_calls=[*executed_calls, pending_call],
                    )
                try:
                    completed_call, result = self._execute_tool_call(
                        session_id=session_id,
                        turn_id=turn_id,
                        tool_call=tool_call,
                        telemetry=telemetry,
                    )
                except Exception as exc:
                    if isinstance(exc, ApiError) and exc.code == "map_algebra_overwrite_confirmation_required":
                        confirmation_args = dict(normalized)
                        confirmation = self._store.create_confirmation(
                            session_id=session_id,
                            turn_id=turn_id,
                            action_type=AssistantConfirmationActionType.LAUNCH_JOB,
                            tool_name=provider_call.name,
                            arguments=confirmation_args,
                        )
                        pending_call = self._store.complete_tool_call(
                            tool_call.tool_call_id,
                            status="confirmation_required",
                            result={},
                        )
                        usage = self._build_usage(
                            provider_id=active_provider_id,
                            model_id=active_model_id,
                            turn_handling_mode="model_tool_loop",
                            cache_attempted=cache_attempted,
                            cache_applied=cache_applied,
                            completion_usage=completion_usage_totals,
                            tool_call_count=len(executed_calls) + 1,
                            telemetry=telemetry,
                            fallback_used=fallback_used,
                        )
                        turn = self._store.update_turn(
                            turn_id,
                            status=AssistantTurnStatus.CONFIRMATION_REQUIRED,
                            usage=usage,
                        )
                        self._emit_event(
                            AssistantEventName.CONFIRMATION_REQUIRED,
                            session_id=session_id,
                            turn_id=turn_id,
                            data=confirmation.model_dump(mode="json"),
                        )
                        assistant_message = self._record_confirmation_required_message(
                            session_id=session_id,
                            turn_id=turn_id,
                            tool_name=provider_call.name,
                            action_type=AssistantConfirmationActionType.LAUNCH_JOB,
                            confirmation=confirmation,
                            arguments=confirmation_args,
                        )
                        return CreateAssistantTurnResponse(
                            turn=turn,
                            assistant_message=assistant_message,
                            confirmation=confirmation,
                            tool_calls=[*executed_calls, pending_call],
                        )
                    failed_call = self._store.complete_tool_call(
                        tool_call.tool_call_id,
                        status="failed",
                        result={},
                        error=str(exc),
                    )
                    usage = self._build_usage(
                        provider_id=provider_id,
                        model_id=model_id,
                        turn_handling_mode="action_plan_fast_path",
                        cache_attempted=False,
                        cache_applied=False,
                        completion_usage={},
                        tool_call_count=len(executed_calls) + 1,
                        telemetry=telemetry,
                    )
                    turn = self._store.update_turn(
                        turn_id,
                        status=AssistantTurnStatus.FAILED,
                        error=str(exc),
                        usage=usage,
                    )
                    self._emit_event(
                        AssistantEventName.ERROR,
                        session_id=session_id,
                        turn_id=turn_id,
                        data={"error": str(exc), "tool_call_id": failed_call.tool_call_id, "error_code": ERROR_TOOL_EXECUTION_FAILED},
                    )
                    return CreateAssistantTurnResponse(
                        turn=turn,
                        assistant_message=None,
                        confirmation=None,
                        tool_calls=[*executed_calls, failed_call],
                    )
                executed_calls.append(completed_call)
                turn_ctx = self._turn_hybrid_context.get(turn_id)
                if turn_ctx is not None and turn_ctx.turn_state is not None:
                    self._turn_state_manager.mark_deterministic_complete(
                        turn_ctx.turn_state,
                        action_segment_text=action.segment,
                        tool_call={
                            "tool_name": completed_call.tool_name,
                            "status": completed_call.status,
                            "arguments": dict(completed_call.arguments),
                        },
                    )
                outputs.append(
                    {
                        "action_id": action.action_id,
                        "segment": action.segment,
                        "tool_name": completed_call.tool_name,
                        "arguments": completed_call.arguments,
                        "output": result,
                    }
                )
                if completed_call.tool_name == "scenario.set_current":
                    next_scenario_id = str(result.get("scenario", {}).get("scenario_id", "")).strip()
                    if next_scenario_id:
                        resolved_scenario_id = next_scenario_id
                        self._runtime(session_id).current_scenario_id = next_scenario_id

        if followup_prompt:
            logger.info(
                "assistant action plan handoff to model loop session_id=%s turn_id=%s followup_prompt=%s executed_tools=%s",
                session_id,
                turn_id,
                followup_prompt,
                [call.tool_name for call in executed_calls],
            )
            turn = self._store.get_turn(turn_id)
            return self._run_model_tool_loop(
                session_id=session_id,
                turn_id=turn.turn_id,
                prompt=followup_prompt,
                scenario_id=resolved_scenario_id,
                provider_id=provider_id,
                model_id=model_id,
                policy=self._store.get_session(session_id).policy,
                telemetry=telemetry,
                is_command_turn=self._is_command_prompt(followup_prompt),
                resume_tool_calls=executed_calls,
                thinking=thinking,
            )

        if len(outputs) == 1:
            only = outputs[0]
            response_text = summarize_tool_result(
                str(only.get("tool_name", "")),
                only.get("output", {}),
            )
        else:
            payload = {"action_outputs": outputs}
            try:
                response_text = json.dumps(payload, ensure_ascii=True)
            except Exception:
                response_text = str(payload)
        usage = self._build_usage(
            provider_id=provider_id,
            model_id=model_id,
            turn_handling_mode="action_plan_fast_path",
            cache_attempted=False,
            cache_applied=False,
            completion_usage={},
            tool_call_count=len(executed_calls),
            telemetry=telemetry,
        )
        assistant_message, turn = self._finalize_turn_with_message(
            session_id=session_id,
            turn_id=turn_id,
            text=response_text,
            usage=usage,
            metadata={
                "action_plan_fast_path": True,
                "action_ids": [action.action_id for action in actions],
                "execution_origin": "deterministic",
            },
            outputs=self._collect_tool_outputs(executed_calls),
            telemetry=telemetry,
        )
        return CreateAssistantTurnResponse(
            turn=turn,
            assistant_message=assistant_message,
            confirmation=None,
            tool_calls=executed_calls,
        )

    @staticmethod
    def _render_action_step_template(*, template: Any, slots: dict[str, Any]) -> Any:
        placeholder_exact = re.compile(r"^\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}$")
        placeholder_any = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
        if isinstance(template, dict):
            return {
                key: AssistantService._render_action_step_template(template=value, slots=slots)
                for key, value in template.items()
            }
        if isinstance(template, list):
            return [AssistantService._render_action_step_template(template=item, slots=slots) for item in template]
        if isinstance(template, str):
            exact = placeholder_exact.match(template)
            if exact is not None:
                return slots.get(exact.group(1))

            def _replace(match: re.Match[str]) -> str:
                name = str(match.group(1))
                value = slots.get(name)
                return "" if value is None else str(value)

            return placeholder_any.sub(_replace, template)
        return template

    def _execute_action_plan_agent_step(
        self,
        *,
        session_id: str,
        turn_id: str,
        step: PlannedAgentStep,
        scenario_id: str | None,
        provider_id: str,
        model_id: str,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        allowed = set([name for name in step.allowed_tools if str(name).strip()])
        if not allowed:
            raise RuntimeError("Agent step has no allowed tools.")
        tool_schema = [
            item
            for item in list_tools_for_model()
            if str(item.get("function", {}).get("name", "")).strip() in allowed
        ]
        if not tool_schema:
            raise RuntimeError("Agent step resolved zero tool schemas after allowlist filtering.")
        conversation: list[dict[str, str]] = [
            {
                "role": "user",
                "content": (
                    f"{step.objective}\n\n"
                    "Return JSON only. Do not include prose.\n"
                    f"Output must validate this JSON schema:\n{json.dumps(step.output_schema, ensure_ascii=True)}"
                ),
            }
        ]
        for iteration in range(max(1, int(step.max_iterations))):
            if int((time.perf_counter() - start) * 1000) > int(step.timeout_ms):
                raise TimeoutError("Agent step exceeded timeout before completion.")
            completion = self._providers.complete(
                provider_id=provider_id,
                model_id=model_id,
                system_prompt=(
                    "You are a constrained sub-agent for deterministic execution.\n"
                    "Use only provided tools when needed.\n"
                    "Return strict JSON matching the requested schema."
                ),
                conversation=conversation,
                tool_schema=tool_schema,
                max_output_tokens=int(step.max_output_tokens),
                thinking=False,
            )
            tool_calls = list(completion.tool_calls)
            if not tool_calls:
                payload = _extract_json_object(completion.text)
                if not isinstance(payload, dict):
                    raise RuntimeError("Agent step returned non-JSON or non-object output.")
                try:
                    jsonschema.validate(instance=payload, schema=step.output_schema)
                except Exception as exc:
                    raise RuntimeError(f"Agent step output schema validation failed: {exc}") from exc
                logger.info(
                    "assistant action plan agent step completed session_id=%s turn_id=%s iteration=%s output=%s",
                    session_id,
                    turn_id,
                    iteration + 1,
                    self._json_for_log(payload),
                )
                return {str(key): value for key, value in payload.items()}
            if len(tool_calls) > 4:
                tool_calls = tool_calls[:4]
            for tool_call in tool_calls:
                tool_name = str(tool_call.name).strip()
                if tool_name not in allowed:
                    raise RuntimeError(f"Agent step attempted disallowed tool: {tool_name}")
                arguments = _parse_tool_arguments_object(tool_call.arguments)
                result = execute_tool(
                    self._tool_services,
                    tool_name=tool_name,
                    arguments=self._normalize_tool_arguments(
                        session_id=session_id,
                        tool_name=tool_name,
                        tool_args=arguments,
                        resolved_scenario_id=scenario_id,
                    ),
                )
                compact = compact_tool_result_for_model_context(tool_name, result)
                conversation.append(
                    {
                        "role": "assistant",
                        "content": (
                            f"Tool `{tool_name}` result:\n"
                            f"{json.dumps(compact, ensure_ascii=True, default=str)}"
                        ),
                    }
                )
        raise RuntimeError("Agent step exhausted iteration budget without valid JSON output.")

    def _execute_explicit_tool_sequence_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        parsed_calls: list[tuple[str, dict[str, Any]]],
        resolved_scenario_id: str | None,
        provider_id: str,
        model_id: str,
        telemetry: TurnTelemetry,
    ) -> CreateAssistantTurnResponse:
        outputs: list[dict[str, Any]] = []
        executed_calls: list[AssistantToolCall] = []
        for index, (tool_name, raw_args) in enumerate(parsed_calls):
            normalized = self._normalize_tool_arguments(
                session_id=session_id,
                tool_name=tool_name,
                tool_args=dict(raw_args),
                resolved_scenario_id=resolved_scenario_id,
            )
            logger.info(
                "assistant explicit tool sequence call session_id=%s turn_id=%s index=%s tool=%s arguments=%s",
                session_id,
                turn_id,
                index + 1,
                tool_name,
                self._json_for_log(normalized),
            )
            tool_call = self._store.create_tool_call(
                session_id=session_id,
                turn_id=turn_id,
                tool_name=tool_name,
                arguments=normalized,
                status="proposed",
                action_type=None,
            )
            self._emit_event(
                AssistantEventName.TOOL_CALL_PROPOSED,
                session_id=session_id,
                turn_id=turn_id,
                data={"tool_call_id": tool_call.tool_call_id, "tool_name": tool_name, "arguments": normalized},
            )
            telemetry.mark_first_event()
            try:
                completed_call, result = self._execute_tool_call(
                    session_id=session_id,
                    turn_id=turn_id,
                    tool_call=tool_call,
                    telemetry=telemetry,
                )
            except Exception as exc:
                failed_call = self._store.complete_tool_call(
                    tool_call.tool_call_id,
                    status="failed",
                    result={},
                    error=str(exc),
                )
                usage = self._build_usage(
                    provider_id=provider_id,
                    model_id=model_id,
                    turn_handling_mode="explicit_tool_sequence",
                    cache_attempted=False,
                    cache_applied=False,
                    completion_usage={},
                    tool_call_count=len(executed_calls) + 1,
                    telemetry=telemetry,
                )
                turn = self._store.update_turn(
                    turn_id,
                    status=AssistantTurnStatus.FAILED,
                    error=str(exc),
                    usage=usage,
                )
                self._emit_event(
                    AssistantEventName.ERROR,
                    session_id=session_id,
                    turn_id=turn_id,
                    data={"error": str(exc), "tool_call_id": failed_call.tool_call_id},
                )
                return CreateAssistantTurnResponse(
                    turn=turn,
                    assistant_message=None,
                    confirmation=None,
                    tool_calls=[*executed_calls, failed_call],
                )
            executed_calls.append(completed_call)
            logger.info(
                "assistant explicit tool sequence result session_id=%s turn_id=%s index=%s tool=%s result=%s",
                session_id,
                turn_id,
                index + 1,
                completed_call.tool_name,
                self._json_for_log(result),
            )
            outputs.append(
                {
                    "tool_name": completed_call.tool_name,
                    "arguments": completed_call.arguments,
                    "output": result,
                }
            )

        payload = {"tool_outputs": outputs}
        try:
            response_text = json.dumps(payload, ensure_ascii=True)
        except Exception:
            response_text = str(payload)
        usage = self._build_usage(
            provider_id=provider_id,
            model_id=model_id,
            turn_handling_mode="explicit_tool_sequence",
            cache_attempted=False,
            cache_applied=False,
            completion_usage={},
            tool_call_count=len(executed_calls),
            telemetry=telemetry,
        )
        assistant_message, turn = self._finalize_turn_with_message(
            session_id=session_id,
            turn_id=turn_id,
            text=response_text,
            usage=usage,
            metadata={"explicit_tool_sequence": True},
            outputs=self._collect_tool_outputs(executed_calls),
            telemetry=telemetry,
        )
        return CreateAssistantTurnResponse(
            turn=turn,
            assistant_message=assistant_message,
            confirmation=None,
            tool_calls=executed_calls,
        )

    def _execute_tool_call(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_call: AssistantToolCall,
        execution_arguments: dict[str, Any] | None = None,
        telemetry: TurnTelemetry,
    ) -> tuple[AssistantToolCall, dict[str, Any]]:
        started_call = tool_call
        if tool_call.status != "started":
            started_call = self._store.complete_tool_call(tool_call.tool_call_id, status="started", result={})
        execute_args = dict(execution_arguments) if execution_arguments is not None else started_call.arguments
        self._emit_event(
            AssistantEventName.TOOL_CALL_STARTED,
            session_id=session_id,
            turn_id=turn_id,
            data={"tool_call_id": started_call.tool_call_id, "tool_name": started_call.tool_name},
        )
        logger.info(
            "assistant tool execute start\n"
            "  session_id=%s\n"
            "  turn_id=%s\n"
            "  tool_call_id=%s\n"
            "  tool=%s\n"
            "  arguments=%s",
            session_id,
            turn_id,
            started_call.tool_call_id,
            started_call.tool_name,
            self._json_for_log(execute_args),
        )
        telemetry.mark_first_event()
        if started_call.tool_name == "scenario.revoke_script_overwrite":
            result = self._revoke_script_overwrite(session_id=session_id, arguments=execute_args)
        else:
            result = execute_tool(
                self._tool_services,  # type: ignore[arg-type]
                tool_name=started_call.tool_name,
                arguments=execute_args,
            )
        outputs = self._extract_outputs(result)
        logger.info(
            "assistant tool execute result\n"
            "  session_id=%s\n"
            "  turn_id=%s\n"
            "  tool_call_id=%s\n"
            "  tool=%s\n"
            "  result=%s",
            session_id,
            turn_id,
            started_call.tool_call_id,
            started_call.tool_name,
            self._json_for_log(result),
        )
        self._apply_tool_side_effects(
            session_id=session_id,
            turn_id=turn_id,
            tool_name=started_call.tool_name,
            arguments=execute_args,
            result=result,
        )
        completed_call = self._store.complete_tool_call(
            started_call.tool_call_id,
            status="completed",
            result=result,
            outputs=outputs,
        )
        self._emit_event(
            AssistantEventName.TOOL_CALL_COMPLETED,
            session_id=session_id,
            turn_id=turn_id,
            data={
                "tool_call_id": completed_call.tool_call_id,
                "tool_name": completed_call.tool_name,
                "has_outputs": bool(outputs),
                "output_count": len(outputs),
            },
        )
        telemetry.mark_first_event()
        return completed_call, result

    def _finalize_turn_with_message(
        self,
        *,
        session_id: str,
        turn_id: str,
        text: str,
        usage: dict[str, Any],
        metadata: dict[str, Any],
        outputs: list[dict[str, Any]] | None,
        telemetry: TurnTelemetry,
        emit_final_delta: bool = True,
    ) -> tuple[AssistantMessage, Any]:
        message_text = text.strip() or "No response returned by provider."
        metadata_payload = dict(metadata or {})
        usage_payload = dict(usage or {})
        turn_ctx = self._turn_hybrid_context.get(turn_id)
        if turn_ctx is not None:
            usage_payload.update(turn_ctx.latencies_ms)
            if turn_ctx.entity_resolution:
                total_mentions = 0
                resolved_mentions = 0
                ambiguous_mentions = 0
                for item in turn_ctx.entity_resolution.values():
                    total_mentions += len(item.mentions)
                    resolved_mentions += len([mention for mention in item.mentions if str(mention.resolved_id or "").strip()])
                    ambiguous_mentions += len(item.ambiguities)
                clarification_rate = (float(ambiguous_mentions) / float(total_mentions)) if total_mentions > 0 else 0.0
                metadata_payload["entity_resolution_metrics"] = {
                    "segment_count": len(turn_ctx.entity_resolution),
                    "total_mentions": total_mentions,
                    "resolved_mentions": resolved_mentions,
                    "ambiguous_mentions": ambiguous_mentions,
                    "clarification_rate": round(clarification_rate, 4),
                }
                metadata_payload["entity_resolution_segments"] = [
                    item.as_dict()
                    for item in turn_ctx.entity_resolution.values()
                ]
            if turn_ctx.execution_plan is not None:
                execution_plan_segments = [
                    {
                        "segment_id": item.segment_id,
                        "execution_mode": item.execution_mode,
                        "selected_recipe_id": item.selected_recipe_id,
                        "prerequisite_count": item.prerequisite_count,
                        "required": item.required,
                        "classification": {
                            "label": item.classification.segment_class,
                            "confidence": item.classification.confidence,
                            "command": item.classification.command,
                            "product_type": item.classification.product_type,
                            "intent_family": item.classification.intent_family,
                            "intent_properties": dict(item.classification.intent_properties),
                            "pixel_type": item.classification.pixel_type,
                            "sources": list(item.classification.sources),
                        },
                        "entity_resolution": (
                            turn_ctx.entity_resolution[item.segment_id].as_dict()
                            if item.segment_id in turn_ctx.entity_resolution
                            else None
                        ),
                    }
                    for item in turn_ctx.execution_plan.segments
                ]
                metadata_payload["execution_plan_segments"] = execution_plan_segments
                tool_calls = self._store.list_turn_tool_calls(turn_id)
                aggregate_status, segment_outcomes = compute_success_semantics(
                    execution_plan_segments=execution_plan_segments,
                    tool_calls=tool_calls,
                    current_scenario_id=self._runtime(session_id).current_scenario_id,
                )
                metadata_payload["aggregate_status"] = aggregate_status
                metadata_payload["segment_outcomes"] = [
                    {
                        "segment_id": item.segment_id,
                        "prompt_class": item.prompt_class,
                        "required": item.required,
                        "status": item.status,
                        "postcondition_checked": item.postcondition_checked,
                        "postcondition_passed": item.postcondition_passed,
                    }
                    for item in segment_outcomes
                ]
                self._emit_event(
                    AssistantEventName.TURN_STATUS_FINALIZED,
                    session_id=session_id,
                    turn_id=turn_id,
                    data={"aggregate_status": aggregate_status},
                )
            if turn_ctx.turn_state is not None:
                merge_payload = self._turn_state_manager.build_merge(turn_ctx.turn_state)
                metadata_payload["turn_state_merge"] = merge_payload
                self._emit_event(
                    AssistantEventName.TURN_MERGE_COMPLETED,
                    session_id=session_id,
                    turn_id=turn_id,
                    data={"segment_count": len(merge_payload.get("segments", []))},
                )
        if emit_final_delta:
            self._emit_event(
                AssistantEventName.DELTA,
                session_id=session_id,
                turn_id=turn_id,
                data={"text_delta": message_text},
            )
        telemetry.mark_first_event()
        assistant_message = self._store.add_message(
            session_id=session_id,
            role=AssistantRole.ASSISTANT,
            content=message_text,
            turn_id=turn_id,
            metadata=metadata_payload,
            outputs=outputs,
        )
        turn = self._store.update_turn(
            turn_id,
            status=AssistantTurnStatus.COMPLETED,
            usage=usage_payload,
        )
        self._emit_event(
            AssistantEventName.TURN_COMPLETED,
            session_id=session_id,
            turn_id=turn_id,
            data={"status": turn.status.value, "usage": usage_payload},
        )
        return assistant_message, turn

    def _record_confirmation_required_message(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_name: str,
        action_type: AssistantConfirmationActionType,
        confirmation: AssistantConfirmation,
        arguments: dict[str, Any],
    ) -> AssistantMessage:
        logger.info(
            "assistant confirmation required session_id=%s turn_id=%s tool=%s action_type=%s confirmation_id=%s arguments=%s",
            session_id,
            turn_id,
            tool_name,
            action_type.value,
            confirmation.confirmation_id,
            self._json_for_log(arguments),
        )
        message_text = (
            f"Confirmation required for `{tool_name}` ({action_type.value}). "
            f"Approve or deny to continue. confirmation_id={confirmation.confirmation_id}"
        )
        self._emit_event(
            AssistantEventName.DELTA,
            session_id=session_id,
            turn_id=turn_id,
            data={"text_delta": message_text},
        )
        return self._store.add_message(
            session_id=session_id,
            role=AssistantRole.ASSISTANT,
            content=message_text,
            turn_id=turn_id,
            metadata={
                "confirmation_id": confirmation.confirmation_id,
                "tool_name": tool_name,
                "action_type": action_type.value,
            },
        )

    def _normalize_tool_arguments(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        resolved_scenario_id: str | None,
    ) -> dict[str, Any]:
        normalized = dict(tool_args)
        if tool_name in {
            "scenario.list_scripts",
            "scenario.list_notebooks",
            "scenario.run_script",
            "scenario.run_marimo_notebook",
            "scenario.write_script",
            "scenario.write_run_script",
            "scenario.revoke_script_overwrite",
            "scenario.rag_ingest",
            "product.list",
            "layer.list_visible",
            "layer.update_state",
            "scenario.import_geotiff",
            "scenario.move_path",
            "raster.calculate",
            "raster.transform",
            "artifact.describe_geotiff",
            "artifact.preview_geotiff",
            "artifact.stats_geotiff",
            "artifact.describe_table",
            "artifact.describe_plot",
        }:
            if not str(normalized.get("scenario_id", "")).strip() and resolved_scenario_id:
                normalized["scenario_id"] = resolved_scenario_id

        if tool_name == "layer.list_visible" and "base_layer_visible" not in normalized:
            normalized["base_layer_visible"] = bool(self._runtime(session_id).base_layer_visible)

        if tool_name in {"scenario.write_script", "scenario.write_run_script"}:
            if self._script_exists(normalized):
                normalized["overwrite"] = True
        if tool_name == "raster.calculate":
            normalized = self._sanitize_raster_calculate_arguments(normalized)
        if tool_name == "raster.transform":
            normalized = self._sanitize_raster_transform_arguments(normalized)
        schema = tool_argument_schema_for_tool(tool_name)
        repaired, repair_outcome = self._argument_repairer.repair(
            tool_name=tool_name,
            arguments=normalized,
            scenario_id=resolved_scenario_id,
            schema=schema,
        )
        normalized = repaired
        self._log_repair_outcome(tool_name=tool_name, outcome=repair_outcome)
        return normalized

    def _log_repair_outcome(self, *, tool_name: str, outcome: RepairOutcome) -> None:
        if not outcome.repair_attempted:
            return
        if not outcome.repair_applied and outcome.repair_status == "not_needed":
            return
        logger.info(
            "assistant tool argument repair tool=%s status=%s applied=%s rules=%s warnings=%s",
            tool_name,
            outcome.repair_status,
            outcome.repair_applied,
            outcome.repair_rules,
            outcome.repair_warning_codes,
        )

    def _sanitize_raster_calculate_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(arguments)
        schema = tool_argument_schema_for_tool("raster.calculate")
        properties = schema.get("properties", {})
        allowed_top_level = set(properties.keys()) if isinstance(properties, dict) else set()
        if allowed_top_level:
            sanitized = {key: value for key, value in sanitized.items() if key in allowed_top_level}

        required = {"scenario_id", "expression", "inputs"}
        sanitized = {
            key: value
            for key, value in sanitized.items()
            if key in required or value is not None
        }

        if "overwrite_mode" in sanitized:
            overwrite_mode = str(sanitized.get("overwrite_mode", "")).strip().lower()
            if overwrite_mode in {"ask", "never", "always"}:
                sanitized["overwrite_mode"] = overwrite_mode
            else:
                sanitized.pop("overwrite_mode", None)

        if "mode" in sanitized:
            mode = str(sanitized.get("mode", "")).strip().lower()
            if mode in {"queued", "immediate"}:
                sanitized["mode"] = mode
            else:
                sanitized.pop("mode", None)

        if "resampling" in sanitized:
            resampling = str(sanitized.get("resampling", "")).strip().lower()
            if resampling in {"nearest", "bilinear", "cubic"}:
                sanitized["resampling"] = resampling
            else:
                sanitized.pop("resampling", None)

        positive_integer_fields = {
            "patch_width",
            "patch_height",
            "chunk_time_count",
            "buffer_count",
            "poll_timeout_ms",
        }
        for field_name in positive_integer_fields:
            if field_name not in sanitized:
                continue
            value = sanitized.get(field_name)
            try:
                parsed = int(value)
            except Exception:
                sanitized.pop(field_name, None)
                continue
            if parsed <= 0:
                sanitized.pop(field_name, None)
            else:
                sanitized[field_name] = parsed

        raw_inputs = sanitized.get("inputs")
        if isinstance(raw_inputs, dict):
            fixed_inputs: dict[str, Any] = {}
            for key, value in raw_inputs.items():
                name = str(key).strip()
                if not name:
                    continue
                if isinstance(value, str) and value.strip():
                    fixed_inputs[name] = {"relative_path": value.strip()}
                    continue
                if not isinstance(value, dict):
                    continue
                cleaned_input: dict[str, Any] = {}
                relative_path = value.get("relative_path")
                if isinstance(relative_path, str) and relative_path.strip():
                    cleaned_input["relative_path"] = relative_path.strip()
                product_id = value.get("product_id")
                if isinstance(product_id, str) and product_id.strip():
                    cleaned_input["product_id"] = product_id.strip()
                signal = value.get("signal")
                if isinstance(signal, str) and signal.strip():
                    cleaned_input["signal"] = signal.strip()
                if cleaned_input:
                    fixed_inputs[name] = cleaned_input
            sanitized["inputs"] = fixed_inputs

        raw_publish_layer = sanitized.get("publish_layer")
        if isinstance(raw_publish_layer, dict):
            cleaned_layer: dict[str, Any] = {}
            enabled = raw_publish_layer.get("enabled")
            if isinstance(enabled, bool):
                cleaned_layer["enabled"] = enabled
            title = raw_publish_layer.get("title")
            if isinstance(title, str) and title.strip():
                cleaned_layer["title"] = title.strip()
            visible = raw_publish_layer.get("visible")
            if isinstance(visible, bool):
                cleaned_layer["visible"] = visible
            opacity = raw_publish_layer.get("opacity")
            if isinstance(opacity, (int, float)):
                opacity_value = float(opacity)
                if 0.0 <= opacity_value <= 1.0:
                    cleaned_layer["opacity"] = opacity_value
            z_index = raw_publish_layer.get("z_index")
            if isinstance(z_index, int):
                cleaned_layer["z_index"] = int(z_index)
            style = raw_publish_layer.get("style")
            if isinstance(style, dict):
                cleaned_layer["style"] = dict(style)
            on_existing = raw_publish_layer.get("on_existing")
            if isinstance(on_existing, str) and on_existing in {"update", "error", "new"}:
                cleaned_layer["on_existing"] = on_existing
            transparent_background = raw_publish_layer.get("transparent_background")
            if isinstance(transparent_background, bool):
                cleaned_layer["transparent_background"] = transparent_background
            if cleaned_layer:
                sanitized["publish_layer"] = cleaned_layer
            else:
                sanitized.pop("publish_layer", None)
        elif raw_publish_layer is not None:
            sanitized.pop("publish_layer", None)

        return sanitized

    def _sanitize_raster_transform_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(arguments)
        schema = tool_argument_schema_for_tool("raster.transform")
        properties = schema.get("properties", {})
        allowed_top_level = set(properties.keys()) if isinstance(properties, dict) else set()
        if allowed_top_level:
            sanitized = {key: value for key, value in sanitized.items() if key in allowed_top_level}

        required = {"scenario_id", "script", "inputs"}
        sanitized = {
            key: value
            for key, value in sanitized.items()
            if key in required or value is not None
        }

        if "overwrite_mode" in sanitized:
            overwrite_mode = str(sanitized.get("overwrite_mode", "")).strip().lower()
            if overwrite_mode in {"ask", "never", "always"}:
                sanitized["overwrite_mode"] = overwrite_mode
            else:
                sanitized.pop("overwrite_mode", None)
        elif "overwrite" in sanitized:
            legacy_overwrite = sanitized.get("overwrite")
            if isinstance(legacy_overwrite, bool):
                sanitized["overwrite_mode"] = "always" if legacy_overwrite else "never"

        if "overwrite" in sanitized and not isinstance(sanitized.get("overwrite"), bool):
            sanitized.pop("overwrite", None)

        if "mode" in sanitized:
            mode = str(sanitized.get("mode", "")).strip().lower()
            if mode in {"queued", "immediate"}:
                sanitized["mode"] = mode
            else:
                sanitized.pop("mode", None)

        if "resampling" in sanitized:
            resampling = str(sanitized.get("resampling", "")).strip().lower()
            if resampling in {"nearest", "bilinear", "cubic"}:
                sanitized["resampling"] = resampling
            else:
                sanitized.pop("resampling", None)

        positive_integer_fields = {
            "patch_width",
            "patch_height",
            "chunk_time_count",
            "buffer_count",
            "poll_timeout_ms",
            "spatial_halo_pixels",
        }
        for field_name in positive_integer_fields:
            if field_name not in sanitized:
                continue
            raw = sanitized.get(field_name)
            if isinstance(raw, bool):
                sanitized.pop(field_name, None)
                continue
            try:
                value = int(raw)
            except Exception:
                sanitized.pop(field_name, None)
                continue
            if value < 0 and field_name == "spatial_halo_pixels":
                sanitized.pop(field_name, None)
                continue
            if value < 1 and field_name != "spatial_halo_pixels":
                sanitized.pop(field_name, None)
                continue
            sanitized[field_name] = value

        number_fields = {"observer_elevation_meters", "time_step_hours"}
        for field_name in number_fields:
            if field_name not in sanitized:
                continue
            raw = sanitized.get(field_name)
            if isinstance(raw, bool):
                sanitized.pop(field_name, None)
                continue
            try:
                sanitized[field_name] = float(raw)
            except Exception:
                sanitized.pop(field_name, None)

        return sanitized

    def _needs_confirmation_for_tool(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        fallback_action_type: Any,
        policy: Any,
    ) -> tuple[bool, Any]:
        if tool_name == "scenario.import_geotiff" and self._scenario_relative_file_exists(tool_args):
            return False, None
        if tool_name in {"scenario.run_script", "scenario.run_marimo_notebook"}:
            script_key = self._script_key_from_arguments(tool_args)
            if script_key and script_key in self._runtime(session_id).script_run_allowlist:
                return False, None
            return True, fallback_action_type
        if tool_name == "scenario.write_run_script":
            script_key = self._script_key_from_arguments(tool_args)
            runtime = self._runtime(session_id)
            if script_key and script_key not in runtime.overwrite_allowlist and self._script_exists(tool_args):
                return True, AssistantConfirmationActionType.WRITE_NOTEBOOK
            if script_key and script_key in runtime.script_run_allowlist:
                return False, None
            return True, AssistantConfirmationActionType.LAUNCH_JOB
        if tool_name == "scenario.write_script":
            should_confirm, action_type = self._write_script_confirmation_state(
                session_id=session_id,
                tool_args=tool_args,
            )
            return should_confirm, action_type
        if tool_name in {"raster.calculate", "raster.transform"}:
            overwrite_mode = str(tool_args.get("overwrite_mode", "ask")).strip().lower() or "ask"
            if overwrite_mode == "ask" and self._raster_output_exists(tool_name=tool_name, tool_args=tool_args):
                return True, AssistantConfirmationActionType.LAUNCH_JOB
        if fallback_action_type is None:
            return False, None
        return self._policy.requires_confirmation(action_type=fallback_action_type, policy=policy), fallback_action_type

    def _write_script_confirmation_state(
        self,
        *,
        session_id: str,
        tool_args: dict[str, Any],
    ) -> tuple[bool, Any]:
        script_key = self._script_key_from_arguments(tool_args)
        if not script_key:
            return False, None
        runtime = self._runtime(session_id)
        if script_key in runtime.created_scripts:
            return False, None
        if script_key in runtime.overwrite_allowlist:
            return False, None
        if not self._script_exists(tool_args):
            return False, None
        return True, AssistantConfirmationActionType.WRITE_NOTEBOOK

    def _script_exists(self, tool_args: dict[str, Any]) -> bool:
        scenario_id = str(tool_args.get("scenario_id", "")).strip()
        relative_path = str(tool_args.get("relative_path", "")).strip()
        if not scenario_id or not relative_path:
            return False
        try:
            scenario = self._tool_services.scenario_service.get_scenario(scenario_id)
            scenario_root = Path(scenario.directory).expanduser().resolve()
            target = self._resolve_script_path(scenario_root, relative_path)
            return target.exists()
        except Exception:
            return False

    def _scenario_relative_file_exists(self, tool_args: dict[str, Any]) -> bool:
        scenario_id = str(tool_args.get("scenario_id", "")).strip()
        source_path = str(tool_args.get("source_path", "")).strip()
        if not scenario_id or not source_path:
            return False
        source = Path(source_path).expanduser()
        if source.is_absolute():
            return False
        normalized = source_path.replace("\\", "/").strip().lstrip("./")
        if not normalized:
            return False
        try:
            self._tool_services.scenario_service.resolve_scenario_file(scenario_id, normalized)
            return True
        except Exception:
            return False

    def _raster_output_exists(self, *, tool_name: str, tool_args: dict[str, Any]) -> bool:
        if tool_name != "raster.calculate":
            return False
        scenario_id = str(tool_args.get("scenario_id", "")).strip()
        output_relative_path = str(tool_args.get("output_relative_path", "")).strip()
        if not scenario_id or not output_relative_path:
            return False
        try:
            scenario = self._tool_services.scenario_service.get_scenario(scenario_id)
            scenario_root = Path(scenario.directory).expanduser().resolve()
            output_rel = output_relative_path.replace("\\", "/").lstrip("/")
            output_path = (scenario_root / output_rel).resolve()
            if scenario_root != output_path and scenario_root not in output_path.parents:
                return False
            return output_path.exists()
        except Exception:
            return False

    @staticmethod
    def _apply_confirmation_argument_overrides(
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        updated = dict(arguments)
        if tool_name in {"raster.calculate", "raster.transform"}:
            overwrite_mode = str(updated.get("overwrite_mode", "")).strip().lower()
            if overwrite_mode == "ask":
                updated["overwrite_mode"] = "always"
            elif not overwrite_mode:
                updated["overwrite_mode"] = "always"
            updated["overwrite"] = True
        return updated

    def _apply_tool_side_effects(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        runtime = self._runtime(session_id)
        if tool_name == "scenario.set_current":
            scenario = result.get("scenario")
            if isinstance(scenario, dict) and str(result.get("status", "")) == "selected":
                scenario_id = str(scenario.get("scenario_id", "")).strip()
                if scenario_id:
                    runtime.current_scenario_id = scenario_id
                    extent = scenario.get("dem_extent")
                    self._emit_event(
                        AssistantEventName.SCENARIO_CHANGED,
                        session_id=session_id,
                        turn_id=turn_id,
                        data={
                            "scenario_id": scenario_id,
                            "scenario_name": str(scenario.get("name", "")),
                            "dem_extent": extent,
                        },
                    )
                    self._store.add_message(
                        session_id=session_id,
                        role=AssistantRole.SYSTEM,
                        turn_id=turn_id,
                        content=f"Scenario switched to {scenario_id}.",
                        metadata={
                            "kind": "scenario_change",
                            "scenario_id": scenario_id,
                            "scenario_name": str(scenario.get("name", "")),
                        },
                    )
        if tool_name in {"scenario.write_script", "scenario.write_run_script"}:
            script_key = self._script_key_from_arguments(arguments)
            if not script_key:
                return
            existed_before = bool(result.get("existed_before", False))
            if not existed_before:
                runtime.created_scripts.add(script_key)

    def _revoke_script_overwrite(self, *, session_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        script_key = self._script_key_from_arguments(arguments)
        if not script_key:
            return {"revoked": False, "message": "scenario_id and relative_path are required."}
        runtime = self._runtime(session_id)
        removed = script_key in runtime.overwrite_allowlist
        runtime.overwrite_allowlist.discard(script_key)
        return {"revoked": removed, "script_key": script_key}

    def _script_key_from_arguments(self, arguments: dict[str, Any]) -> str | None:
        scenario_id = str(arguments.get("scenario_id", "")).strip()
        relative_path = str(arguments.get("relative_path", "")).strip()
        if not scenario_id or not relative_path:
            return None
        rel = relative_path.replace("\\", "/").strip().lstrip("/")
        rel = "/".join([part for part in rel.split("/") if part and part != "."])
        if not rel:
            return None
        return f"{scenario_id}:{rel.lower()}"

    @staticmethod
    def _resolve_script_path(scenario_root: Path, relative_path: str) -> Path:
        rel = relative_path.replace("\\", "/").strip().lstrip("/")
        if ".." in [part for part in rel.split("/") if part]:
            raise ValueError("Path traversal is not allowed.")
        target = (scenario_root / rel).resolve()
        if scenario_root != target and scenario_root not in target.parents:
            raise PermissionError("Path escapes scenario root.")
        return target

    def _resolve_active_scenario_directory(self, scenario_id: str | None) -> str | None:
        scenario_key = str(scenario_id or "").strip()
        if not scenario_key:
            return None
        try:
            getter = getattr(self._tool_services.scenario_service, "get_scenario")
        except Exception:
            return None
        try:
            scenario = getter(scenario_key)
            directory = str(getattr(scenario, "directory", "")).strip()
            return directory or None
        except Exception as exc:
            logger.warning(
                "Failed to resolve scenario directory for external agent turn scenario_id=%s error=%s",
                scenario_key,
                exc,
            )
            return None

    def _run_external_agent_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        prompt: str,
        scenario_id: str | None,
        provider_id: str,
        model_id: str,
        telemetry: TurnTelemetry,
        is_command_turn: bool,
        access_mode: str | None = None,
        thinking: bool | str | None = None,
    ) -> CreateAssistantTurnResponse:
        if not provider_id.strip() or not model_id.strip():
            raise RuntimeError("External MCP agent provider/model must be specified.")
        scenario_directory = self._resolve_active_scenario_directory(scenario_id)
        history = self._store.list_messages(session_id, limit=120)
        compacted_summary = self._latest_compacted_summary(history)
        conversation = build_conversation(history)
        tool_schema_snapshot = list_tools_schema()
        base_system_prompt = build_system_prompt(
            scenario_id=scenario_id,
            scenario_directory=scenario_directory,
            capabilities_text=capabilities_text(),
            compacted_summary=compacted_summary,
            persistent_constraints=self._runtime(session_id).constraints_text,
        )
        system_prompt = self._augment_external_agent_system_prompt(
            base_prompt=base_system_prompt,
            tool_schema=tool_schema_snapshot,
        )
        cache_context = build_cache_context(
            provider_id=provider_id,
            model_id=model_id,
            system_prompt=system_prompt,
            tool_schema=tool_schema_snapshot,
            scenario_id=scenario_id,
            compacted_summary=compacted_summary,
        )
        streamed_delta = False

        def _on_delta(delta: str) -> None:
            nonlocal streamed_delta
            text_delta = str(delta or "")
            if not text_delta:
                return
            streamed_delta = True
            self._emit_event(
                AssistantEventName.DELTA,
                session_id=session_id,
                turn_id=turn_id,
                data={"text_delta": text_delta},
            )
            telemetry.mark_first_event()

        max_output_tokens = self._completion_token_budget(prompt=prompt, is_command_turn=is_command_turn)
        active_model_id = model_id
        completion: ProviderCompletion | None = None
        accumulated_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_prompt_tokens": 0,
        }
        fallback_used = False
        fallback_kind: str | None = None
        candidate_models = [model_id]
        for candidate in self._catalog_models_for_provider(provider_id):
            if candidate and candidate not in candidate_models:
                candidate_models.append(candidate)

        last_exc: Exception | None = None
        for idx, candidate_model in enumerate(candidate_models):
            try:
                completion = self._providers.complete(
                    provider_id=provider_id,
                    model_id=candidate_model,
                    system_prompt=system_prompt,
                    conversation=conversation,
                    session_id=session_id,
                    on_delta=_on_delta,
                    cache_context=cache_context,
                    tool_schema=[],
                    max_output_tokens=max_output_tokens,
                    thinking=thinking,
                    access_mode=access_mode,
                    scenario_working_directory=scenario_directory,
                )
                accumulated_usage = self._sum_usage(accumulated_usage, completion.usage)
                active_model_id = candidate_model
                if idx > 0:
                    logger.warning(
                        "assistant external-agent model fallback applied provider=%s requested_model=%s selected_model=%s",
                        provider_id,
                        model_id,
                        candidate_model,
                    )
                break
            except Exception as exc:
                last_exc = exc
                if idx < len(candidate_models) - 1 and self._is_model_not_found_error(exc):
                    logger.warning(
                        "assistant external-agent model unavailable provider=%s model=%s; trying fallback model",
                        provider_id,
                        candidate_model,
                    )
                    continue
                raise
        if completion is None:
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("External MCP agent completion failed without result.")
        if completion.tool_calls:
            logger.warning(
                "External MCP agent provider returned %s tool call(s); ignoring provider-side tool calls for %s/%s.",
                len(completion.tool_calls),
                provider_id,
                active_model_id,
            )
        if self._looks_like_unknown_mcp_server_error(completion.text):
            tool_names = [
                str(item.get("name", "")).strip()
                for item in tool_schema_snapshot
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            ]
            logger.warning(
                "assistant external-agent reported unknown MCP server provider=%s model=%s turn_id=%s; retrying with explicit tool-call guidance",
                provider_id,
                active_model_id,
                turn_id,
            )
            retry_prompt = self._build_external_mcp_retry_prompt(
                original_prompt=prompt,
                tool_names=tool_names,
            )
            retry_conversation = [
                *conversation,
                {"role": "assistant", "content": completion.text.strip()},
                {"role": "user", "content": retry_prompt},
            ]
            try:
                retry_completion = self._providers.complete(
                    provider_id=provider_id,
                    model_id=active_model_id,
                    system_prompt=system_prompt,
                    conversation=retry_conversation,
                    session_id=session_id,
                    on_delta=_on_delta,
                    cache_context=cache_context,
                    tool_schema=[],
                    max_output_tokens=max_output_tokens,
                    thinking=thinking,
                    access_mode=access_mode,
                    scenario_working_directory=scenario_directory,
                )
                accumulated_usage = self._sum_usage(accumulated_usage, retry_completion.usage)
                if retry_completion.text.strip():
                    completion = retry_completion
                else:
                    logger.warning(
                        "assistant external-agent MCP retry returned empty response provider=%s model=%s turn_id=%s",
                        provider_id,
                        active_model_id,
                        turn_id,
                    )
            except Exception as exc:
                logger.warning(
                    "assistant external-agent MCP retry failed provider=%s model=%s turn_id=%s error=%s",
                    provider_id,
                    active_model_id,
                    turn_id,
                    exc,
                )
        if self._looks_like_unknown_mcp_server_error(completion.text):
            fallback_text = self._run_explicit_tool_sequence_fallback(
                session_id=session_id,
                turn_id=turn_id,
                prompt=prompt,
                resolved_scenario_id=scenario_id,
            )
            if fallback_text is not None:
                logger.warning(
                    "assistant external-agent used explicit tool sequence fallback provider=%s model=%s turn_id=%s",
                    provider_id,
                    active_model_id,
                    turn_id,
                )
                completion = ProviderCompletion(
                    text=fallback_text,
                    finish_reason="stop",
                    usage={
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "cached_prompt_tokens": 0,
                    },
                    cache_attempted=completion.cache_attempted,
                    cache_applied=completion.cache_applied,
                )
                fallback_used = True
                fallback_kind = "explicit_tool_sequence_unknown_mcp_server"

        usage = self._build_usage(
            provider_id=provider_id,
            model_id=active_model_id,
            turn_handling_mode="external_mcp_agent",
            cache_attempted=completion.cache_attempted,
            cache_applied=completion.cache_applied,
            completion_usage=accumulated_usage,
            tool_call_count=0,
            telemetry=telemetry,
            fallback_used=fallback_used,
        )
        assistant_message, turn = self._finalize_turn_with_message(
            session_id=session_id,
            turn_id=turn_id,
            text=completion.text.strip() or "No response returned by provider.",
            usage=usage,
            metadata={
                "provider_id": provider_id,
                "model_id": active_model_id,
                "usage": usage,
                "execution_mode": "external_mcp_agent",
                "access_mode": access_mode,
                "fallback_used": fallback_used,
                "fallback_kind": fallback_kind,
            },
            outputs=None,
            telemetry=telemetry,
            emit_final_delta=not streamed_delta,
        )
        if not completion.text.strip():
            logger.warning(
                "assistant external provider returned empty text provider=%s model=%s turn_id=%s session_id=%s finish_reason=%s usage=%s cache_attempted=%s cache_applied=%s",
                provider_id,
                active_model_id,
                turn_id,
                session_id,
                completion.finish_reason,
                completion.usage,
                completion.cache_attempted,
                completion.cache_applied,
            )
        return CreateAssistantTurnResponse(
            turn=turn,
            assistant_message=assistant_message,
            confirmation=None,
            tool_calls=[],
        )

    @staticmethod
    def _augment_external_agent_system_prompt(
        *,
        base_prompt: str,
        tool_schema: list[dict[str, Any]],
    ) -> str:
        tool_names = [
            str(item.get("name", "")).strip()
            for item in tool_schema
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        lines = [
            base_prompt.strip(),
            "",
            "External MCP agent guidance:",
            "Use MCP tools/call for actions. Do not use resources/read to invoke tools.",
            "Tool names are exact; dot-separated names are single identifiers (for example `capabilities.describe`).",
        ]
        if tool_names:
            lines.append("Available MCP tool names:")
            lines.append(", ".join(tool_names))
        return "\n".join(lines).strip()

    @staticmethod
    def _looks_like_unknown_mcp_server_error(text: str) -> bool:
        raw = str(text or "").strip()
        lower = raw.lower()
        if "unknown mcp server" in lower:
            return True
        if "no mcp server named" in lower:
            return True
        if "failed to get client: mcp startup failed" in lower:
            return True
        if "handshaking with mcp server failed" in lower:
            return True
        if "transport channel closed" in lower and "mcp" in lower:
            return True
        if "is not recognized as a name of a cmdlet" in lower:
            return True
        if "not recognized as an internal or external command" in lower:
            return True
        # Newer codex-cli can emit PowerShell parser frames without the final
        # "not recognized" sentence (for example "Line |", underline tildes,
        # and a dotted identifier like capabilities.describe).
        if "line |" in lower and ("~" in raw or ".describe" in lower or ".list" in lower):
            return True
        return False

    def _maybe_resolve_pending_confirmation_from_prompt(
        self,
        *,
        session_id: str,
        prompt: str,
    ) -> CreateAssistantTurnResponse | None:
        decision = self._auto_confirmation_decision_for_prompt(prompt)
        if decision is None:
            return None
        list_pending = getattr(self._store, "list_session_pending_confirmations", None)
        if not callable(list_pending):
            return None
        pending = list_pending(session_id)
        if not pending:
            return None
        confirmation = pending[0]
        logger.info(
            "assistant auto confirmation resolution\n"
            "  session_id=%s\n"
            "  confirmation_id=%s\n"
            "  tool_name=%s\n"
            "  decision=%s\n"
            "  prompt=%s",
            session_id,
            confirmation.confirmation_id,
            confirmation.tool_name,
            decision.value,
            self._json_for_log(prompt),
        )
        response = self.resolve_confirmation(
            session_id,
            confirmation.confirmation_id,
            AssistantConfirmationDecisionRequest(decision=decision),
        )
        return CreateAssistantTurnResponse(
            turn=response.turn,
            assistant_message=response.assistant_message,
            confirmation=response.confirmation,
            tool_calls=response.tool_calls,
        )

    @staticmethod
    def _auto_confirmation_decision_for_prompt(prompt: str) -> AssistantConfirmationDecision | None:
        normalized = str(prompt or "").strip().lower()
        if normalized in {"approve", "approved", "yes", "ok", "okay", "continue", "proceed"}:
            return AssistantConfirmationDecision.ALLOW_ONCE
        if normalized in {"deny", "denied", "no", "reject", "cancel"}:
            return AssistantConfirmationDecision.DENY_ONCE
        return None

    @staticmethod
    def _build_external_mcp_retry_prompt(*, original_prompt: str, tool_names: list[str]) -> str:
        joined = ", ".join([name for name in tool_names if name][:40])
        return (
            "Retry the previous request using MCP tools/call only.\n"
            "Do not use resources/read.\n"
            "Dot-separated names are full tool names.\n"
            f"Known tool names include: {joined}\n\n"
            f"Original request:\n{original_prompt}"
        )

    def _run_explicit_tool_sequence_fallback(
        self,
        *,
        session_id: str,
        turn_id: str,
        prompt: str,
        resolved_scenario_id: str | None,
    ) -> str | None:
        parsed_calls = self._parse_explicit_tool_call_lines(prompt)
        if not parsed_calls:
            return None
        outputs: list[dict[str, Any]] = []
        for tool_name, raw_args in parsed_calls:
            if action_type_for_tool(tool_name) is not None:
                return None
            normalized_args = self._normalize_tool_arguments(
                session_id=session_id,
                tool_name=tool_name,
                tool_args=dict(raw_args),
                resolved_scenario_id=resolved_scenario_id,
            )
            result = execute_tool(
                self._tool_services,
                tool_name=tool_name,
                arguments=normalized_args,
            )
            outputs.append(
                {
                    "tool_name": tool_name,
                    "arguments": normalized_args,
                    "output": result,
                }
            )
        payload = {"tool_outputs": outputs}
        try:
            return json.dumps(payload, ensure_ascii=True)
        except Exception:
            return str(payload)

    @staticmethod
    def _parse_explicit_tool_call_lines(prompt: str) -> list[tuple[str, dict[str, Any]]]:
        text = str(prompt or "")
        pattern = re.compile(
            r"^\s*\d+\)\s*Call\s+`([^`]+)`\s+with\s+(\{.*\})\s*\.?\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        parsed: list[tuple[str, dict[str, Any]]] = []
        for match in pattern.finditer(text):
            tool_name = str(match.group(1) or "").strip()
            raw_args = str(match.group(2) or "").strip()
            if not tool_name or not raw_args:
                continue
            try:
                loaded = json.loads(raw_args)
            except Exception:
                continue
            if not isinstance(loaded, dict):
                continue
            parsed.append((tool_name, loaded))
        return parsed

    @staticmethod
    def _is_model_not_found_error(exc: Exception) -> bool:
        text = str(exc).strip().lower()
        if not text:
            return False
        markers = (
            "modelnotfounderror",
            "requested entity was not found",
            "requested model was not found",
            "model not found",
            "code: 404",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _capture_eval_rag_context_enabled() -> bool:
        raw = str(os.getenv("ASSISTANT_EVAL_CAPTURE_RAG_CONTEXT", "") or "").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _run_model_tool_loop(
        self,
        *,
        session_id: str,
        turn_id: str,
        prompt: str,
        scenario_id: str | None,
        provider_id: str,
        model_id: str,
        policy: Any,
        telemetry: TurnTelemetry,
        is_command_turn: bool,
        resume_tool_calls: list[AssistantToolCall] | None = None,
        resume_tool_failures: list[dict[str, Any]] | None = None,
        thinking: bool | str | None = None,
    ) -> CreateAssistantTurnResponse:
        history = self._store.list_messages(session_id, limit=120)
        compacted_summary = self._latest_compacted_summary(history)
        conversation = build_conversation(history)
        conversation = self._inject_domain_context_into_conversation(
            conversation=conversation,
            prompt=prompt,
            turn_id=turn_id,
        )
        system_prompt = build_system_prompt(
            scenario_id=scenario_id,
            scenario_directory=self._resolve_active_scenario_directory(scenario_id),
            capabilities_text=capabilities_text(),
            compacted_summary=compacted_summary,
            persistent_constraints=self._runtime(session_id).constraints_text,
        )
        expose_tools = self._should_expose_tools_for_prompt(
            prompt=prompt,
            is_command_turn=is_command_turn,
        )
        selected_tool_names = select_tool_names_for_prompt(prompt=prompt, max_tools=22) if expose_tools else set()
        selected_schema = list_tools_schema_filtered(selected_tool_names=selected_tool_names) if expose_tools else []
        cache_context = build_cache_context(
            provider_id=provider_id,
            model_id=model_id,
            system_prompt=system_prompt,
            tool_schema=selected_schema,
            scenario_id=scenario_id,
            compacted_summary=compacted_summary,
        )
        tool_schema = list_tools_for_model(selected_tool_names=selected_tool_names) if expose_tools else []
        allowed_tool_names = {
            str(tool.get("function", {}).get("name", "")).strip()
            for tool in tool_schema
            if isinstance(tool, dict)
        }
        perf = self._providers.performance()
        max_iterations = max(1, perf.max_tool_iterations_per_turn)
        max_tool_calls = max(1, perf.max_tool_calls_per_iteration)
        max_output_tokens = self._completion_token_budget(prompt=prompt, is_command_turn=is_command_turn)
        adaptive_max_output_tokens = max_output_tokens

        completion_usage_totals: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_prompt_tokens": 0,
        }
        cache_attempted = False
        cache_applied = False
        fallback_used = False
        tool_argument_retry_used = False
        executed_calls: list[AssistantToolCall] = list(resume_tool_calls or [])
        source_references: list[dict[str, Any]] = []
        capture_rag_context = self._capture_eval_rag_context_enabled()
        rag_context_captures: list[dict[str, Any]] = []
        num_ctx_captures: list[dict[str, Any]] = []
        final_text = ""
        active_provider_id = provider_id
        active_model_id = model_id
        attempted_provider_models: set[tuple[str, str]] = set()
        attempted_provider_models_ordered: list[tuple[str, str]] = []
        fallback_chain: list[dict[str, Any]] = []

        def _record_attempt(candidate_provider_id: str, candidate_model_id: str) -> None:
            key = (str(candidate_provider_id), str(candidate_model_id))
            if key in attempted_provider_models:
                return
            attempted_provider_models.add(key)
            attempted_provider_models_ordered.append(key)

        _record_attempt(active_provider_id, active_model_id)
        seen_tool_signatures: set[str] = {
            self._tool_call_signature(call.tool_name, call.arguments)
            for call in executed_calls
        }
        stop_due_to_repeated_tool_call = False
        mutation_intent = self._classify_mutation_intent(prompt)
        mutation_satisfied = any(
            call.tool_name in {"layer.update_state", "scenario.import_geotiff", "scenario.move_path"}
            for call in executed_calls
            if call.status == "completed"
        )

        for completed_call in executed_calls:
            if completed_call.status != "completed":
                continue
            conversation.append(
                {
                        "role": "assistant",
                        "content": (
                            f"Tool `{completed_call.tool_name}` result:\n"
                            f"{json.dumps(compact_tool_result_for_model_context(completed_call.tool_name, completed_call.result), ensure_ascii=True, default=str)}"
                        ),
                    }
                )

        for failed_call in list(resume_tool_failures or []):
            tool_name = str(failed_call.get("tool_name", "")).strip()
            if not tool_name:
                continue
            conversation.append(
                {
                    "role": "assistant",
                    "content": (
                        f"Tool `{tool_name}` failed:\n"
                        f"{json.dumps(failed_call, ensure_ascii=True, default=str)}"
                    ),
                }
            )

        for iteration in range(max_iterations):
            logger.info(
                "assistant model iteration start session_id=%s turn_id=%s iteration=%s/%s provider=%s model=%s max_output_tokens=%s conversation_messages=%s",
                session_id,
                turn_id,
                iteration + 1,
                max_iterations,
                active_provider_id,
                active_model_id,
                adaptive_max_output_tokens,
                len(conversation),
            )
            call_started_at = time.perf_counter()
            try:
                completion = self._providers.complete(
                    provider_id=active_provider_id,
                    model_id=active_model_id,
                    system_prompt=system_prompt,
                    conversation=conversation,
                    cache_context=cache_context,
                    tool_schema=tool_schema,
                    max_output_tokens=adaptive_max_output_tokens,
                    thinking=thinking,
                )
            except Exception as exc:
                logger.warning(
                    "Assistant provider call failed for %s/%s before fallback: %s",
                    active_provider_id,
                    active_model_id,
                    exc,
                )
                recovered = False
                for candidate_provider_id, candidate_model_id in self._provider_fallback_pairs(
                    provider_id=active_provider_id,
                    model_id=active_model_id,
                ):
                    candidate_key = (candidate_provider_id, candidate_model_id)
                    if candidate_key in attempted_provider_models:
                        continue
                    _record_attempt(candidate_provider_id, candidate_model_id)
                    try:
                        prior_provider_id = active_provider_id
                        prior_model_id = active_model_id
                        completion = self._providers.complete(
                            provider_id=candidate_provider_id,
                            model_id=candidate_model_id,
                            system_prompt=system_prompt,
                            conversation=conversation,
                            cache_context=cache_context,
                            tool_schema=tool_schema,
                            max_output_tokens=adaptive_max_output_tokens,
                            thinking=thinking,
                        )
                        active_provider_id = candidate_provider_id
                        active_model_id = candidate_model_id
                        fallback_used = True
                        recovered = True
                        fallback_chain.append(
                            {
                                "from_provider_id": prior_provider_id,
                                "from_model_id": prior_model_id,
                                "to_provider_id": candidate_provider_id,
                                "to_model_id": candidate_model_id,
                                "reason": "provider_exception",
                                "error": str(exc),
                            }
                        )
                        logger.warning(
                            "Assistant model fallback applied after provider failure: %s/%s -> %s/%s",
                            provider_id,
                            model_id,
                            candidate_provider_id,
                            candidate_model_id,
                        )
                        break
                    except Exception as fallback_exc:
                        logger.warning(
                            "Assistant provider call failed for %s/%s and fallback %s/%s also failed: %s",
                            active_provider_id,
                            active_model_id,
                            candidate_provider_id,
                            candidate_model_id,
                            fallback_exc,
                        )
                if not recovered:
                    raise exc
            call_elapsed_ms = int((time.perf_counter() - call_started_at) * 1000)
            completion_usage_totals = self._sum_usage(completion_usage_totals, completion.usage)
            cache_attempted = cache_attempted or completion.cache_attempted
            cache_applied = cache_applied or completion.cache_applied
            completion_metadata = dict(getattr(completion, "metadata", {}) or {})
            num_ctx_raw = completion_metadata.get("num_ctx")
            try:
                num_ctx_value = int(num_ctx_raw) if num_ctx_raw is not None else 0
            except Exception:
                num_ctx_value = 0
            if num_ctx_value > 0:
                num_ctx_captures.append(
                    {
                        "iteration": iteration + 1,
                        "provider_id": active_provider_id,
                        "model_id": active_model_id,
                        "num_ctx": num_ctx_value,
                    }
                )
            if capture_rag_context:
                rag_context_text = str(completion_metadata.get("rag_context_text", "") or "")
                if rag_context_text.strip():
                    rag_context_captures.append(
                        {
                            "iteration": iteration + 1,
                            "provider_id": active_provider_id,
                            "model_id": active_model_id,
                            "context_chars": len(rag_context_text),
                            "context_text": rag_context_text,
                        }
                    )
            for item in completion.references:
                if not isinstance(item, dict):
                    continue
                rel = str(item.get("relative_path", "")).strip()
                chunk_id = str(item.get("chunk_id", "")).strip()
                if not rel or not chunk_id:
                    continue
                source_references.append(dict(item))
            if completion.text.strip():
                final_text = completion.text.strip()
            provider_tool_calls = list(completion.tool_calls)
            if not provider_tool_calls:
                fallback_tool_call = _extract_text_tool_call(
                    completion.text,
                    allowed_tool_names=allowed_tool_names,
                )
                if fallback_tool_call is not None:
                    provider_tool_calls = [fallback_tool_call]
                    final_text = ""
            parsed_call_payload = [
                {
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                }
                for call in provider_tool_calls
            ]
            logger.info(
                "assistant model iteration completion session_id=%s turn_id=%s iteration=%s/%s provider=%s model=%s finish_reason=%s usage=%s text=%s parsed_tool_calls=%s",
                session_id,
                turn_id,
                iteration + 1,
                max_iterations,
                active_provider_id,
                active_model_id,
                completion.finish_reason,
                self._json_for_log(completion.usage),
                completion.text,
                self._json_for_log(parsed_call_payload),
            )
            if (
                not fallback_used
                and not provider_tool_calls
                and not completion.text.strip()
                and call_elapsed_ms >= perf.first_token_timeout_ms
                and perf.slow_turn_fallback_provider
                and perf.slow_turn_fallback_model
            ):
                if (
                    perf.slow_turn_fallback_provider == active_provider_id
                    and (
                    perf.slow_turn_fallback_provider != active_provider_id
                    or perf.slow_turn_fallback_model != active_model_id
                    )
                ):
                    prior_provider_id = active_provider_id
                    prior_model_id = active_model_id
                    active_provider_id = perf.slow_turn_fallback_provider
                    active_model_id = perf.slow_turn_fallback_model
                    _record_attempt(active_provider_id, active_model_id)
                    fallback_chain.append(
                        {
                            "from_provider_id": prior_provider_id,
                            "from_model_id": prior_model_id,
                            "to_provider_id": active_provider_id,
                            "to_model_id": active_model_id,
                            "reason": "slow_empty_completion",
                            "call_elapsed_ms": int(call_elapsed_ms),
                            "finish_reason": str(completion.finish_reason),
                        }
                    )
                    fallback_used = True
                    continue

            if not provider_tool_calls and not completion.text.strip():
                logger.warning(
                    "assistant provider returned empty completion provider=%s model=%s finish_reason=%s usage=%s",
                    active_provider_id,
                    active_model_id,
                    completion.finish_reason,
                    completion.usage,
                )
                if str(completion.finish_reason).strip().lower() == "length":
                    retry_cap = max(256, int(perf.empty_completion_retry_max_output_tokens))
                    next_budget = min(
                        retry_cap,
                        max(adaptive_max_output_tokens + 128, adaptive_max_output_tokens * 2),
                    )
                    if next_budget > adaptive_max_output_tokens:
                        logger.warning(
                            "assistant provider completion exhausted output budget without visible output; retrying with larger max_output_tokens provider=%s model=%s prev=%s next=%s",
                            active_provider_id,
                            active_model_id,
                            adaptive_max_output_tokens,
                            next_budget,
                        )
                        adaptive_max_output_tokens = next_budget
                        continue

            if not provider_tool_calls:
                logger.info(
                    "assistant model iteration stop-no-tool-calls session_id=%s turn_id=%s iteration=%s/%s provider=%s model=%s",
                    session_id,
                    turn_id,
                    iteration + 1,
                    max_iterations,
                    active_provider_id,
                    active_model_id,
                )
                break

            retry_after_invalid_arguments = False
            for provider_call in provider_tool_calls[:max_tool_calls]:
                normalized = self._normalize_tool_arguments(
                    session_id=session_id,
                    tool_name=provider_call.name,
                    tool_args={str(k): v for k, v in provider_call.arguments.items()},
                    resolved_scenario_id=scenario_id,
                )
                normalized, validation_error = self._validate_or_repair_tool_arguments(
                    tool_name=provider_call.name,
                    arguments=normalized,
                )
                if validation_error is not None:
                    if not tool_argument_retry_used:
                        tool_argument_retry_used = True
                        logger.warning(
                            "assistant tool arguments invalid; requesting single retry session_id=%s turn_id=%s tool=%s error=%s",
                            session_id,
                            turn_id,
                            provider_call.name,
                            validation_error,
                        )
                        schema_hint = tool_argument_schema_for_model(provider_call.name)
                        conversation.append(
                            {
                                "role": "assistant",
                                "content": (
                                    f"Tool call `{provider_call.name}` was invalid: {validation_error}. "
                                    "Retry with one corrected call only."
                                ),
                            }
                        )
                        conversation.append(
                            {
                                "role": "user",
                                "content": (
                                    "Return a corrected tool call now. "
                                    "Use only allowed fields, omit unsupported/default fields, "
                                    f"and satisfy this schema: {json.dumps(schema_hint, ensure_ascii=True)}"
                                ),
                            }
                        )
                        retry_after_invalid_arguments = True
                        break
                    usage = self._build_usage(
                        provider_id=active_provider_id,
                        model_id=active_model_id,
                        turn_handling_mode="model_tool_loop",
                        cache_attempted=cache_attempted,
                        cache_applied=cache_applied,
                        completion_usage=completion_usage_totals,
                        tool_call_count=len(executed_calls),
                        telemetry=telemetry,
                        fallback_used=fallback_used,
                    )
                    clarification = (
                        f"Need clarification before running `{provider_call.name}`: {validation_error}. "
                        "Please restate with correctly shaped tool arguments."
                    )
                    assistant_message, turn = self._finalize_turn_with_message(
                        session_id=session_id,
                        turn_id=turn_id,
                        text=clarification,
                        usage=usage,
                        metadata={
                            "provider_id": active_provider_id,
                            "model_id": active_model_id,
                            "usage": usage,
                            "clarification_required": True,
                            "clarification_code": "tool_arguments_invalid",
                            "tool_name": provider_call.name,
                        },
                        outputs=self._collect_tool_outputs(executed_calls),
                        telemetry=telemetry,
                    )
                    return CreateAssistantTurnResponse(
                        turn=turn,
                        assistant_message=assistant_message,
                        confirmation=None,
                        tool_calls=executed_calls,
                    )

                tool_signature = self._tool_call_signature(provider_call.name, normalized)
                if tool_signature in seen_tool_signatures:
                    logger.warning(
                        "assistant model loop repeated identical tool call session_id=%s turn_id=%s iteration=%s/%s tool=%s arguments=%s",
                        session_id,
                        turn_id,
                        iteration + 1,
                        max_iterations,
                        provider_call.name,
                        self._json_for_log(normalized),
                    )
                    matching_call = next(
                        (
                            call
                            for call in reversed(executed_calls)
                            if self._tool_call_signature(call.tool_name, call.arguments) == tool_signature
                        ),
                        None,
                    )
                    if matching_call is not None:
                        final_text = summarize_tool_result(matching_call.tool_name, matching_call.result)
                    elif not final_text:
                        final_text = f"Tool `{provider_call.name}` already returned the requested result."
                    stop_due_to_repeated_tool_call = True
                    break

                action_type = action_type_for_tool(provider_call.name)
                tool_call = self._store.create_tool_call(
                    session_id=session_id,
                    turn_id=turn_id,
                    tool_name=provider_call.name,
                    arguments=normalized,
                    status="proposed",
                    action_type=action_type,
                )
                self._emit_event(
                    AssistantEventName.TOOL_CALL_PROPOSED,
                    session_id=session_id,
                    turn_id=turn_id,
                    data={
                        "tool_call_id": tool_call.tool_call_id,
                        "tool_name": provider_call.name,
                        "arguments": normalized,
                        "provider_call_id": provider_call.call_id,
                    },
                )
                telemetry.mark_first_event()
                needs_confirmation, confirmation_action_type = self._needs_confirmation_for_tool(
                    session_id=session_id,
                    tool_name=provider_call.name,
                    tool_args=normalized,
                    fallback_action_type=action_type,
                    policy=policy,
                )
                if needs_confirmation and confirmation_action_type is not None:
                    confirmation = self._store.create_confirmation(
                        session_id=session_id,
                        turn_id=turn_id,
                        action_type=confirmation_action_type,
                        tool_name=provider_call.name,
                        arguments=normalized,
                    )
                    self._store.complete_tool_call(
                        tool_call.tool_call_id,
                        status="confirmation_required",
                        result={},
                    )
                    usage = self._build_usage(
                        provider_id=active_provider_id,
                        model_id=active_model_id,
                        turn_handling_mode="model_tool_loop",
                        cache_attempted=cache_attempted,
                        cache_applied=cache_applied,
                        completion_usage=completion_usage_totals,
                        tool_call_count=len(executed_calls) + 1,
                        telemetry=telemetry,
                        fallback_used=fallback_used,
                    )
                    turn = self._store.update_turn(
                        turn_id,
                        status=AssistantTurnStatus.CONFIRMATION_REQUIRED,
                        usage=usage,
                    )
                    self._emit_event(
                        AssistantEventName.CONFIRMATION_REQUIRED,
                        session_id=session_id,
                        turn_id=turn_id,
                        data=confirmation.model_dump(mode="json"),
                    )
                    assistant_message = self._record_confirmation_required_message(
                        session_id=session_id,
                        turn_id=turn_id,
                        tool_name=provider_call.name,
                        action_type=confirmation_action_type,
                        confirmation=confirmation,
                        arguments=normalized,
                    )
                    return CreateAssistantTurnResponse(
                        turn=turn,
                        assistant_message=assistant_message,
                        confirmation=confirmation,
                        tool_calls=self._store.list_turn_tool_calls(turn_id),
                    )

                try:
                    completed_call, result = self._execute_tool_call(
                        session_id=session_id,
                        turn_id=turn_id,
                        tool_call=tool_call,
                        telemetry=telemetry,
                    )
                except Exception as exc:
                    failed_call = self._store.complete_tool_call(
                        tool_call.tool_call_id,
                        status="failed",
                        result={},
                        error=str(exc),
                    )
                    usage = self._build_usage(
                        provider_id=active_provider_id,
                        model_id=active_model_id,
                        turn_handling_mode="model_tool_loop",
                        cache_attempted=cache_attempted,
                        cache_applied=cache_applied,
                        completion_usage=completion_usage_totals,
                        tool_call_count=len(executed_calls) + 1,
                        telemetry=telemetry,
                        fallback_used=fallback_used,
                    )
                    assistant_error_text = self._format_tool_execution_error_message(
                        tool_name=provider_call.name,
                        arguments=normalized,
                        error=exc,
                    )
                    self._emit_event(
                        AssistantEventName.ERROR,
                        session_id=session_id,
                        turn_id=turn_id,
                        data={
                            "error": str(exc),
                            "tool_call_id": failed_call.tool_call_id,
                            "assistant_message": assistant_error_text,
                            "error_code": ERROR_TOOL_EXECUTION_FAILED,
                        },
                    )
                    if isinstance(exc, ApiError):
                        error_payload: dict[str, Any] = {
                            "tool_name": provider_call.name,
                            "error_type": "api_error",
                            "code": exc.code,
                            "message": exc.message,
                            "details": dict(exc.details or {}),
                        }
                        conversation.append(
                            {
                                "role": "assistant",
                                "content": (
                                    f"Tool `{provider_call.name}` failed:\n"
                                    f"{json.dumps(error_payload, ensure_ascii=True, default=str)}"
                                ),
                            }
                        )
                        if exc.code in {
                            "map_algebra_output_exists",
                            "map_algebra_overwrite_confirmation_required",
                            "raster_transform_output_exists",
                        }:
                            continue
                        if not final_text:
                            final_text = assistant_error_text
                        continue
                    turn = self._store.update_turn(
                        turn_id,
                        status=AssistantTurnStatus.FAILED,
                        error=str(exc),
                        usage=usage,
                    )
                    assistant_message = self._store.add_message(
                        session_id=session_id,
                        role=AssistantRole.ASSISTANT,
                        content=assistant_error_text,
                        turn_id=turn_id,
                        metadata={
                            "provider_id": active_provider_id,
                            "model_id": active_model_id,
                            "usage": usage,
                            "execution_origin": "model_reasoned",
                            "tool_error": True,
                            "tool_name": provider_call.name,
                        },
                        outputs=self._collect_tool_outputs(executed_calls),
                    )
                    return CreateAssistantTurnResponse(
                        turn=turn,
                        assistant_message=assistant_message,
                        confirmation=None,
                        tool_calls=self._store.list_turn_tool_calls(turn_id),
                    )

                executed_calls.append(completed_call)
                seen_tool_signatures.add(tool_signature)
                if completed_call.tool_name == "tools.describe":
                    described_name = str(result.get("tool_name", "")).strip()
                    if described_name:
                        selected_tool_names.add(described_name)
                        selected_schema = list_tools_schema_filtered(selected_tool_names=selected_tool_names)
                        tool_schema = list_tools_for_model(selected_tool_names=selected_tool_names)
                        allowed_tool_names = {
                            str(tool.get("function", {}).get("name", "")).strip()
                            for tool in tool_schema
                            if isinstance(tool, dict)
                        }
                        cache_context = build_cache_context(
                            provider_id=provider_id,
                            model_id=model_id,
                            system_prompt=system_prompt,
                            tool_schema=selected_schema,
                            scenario_id=scenario_id,
                            compacted_summary=compacted_summary,
                        )
                if completed_call.tool_name in {"layer.update_state", "scenario.import_geotiff", "scenario.move_path"}:
                    mutation_satisfied = True
                conversation.append(
                    {
                        "role": "assistant",
                        "content": (
                            f"Tool `{completed_call.tool_name}` result:\n"
                            f"{json.dumps(compact_tool_result_for_model_context(completed_call.tool_name, result), ensure_ascii=True, default=str)}"
                        ),
                    }
                )

            if retry_after_invalid_arguments:
                continue
            if stop_due_to_repeated_tool_call:
                break

        if not final_text:
            if executed_calls:
                lines = [summarize_tool_result(call.tool_name, call.result) for call in executed_calls[-3:]]
                final_text = "\n".join(lines)
            else:
                final_text = "No response returned by provider."

        if mutation_intent and not mutation_satisfied:
            final_text = (
                "I could not complete the requested state-changing action. "
                "Please specify the exact target (for example `turn on slope.tif`)."
            )

        turn_ctx = self._turn_hybrid_context.get(turn_id)
        if turn_ctx is not None and turn_ctx.turn_state is not None:
            self._turn_state_manager.mark_model_segments_complete(turn_ctx.turn_state)
            self._turn_state_manager.build_handoff(
                turn_ctx.turn_state,
                active_scenario_id=scenario_id,
                active_scenario_directory=None,
            )

        usage = self._build_usage(
            provider_id=active_provider_id,
            model_id=active_model_id,
            turn_handling_mode="model_tool_loop",
            cache_attempted=cache_attempted,
            cache_applied=cache_applied,
            completion_usage=completion_usage_totals,
            tool_call_count=len(executed_calls),
            telemetry=telemetry,
            fallback_used=fallback_used,
        )
        deduped_source_references: list[dict[str, Any]] = []
        seen_refs: set[tuple[str, str]] = set()
        for item in source_references:
            rel = str(item.get("relative_path", "")).strip()
            chunk_id = str(item.get("chunk_id", "")).strip()
            key = (rel, chunk_id)
            if not rel or not chunk_id or key in seen_refs:
                continue
            seen_refs.add(key)
            deduped_source_references.append(dict(item))
        assistant_metadata: dict[str, Any] = {
            "provider_id": active_provider_id,
            "model_id": active_model_id,
            "usage": usage,
            "execution_origin": "model_reasoned",
        }
        if mutation_intent and not mutation_satisfied:
            assistant_metadata["mutation_unsatisfied"] = True
            assistant_metadata["mutation_intent"] = mutation_intent
        if deduped_source_references:
            assistant_metadata["source_references"] = deduped_source_references
        assistant_metadata["requested_provider_id"] = provider_id
        assistant_metadata["requested_model_id"] = model_id
        assistant_metadata["final_provider_id"] = active_provider_id
        assistant_metadata["final_model_id"] = active_model_id
        assistant_metadata["fallback_used"] = bool(fallback_used)
        assistant_metadata["attempted_models"] = [
            {"provider_id": attempted_provider_id, "model_id": attempted_model_id}
            for attempted_provider_id, attempted_model_id in attempted_provider_models_ordered
        ]
        assistant_metadata["fallback_chain"] = list(fallback_chain)
        if num_ctx_captures:
            final_num_ctx = int(num_ctx_captures[-1].get("num_ctx", 0) or 0)
            assistant_metadata["num_ctx"] = final_num_ctx
            assistant_metadata["num_ctx_capture_count"] = len(num_ctx_captures)
            assistant_metadata["num_ctx_captures"] = list(num_ctx_captures)
        if capture_rag_context and rag_context_captures:
            final_capture = rag_context_captures[-1]
            assistant_metadata["rag_context_text"] = str(final_capture.get("context_text", "") or "")
            assistant_metadata["rag_context_chars"] = int(final_capture.get("context_chars", 0) or 0)
            assistant_metadata["rag_context_capture_count"] = len(rag_context_captures)
            assistant_metadata["rag_context_captures"] = rag_context_captures
        assistant_message, turn = self._finalize_turn_with_message(
            session_id=session_id,
            turn_id=turn_id,
            text=final_text,
            usage=usage,
            metadata=assistant_metadata,
            outputs=self._collect_tool_outputs(executed_calls),
            telemetry=telemetry,
        )
        return CreateAssistantTurnResponse(
            turn=turn,
            assistant_message=assistant_message,
            confirmation=None,
            tool_calls=executed_calls,
        )

    @staticmethod
    def _format_tool_execution_error_message(
        *,
        tool_name: str,
        arguments: dict[str, Any],
        error: Exception,
    ) -> str:
        if isinstance(error, ApiError):
            if error.code in {
                "map_algebra_output_exists",
                "map_algebra_overwrite_confirmation_required",
                "raster_transform_output_exists",
            }:
                output_relative_path = str(arguments.get("output_relative_path", "")).strip()
                path_hint = f"`{output_relative_path}`" if output_relative_path else "the requested output path"
                if error.code == "map_algebra_overwrite_confirmation_required":
                    return (
                        f"I couldn't complete `{tool_name}` because {path_hint} already exists and overwrite "
                        "needs confirmation. Ask the user before retrying with `overwrite_mode=\"always\"`, "
                        "or choose a different output filename."
                    )
                return (
                    f"I couldn't complete `{tool_name}` because {path_hint} already exists. "
                    "Re-run with `overwrite_mode=\"always\"` or choose a different output filename."
                )
            return f"I couldn't complete `{tool_name}`: {error.code} - {error.message}"
        return f"I couldn't complete `{tool_name}`: {error}"

    def _validate_or_repair_tool_arguments(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        normalized = dict(arguments)
        error = self._tool_argument_validation_error(tool_name=tool_name, arguments=normalized)
        if error is None:
            return normalized, None
        repaired = self._auto_repair_tool_arguments(tool_name=tool_name, arguments=normalized)
        error_after = self._tool_argument_validation_error(tool_name=tool_name, arguments=repaired)
        return repaired, error_after

    def _tool_argument_validation_error(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str | None:
        schema = tool_argument_schema_for_tool(tool_name)
        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(arguments), key=lambda err: list(err.absolute_path))
        if errors:
            first = errors[0]
            path_bits = [str(part) for part in first.absolute_path]
            arg_path = ".".join(path_bits) if path_bits else "$"
            return f"Invalid argument at {arg_path}: {first.message}"
        required = schema.get("required", [])
        if isinstance(required, list):
            missing = [name for name in required if str(name) and name not in arguments]
            if missing:
                return f"Missing required argument(s): {', '.join(str(name) for name in missing)}"

        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return None
        for key, value in arguments.items():
            spec = properties.get(key)
            if not isinstance(spec, dict):
                continue
            expected = str(spec.get("type", "")).strip()
            if expected == "object" and not isinstance(value, dict):
                return f"Argument '{key}' must be an object"
            if expected == "string" and not isinstance(value, str):
                return f"Argument '{key}' must be a string"
            if expected == "boolean" and not isinstance(value, bool):
                return f"Argument '{key}' must be a boolean"
            if expected == "array" and not isinstance(value, list):
                return f"Argument '{key}' must be an array"
            enum_values = spec.get("enum")
            if isinstance(enum_values, list) and enum_values and value not in enum_values:
                return f"Argument '{key}' must be one of: {', '.join(str(item) for item in enum_values)}"
        return None

    def _auto_repair_tool_arguments(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        repaired = dict(arguments)
        if tool_name in {"raster.calculate", "raster.transform"}:
            raw_inputs = repaired.get("inputs")
            if isinstance(raw_inputs, dict):
                fixed_inputs: dict[str, Any] = {}
                for key, value in raw_inputs.items():
                    if isinstance(value, dict):
                        fixed_inputs[str(key)] = value
                        continue
                    if isinstance(value, str) and value.strip():
                        fixed_inputs[str(key)] = {"relative_path": value.strip()}
                        continue
                    fixed_inputs[str(key)] = value
                repaired["inputs"] = fixed_inputs
            return repaired

        schema = tool_argument_schema_for_tool(tool_name)
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return repaired
        for key, spec in properties.items():
            if key not in repaired:
                continue
            if not isinstance(spec, dict):
                continue
            expected = str(spec.get("type", "")).strip()
            value = repaired.get(key)
            if expected == "object" and isinstance(value, str) and value.strip():
                repaired[key] = {"relative_path": value.strip()}
        return repaired

    def _provider_fallback_pairs(
        self,
        *,
        provider_id: str,
        model_id: str,
    ) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        perf = self._providers.performance()
        fallback_provider = str(perf.slow_turn_fallback_provider or "").strip()
        fallback_model = str(perf.slow_turn_fallback_model or "").strip()
        if (
            fallback_provider
            and fallback_model
            and (fallback_provider, fallback_model) != (provider_id, model_id)
            and fallback_provider == provider_id
        ):
            pairs.append((fallback_provider, fallback_model))
        for candidate_model in self._catalog_models_for_provider(provider_id):
            pair = (provider_id, candidate_model)
            if pair == (provider_id, model_id):
                continue
            if pair not in pairs:
                pairs.append(pair)
        return pairs

    def _catalog_models_for_provider(self, provider_id: str) -> list[str]:
        try:
            catalog = self._providers.catalog()
        except Exception:
            return []
        raw_providers: Any
        if isinstance(catalog, dict):
            raw_providers = catalog.get("providers", [])
        else:
            raw_providers = getattr(catalog, "providers", [])
        if not isinstance(raw_providers, list):
            return []
        models: list[str] = []
        for provider in raw_providers:
            if isinstance(provider, dict):
                pid = str(provider.get("provider_id", "")).strip()
                raw_models = provider.get("models", [])
            else:
                pid = str(getattr(provider, "provider_id", "")).strip()
                raw_models = getattr(provider, "models", [])
            if pid != provider_id:
                continue
            if isinstance(raw_models, list):
                for raw in raw_models:
                    model = str(raw).strip()
                    if model and model not in models:
                        models.append(model)
            break
        return models

    @staticmethod
    def _build_partial_followup_prompt(plan: CommandPlan) -> str:
        if not plan.unmatched_segments:
            return ""
        return " Then ".join(segment.strip() for segment in plan.unmatched_segments if segment.strip())

    @staticmethod
    def _action_plan_has_agent_substeps(plan: CommandPlan) -> bool:
        for action in plan.actions:
            for step in action.steps:
                if isinstance(step, PlannedAgentStep):
                    return True
        return False

    @staticmethod
    def _tool_supports_parser_followup(tool_name: str | None) -> bool:
        return str(tool_name or "").strip() in {
            "capabilities.describe",
            "artifact.describe_geotiff",
            "artifact.preview_geotiff",
            "artifact.stats_geotiff",
            "artifact.describe_table",
            "artifact.describe_plot",
        }

    def _provider_selection_needed_for_turn(
        self,
        *,
        action_plan: CommandPlan,
        tool_name: str | None,
        explicit_tool_sequence_supported: bool,
    ) -> bool:
        if explicit_tool_sequence_supported:
            return True
        if action_plan.actions:
            if not action_plan.is_fully_matched:
                return True
            if self._action_plan_has_agent_substeps(action_plan):
                return True
            return False
        if self._tool_supports_parser_followup(tool_name):
            return True
        return tool_name is None

    def _provider_selection_is_optional_for_turn(
        self,
        *,
        action_plan: CommandPlan,
        tool_name: str | None,
        explicit_tool_sequence_supported: bool,
    ) -> bool:
        if explicit_tool_sequence_supported:
            return True
        if action_plan.actions:
            return action_plan.is_fully_matched and not self._action_plan_has_agent_substeps(action_plan)
        return self._tool_supports_parser_followup(tool_name)

    @staticmethod
    def _coerce_requested_thinking_metadata(thinking: Any) -> bool | str | None:
        if isinstance(thinking, bool):
            return thinking
        if isinstance(thinking, str):
            normalized = thinking.strip().lower()
            if normalized in {"low", "medium", "high"}:
                return normalized
        return None

    @staticmethod
    def _classify_mutation_intent(prompt: str) -> str | None:
        text = str(prompt or "").strip().lower()
        if not text:
            return None
        if re.match(r"^\s*(show|hide|turn on|turn off|enable|disable)\b", text):
            if any(token in text for token in ("table", "csv", "plot", "chart", "image", "file", "artifact")):
                return None
            return "layer_visibility"
        if re.match(r"^\s*(import|add layer|move)\b", text):
            return "mutation"
        return None

    def _completion_token_budget(self, *, prompt: str, is_command_turn: bool) -> int:
        perf = self._providers.performance()
        if is_command_turn:
            return max(64, perf.command_max_output_tokens)
        if len(prompt) > 800:
            return max(256, perf.analysis_max_output_tokens)
        return max(128, perf.analysis_max_output_tokens)

    @staticmethod
    def _sum_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
        merged = dict(left)
        for key in ("prompt_tokens", "completion_tokens", "cached_prompt_tokens"):
            merged[key] = int(merged.get(key, 0) or 0) + int(right.get(key, 0) or 0)
        return merged

    @staticmethod
    def _json_for_log(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=True, default=str)
        except Exception:
            return str(value)

    @staticmethod
    def _json_for_log_pretty(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=True, indent=2, default=str)
        except Exception:
            return str(value)

    @staticmethod
    def _is_command_prompt(prompt: str) -> bool:
        lower = prompt.strip().lower()
        command_starts = (
            "set ",
            "switch ",
            "change ",
            "use ",
            "turn ",
            "highlight ",
            "list ",
            "run ",
            "launch ",
            "cancel ",
            "get ",
            "import ",
            "move ",
            "describe ",
            "write ",
            "create ",
            "apply ",
        )
        if lower.startswith(command_starts):
            return True
        show_hide_match = re.match(r"^\s*(show|hide)\s+(?:the\s+)?(.+?)\s*(?:layer)?\s*[.!?]?\s*$", lower)
        if show_hide_match:
            target = str(show_hide_match.group(2) or "").strip().lower()
            if target and not any(token in target for token in ("table", "csv", "plot", "chart", "image", "file", "artifact")):
                return True
        return False

    @staticmethod
    def _should_expose_tools_for_prompt(*, prompt: str, is_command_turn: bool) -> bool:
        if is_command_turn:
            return True
        lower = str(prompt or "").strip().lower()
        if not lower:
            return True
        explicit_lookup_patterns = (
            r"\bwhat\s+(?:files|products|layers|jobs|scripts|notebooks)\b",
            r"\bwhich\s+(?:files|products|layers|jobs|scripts|notebooks)\b",
            r"\b(?:do|does)\s+.*\b(?:exist|have|contain|include)\b",
            r"\b(?:inspect|preview|summarize|describe|stats for|show stats|list|show|get|run|create|write|import|generate|calculate|plot|move|switch|set|cancel|launch)\b",
        )
        if any(re.search(pattern, lower) for pattern in explicit_lookup_patterns):
            return True
        explanatory_patterns = (
            r"^\s*what should\b",
            r"^\s*how should\b",
            r"^\s*why should\b",
            r"^\s*what are\b",
            r"^\s*explain\b",
            r"^\s*propose\b",
            r"^\s*given\b",
            r"^\s*if\b",
            r"\bbefore making any\b",
            r"\brecommendation\b",
            r"\bnarrative\b",
            r"\bworkflow\b",
            r"\bconstraints?\b",
            r"\bhazards?\b",
            r"\bevidence package\b",
        )
        if any(re.search(pattern, lower) for pattern in explanatory_patterns):
            return False
        return True

    @staticmethod
    def _build_usage(
        *,
        provider_id: str | None,
        model_id: str | None,
        turn_handling_mode: str,
        cache_attempted: bool,
        cache_applied: bool,
        completion_usage: dict[str, int],
        tool_call_count: int,
        telemetry: TurnTelemetry,
        fallback_used: bool = False,
    ) -> dict[str, Any]:
        return {
            "provider_id": provider_id,
            "model_id": model_id,
            "cache_attempted": bool(cache_attempted),
            "cache_applied": bool(cache_applied),
            "prompt_tokens": int(completion_usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(completion_usage.get("completion_tokens", 0) or 0),
            "cached_prompt_tokens": int(completion_usage.get("cached_prompt_tokens", 0) or 0),
            "turn_handling_mode": turn_handling_mode,
            "latency_ms_first_event": telemetry.first_event_ms if telemetry.first_event_ms is not None else telemetry.total_ms(),
            "latency_ms_total": telemetry.total_ms(),
            "tool_call_count": int(tool_call_count),
            "fallback_used": bool(fallback_used),
        }

    def _plan_tool_call(self, *, prompt: str, scenario_id: str | None) -> tuple[str | None, dict[str, Any]]:
        text = prompt.strip()
        lower = text.lower()
        non_empty_lines = [line.strip() for line in text.splitlines() if line.strip()]
        first_line = non_empty_lines[0] if non_empty_lines else text
        first_line_lower = first_line.lower()
        has_followup_content = len(non_empty_lines) > 1
        scenario_command_prefixes = (
            "set scenario ",
            "switch scenario ",
            "switch to scenario ",
            "switch to ",
            "change to scenario ",
            "change to ",
            "use scenario ",
        )
        if not has_followup_content:
            for prefix in scenario_command_prefixes:
                if not first_line_lower.startswith(prefix):
                    continue
                scenario_ref = _normalize_scenario_reference(first_line[len(prefix) :])
                if scenario_ref:
                    return "scenario.set_current", {"scenario_ref": scenario_ref}
                break
        if "what can lunar analyst" in lower or "describe capabilities" in lower or "capabilities" == lower:
            return "capabilities.describe", {}
        if "list predefined jobs" in lower or "list jobs" in lower:
            return "jobs.list_predefined", {}
        if lower.startswith("run predefined job "):
            implementation_name = text[len("run predefined job ") :].strip().split(" ", 1)[0]
            params = _extract_json_object(text) or {}
            return "jobs.run_predefined", {"implementation_name": implementation_name.replace("-", "_"), "params": params}
        if "list scenarios" in lower:
            return "scenario.list", {}
        if "list scripts" in lower:
            if scenario_id:
                return "scenario.list_scripts", {"scenario_id": scenario_id}
        if "list notebooks" in lower or "list marimo notebooks" in lower:
            if scenario_id:
                return "scenario.list_notebooks", {"scenario_id": scenario_id}
        if lower.startswith("run script "):
            rel = _match_quoted(text) or text[len("run script ") :].strip()
            if rel:
                args: dict[str, Any] = {"relative_path": rel}
                if scenario_id:
                    args["scenario_id"] = scenario_id
                return "scenario.run_script", args
        if lower.startswith("run notebook ") or lower.startswith("run marimo notebook "):
            prefix = "run marimo notebook " if lower.startswith("run marimo notebook ") else "run notebook "
            rel = _match_quoted(text) or text[len(prefix) :].strip()
            if rel:
                args = {"relative_path": rel}
                if scenario_id:
                    args["scenario_id"] = scenario_id
                return "scenario.run_marimo_notebook", args
        if lower.startswith("get logs "):
            job_id = text[len("get logs ") :].strip().split(" ", 1)[0]
            head_match = re.search(r"head\\s+([0-9]+)", lower)
            tail_match = re.search(r"tail\\s+([0-9]+)", lower)
            stream = "combined" if "combined" in lower else ("stderr" if "stderr" in lower else "stdout")
            return "runs.get_logs", {
                "job_id": job_id,
                "head_lines": int(head_match.group(1)) if head_match else 40,
                "tail_lines": int(tail_match.group(1)) if tail_match else 80,
                "stream": stream,
            }
        if lower.startswith("run status ") or lower.startswith("job status "):
            prefix = "job status " if lower.startswith("job status ") else "run status "
            job_id = text[len(prefix) :].strip().split(" ", 1)[0]
            return "runs.get_status", {"job_id": job_id}
        if lower.startswith("cancel run ") or lower.startswith("cancel job "):
            prefix = "cancel job " if lower.startswith("cancel job ") else "cancel run "
            job_id = text[len(prefix) :].strip().split(" ", 1)[0]
            return "runs.cancel", {"job_id": job_id}
        if lower.startswith("revoke overwrite approval "):
            rel = _match_quoted(text) or text[len("revoke overwrite approval ") :].strip()
            args: dict[str, Any] = {"relative_path": rel}
            if scenario_id:
                args["scenario_id"] = scenario_id
            return "scenario.revoke_script_overwrite", args
        script_intent = _parse_script_write_intent(text=text, scenario_id=scenario_id)
        if script_intent is not None:
            return script_intent
        if "list products" in lower:
            resolved_scenario = scenario_id or _match_identifier(lower, r"scenario(?:_id)?\s+([a-z0-9_\-]+)")
            if resolved_scenario:
                return "product.list", {"scenario_id": resolved_scenario}
        if "files for product" in lower:
            product_id = _match_identifier(lower, r"files for product\s+([a-z0-9_\-]+)")
            if product_id:
                return "product.files", {"product_id": product_id}
        if any(token in lower for token in ("highlight pixels", "highlight areas", "highlight where")) and "slope" in lower:
            threshold = _extract_slope_threshold(text)
            if threshold is not None and scenario_id:
                threshold_label = str(threshold).replace(".", "p")
                output_relative_path = f"slope_le_{threshold_label}deg_mask.tif"
                return "raster.calculate", {
                    "scenario_id": scenario_id,
                    "expression": f"slope <= {threshold}",
                    "inputs": {
                        "slope": {"relative_path": "slope.tif"},
                    },
                    "output_relative_path": output_relative_path,
                    "overwrite_mode": "always",
                }
        if lower.startswith("launch job "):
            implementation_name = text[len("launch job ") :].strip().split(" ", 1)[0]
            params = _extract_json_object(text) or {}
            return "job.launch", {"implementation_name": implementation_name.replace("-", "_"), "params": params}
        if "import geotiff" in lower:
            scn = scenario_id or _match_identifier(lower, r"scenario(?:_id)?\s+([a-z0-9_\-]+)")
            path = _match_quoted(text)
            if scn and path:
                return "scenario.import_geotiff", {"scenario_id": scn, "source_path": path}
        if lower.startswith("move path "):
            scn = scenario_id or _match_identifier(lower, r"scenario(?:_id)?\s+([a-z0-9_\-]+)")
            quoted = re.findall(r'"([^"]+)"', text)
            if scn and len(quoted) >= 2:
                return "scenario.move_path", {
                    "scenario_id": scn,
                    "source_relative_path": quoted[0],
                    "target_relative_path": quoted[1],
                }
        if lower.startswith("set layer "):
            parts = text.split()
            if len(parts) >= 3:
                layer_id = parts[2]
                opacity_match = re.search(r"opacity\s+([0-9]+(?:\.[0-9]+)?)", lower)
                visible = None
                if "visible true" in lower:
                    visible = True
                elif "visible false" in lower:
                    visible = False
                args: dict[str, Any] = {"layer_id": layer_id}
                if opacity_match:
                    args["opacity"] = float(opacity_match.group(1))
                if visible is not None:
                    args["visible"] = visible
                return "layer.update_state", args
        layer_toggle_match = re.match(
            r"^\s*turn\s+(on|off)\s+(?:the\s+)?(.+?)(?:\s+layer)?\s*[.!?]?\s*$",
            lower,
        )
        if layer_toggle_match:
            if re.search(r"\b(if|when|unless|only if|because|while|except)\b", lower):
                return None, {}
            action = layer_toggle_match.group(1)
            layer_name = layer_toggle_match.group(2).strip()
            if layer_name:
                args = {
                    "layer_name": layer_name,
                    "visible": action == "on",
                }
                if scenario_id:
                    args["scenario_id"] = scenario_id
                return "layer.update_state", args
        show_hide_layer_match = re.match(
            r"^\s*(show|hide)\s+(?:the\s+)?(.+?)\s*(?:layer)?\s*[.!?]?\s*$",
            lower,
        )
        if show_hide_layer_match:
            if re.search(r"\b(if|when|unless|only if|because|while|except)\b", lower):
                return None, {}
            action = show_hide_layer_match.group(1)
            layer_name = show_hide_layer_match.group(2).strip()
            if layer_name and not any(
                token in layer_name
                for token in ("table", "csv", "plot", "chart", "image", "file", "artifact")
            ):
                args = {
                    "layer_name": layer_name,
                    "visible": action == "show",
                }
                if scenario_id:
                    args["scenario_id"] = scenario_id
                return "layer.update_state", args
        if (
            "what layers are currently visible" in lower
            or "which layers are currently visible" in lower
            or "list visible layers" in lower
        ):
            if scenario_id:
                return "layer.list_visible", {"scenario_id": scenario_id}
        if "describe geotiff" in lower:
            file_id = _match_identifier(lower, r"file(?:_id)?\s+([a-z0-9_\-]+)")
            path = _match_quoted(text) or _match_file_token(text, extensions=("tif", "tiff"))
            if file_id:
                return "artifact.describe_geotiff", {"file_id": file_id}
            if path:
                if scenario_id and _is_relative_file_reference(path):
                    return "artifact.describe_geotiff", {"scenario_id": scenario_id, "relative_path": path}
                return "artifact.describe_geotiff", {"path": path}
        if "preview geotiff" in lower or "preview tif" in lower or "preview tiff" in lower:
            file_id = _match_identifier(lower, r"file(?:_id)?\s+([a-z0-9_\-]+)")
            path = _match_quoted(text) or _match_file_token(text, extensions=("tif", "tiff"))
            if file_id:
                return "artifact.preview_geotiff", {"file_id": file_id}
            if path:
                if scenario_id and _is_relative_file_reference(path):
                    return "artifact.preview_geotiff", {"scenario_id": scenario_id, "relative_path": path}
                return "artifact.preview_geotiff", {"path": path}
        if "geotiff stats" in lower or "geotiff statistics" in lower or "stats for geotiff" in lower or "statistics for geotiff" in lower:
            file_id = _match_identifier(lower, r"file(?:_id)?\s+([a-z0-9_\-]+)")
            path = _match_quoted(text) or _match_file_token(text, extensions=("tif", "tiff"))
            if file_id:
                return "artifact.stats_geotiff", {"file_id": file_id}
            if path:
                if scenario_id and _is_relative_file_reference(path):
                    return "artifact.stats_geotiff", {"scenario_id": scenario_id, "relative_path": path}
                return "artifact.stats_geotiff", {"path": path}
        if "describe table" in lower:
            file_id = _match_identifier(lower, r"file(?:_id)?\s+([a-z0-9_\-]+)")
            path = _match_quoted(text) or _match_file_token(text, extensions=("csv", "tsv", "txt"))
            if file_id:
                return "artifact.describe_table", {"file_id": file_id}
            if path:
                if scenario_id and _is_relative_file_reference(path):
                    return "artifact.describe_table", {"scenario_id": scenario_id, "relative_path": path}
                return "artifact.describe_table", {"path": path}
        if "describe plot" in lower or "describe image" in lower:
            file_id = _match_identifier(lower, r"file(?:_id)?\s+([a-z0-9_\-]+)")
            path = _match_quoted(text) or _match_file_token(text, extensions=("png", "jpg", "jpeg", "webp", "gif", "bmp"))
            if file_id:
                return "artifact.describe_plot", {"file_id": file_id}
            if path:
                if scenario_id and _is_relative_file_reference(path):
                    return "artifact.describe_plot", {"scenario_id": scenario_id, "relative_path": path}
                return "artifact.describe_plot", {"path": path}
        if lower.startswith("describe "):
            candidate = _match_quoted(text) or _match_file_token(
                text,
                extensions=("tif", "tiff", "csv", "tsv", "txt", "png", "jpg", "jpeg", "webp", "gif", "bmp"),
            )
            if candidate:
                suffix = Path(candidate).suffix.lower()
                if suffix in {".tif", ".tiff"}:
                    if scenario_id and _is_relative_file_reference(candidate):
                        return "artifact.describe_geotiff", {"scenario_id": scenario_id, "relative_path": candidate}
                    return "artifact.describe_geotiff", {"path": candidate}
                if suffix in {".csv", ".tsv", ".txt"}:
                    if scenario_id and _is_relative_file_reference(candidate):
                        return "artifact.describe_table", {"scenario_id": scenario_id, "relative_path": candidate}
                    return "artifact.describe_table", {"path": candidate}
                if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
                    if scenario_id and _is_relative_file_reference(candidate):
                        return "artifact.describe_plot", {"scenario_id": scenario_id, "relative_path": candidate}
                    return "artifact.describe_plot", {"path": candidate}
        return None, {}

    @staticmethod
    def _latest_compacted_summary(messages: list[AssistantMessage]) -> str | None:
        for msg in reversed(messages):
            if msg.role == AssistantRole.SYSTEM and str(msg.metadata.get("kind", "")) == "compaction_summary":
                return msg.content
        return None

    def _known_product_references(self, scenario_id: str | None) -> list[str]:
        if not scenario_id:
            return []
        product_service = getattr(self._tool_services, "product_service", None)
        list_products = getattr(product_service, "list_products", None)
        if not callable(list_products):
            return []
        try:
            products = list_products(scenario_id)
        except Exception:
            return []
        refs: list[str] = []
        for item in products or []:
            for attr in ("product_id", "name", "label", "title", "subkind", "kind"):
                value = str(getattr(item, attr, "") or "").strip()
                if value:
                    refs.append(value)
        seen: set[str] = set()
        ordered: list[str] = []
        for ref in refs:
            lowered = ref.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            ordered.append(ref)
        return ordered[:100]

    @staticmethod
    def _extract_outputs(result: dict[str, Any]) -> list[dict[str, Any]]:
        raw_outputs = result.get("artifacts")
        if not isinstance(raw_outputs, list):
            return []
        outputs: list[dict[str, Any]] = []
        for item in raw_outputs:
            if not isinstance(item, dict):
                continue
            try:
                validated = AssistantOutput.model_validate(item)
            except Exception:
                logger.warning("assistant tool result produced invalid output payload: %s", item)
                continue
            outputs.append(validated.model_dump(mode="json"))
        return outputs

    @staticmethod
    def _collect_tool_outputs(tool_calls: list[AssistantToolCall]) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        for call in tool_calls:
            outputs.extend(item.model_dump(mode="json") for item in call.outputs)
        return outputs

    @staticmethod
    def _tool_call_signature(tool_name: str, arguments: dict[str, Any]) -> str:
        try:
            encoded_args = json.dumps(arguments, ensure_ascii=True, sort_keys=True, default=str)
        except Exception:
            encoded_args = str(arguments)
        return f"{tool_name}:{encoded_args}"

    def _emit_event(
        self,
        event: AssistantEventName,
        *,
        session_id: str,
        turn_id: str | None,
        data: dict[str, Any],
    ) -> None:
        envelope = AssistantWsEnvelope(
            event=event,
            session_id=session_id,
            turn_id=turn_id,
            timestamp_utc=_utc_now(),
            data=data,
        )
        self._assistant_ws_events.append(envelope.model_dump(mode="json"))


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _match_identifier(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    return str(m.group(1)).strip()


def _match_quoted(text: str) -> str | None:
    m = re.search(r'"([^"]+)"', text)
    if not m:
        return None
    return str(m.group(1)).strip()


def _match_file_token(text: str, *, extensions: tuple[str, ...]) -> str | None:
    if not extensions:
        return None
    joined = "|".join(re.escape(ext) for ext in extensions)
    pattern = rf"([^\s\"']+\.(?:{joined}))\b"
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    return str(m.group(1)).strip()


def _is_relative_file_reference(path_or_rel: str) -> bool:
    text = path_or_rel.strip()
    if not text:
        return False
    p = Path(text)
    if p.is_absolute():
        return False
    if re.match(r"^[a-zA-Z]:[\\/]", text):
        return False
    return True


def _extract_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(text[index:])
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _normalize_scenario_reference(raw: str) -> str:
    return normalize_scenario_reference(raw)


def _extract_fenced_code(text: str) -> str | None:
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    raw = str(m.group(1))
    return raw.strip("\n")


def _extract_text_tool_call(
    text: str,
    *,
    allowed_tool_names: set[str],
) -> ProviderToolCall | None:
    raw = text.strip()
    if not raw:
        return None
    candidates = [raw]
    fenced = _extract_fenced_block(raw)
    if fenced:
        candidates.insert(0, fenced)
    for candidate in candidates:
        payload = _extract_json_object(candidate)
        if not payload:
            continue
        parsed = _payload_to_provider_tool_call(payload, allowed_tool_names=allowed_tool_names)
        if parsed is not None:
            return parsed
    return None


def _payload_to_provider_tool_call(
    payload: dict[str, Any],
    *,
    allowed_tool_names: set[str],
) -> ProviderToolCall | None:
    direct_name = str(payload.get("name", payload.get("tool_name", payload.get("tool", "")))).strip()
    direct_args = payload.get("arguments", payload.get("args", {}))
    if direct_name:
        arguments = _parse_tool_arguments_object(direct_args)
        if direct_name in allowed_tool_names:
            return ProviderToolCall(call_id="text_tool_call_1", name=direct_name, arguments=arguments)

    raw_calls = payload.get("tool_calls")
    if not isinstance(raw_calls, list):
        return None
    for idx, call in enumerate(raw_calls):
        if not isinstance(call, dict):
            continue
        function = call.get("function", call)
        if not isinstance(function, dict):
            continue
        name = str(function.get("name", function.get("tool_name", function.get("tool", "")))).strip()
        if not name or name not in allowed_tool_names:
            continue
        arguments = _parse_tool_arguments_object(function.get("arguments", function.get("args", {})))
        call_id = str(call.get("id", "")).strip() or f"text_tool_call_{idx + 1}"
        return ProviderToolCall(call_id=call_id, name=name, arguments=arguments)
    return None


def _parse_tool_arguments_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return {str(k): v for k, v in parsed.items()}
    return {}


def _extract_fenced_block(text: str) -> str | None:
    m = re.search(r"```(?:[a-z0-9_+-]+)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    return str(m.group(1)).strip()


def _parse_script_write_intent(
    *,
    text: str,
    scenario_id: str | None,
) -> tuple[str, dict[str, Any]] | None:
    lower = text.lower()
    write_run_patterns = (
        "write and run script ",
        "create and run script ",
        "write and run a script ",
        "create and run a script ",
    )
    write_only_patterns = (
        "write script ",
        "create script ",
        "write a script ",
        "create a script ",
    )
    content = _extract_fenced_code(text) or ""
    quoted_path = _match_quoted(text)
    for prefix in write_run_patterns:
        if not lower.startswith(prefix):
            continue
        tail = text[len(prefix) :].strip()
        path_candidate = quoted_path or tail.splitlines()[0].strip()
        rel = path_candidate if _looks_like_script_path(path_candidate) else ""
        if not rel and not content:
            synthesized = _synthesize_script_from_prompt(text)
            if synthesized is None:
                return None
            rel, content = synthesized
        if rel and not content:
            synthesized = _synthesize_script_from_prompt(text)
            if synthesized is not None:
                rel, content = synthesized
        if not rel or not content:
            return None
        args: dict[str, Any] = {"relative_path": rel, "content": content}
        if scenario_id:
            args["scenario_id"] = scenario_id
        return "scenario.write_run_script", args

    for prefix in write_only_patterns:
        if not lower.startswith(prefix):
            continue
        tail = text[len(prefix) :].strip()
        path_candidate = quoted_path or tail.splitlines()[0].strip()
        rel = path_candidate if _looks_like_script_path(path_candidate) else ""
        if not rel or not content:
            return None
        args = {"relative_path": rel, "content": content}
        if scenario_id:
            args["scenario_id"] = scenario_id
        return "scenario.write_script", args
    return None


def _looks_like_script_path(value: str) -> bool:
    candidate = str(value or "").strip().strip('"').strip("'")
    if not candidate:
        return False
    return candidate.lower().endswith(".py")


def _synthesize_script_from_prompt(text: str) -> tuple[str, str] | None:
    lower = text.lower()
    if "dem" not in lower or "slope" not in lower:
        return None
    threshold = _extract_slope_threshold(text)
    if threshold is None:
        return None
    output_name = _extract_output_tif_name(text) or "landing_sites.tif"
    script_name = f"generate_{Path(output_name).stem}_mask.py"
    script = f'''import numpy as np
import rasterio

dem_path = "dem.tif"
output_path = "{output_name}"
threshold_deg = {threshold}

with rasterio.open(dem_path) as src:
    dem = src.read(1).astype(np.float64)
    transform = src.transform
    x_res = abs(float(transform.a)) if transform is not None else 1.0
    y_res = abs(float(transform.e)) if transform is not None else 1.0
    if x_res <= 0:
        x_res = 1.0
    if y_res <= 0:
        y_res = 1.0

    dz_dy, dz_dx = np.gradient(dem, y_res, x_res)
    slope_deg = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
    mask = (slope_deg <= threshold_deg).astype(np.uint8)

    profile = src.profile.copy()
    profile.update(dtype=rasterio.uint8, count=1, nodata=0)
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(mask, 1)

print(f"wrote {{output_path}}")
'''
    return script_name, script


def _extract_slope_threshold(text: str) -> float | None:
    direct = re.search(r"slope[^\n\r]*?(?:<=|≤)\s*([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    if direct:
        try:
            return float(direct.group(1))
        except Exception:
            return None
    phrase = re.search(
        r"slope[^\n\r]*?(?:less than or equal to|at most|no more than|[0-9]+(?:\.[0-9]+)?\s*degrees?\s*or\s*less)\s*([0-9]+(?:\.[0-9]+)?)",
        text,
        re.IGNORECASE,
    )
    if phrase:
        try:
            return float(phrase.group(1))
        except Exception:
            pass
    reverse_phrase = re.search(
        r"slope[^\n\r]*?\b([0-9]+(?:\.[0-9]+)?)\s*degrees?\s*or\s*less\b",
        text,
        re.IGNORECASE,
    )
    if reverse_phrase:
        try:
            return float(reverse_phrase.group(1))
        except Exception:
            return None
    return None


def _extract_output_tif_name(text: str) -> str | None:
    m = re.search(r"(?:named|called)\s+([A-Za-z0-9_.-]+\.(?:tif|tiff))\b", text, re.IGNORECASE)
    if not m:
        m = re.search(r"\b([A-Za-z0-9_.-]+\.(?:tif|tiff))\b", text, re.IGNORECASE)
    if not m:
        return None
    return str(m.group(1)).strip()
