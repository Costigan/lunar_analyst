from __future__ import annotations

from backend.evals.assistant.intent_family_thresholds import evaluate_intent_family_thresholds


def test_intent_family_thresholds_pass_when_rates_meet_limits() -> None:
    report = {
        "families": {
            "layer_style_update": {
                "validation_rate": 0.9,
                "mapping_success_rate": 0.8,
                "clarification_rate": 0.1,
                "fallback_to_model_rate": 0.2,
            }
        }
    }
    result = evaluate_intent_family_thresholds(report)
    assert result["pass"] is True
    assert result["families"]["layer_style_update"]["pass"] is True


def test_intent_family_thresholds_fail_when_mapping_success_too_low() -> None:
    report = {
        "families": {
            "surface_route_planning": {
                "validation_rate": 0.8,
                "mapping_success_rate": 0.2,
                "clarification_rate": 0.2,
                "fallback_to_model_rate": 0.3,
            }
        }
    }
    result = evaluate_intent_family_thresholds(report)
    assert result["pass"] is False
    assert result["families"]["surface_route_planning"]["pass"] is False
    assert result["families"]["surface_route_planning"]["checks"]["mapping_success_rate"] is False
