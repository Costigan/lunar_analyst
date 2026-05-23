from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

from backend.api.errors import ApiError
from backend.contracts.assistant_models import (
    AssistantConfirmationActionType,
    AssistantConfirmationDecisionRequest,
    CompactAssistantSessionRequest,
    CreateAssistantSessionRequest,
    CreateAssistantTurnRequest,
)
from backend.services.assistant.assistant_service import AssistantService, ToolExecutionServices
from backend.services.assistant.command_router import HybridCommandRouter
from backend.services.assistant.entity_reference_resolver import SegmentEntityResolution
from backend.services.assistant.prompt_classifier import PromptClassifier, SegmentClassification, SegmentOffsets
from backend.services.assistant.prompt_segmenter import PromptSegmenter
from backend.services.assistant.policy_service import AssistantPolicyService
from backend.services.assistant.provider_registry import AssistantPerformanceConfig, ProviderSelection
from backend.services.assistant.providers.base import ProviderCompletion, ProviderToolCall
from backend.services.assistant.session_store import AssistantSessionStore
from backend.services.assistant.tool_argument_repair import ToolArgumentRepairer
from backend.services.assistant.turn_execution_plan import TurnExecutionPlanBuilder
from backend.services.assistant.verb_normalizer import VerbNormalizationResult


class _QueueProviderRegistry:
    def __init__(self, completions: list[ProviderCompletion]) -> None:
        self._completions = list(completions)

    def select_for_prompt(self, *, provider_id, model_id, is_command_turn):  # noqa: ANN001
        return ProviderSelection(provider_id="dummy", model_id="dummy-model")

    def complete(
        self,
        *,
        provider_id: str,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        cache_context=None,  # noqa: ANN001
        tool_schema=None,  # noqa: ANN001
        max_output_tokens=None,  # noqa: ANN001
        thinking=None,  # noqa: ANN001
    ) -> ProviderCompletion:
        del system_prompt, cache_context, tool_schema, max_output_tokens, thinking
        assert provider_id == "dummy"
        assert model_id == "dummy-model"
        assert isinstance(conversation, list)
        if not self._completions:
            return ProviderCompletion(text="done")
        return self._completions.pop(0)

    def performance(self) -> AssistantPerformanceConfig:
        return AssistantPerformanceConfig(max_tool_iterations_per_turn=4, max_tool_calls_per_iteration=3)

    def catalog(self):  # noqa: ANN201
        return {"providers": []}


class _CapturingQueueProviderRegistry(_QueueProviderRegistry):
    def __init__(self, completions: list[ProviderCompletion]) -> None:
        super().__init__(completions)
        self.seen_conversations: list[list[dict[str, str]]] = []
        self.seen_tool_schemas: list[object] = []

    def complete(
        self,
        *,
        provider_id: str,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        cache_context=None,  # noqa: ANN001
        tool_schema=None,  # noqa: ANN001
        max_output_tokens=None,  # noqa: ANN001
        thinking=None,  # noqa: ANN001
    ) -> ProviderCompletion:
        del system_prompt, cache_context, max_output_tokens, thinking
        self.seen_conversations.append(list(conversation))
        self.seen_tool_schemas.append(tool_schema)
        return super().complete(
            provider_id=provider_id,
            model_id=model_id,
            system_prompt="",
            conversation=conversation,
        )


class _ThinkingCapturingQueueProviderRegistry(_QueueProviderRegistry):
    def __init__(self, completions: list[ProviderCompletion], thinking_mode: str = "level") -> None:
        super().__init__(completions)
        self.last_thinking = None
        self._thinking_mode = thinking_mode

    def normalize_thinking_setting(self, *, provider_id, model_id, thinking):  # noqa: ANN001
        del provider_id, model_id
        if self._thinking_mode == "level" and thinking in {"low", "medium", "high"}:
            return thinking
        if self._thinking_mode == "boolean" and isinstance(thinking, bool):
            return thinking
        return None

    def complete(
        self,
        *,
        provider_id: str,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        cache_context=None,  # noqa: ANN001
        tool_schema=None,  # noqa: ANN001
        max_output_tokens=None,  # noqa: ANN001
        thinking=None,  # noqa: ANN001
    ) -> ProviderCompletion:
        self.last_thinking = thinking
        return super().complete(
            provider_id=provider_id,
            model_id=model_id,
            system_prompt=system_prompt,
            conversation=conversation,
            cache_context=cache_context,
            tool_schema=tool_schema,
            max_output_tokens=max_output_tokens,
            thinking=thinking,
        )


class _NoProviderRegistry:
    def select_for_prompt(self, *, provider_id, model_id, is_command_turn):  # noqa: ANN001
        raise RuntimeError("No assistant provider is configured.")

    def complete(self, **kwargs):  # noqa: ANN003, ANN201
        raise AssertionError("complete() should not be called for parser fast-path")

    def performance(self) -> AssistantPerformanceConfig:
        return AssistantPerformanceConfig()

    def catalog(self):  # noqa: ANN201
        return {"providers": []}


class _CompactionResetRegistry(_NoProviderRegistry):
    def __init__(self) -> None:
        self.reset_calls: list[str] = []

    def reset_session(self, session_id: str) -> None:
        self.reset_calls.append(session_id)


class _FailThenFallbackRegistry:
    def select_for_prompt(self, *, provider_id, model_id, is_command_turn):  # noqa: ANN001
        return ProviderSelection(provider_id="ollama", model_id="qwen3.5:35b-a3b")

    def complete(
        self,
        *,
        provider_id: str,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        cache_context=None,  # noqa: ANN001
        tool_schema=None,  # noqa: ANN001
        max_output_tokens=None,  # noqa: ANN001
        thinking=None,  # noqa: ANN001
    ) -> ProviderCompletion:
        del system_prompt, conversation, cache_context, tool_schema, max_output_tokens, thinking
        if provider_id == "ollama" and model_id == "qwen3.5:35b-a3b":
            raise RuntimeError("Ollama provider unavailable: HTTP Error 500: Internal Server Error")
        return ProviderCompletion(
            text="Fallback model response.",
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 6, "cached_prompt_tokens": 0},
        )

    def performance(self) -> AssistantPerformanceConfig:
        return AssistantPerformanceConfig(
            slow_turn_fallback_provider="ollama",
            slow_turn_fallback_model="qwen2.5-coder:7b-instruct-q4_K_M",
        )

    def catalog(self):  # noqa: ANN201
        return {
            "providers": [
                {
                    "provider_id": "ollama",
                    "models": [
                        "qwen3.5:35b-a3b",
                        "qwen3.5:27b",
                        "qwen2.5-coder:7b-instruct-q4_K_M",
                    ],
                }
            ]
        }


class _ExternalAgentRegistry:
    def __init__(self) -> None:
        self.last_access_mode: str | None = None
        self.last_scenario_working_directory: str | None = None

    def select_for_prompt(self, *, provider_id, model_id, is_command_turn):  # noqa: ANN001
        return ProviderSelection(
            provider_id="codex_cli",
            model_id="gpt-5-codex",
            execution_mode="external_mcp_agent",
        )

    def complete(
        self,
        *,
        provider_id: str,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        session_id=None,  # noqa: ANN001
        on_delta=None,  # noqa: ANN001
        cache_context=None,  # noqa: ANN001
        tool_schema=None,  # noqa: ANN001
        max_output_tokens=None,  # noqa: ANN001
        thinking=None,  # noqa: ANN001
        access_mode=None,  # noqa: ANN001
        scenario_working_directory=None,  # noqa: ANN001
    ) -> ProviderCompletion:
        del system_prompt, cache_context, tool_schema, max_output_tokens, thinking
        assert provider_id == "codex_cli"
        assert model_id == "gpt-5-codex"
        assert isinstance(conversation, list)
        assert isinstance(session_id, str)
        if callable(on_delta):
            on_delta("partial")
        self.last_access_mode = access_mode
        self.last_scenario_working_directory = scenario_working_directory
        return ProviderCompletion(
            text="External MCP agent response.",
            finish_reason="stop",
            usage={"prompt_tokens": 9, "completion_tokens": 4, "cached_prompt_tokens": 0},
        )

    def performance(self) -> AssistantPerformanceConfig:
        return AssistantPerformanceConfig()

    def catalog(self):  # noqa: ANN201
        return {
            "providers": [
                {
                    "provider_id": "codex_cli",
                    "models": ["gpt-5-codex"],
                }
            ]
        }


class _ExternalAgentUnknownServerRetryRegistry:
    def __init__(self) -> None:
        self.calls = 0
        self.last_system_prompt = ""

    def select_for_prompt(self, *, provider_id, model_id, is_command_turn):  # noqa: ANN001
        return ProviderSelection(
            provider_id="codex_cli",
            model_id="gpt-5-codex",
            execution_mode="external_mcp_agent",
        )

    def complete(
        self,
        *,
        provider_id: str,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        session_id=None,  # noqa: ANN001
        on_delta=None,  # noqa: ANN001
        cache_context=None,  # noqa: ANN001
        tool_schema=None,  # noqa: ANN001
        max_output_tokens=None,  # noqa: ANN001
        thinking=None,  # noqa: ANN001
        access_mode=None,  # noqa: ANN001
        scenario_working_directory=None,  # noqa: ANN001
    ) -> ProviderCompletion:
        del session_id, on_delta, cache_context, tool_schema, max_output_tokens, thinking, access_mode, scenario_working_directory
        assert provider_id == "codex_cli"
        assert model_id == "gpt-5-codex"
        assert isinstance(conversation, list)
        self.calls += 1
        self.last_system_prompt = system_prompt
        if self.calls == 1:
            return ProviderCompletion(
                text="resources/read failed: unknown MCP server 'capabilities'",
                finish_reason="stop",
                usage={"prompt_tokens": 3, "completion_tokens": 2, "cached_prompt_tokens": 0},
            )
        return ProviderCompletion(
            text='{"capabilities":{"text":"ok"},"scenarios":{"items":[],"count":0}}',
            finish_reason="stop",
            usage={"prompt_tokens": 4, "completion_tokens": 3, "cached_prompt_tokens": 0},
        )

    def performance(self) -> AssistantPerformanceConfig:
        return AssistantPerformanceConfig()

    def catalog(self):  # noqa: ANN201
        return {
            "providers": [
                {
                    "provider_id": "codex_cli",
                    "models": ["gpt-5-codex"],
                }
            ]
        }


class _ExternalAgentAlwaysUnknownServerRegistry:
    def __init__(self) -> None:
        self.calls = 0

    def select_for_prompt(self, *, provider_id, model_id, is_command_turn):  # noqa: ANN001
        return ProviderSelection(
            provider_id="codex_cli",
            model_id="gpt-5-codex",
            execution_mode="external_mcp_agent",
        )

    def complete(
        self,
        *,
        provider_id: str,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        session_id=None,  # noqa: ANN001
        on_delta=None,  # noqa: ANN001
        cache_context=None,  # noqa: ANN001
        tool_schema=None,  # noqa: ANN001
        max_output_tokens=None,  # noqa: ANN001
        thinking=None,  # noqa: ANN001
        access_mode=None,  # noqa: ANN001
        scenario_working_directory=None,  # noqa: ANN001
    ) -> ProviderCompletion:
        del system_prompt, conversation, session_id, on_delta, cache_context, tool_schema, max_output_tokens, thinking, access_mode, scenario_working_directory
        assert provider_id == "codex_cli"
        assert model_id == "gpt-5-codex"
        self.calls += 1
        return ProviderCompletion(
            text="resources/list failed: unknown MCP server 'capabilities.describe'",
            finish_reason="stop",
            usage={"prompt_tokens": 2, "completion_tokens": 2, "cached_prompt_tokens": 0},
        )

    def performance(self) -> AssistantPerformanceConfig:
        return AssistantPerformanceConfig()

    def catalog(self):  # noqa: ANN201
        return {
            "providers": [
                {
                    "provider_id": "codex_cli",
                    "models": ["gpt-5-codex"],
                }
            ]
        }


class _ExternalAgentAlwaysCmdletToolErrorRegistry:
    def __init__(self) -> None:
        self.calls = 0

    def select_for_prompt(self, *, provider_id, model_id, is_command_turn):  # noqa: ANN001
        return ProviderSelection(
            provider_id="codex_cli",
            model_id="gpt-5-codex",
            execution_mode="external_mcp_agent",
        )

    def complete(
        self,
        *,
        provider_id: str,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        session_id=None,  # noqa: ANN001
        on_delta=None,  # noqa: ANN001
        cache_context=None,  # noqa: ANN001
        tool_schema=None,  # noqa: ANN001
        max_output_tokens=None,  # noqa: ANN001
        thinking=None,  # noqa: ANN001
        access_mode=None,  # noqa: ANN001
        scenario_working_directory=None,  # noqa: ANN001
    ) -> ProviderCompletion:
        del system_prompt, conversation, session_id, on_delta, cache_context, tool_schema, max_output_tokens, thinking, access_mode, scenario_working_directory
        assert provider_id == "codex_cli"
        assert model_id == "gpt-5-codex"
        self.calls += 1
        return ProviderCompletion(
            text="The term 'capabilities.describe' is not recognized as a name of a cmdlet.",
            finish_reason="stop",
            usage={"prompt_tokens": 2, "completion_tokens": 2, "cached_prompt_tokens": 0},
        )

    def performance(self) -> AssistantPerformanceConfig:
        return AssistantPerformanceConfig()

    def catalog(self):  # noqa: ANN201
        return {
            "providers": [
                {
                    "provider_id": "codex_cli",
                    "models": ["gpt-5-codex"],
                }
            ]
        }


class _ExternalAgentAlwaysPwshFrameOnlyErrorRegistry:
    def __init__(self) -> None:
        self.calls = 0

    def select_for_prompt(self, *, provider_id, model_id, is_command_turn):  # noqa: ANN001
        return ProviderSelection(
            provider_id="codex_cli",
            model_id="gpt-5-codex",
            execution_mode="external_mcp_agent",
        )

    def complete(
        self,
        *,
        provider_id: str,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        session_id=None,  # noqa: ANN001
        on_delta=None,  # noqa: ANN001
        cache_context=None,  # noqa: ANN001
        tool_schema=None,  # noqa: ANN001
        max_output_tokens=None,  # noqa: ANN001
        thinking=None,  # noqa: ANN001
        access_mode=None,  # noqa: ANN001
        scenario_working_directory=None,  # noqa: ANN001
    ) -> ProviderCompletion:
        del system_prompt, conversation, session_id, on_delta, cache_context, tool_schema, max_output_tokens, thinking, access_mode, scenario_working_directory
        assert provider_id == "codex_cli"
        assert model_id == "gpt-5-codex"
        self.calls += 1
        return ProviderCompletion(
            text=(
                "capabilities.describe:\n"
                "Line |\n"
                "   2 | capabilities.describe {}\n"
                "     | ~~~~~~~~~~~~~~~~~~~~~\n"
            ),
            finish_reason="stop",
            usage={"prompt_tokens": 2, "completion_tokens": 2, "cached_prompt_tokens": 0},
        )

    def performance(self) -> AssistantPerformanceConfig:
        return AssistantPerformanceConfig()

    def catalog(self):  # noqa: ANN201
        return {
            "providers": [
                {
                    "provider_id": "codex_cli",
                    "models": ["gpt-5-codex"],
                }
            ]
        }


class _ExternalAgentAlwaysMcpHandshakeErrorRegistry:
    def __init__(self) -> None:
        self.calls = 0

    def select_for_prompt(self, *, provider_id, model_id, is_command_turn):  # noqa: ANN001
        return ProviderSelection(
            provider_id="codex_cli",
            model_id="gpt-5-codex",
            execution_mode="external_mcp_agent",
        )

    def complete(
        self,
        *,
        provider_id: str,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        session_id=None,  # noqa: ANN001
        on_delta=None,  # noqa: ANN001
        cache_context=None,  # noqa: ANN001
        tool_schema=None,  # noqa: ANN001
        max_output_tokens=None,  # noqa: ANN001
        thinking=None,  # noqa: ANN001
        access_mode=None,  # noqa: ANN001
        scenario_working_directory=None,  # noqa: ANN001
    ) -> ProviderCompletion:
        del system_prompt, conversation, session_id, on_delta, cache_context, tool_schema, max_output_tokens, thinking, access_mode, scenario_working_directory
        assert provider_id == "codex_cli"
        assert model_id == "gpt-5-codex"
        self.calls += 1
        return ProviderCompletion(
            text=(
                "resources/read failed: failed to get client: MCP startup failed: "
                "handshaking with MCP server failed: Send message error "
                "Transport channel closed"
            ),
            finish_reason="stop",
            usage={"prompt_tokens": 2, "completion_tokens": 2, "cached_prompt_tokens": 0},
        )

    def performance(self) -> AssistantPerformanceConfig:
        return AssistantPerformanceConfig()

    def catalog(self):  # noqa: ANN201
        return {
            "providers": [
                {
                    "provider_id": "codex_cli",
                    "models": ["gpt-5-codex"],
                }
            ]
        }


class _ExternalAgentModelFallbackRegistry:
    def select_for_prompt(self, *, provider_id, model_id, is_command_turn):  # noqa: ANN001
        return ProviderSelection(
            provider_id="gemini_cli",
            model_id="gemini-3.1-pro",
            execution_mode="external_mcp_agent",
        )

    def complete(
        self,
        *,
        provider_id: str,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        session_id=None,  # noqa: ANN001
        on_delta=None,  # noqa: ANN001
        cache_context=None,  # noqa: ANN001
        tool_schema=None,  # noqa: ANN001
        max_output_tokens=None,  # noqa: ANN001
        thinking=None,  # noqa: ANN001
        access_mode=None,  # noqa: ANN001
        scenario_working_directory=None,  # noqa: ANN001
    ) -> ProviderCompletion:
        del system_prompt, conversation, session_id, on_delta, cache_context, tool_schema, max_output_tokens, thinking, access_mode, scenario_working_directory
        assert provider_id == "gemini_cli"
        if model_id == "gemini-3.1-pro":
            raise RuntimeError("ModelNotFoundError: Requested entity was not found.")
        assert model_id == "gemini-2.5-pro"
        return ProviderCompletion(
            text="Gemini fallback response.",
            finish_reason="stop",
            usage={"prompt_tokens": 12, "completion_tokens": 5, "cached_prompt_tokens": 0},
        )

    def performance(self) -> AssistantPerformanceConfig:
        return AssistantPerformanceConfig()

    def catalog(self):  # noqa: ANN201
        return {
            "providers": [
                {
                    "provider_id": "gemini_cli",
                    "models": ["gemini-3.1-pro", "gemini-2.5-pro"],
                }
            ]
        }


class _Layer:
    def __init__(
        self,
        *,
        layer_id: str,
        scenario_id: str,
        title: str,
        visible: bool,
        opacity: float,
        z_index: int,
    ) -> None:
        self.layer_id = layer_id
        self.scenario_id = scenario_id
        self.title = title
        self.visible = visible
        self.opacity = opacity
        self.z_index = z_index

    def model_dump(self, mode: str = "json"):  # noqa: ANN201, ARG002
        return {
            "layer_id": self.layer_id,
            "scenario_id": self.scenario_id,
            "title": self.title,
            "visible": self.visible,
            "opacity": self.opacity,
            "z_index": self.z_index,
        }


def _build_service(  # noqa: ANN001
    tmp_path: Path,
    provider_registry,
    *,
    prompt_segmentation_enabled: bool = False,
    prompt_classification_contract_enabled: bool = False,
    turn_execution_plan_contract_enabled: bool = False,
    segment_state_merge_policy_enabled: bool = False,
    argument_repair_enabled: bool = False,
    success_semantics_policy_enabled: bool = False,
    observability_contract_enabled: bool = False,
) -> AssistantService:
    store = AssistantSessionStore(tmp_path / "assistant_sessions.json")
    policy = AssistantPolicyService(require_confirmation_for_mutations=True)
    scenarios_root = tmp_path / "scenarios"
    scenarios_root.mkdir(parents=True, exist_ok=True)

    def _get_scenario(scenario_id: str):  # noqa: ANN001, ANN202
        scenario_dir = scenarios_root / scenario_id
        scenario_dir.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(scenario_id=scenario_id, directory=str(scenario_dir.resolve()))

    tool_services = ToolExecutionServices(
        scenario_service=SimpleNamespace(
            get_scenario=_get_scenario,
            list_scenarios=lambda: [],
        ),
        product_service=SimpleNamespace(),
        layer_service=SimpleNamespace(
            list_layers=lambda scenario_id: [  # noqa: ARG005
                _Layer(
                    layer_id="layer_visible",
                    scenario_id=scenario_id,
                    title="Visible Layer",
                    visible=True,
                    opacity=1.0,
                    z_index=2,
                ),
                _Layer(
                    layer_id="layer_hidden",
                    scenario_id=scenario_id,
                    title="Hidden Layer",
                    visible=False,
                    opacity=0.5,
                    z_index=1,
                ),
            ]
        ),
        job_service=SimpleNamespace(),
        notebook_job_service=SimpleNamespace(),
        stores=SimpleNamespace(),
    )
    service = AssistantService(
        store=store,
        policy_service=policy,
        provider_registry=provider_registry,
        tool_services=tool_services,
        assistant_ws_events=[],
        prompt_segmentation_enabled=prompt_segmentation_enabled,
        prompt_classification_contract_enabled=prompt_classification_contract_enabled,
        turn_execution_plan_contract_enabled=turn_execution_plan_contract_enabled,
        segment_state_merge_policy_enabled=segment_state_merge_policy_enabled,
        argument_repair_enabled=argument_repair_enabled,
        success_semantics_policy_enabled=success_semantics_policy_enabled,
        observability_contract_enabled=observability_contract_enabled,
    )
    service._prompt_classifier = PromptClassifier()  # type: ignore[attr-defined]
    return service


def test_model_tool_loop_executes_tool_and_completes_turn(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[ProviderToolCall(call_id="tc1", name="capabilities.describe", arguments={})],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(text="Capabilities summarized.", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="tool-loop"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Please help me with this tool."),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "Capabilities summarized" in response.assistant_message.content
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_name == "capabilities.describe"
    assert response.turn.usage.get("turn_handling_mode") == "ordered_segment_execution"
    assert int(response.turn.usage.get("tool_call_count", 0)) == 1


def test_model_tool_loop_surfaces_output_exists_with_recovery_hint(tmp_path: Path, monkeypatch) -> None:
    provider_registry = _CapturingQueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[
                    ProviderToolCall(
                        call_id="tc_raster",
                        name="raster.calculate",
                        arguments={
                            "scenario_id": "scn_1",
                            "expression": "slope <= 5",
                            "inputs": {"slope": {"relative_path": "slope.tif"}},
                            "output_relative_path": "landing_sites3.tif",
                            "overwrite_mode": "ask",
                            "mode": "immediate",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(
                text="Retrying with overwrite confirmed.",
                tool_calls=[
                    ProviderToolCall(
                        call_id="tc_raster_retry",
                        name="raster.calculate",
                        arguments={
                            "scenario_id": "scn_1",
                            "expression": "slope <= 5",
                            "inputs": {"slope": {"relative_path": "slope.tif"}},
                            "output_relative_path": "landing_sites3.tif",
                            "overwrite_mode": "always",
                            "mode": "immediate",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(text="Completed after overwrite confirmation.", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    service._policy = AssistantPolicyService(require_confirmation_for_mutations=False)  # type: ignore[attr-defined]
    session = service.create_session(CreateAssistantSessionRequest(title="tool-loop-output-exists"))

    calls = {"count": 0}

    def _raise_output_exists(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        assert tool_name == "raster.calculate"
        calls["count"] += 1
        if calls["count"] == 1:
            raise ApiError(
                status_code=409,
                code="map_algebra_output_exists",
                message="Output file already exists: landing_sites3.tif",
                details={"output_path": "landing_sites3.tif"},
            )
        return {
            "tool_name": tool_name,
            "job_id": "job_retry",
            "run_id": "job_retry",
            "job": {
                "job_id": "job_retry",
                "scenario_id": "scn_1",
                "status": "completed",
            },
            "result": {"output_relative_path": "landing_sites3.tif"},
        }

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _raise_output_exists)

    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Create landing_sites3 from slope <= 5."),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert response.turn.usage.get("turn_handling_mode") in {"model_tool_loop", "ordered_segment_execution"}
    assert "Completed after overwrite confirmation" in response.assistant_message.content
    assert calls["count"] >= 2
    assert len(provider_registry.seen_conversations) >= 2
    final_json = json.dumps(provider_registry.seen_conversations[-1], ensure_ascii=True)
    assert "landing_sites3.tif" in final_json


def test_model_tool_loop_persists_source_references_metadata(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="Slope guidance available.",
                finish_reason="stop",
                references=[
                    {
                        "relative_path": "terrain/constraints.md",
                        "chunk_id": "terrain/constraints.md:0",
                        "score": 1.2,
                        "snippet": "Max slope threshold is 8 degrees.",
                    }
                ],
            )
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="source-refs"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="What is the slope limit?"),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    refs = response.assistant_message.metadata.get("source_references")
    assert isinstance(refs, list)
    assert refs and refs[0]["relative_path"] == "terrain/constraints.md"


def test_model_tool_loop_persists_eval_rag_context_when_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_EVAL_CAPTURE_RAG_CONTEXT", "1")
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="Answered with retrieved context.",
                finish_reason="stop",
                metadata={
                    "rag_context_text": "[src#1 path=terrain.md chunk=terrain.md:0]\nMax slope threshold is 8 degrees.",
                    "rag_context_chars": 78,
                },
            )
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="rag-context"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="What is the slope limit?"),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    meta = response.assistant_message.metadata
    assert str(meta.get("rag_context_text", "")).startswith("[src#1 path=terrain.md")
    assert int(meta.get("rag_context_chars", 0) or 0) > 0
    assert int(meta.get("rag_context_capture_count", 0) or 0) == 1


def test_parser_fast_path_attaches_tool_outputs_to_assistant_message(tmp_path: Path, monkeypatch) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="tool-outputs"))

    def _fake_execute_tool(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        assert tool_name == "artifact.describe_table"
        assert "path" in arguments
        return {
            "summary_text": "Table preview ready.",
            "artifacts": [
                {
                    "output_id": "out_table",
                    "kind": "table",
                    "mime_type": "application/vnd.lunar-analyst.table+json",
                    "storage": "inline",
                    "data": {
                        "columns": [{"key": "value", "label": "value", "dtype": "number"}],
                        "rows": [{"value": "1"}],
                        "row_count": 1,
                        "truncated": False,
                    },
                    "metadata": {},
                }
            ],
        }

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)
    sample_csv = tmp_path / "sample.csv"
    sample_csv.write_text("value\n1\n", encoding="utf-8")

    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt=f'describe table "{sample_csv}"'),
    )

    assert response.turn.status.value == "completed"
    assert response.tool_calls[0].outputs[0].output_id == "out_table"
    assert response.assistant_message is not None
    assert response.assistant_message.outputs[0].kind == "table"


def test_parser_fast_path_synthesizes_write_run_script_for_slope_mask_prompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    service._semantic_intent_families_enabled = False  # type: ignore[attr-defined]
    session = service.create_session(CreateAssistantSessionRequest(title="script-intent"))
    captured: dict[str, object] = {}

    def _fake_execute_tool(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        captured["tool_name"] = tool_name
        captured["arguments"] = dict(arguments)
        return {
            "scenario_id": str(arguments.get("scenario_id", "")),
            "relative_path": str(arguments.get("relative_path", "")),
            "job_id": "job_1",
            "run_id": "job_1",
            "status": "completed",
            "result": {},
            "run_metadata": {},
        }

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)
    prompt = (
        "Write and run a script to take the DEM for this scenario and generate a boolean mask "
        "that identifies pixels where the slope is less than or equal to 5 degrees. "
        "Output the mask as a geotiff named landing_sites.tif."
    )
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt=prompt, scenario_id="mons-mouton"),
    )
    assert response.turn.status.value == "confirmation_required"
    assert response.confirmation is not None
    resolved = service.resolve_confirmation(
        session.session_id,
        response.confirmation.confirmation_id,
        AssistantConfirmationDecisionRequest(decision="allow_once"),
    )
    assert resolved.turn.status.value == "completed"
    assert captured["tool_name"] == "scenario.write_run_script"
    args = dict(captured["arguments"])  # type: ignore[arg-type]
    assert str(args.get("relative_path", "")).endswith(".py")
    content = str(args.get("content", ""))
    assert "landing_sites.tif" in content
    assert "<= threshold_deg" in content


def test_model_tool_loop_injects_domain_entity_context_wrappers(tmp_path: Path) -> None:
    prompt = "Explain hazards near Mons Mouton."
    registry = _CapturingQueueProviderRegistry([ProviderCompletion(text="Hazards summary.", finish_reason="stop")])
    service = _build_service(tmp_path, registry)
    session = service.create_session(CreateAssistantSessionRequest(title="domain-context-wrapper"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt=prompt, scenario_id="scn_1"),
    )
    assert response.turn.status.value == "completed"
    assert registry.seen_conversations
    user_contents = [
        str(item.get("content", ""))
        for item in registry.seen_conversations[-1]
        if str(item.get("role", "")) == "user"
    ]
    assert any("<DOMAIN_ENTITY_CONTEXT>" in item and "</DOMAIN_ENTITY_CONTEXT>" in item for item in user_contents)
    assert any(f"<USER_QUERY>\n{prompt}\n</USER_QUERY>" in item for item in user_contents)


def test_classifier_disabled_routes_covered_entity_kind_prompt_without_extractor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class _UnavailableExtractor:
        def classify_or_other(self, **_: object):  # noqa: ANN201
            raise RuntimeError("unavailable:should-not-be-called")

    def _fake_execute_tool(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        if tool_name == "location.goto":
            return {"scenario_id": arguments.get("scenario_id"), "feature_id": arguments.get("feature_id")}
        if tool_name == "layer.update_state":
            return {"scenario_id": arguments.get("scenario_id"), "layer_name": arguments.get("layer_name")}
        raise AssertionError(f"unexpected tool {tool_name}")

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)

    service = _build_service(
        tmp_path,
        _QueueProviderRegistry(completions=[ProviderCompletion(text="unused", finish_reason="stop")]),
        prompt_segmentation_enabled=True,
    )
    service._command_router = HybridCommandRouter(enabled=False)  # type: ignore[attr-defined]
    service._prompt_classifier = PromptClassifier(extractor=_UnavailableExtractor())  # type: ignore[arg-type,attr-defined]
    service._prompt_classifier.classify = lambda **_: [  # type: ignore[attr-defined]
        SegmentClassification(
            segment_id="s1",
            text="show mons mouton",
            offsets=SegmentOffsets(start=0, stop=16),
            segment_class="other",
            confidence=0.7,
            classification_origin="fallback_other",
        )
    ]
    service._entity_resolver.resolve_segments = lambda classifications, scenario_id: {  # type: ignore[attr-defined]
        item.segment_id: SegmentEntityResolution(
            segment_id=item.segment_id,
            canonical_operation="show",
            verb_normalization=VerbNormalizationResult(
                canonical_operation="show",
                normalized_input_operation="show",
                source="test",
            ),
            target_kind="feature",
            target_mention="Mons Mouton",
            target_resolved_id="feature:42",
        )
        for item in classifications
    }
    session = service.create_session(CreateAssistantSessionRequest(title="classifier-disabled-covered"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="show mons mouton", scenario_id="scn_1"),
    )
    if response.turn.status.value == "confirmation_required":
        assert response.confirmation is not None
        assert response.confirmation.tool_name in {"location.goto", "layer.update_state"}
        response = service.resolve_confirmation(
            session.session_id,
            response.confirmation.confirmation_id,
            AssistantConfirmationDecisionRequest(decision="allow_once"),
        )
    assert response.turn.status.value == "completed"
    assert response.turn.usage.get("turn_handling_mode") in {"ordered_segment_execution", "model_tool_loop"}
    assert response.turn.usage.get("turn_handling_mode") != "intent_classification_failed"
    if response.tool_calls:
        assert [item.tool_name for item in response.tool_calls] in (
            ["location.goto"],
            ["layer.update_state"],
        )


def test_classifier_disabled_uncovered_prompt_falls_back_to_model_loop(tmp_path: Path) -> None:
    class _UnavailableExtractor:
        def classify_or_other(self, **_: object):  # noqa: ANN201
            raise RuntimeError("unavailable:should-not-be-called")

    registry = _QueueProviderRegistry([ProviderCompletion(text="Fallback response.", finish_reason="stop")])
    service = _build_service(tmp_path, registry)
    service._prompt_classifier = PromptClassifier(extractor=_UnavailableExtractor())  # type: ignore[arg-type,attr-defined]
    session = service.create_session(CreateAssistantSessionRequest(title="classifier-disabled-uncovered"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="Explain why this site is scientifically valuable.",
            scenario_id="scn_1",
        ),
    )
    assert response.turn.status.value == "completed"
    assert response.turn.usage.get("turn_handling_mode") in {"model_tool_loop", "ordered_segment_execution"}
    assert response.assistant_message is not None
    assert "fallback response" in response.assistant_message.content.lower()


def test_zoom_feature_prompt_skips_semantic_extractor_when_deterministic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class _UnavailableExtractor:
        def classify_or_other(self, **_: object):  # noqa: ANN201
            raise RuntimeError("unavailable:should-not-be-called")

    def _fake_execute_tool(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        assert tool_name == "location.goto"
        return {"scenario_id": arguments.get("scenario_id"), "feature_id": arguments.get("feature_id")}

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)

    service = _build_service(
        tmp_path,
        _QueueProviderRegistry(completions=[ProviderCompletion(text="unused", finish_reason="stop")]),
        prompt_segmentation_enabled=True,
    )
    service._prompt_classifier = PromptClassifier(extractor=_UnavailableExtractor())  # type: ignore[arg-type,attr-defined]
    service._entity_resolver.resolve_segments = lambda classifications, scenario_id: {  # type: ignore[attr-defined]
        item.segment_id: SegmentEntityResolution(
            segment_id=item.segment_id,
            canonical_operation="goto",
            verb_normalization=VerbNormalizationResult(
                canonical_operation="goto",
                normalized_input_operation=None,
                source="segment_text",
                operation_candidates=["goto"],
                matched_aliases_by_operation={"goto": ["zoom to"]},
            ),
            target_kind="feature",
            target_mention="Mons Mouton",
            target_resolved_id="feature:9071",
            mentions=[],
        )
        for item in classifications
    }
    session = service.create_session(CreateAssistantSessionRequest(title="zoom-deterministic"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Zoom to Mons Mouton.", scenario_id="scn_1"),
    )
    if response.turn.status.value == "confirmation_required":
        assert response.confirmation is not None
        assert response.confirmation.tool_name == "location.goto"
        response = service.resolve_confirmation(
            session.session_id,
            response.confirmation.confirmation_id,
            AssistantConfirmationDecisionRequest(decision="allow_once"),
        )
    assert response.turn.status.value == "completed"
    assert response.turn.usage.get("turn_handling_mode") in {"ordered_segment_execution", "model_tool_loop"}
    assert response.turn.usage.get("turn_handling_mode") != "intent_classification_failed"
    assert [item.tool_name for item in response.tool_calls] == ["location.goto"]


def test_model_tool_loop_repairs_raster_input_shape_before_execution(tmp_path: Path, monkeypatch) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[
                    ProviderToolCall(
                        call_id="tc1",
                        name="raster.calculate",
                        arguments={
                            "scenario_id": "scn_1",
                            "expression": "slope <= 5",
                            "inputs": {"slope": "slope.tif"},
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(text="done", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    # Disable mutation confirmation in this test so the tool executes and we can inspect arguments.
    service._policy = AssistantPolicyService(require_confirmation_for_mutations=False)  # type: ignore[attr-defined]
    session = service.create_session(CreateAssistantSessionRequest(title="schema-repair"))
    captured: dict[str, object] = {}

    def _fake_execute_tool(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        captured["tool_name"] = tool_name
        captured["arguments"] = dict(arguments)
        return {"ok": True}

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="compute a raster mask"),
    )
    assert response.turn.status.value == "completed"
    assert captured["tool_name"] == "raster.calculate"
    args = dict(captured["arguments"])  # type: ignore[arg-type]
    assert isinstance(args["inputs"], dict)
    assert args["inputs"]["slope"] == {"relative_path": "slope.tif"}


def test_model_tool_loop_sanitizes_raster_calculate_noisy_arguments(tmp_path: Path, monkeypatch) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[
                    ProviderToolCall(
                        call_id="tc1",
                        name="raster.calculate",
                        arguments={
                            "scenario_id": "scn_1",
                            "expression": "slope <= 5",
                            "inputs": {"slope": {"relative_path": "slope.tif"}},
                            "output_relative_path": "landing_sites5.tif",
                            "overwrite_mode": "ask",
                            "mode": "interpolate",
                            "patch_width": 0,
                            "patch_height": 0,
                            "chunk_time_count": 0,
                            "buffer_count": 0,
                            "poll_timeout_ms": 0,
                            "output_path": None,
                            "scenario_root_dir": None,
                            "publish_layer": {
                                "enabled": True,
                                "title": "landing_sites5",
                                "visible": True,
                                "transparent_background": True,
                                "junk": "drop-me",
                            },
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(text="done", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    service._policy = AssistantPolicyService(require_confirmation_for_mutations=False)  # type: ignore[attr-defined]
    session = service.create_session(CreateAssistantSessionRequest(title="sanitize-raster-calc"))
    captured: dict[str, object] = {}

    def _fake_execute_tool(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        captured["tool_name"] = tool_name
        captured["arguments"] = dict(arguments)
        return {"ok": True}

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="highlight slope <= 5"),
    )
    assert response.turn.status.value == "completed"
    args = dict(captured["arguments"])  # type: ignore[arg-type]
    assert captured["tool_name"] == "raster.calculate"
    assert "output_path" not in args
    assert "scenario_root_dir" not in args
    assert "mode" not in args
    assert "patch_width" not in args
    assert "patch_height" not in args
    assert "chunk_time_count" not in args
    assert "buffer_count" not in args
    assert "poll_timeout_ms" not in args
    assert args["publish_layer"]["transparent_background"] is True
    assert "junk" not in args["publish_layer"]


def test_model_tool_loop_retries_once_after_invalid_tool_arguments(tmp_path: Path, monkeypatch) -> None:
    provider_registry = _CapturingQueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[
                    ProviderToolCall(
                        call_id="tc1",
                        name="raster.calculate",
                        arguments={
                            "scenario_id": "scn_1",
                            "inputs": {"slope": {"relative_path": "slope.tif"}},
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(
                text="",
                tool_calls=[
                    ProviderToolCall(
                        call_id="tc2",
                        name="raster.calculate",
                        arguments={
                            "scenario_id": "scn_1",
                            "expression": "slope <= 5",
                            "inputs": {"slope": {"relative_path": "slope.tif"}},
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(text="done", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    service._policy = AssistantPolicyService(require_confirmation_for_mutations=False)  # type: ignore[attr-defined]
    session = service.create_session(CreateAssistantSessionRequest(title="retry-invalid-args"))

    def _fake_execute_tool(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        assert tool_name == "raster.calculate"
        assert "expression" in arguments
        return {"ok": True}

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="highlight slope <= 5"),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "done" in response.assistant_message.content
    assert len(response.tool_calls) == 1
    assert len(provider_registry.seen_conversations) >= 2
    replayed_second = json.dumps(provider_registry.seen_conversations[1], ensure_ascii=True)
    assert "invalid" in replayed_second.lower()
    assert "corrected tool call" in replayed_second.lower()


def test_model_tool_loop_stops_on_repeated_identical_tool_call(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[
                    ProviderToolCall(
                        call_id="tc1",
                        name="artifact.describe_table",
                        arguments={"scenario_id": "scn_test", "relative_path": "outputs/sample_stats.csv"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(
                text="",
                tool_calls=[
                    ProviderToolCall(
                        call_id="tc2",
                        name="artifact.describe_table",
                        arguments={"scenario_id": "scn_test", "relative_path": "outputs/sample_stats.csv"},
                    )
                ],
                finish_reason="tool_calls",
            ),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="tool-loop-repeat-guard"))

    sample_csv = tmp_path / "scenarios" / "scn_test" / "outputs" / "sample_stats.csv"
    sample_csv.parent.mkdir(parents=True, exist_ok=True)
    sample_csv.write_text("name,age,zipcode\nmark,65,95051\nhugh,28,02118\n", encoding="utf-8")

    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Show that csv file as a table.", scenario_id="scn_test"),
    )

    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "sample_stats.csv" in response.assistant_message.content
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_name == "artifact.describe_table"
    assert response.assistant_message.outputs[0].kind == "table"


def test_model_tool_loop_falls_back_after_provider_failure(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _FailThenFallbackRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="provider-fallback"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Help me create a script for this scenario."),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert response.assistant_message.content == "Fallback model response."
    assert response.turn.usage.get("turn_handling_mode") == "ordered_segment_execution"
    assert response.turn.usage.get("fallback_used") is True
    assert response.turn.usage.get("model_id") == "qwen2.5-coder:7b-instruct-q4_K_M"
    assert response.assistant_message is not None
    metadata = response.assistant_message.metadata
    assert metadata.get("requested_provider_id") == "ollama"
    assert metadata.get("requested_model_id") == "qwen3.5:35b-a3b"
    assert metadata.get("final_provider_id") == "ollama"
    assert metadata.get("final_model_id") == "qwen2.5-coder:7b-instruct-q4_K_M"
    assert metadata.get("fallback_used") is True
    attempted = metadata.get("attempted_models")
    assert isinstance(attempted, list)
    assert len(attempted) >= 2
    assert attempted[0]["model_id"] == "qwen3.5:35b-a3b"
    assert attempted[1]["model_id"] == "qwen2.5-coder:7b-instruct-q4_K_M"
    chain = metadata.get("fallback_chain")
    assert isinstance(chain, list)
    assert chain and chain[0].get("reason") == "provider_exception"


def test_model_tool_loop_retries_on_empty_length_completion(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(text="", finish_reason="length"),
            ProviderCompletion(text="Recovered after budget retry.", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="tool-loop-empty-length-retry"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="foo"),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert response.assistant_message.content == "Recovered after budget retry."
    assert response.turn.usage.get("turn_handling_mode") == "ordered_segment_execution"


def test_model_tool_loop_parses_first_json_tool_call_when_text_contains_multiple_objects(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text='{"name":"capabilities.describe","arguments":{}}\n\n{"name":"scenario.list","arguments":{}}',
                finish_reason="stop",
            ),
            ProviderCompletion(text="Done.", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="tool-loop-multi-json"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="foo"),
    )
    assert response.turn.status.value == "completed"
    assert response.turn.usage.get("turn_handling_mode") == "ordered_segment_execution"
    assert response.assistant_message is not None
    assert response.assistant_message.content == "Done."
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_name == "capabilities.describe"


def test_model_tool_loop_logs_original_error_before_fallback(tmp_path: Path, caplog) -> None:  # noqa: ANN001
    service = _build_service(tmp_path, _FailThenFallbackRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="provider-fallback-log"))
    with caplog.at_level(logging.WARNING):
        response = service.create_turn(
            session.session_id,
            CreateAssistantTurnRequest(prompt="Help me create a script for this scenario."),
        )
    assert response.turn.status.value == "completed"


def test_model_tool_loop_respects_confirmation_gate(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[
                    ProviderToolCall(
                        call_id="tc1",
                        name="job.launch",
                        arguments={"handler_name": "ping", "params": {"message": "hello"}},
                    )
                ],
                finish_reason="tool_calls",
            ),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="tool-loop-confirm"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Can you run this now?"),
    )
    assert response.turn.status.value == "confirmation_required"
    assert response.confirmation is not None
    assert response.confirmation.tool_name == "job.launch"
    assert response.assistant_message is not None
    assert "Confirmation required for `job.launch`" in response.assistant_message.content
    assert response.turn.usage.get("turn_handling_mode") == "ordered_segment_execution"


def test_resolve_confirmation_conveys_tool_execution_failure_back_to_model_loop(tmp_path: Path, monkeypatch) -> None:
    provider_registry = _CapturingQueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[
                    ProviderToolCall(
                        call_id="tc1",
                        name="scenario.run_script",
                        arguments={"scenario_id": "scn_test", "relative_path": "scripts/extract_ridges.py"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(
                text="",
                tool_calls=[ProviderToolCall(call_id="tc2", name="capabilities.describe", arguments={})],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(text="Recovered after tool failure.", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="confirm-failure-recovery"))

    call_count = {"scenario.run_script": 0}

    def _fake_execute_tool(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        del arguments
        if tool_name == "scenario.run_script":
            call_count["scenario.run_script"] += 1
            raise RuntimeError("script execution failed: NoneType has no attribute CreateField")
        if tool_name == "capabilities.describe":
            return {"tool_count": 0}
        raise AssertionError(f"Unexpected tool: {tool_name}")

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)

    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Run the ridge script and continue.", scenario_id="scn_test"),
    )
    assert response.turn.status.value == "confirmation_required"
    assert response.confirmation is not None
    assert response.confirmation.tool_name == "scenario.run_script"

    resolved = service.resolve_confirmation(
        session.session_id,
        response.confirmation.confirmation_id,
        AssistantConfirmationDecisionRequest(decision="allow_once"),
    )
    assert resolved.turn.status.value == "completed"
    assert resolved.assistant_message is not None
    assert resolved.assistant_message.content == "Recovered after tool failure."
    assert any(call.tool_name == "capabilities.describe" for call in resolved.tool_calls)
    assert call_count["scenario.run_script"] == 1

    turn_calls = service._store.list_turn_tool_calls(resolved.turn.turn_id)  # type: ignore[attr-defined]
    assert any(call.tool_name == "scenario.run_script" and call.status == "failed" for call in turn_calls)

    saw_failure_feedback = any(
        "Tool `scenario.run_script` failed:" in str(message.get("content", ""))
        for conversation in provider_registry.seen_conversations
        for message in conversation
        if isinstance(message, dict)
    )
    assert saw_failure_feedback


def test_raster_calculate_ask_confirmation_reuses_single_tool_call(tmp_path: Path, monkeypatch) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[
                    ProviderToolCall(
                        call_id="tc1",
                        name="raster.calculate",
                        arguments={
                            "scenario_id": "scn_test",
                            "expression": "slope <= 5",
                            "inputs": {"slope": {"relative_path": "slope.tif"}},
                            "output_relative_path": "landing_sites3.tif",
                            "overwrite_mode": "ask",
                            "mode": "immediate",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(text="Completed.", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    service._policy = AssistantPolicyService(require_confirmation_for_mutations=False)  # type: ignore[attr-defined]
    scenario_dir = tmp_path / "scenarios" / "scn_test"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "landing_sites3.tif").write_bytes(b"existing")

    captured: dict[str, object] = {}

    def _fake_execute_tool(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        captured["tool_name"] = tool_name
        captured["arguments"] = dict(arguments)
        return {
            "tool_name": tool_name,
            "job_id": "job_1",
            "run_id": "job_1",
            "job": {"job_id": "job_1", "scenario_id": "scn_test", "status": "completed"},
            "result": {"output_relative_path": "landing_sites3.tif"},
        }

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)

    session = service.create_session(CreateAssistantSessionRequest(title="ask-confirm"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Highlight slope <= 5 as landing_sites3.", scenario_id="scn_test"),
    )
    assert response.turn.status.value == "confirmation_required"
    assert response.confirmation is not None
    assert response.tool_calls
    pending_id = response.tool_calls[0].tool_call_id

    resolved = service.resolve_confirmation(
        session.session_id,
        response.confirmation.confirmation_id,
        AssistantConfirmationDecisionRequest(decision="allow_once"),
    )
    assert resolved.turn.status.value == "completed"
    assert resolved.assistant_message is not None
    assert resolved.assistant_message.content == "Completed."
    assert captured.get("tool_name") == "raster.calculate"
    args = captured.get("arguments")
    assert isinstance(args, dict)
    assert args.get("overwrite_mode") == "always"
    assert len(resolved.tool_calls) == 1
    assert resolved.tool_calls[0].tool_call_id == pending_id


def test_model_tool_loop_replays_compact_tool_result_only(tmp_path: Path, monkeypatch) -> None:
    provider_registry = _CapturingQueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[
                    ProviderToolCall(
                        call_id="tc1",
                        name="artifact.preview_geotiff",
                        arguments={"scenario_id": "scn_test", "relative_path": "slope.tif"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(text="Preview ready.", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    service._semantic_intent_families_enabled = False  # type: ignore[attr-defined]
    session = service.create_session(CreateAssistantSessionRequest(title="compact-tool-result"))

    def _fake_execute_tool(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        assert tool_name == "artifact.preview_geotiff"
        assert arguments["relative_path"] == "slope.tif"
        return {
            "summary_text": "GeoTIFF preview generated for `slope.tif`.",
            "key_stats": {"preview_width": 256, "preview_height": 256},
            "generated_file_id": "fil_preview",
            "generated_relative_path": ".assistant_previews/slope.preview.png",
            "artifacts": [
                {
                    "output_id": "preview",
                    "kind": "image",
                    "mime_type": "image/png",
                    "storage": "file",
                    "title": "slope.tif preview",
                    "file_id": "fil_preview",
                    "data": {"base64": "a" * 2048},
                    "metadata": {"width": 256, "height": 256},
                }
            ],
        }

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)

    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt='Show me slope.tif as an image preview.', scenario_id="scn_test"),
    )

    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert response.assistant_message.outputs[0].file_id == "fil_preview"
    assert len(provider_registry.seen_conversations) == 2
    replayed = json.dumps(provider_registry.seen_conversations[1], ensure_ascii=True)
    assert "fil_preview" in replayed
    assert "base64" not in replayed
    assert ".assistant_previews/slope.preview.png" in replayed


def test_action_plan_fast_path_still_works_without_provider(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="parser-fast-path"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="describe capabilities"),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert response.turn.usage.get("turn_handling_mode") == "action_plan_fast_path"
    assert int(response.turn.usage.get("tool_call_count", 0)) == 1


def test_parser_fast_path_handles_visible_layers_prompt(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="visible-fast-path"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="What layers are currently visible?", scenario_id="scn_1"),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "Visible layers:" in response.assistant_message.content
    assert "Visible Layer" in response.assistant_message.content
    assert "Moon Trek Base" in response.assistant_message.content
    assert response.turn.usage.get("turn_handling_mode") == "action_plan_fast_path"


def test_explicit_tool_sequence_executes_numbered_calls_and_returns_raw_outputs(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="explicit-tool-sequence"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt=(
                "Use tool calls only.\n\n"
                "1) Call `capabilities.describe` with {}.\n"
                "2) Call `scenario.list` with {}.\n"
                "Return raw outputs."
            )
        ),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    payload = json.loads(response.assistant_message.content)
    outputs = payload.get("tool_outputs")
    assert isinstance(outputs, list)
    assert len(outputs) == 2
    assert outputs[0]["tool_name"] == "capabilities.describe"
    assert outputs[1]["tool_name"] == "scenario.list"
    assert response.turn.usage.get("turn_handling_mode") == "explicit_tool_sequence"
    assert int(response.turn.usage.get("tool_call_count", 0)) == 2


def test_action_plan_fast_path_describe_executes_without_model_followup(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(text="Capabilities summary from model.", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="describe-followup"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="describe capabilities"),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "Lunar Analyst can manage scenarios" in response.assistant_message.content
    assert response.turn.usage.get("turn_handling_mode") == "action_plan_fast_path"
    assert int(response.turn.usage.get("tool_call_count", 0)) == 1


def test_action_plan_fast_path_persists_selected_thinking_setting(tmp_path: Path) -> None:
    provider_registry = _ThinkingCapturingQueueProviderRegistry(
        completions=[ProviderCompletion(text="Capabilities summary from model.", finish_reason="stop")],
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="describe-thinking"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="describe capabilities",
            provider_id="ollama",
            model_id="gpt-oss:20b",
            thinking="high",
        ),
    )
    assert response.turn.status.value == "completed"
    assert provider_registry.last_thinking is None
    user_messages = [message for message in service.list_messages(session.session_id).messages if message.role.value == "user"]
    assert user_messages[-1].metadata.get("thinking") == "high"


def test_model_tool_loop_parses_text_json_tool_call_and_executes(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text='{"name":"capabilities.describe","arguments":{}}',
                tool_calls=[],
                finish_reason="stop",
            ),
            ProviderCompletion(text="Capabilities ready.", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="text-json-call"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Tell me what this app can do."),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert response.assistant_message.content == "Capabilities ready."
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_name == "capabilities.describe"


def test_external_mcp_agent_provider_uses_external_turn_path(tmp_path: Path) -> None:
    provider_registry = _ExternalAgentRegistry()
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="external-agent"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="List capabilities and map data."),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert response.assistant_message.content == "External MCP agent response."
    assert response.turn.usage.get("turn_handling_mode") == "external_mcp_agent"
    assert int(response.turn.usage.get("tool_call_count", 0)) == 0
    assert provider_registry.last_access_mode is None


def test_external_mcp_agent_provider_forwards_access_mode_override(tmp_path: Path) -> None:
    provider_registry = _ExternalAgentRegistry()
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="external-agent-access-mode"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="List capabilities and map data.",
            access_mode="scenario_root",
        ),
    )
    assert response.turn.status.value == "completed"
    assert provider_registry.last_access_mode == "scenario_root"


def test_external_mcp_agent_provider_forwards_active_scenario_directory(tmp_path: Path) -> None:
    provider_registry = _ExternalAgentRegistry()
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="external-agent-scenario-dir"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="Create temp1.py in current scenario directory.",
            access_mode="scenario_root",
            scenario_id="scn_test",
        ),
    )
    assert response.turn.status.value == "completed"
    assert provider_registry.last_scenario_working_directory is not None
    assert provider_registry.last_scenario_working_directory.replace("\\", "/").endswith("/scenarios/scn_test")


def test_external_mcp_agent_provider_falls_back_when_model_not_found(tmp_path: Path) -> None:
    provider_registry = _ExternalAgentModelFallbackRegistry()
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="external-agent-model-fallback"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Describe dem.tif"),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert response.assistant_message.content == "Gemini fallback response."
    assert response.assistant_message.metadata.get("model_id") == "gemini-2.5-pro"
    assert response.turn.usage.get("model_id") == "gemini-2.5-pro"


def test_external_mcp_agent_provider_retries_unknown_mcp_server_error(tmp_path: Path) -> None:
    provider_registry = _ExternalAgentUnknownServerRetryRegistry()
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="external-agent-retry"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt=(
                "Use tool calls only.\n"
                "1) Call `capabilities.describe` with {}.\n"
                "2) Call `scenario.list` with {}."
            ),
        ),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert response.assistant_message.content == '{"capabilities":{"text":"ok"},"scenarios":{"items":[],"count":0}}'
    assert provider_registry.calls == 2
    assert "Do not use resources/read to invoke tools." in provider_registry.last_system_prompt
    assert "`capabilities.describe`" in provider_registry.last_system_prompt


def test_external_mcp_agent_provider_unknown_server_falls_back_to_explicit_tool_sequence(tmp_path: Path) -> None:
    provider_registry = _ExternalAgentAlwaysUnknownServerRegistry()
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="external-agent-safe-fallback"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt=(
                "Use tool calls only.\n\n"
                "1) Call `capabilities.describe` with {}.\n"
                "2) Call `scenario.list` with {}.\n"
                "3) Return the raw tool outputs."
            ),
        ),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "tool_outputs" in response.assistant_message.content
    assert "capabilities.describe" in response.assistant_message.content
    assert "scenario.list" in response.assistant_message.content
    assert response.assistant_message.metadata.get("fallback_used") is True
    assert response.assistant_message.metadata.get("fallback_kind") == "explicit_tool_sequence_unknown_mcp_server"
    assert response.turn.usage.get("fallback_used") is True
    assert provider_registry.calls == 2


def test_external_mcp_agent_provider_cmdlet_error_falls_back_to_explicit_tool_sequence(tmp_path: Path) -> None:
    provider_registry = _ExternalAgentAlwaysCmdletToolErrorRegistry()
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="external-agent-cmdlet-fallback"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt=(
                "Use tool calls only.\n\n"
                "1) Call `capabilities.describe` with {}.\n"
                "2) Call `scenario.list` with {}.\n"
                "3) Return the raw tool outputs."
            ),
        ),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "tool_outputs" in response.assistant_message.content
    assert "capabilities.describe" in response.assistant_message.content
    assert "scenario.list" in response.assistant_message.content
    assert response.assistant_message.metadata.get("fallback_used") is True
    assert response.assistant_message.metadata.get("fallback_kind") == "explicit_tool_sequence_unknown_mcp_server"
    assert response.turn.usage.get("fallback_used") is True
    assert provider_registry.calls == 2


def test_external_mcp_agent_provider_pwsh_frame_only_error_falls_back_to_explicit_tool_sequence(tmp_path: Path) -> None:
    provider_registry = _ExternalAgentAlwaysPwshFrameOnlyErrorRegistry()
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="external-agent-pwsh-frame-fallback"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt=(
                "Use tool calls only.\n\n"
                "1) Call `capabilities.describe` with {}.\n"
                "2) Call `scenario.list` with {}.\n"
                "3) Return the raw tool outputs."
            ),
        ),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "tool_outputs" in response.assistant_message.content
    assert "capabilities.describe" in response.assistant_message.content
    assert "scenario.list" in response.assistant_message.content
    assert response.assistant_message.metadata.get("fallback_used") is True
    assert response.assistant_message.metadata.get("fallback_kind") == "explicit_tool_sequence_unknown_mcp_server"
    assert response.turn.usage.get("fallback_used") is True
    assert provider_registry.calls == 2


def test_external_mcp_agent_provider_handshake_error_falls_back_to_explicit_tool_sequence(tmp_path: Path) -> None:
    provider_registry = _ExternalAgentAlwaysMcpHandshakeErrorRegistry()
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="external-agent-handshake-fallback"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt=(
                "Use tool calls only.\n\n"
                "1) Call `capabilities.describe` with {}.\n"
                "2) Call `scenario.list` with {}.\n"
                "3) Return the raw tool outputs."
            ),
        ),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "tool_outputs" in response.assistant_message.content
    assert "capabilities.describe" in response.assistant_message.content
    assert "scenario.list" in response.assistant_message.content
    assert response.assistant_message.metadata.get("fallback_used") is True
    assert response.assistant_message.metadata.get("fallback_kind") == "explicit_tool_sequence_unknown_mcp_server"
    assert response.turn.usage.get("fallback_used") is True
    assert provider_registry.calls == 2


def test_compact_session_resets_external_provider_session(tmp_path: Path) -> None:
    provider_registry = _CompactionResetRegistry()
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="compact-reset"))
    created = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="describe capabilities"),
    )
    assert created.turn.status.value == "completed"

    compacted = service.compact_session(
        session.session_id,
        CompactAssistantSessionRequest(max_messages_to_compact=5),
    )
    assert compacted.compacted_message_count > 0
    assert provider_registry.reset_calls == [session.session_id]


def test_provider_switch_triggers_compaction_and_reset(tmp_path: Path) -> None:
    class _SwitchingRegistry(_CompactionResetRegistry):
        def __init__(self) -> None:
            super().__init__()
            self._calls = 0

        def select_for_prompt(self, *, provider_id, model_id, is_command_turn):  # noqa: ANN001
            del provider_id, model_id, is_command_turn
            self._calls += 1
            if self._calls == 1:
                return ProviderSelection(provider_id="codex_cli", model_id="gpt-5.3-codex", execution_mode="external_mcp_agent")
            return ProviderSelection(provider_id="gemini_cli", model_id="pro", execution_mode="external_mcp_agent")

        def complete(self, **kwargs):  # noqa: ANN003, ANN201
            provider_id = str(kwargs.get("provider_id", ""))
            return ProviderCompletion(text=f"{provider_id} response", finish_reason="stop")

        def catalog(self):  # noqa: ANN201
            return {
                "providers": [
                    {"provider_id": "codex_cli", "models": ["gpt-5.3-codex"]},
                    {"provider_id": "gemini_cli", "models": ["pro"]},
                ]
            }

    provider_registry = _SwitchingRegistry()
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="provider-switch"))

    first = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Describe dem.tif"),
    )
    assert first.turn.status.value == "completed"
    assert provider_registry.reset_calls == []

    second = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Describe dem.tif again"),
    )
    assert second.turn.status.value == "completed"
    assert provider_registry.reset_calls == [session.session_id]
    system_messages = [m for m in service.list_messages(session.session_id).messages if m.role.value == "system"]
    assert any(m.metadata.get("kind") == "provider_switch" for m in system_messages)


def test_model_tool_loop_executes_layer_list_visible_tool(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text='{"name":"layer.list_visible","arguments":{}}',
                tool_calls=[],
                finish_reason="stop",
            ),
            ProviderCompletion(text="", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="visible-layers"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="What layers are currently visible?", scenario_id="scn_1"),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "Visible layers:" in response.assistant_message.content
    assert "Visible Layer" in response.assistant_message.content
    assert "Moon Trek Base" in response.assistant_message.content
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_name == "layer.list_visible"


def test_layer_list_visible_respects_base_visibility_from_turn_request(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="visible-base-off"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="What layers are currently visible?",
            scenario_id="scn_1",
            base_layer_visible=False,
        ),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "Visible Layer" in response.assistant_message.content
    assert "Moon Trek Base" not in response.assistant_message.content


def test_plan_tool_call_describe_relative_geotiff_uses_scenario_relative_path(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    tool_name, tool_args = service._plan_tool_call(  # noqa: SLF001
        prompt="Describe hillshade.tif",
        scenario_id="scn_1",
    )
    assert tool_name == "artifact.describe_geotiff"
    assert tool_args == {"scenario_id": "scn_1", "relative_path": "hillshade.tif"}


def test_plan_tool_call_set_scenario_normalizes_to_prefix_and_trailing_punctuation(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    tool_name, tool_args = service._plan_tool_call(  # noqa: SLF001
        prompt="set scenario to test_scenario .",
        scenario_id="scn_1",
    )
    assert tool_name == "scenario.set_current"
    assert tool_args == {"scenario_ref": "test_scenario"}


def test_plan_tool_call_does_not_fast_path_multiline_set_scenario_prompt(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    tool_name, tool_args = service._plan_tool_call(  # noqa: SLF001
        prompt=(
            "set scenario to test_scenario .\n"
            "Do not write or run Python scripts. Use only the raster.calculate tool."
        ),
        scenario_id="scn_1",
    )
    assert tool_name is None
    assert tool_args == {}


def test_plan_tool_call_turn_off_layer_uses_layer_update_state_with_layer_name(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    tool_name, tool_args = service._plan_tool_call(  # noqa: SLF001
        prompt="Turn off the slope layer.",
        scenario_id="scn_1",
    )
    assert tool_name == "layer.update_state"
    assert tool_args == {
        "scenario_id": "scn_1",
        "layer_name": "slope",
        "visible": False,
    }


def test_plan_tool_call_turn_on_layer_without_layer_suffix_uses_layer_update_state(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    tool_name, tool_args = service._plan_tool_call(  # noqa: SLF001
        prompt="Turn on slope",
        scenario_id="scn_1",
    )
    assert tool_name == "layer.update_state"
    assert tool_args == {
        "scenario_id": "scn_1",
        "layer_name": "slope",
        "visible": True,
    }


def test_plan_tool_call_highlight_slope_threshold_uses_raster_calculate(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    tool_name, tool_args = service._plan_tool_call(  # noqa: SLF001
        prompt="Highlight pixels where the slope is 5 degrees or less.",
        scenario_id="scn_1",
    )
    assert tool_name == "raster.calculate"
    assert tool_args.get("scenario_id") == "scn_1"
    assert tool_args.get("expression") == "slope <= 5.0"
    assert tool_args.get("output_relative_path") == "slope_le_5p0deg_mask.tif"
    assert tool_args.get("overwrite_mode") == "always"
    inputs = tool_args.get("inputs")
    assert isinstance(inputs, dict)
    assert inputs.get("slope", {}).get("relative_path") == "slope.tif"


def test_parser_fast_path_highlight_slope_threshold_requires_confirmation(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="highlight-slope-confirm"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="Highlight pixels where the slope is 5 degrees or less.",
            scenario_id="scn_1",
        ),
    )
    assert response.turn.status.value == "confirmation_required"
    assert response.confirmation is not None
    assert response.confirmation.tool_name == "raster.calculate"


def test_is_command_prompt_treats_turn_on_off_layer_as_command(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    assert service._is_command_prompt("Turn off the slope layer.") is True  # noqa: SLF001
    assert service._is_command_prompt("Show the hillshade layer.") is True  # noqa: SLF001
    assert service._is_command_prompt("Apply the magma colormap to the slope layer.") is True  # noqa: SLF001


def test_should_expose_tools_for_advisory_domain_question(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    assert (
        service._should_expose_tools_for_prompt(  # noqa: SLF001
            prompt="If slope_le5.tif is not present, what should the analyst do first before making any landing-site recommendation?",
            is_command_turn=False,
        )
        is False
    )
    assert (
        service._should_expose_tools_for_prompt(  # noqa: SLF001
            prompt="Explain key hazards associated with permanently shadowed regions and how they impact traverse planning.",
            is_command_turn=False,
        )
        is False
    )


def test_should_expose_tools_for_lookup_question_and_command_prompt(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    assert (
        service._should_expose_tools_for_prompt(  # noqa: SLF001
            prompt="What files are available for product prd_123?",
            is_command_turn=False,
        )
        is True
    )
    assert (
        service._should_expose_tools_for_prompt(  # noqa: SLF001
            prompt="Run script slope_analysis.py",
            is_command_turn=True,
        )
        is True
    )


def test_advisory_domain_turn_omits_tool_schema(tmp_path: Path) -> None:
    registry = _CapturingQueueProviderRegistry([ProviderCompletion(text="Start by deriving a slope mask from the DEM.")])
    service = _build_service(tmp_path, registry)
    session = service.create_session(CreateAssistantSessionRequest(title="domain-advice"))

    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="If slope_le5.tif is not present, what should the analyst do first before making any landing-site recommendation?",
            scenario_id="scn_1",
        ),
    )

    assert response.tool_calls == []
    assert response.assistant_message is not None
    assert "slope" in response.assistant_message.content.lower()
    assert registry.seen_tool_schemas
    assert registry.seen_tool_schemas[-1] == []


def test_parser_fast_path_confirmation_turn_returns_assistant_message(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="fast-path-confirm-message"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt='run predefined job ping {"message":"hello"}'),
    )
    assert response.turn.status.value == "confirmation_required"
    assert response.confirmation is not None
    assert response.assistant_message is not None
    assert "Confirmation required for `jobs.run_predefined`" in response.assistant_message.content


def test_action_plan_fast_path_turn_on_layer_requires_confirmation(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="action-plan-layer-confirm"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Turn on slope", scenario_id="scn_1"),
    )
    assert response.turn.status.value == "confirmation_required"
    assert response.turn.usage.get("turn_handling_mode") == "action_plan_fast_path"
    assert response.confirmation is not None
    assert response.confirmation.tool_name == "layer.update_state"
    assert response.confirmation.arguments.get("layer_name") == "slope"
    assert response.confirmation.arguments.get("visible") is True


def test_action_plan_fast_path_show_slope_requires_confirmation(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="action-plan-show-slope-confirm"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="show slope", scenario_id="scn_1"),
    )
    assert response.turn.status.value == "confirmation_required"
    assert response.turn.usage.get("turn_handling_mode") == "action_plan_fast_path"
    assert response.confirmation is not None
    assert response.confirmation.tool_name == "layer.update_state"
    assert response.confirmation.arguments.get("layer_name") == "slope"
    assert response.confirmation.arguments.get("visible") is True


def test_create_turn_approve_auto_resolves_pending_confirmation(tmp_path: Path, monkeypatch) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="auto-approve"))

    def _fake_execute_tool(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        if tool_name == "layer.update_state":
            return {
                "layer_id": "lyr_slope",
                "scenario_id": str(arguments.get("scenario_id", "scn_1")),
                "title": "slope",
                "visible": bool(arguments.get("visible", True)),
            }
        raise AssertionError(f"Unexpected tool execution: {tool_name}")

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)

    first = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Turn on slope", scenario_id="scn_1"),
    )
    assert first.turn.status.value == "confirmation_required"
    assert first.confirmation is not None
    assert first.confirmation.tool_name == "layer.update_state"

    approved = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="approve", scenario_id="scn_1"),
    )
    # Auto-approval should resume the original turn instead of creating a separate normal turn.
    assert approved.turn.turn_id == first.turn.turn_id
    assert approved.turn.status.value == "completed"
    assert any(call.tool_name == "layer.update_state" and call.status == "completed" for call in approved.tool_calls)

def test_action_plan_fast_path_multi_intent_runs_switch_before_layer_confirmation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="action-plan-multi-intent"))
    calls: list[tuple[str, dict[str, object]]] = []

    def _fake_execute_tool(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        calls.append((tool_name, dict(arguments)))
        if tool_name == "scenario.set_current":
            return {
                "status": "selected",
                "scenario": {"scenario_id": "scn_test_scenario"},
            }
        raise AssertionError(f"Unexpected tool execution: {tool_name}")

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)

    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="Switch to test_scenario, then turn on slope.",
            scenario_id="scn_1",
        ),
    )
    assert response.turn.status.value == "confirmation_required"
    assert response.turn.usage.get("turn_handling_mode") == "action_plan_fast_path"
    assert calls == [("scenario.set_current", {"scenario_ref": "test_scenario"})]
    assert response.confirmation is not None
    assert response.confirmation.tool_name == "layer.update_state"
    assert response.confirmation.arguments.get("scenario_id") == "scn_test_scenario"
    assert response.confirmation.arguments.get("layer_name") == "slope"


def test_action_plan_partial_handoff_runs_deterministic_then_model_loop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[ProviderCompletion(text="Capabilities summary from model.", finish_reason="stop")]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="action-plan-partial-handoff"))
    calls: list[tuple[str, dict[str, object]]] = []

    def _fake_execute_tool(_services, *, tool_name: str, arguments: dict[str, object]):  # noqa: ANN001
        calls.append((tool_name, dict(arguments)))
        if tool_name == "scenario.set_current":
            return {
                "status": "selected",
                "scenario": {"scenario_id": "scn_test_scenario"},
            }
        raise AssertionError(f"Unexpected tool execution: {tool_name}")

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="Switch to test_scenario, then describe capabilities.",
            scenario_id="scn_1",
        ),
    )
    assert response.turn.status.value == "completed"
    assert response.turn.usage.get("turn_handling_mode") == "model_tool_loop"
    assert response.assistant_message is not None
    assert "Capabilities summary from model." in response.assistant_message.content
    assert calls == [("scenario.set_current", {"scenario_ref": "test_scenario"})]


def test_model_tool_loop_marks_mutation_unsatisfied_for_complex_visibility_prompt(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[ProviderCompletion(text="I checked products.", finish_reason="stop")]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="mutation-unsatisfied"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Show slope only if sun elevation is above 10 degrees.", scenario_id="scn_1"),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "could not complete the requested state-changing action" in response.assistant_message.content
    assert bool(response.assistant_message.metadata.get("mutation_unsatisfied")) is True
    assert response.assistant_message.metadata.get("mutation_intent") == "layer_visibility"


def test_existing_scenario_file_import_request_skips_confirmation(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _NoProviderRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="import-existing-no-confirm"))
    service._tool_services.scenario_service.resolve_scenario_file = lambda scenario_id, relative_path: (  # type: ignore[attr-defined] # noqa: E731
        tmp_path / "scenarios" / scenario_id / relative_path
    )
    policy = service._store.get_session(session.session_id).policy  # noqa: SLF001
    needs_confirmation, action_type = service._needs_confirmation_for_tool(  # noqa: SLF001
        session_id=session.session_id,
        tool_name="scenario.import_geotiff",
        tool_args={"scenario_id": "scn_1", "source_path": "hillshade.tif"},
        fallback_action_type=AssistantConfirmationActionType.IMPORT_FILE,
        policy=policy,
    )
    assert needs_confirmation is False
    assert action_type is None


def test_prompt_segmenter_splits_mixed_prompt_segments(prompt_segmenter_factory) -> None:  # noqa: ANN001
    segmenter = prompt_segmenter_factory()
    segments = segmenter.segment(
        "Switch to Shackleton scenario. Turn on slope and hillshade. Then suggest top 3 landing zones."
    )
    assert len(segments) >= 3
    assert segments[0].text.lower().startswith("switch to shackleton")
    assert any("suggest top 3 landing zones" in item.text.lower() for item in segments)


def test_prompt_classifier_labels_deterministic_and_llm(prompt_segmenter_factory) -> None:  # noqa: ANN001
    segmenter = prompt_segmenter_factory()
    segments = segmenter.segment("Turn on slope layer. Explain why this site is scientifically valuable.")
    router = HybridCommandRouter(enabled=True)
    classifier = PromptClassifier()
    classifications = classifier.classify(segments=segments, scenario_id="scn_1", router=router)
    labels = [item.segment_class for item in classifications]
    assert "command" in labels
    assert "other" in labels


def test_turn_execution_plan_builds_ordered_modes(prompt_segmenter_factory) -> None:  # noqa: ANN001
    segmenter = prompt_segmenter_factory()
    segments = segmenter.segment("Switch to s1. Turn on slope. Then explain tradeoffs.")
    router = HybridCommandRouter(enabled=True)
    classifier = PromptClassifier()
    classifications = classifier.classify(segments=segments, scenario_id="scn_1", router=router)
    planner = TurnExecutionPlanBuilder().build(
        turn_id="turn_1",
        session_id="sess_1",
        prompt_text="Switch to s1. Turn on slope. Then explain tradeoffs.",
        segments=segments,
        classifications=classifications,
        runtime_state_seed={"active_scenario_id": "scn_1"},
    )
    assert planner.schema_version == "1.0"
    assert len(planner.segments) >= 2
    assert planner.segments[0].start_char <= planner.segments[1].start_char


def test_argument_repair_normalizes_and_blocks_path_escape() -> None:
    repairer = ToolArgumentRepairer(enabled=True)
    repaired, outcome = repairer.repair(
        tool_name="raster.calculate",
        arguments={"output_relative_path": "results\\mask.tif"},
        scenario_id="scn_1",
        schema={},
    )
    assert outcome.repair_applied is True
    assert repaired["output_relative_path"] == "results/mask.tif"

    _, blocked = repairer.repair(
        tool_name="scenario.write_script",
        arguments={"relative_path": "../escape.py"},
        scenario_id="scn_1",
        schema={},
    )
    assert blocked.repair_status == "blocked_requires_clarification"


def test_turn_metadata_includes_execution_plan_and_success_semantics_when_enabled(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[ProviderToolCall(call_id="tc1", name="layer.list_visible", arguments={})],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(text="Visible layers returned.", finish_reason="stop"),
        ]
    )
    service = _build_service(
        tmp_path,
        provider_registry,
        prompt_segmentation_enabled=True,
        prompt_classification_contract_enabled=True,
        turn_execution_plan_contract_enabled=True,
        segment_state_merge_policy_enabled=True,
        success_semantics_policy_enabled=True,
        observability_contract_enabled=True,
    )
    session = service.create_session(CreateAssistantSessionRequest(title="meta"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="List visible layers and explain what that means.", scenario_id="scn_1"),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "execution_plan_segments" in response.assistant_message.metadata
    assert "aggregate_status" in response.assistant_message.metadata
    assert "turn_state_merge" in response.assistant_message.metadata


def test_turn_metadata_omits_execution_plan_fields_when_disabled(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[ProviderToolCall(call_id="tc1", name="layer.list_visible", arguments={})],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(text="Visible layers returned.", finish_reason="stop"),
        ]
    )
    service = _build_service(
        tmp_path,
        provider_registry,
        prompt_segmentation_enabled=True,
        prompt_classification_contract_enabled=True,
        turn_execution_plan_contract_enabled=False,
        segment_state_merge_policy_enabled=True,
        success_semantics_policy_enabled=True,
        observability_contract_enabled=True,
    )
    session = service.create_session(CreateAssistantSessionRequest(title="meta-disabled"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="List visible layers and explain what that means.", scenario_id="scn_1"),
    )
    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "execution_plan_segments" not in response.assistant_message.metadata
    assert "aggregate_status" not in response.assistant_message.metadata
    assert "turn_state_merge" not in response.assistant_message.metadata
