from __future__ import annotations

from types import SimpleNamespace

from backend.evals.assistant import benchmark_core
from backend.evals.assistant import score as score_module


def test_live_prediction_includes_raster_transform_prefilter_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmark_core,
        "_run_raster_transform_prefilter",
        lambda **_kwargs: {
            "eligible": False,
            "failure_stage": "parse_validate",
            "error": {"code": "raster_transform_disallowed_syntax", "message": "bad", "details": {}},
        },
    )
    response = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                tool_name="raster.transform",
                arguments={
                    "scenario_id": "scn_demo",
                    "script": "result = (a > 0) and (a < 1)",
                    "inputs": {"a": {"relative_path": "a.tif"}},
                },
            )
        ],
        assistant_message=SimpleNamespace(content="", metadata={}),
        turn=SimpleNamespace(status="confirmation_required"),
    )
    prediction = benchmark_core._build_prediction_from_live_response(
        {"id": "case_1", "prompt": "x", "scenario_id_used": "scn_demo"},
        response,
    )
    assert prediction["prefilter_eligible"] is False
    assert prediction["prefilter_failure_stage"] == "parse_validate"
    assert prediction["prefilter_error_code"] == "raster_transform_disallowed_syntax"


def test_score_prefilter_metrics_include_repair_loop_and_taxonomy() -> None:
    (
        eligibility,
        eligible_exec_success,
        repair_recovery,
        taxonomy,
    ) = score_module._compute_prefilter_metrics(
        [
            {
                "id": "case_a",
                "prefilter_eligible": False,
                "prefilter_error_code": "raster_transform_disallowed_syntax",
                "first_try_success": False,
            },
            {
                "id": "case_a.followup",
                "prefilter_eligible": True,
                "first_try_success": True,
            },
            {
                "id": "case_b",
                "prefilter_eligible": True,
                "first_try_success": True,
            },
        ]
    )
    assert eligibility.numerator == 1
    assert eligibility.denominator == 2
    assert eligible_exec_success.numerator == 1
    assert eligible_exec_success.denominator == 2
    assert repair_recovery.numerator == 1
    assert repair_recovery.denominator == 1
    assert taxonomy == {"raster_transform_disallowed_syntax": 1}


def test_live_prediction_quality_gate_rejects_garbled_text() -> None:
    garbled = "&time?" * 80
    response = SimpleNamespace(
        tool_calls=[],
        assistant_message=SimpleNamespace(content=garbled, metadata={}),
        turn=SimpleNamespace(status="completed"),
    )
    prediction = benchmark_core._build_prediction_from_live_response(
        {"id": "case_bad_resp", "prompt": "x", "scenario_id_used": "scn_demo"},
        response,
    )
    assert prediction["mode"] == "respond"
    assert prediction["answer_generated"] is True
    assert prediction["quality_gate_applied"] is True
    assert prediction["quality_pass"] is False
    assert prediction["quality_issue_count"] > 0
    assert prediction["overall_success"] is False


def test_live_prediction_quality_gate_accepts_normal_domain_text() -> None:
    response = SimpleNamespace(
        tool_calls=[],
        assistant_message=SimpleNamespace(
            content=(
                "Near the lunar south pole, illumination is highly intermittent due to low solar elevation. "
                "Operations depend on short windows of grazing sunlight, frequent terrain-cast shadowing, and "
                "power/thermal margins across local day-night transitions."
            ),
            metadata={},
        ),
        turn=SimpleNamespace(status="completed"),
    )
    prediction = benchmark_core._build_prediction_from_live_response(
        {"id": "case_good_resp", "prompt": "x", "scenario_id_used": "scn_demo"},
        response,
    )
    assert prediction["mode"] == "respond"
    assert prediction["quality_gate_applied"] is True
    assert prediction["quality_pass"] is True
    assert prediction["quality_issue_count"] == 0
    assert prediction["overall_success"] is True


def test_live_prediction_extracts_rag_context_from_metadata() -> None:
    context_text = "[src#1 path=terrain.md chunk=terrain.md:0]\nMax slope threshold is 8 degrees."
    response = SimpleNamespace(
        tool_calls=[],
        assistant_message=SimpleNamespace(
            content="The slope threshold is 8 degrees.",
            metadata={
                "rag_context_text": context_text,
                "rag_context_chars": len(context_text),
                "rag_context_capture_count": 1,
                "rag_context_captures": [
                    {
                        "iteration": 1,
                        "provider_id": "ollama",
                        "model_id": "gpt-oss:20b",
                        "context_chars": len(context_text),
                        "context_text": context_text,
                    }
                ],
            },
        ),
        turn=SimpleNamespace(status="completed"),
    )
    prediction = benchmark_core._build_prediction_from_live_response(
        {"id": "case_rag_ctx", "prompt": "x", "scenario_id_used": "scn_demo"},
        response,
    )
    assert prediction["rag_context_text"] == context_text
    assert prediction["rag_context_chars"] == len(context_text)
    assert prediction["rag_context_capture_count"] == 1
    assert isinstance(prediction["rag_context_captures"], list)
    assert prediction["rag_context_captures"][0]["iteration"] == 1


def test_live_prediction_extracts_model_attempt_metadata() -> None:
    response = SimpleNamespace(
        tool_calls=[],
        assistant_message=SimpleNamespace(
            content="Answer text.",
            metadata={
                "requested_provider_id": "ollama",
                "requested_model_id": "qwen2.5-coder:7b-instruct-q4_K_M",
                "final_provider_id": "ollama",
                "final_model_id": "gpt-oss:20b",
                "fallback_used": True,
                "attempted_models": [
                    {"provider_id": "ollama", "model_id": "qwen2.5-coder:7b-instruct-q4_K_M"},
                    {"provider_id": "ollama", "model_id": "gpt-oss:20b"},
                ],
                "fallback_chain": [
                    {
                        "from_provider_id": "ollama",
                        "from_model_id": "qwen2.5-coder:7b-instruct-q4_K_M",
                        "to_provider_id": "ollama",
                        "to_model_id": "gpt-oss:20b",
                        "reason": "provider_exception",
                    }
                ],
            },
        ),
        turn=SimpleNamespace(status="completed"),
    )
    prediction = benchmark_core._build_prediction_from_live_response(
        {"id": "case_model_meta", "prompt": "x", "scenario_id_used": "scn_demo"},
        response,
    )
    assert prediction["requested_provider_id"] == "ollama"
    assert prediction["requested_model_id"] == "qwen2.5-coder:7b-instruct-q4_K_M"
    assert prediction["final_provider_id"] == "ollama"
    assert prediction["final_model_id"] == "gpt-oss:20b"
    assert prediction["fallback_used"] is True
    assert prediction["attempted_model_count"] == 2
    assert prediction["fallback_chain_count"] == 1


def test_live_prediction_extracts_intent_family_segments_and_turn_handling_mode() -> None:
    response = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                tool_name="colormap.list",
                arguments={"scenario_id": "scn_1"},
            )
        ],
        assistant_message=SimpleNamespace(
            content="Listed colormaps.",
            metadata={
                "usage": {"turn_handling_mode": "ordered_segment_execution"},
                "execution_plan_segments": [
                    {
                        "classification": {
                            "label": "intent_family",
                            "intent_family": "layer_style_update",
                            "validation_status": "validated",
                        }
                    }
                ],
            },
        ),
        turn=SimpleNamespace(status="completed"),
    )
    prediction = benchmark_core._build_prediction_from_live_response(
        {"id": "case_family_meta", "prompt": "x", "scenario_id_used": "scn_demo"},
        response,
    )
    assert prediction["turn_handling_mode"] == "ordered_segment_execution"
    assert prediction["intent_families"] == ["layer_style_update"]
    assert prediction["intent_family_segments"] == [
        {
            "intent_family": "layer_style_update",
            "label": "intent_family",
            "validation_status": "validated",
        }
    ]
