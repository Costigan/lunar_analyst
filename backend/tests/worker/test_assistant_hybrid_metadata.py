from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.contracts.assistant_models import CreateAssistantSessionRequest, CreateAssistantTurnRequest
from backend.services.assistant.assistant_service import AssistantService, ToolExecutionServices
from backend.services.assistant.policy_service import AssistantPolicyService
from backend.services.assistant.prompt_classifier import PromptClassifier
from backend.services.assistant.provider_registry import AssistantPerformanceConfig, ProviderSelection
from backend.services.assistant.providers.base import ProviderCompletion, ProviderToolCall
from backend.services.assistant.session_store import AssistantSessionStore


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
        del provider_id, model_id, system_prompt, conversation, cache_context, tool_schema, max_output_tokens, thinking
        if not self._completions:
            return ProviderCompletion(text="done")
        return self._completions.pop(0)

    def performance(self) -> AssistantPerformanceConfig:
        return AssistantPerformanceConfig(max_tool_iterations_per_turn=4, max_tool_calls_per_iteration=3)

    def catalog(self):  # noqa: ANN201
        return {"providers": []}


class _FailingProviderRegistry:
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
        del provider_id, model_id, system_prompt, conversation, cache_context, tool_schema, max_output_tokens, thinking
        raise RuntimeError("provider completion failed")

    def performance(self) -> AssistantPerformanceConfig:
        return AssistantPerformanceConfig(max_tool_iterations_per_turn=4, max_tool_calls_per_iteration=3)

    def catalog(self):  # noqa: ANN201
        return {"providers": []}


def _build_service(tmp_path: Path, provider_registry: _QueueProviderRegistry) -> AssistantService:
    store = AssistantSessionStore(tmp_path / "assistant_sessions.db")
    policy = AssistantPolicyService(require_confirmation_for_mutations=False)
    scenarios_root = tmp_path / "scenarios"
    scenarios_root.mkdir(parents=True, exist_ok=True)

    def _get_scenario(scenario_id: str):  # noqa: ANN001, ANN202
        scenario_dir = scenarios_root / scenario_id
        scenario_dir.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(scenario_id=scenario_id, directory=str(scenario_dir.resolve()))

    tool_services = ToolExecutionServices(
        scenario_service=SimpleNamespace(get_scenario=_get_scenario, list_scenarios=lambda: []),
        product_service=SimpleNamespace(
            list_products=lambda scenario_id: [
                SimpleNamespace(
                    product_id="primary_dem",
                    name="Primary DEM",
                    kind="raster",
                    subkind="dem",
                )
            ],
            list_product_files=lambda product_id: [
                SimpleNamespace(
                    file_id="file_dem",
                    product_id=product_id,
                    scenario_id="scn_1",
                    relative_path="dem.tif",
                    role="data",
                )
            ]
        ),
        layer_service=SimpleNamespace(
            list_layers=lambda scenario_id: [
                SimpleNamespace(
                    layer_id="layer_visible",
                    scenario_id=scenario_id,
                    title="Visible Layer",
                    visible=True,
                    opacity=1.0,
                    z_index=1,
                )
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
        legacy_parser_enabled=True,
    )
    # Keep worker tests hermetic: avoid live extractor dependency.
    service._prompt_classifier = PromptClassifier(extractor=None)  # type: ignore[attr-defined]
    return service


def test_assistant_turn_metadata_contains_execution_plan_and_merge_payload(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[ProviderToolCall(call_id="tc1", name="capabilities.describe", arguments={})],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(text="Visible layers are loaded.", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    session = service.create_session(CreateAssistantSessionRequest(title="hybrid-meta"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="Please summarize what tools are available for this scenario analysis.",
            scenario_id="scn_1",
        ),
    )

    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    metadata = response.assistant_message.metadata
    assert "execution_plan_segments" in metadata
    assert "aggregate_status" in metadata
    assert "segment_outcomes" in metadata
    assert "turn_state_merge" in metadata
    assert "entity_resolution_segments" in metadata
    segment_resolution = metadata["entity_resolution_segments"][0]
    assert "canonical_operation" in segment_resolution
    assert "direct_object_candidate" in segment_resolution
    assert "resolved_entity_summary" in segment_resolution


def test_known_product_references_collects_product_fields(tmp_path: Path) -> None:
    provider_registry = _QueueProviderRegistry(completions=[ProviderCompletion(text="done", finish_reason="stop")])
    service = _build_service(tmp_path, provider_registry)
    refs = service._known_product_references("scn_1")  # noqa: SLF001
    assert "primary_dem" in refs
    assert "Primary DEM" in refs
    assert "dem" in refs


def test_create_product_turn_dispatches_raster_calculate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider_registry = _QueueProviderRegistry(completions=[ProviderCompletion(text="unused", finish_reason="stop")])
    service = _build_service(tmp_path, provider_registry)
    service._prompt_classifier = PromptClassifier(extractor=None)  # noqa: SLF001
    captured: dict[str, object] = {}

    def _fake_execute_tool(services, *, tool_name, arguments):  # noqa: ANN001, ANN202
        del services
        captured["tool_name"] = tool_name
        captured["arguments"] = dict(arguments)
        return {
            "scenario_id": arguments["scenario_id"],
            "output_relative_path": arguments["output_relative_path"],
            "product_id": "prod_slope",
            "file_id": "file_slope",
        }

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)

    session = service.create_session(CreateAssistantSessionRequest(title="create-product"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="Create a slope raster from the primary DEM.",
            scenario_id="scn_1",
        ),
    )

    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert captured["tool_name"] == "raster.calculate"
    arguments = captured["arguments"]
    assert isinstance(arguments, dict)
    assert arguments["expression"] == "slope(dem)"
    assert arguments["inputs"] == {"dem": {"product_id": "primary_dem"}}
    assert arguments["output_relative_path"] == "slope.tif"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_name == "raster.calculate"
    assert response.assistant_message is not None
    plan_segments = response.assistant_message.metadata["execution_plan_segments"]
    create_segment = [item for item in plan_segments if item["classification"]["label"] == "create_product"][0]
    assert create_segment["selected_recipe_id"] == "slope_from_dem_v1"
    assert create_segment["prerequisite_count"] == 1


def test_create_product_turn_reuses_existing_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider_registry = _QueueProviderRegistry(completions=[ProviderCompletion(text="unused", finish_reason="stop")])
    service = _build_service(tmp_path, provider_registry)
    service._prompt_classifier = PromptClassifier(extractor=None)  # noqa: SLF001
    service._tool_services.product_service.list_products = lambda scenario_id: [  # noqa: SLF001
        SimpleNamespace(product_id="primary_dem", name="Primary DEM", kind="raster", subkind="dem"),
        SimpleNamespace(product_id="prod_slope", name="Slope", kind="raster", subkind="slope_raster"),
    ]
    service._tool_services.product_service.list_product_files = lambda product_id: [  # noqa: SLF001
        SimpleNamespace(file_id="file_dem", product_id="primary_dem", scenario_id="scn_1", relative_path="dem.tif", role="data")
        if product_id == "primary_dem"
        else SimpleNamespace(file_id="file_slope", product_id="prod_slope", scenario_id="scn_1", relative_path="slope.tif", role="data")
    ]

    def _fail_execute_tool(services, *, tool_name, arguments):  # noqa: ANN001, ANN202
        del services, tool_name, arguments
        raise AssertionError("execute_tool should not be called when reusing an existing output")

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fail_execute_tool)

    session = service.create_session(CreateAssistantSessionRequest(title="reuse-product"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="Create a slope raster from the primary DEM.",
            scenario_id="scn_1",
        ),
    )

    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "Using existing product `slope.tif`" in response.assistant_message.content
    assert response.tool_calls == []


def test_create_product_turn_blocks_unsupported_product_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider_registry = _QueueProviderRegistry(completions=[ProviderCompletion(text="unused", finish_reason="stop")])
    service = _build_service(tmp_path, provider_registry)
    service._prompt_classifier = PromptClassifier(extractor=None)  # noqa: SLF001

    def _fail_execute_tool(services, *, tool_name, arguments):  # noqa: ANN001, ANN202
        del services, tool_name, arguments
        raise AssertionError("execute_tool should not run for blocked create_product turns")

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fail_execute_tool)

    session = service.create_session(CreateAssistantSessionRequest(title="blocked-product"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="Create a roughness raster from the primary DEM.",
            scenario_id="scn_1",
        ),
    )

    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert "No deterministic recipe is configured" in response.assistant_message.content
    assert response.tool_calls == []


def test_create_product_threshold_mask_uses_dem_when_slope_raster_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_registry = _QueueProviderRegistry(completions=[ProviderCompletion(text="unused", finish_reason="stop")])
    service = _build_service(tmp_path, provider_registry)
    service._prompt_classifier = PromptClassifier(extractor=None)  # noqa: SLF001
    captured: dict[str, object] = {}

    def _fake_execute_tool(services, *, tool_name, arguments):  # noqa: ANN001, ANN202
        del services
        captured["tool_name"] = tool_name
        captured["arguments"] = dict(arguments)
        return {
            "scenario_id": arguments["scenario_id"],
            "output_relative_path": arguments["output_relative_path"],
            "product_id": "prod_mask",
            "file_id": "file_mask",
        }

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)

    session = service.create_session(CreateAssistantSessionRequest(title="threshold-mask"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="Create a threshold mask where slope <= 5 degrees from the primary DEM.",
            scenario_id="scn_1",
        ),
    )

    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert captured["tool_name"] == "raster.calculate"
    arguments = captured["arguments"]
    assert isinstance(arguments, dict)
    assert arguments["expression"] == "slope(dem) <= 5.0"
    assert arguments["inputs"] == {"dem": {"product_id": "primary_dem"}}
    assert arguments["output_relative_path"] == "slope_le_5p0deg_mask.tif"


def test_ordered_deterministic_segments_execute_in_prompt_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_registry = _QueueProviderRegistry(completions=[ProviderCompletion(text="unused", finish_reason="stop")])
    service = _build_service(tmp_path, provider_registry)
    service._prompt_classifier = PromptClassifier(extractor=None)  # noqa: SLF001
    call_order: list[str] = []

    def _fake_execute_tool(services, *, tool_name, arguments):  # noqa: ANN001, ANN202
        del services
        call_order.append(tool_name)
        if tool_name == "layer.update_state":
            return {"layer_id": "layer_slope", "title": "Slope", "visible": True}
        return {
            "scenario_id": arguments["scenario_id"],
            "output_relative_path": arguments["output_relative_path"],
            "product_id": "prod_slope",
            "file_id": "file_slope",
        }

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)

    session = service.create_session(CreateAssistantSessionRequest(title="ordered-deterministic"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="Turn on slope. Create a slope raster from the primary DEM.",
            scenario_id="scn_1",
        ),
    )

    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert call_order == ["layer.update_state", "raster.calculate"]
    assert [item.tool_name for item in response.tool_calls] == ["layer.update_state", "raster.calculate"]
    assert "layer_id='layer_slope'" in response.assistant_message.content
    assert "output_relative_path='slope.tif'" in response.assistant_message.content


def test_mixed_segments_execute_in_prompt_order_with_other_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[ProviderToolCall(call_id="tc1", name="capabilities.describe", arguments={})],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(text="Capabilities summary.", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    service._prompt_classifier = PromptClassifier(extractor=None)  # noqa: SLF001
    call_order: list[str] = []

    def _fake_execute_tool(services, *, tool_name, arguments):  # noqa: ANN001, ANN202
        del services
        call_order.append(tool_name)
        if tool_name == "capabilities.describe":
            return {"text": "Capabilities summary."}
        return {
            "scenario_id": arguments["scenario_id"],
            "output_relative_path": arguments["output_relative_path"],
            "product_id": "prod_slope",
            "file_id": "file_slope",
        }

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)

    session = service.create_session(CreateAssistantSessionRequest(title="mixed-ordered"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="What tools are available? Create a slope raster from the primary DEM.",
            scenario_id="scn_1",
        ),
    )

    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert call_order == ["capabilities.describe", "raster.calculate"]
    assert [item.tool_name for item in response.tool_calls] == ["capabilities.describe", "raster.calculate"]
    assert "Capabilities summary." in response.assistant_message.content
    assert "output_relative_path='slope.tif'" in response.assistant_message.content


def test_mixed_segments_blocked_create_product_preserves_prior_tool_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_registry = _QueueProviderRegistry(
        completions=[
            ProviderCompletion(
                text="",
                tool_calls=[ProviderToolCall(call_id="tc1", name="capabilities.describe", arguments={})],
                finish_reason="tool_calls",
            ),
            ProviderCompletion(text="Capabilities summary.", finish_reason="stop"),
        ]
    )
    service = _build_service(tmp_path, provider_registry)
    service._prompt_classifier = PromptClassifier(extractor=None)  # noqa: SLF001
    call_order: list[str] = []

    def _fake_execute_tool(services, *, tool_name, arguments):  # noqa: ANN001, ANN202
        del services, arguments
        call_order.append(tool_name)
        if tool_name == "capabilities.describe":
            return {"text": "Capabilities summary."}
        raise AssertionError("blocked create_product path should not execute raster tools")

    monkeypatch.setattr("backend.services.assistant.assistant_service.execute_tool", _fake_execute_tool)

    session = service.create_session(CreateAssistantSessionRequest(title="mixed-blocked"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="What tools are available? Create a roughness raster from the primary DEM.",
            scenario_id="scn_1",
        ),
    )

    assert response.turn.status.value == "completed"
    assert response.assistant_message is not None
    assert call_order == ["capabilities.describe"]
    assert [item.tool_name for item in response.tool_calls] == ["capabilities.describe"]
    assert response.assistant_message.metadata["create_product_status"] == "blocked"
    assert response.assistant_message.metadata["blocking_reason_code"] == "no_supported_recipe"
    assert "No deterministic recipe is configured" in response.assistant_message.content


def test_ordered_other_segment_provider_failure_marks_turn_failed(tmp_path: Path) -> None:
    service = _build_service(tmp_path, _FailingProviderRegistry())
    service._prompt_classifier = PromptClassifier(extractor=None)  # noqa: SLF001

    session = service.create_session(CreateAssistantSessionRequest(title="ordered-other-failure"))
    response = service.create_turn(
        session.session_id,
        CreateAssistantTurnRequest(
            prompt="What tools are available?",
            scenario_id="scn_1",
        ),
    )

    assert response.turn.status.value == "failed"
    assert response.assistant_message is None
    assert "provider completion failed" in (response.turn.error or "")
