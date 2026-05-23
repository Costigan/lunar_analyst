from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from backend.services.assistant.canonical_recipe_catalog import RecipeTemplateSpec, load_recipe_catalog, recipe_ids_for_product_type
from backend.services.assistant.product_type_dictionary import PRODUCT_TYPE_DICT, default_filenames_for_product_type
from backend.services.assistant.prompt_classifier import SegmentClassification


@dataclass(frozen=True)
class AvailableProduct:
    product_id: str
    kind: str
    subkind: str
    filename: str | None = None
    references: tuple[str, ...] = ()
    relative_paths: tuple[str, ...] = ()

    def matches(self, query: str) -> bool:
        needle = _normalize_ref(query)
        if not needle:
            return False
        candidates = {
            _normalize_ref(self.product_id),
            _normalize_ref(self.kind),
            _normalize_ref(self.subkind),
            _normalize_ref(self.filename or ""),
        }
        for item in self.references:
            candidates.add(_normalize_ref(item))
        for rel in self.relative_paths:
            candidates.add(_normalize_ref(rel))
            candidates.add(_normalize_ref(Path(rel).name))
            candidates.add(_normalize_ref(Path(rel).stem))
        return needle in candidates

    @property
    def preferred_relative_path(self) -> str | None:
        for rel in self.relative_paths:
            cleaned = str(rel).strip()
            if cleaned:
                return cleaned
        if self.filename:
            cleaned = str(self.filename).strip()
            if cleaned:
                return cleaned
        return None

    @property
    def preferred_stem(self) -> str:
        rel = self.preferred_relative_path
        if rel:
            return Path(rel).stem
        return self.product_id


@dataclass(frozen=True)
class CreateProductStep:
    recipe_id: str
    tool_name: str
    tool_args: dict[str, Any]
    output_relative_path: str


@dataclass(frozen=True)
class CreateProductPlan:
    recipe_id: str
    requested_product_type: str
    prerequisite_count: int
    steps: list[CreateProductStep]
    output_relative_path: str


@dataclass(frozen=True)
class CreateProductReuse:
    output_relative_path: str
    product_id: str
    message: str


@dataclass(frozen=True)
class CreateProductBlock:
    reason_code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _ThresholdPredicate:
    operator: str
    label: str
    threshold: float


class CreateProductPlanner:
    def __init__(self) -> None:
        catalog = load_recipe_catalog()
        self._recipes_by_id: dict[str, RecipeTemplateSpec] = {spec.recipe_id: spec for spec in catalog}

    def plan(
        self,
        *,
        classification: SegmentClassification,
        scenario_id: str | None,
        scenario_dir: str | Path | None = None,
        available_products: list[AvailableProduct] | None = None,
        allow_reuse: bool = True,
    ) -> CreateProductPlan | CreateProductReuse | CreateProductBlock:
        if classification.segment_class != "create_product":
            return self.build_structured_block(
                reason_code="product_request_unparseable",
                message="The segment is not a structured create_product request.",
            )
        scenario_key = str(scenario_id or "").strip()
        if not scenario_key:
            return self.build_structured_block(
                reason_code="missing_scenario",
                message="I couldn't create the requested product because no scenario is active.",
            )
        resolved_inventory = self._resolve_available_products(
            scenario_dir=scenario_dir,
            available_products=available_products,
        )

        selection = self.select_recipe_for_classification(classification=classification)
        if isinstance(selection, CreateProductBlock):
            return selection
        recipe = selection

        prerequisite_inputs = self.expand_prerequisites(
            recipe=recipe,
            classification=classification,
            available_products=resolved_inventory,
        )
        if isinstance(prerequisite_inputs, CreateProductBlock):
            return prerequisite_inputs

        parameters = self.resolve_required_parameters(recipe=recipe, classification=classification)
        if isinstance(parameters, CreateProductBlock):
            return parameters

        compiled = self.compile_recipe_step_to_tool_call(
            recipe=recipe,
            scenario_id=scenario_key,
            classification=classification,
            prerequisite_inputs=prerequisite_inputs,
            parameters=parameters,
        )
        if isinstance(compiled, CreateProductBlock):
            return compiled

        fingerprint = self.compute_reuse_key_fingerprint(
            recipe=recipe,
            scenario_id=scenario_key,
            prerequisite_inputs=prerequisite_inputs,
            parameters=parameters,
        )
        if allow_reuse:
            reuse = self.find_reusable_product(
                recipe=recipe,
                output_relative_path=compiled.output_relative_path,
                available_products=resolved_inventory,
                fingerprint=fingerprint,
            )
            if reuse is not None:
                return reuse

        return CreateProductPlan(
            recipe_id=recipe.recipe_id,
            requested_product_type=recipe.product_type,
            prerequisite_count=sum(
                1
                for key, value in prerequisite_inputs.items()
                if key in {"dem", "source_raster"} and isinstance(value, AvailableProduct)
            ),
            steps=[compiled],
            output_relative_path=compiled.output_relative_path,
        )

    def discover_available_products(self, *, scenario_dir: str | Path) -> list[AvailableProduct]:
        scenario_path = Path(scenario_dir).resolve()
        if not scenario_path.is_dir():
            return []
        file_index: dict[str, list[str]] = {}
        for file_path in scenario_path.rglob("*"):
            if not file_path.is_file():
                continue
            rel = str(file_path.relative_to(scenario_path)).replace("\\", "/")
            file_index.setdefault(file_path.name.lower(), []).append(rel)

        inventory: list[AvailableProduct] = []
        for product_type in sorted(PRODUCT_TYPE_DICT.keys()):
            seen_paths: set[str] = set()
            matched_paths: list[str] = []
            for candidate in default_filenames_for_product_type(product_type):
                key = str(candidate).strip().lower()
                for rel in sorted(file_index.get(key, [])):
                    if rel in seen_paths:
                        continue
                    seen_paths.add(rel)
                    matched_paths.append(rel)
            for index, rel in enumerate(matched_paths, start=1):
                path = Path(rel)
                product_id = product_type if index == 1 else f"{product_type}:{index}"
                refs = {
                    product_id,
                    product_type,
                    path.name,
                    path.stem,
                }
                if product_type == "dem":
                    refs.update({"dem", "primary_dem"})
                inventory.append(
                    AvailableProduct(
                        product_id=product_id,
                        kind="raster",
                        subkind=product_type,
                        filename=path.name,
                        references=tuple(sorted(item for item in refs if item)),
                        relative_paths=(rel,),
                    )
                )
        return inventory

    def _resolve_available_products(
        self,
        *,
        scenario_dir: str | Path | None,
        available_products: list[AvailableProduct] | None,
    ) -> list[AvailableProduct]:
        if scenario_dir is not None:
            return self.discover_available_products(scenario_dir=scenario_dir)
        return list(available_products or [])

    def select_recipe_for_classification(
        self,
        *,
        classification: SegmentClassification,
    ) -> RecipeTemplateSpec | CreateProductBlock:
        product_type = str(classification.product_type or "").strip()
        if not product_type:
            return self.build_structured_block(
                reason_code="product_request_unparseable",
                message="I couldn't determine which canonical product type you requested.",
            )
        if product_type not in PRODUCT_TYPE_DICT:
            return self.build_structured_block(
                reason_code="unknown_canonical_product_type",
                message=f"`{product_type}` is not in the canonical product dictionary.",
                details={"product_type": product_type},
            )
        recipe_ids = recipe_ids_for_product_type(product_type)
        if not recipe_ids:
            return self.build_structured_block(
                reason_code="no_supported_recipe",
                message=f"No deterministic recipe is configured for `{product_type}` yet.",
                details={"product_type": product_type},
            )
        recipe_id = recipe_ids[0]
        recipe = self._recipes_by_id.get(recipe_id)
        if recipe is None:
            return self.build_structured_block(
                reason_code="execution_ref_unavailable",
                message=f"Recipe `{recipe_id}` is missing from the recipe catalog.",
                details={"recipe_id": recipe_id, "product_type": product_type},
            )
        return recipe

    def expand_prerequisites(
        self,
        *,
        recipe: RecipeTemplateSpec,
        classification: SegmentClassification,
        available_products: list[AvailableProduct],
    ) -> dict[str, Any] | CreateProductBlock:
        resolved: dict[str, Any] = {}
        if "dem" in recipe.requires:
            dem = self._resolve_dem(classification=classification, available_products=available_products)
            if isinstance(dem, CreateProductBlock):
                return dem
            resolved["dem"] = dem
        if "source_raster" in recipe.requires:
            source_product = self._resolve_threshold_source(
                classification=classification,
                available_products=available_products,
            )
            if isinstance(source_product, CreateProductBlock):
                return source_product
            if source_product is not None:
                resolved["source_raster"] = source_product
            elif "dem" in resolved:
                resolved["source_raster"] = None
            else:
                dem = self._resolve_dem(classification=classification, available_products=available_products)
                if isinstance(dem, CreateProductBlock):
                    return dem
                resolved["dem"] = dem
                resolved["source_raster"] = None
        return resolved

    def resolve_required_parameters(
        self,
        *,
        recipe: RecipeTemplateSpec,
        classification: SegmentClassification,
    ) -> dict[str, Any] | CreateProductBlock:
        if not recipe.required_parameters:
            return {}
        if recipe.product_type == "threshold_mask":
            predicate = _extract_threshold_predicate(classification.text)
            if predicate is None:
                return self.build_structured_block(
                    reason_code="missing_required_product_parameter",
                    message=(
                        "I couldn't determine the threshold comparison for this mask request. "
                        "Specify something like `slope <= 5` or `roughness < 2`."
                    ),
                )
            return {"operator": predicate.operator, "threshold": predicate.threshold, "predicate": predicate}
        return self.build_structured_block(
            reason_code="missing_required_product_parameter",
            message=f"Recipe `{recipe.recipe_id}` requires parameters that are not resolved by this planner.",
            details={"required_parameters": list(recipe.required_parameters)},
        )

    def compile_recipe_step_to_tool_call(
        self,
        *,
        recipe: RecipeTemplateSpec,
        scenario_id: str,
        classification: SegmentClassification,
        prerequisite_inputs: dict[str, Any],
        parameters: dict[str, Any],
    ) -> CreateProductStep | CreateProductBlock:
        if recipe.execution_ref != "raster.calculate":
            return self.build_structured_block(
                reason_code="execution_ref_unavailable",
                message=f"Unsupported execution reference `{recipe.execution_ref}` for deterministic create_product.",
                details={"recipe_id": recipe.recipe_id},
            )

        if recipe.product_type == "threshold_mask":
            predicate = parameters.get("predicate")
            assert isinstance(predicate, _ThresholdPredicate)
            source_raster = prerequisite_inputs.get("source_raster")
            if isinstance(source_raster, AvailableProduct):
                expression = recipe.expression_template.format(
                    source_raster="source",
                    operator=parameters["operator"],
                    threshold=parameters["threshold"],
                )
                inputs = {"source": {"product_id": source_raster.product_id}}
                output_relative_path = _threshold_output_name(
                    source_name=source_raster.preferred_stem,
                    predicate=predicate,
                )
            else:
                dem = prerequisite_inputs.get("dem")
                if not isinstance(dem, AvailableProduct):
                    return self.build_structured_block(
                        reason_code="missing_prerequisite_product",
                        message="I couldn't resolve a source raster for threshold mask generation.",
                        details={"recipe_id": recipe.recipe_id},
                    )
                expression = f"slope(dem) {parameters['operator']} {parameters['threshold']}"
                inputs = {"dem": {"product_id": dem.product_id}}
                output_relative_path = _threshold_output_name(source_name="slope", predicate=predicate)
        else:
            dem = prerequisite_inputs.get("dem")
            if not isinstance(dem, AvailableProduct):
                return self.build_structured_block(
                    reason_code="missing_prerequisite_product",
                    message="I couldn't find a DEM prerequisite for this canonical product.",
                    details={"recipe_id": recipe.recipe_id, "required": list(recipe.requires)},
                )
            expression = recipe.expression_template
            inputs = {"dem": {"product_id": dem.product_id}}
            output_relative_path = recipe.default_output_relative_path

        return CreateProductStep(
            recipe_id=recipe.recipe_id,
            tool_name="raster.calculate",
            tool_args={
                "scenario_id": scenario_id,
                "expression": expression,
                "inputs": inputs,
                "output_relative_path": output_relative_path,
                "overwrite_mode": "ask",
            },
            output_relative_path=output_relative_path,
        )

    def compute_reuse_key_fingerprint(
        self,
        *,
        recipe: RecipeTemplateSpec,
        scenario_id: str,
        prerequisite_inputs: dict[str, Any],
        parameters: dict[str, Any],
    ) -> str:
        source_product = prerequisite_inputs.get("source_raster") or prerequisite_inputs.get("dem")
        source_product_id = source_product.product_id if isinstance(source_product, AvailableProduct) else None
        payload = {
            "recipe_id": recipe.recipe_id,
            "product_type": recipe.product_type,
            "scenario_id": scenario_id,
            "source_product_id": source_product_id,
            "parameters": {k: v for k, v in parameters.items() if k != "predicate"},
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def find_reusable_product(
        self,
        *,
        recipe: RecipeTemplateSpec,
        output_relative_path: str,
        available_products: list[AvailableProduct],
        fingerprint: str,
    ) -> CreateProductReuse | None:
        target = _normalize_ref(output_relative_path)
        for product in available_products:
            if any(_normalize_ref(rel) == target for rel in product.relative_paths):
                return CreateProductReuse(
                    output_relative_path=output_relative_path,
                    product_id=product.product_id,
                    message=f"Using existing product `{output_relative_path}` instead of recomputing it.",
                )
            if any(fingerprint in str(ref) for ref in product.references):
                return CreateProductReuse(
                    output_relative_path=output_relative_path,
                    product_id=product.product_id,
                    message=(
                        f"Using existing product `{product.product_id}` based on matching reuse keys "
                        f"for recipe `{recipe.recipe_id}`."
                    ),
                )
        return None

    @staticmethod
    def build_structured_block(
        *,
        reason_code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> CreateProductBlock:
        return CreateProductBlock(
            reason_code=reason_code,
            message=message,
            details=dict(details or {}),
        )

    def _resolve_dem(
        self,
        *,
        classification: SegmentClassification,
        available_products: list[AvailableProduct],
    ) -> AvailableProduct | CreateProductBlock:
        requested = [item for item in classification.sources if str(item).strip()]
        dem_candidates = [
            item
            for item in available_products
            if item.subkind == "dem" or item.matches("primary_dem") or item.matches("dem")
        ]
        if requested:
            filtered = [
                item
                for item in dem_candidates
                if any(item.matches(ref) for ref in requested)
            ]
            if filtered:
                dem_candidates = filtered
        primary = [item for item in dem_candidates if item.matches("primary_dem")]
        if len(primary) == 1:
            return primary[0]
        if len(dem_candidates) == 1:
            return dem_candidates[0]
        if not dem_candidates:
            return self.build_structured_block(
                reason_code="missing_prerequisite_product",
                message="I couldn't find a DEM source product in the active scenario.",
            )
        return self.build_structured_block(
            reason_code="missing_prerequisite_product",
            message="I found multiple DEM-like products. Specify which DEM to use.",
        )

    def _resolve_threshold_source(
        self,
        *,
        classification: SegmentClassification,
        available_products: list[AvailableProduct],
    ) -> AvailableProduct | CreateProductBlock | None:
        lowered = str(classification.text or "").lower()
        requested = [item for item in classification.sources if str(item).strip()]
        for ref in requested:
            matches = [item for item in available_products if item.matches(ref)]
            if len(matches) == 1:
                if _looks_like_dem(matches[0]) and "slope" in lowered:
                    return None
                return matches[0]
            if len(matches) > 1:
                return self.build_structured_block(
                    reason_code="missing_prerequisite_product",
                    message=f"I found multiple products matching `{ref}`. Specify the source raster more precisely.",
                )
        if "slope" in lowered:
            slope_matches = [
                item
                for item in available_products
                if item.matches("slope") or item.subkind == "slope_raster"
            ]
            if len(slope_matches) == 1:
                return slope_matches[0]
            if len(slope_matches) > 1:
                return self.build_structured_block(
                    reason_code="missing_prerequisite_product",
                    message="I found multiple slope rasters. Specify which one to threshold.",
                )
            return None
        if requested:
            return self.build_structured_block(
                reason_code="missing_prerequisite_product",
                message="I couldn't resolve the requested source raster for the threshold mask.",
            )
        return self.build_structured_block(
            reason_code="missing_prerequisite_product",
            message="I couldn't determine which source raster to threshold.",
        )


def _looks_like_dem(product: AvailableProduct) -> bool:
    return product.subkind == "dem" or product.matches("primary_dem") or product.matches("dem")


def _normalize_ref(value: str) -> str:
    text = str(value or "").strip().lower().replace("\\", "/")
    text = re.sub(r"\s+", " ", text)
    return text


def _extract_threshold_predicate(text: str) -> _ThresholdPredicate | None:
    direct_patterns = (
        (r"(?:<=|≤)\s*([0-9]+(?:\.[0-9]+)?)", "<=", "le"),
        (r"(?:>=|≥)\s*([0-9]+(?:\.[0-9]+)?)", ">=", "ge"),
        (r"(?<![<>])<\s*([0-9]+(?:\.[0-9]+)?)", "<", "lt"),
        (r"(?<![<>])>\s*([0-9]+(?:\.[0-9]+)?)", ">", "gt"),
    )
    for pattern, operator, label in direct_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _ThresholdPredicate(operator=operator, label=label, threshold=float(match.group(1)))
    phrase_patterns = (
        (r"\b(?:at most|no more than|less than or equal to)\s*([0-9]+(?:\.[0-9]+)?)", "<=", "le"),
        (r"\b(?:under|less than)\s*([0-9]+(?:\.[0-9]+)?)", "<", "lt"),
        (r"\b(?:at least|greater than or equal to)\s*([0-9]+(?:\.[0-9]+)?)", ">=", "ge"),
        (r"\b(?:more than|greater than|over)\s*([0-9]+(?:\.[0-9]+)?)", ">", "gt"),
        (r"\b([0-9]+(?:\.[0-9]+)?)\s*degrees?\s*or\s*less\b", "<=", "le"),
        (r"\b([0-9]+(?:\.[0-9]+)?)\s*degrees?\s*or\s*more\b", ">=", "ge"),
    )
    lowered = str(text or "").lower()
    for pattern, operator, label in phrase_patterns:
        match = re.search(pattern, lowered, re.IGNORECASE)
        if match:
            return _ThresholdPredicate(operator=operator, label=label, threshold=float(match.group(1)))
    return None


def _threshold_output_name(*, source_name: str, predicate: _ThresholdPredicate) -> str:
    source = re.sub(r"[^a-z0-9]+", "_", str(source_name or "").strip().lower()).strip("_") or "mask"
    threshold_label = str(predicate.threshold).replace(".", "p")
    if "slope" in source:
        return f"{source}_{predicate.label}_{threshold_label}deg_mask.tif"
    return f"{source}_{predicate.label}_{threshold_label}_mask.tif"
