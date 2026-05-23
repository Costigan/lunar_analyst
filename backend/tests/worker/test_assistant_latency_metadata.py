from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.contracts.assistant_models import CreateAssistantSessionRequest, CreateAssistantTurnRequest
from backend.services.assistant.assistant_service import AssistantService, ToolExecutionServices
from backend.services.assistant.policy_service import AssistantPolicyService
from backend.services.assistant.session_store import AssistantSessionStore


class _NoProviderRegistry:
    def select_for_prompt(self, *, provider_id, model_id, is_command_turn):  # noqa: ANN001
        raise RuntimeError("No assistant provider is configured.")

    def complete(self, **kwargs):  # noqa: ANN003, ANN201
        raise AssertionError("complete() should not be called for parser fast-path")

    def performance(self):  # noqa: ANN201
        from backend.services.assistant.provider_registry import AssistantPerformanceConfig

        return AssistantPerformanceConfig()

    def catalog(self):  # noqa: ANN201
        return {"providers": []}


def test_turn_usage_includes_latency_fields(tmp_path: Path) -> None:
    store = AssistantSessionStore(tmp_path / "assistant_sessions.json")
    service = AssistantService(
        store=store,
        policy_service=AssistantPolicyService(require_confirmation_for_mutations=True),
        provider_registry=_NoProviderRegistry(),
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
    session = service.create_session(CreateAssistantSessionRequest(title="latency"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(prompt="describe capabilities"),
    )
    usage = response.turn.usage
    assert "latency_ms_total" in usage
    assert "latency_ms_first_event" in usage
    assert int(usage["latency_ms_total"]) >= 0
    assert int(usage["latency_ms_first_event"]) >= 0
    assert usage.get("turn_handling_mode") == "action_plan_fast_path"
