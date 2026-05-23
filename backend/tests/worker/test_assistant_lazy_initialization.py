from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.dependencies import build_service_container
from backend.contracts.assistant_models import CreateAssistantSessionRequest, CreateAssistantTurnRequest
from backend.services.assistant.assistant_service import AssistantService, ToolExecutionServices
from backend.services.assistant.policy_service import AssistantPolicyService
from backend.services.assistant.provider_registry import (
    AssistantProviderInitializationError,
    AssistantProviderRegistry,
    ProviderSelection,
)
from backend.services.assistant.providers.base import ProviderCompletion
from backend.services.assistant.session_store import AssistantSessionStore


class _DummyProvider:
    provider_id = "dummy"

    def list_models(self) -> list[str]:
        return ["dummy-model"]


class _FailingInitRegistry:
    def ensure_initialized(self) -> None:
        raise AssistantProviderInitializationError("Assistant provider initialization failed before execution.")

    def shutdown(self) -> None:
        return None

    def performance(self):  # noqa: ANN201
        from backend.services.assistant.provider_registry import AssistantPerformanceConfig

        return AssistantPerformanceConfig()


def _build_service(tmp_path: Path, provider_registry) -> AssistantService:  # noqa: ANN001
    store = AssistantSessionStore(tmp_path / "assistant_sessions.json")
    return AssistantService(
        store=store,
        policy_service=AssistantPolicyService(require_confirmation_for_mutations=True),
        provider_registry=provider_registry,  # type: ignore[arg-type]
        tool_services=ToolExecutionServices(
            scenario_service=SimpleNamespace(),
            product_service=SimpleNamespace(),
            layer_service=SimpleNamespace(),
            job_service=SimpleNamespace(),
            notebook_job_service=SimpleNamespace(),
            stores=SimpleNamespace(),
        ),
        assistant_ws_events=[],
    )


def test_provider_registry_is_lazy_until_first_use(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _register_defaults(self) -> None:  # noqa: ANN001
        calls.append("init")
        self._providers["dummy"] = _DummyProvider()

    monkeypatch.setattr(AssistantProviderRegistry, "_register_defaults", _register_defaults)
    registry = AssistantProviderRegistry(config={"default_provider": "dummy", "default_model": "dummy-model"})

    assert registry.initialization_state() == "uninitialized"

    registry.refresh_rag_indexes_on_startup()
    assert calls == []

    selection = registry.select(provider_id=None, model_id=None)
    assert selection == ProviderSelection(provider_id="dummy", model_id="dummy-model", execution_mode="tool_loop")
    assert registry.initialization_state() == "ready"
    assert calls == ["init"]

    registry.select(provider_id=None, model_id=None)
    assert calls == ["init"]


def test_provider_registry_caches_initialization_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _register_defaults(self) -> None:  # noqa: ANN001
        calls.append("init")
        raise RuntimeError("boom")

    monkeypatch.setattr(AssistantProviderRegistry, "_register_defaults", _register_defaults)
    registry = AssistantProviderRegistry(config={"default_provider": "dummy", "default_model": "dummy-model"})

    with pytest.raises(AssistantProviderInitializationError):
        registry.catalog()
    with pytest.raises(AssistantProviderInitializationError):
        registry.catalog()

    assert registry.initialization_state() == "failed"
    assert calls == ["init"]


def test_assistant_turn_returns_failed_turn_when_provider_init_fails(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _FailingInitRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="lazy-init"))

    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="Summarize the current scenario and suggest next steps."),
    )

    assert response.turn.status.value == "failed"
    assert response.turn.error == "Assistant provider initialization failed before execution."
    assert response.turn.usage["turn_handling_mode"] == "assistant_initialization_failed"
    assert response.assistant_message is None


def test_assistant_deterministic_turn_skips_provider_init(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _FailingInitRegistry())
    session = service.create_session(CreateAssistantSessionRequest(title="deterministic"))

    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="describe capabilities"),
    )

    assert response.turn.status.value == "completed"
    assert response.turn.error is None
    assert response.assistant_message is not None
    assert "Lunar Analyst" in response.assistant_message.content


def test_build_service_container_does_not_initialize_assistant_providers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LUNAR_ANALYST_WORKSPACE_ROOT", str(tmp_path / "workspace"))

    def _register_defaults(self) -> None:  # noqa: ANN001
        raise AssertionError("assistant providers should initialize lazily")

    monkeypatch.setattr(AssistantProviderRegistry, "_register_defaults", _register_defaults)

    services = build_service_container()
    try:
        assert services.assistant_service._providers.initialization_state() == "uninitialized"  # type: ignore[attr-defined]
        assert services.job_service is not None
    finally:
        services.job_service.shutdown()
        services.notebook_job_service.terminate_all_running(reason="test shutdown")
        services.marimo_service.stop_if_running()
