from __future__ import annotations

from backend.services.assistant.product_type_dictionary import PRODUCT_TYPE_DICT, validate_product_type_dictionary


def test_product_type_dictionary_is_complete() -> None:
    validate_product_type_dictionary()
    assert "hillshade_raster" in PRODUCT_TYPE_DICT
    assert "slope_raster" in PRODUCT_TYPE_DICT
    assert "threshold_mask" in PRODUCT_TYPE_DICT

    for label, spec in PRODUCT_TYPE_DICT.items():
        assert label == spec.product_type
        assert spec.description.strip()
        assert spec.generation_functions
        assert spec.generation_time_estimator_name.strip()
        assert spec.name_strategy_name.strip()
