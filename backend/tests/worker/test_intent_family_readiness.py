from __future__ import annotations

from backend.evals.assistant.intent_family_readiness import compute_intent_family_readiness


def test_intent_family_readiness_uses_explicit_segments_and_rates() -> None:
    report = compute_intent_family_readiness(
        [
            {
                "mode": "tool_call",
                "overall_success": True,
                "turn_handling_mode": "ordered_segment_execution",
                "fallback_used": False,
                "intent_family_segments": [
                    {"intent_family": "layer_style_update", "validation_status": "validated"}
                ],
            },
            {
                "mode": "respond",
                "overall_success": True,
                "turn_handling_mode": "model_tool_loop",
                "fallback_used": True,
                "intent_family_segments": [
                    {"intent_family": "layer_style_update", "validation_status": "downgraded"}
                ],
            },
            {
                "mode": "clarify",
                "overall_success": False,
                "turn_handling_mode": "model_tool_loop",
                "fallback_used": False,
                "intent_family_segments": [
                    {"intent_family": "layer_style_update", "validation_status": "validated"}
                ],
            },
        ]
    )
    fam = report["families"]["layer_style_update"]
    assert fam["samples"] == 3
    assert fam["counts"]["validated_segments"] == 2
    assert fam["validation_rate"] == 2 / 3
    assert fam["mapping_success_rate"] == 1 / 3
    assert fam["clarification_rate"] == 1 / 3
    assert fam["fallback_to_model_rate"] == 2 / 3
    assert fam["provider_fallback_rate"] == 1 / 3


def test_intent_family_readiness_falls_back_to_tool_inference() -> None:
    report = compute_intent_family_readiness(
        [
            {
                "mode": "tool_call",
                "overall_success": True,
                "turn_handling_mode": "ordered_segment_execution",
                "fallback_used": False,
                "intent_family_segments": [],
                "tool_calls": [{"name": "artifact.preview_geotiff"}],
            }
        ]
    )
    assert "artifact_inspection" in report["families"]
    fam = report["families"]["artifact_inspection"]
    assert fam["samples"] == 1
    assert fam["counts"]["segments_observed"] == 1
