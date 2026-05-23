from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.assistant.command_router import HybridCommandRouter
from backend.services.assistant.prompt_classifier import PromptClassifier
from backend.services.assistant.prompt_segmenter import PromptSegmenter


def _cases_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "assistant_segmentation_classification"
        / "golden_cases_v2.jsonl"
    )


def _load_cases() -> list[dict]:
    rows: list[dict] = []
    path = _cases_path()
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"invalid case row at line {idx}")
        rows.append(payload)
    return rows


def test_segmentation_classification_golden_cases() -> None:
    try:
        segmenter = PromptSegmenter()
    except RuntimeError as exc:
        pytest.skip(f"Prompt segmenter spaCy model is unavailable in this environment: {exc}")
    classifier = PromptClassifier()
    router = HybridCommandRouter(enabled=True)

    for case in _load_cases():
        prompt = str(case.get("prompt", ""))
        expected_segments = list(case.get("expected_segments", []))
        expectations = dict(case.get("expectations", {}))

        actual_segments = segmenter.segment(prompt)
        actual_classes = classifier.classify(
            segments=actual_segments,
            scenario_id="scn_test",
            router=router,
        )
        actual_texts = [item.text for item in actual_segments]
        actual_labels = [item.segment_class for item in actual_classes]

        expected_texts = [str(item.get("text", "")) for item in expected_segments]
        expected_labels = [str(item.get("class", "")) for item in expected_segments]

        case_id = str(case.get("id", "unknown"))
        if bool(expectations.get("strict_text_match", True)):
            assert actual_texts == expected_texts, f"{case_id}: segment text mismatch"
        if bool(expectations.get("strict_label_match", True)):
            assert actual_labels == expected_labels, f"{case_id}: label mismatch"

        for idx, expected in enumerate(expected_segments):
            if idx >= len(actual_classes):
                break
            actual = actual_classes[idx]
            expected_command = expected.get("command")
            if expected_command is not None:
                assert actual.command == str(expected_command), f"{case_id}: command mismatch at segment {idx}"
            expected_product_type = expected.get("product_type")
            if expected_product_type is not None:
                assert actual.product_type == str(expected_product_type), f"{case_id}: product_type mismatch at segment {idx}"

        min_segment_count = case.get("min_segment_count")
        if min_segment_count is not None:
            assert len(actual_segments) >= int(min_segment_count), f"{case_id}: too few segments"
        max_segment_count = case.get("max_segment_count")
        if max_segment_count is not None:
            assert len(actual_segments) <= int(max_segment_count), f"{case_id}: too many segments"
