from __future__ import annotations

from backend.services.assistant.create_product_planner import (
    AvailableProduct,
    CreateProductBlock,
    CreateProductPlan,
    CreateProductPlanner,
)
from backend.services.assistant.command_router import HybridCommandRouter
from backend.services.assistant.prompt_classifier import PromptClassifier
from backend.services.assistant.prompt_segmenter import PromptSegment
from backend.services.assistant.prompt_classifier import SegmentClassification, SegmentOffsets


def _classification(*, text: str, product_type: str, sources: list[str] | None = None) -> SegmentClassification:
    return SegmentClassification(
        segment_id="s1",
        text=text,
        offsets=SegmentOffsets(start=0, stop=len(text)),
        segment_class="create_product",
        confidence=0.9,
        classification_origin="test",
        product_type=product_type,
        sources=list(sources or []),
    )


def _inventory() -> list[AvailableProduct]:
    return [
        AvailableProduct(
            product_id="primary_dem",
            kind="raster",
            subkind="dem",
            filename="dem.tif",
            references=("primary_dem", "dem"),
            relative_paths=("dem.tif",),
        )
    ]


def test_planner_builds_recipe_plan_for_slope() -> None:
    planner = CreateProductPlanner()
    outcome = planner.plan(
        classification=_classification(text="Create a slope raster from the primary DEM", product_type="slope_raster", sources=["primary_dem"]),
        scenario_id="scn_1",
        available_products=_inventory(),
    )
    assert isinstance(outcome, CreateProductPlan)
    assert outcome.recipe_id == "slope_from_dem_v1"
    assert outcome.prerequisite_count == 1
    assert len(outcome.steps) == 1
    assert outcome.steps[0].tool_name == "raster.calculate"
    assert outcome.steps[0].tool_args["expression"] == "slope(dem)"


def test_planner_blocks_when_recipe_missing() -> None:
    planner = CreateProductPlanner()
    outcome = planner.plan(
        classification=_classification(text="Create a roughness raster", product_type="roughness_raster"),
        scenario_id="scn_1",
        available_products=_inventory(),
    )
    assert isinstance(outcome, CreateProductBlock)
    assert outcome.reason_code == "no_supported_recipe"


def test_planner_blocks_when_product_type_is_not_canonical() -> None:
    planner = CreateProductPlanner()
    outcome = planner.plan(
        classification=_classification(text="Create a science raster", product_type="science_raster"),
        scenario_id="scn_1",
        available_products=_inventory(),
    )
    assert isinstance(outcome, CreateProductBlock)
    assert outcome.reason_code == "unknown_canonical_product_type"


def test_planner_blocks_threshold_mask_without_threshold_parameter() -> None:
    planner = CreateProductPlanner()
    outcome = planner.plan(
        classification=_classification(
            text="Create a threshold mask from slope raster",
            product_type="threshold_mask",
            sources=["slope_raster"],
        ),
        scenario_id="scn_1",
        available_products=_inventory(),
    )
    assert isinstance(outcome, CreateProductBlock)
    assert outcome.reason_code == "missing_required_product_parameter"


def test_planner_uses_existing_output_reuse() -> None:
    planner = CreateProductPlanner()
    available = _inventory() + [
        AvailableProduct(
            product_id="prod_slope",
            kind="raster",
            subkind="slope_raster",
            filename="slope.tif",
            references=("slope",),
            relative_paths=("slope.tif",),
        )
    ]
    outcome = planner.plan(
        classification=_classification(text="Create a slope raster from the primary DEM", product_type="slope_raster", sources=["primary_dem"]),
        scenario_id="scn_1",
        available_products=available,
    )
    assert not isinstance(outcome, CreateProductPlan)
    assert not isinstance(outcome, CreateProductBlock)
    assert outcome.output_relative_path == "slope.tif"


def test_planner_can_force_plan_even_when_reusable_product_exists() -> None:
    planner = CreateProductPlanner()
    available = _inventory() + [
        AvailableProduct(
            product_id="prod_slope",
            kind="raster",
            subkind="slope_raster",
            filename="slope.tif",
            references=("slope",),
            relative_paths=("slope.tif",),
        )
    ]
    outcome = planner.plan(
        classification=_classification(
            text="Create a slope raster from the primary DEM",
            product_type="slope_raster",
            sources=["primary_dem"],
        ),
        scenario_id="scn_1",
        available_products=available,
        allow_reuse=False,
    )
    assert isinstance(outcome, CreateProductPlan)
    assert outcome.output_relative_path == "slope.tif"
    assert outcome.steps[0].tool_name == "raster.calculate"


def test_noun_phrase_classification_integrates_with_deterministic_create_product_recipe_plan() -> None:
    classifier = PromptClassifier()
    router = HybridCommandRouter(enabled=True)
    segment_text = "Need a hillshade from the primary DEM."
    classifications = classifier.classify(
        segments=[
            PromptSegment(
                segment_id="s1",
                text=segment_text,
                start_char=0,
                end_char=len(segment_text),
                is_imperative_candidate=False,
                has_complexity_guard=False,
                segmentation_confidence=0.82,
            )
        ],
        scenario_id="scn_1",
        router=router,
    )
    assert len(classifications) == 1
    classification = classifications[0]
    assert classification.segment_class == "create_product"
    assert classification.product_type == "hillshade_raster"

    planner = CreateProductPlanner()
    outcome = planner.plan(
        classification=classification,
        scenario_id="scn_1",
        available_products=_inventory(),
        allow_reuse=False,
    )
    assert isinstance(outcome, CreateProductPlan)
    assert outcome.recipe_id == "hillshade_from_dem_v1"
    assert outcome.steps[0].tool_name == "raster.calculate"
    assert outcome.steps[0].tool_args["expression"] == "hillshade(dem, 315, 45)"
