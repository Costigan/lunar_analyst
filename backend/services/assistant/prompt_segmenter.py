from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

_COMPLEXITY_RE = re.compile(
    r"\b(if|when|unless|only if|except|while|compare|tradeoff|best|optimi[sz]e)\b",
    re.IGNORECASE,
)
_CONNECTOR_RE = re.compile(r"\b(and then|then|also|next|after that)\b", re.IGNORECASE)
_CLAUSE_START_WORDS = {
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
    "explain",
    "suggest",
    "recommend",
    "compare",
    "compute",
    "calculate",
    "save",
    "export",
    "open",
    "apply",
}
_IMPERATIVE_PREFIXES = (
    "turn on ",
    "turn off ",
    "show ",
    "hide ",
    "set ",
    "switch ",
    "change ",
    "use ",
    "list ",
    "run ",
    "launch ",
    "cancel ",
    "get ",
    "import ",
    "move ",
    "describe ",
    "write ",
    "create ",
    "save ",
    "export ",
    "open ",
    "apply ",
)

_IMPERATIVE_LEAD_WORDS = {
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
    "compute",
    "calculate",
    "save",
    "export",
    "open",
    "apply",
}


@dataclass(frozen=True)
class PromptSegment:
    segment_id: str
    text: str
    start_char: int
    end_char: int
    is_imperative_candidate: bool
    has_complexity_guard: bool
    segmentation_confidence: float


class PromptSegmenter:
    def __init__(self, *, model_name: str = "en_core_web_sm") -> None:
        self._model_name = str(model_name).strip() or "en_core_web_sm"
        self._nlp = self._load_spacy_model(self._model_name)

    @staticmethod
    def _load_spacy_model(model_name: str) -> Any:
        try:
            import spacy  # type: ignore
        except Exception as exc:
            raise RuntimeError("Prompt segmentation requires spaCy to be installed.") from exc
        try:
            return spacy.load(model_name, disable=["ner"])
        except Exception as exc:
            raise RuntimeError(
                f"Prompt segmentation requires the spaCy model '{model_name}' to be installed and loadable."
            ) from exc

    def segment(self, prompt: str) -> list[PromptSegment]:
        text = str(prompt or "")
        if not text.strip():
            return []
        spans = self._sentence_spans(text)
        pieces: list[tuple[int, int, str, float]] = []
        for start, end in spans:
            sentence = text[start:end].strip()
            if not sentence:
                continue
            sentence_start = text.find(sentence, start, end + len(sentence))
            if sentence_start < 0:
                sentence_start = start
            sentence_end = sentence_start + len(sentence)
            split = self._split_sentence(text=text, start=sentence_start, end=sentence_end)
            if split:
                pieces.extend(split)
            else:
                pieces.append((sentence_start, sentence_end, sentence, 0.8))

        merged = self._merge_small_fragments(pieces)
        segments: list[PromptSegment] = []
        for idx, (start, end, seg_text, confidence) in enumerate(merged, start=1):
            cleaned = seg_text.strip()
            if not cleaned:
                continue
            has_complexity = bool(_COMPLEXITY_RE.search(cleaned))
            lower = cleaned.lower()
            first_word = lower.split(maxsplit=1)[0] if lower else ""
            is_imperative = first_word in _IMPERATIVE_LEAD_WORDS
            if has_complexity:
                confidence = min(confidence, 0.65)
            segments.append(
                PromptSegment(
                    segment_id=f"s{idx}",
                    text=cleaned,
                    start_char=int(start),
                    end_char=int(end),
                    is_imperative_candidate=bool(is_imperative),
                    has_complexity_guard=has_complexity,
                    segmentation_confidence=max(0.0, min(1.0, float(confidence))),
                )
            )
        return segments

    def _sentence_spans(self, text: str) -> list[tuple[int, int]]:
        doc = self._nlp(text)
        spans: list[tuple[int, int]] = []
        for sent in getattr(doc, "sents", []):
            start = int(getattr(sent, "start_char", 0))
            end = int(getattr(sent, "end_char", 0))
            if end > start:
                spans.append((start, end))
        if spans:
            return spans
        raise RuntimeError("Prompt segmentation failed because spaCy did not produce sentence boundaries.")

    def _split_sentence(self, *, text: str, start: int, end: int) -> list[tuple[int, int, str, float]]:
        sentence = text[start:end]
        if _COMPLEXITY_RE.search(sentence):
            return [(start, end, sentence.strip(), 0.65)]
        imperative_split = self._split_imperative_coordination(sentence=sentence, start=start, end=end)
        if imperative_split is not None:
            return imperative_split
        parts: list[tuple[int, int, str, float]] = []
        cursor = 0
        for match in _CONNECTOR_RE.finditer(sentence):
            following = sentence[match.end() :].lstrip(" ,")
            next_word_match = re.match(r"[A-Za-z]+", following)
            if not next_word_match:
                continue
            next_word = next_word_match.group(0).lower()
            if next_word not in _CLAUSE_START_WORDS:
                continue
            split_at = match.start()
            left = sentence[cursor:split_at].strip(" ,")
            if left:
                left_start = start + cursor + sentence[cursor:].find(left)
                left_end = left_start + len(left)
                parts.append((left_start, left_end, left, 0.88))
            cursor = match.end()
        tail = sentence[cursor:].strip(" ,")
        if tail:
            tail_start = start + cursor + sentence[cursor:].find(tail)
            tail_end = tail_start + len(tail)
            parts.append((tail_start, tail_end, tail, 0.88))
        if len(parts) <= 1:
            return [(start, end, sentence.strip(), 0.82)]
        return parts

    def _split_imperative_coordination(
        self,
        *,
        sentence: str,
        start: int,
        end: int,
    ) -> list[tuple[int, int, str, float]] | None:
        raw = sentence.strip()
        lowered = raw.lower().rstrip(".!?")
        matched_prefix = ""
        for prefix in _IMPERATIVE_PREFIXES:
            if lowered.startswith(prefix):
                matched_prefix = prefix
                break
        if not matched_prefix:
            return None
        if " and " not in lowered:
            return None
        tail_original = raw[len(matched_prefix) :]
        tail_clean = tail_original.strip().rstrip(".!?")
        if not tail_clean:
            return None
        parts = [item.strip() for item in tail_clean.split(" and ") if item.strip()]
        if len(parts) < 2:
            return None
        for part in parts[1:]:
            if any(token in part.lower().split() for token in ("if", "when", "unless", "because", "while", "then")):
                return None

        rebuilt: list[str] = []
        for index, part in enumerate(parts):
            if index == 0:
                rebuilt.append(f"{matched_prefix}{part}".strip())
                continue
            part_first_word = part.lower().split(maxsplit=1)[0] if part else ""
            if part_first_word in _IMPERATIVE_LEAD_WORDS:
                rebuilt.append(part.strip())
            else:
                rebuilt.append(f"{matched_prefix}{part}".strip())
        spans: list[tuple[int, int, str, float]] = []
        cursor = start
        for text_part in rebuilt:
            piece_start = cursor
            piece_end = piece_start + len(text_part)
            spans.append((piece_start, piece_end, text_part, 0.9))
            cursor = piece_end + 1
        return spans if spans else None

    @staticmethod
    def _merge_small_fragments(parts: list[tuple[int, int, str, float]]) -> list[tuple[int, int, str, float]]:
        if not parts:
            return []
        merged: list[tuple[int, int, str, float]] = []
        for start, end, text, confidence in parts:
            trimmed = text.strip()
            if not trimmed:
                continue
            if merged and len(trimmed) < 8:
                prev_start, prev_end, prev_text, prev_conf = merged[-1]
                merged[-1] = (prev_start, end, f"{prev_text} {trimmed}".strip(), min(prev_conf, confidence))
                continue
            merged.append((start, end, trimmed, confidence))
        return merged
