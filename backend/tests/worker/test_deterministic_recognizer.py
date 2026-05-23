from __future__ import annotations

from backend.services.assistant.action_router_config import EntityKindRoutingRule
from backend.services.assistant.command_router import HybridCommandRouter
from backend.services.assistant.deterministic_recognizer import UnifiedDeterministicRecognizer
from backend.services.assistant.entity_reference_resolver import EntityMention, SegmentEntityResolution
from backend.services.assistant.prompt_classifier import SegmentClassification, SegmentOffsets
from backend.services.assistant.verb_normalizer import VerbNormalizationResult


def _other(segment_id: str, text: str) -> SegmentClassification:
    return SegmentClassification(
        segment_id=segment_id,
        text=text,
        offsets=SegmentOffsets(start=0, stop=len(text)),
        segment_class="other",
        confidence=0.8,
        classification_origin="fallback_other",
    )


def test_recognizer_promotes_zoom_feature_to_location_navigation() -> None:
    recognizer = UnifiedDeterministicRecognizer(
        command_router=HybridCommandRouter(enabled=False),
        entity_kind_rules=[
            EntityKindRoutingRule(
                rule_id="navigation.feature",
                required_verbs=["goto", "search", "show"],
                required_entity_kinds=["feature"],
                intent_families=["location_navigation", "layer_visibility_update"],
                min_confidence=0.9,
                allow_ambiguity=False,
            )
        ],
        entity_kind_routing_enabled=True,
    )
    classifications = [_other("s1", "Zoom to Mons Mouton.")]
    resolutions = {
        "s1": SegmentEntityResolution(
            segment_id="s1",
            canonical_operation="goto",
            verb_normalization=VerbNormalizationResult(
                canonical_operation="goto",
                normalized_input_operation=None,
                source="segment_text",
                operation_candidates=["goto"],
                matched_aliases_by_operation={"goto": ["zoom to"]},
            ),
            mentions=[
                EntityMention(
                    kind="feature",
                    mention_text="Mons Mouton",
                    normalized_ref="mons mouton",
                    strategy="exact",
                    resolved_id="feature:9071",
                    confidence=1.0,
                    reason_code="entity_exact_match",
                    dep_role="pobj",
                    dep_head="to",
                )
            ],
            target_kind="feature",
            target_mention="Mons Mouton",
            target_resolved_id="feature:9071",
        )
    }

    promoted, traces, fallback_ids = recognizer.promote(
        classifications=classifications,
        resolutions=resolutions,
        scenario_id="scn_1",
    )

    assert fallback_ids == []
    assert promoted[0].segment_class == "intent_family"
    assert promoted[0].intent_family == "location_navigation"
    assert promoted[0].intent_properties.get("operation") == "goto"
    assert promoted[0].intent_properties.get("feature_ref") == "Mons Mouton"
    assert traces["s1"].status == "matched"
    assert traces["s1"].matched_rule_id == "navigation.feature"


def test_recognizer_marks_no_match_for_semantic_fallback() -> None:
    recognizer = UnifiedDeterministicRecognizer(
        command_router=HybridCommandRouter(enabled=False),
        entity_kind_rules=[],
        entity_kind_routing_enabled=True,
    )
    classifications = [_other("s1", "Explain hazards around this site.")]
    resolutions = {
        "s1": SegmentEntityResolution(
            segment_id="s1",
            canonical_operation=None,
            verb_normalization=VerbNormalizationResult(
                canonical_operation=None,
                normalized_input_operation=None,
                source="none",
                operation_candidates=[],
            ),
            mentions=[],
        )
    }

    promoted, traces, fallback_ids = recognizer.promote(
        classifications=classifications,
        resolutions=resolutions,
        scenario_id="scn_1",
    )

    assert promoted[0].segment_class == "other"
    assert fallback_ids == ["s1"]
    assert traces["s1"].status == "no_match"
