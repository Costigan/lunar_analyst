from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.services.assistant.intent_to_tool_planner import IntentToToolPlanner
from backend.services.assistant.prompt_classifier import SegmentClassification, SegmentOffsets


def _cases_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "assistant_intent_family_replay"
        / "golden_cases_v1.jsonl"
    )


def _load_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _cases_path().read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _classification_for_case(case: dict[str, Any]) -> SegmentClassification:
    payload = dict(case.get("classification", {}) or {})
    prompt = str(case.get("prompt", ""))
    return SegmentClassification(
        segment_id="s1",
        text=prompt,
        offsets=SegmentOffsets(start=0, stop=max(0, len(prompt))),
        segment_class="intent_family",
        confidence=0.9,
        classification_origin="replay_fixture",
        intent_family=str(payload.get("intent_family", "")).strip() or None,
        intent_properties=dict(payload.get("intent_properties", {}) or {}),
        validation_status="validated",
    )


def test_intent_family_replay_golden_cases() -> None:
    planner = IntentToToolPlanner()
    for case in _load_cases():
        case_id = str(case.get("id", "unknown"))
        scenario_id = str(case.get("scenario_id", "")).strip() or None
        expected = dict(case.get("expected", {}) or {})
        classification = _classification_for_case(case)
        result = planner.map(classification=classification, scenario_id=scenario_id)

        assert result is not None, f"{case_id}: planner returned None"
        mapped = bool(expected.get("mapped", False))
        if mapped:
            assert result.requires_clarification is False, f"{case_id}: unexpected clarification"
            if bool(expected.get("model_handoff", False)):
                assert str(result.model_handoff_prompt or "").strip(), f"{case_id}: expected model handoff prompt"
            else:
                assert len(result.tool_steps) >= 1, f"{case_id}: expected mapped tool step"
                first = result.tool_steps[0]
                assert first.tool_name == str(expected.get("tool_name", "")), f"{case_id}: tool mismatch"
                expected_args = dict(expected.get("arguments", {}) or {})
                for key, value in expected_args.items():
                    assert first.arguments.get(key) == value, f"{case_id}: argument mismatch for {key}"
        else:
            assert result.requires_clarification is bool(expected.get("requires_clarification", True)), (
                f"{case_id}: clarification flag mismatch"
            )
            expected_reason = str(expected.get("blocking_reason_code", "")).strip()
            if expected_reason:
                assert result.blocking_reason_code == expected_reason, f"{case_id}: blocking reason mismatch"
