from __future__ import annotations

import json
import os
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
    for raw in _cases_path().read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def test_segmentation_classification_snapshot_dump(prompt_segmenter_factory) -> None:  # noqa: ANN001
    if os.getenv("WRITE_ASSISTANT_SEGMENTATION_SNAPSHOT", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("snapshot dump is disabled; set WRITE_ASSISTANT_SEGMENTATION_SNAPSHOT=1")

    segmenter = prompt_segmenter_factory()
    classifier = PromptClassifier()
    router = HybridCommandRouter(enabled=True)
    rows: list[dict] = []
    for case in _load_cases():
        prompt = str(case.get("prompt", ""))
        segments = segmenter.segment(prompt)
        classes = classifier.classify(segments=segments, scenario_id="scn_test", router=router)
        rows.append(
            {
                "id": case.get("id"),
                "suite": case.get("suite"),
                "prompt": prompt,
                "segments": [
                    {
                        "text": seg.text,
                        "start_char": seg.start_char,
                        "end_char": seg.end_char,
                        "confidence": seg.segmentation_confidence,
                    }
                    for seg in segments
                ],
                "labels": [item.segment_class for item in classes],
            }
        )

    out_path = _cases_path().with_name("snapshot_results.json")
    out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=True), encoding="utf-8")
    assert out_path.exists()
