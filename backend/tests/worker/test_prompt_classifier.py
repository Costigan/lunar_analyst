from __future__ import annotations

from backend.services.assistant.command_router import HybridCommandRouter
from backend.services.assistant.prompt_classifier import PromptClassifier
from backend.services.assistant.prompt_segmenter import PromptSegment
from backend.services.assistant.product_type_dictionary import PRODUCT_TYPE_DICT


def test_classifier_labels_router_and_model_segments() -> None:
    classifier = PromptClassifier()
    router = HybridCommandRouter(enabled=True)
    segments = [
        PromptSegment(
            segment_id="s1",
            text="Turn on slope layer",
            start_char=0,
            end_char=19,
            is_imperative_candidate=True,
            has_complexity_guard=False,
            segmentation_confidence=0.9,
        ),
        PromptSegment(
            segment_id="s2",
            text="Explain the scientific tradeoffs",
            start_char=21,
            end_char=52,
            is_imperative_candidate=False,
            has_complexity_guard=True,
            segmentation_confidence=0.85,
        ),
    ]

    result = classifier.classify(segments=segments, scenario_id="scn_1", router=router)
    by_id = {item.segment_id: item for item in result}
    assert by_id["s1"].segment_class == "command"
    assert by_id["s1"].command == "layer.set_visible_by_name"
    assert by_id["s2"].segment_class == "other"


def test_classifier_heuristic_create_product_sets_minimal_intent_properties() -> None:
    classifier = PromptClassifier()
    router = HybridCommandRouter(enabled=True)
    segments = [
        PromptSegment(
            segment_id="s1",
            text="Create a slope raster.",
            start_char=0,
            end_char=22,
            is_imperative_candidate=True,
            has_complexity_guard=False,
            segmentation_confidence=0.82,
        )
    ]
    result = classifier.classify(segments=segments, scenario_id="scn_1", router=router)
    assert len(result) == 1
    assert result[0].segment_class == "create_product"
    assert result[0].product_type == "slope_raster"
    assert result[0].intent_properties == {
        "operation": "create",
        "product_type": "slope_raster",
    }


def test_classifier_can_defer_command_classification_for_unified_deterministic_promotion() -> None:
    classifier = PromptClassifier()
    router = HybridCommandRouter(enabled=True)
    segments = [
        PromptSegment(
            segment_id="s1",
            text="Turn on slope layer",
            start_char=0,
            end_char=19,
            is_imperative_candidate=True,
            has_complexity_guard=False,
            segmentation_confidence=0.9,
        ),
    ]

    result = classifier.classify(
        segments=segments,
        scenario_id="scn_1",
        router=router,
        deterministic_command_classification_enabled=False,
    )
    assert len(result) == 1
    assert result[0].segment_class == "other"
    assert result[0].classification_origin == "deterministic_command_candidate"


def test_classifier_noun_phrase_match_for_recipe_backed_product_type() -> None:
    classifier = PromptClassifier()
    router = HybridCommandRouter(enabled=True)
    segments = [
        PromptSegment(
            segment_id="s1",
            text="Need a hillshade for this scenario.",
            start_char=0,
            end_char=34,
            is_imperative_candidate=False,
            has_complexity_guard=False,
            segmentation_confidence=0.8,
        )
    ]
    result = classifier.classify(segments=segments, scenario_id="scn_1", router=router)
    assert len(result) == 1
    assert result[0].segment_class == "create_product"
    assert result[0].classification_origin == "deterministic_noun_phrase_product_match"
    assert result[0].product_type == "hillshade_raster"
    assert result[0].intent_properties == {
        "operation": "create",
        "product_type": "hillshade_raster",
    }


def test_classifier_noun_phrase_match_uses_product_type_alias_source_of_truth() -> None:
    slope_spec = PRODUCT_TYPE_DICT["slope_raster"]
    assert "slope map" in slope_spec.noun_phrase_aliases

    classifier = PromptClassifier()
    router = HybridCommandRouter(enabled=True)
    segments = [
        PromptSegment(
            segment_id="s1",
            text="Please use a slope map from the primary DEM.",
            start_char=0,
            end_char=41,
            is_imperative_candidate=False,
            has_complexity_guard=False,
            segmentation_confidence=0.8,
        )
    ]
    result = classifier.classify(segments=segments, scenario_id="scn_1", router=router)
    assert len(result) == 1
    assert result[0].segment_class == "create_product"
    assert result[0].product_type == "slope_raster"
    assert result[0].sources == ["primary_dem"]


def test_classifier_noun_phrase_ambiguity_downgrades_to_other() -> None:
    classifier = PromptClassifier()
    router = HybridCommandRouter(enabled=True)
    segments = [
        PromptSegment(
            segment_id="s1",
            text="Compare hillshade and slope.",
            start_char=0,
            end_char=27,
            is_imperative_candidate=False,
            has_complexity_guard=True,
            segmentation_confidence=0.8,
        )
    ]
    result = classifier.classify(segments=segments, scenario_id="scn_1", router=router)
    assert len(result) == 1
    assert result[0].segment_class == "other"
    assert result[0].classification_origin == "fallback_other"


def test_classifier_noun_phrase_non_recipe_product_type_remains_other() -> None:
    roughness_spec = PRODUCT_TYPE_DICT["roughness_raster"]
    assert "roughness" in roughness_spec.noun_phrase_aliases
    assert roughness_spec.canonical_recipe_ids == ()

    classifier = PromptClassifier()
    router = HybridCommandRouter(enabled=True)
    segments = [
        PromptSegment(
            segment_id="s1",
            text="Need roughness for this landing region.",
            start_char=0,
            end_char=38,
            is_imperative_candidate=False,
            has_complexity_guard=False,
            segmentation_confidence=0.82,
        )
    ]
    result = classifier.classify(segments=segments, scenario_id="scn_1", router=router)
    assert len(result) == 1
    assert result[0].segment_class == "other"
    assert result[0].classification_origin == "fallback_other"
