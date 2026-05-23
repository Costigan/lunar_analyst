from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


PixelType = str


@dataclass(frozen=True)
class GenerationFunctionSpec:
    function_name: str
    description: str
    argument_contract: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProductTypeSpec:
    product_type: str
    description: str
    precursor_requirements: list[str]
    default_pixel_type: PixelType
    generation_functions: list[GenerationFunctionSpec]
    generation_time_estimator_name: str
    name_strategy_name: str
    parameter_constraints: dict[str, Any] = field(default_factory=dict)
    canonical_recipe_ids: tuple[str, ...] = ()
    reuse_keys: tuple[str, ...] = ()
    required_parameters: tuple[str, ...] = ()
    noun_phrase_aliases: tuple[str, ...] = ()


def default_runtime_estimator(**_kwargs: Any) -> float:
    return 0.0


def canonical_name_from_product_type(*, product_type: str, sources: list[str] | None = None) -> str:
    source_prefix = ""
    if sources:
        source_prefix = "_".join(item.strip() for item in sources if str(item).strip())
    if source_prefix:
        return f"{source_prefix}_{product_type}"
    return product_type


def _single_step(function_name: str, description: str, **argument_contract: str) -> list[GenerationFunctionSpec]:
    return [
        GenerationFunctionSpec(
            function_name=function_name,
            description=description,
            argument_contract=dict(argument_contract),
        )
    ]


PRODUCT_TYPE_SPECS: tuple[ProductTypeSpec, ...] = (
    ProductTypeSpec(
        product_type="dem",
        description="Raster of surface elevation in meters from the lunar reference sphere (radius=1737.4 km).",
        precursor_requirements=[],
        default_pixel_type="float",
        generation_functions=_single_step(
            "scenario.import_geotiff",
            "Import an existing DEM into the scenario.",
            source_path="path",
        ),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("dem", "digital elevation model", "elevation raster", "elevation map"),
    ),
    ProductTypeSpec(
        product_type="hillshade_raster",
        description="Shaded relief image to visualize elevation changes.",
        precursor_requirements=["dem"],
        default_pixel_type="byte",
        generation_functions=_single_step("raster.calculate.hillshade", "Generate hillshade from a DEM.", dem="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        canonical_recipe_ids=("hillshade_from_dem_v1",),
        reuse_keys=("scenario_id", "source_product_id", "crs", "resolution", "parameter_hash"),
        noun_phrase_aliases=("hillshade", "hillshade raster", "shaded relief", "shaded relief map"),
    ),
    ProductTypeSpec(
        product_type="slope_raster",
        description="Slope raster expressed in units of degrees.",
        precursor_requirements=["dem"],
        default_pixel_type="float",
        generation_functions=_single_step("raster.calculate.slope", "Generate slope from a DEM.", dem="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        canonical_recipe_ids=("slope_from_dem_v1",),
        reuse_keys=("scenario_id", "source_product_id", "crs", "resolution", "parameter_hash"),
        noun_phrase_aliases=("slope", "slope raster", "slope map"),
    ),
    ProductTypeSpec(
        product_type="aspect_raster",
        description="Downhill direction in degrees (0=north, 90=east, ...).",
        precursor_requirements=["dem"],
        default_pixel_type="float",
        generation_functions=_single_step("raster.calculate.aspect", "Generate aspect from a DEM.", dem="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        canonical_recipe_ids=("aspect_from_dem_v1",),
        reuse_keys=("scenario_id", "source_product_id", "crs", "resolution", "parameter_hash"),
        noun_phrase_aliases=("aspect", "aspect raster", "aspect map"),
    ),
    ProductTypeSpec(
        product_type="ruggedness_raster",
        description="Elevation difference between central and surrounding pixels.",
        precursor_requirements=["dem"],
        default_pixel_type="float",
        generation_functions=_single_step("raster.calculate.ruggedness", "Generate ruggedness from a DEM.", dem="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("ruggedness", "ruggedness raster", "ruggedness map"),
    ),
    ProductTypeSpec(
        product_type="tpi_raster",
        description="Elevation difference between central and the mean of the surrounding pixels.",
        precursor_requirements=["dem"],
        default_pixel_type="float",
        generation_functions=_single_step("raster.calculate.tpi", "Generate topographic position index from a DEM.", dem="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("tpi", "topographic position index", "tpi raster"),
    ),
    ProductTypeSpec(
        product_type="roughness_raster",
        description="Max elevation difference between central pixel and any of the surrounding pixels.",
        precursor_requirements=["dem"],
        default_pixel_type="float",
        generation_functions=_single_step("raster.calculate.roughness", "Generate roughness from a DEM.", dem="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("roughness", "roughness raster", "roughness map"),
    ),
    ProductTypeSpec(
        product_type="threshold_mask",
        description="Boolean raster generated by comparing another raster with a fixed threshold.",
        precursor_requirements=["source_raster"],
        default_pixel_type="boolean",
        generation_functions=_single_step(
            "raster.calculate.threshold_mask",
            "Generate a threshold mask from a source raster.",
            source_raster="product_ref",
            threshold="number",
            operator="comparison",
        ),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        canonical_recipe_ids=("threshold_mask_v1",),
        required_parameters=("operator", "threshold"),
        reuse_keys=("scenario_id", "source_product_id", "parameter_hash"),
        noun_phrase_aliases=("threshold mask", "mask where", "mask of"),
    ),
    ProductTypeSpec(
        product_type="combined_mask",
        description="Boolean raster generated by AND'ing or OR'ing other rasters.",
        precursor_requirements=["mask_raster"],
        default_pixel_type="boolean",
        generation_functions=_single_step("raster.calculate.combined_mask", "Combine multiple masks.", masks="product_ref_list"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("combined mask",),
    ),
    ProductTypeSpec(
        product_type="selection_mask",
        description="Boolean raster indicating pixels with a particular property.",
        precursor_requirements=["source_raster"],
        default_pixel_type="boolean",
        generation_functions=_single_step("raster.calculate.selection_mask", "Generate a selection mask from a predicate.", source_raster="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("selection mask",),
    ),
    ProductTypeSpec(
        product_type="boolean_raster",
        description="Generic boolean raster with zero vs non-zero semantics.",
        precursor_requirements=["source_raster"],
        default_pixel_type="boolean",
        generation_functions=_single_step("raster.calculate.boolean", "Generate a boolean raster.", source_raster="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("boolean raster", "binary raster"),
    ),
    ProductTypeSpec(
        product_type="region_labels",
        description="Integer raster where equal values indicate regions within the raster.",
        precursor_requirements=["mask_raster"],
        default_pixel_type="integer",
        generation_functions=_single_step("raster.calculate.region_labels", "Label connected regions in a mask.", mask_raster="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("region labels", "labeled regions raster"),
    ),
    ProductTypeSpec(
        product_type="region_sizes",
        description="Integer raster where values are the size of each region.",
        precursor_requirements=["region_labels"],
        default_pixel_type="integer",
        generation_functions=_single_step("raster.calculate.region_sizes", "Measure labeled region sizes.", region_labels="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("region sizes", "region size raster"),
    ),
    ProductTypeSpec(
        product_type="region_borders",
        description="Boolean raster where true values represent edge pixels in a region.",
        precursor_requirements=["region_labels"],
        default_pixel_type="boolean",
        generation_functions=_single_step("raster.calculate.region_borders", "Extract borders from region labels.", region_labels="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("region borders", "region border raster"),
    ),
    ProductTypeSpec(
        product_type="illumination_raster",
        description="Pixel values indicate fraction of full sun at a specific time.",
        precursor_requirements=["dem"],
        default_pixel_type="byte",
        generation_functions=_single_step("jobs.generate_illumination", "Generate illumination from DEM and time inputs.", dem="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("illumination", "illumination raster", "sun fraction"),
    ),
    ProductTypeSpec(
        product_type="earth_visibility_raster",
        description="Boolean raster indicating whether Earth is visible at a specific time.",
        precursor_requirements=["dem"],
        default_pixel_type="boolean",
        generation_functions=_single_step("jobs.generate_earth_visibility", "Generate Earth visibility from DEM and time inputs.", dem="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("earth visibility", "earth visibility raster"),
    ),
    ProductTypeSpec(
        product_type="duration_raster",
        description="Float raster indicating a time duration of some property at each pixel.",
        precursor_requirements=["source_raster"],
        default_pixel_type="float",
        generation_functions=_single_step("raster.calculate.duration", "Reduce temporal property duration to a raster.", source_raster="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("duration raster", "duration map"),
    ),
    ProductTypeSpec(
        product_type="psr_raster",
        description="Boolean raster where non-zero values indicate permanent shadow.",
        precursor_requirements=["illumination_raster"],
        default_pixel_type="boolean",
        generation_functions=_single_step("raster.calculate.psr", "Generate permanent shadow mask.", illumination_raster="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("permanent shadow", "psr", "psr raster"),
    ),
    ProductTypeSpec(
        product_type="sun_center_above_horizon",
        description="Angle in degrees of the sun's center above the horizon.",
        precursor_requirements=["dem"],
        default_pixel_type="float",
        generation_functions=_single_step("jobs.generate_sun_center_above_horizon", "Generate sun-center-above-horizon raster.", dem="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("sun center above horizon",),
    ),
    ProductTypeSpec(
        product_type="sun_bottom_above_horizon",
        description="Angle in degrees of the sun's bottom limb above the horizon.",
        precursor_requirements=["dem"],
        default_pixel_type="float",
        generation_functions=_single_step("jobs.generate_sun_bottom_above_horizon", "Generate sun-bottom-above-horizon raster.", dem="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("sun bottom above horizon",),
    ),
    ProductTypeSpec(
        product_type="earth_center_above_horizon",
        description="Angle in degrees of Earth's center above the horizon.",
        precursor_requirements=["dem"],
        default_pixel_type="float",
        generation_functions=_single_step("jobs.generate_earth_center_above_horizon", "Generate Earth-center-above-horizon raster.", dem="product_ref"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("earth center above horizon",),
    ),
    ProductTypeSpec(
        product_type="station_over_horizon_deg",
        description="Angle in degrees of a specific ground station above the horizon.",
        precursor_requirements=["dem"],
        default_pixel_type="float",
        generation_functions=_single_step("jobs.generate_station_over_horizon_deg", "Generate station-over-horizon raster.", dem="product_ref", station="string"),
        generation_time_estimator_name="default_runtime_estimator",
        name_strategy_name="canonical_name_from_product_type",
        noun_phrase_aliases=("station over horizon", "station over horizon deg"),
    ),
)


PRODUCT_TYPE_DICT: dict[str, ProductTypeSpec] = {item.product_type: item for item in PRODUCT_TYPE_SPECS}

DEFAULT_PRODUCT_FILENAMES_BY_TYPE: dict[str, tuple[str, ...]] = {
    "dem": ("primary_dem.tif", "dem.tif"),
    "hillshade_raster": ("hillshade.tif",),
    "slope_raster": ("slope.tif",),
    "aspect_raster": ("aspect.tif",),
    "ruggedness_raster": ("ruggedness.tif", "tri.tif"),
    "tpi_raster": ("tpi.tif",),
    "roughness_raster": ("roughness.tif",),
    "threshold_mask": ("threshold_mask.tif",),
    "combined_mask": ("combined_mask.tif",),
    "selection_mask": ("selection_mask.tif",),
    "boolean_raster": ("boolean_raster.tif",),
    "region_labels": ("region_labels.tif",),
    "region_sizes": ("region_sizes.tif",),
    "region_borders": ("region_borders.tif",),
    "illumination_raster": ("illumination.tif", "sun_fraction.tif"),
    "earth_visibility_raster": ("earth_visibility.tif", "earth_visibility_raster.tif"),
    "duration_raster": ("duration.tif",),
    "psr_raster": ("psr.tif", "psr_raster.tif"),
    "sun_center_above_horizon": ("sun_center_above_horizon.tif",),
    "sun_bottom_above_horizon": ("sun_bottom_above_horizon.tif",),
    "earth_center_above_horizon": ("earth_center_above_horizon.tif",),
    "station_over_horizon_deg": ("station_over_horizon_deg.tif",),
}


def default_filenames_for_product_type(product_type: str) -> tuple[str, ...]:
    key = str(product_type or "").strip()
    values = DEFAULT_PRODUCT_FILENAMES_BY_TYPE.get(key)
    if values is None:
        raise KeyError(f"Unknown product_type: {product_type}")
    return values


def validate_product_type_dictionary() -> None:
    for label, spec in PRODUCT_TYPE_DICT.items():
        if not label:
            raise ValueError("product_type_dictionary_invalid_label")
        if not spec.description.strip():
            raise ValueError(f"product_type_dictionary_missing_description:{label}")
        if spec.default_pixel_type not in {"boolean", "byte", "integer", "float"}:
            raise ValueError(f"product_type_dictionary_invalid_pixel_type:{label}")
        if not spec.generation_functions:
            raise ValueError(f"product_type_dictionary_missing_generation_functions:{label}")
        if not spec.generation_time_estimator_name.strip():
            raise ValueError(f"product_type_dictionary_missing_estimator:{label}")
        if not spec.name_strategy_name.strip():
            raise ValueError(f"product_type_dictionary_missing_name_strategy:{label}")
        if spec.canonical_recipe_ids and not all(str(item).strip() for item in spec.canonical_recipe_ids):
            raise ValueError(f"product_type_dictionary_invalid_recipe_ids:{label}")
        if spec.required_parameters and not all(str(item).strip() for item in spec.required_parameters):
            raise ValueError(f"product_type_dictionary_invalid_required_parameters:{label}")
        if spec.noun_phrase_aliases and not all(str(item).strip() for item in spec.noun_phrase_aliases):
            raise ValueError(f"product_type_dictionary_invalid_noun_phrase_aliases:{label}")
        default_filenames = DEFAULT_PRODUCT_FILENAMES_BY_TYPE.get(label)
        if default_filenames is None:
            raise ValueError(f"product_type_dictionary_missing_default_filenames:{label}")
        if not default_filenames or not all(str(item).strip() for item in default_filenames):
            raise ValueError(f"product_type_dictionary_invalid_default_filenames:{label}")


validate_product_type_dictionary()
