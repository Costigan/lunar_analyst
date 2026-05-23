from __future__ import annotations

import json
from pathlib import Path

from backend.services.assistant.command_router import HybridCommandRouter
from backend.services.assistant.prompt_classifier import PromptClassifier
from backend.services.assistant.prompt_segmenter import PromptSegmenter

_VERB_STARTS = {
    "then",
    "also",
    "next",
    "after",
    "turn",
    "show",
    "hide",
    "set",
    "switch",
    "change",
    "use",
    "list",
    "run",
    "launch",
    "cancel",
    "get",
    "import",
    "move",
    "describe",
    "write",
    "create",
    "apply",
    "explain",
    "suggest",
    "recommend",
}


def _cases_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "assistant_segmentation_classification"
        / "golden_cases_v2.jsonl"
    )


def _load_cases() -> list[dict]:
    rows: list[dict] = []
    for raw in _cases_path().read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def test_segmentation_classification_invariants(prompt_segmenter_factory) -> None:  # noqa: ANN001
    segmenter = prompt_segmenter_factory()
    classifier = PromptClassifier()
    router = HybridCommandRouter(enabled=True)

    for case in _load_cases():
        prompt = str(case.get("prompt", ""))
        case_id = str(case.get("id", "unknown"))
        expectations = dict(case.get("expectations", {}))
        allow_offset_tolerance = bool(expectations.get("allow_offset_tolerance", False))
        segments = segmenter.segment(prompt)
        classes = classifier.classify(segments=segments, scenario_id="scn_test", router=router)

        assert len(segments) == len(classes), f"{case_id}: segment/classification length mismatch"
        last_end = -1
        for item in segments:
            assert item.text.strip(), f"{case_id}: empty segment text"
            assert 0.0 <= item.segmentation_confidence <= 1.0, f"{case_id}: invalid confidence"
            if not allow_offset_tolerance:
                assert item.start_char >= last_end, f"{case_id}: non-monotonic offsets"
            assert item.end_char >= item.start_char, f"{case_id}: invalid offset range"
            last_end = item.end_char

        if bool(expectations.get("forbid_bare_noun_segments", False)):
            for item in segments:
                first = item.text.strip().split()[0].strip(".,!?").lower() if item.text.strip() else ""
                assert first in _VERB_STARTS, f"{case_id}: verb-less segment emitted: {item.text!r}"

        for cls in classes:
            assert cls.segment_class in {"command", "create_product", "intent_family", "other"}, (
                f"{case_id}: invalid label {cls.segment_class}"
            )
