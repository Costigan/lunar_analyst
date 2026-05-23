from __future__ import annotations

import pytest

from backend.services.assistant.prompt_segmenter import PromptSegmenter


def test_segmenter_splits_multi_sentence_mixed_prompt(prompt_segmenter_factory) -> None:  # noqa: ANN001
    segmenter = prompt_segmenter_factory()
    prompt = "Switch to Shackleton scenario. Turn on slope and hillshade. Then explain tradeoffs."
    segments = segmenter.segment(prompt)

    assert len(segments) >= 3
    assert segments[0].text.lower().startswith("switch to shackleton")
    assert "explain tradeoffs" in segments[-1].text.lower()
    assert segments[0].start_char < segments[1].start_char
    assert segments[1].end_char <= segments[-1].end_char


def test_segmenter_marks_complexity_guard_for_conditional_clause(prompt_segmenter_factory) -> None:  # noqa: ANN001
    segmenter = prompt_segmenter_factory()
    segments = segmenter.segment("Show slope only if sun elevation is above 10 degrees.")

    assert len(segments) >= 1
    assert any(item.has_complexity_guard for item in segments)


def test_segmenter_emits_reasonable_confidence_and_offsets(prompt_segmenter_factory) -> None:  # noqa: ANN001
    segmenter = prompt_segmenter_factory()
    segments = segmenter.segment("List visible layers.")

    assert len(segments) == 1
    item = segments[0]
    assert item.start_char == 0
    assert item.end_char >= len("List visible layers")
    assert 0.0 <= item.segmentation_confidence <= 1.0


def test_segmenter_does_not_emit_verb_less_tail_for_and_coordination(prompt_segmenter_factory) -> None:  # noqa: ANN001
    segmenter = prompt_segmenter_factory()
    segments = segmenter.segment("Turn on slope and hillshade.")

    texts = [item.text.strip().lower().rstrip(".") for item in segments]
    assert "hillshade" not in texts
    assert all(text.split()[0] in {"turn", "show", "hide", "set", "switch", "change", "use", "list", "run", "launch", "cancel", "get", "import", "move", "describe", "write", "create", "apply"} for text in texts)


def test_segmenter_propagates_verb_for_split_coordination(prompt_segmenter_factory) -> None:  # noqa: ANN001
    segmenter = prompt_segmenter_factory()
    segments = segmenter.segment("Turn on slope and hillshade.")
    texts = [item.text.strip().lower().rstrip(".") for item in segments]

    # Accept either unsplit or propagated split; both are semantically valid.
    if len(texts) == 1:
        assert texts[0] == "turn on slope and hillshade"
    else:
        assert "turn on slope" in texts
        assert "turn on hillshade" in texts


def test_segmenter_does_not_propagate_create_to_save_clause(prompt_segmenter_factory) -> None:  # noqa: ANN001
    segmenter = prompt_segmenter_factory()
    segments = segmenter.segment("Create a slope mask at <= 5 degrees and save it as landing_sites.tif")
    texts = [item.text.strip().lower().rstrip(".") for item in segments]

    assert "create save it as landing_sites.tif" not in texts
    assert "create a slope mask at <= 5 degrees" in texts
    assert "save it as landing_sites.tif" in texts


def test_segmenter_marks_save_clause_as_imperative(prompt_segmenter_factory) -> None:  # noqa: ANN001
    segmenter = prompt_segmenter_factory()
    segments = segmenter.segment("Save it as landing_sites.tif")

    assert len(segments) == 1
    assert segments[0].text.lower().startswith("save ")
    assert segments[0].is_imperative_candidate is True


def test_segmenter_requires_spacy_model() -> None:
    def _raise(_: str):  # noqa: ANN001
        raise RuntimeError("missing spacy model")

    original = PromptSegmenter._load_spacy_model
    PromptSegmenter._load_spacy_model = staticmethod(_raise)
    try:
        with pytest.raises(RuntimeError, match="missing spacy model"):
            PromptSegmenter()
    finally:
        PromptSegmenter._load_spacy_model = staticmethod(original)
