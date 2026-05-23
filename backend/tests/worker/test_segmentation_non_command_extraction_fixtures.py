from __future__ import annotations

import json
from pathlib import Path

from backend.services.assistant.command_router import HybridCommandRouter
from backend.services.assistant.prompt_classifier import PromptClassifier
from backend.services.assistant.prompt_segmenter import PromptSegment


def _cases_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "assistant_segmentation_classification"
        / "non_command_extraction_cases.jsonl"
    )


def _load_cases() -> list[dict]:
    rows: list[dict] = []
    for raw in _cases_path().read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def test_non_command_extraction_fixtures_offline_heuristics() -> None:
    classifier = PromptClassifier(extractor=None)
    router = HybridCommandRouter(enabled=True)

    for case in _load_cases():
        case_id = str(case.get("id", "unknown"))
        text = str(case.get("segment_text", ""))
        offsets = dict(case.get("offsets", {}))
        start = int(offsets.get("start", 0))
        stop = int(offsets.get("stop", len(text)))

        segments = [
            PromptSegment(
                segment_id="s1",
                text=text,
                start_char=start,
                end_char=stop,
                is_imperative_candidate=True,
                has_complexity_guard=False,
                segmentation_confidence=0.8,
            )
        ]
        classifications = classifier.classify(
            segments=segments,
            scenario_id="scn_test",
            router=router,
        )
        assert len(classifications) == 1
        cls = classifications[0]

        expected = dict(case.get("expected", {}))
        assert cls.segment_class == str(expected.get("class", "")), f"{case_id}: class mismatch"
        if "product_type" in expected:
            assert cls.product_type == str(expected.get("product_type")), f"{case_id}: product_type mismatch"
        if "pixel_type" in expected:
            assert cls.pixel_type == str(expected.get("pixel_type")), f"{case_id}: pixel_type mismatch"
