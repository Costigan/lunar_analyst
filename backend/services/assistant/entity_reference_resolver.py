from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
import re
from typing import Any

from backend.services.assistant.prompt_classifier import SegmentClassification
from backend.services.assistant.scenario_ref_normalization import normalize_scenario_reference
from backend.services.assistant.verb_normalizer import VerbNormalizationResult, VerbNormalizer
from backend.services.assistant.canonical_recipe_catalog import recipe_ids_for_product_type
from backend.core.config import load_app_config, resolve_config_path
from backend.services.colormap_support import resolve_colormap_registry
from backend.services.nomenclature_service import NomenclatureService, clean_name
from backend.services.assistant.nomenclature_variants import resolve_feature_with_variants

_PRONOUN_RE = re.compile(r"\b(it|that|there)\b", re.IGNORECASE)
# Personal pronouns that should never be treated as geographic entity candidates.
_PERSONAL_PRONOUNS: frozenset[str] = frozenset({
    "i", "me", "my", "mine", "myself",
    "you", "your", "yours", "yourself",
    "he", "him", "his", "himself",
    "she", "her", "hers", "herself",
    "it", "its", "itself",
    "we", "us", "our", "ours", "ourselves",
    "they", "them", "their", "theirs", "themselves",
    "this", "that", "these", "those",
})
_PRODUCT_ID_RE = re.compile(r"\b(prod_[a-z0-9_\-]+)\b", re.IGNORECASE)
_JOB_ID_RE = re.compile(r"\b(job_[a-z0-9_\-]+)\b", re.IGNORECASE)
_TOOL_NAME_RE = re.compile(r"\b([a-z][a-z0-9_]+\.[a-z][a-z0-9_]+)\b")
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_COORD_RE = re.compile(r"\b(?:x\s*[:=]\s*(-?\d+(?:\.\d+)?)[,\s]+y\s*[:=]\s*(-?\d+(?:\.\d+)?))\b", re.IGNORECASE)
_FEATURE_TYPE_WORDS = (
    r"Crater|Mons|Mare|Massif|Rima|Rupes|Vallis|Dorsum|Planitia|Palus|Lacus|Promontorium|Montes|Patera|Sinus|Catena"
)
# Matches IAU-style TYPE-before-NAME: "Mons Malapert", "Crater Dawa"
_FEATURE_TYPE_PATTERN = re.compile(
    rf"\b({_FEATURE_TYPE_WORDS})"
    r"\s+([A-Za-z][A-Za-z0-9'._-]*(?:\s+(?!and\b|to\b|with\b|for\b|from\b|on\b|in\b|at\b)[A-Za-z][A-Za-z0-9'._-]*){0,3})\b",
    re.IGNORECASE,
)
# Matches colloquial NAME-before-TYPE: "Dawa Crater", "Malapert Mons"
# NAME tokens must start with an uppercase letter to avoid matching verb phrases.
_FEATURE_NAME_TYPE_PATTERN = re.compile(
    rf"\b([A-Z][A-Za-z0-9'._-]+(?:\s+(?!(?:{_FEATURE_TYPE_WORDS})\b)[A-Z][A-Za-z0-9'._-]+){{0,2}})"
    rf"\s+({_FEATURE_TYPE_WORDS})\b",
)


def _norm(value: str | None) -> str:
    text = str(value or "").strip()
    return text


def _score_name_match(*, query: str, candidate: str) -> float:
    q = clean_name(query)
    c = clean_name(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if c.startswith(q):
        return 0.92
    if q in c:
        return 0.86
    return 0.0


def _extract_file_token(text: str) -> str | None:
    match = re.search(r"\b([A-Za-z0-9_.\-/]+?\.(?:tif|tiff|json|geojson|csv|py|md|txt))\b", text, re.IGNORECASE)
    if match is None:
        return None
    return str(match.group(1)).strip()


@dataclass(frozen=True)
class EntityCandidate:
    kind: str
    resolved_id: str
    label: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "resolved_id": self.resolved_id,
            "label": self.label,
            "score": round(float(self.score), 4),
        }


@dataclass(frozen=True)
class EntityMention:
    kind: str
    mention_text: str
    normalized_ref: str
    strategy: str
    resolved_id: str | None
    confidence: float
    reason_code: str
    candidates: list[EntityCandidate] = field(default_factory=list)
    dep_role: str | None = None
    dep_head: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "mention_text": self.mention_text,
            "normalized_ref": self.normalized_ref,
            "strategy": self.strategy,
            "resolved_id": self.resolved_id,
            "confidence": round(float(self.confidence), 4),
            "reason_code": self.reason_code,
            "candidates": [item.as_dict() for item in self.candidates],
            "dep_role": self.dep_role,
            "dep_head": self.dep_head,
        }


@dataclass(frozen=True)
class SegmentEntityResolution:
    segment_id: str
    canonical_operation: str | None
    verb_normalization: VerbNormalizationResult
    direct_object_candidate: str | None = None
    target_kind: str | None = None
    target_mention: str | None = None
    target_resolved_id: str | None = None
    mentions: list[EntityMention] = field(default_factory=list)
    ambiguities: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        resolved_by_kind: dict[str, int] = {}
        for item in self.mentions:
            kind = str(item.kind).strip()
            if not kind:
                continue
            by_kind[kind] = by_kind.get(kind, 0) + 1
            if str(item.resolved_id or "").strip():
                resolved_by_kind[kind] = resolved_by_kind.get(kind, 0) + 1
        return {
            "segment_id": self.segment_id,
            "canonical_operation": self.canonical_operation,
            "verb_normalization": self.verb_normalization.as_dict(),
            "direct_object_candidate": self.direct_object_candidate,
            "target_kind": self.target_kind,
            "target_mention": self.target_mention,
            "target_resolved_id": self.target_resolved_id,
            "mentions": [item.as_dict() for item in self.mentions],
            "ambiguities": list(self.ambiguities),
            "errors": list(self.errors),
            "resolved_entity_summary": {
                "mention_count": len(self.mentions),
                "resolved_count": len([item for item in self.mentions if str(item.resolved_id or "").strip()]),
                "ambiguity_count": len(self.ambiguities),
                "by_kind": by_kind,
                "resolved_by_kind": resolved_by_kind,
            },
        }


class EntityReferenceResolver:
    def __init__(
        self,
        *,
        tool_services: Any,
        scenario_directory_resolver: Any,
        verb_normalizer: VerbNormalizer | None = None,
    ) -> None:
        self._services = tool_services
        self._scenario_directory_resolver = scenario_directory_resolver
        self._verb_normalizer = verb_normalizer or VerbNormalizer()
        self._tool_names_cache: set[str] | None = None
        self._pos_nlp = self._load_pos_model()
        workspace_root = Path(str(getattr(tool_services.stores, "workspace_root", "")).strip() or ".").resolve()
        self._nomenclature = NomenclatureService(db_path=(workspace_root / "scenario_catalog.db").resolve())

    def resolve_segments(
        self,
        *,
        classifications: list[SegmentClassification],
        scenario_id: str | None,
    ) -> dict[str, SegmentEntityResolution]:
        resolved: dict[str, SegmentEntityResolution] = {}
        prior_mentions: list[EntityMention] = []
        for classification in classifications:
            item = self.resolve_segment(
                classification=classification,
                scenario_id=scenario_id,
                prior_mentions=prior_mentions,
            )
            resolved[item.segment_id] = item
            prior_mentions.extend([mention for mention in item.mentions if mention.resolved_id])
        return resolved

    @staticmethod
    def _apply_prior_mention_bindings(
        *,
        mentions: list[EntityMention],
        prior_mentions: list[EntityMention],
    ) -> list[EntityMention]:
        prior_index: dict[tuple[str, str], EntityMention] = {}
        for item in prior_mentions:
            resolved_id = str(item.resolved_id or "").strip()
            norm = str(item.normalized_ref or "").strip()
            kind = str(item.kind or "").strip()
            if not resolved_id or not norm or not kind:
                continue
            prior_index[(kind, norm)] = item

        rebound: list[EntityMention] = []
        for item in mentions:
            if str(item.resolved_id or "").strip():
                rebound.append(item)
                continue
            key = (str(item.kind or "").strip(), str(item.normalized_ref or "").strip())
            bound = prior_index.get(key)
            if bound is None:
                rebound.append(item)
                continue
            rebound.append(
                replace(
                    item,
                    resolved_id=bound.resolved_id,
                    confidence=max(float(item.confidence or 0.0), 0.88),
                    reason_code="entity_bound_from_prior_segment",
                )
            )
        return rebound

    def resolve_segment(
        self,
        *,
        classification: SegmentClassification,
        scenario_id: str | None,
        prior_mentions: list[EntityMention],
    ) -> SegmentEntityResolution:
        props = dict(classification.intent_properties or {})
        input_operation = str(props.get("operation", "")).strip() or None
        verb = self._verb_normalizer.normalize(text=classification.text, input_operation=input_operation)
        operation_candidates = [
            str(item).strip().lower()
            for item in list(verb.operation_candidates or verb.candidates or [])
            if str(item).strip()
        ]
        selected_operation = str(verb.canonical_operation or "").strip().lower() or (
            operation_candidates[0] if operation_candidates else ""
        )
        mentions: list[EntityMention] = []
        ambiguities: list[dict[str, Any]] = []
        errors: list[str] = []
        dep_meta_by_mention = self._build_dependency_metadata_map(classification.text)
        direct_object_candidate = self._direct_object_candidate_from_mentions(classification.text, dep_meta_by_mention)

        def _append(kind: str, value: str | None, *, context_filter: str | None = None) -> None:
            if not str(value or "").strip():
                return
            mention = self._resolve_reference(
                kind=kind,
                mention_text=str(value).strip(),
                scenario_id=scenario_id,
                context_filter=context_filter,
            )
            mention = self._with_dep_metadata(mention, dep_meta_by_mention=dep_meta_by_mention)
            mentions.append(mention)
            if mention.reason_code == "entity_ambiguous_requires_clarification":
                ambiguities.append(
                    {
                        "kind": kind,
                        "mention_text": str(value).strip(),
                        "reason_code": mention.reason_code,
                        "candidates": [item.as_dict() for item in mention.candidates],
                    }
                )

        if classification.segment_class == "intent_family":
            family = str(classification.intent_family or "").strip()
            if family == "location_navigation":
                _append("feature", props.get("feature_ref"), context_filter=str(props.get("context_filter", "")).strip() or None)
            elif family == "scenario_context_management":
                _append("scenario", props.get("scenario_ref"))
            elif family == "layer_visibility_update":
                target = props.get("target")
                if isinstance(target, dict):
                    _append("layer", target.get("layer_ref"))
            elif family == "layer_style_update":
                target = props.get("target")
                if isinstance(target, dict):
                    _append("layer", target.get("layer_ref"))
                style = props.get("style")
                if isinstance(style, dict):
                    _append("colormap", style.get("colormap_ref"))
            elif family == "artifact_inspection":
                target = props.get("target")
                if isinstance(target, dict):
                    _append("file", target.get("relative_path"))
        elif classification.segment_class == "command":
            args = {arg.name: arg.value for arg in classification.args}
            _append("scenario", str(args.get("scenario_ref", "")).strip() or None)
            _append("layer", str(args.get("layer_name", "")).strip() or None)
            _append("file", str(args.get("relative_path", "")).strip() or None)
            _append("file", str(args.get("source_path", "")).strip() or None)
            _append("file", str(args.get("source_relative_path", "")).strip() or None)
            _append("file", str(args.get("target_relative_path", "")).strip() or None)
        else:
            token = _extract_file_token(classification.text)
            if token:
                _append("file", token)

        if _PRONOUN_RE.search(classification.text):
            pronoun_mention = self._bind_pronoun(
                canonical_operation=selected_operation or None,
                prior_mentions=prior_mentions,
            )
            if pronoun_mention is not None:
                mentions.append(pronoun_mention)
                if pronoun_mention.reason_code == "entity_ambiguous_requires_clarification":
                    ambiguities.append(
                        {
                            "kind": pronoun_mention.kind,
                            "mention_text": pronoun_mention.mention_text,
                            "reason_code": pronoun_mention.reason_code,
                            "candidates": [item.as_dict() for item in pronoun_mention.candidates],
                        }
                    )

        # Opportunistic feature extraction even for non-intent-family segments.
        extracted_features = _extract_feature_mentions_from_text(classification.text)
        for feature_name, feature_type in extracted_features:
            full_phrase = f"{feature_type} {feature_name}".strip()
            feature_mention = self._resolve_reference(
                kind="feature",
                mention_text=full_phrase,
                scenario_id=scenario_id,
                context_filter=feature_type,
            )
            if not feature_mention.resolved_id:
                # Fallback for gazetteer rows where the canonical name omits the
                # feature-type prefix.
                feature_mention = self._resolve_reference(
                    kind="feature",
                    mention_text=feature_name,
                    scenario_id=scenario_id,
                    context_filter=feature_type,
                )
            if feature_mention.resolved_id and not any(
                item.kind == "feature" and item.resolved_id == feature_mention.resolved_id
                for item in mentions
            ):
                mentions.append(self._with_dep_metadata(feature_mention, dep_meta_by_mention=dep_meta_by_mention))

        # Always include POS-derived noun mentions so downstream paths can use
        # additional context even when no typed entity resolver matched.
        pos_mentions = self._extract_pos_mentions(classification.text)
        existing_norms = {clean_name(item.mention_text) for item in mentions}
        typed_tokens: set[str] = set()
        for item in mentions:
            if item.kind == "untyped_noun":
                continue
            tokens = [part for part in clean_name(item.mention_text).split() if part]
            typed_tokens.update(tokens)
        for noun in pos_mentions:
            norm = clean_name(noun)
            if not norm or norm in existing_norms:
                continue
            noun_tokens = [part for part in norm.split() if part]
            if noun_tokens and all(token in typed_tokens for token in noun_tokens):
                # Suppress POS-only component mentions when a resolved/typed entity
                # already subsumes that noun phrase.
                continue
            mentions.append(
                self._with_dep_metadata(
                    EntityMention(
                    kind="untyped_noun",
                    mention_text=noun,
                    normalized_ref=norm,
                    strategy="pos",
                    resolved_id=None,
                    confidence=0.5,
                    reason_code="entity_pos_tagged_untyped",
                    ),
                    dep_meta_by_mention=dep_meta_by_mention,
                )
            )
            existing_norms.add(norm)

        if set(operation_candidates) & {"show", "hide"}:
            preferred_target = direct_object_candidate or (pos_mentions[0] if pos_mentions else None)
            if preferred_target:
                for kind in ("layer", "file"):
                    candidate = self._resolve_reference(
                        kind=kind,
                        mention_text=str(preferred_target),
                        scenario_id=scenario_id,
                    )
                    candidate = self._with_dep_metadata(candidate, dep_meta_by_mention=dep_meta_by_mention)
                    if not candidate.resolved_id and candidate.reason_code != "entity_ambiguous_requires_clarification":
                        continue
                    if any(
                        existing.kind == candidate.kind
                        and existing.normalized_ref == candidate.normalized_ref
                        and str(existing.resolved_id or "") == str(candidate.resolved_id or "")
                        for existing in mentions
                    ):
                        continue
                    mentions.append(candidate)
                    if candidate.reason_code == "entity_ambiguous_requires_clarification":
                        ambiguities.append(
                            {
                                "kind": candidate.kind,
                                "mention_text": candidate.mention_text,
                                "reason_code": candidate.reason_code,
                                "candidates": [item.as_dict() for item in candidate.candidates],
                            }
                        )

        if set(operation_candidates) & {"show", "goto", "search"}:
            preferred_target = direct_object_candidate or (pos_mentions[0] if pos_mentions else None)
            if preferred_target:
                feature_candidate = self._resolve_reference(
                    kind="feature",
                    mention_text=str(preferred_target),
                    scenario_id=scenario_id,
                )
                feature_candidate = self._with_dep_metadata(feature_candidate, dep_meta_by_mention=dep_meta_by_mention)
                if feature_candidate.resolved_id and not any(
                    existing.kind == "feature" and existing.resolved_id == feature_candidate.resolved_id
                    for existing in mentions
                ):
                    mentions.append(feature_candidate)
                elif feature_candidate.reason_code == "entity_ambiguous_requires_clarification":
                    ambiguities.append(
                        {
                            "kind": "feature",
                            "mention_text": str(preferred_target).strip(),
                            "reason_code": feature_candidate.reason_code,
                            "candidates": [item.as_dict() for item in feature_candidate.candidates],
                        }
                    )

        mentions.extend(
            [
                self._with_dep_metadata(item, dep_meta_by_mention=dep_meta_by_mention)
                for item in self._resolve_expansion_entities(
                classification=classification,
                scenario_id=scenario_id,
                )
            ]
        )

        # Cleanup: remove ambiguities that are subsumed by successfully resolved mentions.
        # e.g. if "Mons Malapert" is resolved, don't let a "Malapert" ambiguity block execution.
        if ambiguities:
            resolved_norms = {item.normalized_ref for item in mentions if item.resolved_id}
            final_ambiguities: list[dict[str, Any]] = []
            for amb in ambiguities:
                norm = clean_name(str(amb.get("mention_text", "")))
                subsumed = False
                for r_norm in resolved_norms:
                    if norm == r_norm or (norm and norm in r_norm):
                        subsumed = True
                        break
                if not subsumed:
                    final_ambiguities.append(amb)
            ambiguities = final_ambiguities

        mentions = self._apply_prior_mention_bindings(
            mentions=mentions,
            prior_mentions=prior_mentions,
        )
        if ambiguities:
            resolved_norms = {item.normalized_ref for item in mentions if item.resolved_id}
            ambiguities = [
                amb
                for amb in ambiguities
                if clean_name(str(amb.get("mention_text", ""))) not in resolved_norms
            ]

        target = self._select_target_entity(
            canonical_operation=selected_operation or None,
            mentions=mentions,
            direct_object_candidate=direct_object_candidate,
        )
        return SegmentEntityResolution(
            segment_id=classification.segment_id,
            canonical_operation=selected_operation or None,
            verb_normalization=verb,
            direct_object_candidate=direct_object_candidate,
            target_kind=target.get("kind"),
            target_mention=target.get("mention_text"),
            target_resolved_id=target.get("resolved_id"),
            mentions=mentions,
            ambiguities=ambiguities,
            errors=errors,
        )

    def _resolve_expansion_entities(
        self,
        *,
        classification: SegmentClassification,
        scenario_id: str | None,
    ) -> list[EntityMention]:
        text = str(classification.text or "")
        mentions: list[EntityMention] = []

        for match in _PRODUCT_ID_RE.finditer(text):
            product_id = str(match.group(1)).strip()
            mentions.append(
                EntityMention(
                    kind="product",
                    mention_text=product_id,
                    normalized_ref=clean_name(product_id),
                    strategy="exact",
                    resolved_id=f"product:{product_id}",
                    confidence=1.0,
                    reason_code="entity_exact_match",
                )
            )

        for match in _JOB_ID_RE.finditer(text):
            job_id = str(match.group(1)).strip()
            mentions.append(
                EntityMention(
                    kind="job",
                    mention_text=job_id,
                    normalized_ref=clean_name(job_id),
                    strategy="exact",
                    resolved_id=f"job:{job_id}",
                    confidence=1.0,
                    reason_code="entity_exact_match",
                )
            )

        for match in _TOOL_NAME_RE.finditer(text):
            tool_name = str(match.group(1)).strip()
            if tool_name in self._known_tool_names():
                mentions.append(
                    EntityMention(
                        kind="tool",
                        mention_text=tool_name,
                        normalized_ref=clean_name(tool_name),
                        strategy="exact",
                        resolved_id=f"tool:{tool_name}",
                        confidence=1.0,
                        reason_code="entity_exact_match",
                    )
                )

        coord = _COORD_RE.search(text)
        if coord is not None:
            x = str(coord.group(1)).strip()
            y = str(coord.group(2)).strip()
            mentions.append(
                EntityMention(
                    kind="coordinate",
                    mention_text=f"x={x}, y={y}",
                    normalized_ref=f"x={x},y={y}",
                    strategy="exact",
                    resolved_id=f"coordinate:{x},{y}",
                    confidence=1.0,
                    reason_code="entity_exact_match",
                )
            )

        dates = [str(match.group(1)).strip() for match in _DATE_RE.finditer(text)]
        if len(dates) == 1:
            mentions.append(
                EntityMention(
                    kind="time_window",
                    mention_text=dates[0],
                    normalized_ref=dates[0],
                    strategy="exact",
                    resolved_id=f"time_window:{dates[0]}",
                    confidence=1.0,
                    reason_code="entity_exact_match",
                )
            )
        elif len(dates) >= 2:
            mentions.append(
                EntityMention(
                    kind="time_window",
                    mention_text=f"{dates[0]}..{dates[1]}",
                    normalized_ref=f"{dates[0]}..{dates[1]}",
                    strategy="exact",
                    resolved_id=f"time_window:{dates[0]}..{dates[1]}",
                    confidence=1.0,
                    reason_code="entity_exact_match",
                )
            )

        lower_text = text.lower()
        if "pin" in lower_text or "marker" in lower_text:
            pin_name = ""
            quoted = re.search(r'"([^"]+)"', text)
            if quoted:
                pin_name = str(quoted.group(1)).strip()
            if pin_name:
                mentions.append(
                    EntityMention(
                        kind="marker_or_pin",
                        mention_text=pin_name,
                        normalized_ref=clean_name(pin_name),
                        strategy="fuzzy",
                        resolved_id=f"marker_or_pin:{clean_name(pin_name)}",
                        confidence=0.9,
                        reason_code="entity_fuzzy_match",
                    )
                )

        source_match = re.search(r"\bsource\s+([A-Za-z0-9_.\-/]+)", text, re.IGNORECASE)
        if source_match:
            source_name = str(source_match.group(1)).strip()
            mentions.append(
                EntityMention(
                    kind="dataset_or_source",
                    mention_text=source_name,
                    normalized_ref=clean_name(source_name),
                    strategy="fuzzy",
                    resolved_id=f"dataset_or_source:{clean_name(source_name)}",
                    confidence=0.9,
                    reason_code="entity_fuzzy_match",
                )
            )

        recipe_match = re.search(r"\b(recipe[_\-\s]?[a-z0-9_\-]+)\b", lower_text)
        if recipe_match:
            recipe_id = str(recipe_match.group(1)).replace(" ", "_").strip()
            mentions.append(
                EntityMention(
                    kind="recipe",
                    mention_text=recipe_id,
                    normalized_ref=clean_name(recipe_id),
                    strategy="fuzzy",
                    resolved_id=f"recipe:{recipe_id}",
                    confidence=0.9,
                    reason_code="entity_fuzzy_match",
                )
            )
        product_type = str(classification.product_type or "").strip()
        if product_type:
            recipe_ids = recipe_ids_for_product_type(product_type)
            if recipe_ids:
                recipe_id = str(recipe_ids[0]).strip()
                mentions.append(
                    EntityMention(
                        kind="recipe",
                        mention_text=recipe_id,
                        normalized_ref=clean_name(recipe_id),
                        strategy="exact",
                        resolved_id=f"recipe:{recipe_id}",
                        confidence=1.0,
                        reason_code="entity_exact_match",
                    )
                )

        if ".py" in lower_text:
            notebook = _extract_file_token(text)
            if notebook and notebook.lower().endswith(".py"):
                mentions.append(
                    EntityMention(
                        kind="notebook",
                        mention_text=notebook,
                        normalized_ref=clean_name(notebook),
                        strategy="fuzzy",
                        resolved_id=f"notebook:{notebook}",
                        confidence=0.9,
                        reason_code="entity_fuzzy_match",
                    )
                )
        return mentions

    def _known_tool_names(self) -> set[str]:
        if self._tool_names_cache is not None:
            return self._tool_names_cache
        try:
            from backend.services.assistant.tool_registry import list_tools_schema

            names: set[str] = set()
            for item in list_tools_schema():
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    name = str(item.get("function", {}).get("name", "")).strip()
                if name:
                    names.add(name)
            self._tool_names_cache = names
            return names
        except Exception:
            return set()

    @staticmethod
    def _with_dep_metadata(
        mention: EntityMention,
        *,
        dep_meta_by_mention: dict[str, tuple[str | None, str | None]],
    ) -> EntityMention:
        key = clean_name(mention.mention_text)
        dep_role, dep_head = dep_meta_by_mention.get(key, (None, None))
        if dep_role is None and dep_head is None:
            return mention
        return EntityMention(
            kind=mention.kind,
            mention_text=mention.mention_text,
            normalized_ref=mention.normalized_ref,
            strategy=mention.strategy,
            resolved_id=mention.resolved_id,
            confidence=mention.confidence,
            reason_code=mention.reason_code,
            candidates=mention.candidates,
            dep_role=dep_role,
            dep_head=dep_head,
        )

    @staticmethod
    def _load_pos_model() -> Any | None:
        try:
            import spacy  # type: ignore
        except Exception:
            return None
        try:
            return spacy.load("en_core_web_sm", disable=["ner"])
        except Exception:
            return None

    def _extract_pos_mentions(self, text: str) -> list[str]:
        nlp = self._pos_nlp
        raw = str(text or "").strip()
        if nlp is None or not raw:
            return []
        try:
            doc = nlp(raw)
        except Exception:
            return []
        mentions: list[str] = []
        seen: set[str] = set()
        try:
            for chunk in getattr(doc, "noun_chunks", []):
                item = str(chunk.text).strip(" .,:;!?")
                key = clean_name(item)
                if not key or key in seen:
                    continue
                root = getattr(chunk, "root", None)
                if getattr(root, "pos_", "") == "PRON" or key in _PERSONAL_PRONOUNS:
                    continue
                mentions.append(item)
                seen.add(key)
                if len(mentions) >= 8:
                    return mentions
        except Exception:
            pass
        for token in doc:
            if getattr(token, "pos_", "") not in {"NOUN", "PROPN"}:
                continue
            item = str(token.text).strip(" .,:;!?")
            key = clean_name(item)
            if not key or key in seen:
                continue
            if key in {"it", "that", "there"}:
                continue
            mentions.append(item)
            seen.add(key)
            if len(mentions) >= 8:
                break
        return mentions

    def _build_dependency_metadata_map(self, text: str) -> dict[str, tuple[str | None, str | None]]:
        nlp = self._pos_nlp
        raw = str(text or "").strip()
        if nlp is None or not raw:
            return {}
        try:
            doc = nlp(raw)
        except Exception:
            return {}
        meta: dict[str, tuple[str | None, str | None]] = {}
        try:
            for chunk in getattr(doc, "noun_chunks", []):
                mention = str(chunk.text).strip(" .,:;!?")
                key = clean_name(mention)
                if not key or key in meta:
                    continue
                root = getattr(chunk, "root", None)
                # Skip pronoun-headed chunks (e.g. "me", "we") — they are not
                # geographic entity references and should not influence target selection.
                if getattr(root, "pos_", "") == "PRON" or key in _PERSONAL_PRONOUNS:
                    continue
                dep = str(getattr(root, "dep_", "")).strip() or None
                head = str(getattr(getattr(root, "head", None), "lemma_", "")).strip() or None
                if head:
                    head = head.lower()
                meta[key] = (dep, head)
        except Exception:
            pass
        for token in doc:
            mention = str(token.text).strip(" .,:;!?")
            key = clean_name(mention)
            if not key or key in meta:
                continue
            if getattr(token, "pos_", "") not in {"NOUN", "PROPN"}:
                continue
            dep = str(getattr(token, "dep_", "")).strip() or None
            head = str(getattr(getattr(token, "head", None), "lemma_", "")).strip() or None
            if head:
                head = head.lower()
            meta[key] = (dep, head)
        return meta

    @staticmethod
    def _direct_object_candidate_from_mentions(
        text: str,
        dep_meta_by_mention: dict[str, tuple[str | None, str | None]],
    ) -> str | None:
        raw = str(text or "").strip()
        if not raw or not dep_meta_by_mention:
            return None
        object_deps = {"dobj", "obj", "pobj"}
        best: str | None = None
        for chunk in re.split(r"[\s,;]+", raw):
            mention = str(chunk).strip(" .:!?")
            if not mention:
                continue
            key = clean_name(mention)
            if key in _PERSONAL_PRONOUNS:
                continue
            dep_role = dep_meta_by_mention.get(key, (None, None))[0]
            if dep_role in object_deps:
                best = mention
                break
        if best:
            return best
        ranked: list[tuple[int, str]] = []
        for mention_key, (dep_role, _dep_head) in dep_meta_by_mention.items():
            if dep_role not in object_deps:
                continue
            if mention_key in _PERSONAL_PRONOUNS:
                continue
            ranked.append((len(mention_key), mention_key))
        if not ranked:
            return None
        ranked.sort(reverse=True)
        return ranked[0][1]

    @staticmethod
    def _select_target_entity(
        *,
        canonical_operation: str | None,
        mentions: list[EntityMention],
        direct_object_candidate: str | None,
    ) -> dict[str, str | None]:
        op = str(canonical_operation or "").strip().lower()
        resolved = [item for item in mentions if str(item.resolved_id or "").strip()]
        if not resolved:
            return {"kind": None, "mention_text": None, "resolved_id": None}

        preferred_kinds: list[str] = []
        if op in {"goto", "search"}:
            preferred_kinds = ["feature", "scenario"]
        elif op in {"show", "hide"}:
            preferred_kinds = ["layer", "file", "feature"]
        elif op in {"set_current"}:
            preferred_kinds = ["scenario"]

        object_mentions = [item for item in resolved if item.dep_role in {"dobj", "obj", "pobj"}]
        if direct_object_candidate:
            norm = clean_name(direct_object_candidate)
            object_mentions = [
                item
                for item in resolved
                if clean_name(item.mention_text) == norm or item.dep_role in {"dobj", "obj", "pobj"}
            ] or object_mentions

        ranked_pool = object_mentions or resolved

        if op == "show":
            top_layer = next((item for item in ranked_pool if item.kind == "layer"), None)
            top_file = next((item for item in ranked_pool if item.kind == "file"), None)
            if top_layer is not None and top_file is not None:
                layer_norm = clean_name(top_layer.mention_text)
                file_norm = clean_name(top_file.mention_text)
                if layer_norm and file_norm and (
                    layer_norm == file_norm or layer_norm in file_norm or file_norm in layer_norm
                ) and abs(float(top_layer.confidence) - float(top_file.confidence)) <= 0.12:
                    return {
                        "kind": "ambiguous_layer_or_file",
                        "mention_text": top_layer.mention_text,
                        "resolved_id": None,
                    }

        for preferred in preferred_kinds:
            for item in ranked_pool:
                if item.kind == preferred:
                    return {
                        "kind": item.kind,
                        "mention_text": item.mention_text,
                        "resolved_id": item.resolved_id,
                    }
        top = sorted(
            ranked_pool,
            key=lambda item: (float(item.confidence), 1 if item.dep_role in {"dobj", "obj", "pobj"} else 0),
            reverse=True,
        )[0]
        return {
            "kind": top.kind,
            "mention_text": top.mention_text,
            "resolved_id": top.resolved_id,
        }

    def _bind_pronoun(
        self,
        *,
        canonical_operation: str | None,
        prior_mentions: list[EntityMention],
    ) -> EntityMention | None:
        if not prior_mentions:
            return None
        preferred_kind = _preferred_kind_for_operation(canonical_operation)
        candidates = [item for item in reversed(prior_mentions) if item.resolved_id]
        if preferred_kind:
            candidates = [item for item in candidates if item.kind == preferred_kind] or candidates
        if not candidates:
            return None
        top = candidates[0]
        if len(candidates) > 1 and candidates[0].kind == candidates[1].kind and candidates[0].confidence == candidates[1].confidence:
            choice_candidates = [
                EntityCandidate(kind=item.kind, resolved_id=str(item.resolved_id), label=str(item.mention_text), score=item.confidence)
                for item in candidates[:3]
            ]
            return EntityMention(
                kind=top.kind,
                mention_text="it",
                normalized_ref="it",
                strategy="pronoun_from_turn_state",
                resolved_id=None,
                confidence=0.0,
                reason_code="entity_ambiguous_requires_clarification",
                candidates=choice_candidates,
            )
        return EntityMention(
            kind=top.kind,
            mention_text="it",
            normalized_ref="it",
            strategy="pronoun_from_turn_state",
            resolved_id=top.resolved_id,
            confidence=0.99,
            reason_code=f"entity_pronoun_bound_last_{top.kind}",
            candidates=[],
        )

    def _resolve_reference(
        self,
        *,
        kind: str,
        mention_text: str,
        scenario_id: str | None,
        context_filter: str | None = None,
    ) -> EntityMention:
        mention = _norm(mention_text)
        normalized = clean_name(mention)
        if not mention:
            return EntityMention(
                kind=kind,
                mention_text=mention_text,
                normalized_ref=normalized,
                strategy="exact",
                resolved_id=None,
                confidence=0.0,
                reason_code="entity_no_match",
            )
        if kind == "feature":
            return self._resolve_feature(mention=mention, normalized=normalized, feature_type=context_filter)
        if kind == "scenario":
            return self._resolve_scenario(mention=mention, normalized=normalized)
        if kind == "layer":
            return self._resolve_layer(mention=mention, normalized=normalized, scenario_id=scenario_id)
        if kind == "file":
            return self._resolve_file(mention=mention, normalized=normalized, scenario_id=scenario_id)
        if kind == "colormap":
            return self._resolve_colormap(mention=mention, normalized=normalized, scenario_id=scenario_id)
        return EntityMention(
            kind=kind,
            mention_text=mention,
            normalized_ref=normalized,
            strategy="exact",
            resolved_id=None,
            confidence=0.0,
            reason_code="entity_no_match",
        )

    def _resolve_feature(self, *, mention: str, normalized: str, feature_type: str | None) -> EntityMention:
        # 1. Try exact match variations first via shared helper.
        exact = resolve_feature_with_variants(self._nomenclature, mention, feature_type)
        if exact is not None:
            feature_id = int(exact.get("feature_id", 0))
            return EntityMention(
                kind="feature",
                mention_text=mention,
                normalized_ref=normalized,
                strategy="exact",
                resolved_id=f"feature:{feature_id}",
                confidence=1.0,
                reason_code="entity_exact_match",
                candidates=[],
            )

        # 2. Fall back to fuzzy search for the primary mention.
        results = self._nomenclature.search_fuzzy(query=mention, limit=3, feature_type=feature_type)
        candidates: list[EntityCandidate] = []
        for item in results:
            feature_id = int(item.get("feature_id", 0))
            candidates.append(
                EntityCandidate(
                    kind="feature",
                    resolved_id=f"feature:{feature_id}",
                    label=str(item.get("name", "")).strip() or f"feature:{feature_id}",
                    score=float(item.get("match_score", 0.0)) / 100.0,
                )
            )
        if not candidates:
            return EntityMention(
                kind="feature",
                mention_text=mention,
                normalized_ref=normalized,
                strategy="fuzzy",
                resolved_id=None,
                confidence=0.0,
                reason_code="entity_no_match",
                candidates=[],
            )
        top = candidates[0]
        if len(candidates) > 1 and abs(candidates[0].score - candidates[1].score) <= 0.05:
            return EntityMention(
                kind="feature",
                mention_text=mention,
                normalized_ref=normalized,
                strategy="fuzzy",
                resolved_id=None,
                confidence=top.score,
                reason_code="entity_ambiguous_requires_clarification",
                candidates=candidates,
            )
        return EntityMention(
            kind="feature",
            mention_text=mention,
            normalized_ref=normalized,
            strategy="fuzzy",
            resolved_id=top.resolved_id if top.score >= 0.90 else None,
            confidence=top.score,
            reason_code="entity_fuzzy_match" if top.score >= 0.90 else "entity_ambiguous_requires_clarification",
            candidates=candidates,
        )

    def _resolve_scenario(self, *, mention: str, normalized: str) -> EntityMention:
        items: list[EntityCandidate] = []
        for scenario in self._services.scenario_service.list_scenarios():
            sid = str(getattr(scenario, "scenario_id", "")).strip()
            sroot = str(getattr(scenario, "scenario_root", "")).strip()
            sname = str(getattr(scenario, "name", "")).strip()
            refs = [sid, sroot, sname]
            best = max((_score_name_match(query=mention, candidate=ref) for ref in refs if ref), default=0.0)
            if best <= 0:
                continue
            items.append(EntityCandidate(kind="scenario", resolved_id=f"scenario:{sid}", label=sname or sid, score=best))
        if not items:
            return EntityMention("scenario", mention, normalized, "exact", None, 0.0, "entity_no_match")
        items = sorted(items, key=lambda item: (-item.score, item.label))
        top = items[0]
        if len(items) > 1 and abs(items[0].score - items[1].score) <= 0.05:
            return EntityMention("scenario", mention, normalized, "fuzzy", None, top.score, "entity_ambiguous_requires_clarification", items[:3])
        return EntityMention(
            "scenario",
            mention,
            normalized,
            "exact" if top.score >= 1.0 else "fuzzy",
            top.resolved_id if top.score >= 0.90 else None,
            top.score,
            "entity_exact_match" if top.score >= 1.0 else "entity_fuzzy_match" if top.score >= 0.90 else "entity_ambiguous_requires_clarification",
            items[:3],
        )

    def _resolve_layer(self, *, mention: str, normalized: str, scenario_id: str | None) -> EntityMention:
        if not scenario_id:
            return EntityMention("layer", mention, normalized, "exact", None, 0.0, "entity_no_match")
        items: list[EntityCandidate] = []
        for layer in self._services.layer_service.list_layers(scenario_id):
            layer_id = str(getattr(layer, "layer_id", "")).strip()
            title = str(getattr(layer, "title", "") or "").strip()
            best = max(
                _score_name_match(query=mention, candidate=candidate)
                for candidate in [layer_id, title, Path(title).stem if title else ""]
            )
            if best <= 0:
                continue
            label = title or layer_id
            items.append(EntityCandidate(kind="layer", resolved_id=f"layer:{layer_id}", label=label, score=best))
        if not items:
            return EntityMention("layer", mention, normalized, "exact", None, 0.0, "entity_no_match")
        items = sorted(items, key=lambda item: (-item.score, item.label))
        top = items[0]
        if len(items) > 1 and abs(items[0].score - items[1].score) <= 0.05:
            return EntityMention("layer", mention, normalized, "fuzzy", None, top.score, "entity_ambiguous_requires_clarification", items[:3])
        return EntityMention(
            "layer",
            mention,
            normalized,
            "exact" if top.score >= 1.0 else "fuzzy",
            top.resolved_id if top.score >= 0.90 else None,
            top.score,
            "entity_exact_match" if top.score >= 1.0 else "entity_fuzzy_match" if top.score >= 0.90 else "entity_ambiguous_requires_clarification",
            items[:3],
        )

    def _resolve_file(self, *, mention: str, normalized: str, scenario_id: str | None) -> EntityMention:
        scenario_dir = self._scenario_directory_resolver(scenario_id) if callable(self._scenario_directory_resolver) else None
        if scenario_dir is None:
            return EntityMention("file", mention, normalized, "exact", None, 0.0, "entity_no_match")
        root = Path(str(scenario_dir)).resolve()
        if not root.exists() or not root.is_dir():
            return EntityMention("file", mention, normalized, "exact", None, 0.0, "entity_no_match")
        rel_query = mention.replace("\\", "/").strip("/")
        best_rel: str | None = None
        best_score = 0.0
        scanned = 0
        for path in root.rglob("*"):
            if scanned >= 4000:
                break
            scanned += 1
            if not path.is_file():
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            score = max(
                _score_name_match(query=rel_query, candidate=rel),
                _score_name_match(query=rel_query, candidate=path.name),
                _score_name_match(query=rel_query, candidate=Path(rel).stem),
            )
            if score > best_score:
                best_score = score
                best_rel = rel
        if not best_rel:
            return EntityMention("file", mention, normalized, "exact", None, 0.0, "entity_no_match")
        resolved_id = f"file:{best_rel}"
        return EntityMention(
            "file",
            mention,
            normalized,
            "exact" if best_score >= 1.0 else "fuzzy",
            resolved_id if best_score >= 0.90 else None,
            best_score,
            "entity_exact_match" if best_score >= 1.0 else "entity_fuzzy_match" if best_score >= 0.90 else "entity_ambiguous_requires_clarification",
            [EntityCandidate(kind="file", resolved_id=resolved_id, label=best_rel, score=best_score)],
        )

    def _resolve_colormap(self, *, mention: str, normalized: str, scenario_id: str | None) -> EntityMention:
        scenario_root = self._scenario_directory_resolver(scenario_id) if callable(self._scenario_directory_resolver) else None
        repo_root = Path(__file__).resolve().parents[3]
        app_cfg = load_app_config(strict=False)
        backend_cfg = app_cfg.get("backend", {}) if isinstance(app_cfg, dict) else {}
        map_cfg = backend_cfg.get("map", {}) if isinstance(backend_cfg, dict) else {}
        if not isinstance(map_cfg, dict):
            map_cfg = {}
        registry = resolve_colormap_registry(
            repo_root=repo_root,
            config_path=resolve_config_path(),
            map_cfg=map_cfg,
            scenario_root=Path(str(scenario_root)).resolve() if scenario_root else None,
        )
        items: list[EntityCandidate] = []
        for entry in list(registry.get("colormaps", [])):
            cmap_id = str(entry.get("id", "")).strip()
            if not cmap_id:
                continue
            score = _score_name_match(query=mention, candidate=cmap_id)
            if score <= 0:
                continue
            items.append(EntityCandidate(kind="colormap", resolved_id=f"colormap:{cmap_id}", label=cmap_id, score=score))
        if not items:
            return EntityMention("colormap", mention, normalized, "exact", None, 0.0, "entity_no_match")
        items = sorted(items, key=lambda item: (-item.score, item.label))
        top = items[0]
        return EntityMention(
            "colormap",
            mention,
            normalized,
            "exact" if top.score >= 1.0 else "fuzzy",
            top.resolved_id if top.score >= 0.90 else None,
            top.score,
            "entity_exact_match" if top.score >= 1.0 else "entity_fuzzy_match" if top.score >= 0.90 else "entity_ambiguous_requires_clarification",
            items[:3],
        )


def _preferred_kind_for_operation(canonical_operation: str | None) -> str | None:
    op = str(canonical_operation or "").strip().lower()
    if op in {"goto", "search", "identify", "nearby"}:
        return "feature"
    if op in {"set_current"}:
        return "scenario"
    if op in {"show", "hide"}:
        return "layer"
    if op in {"apply"}:
        return "colormap"
    return None


def _extract_feature_mentions_from_text(text: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    # IAU-style TYPE NAME: "Mons Malapert", "Crater Dawa"
    for match in _FEATURE_TYPE_PATTERN.finditer(str(text or "")):
        feature_type = str(match.group(1)).strip().title()
        feature_name = str(match.group(2)).strip()
        if feature_name and feature_name.lower() not in seen:
            results.append((feature_name, feature_type))
            seen.add(feature_name.lower())
    # Colloquial NAME TYPE: "Dawa Crater", "Malapert Mons"
    for match in _FEATURE_NAME_TYPE_PATTERN.finditer(str(text or "")):
        feature_name = str(match.group(1)).strip()
        feature_type = str(match.group(2)).strip().title()
        if feature_name and feature_name.lower() not in seen:
            results.append((feature_name, feature_type))
            seen.add(feature_name.lower())
    return results
