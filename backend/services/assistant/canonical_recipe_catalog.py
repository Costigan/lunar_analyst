from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RecipeTemplateSpec:
    recipe_id: str
    product_type: str
    requires: tuple[str, ...]
    execution_ref: str
    expression_template: str
    default_output_relative_path: str
    required_parameters: tuple[str, ...] = ()
    reuse_keys: tuple[str, ...] = ("scenario_id", "source_product_id", "parameter_hash")
    metadata: dict[str, Any] = field(default_factory=dict)


_RECIPE_CATALOG: tuple[RecipeTemplateSpec, ...] = (
    RecipeTemplateSpec(
        recipe_id="hillshade_from_dem_v1",
        product_type="hillshade_raster",
        requires=("dem",),
        execution_ref="raster.calculate",
        expression_template="hillshade(dem, 315, 45)",
        default_output_relative_path="hillshade.tif",
        reuse_keys=("scenario_id", "source_product_id", "crs", "resolution", "parameter_hash"),
    ),
    RecipeTemplateSpec(
        recipe_id="slope_from_dem_v1",
        product_type="slope_raster",
        requires=("dem",),
        execution_ref="raster.calculate",
        expression_template="slope(dem)",
        default_output_relative_path="slope.tif",
        reuse_keys=("scenario_id", "source_product_id", "crs", "resolution", "parameter_hash"),
    ),
    RecipeTemplateSpec(
        recipe_id="aspect_from_dem_v1",
        product_type="aspect_raster",
        requires=("dem",),
        execution_ref="raster.calculate",
        expression_template="aspect(dem)",
        default_output_relative_path="aspect.tif",
        reuse_keys=("scenario_id", "source_product_id", "crs", "resolution", "parameter_hash"),
    ),
    RecipeTemplateSpec(
        recipe_id="threshold_mask_v1",
        product_type="threshold_mask",
        requires=("source_raster",),
        execution_ref="raster.calculate",
        expression_template="({source_raster}) {operator} {threshold}",
        default_output_relative_path="threshold_mask.tif",
        required_parameters=("operator", "threshold"),
    ),
)


def load_recipe_catalog() -> tuple[RecipeTemplateSpec, ...]:
    return _RECIPE_CATALOG


def validate_recipe_catalog() -> None:
    seen_ids: set[str] = set()
    for spec in _RECIPE_CATALOG:
        if not spec.recipe_id.strip():
            raise ValueError("recipe_catalog_missing_recipe_id")
        if spec.recipe_id in seen_ids:
            raise ValueError(f"recipe_catalog_duplicate_recipe_id:{spec.recipe_id}")
        seen_ids.add(spec.recipe_id)
        if not spec.product_type.strip():
            raise ValueError(f"recipe_catalog_missing_product_type:{spec.recipe_id}")
        if not spec.execution_ref.strip():
            raise ValueError(f"recipe_catalog_missing_execution_ref:{spec.recipe_id}")
        if not spec.expression_template.strip():
            raise ValueError(f"recipe_catalog_missing_expression_template:{spec.recipe_id}")
        if not spec.default_output_relative_path.strip():
            raise ValueError(f"recipe_catalog_missing_output_path:{spec.recipe_id}")


def recipe_ids_for_product_type(product_type: str) -> list[str]:
    key = str(product_type or "").strip()
    return [spec.recipe_id for spec in _RECIPE_CATALOG if spec.product_type == key]


def get_recipe(recipe_id: str) -> RecipeTemplateSpec:
    key = str(recipe_id or "").strip()
    for spec in _RECIPE_CATALOG:
        if spec.recipe_id == key:
            return spec
    raise KeyError(f"unknown_recipe_id:{recipe_id}")


validate_recipe_catalog()
