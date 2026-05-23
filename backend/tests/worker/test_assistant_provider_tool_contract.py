from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from backend.services.assistant.provider_registry import AssistantProviderRegistry
from backend.services.assistant.providers.base import ProviderCompletion, ProviderToolCall


@dataclass
class _DummyProvider:
    provider_id: str = "dummy"
    received: dict[str, Any] = field(default_factory=dict)

    def list_models(self) -> list[str]:
        return ["dummy-model"]

    def complete(
        self,
        *,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        session_id: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        cache_context: dict[str, str] | None = None,
        tool_schema: list[dict[str, object]] | None = None,
        max_output_tokens: int | None = None,
        thinking: bool | str | None = None,
    ) -> ProviderCompletion:
        if on_delta is not None:
            on_delta("d")
        self.received = {
            "model_id": model_id,
            "system_prompt": system_prompt,
            "conversation": conversation,
            "session_id": session_id,
            "cache_context": cache_context,
            "tool_schema": tool_schema,
            "max_output_tokens": max_output_tokens,
            "thinking": thinking,
        }
        return ProviderCompletion(
            text="ok",
            tool_calls=[ProviderToolCall(call_id="t1", name="capabilities.describe", arguments={})],
            finish_reason="tool_calls",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "cached_prompt_tokens": 0},
        )


def test_provider_registry_forwards_tool_schema_and_token_budget() -> None:
    registry = AssistantProviderRegistry(config={"default_provider": "dummy", "default_model": "dummy-model"})
    dummy = _DummyProvider()
    registry._providers = {"dummy": dummy}  # type: ignore[attr-defined]
    deltas: list[str] = []
    result = registry.complete(
        provider_id="dummy",
        model_id="dummy-model",
        system_prompt="system",
        conversation=[{"role": "user", "content": "hello"}],
        session_id="as_1",
        on_delta=deltas.append,
        cache_context={"stable_prefix_hash": "abc"},
        tool_schema=[{"type": "function", "function": {"name": "capabilities.describe"}}],
        max_output_tokens=128,
        thinking="high",
    )
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls[0].name == "capabilities.describe"
    assert dummy.received["max_output_tokens"] == 128
    assert dummy.received["thinking"] == "high"
    assert dummy.received["session_id"] == "as_1"
    assert isinstance(dummy.received["tool_schema"], list)
    assert deltas == ["d"]


def test_provider_registry_catalog_includes_model_metadata() -> None:
    registry = AssistantProviderRegistry(config={"default_provider": "dummy", "default_model": "dummy-model"})
    dummy = _DummyProvider()

    def _list_model_metadata(*, models=None):  # noqa: ANN001, ANN202
        assert models == ["dummy-model"]
        return {"dummy-model": {"capabilities": ["thinking"], "thinking_mode": "boolean"}}

    dummy.list_model_metadata = _list_model_metadata  # type: ignore[attr-defined]
    registry._providers = {"dummy": dummy}  # type: ignore[attr-defined]

    catalog = registry.catalog()
    assert catalog.providers[0].model_metadata["dummy-model"].thinking_mode == "boolean"


def test_provider_registry_normalizes_thinking_setting_against_model_metadata() -> None:
    registry = AssistantProviderRegistry(config={"default_provider": "dummy", "default_model": "dummy-model"})
    dummy = _DummyProvider()

    def _list_model_metadata(*, models=None):  # noqa: ANN001, ANN202
        model_id = (models or ["dummy-model"])[0]
        mode = "level" if model_id == "gpt-oss:20b" else "boolean"
        return {model_id: {"capabilities": ["thinking"], "thinking_mode": mode}}

    dummy.list_model_metadata = _list_model_metadata  # type: ignore[attr-defined]
    registry._providers = {"dummy": dummy}  # type: ignore[attr-defined]

    assert registry.normalize_thinking_setting(
        provider_id="dummy",
        model_id="gpt-oss:20b",
        thinking="high",
    ) == "high"
    assert registry.normalize_thinking_setting(
        provider_id="dummy",
        model_id="qwen3.5:27b",
        thinking="high",
    ) is None
    assert registry.normalize_thinking_setting(
        provider_id="dummy",
        model_id="qwen3.5:27b",
        thinking="true",
    ) is True


def test_provider_registry_select_for_prompt_uses_command_override() -> None:
    registry = AssistantProviderRegistry(
        config={
            "default_provider": "ollama",
            "default_model": "slow-model",
            "ollama": {
                "enabled": True,
                "base_url": "http://127.0.0.1:11434",
                "model": "slow-model",
                "models": ["slow-model", "fast-model"],
            },
            "performance": {
                "command_provider": "ollama",
                "command_model": "fast-model",
            },
        }
    )
    selection = registry.select_for_prompt(provider_id=None, model_id=None, is_command_turn=True)
    assert selection.provider_id == "ollama"
    assert selection.model_id == "fast-model"
    assert registry.performance().empty_completion_retry_max_output_tokens == 4096


def test_provider_registry_registers_external_cli_provider_with_execution_mode() -> None:
    registry = AssistantProviderRegistry(
        config={
            "default_provider": "codex_cli",
            "default_model": "gpt-5-codex",
            "ollama": {"enabled": False},
            "codex_cli": {
                "enabled": True,
                "command": ["codex", "exec"],
                "args": ["--model", "{model_id}"],
                "model": "gpt-5-codex",
                "models": ["gpt-5-codex"],
                "mcp_sse_url": "http://127.0.0.1:8000/api/v1/mcp/sse",
            },
        }
    )
    selection = registry.select(provider_id=None, model_id=None)
    assert selection.provider_id == "codex_cli"
    assert selection.model_id == "gpt-5-codex"
    assert selection.execution_mode == "external_mcp_agent"

    catalog = registry.catalog()
    assert catalog.default_provider_id == "codex_cli"
    assert catalog.default_model_id == "gpt-5-codex"
    providers = {item.provider_id: item for item in catalog.providers}
    codex = providers["codex_cli"]
    assert codex.kind == "local"
    assert codex.execution_mode == "external_mcp_agent"
    assert codex.access_mode == "mcp_only"
    assert bool(getattr(registry._providers["codex_cli"], "persistent", False)) is True  # type: ignore[attr-defined]
