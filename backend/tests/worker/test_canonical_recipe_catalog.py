from __future__ import annotations

from backend.services.assistant.canonical_recipe_catalog import (
    get_recipe,
    recipe_ids_for_product_type,
    validate_recipe_catalog,
)


def test_recipe_catalog_validates() -> None:
    validate_recipe_catalog()


def test_recipe_lookup_for_supported_product() -> None:
    ids = recipe_ids_for_product_type("slope_raster")
    assert ids == ["slope_from_dem_v1"]
    recipe = get_recipe(ids[0])
    assert recipe.execution_ref == "raster.calculate"
