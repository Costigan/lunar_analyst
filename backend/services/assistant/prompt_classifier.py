from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import re
from typing import Any

from backend.services.assistant.canonical_recipe_catalog import recipe_ids_for_product_type
from backend.services.assistant.command_router import HybridCommandRouter
from backend.services.assistant.prompt_segmenter import PromptSegment
from backend.services.assistant.product_type_dictionary import PRODUCT_TYPE_DICT, default_filenames_for_product_type


_PRODUCT_HEAD_VOCAB: set[str] = {
    "raster",
    "map",
    "mask",
    "hillshade",
    "slope",
    "aspect",
    "tpi",
    "roughness",
    "ruggedness",
    "illumination",
    "visibility",
    "shadow",
    "psr",
}

_CREATE_INTENT_TOKENS: tuple[str, ...] = (
    "create",
    "generate",
    "make",
    "build",
    "produce",
    "derive",
    "compute",
    "calculate",
    "need",
    "needs",
    "want",
    "wants",
)

_NON_CREATE_LEAD_WORDS: set[str] = {
    "write",
    "run",
    "output",
    "show",
    "hide",
    "turn",
    "open",
    "list",
    "call",
    "explain",
    "compare",
    "if",
    "what",
    "why",
    "how",
    "when",
    "where",
    "should",
    "can",
    "could",
    "would",
    "do",
    "does",
    "is",
    "are",
    "was",
    "were",
}


@dataclass(frozen=True)
class SegmentOffsets:
    start: int
    stop: int


@dataclass(frozen=True)
class SegmentArgument:
    name: str
    value: Any


@dataclass(frozen=True)
class SegmentClassification:
    segment_id: str
    text: str
    offsets: SegmentOffsets
    segment_class: str
    confidence: float
    classification_origin: str
    command: str | None = None
    args: list[SegmentArgument] = field(default_factory=list)
    pixel_type: str | None = None
    semantics: str | None = None
    sources: list[str] = field(default_factory=list)
    product_type: str | None = None
    intent_family: str | None = None
    intent_properties: dict[str, Any] = field(default_factory=dict)
    matched_action_ids: list[str] = field(default_factory=list)
    missing_required_slots: list[str] = field(default_factory=list)
    blocking_reason_code: str | None = None
    requires_clarification: bool = False
    validation_status: str = "validated"
    downgrade_reason: str | None = None
    candidate_product_types: list[str] = field(default_factory=list)


class PromptClassifier:
    def __init__(self, *, extractor: Any | None = None) -> None:
        _ = extractor

    def classify(
        self,
        *,
        segments: list[PromptSegment],
        scenario_id: str | None,
        router: HybridCommandRouter,
        constraints_text: str | None = None,
        known_products: list[str] | None = None,
        deterministic_command_classification_enabled: bool = True,
    ) -> list[SegmentClassification]:
        results: list[SegmentClassification] = []
        for segment in segments:
            planned = self._plan_segment(router=router, text=segment.text, scenario_id=scenario_id)
            if planned is not None and deterministic_command_classification_enabled:
                args = [
                    SegmentArgument(name=str(key), value=value)
                    for key, value in sorted(planned.slots.items())
                    if str(key) != "segment"
                ]
                results.append(
                    SegmentClassification(
                        segment_id=segment.segment_id,
                        text=segment.text,
                        offsets=SegmentOffsets(start=segment.start_char, stop=segment.end_char),
                        segment_class="command",
                        confidence=max(0.5, min(0.99, segment.segmentation_confidence + 0.08)),
                        classification_origin="deterministic_command",
                        command=planned.action_id,
                        args=args,
                        matched_action_ids=[planned.action_id],
                    )
                )
                continue
            if planned is not None and not deterministic_command_classification_enabled:
                results.append(
                    SegmentClassification(
                        segment_id=segment.segment_id,
                        text=segment.text,
                        offsets=SegmentOffsets(start=segment.start_char, stop=segment.end_char),
                        segment_class="other",
                        confidence=max(0.5, min(0.99, segment.segmentation_confidence + 0.05)),
                        classification_origin="deterministic_command_candidate",
                    )
                )
                continue

            extraction = self._extract_non_command(
                segment=segment,
                constraints_text=constraints_text,
                scenario_id=scenario_id,
                known_products=known_products or [],
            )
            results.append(extraction)
        return results

    def _extract_non_command(
        self,
        *,
        segment: PromptSegment,
        constraints_text: str | None,
        scenario_id: str | None,
        known_products: list[str],
    ) -> SegmentClassification:
        _ = constraints_text
        _ = scenario_id
        _ = known_products
        candidate_product_types = _candidate_product_types_for_text(segment.text)
        noun_phrase_match = self._noun_phrase_create_product(segment.text)
        if noun_phrase_match is not None:
            return SegmentClassification(
                segment_id=segment.segment_id,
                text=segment.text,
                offsets=SegmentOffsets(start=segment.start_char, stop=segment.end_char),
                segment_class="create_product",
                confidence=min(0.82, max(0.57, segment.segmentation_confidence)),
                classification_origin="deterministic_noun_phrase_product_match",
                pixel_type=noun_phrase_match["pixel_type"],
                semantics=noun_phrase_match["semantics"],
                sources=list(noun_phrase_match["sources"]),
                product_type=noun_phrase_match["product_type"],
                intent_properties=dict(noun_phrase_match.get("intent_properties") or {}),
                candidate_product_types=list(candidate_product_types),
            )

        heuristic = self._heuristic_create_product(segment.text)
        if heuristic is not None:
            return SegmentClassification(
                segment_id=segment.segment_id,
                text=segment.text,
                offsets=SegmentOffsets(start=segment.start_char, stop=segment.end_char),
                segment_class="create_product",
                confidence=min(0.8, max(0.55, segment.segmentation_confidence)),
                classification_origin="heuristic_create_product",
                pixel_type=heuristic["pixel_type"],
                semantics=heuristic["semantics"],
                sources=list(heuristic["sources"]),
                product_type=heuristic["product_type"],
                intent_properties=dict(heuristic.get("intent_properties") or {}),
                candidate_product_types=list(candidate_product_types),
            )
        return SegmentClassification(
            segment_id=segment.segment_id,
            text=segment.text,
            offsets=SegmentOffsets(start=segment.start_char, stop=segment.end_char),
            segment_class="other",
            confidence=min(0.79, segment.segmentation_confidence),
            classification_origin="fallback_other",
            candidate_product_types=list(candidate_product_types),
        )

    @staticmethod
    def _heuristic_create_product(text: str) -> dict[str, Any] | None:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return None
        if not any(lowered.startswith(prefix) for prefix in ("create ", "generate ", "make ", "build ")):
            return None
        heuristics: list[tuple[str, tuple[str, ...], str]] = [
            ("threshold_mask", ("threshold mask", "mask where", "mask of"), "Boolean mask identifying pixels that satisfy a threshold comparison."),
            ("combined_mask", ("combined mask",), "Boolean mask generated by combining multiple other masks."),
            ("hillshade_raster", ("hillshade",), "Shaded relief image derived from terrain elevation to visualize topographic variation."),
            ("slope_raster", ("slope raster", "slope map", "slope"), "Slope in degrees at each pixel."),
            ("aspect_raster", ("aspect",), "Downhill direction in degrees at each pixel."),
            ("ruggedness_raster", ("ruggedness",), "Elevation difference between central and surrounding pixels."),
            ("tpi_raster", ("tpi", "topographic position index"), "Topographic position index at each pixel."),
            ("roughness_raster", ("roughness",), "Maximum elevation difference between each pixel and its neighbors."),
            ("illumination_raster", ("illumination",), "Fraction of full sunlight at the requested time for each pixel."),
            ("earth_visibility_raster", ("earth visibility",), "Boolean raster indicating whether Earth is visible at each pixel."),
            ("psr_raster", ("permanent shadow", "psr"), "Boolean raster indicating permanent shadow."),
        ]
        for product_type, phrases, semantics in heuristics:
            if any(phrase in lowered for phrase in phrases):
                spec = PRODUCT_TYPE_DICT[product_type]
                sources = _extract_sources(lowered=lowered, product_type=product_type)
                return {
                    "product_type": product_type,
                    "pixel_type": spec.default_pixel_type,
                    "semantics": semantics,
                    "sources": sources,
                    "intent_properties": {"operation": "create", "product_type": product_type},
                }
        return None

    @staticmethod
    def _noun_phrase_create_product(text: str) -> dict[str, Any] | None:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return None
        if not _has_create_intent_signal(lowered):
            return None
        normalized = _normalize_for_product_match(lowered)
        if not normalized:
            return None
        alias_index = _recipe_backed_product_alias_index()
        if not alias_index:
            return None
        candidates = _noun_phrase_candidates(normalized)
        matched_types = {alias_index[candidate] for candidate in candidates if candidate in alias_index}
        if len(matched_types) != 1:
            return None
        product_type = next(iter(matched_types))
        spec = PRODUCT_TYPE_DICT.get(product_type)
        if spec is None:
            return None
        return {
            "product_type": product_type,
            "pixel_type": spec.default_pixel_type,
            "semantics": spec.description,
            "sources": _extract_sources(lowered=lowered, product_type=product_type),
            "intent_properties": {"operation": "create", "product_type": product_type},
        }

    @staticmethod
    def _plan_segment(
        *,
        router: HybridCommandRouter,
        text: str,
        scenario_id: str | None,
    ) -> Any | None:
        # Router exposes only plan(prompt=...), but segment-level classification needs
        # direct action matching. Use private helper if available; otherwise fallback.
        planner = getattr(router, "_plan_segment", None)
        if callable(planner):
            return planner(segment=text, scenario_id=scenario_id)
        command_plan = router.plan(prompt=text, scenario_id=scenario_id)
        if command_plan.actions:
            return command_plan.actions[0]
        return None


def _has_recipe(product_type: str) -> bool:
    return bool(recipe_ids_for_product_type(product_type))


def _has_create_intent_signal(lowered: str) -> bool:
    text = str(lowered or "").strip().lower()
    if not text:
        return False
    if "script" in text and ("write" in text or "run" in text):
        return False
    if "`" in text and "call " in text:
        return False
    if re.search(r"\bcall\s+`?[a-z0-9_]+\.[a-z0-9_]+`?\b", text):
        return False
    if re.search(r"\bcall\b.*\bwith\s*\{", text):
        return False
    if "?" in text:
        explicit_create_question = re.search(r"\b(create|generate|make|build|produce|derive|compute|calculate)\b", text)
        if not explicit_create_question:
            return False
    first_word = text.split(maxsplit=1)[0] if text else ""
    if first_word in _NON_CREATE_LEAD_WORDS and first_word not in {"create", "generate", "make", "build"}:
        return False
    create_pattern = r"\b(" + "|".join(re.escape(item) for item in _CREATE_INTENT_TOKENS) + r")\b"
    if re.search(create_pattern, text):
        return True
    if re.search(r"\buse\b.+\b(from|using)\b.+\bdem\b", text):
        return True
    return False


def _normalize_for_product_match(text: str) -> str:
    value = str(text or "").lower()
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _extract_sources(*, lowered: str, product_type: str) -> list[str]:
    sources: list[str] = []
    if "primary dem" in lowered:
        sources.append("primary_dem")
    elif "dem" in lowered and product_type != "dem":
        sources.append("dem")
    elif "slope" in lowered and product_type == "threshold_mask":
        sources.append("slope_raster")
    return sources


def _noun_phrase_candidates(normalized: str) -> set[str]:
    tokens = [token for token in normalized.split() if token]
    candidates: set[str] = set()
    for token in tokens:
        if token in _PRODUCT_HEAD_VOCAB:
            candidates.add(token)
    max_size = min(5, len(tokens))
    for size in range(1, max_size + 1):
        for start in range(0, len(tokens) - size + 1):
            phrase_tokens = tokens[start : start + size]
            head = phrase_tokens[-1]
            if head not in _PRODUCT_HEAD_VOCAB and not any(item in _PRODUCT_HEAD_VOCAB for item in phrase_tokens):
                continue
            phrase = " ".join(phrase_tokens).strip()
            if phrase:
                candidates.add(phrase)
    return candidates


@lru_cache(maxsize=1)
def _recipe_backed_product_alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for product_type, spec in PRODUCT_TYPE_DICT.items():
        if not _has_recipe(product_type):
            continue
        aliases: set[str] = set()
        aliases.add(str(product_type).replace("_", " "))
        aliases.update(str(item) for item in spec.noun_phrase_aliases if str(item).strip())
        for filename in default_filenames_for_product_type(product_type):
            stem = str(filename).rsplit(".", 1)[0]
            if stem:
                aliases.add(stem.replace("_", " "))
        for alias in aliases:
            normalized = _normalize_for_product_match(alias)
            if not normalized:
                continue
            existing = index.get(normalized)
            if existing is None:
                index[normalized] = product_type
                continue
            if existing != product_type:
                # Ambiguous aliases are intentionally excluded from deterministic routing.
                index.pop(normalized, None)
    return index


@lru_cache(maxsize=1)
def _all_product_alias_index() -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for product_type, spec in PRODUCT_TYPE_DICT.items():
        aliases: set[str] = set()
        aliases.add(str(product_type).replace("_", " "))
        aliases.update(str(item) for item in spec.noun_phrase_aliases if str(item).strip())
        for filename in default_filenames_for_product_type(product_type):
            stem = str(filename).rsplit(".", 1)[0]
            if stem:
                aliases.add(stem.replace("_", " "))
        for alias in aliases:
            normalized = _normalize_for_product_match(alias)
            if not normalized:
                continue
            index.setdefault(normalized, set()).add(product_type)
    return index


def _candidate_product_types_for_text(text: str) -> list[str]:
    normalized = _normalize_for_product_match(str(text or ""))
    if not normalized:
        return []
    alias_index = _all_product_alias_index()
    if not alias_index:
        return []
    candidates = _noun_phrase_candidates(normalized)
    matched: set[str] = set()
    for candidate in candidates:
        for product_type in alias_index.get(candidate, set()):
            matched.add(product_type)
    return sorted(matched)
