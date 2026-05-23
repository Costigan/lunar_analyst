from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.contracts.assistant_models import CreateAssistantSessionRequest
from backend.services.assistant.assistant_service import AssistantService, ToolExecutionServices
from backend.services.assistant.policy_service import AssistantPolicyService
from backend.services.assistant.session_store import AssistantSessionStore


class _ProviderRegistry:
    def __init__(self) -> None:
        self.refresh_calls = 0

    def refresh_rag_indexes_on_startup(self) -> None:
        self.refresh_calls += 1

    def shutdown(self) -> None:
        return None


def test_assistant_service_calls_registry_rag_refresh(tmp_path: Path) -> None:
    store = AssistantSessionStore(tmp_path / "assistant_sessions.json")
    registry = _ProviderRegistry()
    service = AssistantService(
        store=store,
        policy_service=AssistantPolicyService(require_confirmation_for_mutations=True),
        provider_registry=registry,  # type: ignore[arg-type]
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
    service.create_session(CreateAssistantSessionRequest(title="startup-refresh"))
    service.refresh_rag_indexes_on_startup()
    assert registry.refresh_calls == 1

