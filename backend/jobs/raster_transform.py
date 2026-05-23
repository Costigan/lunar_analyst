from __future__ import annotations

import ast
import difflib
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from typing import Callable
from typing import Iterator

import numpy as np
import rasterio

from backend.core.config import load_app_config
from backend.core.config import resolve_config_path
from backend.core.config import resolve_config_relative_path
from backend.jobs.map_algebra import (
    AlignedRaster,
    MapAlgebraError,
    TargetGrid,
    align_inputs_to_target as _align_inputs_to_target,
    load_target_grid_from_dem as _load_target_grid_from_dem,
)
from backend.notebook.runtime import get_context
from backend.notebook.runtime import infer_local_scenario_identity_and_root
from backend.notebook.runtime import is_running_under_job_runner


MAX_SCRIPT_CHARS = 8192
MAX_AST_DEPTH = 96
MAX_AST_NODES = 2048
DEFAULT_MAX_ESTIMATED_WORKING_SET_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_TEMPORAL_FULL_EXTENT_BYTES = 768 * 1024 * 1024
DEFAULT_MAX_TEMPORAL_TILE_BYTES = 768 * 1024 * 1024
DEFAULT_NOTEBOOK_SCENARIO_PARENT_DIR = "scenarios"

TEMPORAL_REDUCERS = {"min", "max", "avg", "std"}
NODATA_FUNCTION_ALIASES = frozenset({"nodata", "nan", "null"})
SAFE_CALLS = {"where", "slope", "aspect", "hillshade", *TEMPORAL_REDUCERS, *NODATA_FUNCTION_ALIASES}
SAFE_METADATA_NAMES = {"time_step_hours"}
SAFE_FACADE_NAMES = frozenset({"np"})
SAFE_FACADE_CALLS = {"np.where": "where"}
_HORIZON_FILE_RE = re.compile(r"^horizon_(\d{5})_(\d{5})_(\d{3})\.(?:c)?bin$", re.IGNORECASE)


class RasterTransformError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        status_code: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = int(status_code)


class _SealedNumpyFacade:
    __slots__ = ("_where_fn",)

    def __init__(self, where_fn: Callable[[Any, Any, Any], Any]) -> None:
        self._where_fn = where_fn

    def where(self, condition: Any, x_value: Any, y_value: Any) -> Any:
        return self._where_fn(condition, x_value, y_value)


@dataclass(frozen=True)
class ParsedScript:
    tree: ast.Module
    normalized_script: str
    script_form: str
    used_variables: set[str]
    used_functions: set[str]
    used_operators: set[str]
    assigned_variables: set[str]
    intermediate_variable_count: int


@dataclass(frozen=True)
class RasterTransformPlan:
    execution_strategy: str
    estimated_working_set_bytes: int
    principal_reason: str
    width: int
    height: int
    dtype_bytes: int
    static_input_count: int
    temporal_input_count: int
    intermediate_array_count: int
    time_count: int
    spatial_partitioning: str
    time_partitioning: str
    spatial_halo_pixels: int
    patch_width: int
    patch_height: int

    def model_dump(self) -> dict[str, Any]:
        return {
            "execution_strategy": self.execution_strategy,
            "estimated_working_set_bytes": int(self.estimated_working_set_bytes),
            "principal_reason": self.principal_reason,
            "width": int(self.width),
            "height": int(self.height),
            "dtype_bytes": int(self.dtype_bytes),
            "static_input_count": int(self.static_input_count),
            "temporal_input_count": int(self.temporal_input_count),
            "intermediate_array_count": int(self.intermediate_array_count),
            "time_count": int(self.time_count),
            "spatial_partitioning": self.spatial_partitioning,
            "time_partitioning": self.time_partitioning,
            "spatial_halo_pixels": int(self.spatial_halo_pixels),
            "patch_width": int(self.patch_width),
            "patch_height": int(self.patch_height),
        }


@dataclass(frozen=True)
class GridSpec:
    crs: Any
    transform: Any
    width: int
    height: int
    time_count: int | None = None


@dataclass(frozen=True)
class ExecutionHints:
    spatial_partitioning: str = "auto"
    time_partitioning: str = "auto"
    spatial_halo_pixels: int = 0


@dataclass
class _ValidationState:
    input_names: set[str]
    metadata_names: set[str]
    available_names: set[str] = field(default_factory=set)
    assigned_variables: set[str] = field(default_factory=set)
    used_input_variables: set[str] = field(default_factory=set)
    used_functions: set[str] = field(default_factory=set)
    used_operators: set[str] = field(default_factory=set)


@dataclass
class _NotebookRuntimeContext:
    scenario_id: str
    scenario_root: Path
    dem_path: Path
    target_grid: TargetGrid
    aligned_cache: dict[str, np.ndarray] = field(default_factory=dict)


@dataclass(frozen=True)
class _RasterNode:
    kind: str
    inputs: tuple[Any, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)
    execution_hints: ExecutionHints = field(default_factory=ExecutionHints)
    name: str | None = None


class Raster:
    __array_priority__ = 1000

    def __init__(
        self,
        *,
        node: _RasterNode,
        grid: GridSpec | None = None,
        dtype: str = "float32",
        name: str | None = None,
        execution_hints: ExecutionHints | None = None,
    ) -> None:
        self._node = node
        self.grid = grid
        self.dtype = str(dtype)
        self.name = name
        self.execution_hints = execution_hints or node.execution_hints

    def read(self, window: tuple[slice, slice] | None = None) -> Any:
        array = self.materialize()
        if window is None:
            return array
        return array[window]

    def blocks(self, plan: Any = None) -> Iterator[Any]:
        _ = plan
        yield self.materialize()

    def materialize(self) -> Any:
        context = _resolve_notebook_runtime_context()
        return _materialize_raster_node(self._node, context=context)

    def planner_info(self) -> dict[str, Any]:
        return {
            "operation_id": self._node.kind,
            "name": self.name,
            "execution_hints": {
                "spatial_partitioning": self.execution_hints.spatial_partitioning,
                "time_partitioning": self.execution_hints.time_partitioning,
                "spatial_halo_pixels": int(self.execution_hints.spatial_halo_pixels),
            },
            "grid": None
            if self.grid is None
            else {
                "width": int(self.grid.width),
                "height": int(self.grid.height),
                "time_count": self.grid.time_count,
            },
        }

    def _binary(self, op_name: str, other: Any) -> Raster:
        return _wrap_raster(_RasterNode(kind=op_name, inputs=(self, other)))

    def _rbinary(self, op_name: str, other: Any) -> Raster:
        return _wrap_raster(_RasterNode(kind=op_name, inputs=(other, self)))

    def __add__(self, other: Any) -> Raster:
        return self._binary("add", other)

    def __radd__(self, other: Any) -> Raster:
        return self._rbinary("add", other)

    def __sub__(self, other: Any) -> Raster:
        return self._binary("sub", other)

    def __rsub__(self, other: Any) -> Raster:
        return self._rbinary("sub", other)

    def __mul__(self, other: Any) -> Raster:
        return self._binary("mul", other)

    def __rmul__(self, other: Any) -> Raster:
        return self._rbinary("mul", other)

    def __truediv__(self, other: Any) -> Raster:
        return self._binary("div", other)

    def __rtruediv__(self, other: Any) -> Raster:
        return self._rbinary("div", other)

    def __pow__(self, other: Any) -> Raster:
        return self._binary("pow", other)

    def __rpow__(self, other: Any) -> Raster:
        return self._rbinary("pow", other)

    def __and__(self, other: Any) -> Raster:
        return self._binary("bitand", other)

    def __rand__(self, other: Any) -> Raster:
        return self._rbinary("bitand", other)

    def __or__(self, other: Any) -> Raster:
        return self._binary("bitor", other)

    def __ror__(self, other: Any) -> Raster:
        return self._rbinary("bitor", other)

    def __invert__(self) -> Raster:
        return _wrap_raster(_RasterNode(kind="invert", inputs=(self,)))

    def __neg__(self) -> Raster:
        return _wrap_raster(_RasterNode(kind="neg", inputs=(self,)))

    def __pos__(self) -> Raster:
        return _wrap_raster(_RasterNode(kind="pos", inputs=(self,)))

    def __lt__(self, other: Any) -> Raster:
        return self._binary("lt", other)

    def __le__(self, other: Any) -> Raster:
        return self._binary("lte", other)

    def __gt__(self, other: Any) -> Raster:
        return self._binary("gt", other)

    def __ge__(self, other: Any) -> Raster:
        return self._binary("gte", other)

    def __eq__(self, other: Any) -> Raster:  # type: ignore[override]
        return self._binary("eq", other)

    def __ne__(self, other: Any) -> Raster:  # type: ignore[override]
        return self._binary("neq", other)

    def __array_function__(
        self,
        func: Callable[..., Any],
        types: tuple[type[Any], ...],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        _ = types
        if func is np.where:
            if set(kwargs) - {"condition", "x", "y"}:
                return NotImplemented
            if kwargs:
                condition = kwargs.get("condition")
                x_value = kwargs.get("x")
                y_value = kwargs.get("y")
            else:
                if len(args) != 3:
                    return NotImplemented
                condition, x_value, y_value = args
            return _wrap_raster(
                _RasterNode(kind="where", inputs=(condition, x_value, y_value))
            )
        return NotImplemented


class RasterLet:
    def __init__(self, bindings: dict[str, Any]) -> None:
        self.bindings = dict(bindings)

    def eval(self, body: Callable[[Any], Any]) -> Any:
        env = SimpleNamespace()
        resolved: dict[str, Any] = {}
        for name, value in self.bindings.items():
            resolved_value = value(env) if callable(value) else value
            resolved[name] = resolved_value
            setattr(env, name, resolved_value)
        validate_bindings(resolved)
        return plan_result(body(env), resolved)


def raster_let(**bindings: Any) -> RasterLet:
    return RasterLet(bindings)


def validate_bindings(resolved: dict[str, Any]) -> None:
    for name, value in resolved.items():
        if not str(name).strip():
            raise ValueError("Binding names must be non-empty.")
        if not isinstance(value, Raster):
            raise TypeError(f"Binding '{name}' must resolve to a Raster.")


def plan_result(result: Any, resolved: dict[str, Any]) -> Any:
    _ = resolved
    return result


def compute_script_hash(normalized_script: str) -> str:
    return hashlib.sha256(normalized_script.encode("utf-8")).hexdigest()


def _with_repair_hint(error: RasterTransformError) -> RasterTransformError:
    details = dict(error.details)
    if error.code == "raster_transform_missing_result":
        details.setdefault(
            "hint",
            "Assign the final output to `result`, for example: result = where(mask, a, b).",
        )
    if error.code == "raster_transform_unknown_function":
        raw_name = str(details.get("function", "") or "").strip()
        if raw_name:
            suggestions = _nearest_allowed_functions(raw_name)
            if suggestions:
                details.setdefault("suggestions", suggestions)
        details.setdefault(
            "hint",
            "Use only sealed raster functions (for example where/slope/aspect/hillshade/min/max/avg/std/nodata) or np.where.",
        )
    if error.code == "raster_transform_disallowed_syntax" and str(details.get("syntax_node", "")).strip() == "BoolOp":
        details.setdefault(
            "hint",
            "Use elementwise logical operators with parentheses: `(cond_a) & (cond_b)` or `(cond_a) | (cond_b)`.",
        )
    if details == error.details:
        return error
    return RasterTransformError(
        code=error.code,
        message=error.message,
        details=details,
        status_code=error.status_code,
    )


def _nearest_allowed_functions(name: str) -> list[str]:
    options = sorted(set(SAFE_CALLS) | set(SAFE_FACADE_CALLS.keys()))
    return list(difflib.get_close_matches(str(name), options, n=3, cutoff=0.5))


def parse_validate_script(
    script: str,
    *,
    allowed_variables: set[str],
    metadata_names: set[str] | None = None,
) -> ParsedScript:
    raw = str(script or "").strip()
    if not raw:
        raise RasterTransformError(
            code="raster_transform_parse_error",
            message="script is required.",
        )
    if len(raw) > MAX_SCRIPT_CHARS:
        raise RasterTransformError(
            code="raster_transform_disallowed_syntax",
            message="Script exceeds maximum length.",
            details={"max_chars": MAX_SCRIPT_CHARS},
        )

    module: ast.Module
    script_form = "block"
    try:
        expression_tree = ast.parse(raw, mode="eval")
    except SyntaxError:
        expression_tree = None
    if expression_tree is not None:
        module = ast.Module(
            body=[
                ast.Assign(
                    targets=[ast.Name(id="result", ctx=ast.Store())],
                    value=expression_tree.body,
                )
            ],
            type_ignores=[],
        )
        module = ast.fix_missing_locations(module)
        script_form = "expression"
    else:
        try:
            module = ast.parse(raw, mode="exec")
        except SyntaxError as exc:
            raise RasterTransformError(
                code="raster_transform_parse_error",
                message="Invalid raster transform script syntax.",
                details={
                    "lineno": int(exc.lineno or 0),
                    "offset": int(exc.offset or 0),
                    "text": (exc.text or "").strip(),
                },
            ) from exc

    state = _ValidationState(
        input_names=set(allowed_variables),
        metadata_names=set(metadata_names or SAFE_METADATA_NAMES),
    )
    reserved_conflicts = sorted(name for name in state.input_names if name in SAFE_FACADE_NAMES)
    if reserved_conflicts:
        raise RasterTransformError(
            code="raster_transform_invalid_argument",
            message=f"Input bindings cannot use reserved facade name(s): {', '.join(reserved_conflicts)}",
            details={
                "reserved_names": reserved_conflicts,
                "hint": "Rename inputs that collide with reserved runtime facades (for example `np`).",
            },
        )
    state.available_names = set(state.input_names) | set(state.metadata_names)

    try:
        filtered_body: list[ast.stmt] = []
        for statement in module.body:
            if _is_ignored_numpy_import_statement(statement):
                continue
            _validate_statement(statement, state=state, depth=1)
            filtered_body.append(statement)

        module.body = filtered_body

        if "result" not in state.assigned_variables:
            raise RasterTransformError(
                code="raster_transform_missing_result",
                message="Statement blocks must assign the final output to `result`.",
            )
    except RasterTransformError as exc:
        raise _with_repair_hint(exc) from exc

    node_count = sum(1 for _ in ast.walk(module))
    if node_count > MAX_AST_NODES:
        raise RasterTransformError(
            code="raster_transform_disallowed_syntax",
            message="Script AST exceeds complexity limit.",
            details={"max_nodes": MAX_AST_NODES, "node_count": node_count},
        )

    normalized = ast.unparse(module) if hasattr(ast, "unparse") else raw
    intermediate_variables = {
        name
        for name in state.assigned_variables
        if name not in state.input_names and name != "result"
    }
    return ParsedScript(
        tree=module,
        normalized_script=normalized,
        script_form=script_form,
        used_variables=set(state.used_input_variables),
        used_functions=set(state.used_functions),
        used_operators=set(state.used_operators),
        assigned_variables=set(state.assigned_variables),
        intermediate_variable_count=len(intermediate_variables),
    )


def _is_ignored_numpy_import_statement(statement: ast.stmt) -> bool:
    if not isinstance(statement, ast.Import):
        return False
    if len(statement.names) != 1:
        return False
    alias = statement.names[0]
    imported_name = str(getattr(alias, "name", "") or "").strip()
    as_name = str(getattr(alias, "asname", "") or "").strip()
    return imported_name == "numpy" and as_name == "np"


def build_plan(
    *,
    parsed: ParsedScript,
    target_grid: TargetGrid,
    static_input_names: list[str],
    temporal_input_names: list[str],
    spatial_partitioning: str,
    time_partitioning: str,
    spatial_halo_pixels: int,
    patch_width: int,
    patch_height: int,
    time_count: int,
    dtype_bytes: int = 4,
) -> RasterTransformPlan:
    width = int(target_grid.width)
    height = int(target_grid.height)
    time_count_value = max(1, int(time_count))
    intermediate_count = max(1, int(parsed.intermediate_variable_count) + 1)

    if temporal_input_names and spatial_partitioning == "forbidden":
        strategy = "full_extent_temporal"
        principal_reason = "spatial_partitioning_forbidden"
        spatial_cells = width * height
    elif temporal_input_names:
        strategy = "tiled_temporal"
        principal_reason = "spatial_partitioning_allowed"
        spatial_cells = max(1, int(patch_width)) * max(1, int(patch_height))
    else:
        strategy = "full_extent_static"
        principal_reason = "non_temporal_transform"
        spatial_cells = width * height

    estimated = (
        len(static_input_names) * spatial_cells * dtype_bytes
        + (len(temporal_input_names) + intermediate_count + 1)
        * spatial_cells
        * max(1, time_count_value if temporal_input_names else 1)
        * dtype_bytes
    )
    return RasterTransformPlan(
        execution_strategy=strategy,
        estimated_working_set_bytes=int(estimated),
        principal_reason=principal_reason,
        width=width,
        height=height,
        dtype_bytes=int(dtype_bytes),
        static_input_count=len(static_input_names),
        temporal_input_count=len(temporal_input_names),
        intermediate_array_count=intermediate_count,
        time_count=time_count_value,
        spatial_partitioning=str(spatial_partitioning),
        time_partitioning=str(time_partitioning),
        spatial_halo_pixels=max(0, int(spatial_halo_pixels)),
        patch_width=max(1, int(patch_width)),
        patch_height=max(1, int(patch_height)),
    )


def enforce_plan_limits(plan: RasterTransformPlan) -> None:
    limits = _load_plan_limits()
    max_plan_bytes = int(
        limits.get("max_estimated_working_set_bytes", DEFAULT_MAX_ESTIMATED_WORKING_SET_BYTES)
    )
    max_temporal_full_extent_bytes = int(
        limits.get(
            "max_temporal_full_extent_bytes",
            DEFAULT_MAX_TEMPORAL_FULL_EXTENT_BYTES,
        )
    )
    max_temporal_tile_bytes = int(
        limits.get(
            "max_tiled_temporal_working_set_bytes",
            DEFAULT_MAX_TEMPORAL_TILE_BYTES,
        )
    )
    if plan.execution_strategy == "full_extent_temporal" and (
        plan.estimated_working_set_bytes > max_temporal_full_extent_bytes
    ):
        raise RasterTransformError(
            code="raster_transform_plan_too_large",
            message="Temporal full-extent transform exceeds configured working-set limits.",
            details={
                "estimated_working_set_bytes": int(plan.estimated_working_set_bytes),
                "limit_bytes": int(max_temporal_full_extent_bytes),
                "execution_strategy": plan.execution_strategy,
                "principal_reason": plan.principal_reason,
                "time_count": int(plan.time_count),
            },
        )
    if plan.execution_strategy == "tiled_temporal" and (
        plan.estimated_working_set_bytes > max_temporal_tile_bytes
    ):
        raise RasterTransformError(
            code="raster_transform_plan_too_large",
            message="Temporal tiled transform exceeds configured working-set limits.",
            details={
                "estimated_working_set_bytes": int(plan.estimated_working_set_bytes),
                "limit_bytes": int(max_temporal_tile_bytes),
                "execution_strategy": plan.execution_strategy,
                "principal_reason": plan.principal_reason,
                "patch_width": int(plan.patch_width),
                "patch_height": int(plan.patch_height),
                "time_count": int(plan.time_count),
            },
        )
    if plan.estimated_working_set_bytes > max_plan_bytes:
        raise RasterTransformError(
            code="raster_transform_plan_too_large",
            message="Transform exceeds configured working-set limits.",
            details={
                "estimated_working_set_bytes": int(plan.estimated_working_set_bytes),
                "limit_bytes": int(max_plan_bytes),
                "execution_strategy": plan.execution_strategy,
                "principal_reason": plan.principal_reason,
            },
        )


def load_target_grid_from_dem(dem_path: Path) -> TargetGrid:
    try:
        return _load_target_grid_from_dem(dem_path)
    except MapAlgebraError as exc:
        raise _translate_map_error(exc) from exc


def align_inputs_to_target(
    *,
    input_paths: dict[str, Path],
    target_grid: TargetGrid,
    resampling_name: str,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    is_cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, AlignedRaster]:
    try:
        return _align_inputs_to_target(
            input_paths=input_paths,
            target_grid=target_grid,
            resampling_name=resampling_name,
            on_progress=on_progress,
            is_cancel_requested=is_cancel_requested,
        )
    except MapAlgebraError as exc:
        raise _translate_map_error(exc) from exc


def finalize_output_array(
    *,
    result: np.ndarray,
    semantic: str,
    used_variables: set[str],
    participating_variables: set[str] | None = None,
    aligned_inputs: dict[str, AlignedRaster] | None = None,
    nodata_masks: dict[str, np.ndarray] | None = None,
) -> tuple[np.ndarray, str, float | None, np.ndarray | None]:
    _ = used_variables
    participants = set(participating_variables or ())
    if not participants:
        participants = set(used_variables)
    semantic_key = semantic if semantic in {"mask", "byte"} else "continuous"
    combined_invalid = _combine_invalid_masks(
        participants=participants,
        result_shape=np.asarray(result).shape,
        aligned_inputs=aligned_inputs,
        nodata_masks=nodata_masks,
    )
    if semantic_key in {"mask", "byte"}:
        if semantic_key == "mask":
            out = np.where(np.asarray(result, dtype=bool), 1, 0).astype(np.uint8)
        else:
            out = np.clip(np.rint(np.asarray(result, dtype=np.float64)), 0, 255).astype(np.uint8)
        if combined_invalid is not None:
            out = np.array(out, copy=True)
            out[combined_invalid] = np.uint8(0)
            return out, "uint8", None, ~combined_invalid
        return out, "uint8", None, None

    out = np.asarray(result, dtype=np.float32)
    invalid = np.zeros(out.shape, dtype=bool)
    if combined_invalid is not None:
        invalid |= combined_invalid
    invalid |= ~np.isfinite(out)
    if bool(np.any(invalid)):
        out = np.array(out, copy=True)
        out[invalid] = np.float32(-9999.0)
        return out, "float32", float(-9999.0), ~invalid
    return out, "float32", float(-9999.0), None


def write_output_raster(
    *,
    output_path: Path,
    target_grid: TargetGrid,
    array: np.ndarray,
    nodata_value: float | None = None,
    valid_mask: np.ndarray | None = None,
    overwrite: bool,
) -> int:
    try:
        if output_path.exists() and not overwrite:
            raise MapAlgebraError(
                code="map_algebra_output_exists",
                message=f"Output file already exists: {output_path}",
                details={"output_path": str(output_path)},
                status_code=409,
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(
            output_path,
            "w",
            driver="GTiff",
            width=int(target_grid.width),
            height=int(target_grid.height),
            count=1,
            dtype=str(array.dtype),
            crs=target_grid.crs,
            transform=target_grid.transform,
            nodata=nodata_value,
            tiled=True,
            blockxsize=128,
            blockysize=128,
            compress="lzw",
            bigtiff="IF_SAFER",
        ) as dst:
            dst.write(array, 1)
            if valid_mask is not None:
                mask_array = np.asarray(valid_mask, dtype=bool)
                if mask_array.shape != array.shape:
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message="Output validity mask shape does not match output array.",
                        details={
                            "mask_shape": list(mask_array.shape),
                            "array_shape": list(array.shape),
                        },
                    )
                dst.write_mask(np.where(mask_array, 255, 0).astype(np.uint8))
        return int(output_path.stat().st_size) if output_path.exists() else 0
    except MapAlgebraError as exc:
        raise _translate_map_error(exc) from exc


def expected_patch_keys(
    *,
    width: int,
    height: int,
    patch_width: int,
    patch_height: int,
) -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    for yoff in range(0, max(1, int(height)), max(1, int(patch_height))):
        for xoff in range(0, max(1, int(width)), max(1, int(patch_width))):
            keys.add((int(yoff), int(xoff)))
    return keys


def available_horizon_patch_keys(
    *,
    horizons_dir: Path,
    observer_elevation_meters: float,
) -> set[tuple[int, int]]:
    expected_suffix = int(round(float(observer_elevation_meters) * 10.0))
    patch_keys: set[tuple[int, int]] = set()
    if not horizons_dir.exists() or not horizons_dir.is_dir():
        return patch_keys
    for path in horizons_dir.iterdir():
        if not path.is_file():
            continue
        match = _HORIZON_FILE_RE.match(path.name)
        if match is None:
            continue
        row = int(match.group(1))
        col = int(match.group(2))
        suffix = int(match.group(3))
        if suffix != expected_suffix:
            continue
        patch_keys.add((row, col))
    return patch_keys


def mark_patch_invalid(
    mask: np.ndarray,
    *,
    patch_key: tuple[int, int],
    patch_width: int,
    patch_height: int,
    width: int,
    height: int,
) -> None:
    yoff, xoff = patch_key
    y1 = min(int(height), int(yoff) + max(1, int(patch_height)))
    x1 = min(int(width), int(xoff) + max(1, int(patch_width)))
    if yoff >= y1 or xoff >= x1:
        return
    mask[int(yoff) : y1, int(xoff) : x1] = True


def compute_value_range(
    array: np.ndarray,
    *,
    nodata_value: float | None = None,
    valid_mask: np.ndarray | None = None,
) -> tuple[float | None, float | None]:
    values = np.asarray(array)
    if valid_mask is not None:
        mask_array = np.asarray(valid_mask, dtype=bool)
        if mask_array.shape != values.shape:
            raise RasterTransformError(
                code="raster_transform_internal_error",
                message="Output validity mask shape does not match value array.",
                details={
                    "mask_shape": list(mask_array.shape),
                    "value_shape": list(values.shape),
                },
            )
        values = values[mask_array]
    elif nodata_value is not None:
        values = values[values != nodata_value]
    if not isinstance(values, np.ndarray) or values.size == 0:
        return None, None
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None, None
    return float(np.min(finite)), float(np.max(finite))


def execute_script(
    *,
    parsed: ParsedScript,
    variables: dict[str, Any],
    target_shape: tuple[int, int],
    transform: Any | None = None,
    metadata: dict[str, Any] | None = None,
) -> np.ndarray:
    env = _build_runtime_namespace(
        variables=variables,
        target_shape=target_shape,
        transform=transform,
        metadata=metadata,
    )
    local_env = dict(env)
    try:
        exec(compile(parsed.tree, "<raster_transform>", "exec"), {"__builtins__": {}}, local_env)
    except RasterTransformError:
        raise
    except Exception as exc:
        raise RasterTransformError(
            code="raster_transform_internal_error",
            message="Raster transform execution failed.",
            details={"error": str(exc)},
        ) from exc
    if "result" not in local_env:
        raise RasterTransformError(
            code="raster_transform_missing_result",
            message="Raster transform did not assign `result`.",
            details={
                "hint": "Assign final output to `result`, for example: result = where(mask, a, b).",
            },
        )
    return _coerce_result_array(
        local_env["result"],
        target_shape=target_shape,
    )


def infer_result_semantic(result: Any) -> str:
    array = np.asarray(result)
    if array.dtype == np.bool_:
        return "mask"
    if np.issubdtype(array.dtype, np.unsignedinteger) and array.ndim >= 2:
        return "byte"
    return "continuous"


def scenario_dem() -> Raster:
    scenario_id, scenario_root = _resolve_local_scenario_identity_and_root()
    dem_path = _resolve_local_dem_path(scenario_root=scenario_root)
    target_grid = load_target_grid_from_dem(dem_path)
    return Raster(
        node=_RasterNode(
            kind="scenario_dem",
            params={
                "scenario_id": scenario_id,
                "scenario_root": str(scenario_root),
                "dem_path": str(dem_path),
            },
            name="scenario_dem",
        ),
        grid=GridSpec(
            crs=target_grid.crs,
            transform=target_grid.transform,
            width=int(target_grid.width),
            height=int(target_grid.height),
        ),
        dtype="float32",
        name="scenario_dem",
    )


def raster_file(path: str | Path) -> Raster:
    raw = Path(path)
    scenario_id, scenario_root = _resolve_local_scenario_identity_and_root()
    resolved = raw if raw.is_absolute() else (scenario_root / raw).resolve()
    return Raster(
        node=_RasterNode(
            kind="file",
            params={
                "scenario_id": scenario_id,
                "scenario_root": str(scenario_root),
                "path": str(resolved),
            },
            name=resolved.name,
        ),
        dtype="float32",
        name=resolved.name,
    )


def slope_raster(src: Raster) -> Raster:
    return _wrap_raster(_RasterNode(kind="slope", inputs=(src,)))


def aspect_raster(src: Raster) -> Raster:
    return _wrap_raster(_RasterNode(kind="aspect", inputs=(src,)))


def hillshade_raster(
    src: Raster,
    azimuth_deg: float = 315.0,
    elevation_deg: float = 45.0,
) -> Raster:
    return _wrap_raster(
        _RasterNode(
            kind="hillshade",
            inputs=(src, float(azimuth_deg), float(elevation_deg)),
        )
    )


def _validate_statement(statement: ast.stmt, *, state: _ValidationState, depth: int) -> None:
    if depth > MAX_AST_DEPTH:
        raise RasterTransformError(
            code="raster_transform_disallowed_syntax",
            message="Script exceeds maximum depth.",
            details={"max_depth": MAX_AST_DEPTH},
        )
    if isinstance(statement, ast.Assign):
        if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
            raise RasterTransformError(
                code="raster_transform_disallowed_syntax",
                message="Only single-name assignments are supported.",
            )
        target_name = str(statement.targets[0].id)
        if target_name in state.input_names or target_name in state.metadata_names:
            raise RasterTransformError(
                code="raster_transform_invalid_argument",
                message=f"Assignment target cannot shadow reserved input or metadata name: {target_name}",
                details={"variable": target_name},
            )
        if target_name in SAFE_CALLS:
            raise RasterTransformError(
                code="raster_transform_invalid_argument",
                message=f"Assignment target cannot shadow a sealed function name: {target_name}",
                details={"variable": target_name},
            )
        if target_name in SAFE_FACADE_NAMES:
            raise RasterTransformError(
                code="raster_transform_invalid_argument",
                message=f"Assignment target cannot shadow a reserved runtime facade name: {target_name}",
                details={
                    "variable": target_name,
                    "hint": "Use a different variable name; reserved facade names are injected by the runtime.",
                },
            )
        _validate_expression(statement.value, state=state, depth=depth + 1)
        state.available_names.add(target_name)
        state.assigned_variables.add(target_name)
        return
    raise RasterTransformError(
        code="raster_transform_disallowed_syntax",
        message=f"Unsupported statement type: {type(statement).__name__}",
    )


def _validate_expression(node: ast.AST, *, state: _ValidationState, depth: int) -> None:
    if depth > MAX_AST_DEPTH:
        raise RasterTransformError(
            code="raster_transform_disallowed_syntax",
            message="Script exceeds maximum depth.",
            details={"max_depth": MAX_AST_DEPTH},
        )
    if isinstance(node, ast.Name):
        identifier = str(node.id)
        if identifier not in state.available_names and identifier not in SAFE_CALLS:
            raise RasterTransformError(
                code="raster_transform_unknown_variable",
                message=f"Unknown variable: {identifier}",
                details={"variable": identifier},
            )
        if identifier in state.input_names:
            state.used_input_variables.add(identifier)
        return
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool)):
            return
        raise RasterTransformError(
            code="raster_transform_invalid_argument",
            message="Only numeric and boolean constants are supported.",
        )
    if isinstance(node, ast.BinOp):
        _validate_expression(node.left, state=state, depth=depth + 1)
        _validate_expression(node.right, state=state, depth=depth + 1)
        state.used_operators.add(_operator_symbol(node.op))
        if not isinstance(
            node.op,
            (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.BitAnd, ast.BitOr),
        ):
            raise RasterTransformError(
                code="raster_transform_disallowed_syntax",
                message=f"Unsupported operator: {type(node.op).__name__}",
            )
        return
    if isinstance(node, ast.UnaryOp):
        _validate_expression(node.operand, state=state, depth=depth + 1)
        if isinstance(node.op, ast.Not):
            raise RasterTransformError(
                code="raster_transform_disallowed_syntax",
                message="Boolean `not` is not supported for elementwise raster logic.",
                details={"syntax_node": "BoolOp", "operator": "not"},
            )
        if not isinstance(node.op, (ast.UAdd, ast.USub, ast.Invert)):
            raise RasterTransformError(
                code="raster_transform_disallowed_syntax",
                message=f"Unsupported unary operator: {type(node.op).__name__}",
            )
        state.used_operators.add(_operator_symbol(node.op))
        return
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise RasterTransformError(
                code="raster_transform_invalid_argument",
                message="Chained comparisons are not supported.",
            )
        _validate_expression(node.left, state=state, depth=depth + 1)
        _validate_expression(node.comparators[0], state=state, depth=depth + 1)
        state.used_operators.add(_operator_symbol(node.ops[0]))
        return
    if isinstance(node, ast.Call):
        fn_name = ""
        function_name = ""
        if isinstance(node.func, ast.Name):
            function_name = str(node.func.id)
            fn_name = function_name
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            function_name = f"{str(node.func.value.id)}.{str(node.func.attr)}"
            fn_name = SAFE_FACADE_CALLS.get(function_name, "")
        if not fn_name:
            details = {"function": function_name} if function_name else {}
            if function_name:
                suggestions = _nearest_allowed_functions(function_name)
                if suggestions:
                    details["suggestions"] = suggestions
            raise RasterTransformError(
                code="raster_transform_unknown_function",
                message=(
                    f"Unsupported function: {function_name}"
                    if function_name
                    else "Only sealed function calls are supported."
                ),
                details=details,
            )
        if fn_name not in SAFE_CALLS:
            raise RasterTransformError(
                code="raster_transform_unknown_function",
                message=f"Unsupported function: {function_name}",
                details={
                    "function": function_name,
                    "suggestions": _nearest_allowed_functions(function_name),
                },
            )
        if node.keywords:
            raise RasterTransformError(
                code="raster_transform_invalid_argument",
                message="Keyword arguments are not supported in raster transform calls.",
            )
        state.used_functions.add(fn_name)
        expected = {
            "where": 3,
            "slope": 1,
            "aspect": 1,
            "hillshade": 3,
            "min": 1,
            "max": 1,
            "avg": 1,
            "std": 1,
            "nodata": 0,
            "nan": 0,
            "null": 0,
        }[fn_name]
        if len(node.args) != expected:
            raise RasterTransformError(
                code="raster_transform_invalid_argument",
                message=f"{fn_name}() requires exactly {expected} argument(s).",
            )
        for arg in node.args:
            _validate_expression(arg, state=state, depth=depth + 1)
        return
    if isinstance(node, ast.Attribute):
        raise RasterTransformError(
            code="raster_transform_disallowed_syntax",
            message="Attribute access is not allowed in raster transform scripts.",
        )
    if isinstance(node, ast.BoolOp):
        operator = "and" if isinstance(getattr(node, "op", None), ast.And) else "or"
        raise RasterTransformError(
            code="raster_transform_disallowed_syntax",
            message="Boolean `and`/`or` are not supported for elementwise raster logic.",
            details={"syntax_node": "BoolOp", "operator": operator},
        )
    if isinstance(
        node,
        (
            ast.IfExp,
            ast.Subscript,
            ast.Dict,
            ast.List,
            ast.Tuple,
            ast.Set,
            ast.Lambda,
            ast.ListComp,
            ast.DictComp,
            ast.SetComp,
            ast.GeneratorExp,
        ),
    ):
        raise RasterTransformError(
            code="raster_transform_disallowed_syntax",
            message=f"Unsupported syntax node: {type(node).__name__}",
        )
    raise RasterTransformError(
        code="raster_transform_disallowed_syntax",
        message=f"Unsupported syntax node: {type(node).__name__}",
    )


def _operator_symbol(op: ast.AST) -> str:
    mapping: dict[type[ast.AST], str] = {
        ast.Add: "+",
        ast.Sub: "-",
        ast.Mult: "*",
        ast.Div: "/",
        ast.Pow: "**",
        ast.BitAnd: "&",
        ast.BitOr: "|",
        ast.Invert: "~",
        ast.UAdd: "+",
        ast.USub: "-",
        ast.Gt: ">",
        ast.GtE: ">=",
        ast.Lt: "<",
        ast.LtE: "<=",
        ast.Eq: "==",
        ast.NotEq: "!=",
    }
    key = type(op)
    if key not in mapping:
        raise RasterTransformError(
            code="raster_transform_disallowed_syntax",
            message=f"Unsupported operator: {key.__name__}",
        )
    return mapping[key]


def _translate_map_error(error: MapAlgebraError) -> RasterTransformError:
    code = str(error.code or "").strip()
    if code.startswith("map_algebra_"):
        code = "raster_transform_" + code[len("map_algebra_") :]
    if code == "raster_transform_temporal_axis_too_large":
        code = "raster_transform_plan_too_large"
    return RasterTransformError(
        code=code or "raster_transform_internal_error",
        message=error.message,
        details=dict(error.details),
        status_code=int(error.status_code),
    )


def _load_plan_limits() -> dict[str, Any]:
    config = load_app_config(strict=False)
    backend_cfg = config.get("backend", {}) if isinstance(config, dict) else {}
    if not isinstance(backend_cfg, dict):
        return {}
    limits = backend_cfg.get("raster_transform", {})
    return limits if isinstance(limits, dict) else {}


def _build_runtime_namespace(
    *,
    variables: dict[str, Any],
    target_shape: tuple[int, int],
    transform: Any | None,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    runtime_metadata = dict(metadata or {})
    where_fn = lambda cond, a, b: np.where(np.asarray(cond, dtype=bool), a, b)
    namespace: dict[str, Any] = dict(variables)
    namespace.update(
        {
            "where": where_fn,
            "slope": lambda value: _slope_array(value, transform=transform),
            "aspect": lambda value: _aspect_array(value, transform=transform),
            "hillshade": lambda value, azimuth_deg, elevation_deg: _hillshade_array(
                value,
                azimuth_deg,
                elevation_deg,
                transform=transform,
            ),
            "min": lambda value: _reduce_time_array(value, reducer=np.nanmin, name="min"),
            "max": lambda value: _reduce_time_array(value, reducer=np.nanmax, name="max"),
            "avg": lambda value: _reduce_time_array(value, reducer=np.nanmean, name="avg"),
            "std": lambda value: _reduce_time_array(value, reducer=np.nanstd, name="std"),
            "nodata": lambda: np.float64(np.nan),
            "nan": lambda: np.float64(np.nan),
            "null": lambda: np.float64(np.nan),
            "np": _SealedNumpyFacade(where_fn),
        }
    )
    for key in SAFE_METADATA_NAMES:
        if key in runtime_metadata:
            namespace[key] = runtime_metadata[key]
    namespace["_target_shape"] = target_shape
    return namespace


def _coerce_result_array(value: Any, *, target_shape: tuple[int, int]) -> np.ndarray:
    if isinstance(value, np.ndarray):
        array = value
    else:
        array = np.full(target_shape, value, dtype=np.float64)
    if array.ndim == 2:
        if tuple(array.shape) != tuple(target_shape):
            raise RasterTransformError(
                code="raster_transform_invalid_argument",
                message="Transform result does not match target grid shape.",
                details={
                    "result_shape": list(array.shape),
                    "target_shape": [target_shape[0], target_shape[1]],
                },
            )
        return array
    if array.ndim == 3:
        if tuple(array.shape[1:]) != tuple(target_shape):
            raise RasterTransformError(
                code="raster_transform_invalid_argument",
                message="Temporal transform result does not match target grid shape.",
                details={
                    "result_shape": list(array.shape),
                    "target_shape": [target_shape[0], target_shape[1]],
                },
            )
        return array
    raise RasterTransformError(
        code="raster_transform_invalid_argument",
        message="Transform result must be scalar, 2D raster, or 3D temporal raster.",
        details={"result_ndim": int(array.ndim)},
    )


def _xres_yres(transform: Any | None) -> tuple[float, float]:
    xres = abs(float(getattr(transform, "a", 1.0) or 1.0))
    yres = abs(float(getattr(transform, "e", -1.0) or 1.0))
    if xres <= 0.0:
        xres = 1.0
    if yres <= 0.0:
        yres = 1.0
    return xres, yres


def _slope_array(value: Any, *, transform: Any | None) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 2:
        raise RasterTransformError(
            code="raster_transform_invalid_argument",
            message="slope() expects a 2D raster input.",
            details={"ndim": int(array.ndim)},
        )
    xres, yres = _xres_yres(transform)
    dz_dy, dz_dx = np.gradient(array, yres, xres)
    return np.degrees(np.arctan(np.hypot(dz_dx, dz_dy))).astype(np.float32)


def _aspect_array(value: Any, *, transform: Any | None) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 2:
        raise RasterTransformError(
            code="raster_transform_invalid_argument",
            message="aspect() expects a 2D raster input.",
            details={"ndim": int(array.ndim)},
        )
    xres, yres = _xres_yres(transform)
    dz_dy, dz_dx = np.gradient(array, yres, xres)
    aspect = 90.0 - np.degrees(np.arctan2(dz_dy, -dz_dx))
    aspect = np.where(aspect < 0.0, aspect + 360.0, aspect)
    aspect = np.where(aspect >= 360.0, aspect - 360.0, aspect)
    return aspect.astype(np.float32)


def _hillshade_array(
    value: Any,
    azimuth_deg: Any,
    elevation_deg: Any,
    *,
    transform: Any | None,
) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 2:
        raise RasterTransformError(
            code="raster_transform_invalid_argument",
            message="hillshade() expects a 2D raster input.",
            details={"ndim": int(array.ndim)},
        )
    azimuth = float(np.asarray(azimuth_deg, dtype=np.float64))
    elevation = float(np.asarray(elevation_deg, dtype=np.float64))
    xres, yres = _xres_yres(transform)
    dz_dy, dz_dx = np.gradient(array, yres, xres)
    slope_rad = np.arctan(np.hypot(dz_dx, dz_dy))
    aspect_rad = np.arctan2(dz_dy, -dz_dx)
    azimuth_math = np.radians(360.0 - azimuth + 90.0)
    zenith_rad = np.radians(90.0 - elevation)
    shaded = (
        np.cos(zenith_rad) * np.cos(slope_rad)
        + np.sin(zenith_rad) * np.sin(slope_rad) * np.cos(azimuth_math - aspect_rad)
    )
    return np.clip(255.0 * shaded, 0.0, 255.0).astype(np.uint8)


def _reduce_time_array(
    value: Any,
    *,
    reducer: Callable[..., np.ndarray],
    name: str,
) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 3:
        raise RasterTransformError(
            code="raster_transform_invalid_argument",
            message=f"{name}() expects a 3D temporal raster input [time, height, width].",
            details={"ndim": int(array.ndim)},
        )
    try:
        return np.asarray(reducer(array, axis=0), dtype=np.float32)
    except Exception as exc:
        raise RasterTransformError(
            code="raster_transform_invalid_argument",
            message=f"{name}() failed to reduce temporal raster.",
            details={"error": str(exc)},
        ) from exc


def _wrap_raster(node: _RasterNode) -> Raster:
    return Raster(node=node, dtype="float32", name=node.name)


def _resolve_local_scenario_identity_and_root() -> tuple[str, Path]:
    if is_running_under_job_runner():
        ctx = get_context()
        return str(ctx.scenario_id), Path(ctx.scenario_root_dir).resolve()
    scenario_id = str(os.getenv("LUNAR_NOTEBOOK_SCENARIO_ID", "")).strip()
    root_raw = str(os.getenv("LUNAR_NOTEBOOK_SCENARIO_ROOT", "")).strip()
    if root_raw:
        scenario_root = Path(root_raw).expanduser().resolve()
        return scenario_id or scenario_root.name, scenario_root
    if scenario_id:
        scenario_root = (_resolve_default_notebook_scenario_parent_dir() / scenario_id).resolve()
        return scenario_id, scenario_root
    inferred = infer_local_scenario_identity_and_root()
    if inferred is not None:
        return inferred
    scenario_id = "test_scenario"
    scenario_root = (_resolve_default_notebook_scenario_parent_dir() / scenario_id).resolve()
    return scenario_id, scenario_root


def _resolve_default_notebook_scenario_parent_dir() -> Path:
    env_override = str(os.getenv("LUNAR_ANALYST_WORKSPACE_ROOT", "")).strip()
    if env_override:
        return Path(env_override).expanduser().resolve()
    config = load_app_config()
    backend_cfg = config.get("backend", {}) if isinstance(config, dict) else {}
    if isinstance(backend_cfg, dict):
        cfg_root = backend_cfg.get("workspace_root")
        if isinstance(cfg_root, str) and cfg_root.strip():
            return resolve_config_relative_path(cfg_root, config_path=resolve_config_path()).resolve()
    return (Path.cwd() / DEFAULT_NOTEBOOK_SCENARIO_PARENT_DIR).resolve()


def _resolve_local_dem_path(*, scenario_root: Path) -> Path:
    for candidate_name in ("primary_dem.tif", "dem.tif"):
        candidate = (scenario_root / candidate_name).resolve()
        if candidate.exists() and candidate.is_file():
            return candidate
    return (scenario_root / "dem.tif").resolve()


def _resolve_notebook_runtime_context() -> _NotebookRuntimeContext:
    scenario_id, scenario_root = _resolve_local_scenario_identity_and_root()
    dem_path = _resolve_local_dem_path(scenario_root=scenario_root)
    target_grid = load_target_grid_from_dem(dem_path)
    return _NotebookRuntimeContext(
        scenario_id=scenario_id,
        scenario_root=scenario_root,
        dem_path=dem_path,
        target_grid=target_grid,
    )


def _materialize_operand(value: Any, *, context: _NotebookRuntimeContext) -> Any:
    if isinstance(value, Raster):
        return _materialize_raster_node(value._node, context=context)
    return value


def _materialize_raster_node(node: _RasterNode, *, context: _NotebookRuntimeContext) -> np.ndarray:
    if node.kind == "scenario_dem":
        cache_key = str(context.dem_path)
        cached = context.aligned_cache.get(cache_key)
        if cached is not None:
            return cached
        with rasterio.open(context.dem_path) as ds:
            data = np.asarray(ds.read(1), dtype=np.float32)
        context.aligned_cache[cache_key] = data
        return data
    if node.kind == "file":
        raw_path = Path(str(node.params["path"])).expanduser().resolve()
        cache_key = str(raw_path)
        cached = context.aligned_cache.get(cache_key)
        if cached is not None:
            return cached
        aligned = align_inputs_to_target(
            input_paths={"input": raw_path},
            target_grid=context.target_grid,
            resampling_name="bilinear",
        )
        data = np.asarray(aligned["input"].data, dtype=np.float32)
        context.aligned_cache[cache_key] = data
        return data
    if node.kind in {"add", "sub", "mul", "div", "pow", "bitand", "bitor", "lt", "lte", "gt", "gte", "eq", "neq"}:
        left = _materialize_operand(node.inputs[0], context=context)
        right = _materialize_operand(node.inputs[1], context=context)
        return _apply_binary_operation(node.kind, left, right)
    if node.kind in {"invert", "neg", "pos"}:
        value = _materialize_operand(node.inputs[0], context=context)
        return _apply_unary_operation(node.kind, value)
    if node.kind == "where":
        cond = _materialize_operand(node.inputs[0], context=context)
        left = _materialize_operand(node.inputs[1], context=context)
        right = _materialize_operand(node.inputs[2], context=context)
        return np.where(np.asarray(cond, dtype=bool), left, right)
    if node.kind == "slope":
        value = _materialize_operand(node.inputs[0], context=context)
        return _slope_array(value, transform=context.target_grid.transform)
    if node.kind == "aspect":
        value = _materialize_operand(node.inputs[0], context=context)
        return _aspect_array(value, transform=context.target_grid.transform)
    if node.kind == "hillshade":
        value = _materialize_operand(node.inputs[0], context=context)
        return _hillshade_array(
            value,
            node.inputs[1],
            node.inputs[2],
            transform=context.target_grid.transform,
        )
    raise RasterTransformError(
        code="raster_transform_internal_error",
        message=f"Unsupported lazy raster node: {node.kind}",
    )


def _apply_binary_operation(op_name: str, left: Any, right: Any) -> np.ndarray:
    if op_name == "add":
        return np.asarray(left) + np.asarray(right)
    if op_name == "sub":
        return np.asarray(left) - np.asarray(right)
    if op_name == "mul":
        return np.asarray(left) * np.asarray(right)
    if op_name == "div":
        return np.asarray(left) / np.asarray(right)
    if op_name == "pow":
        return np.asarray(left) ** np.asarray(right)
    if op_name == "bitand":
        return np.logical_and(np.asarray(left, dtype=bool), np.asarray(right, dtype=bool))
    if op_name == "bitor":
        return np.logical_or(np.asarray(left, dtype=bool), np.asarray(right, dtype=bool))
    if op_name == "lt":
        return np.asarray(left) < np.asarray(right)
    if op_name == "lte":
        return np.asarray(left) <= np.asarray(right)
    if op_name == "gt":
        return np.asarray(left) > np.asarray(right)
    if op_name == "gte":
        return np.asarray(left) >= np.asarray(right)
    if op_name == "eq":
        return np.asarray(left) == np.asarray(right)
    if op_name == "neq":
        return np.asarray(left) != np.asarray(right)
    raise RasterTransformError(
        code="raster_transform_internal_error",
        message=f"Unsupported binary operation: {op_name}",
    )


def _apply_unary_operation(op_name: str, value: Any) -> np.ndarray:
    if op_name == "invert":
        return np.logical_not(np.asarray(value, dtype=bool))
    if op_name == "neg":
        return -np.asarray(value)
    if op_name == "pos":
        return +np.asarray(value)
    raise RasterTransformError(
        code="raster_transform_internal_error",
        message=f"Unsupported unary operation: {op_name}",
    )


def _combine_invalid_masks(
    *,
    participants: set[str],
    result_shape: tuple[int, ...],
    aligned_inputs: dict[str, AlignedRaster] | None,
    nodata_masks: dict[str, np.ndarray] | None,
) -> np.ndarray | None:
    combined_invalid: np.ndarray | None = None
    for variable in sorted(participants):
        mask: np.ndarray | None = None
        if nodata_masks is not None:
            mask = nodata_masks.get(variable)
        if mask is None and aligned_inputs is not None:
            aligned = aligned_inputs.get(variable)
            mask = None if aligned is None else aligned.nodata_mask
        if mask is None:
            continue
        mask_array = np.asarray(mask, dtype=bool)
        if mask_array.shape != result_shape:
            if mask_array.ndim == 2 and len(result_shape) == 3 and tuple(result_shape[1:]) == tuple(mask_array.shape):
                mask_array = np.broadcast_to(mask_array, result_shape)
            else:
                raise RasterTransformError(
                    code="raster_transform_invalid_argument",
                    message="Input validity mask shape does not match transform result shape.",
                    details={
                        "variable": variable,
                        "mask_shape": list(mask_array.shape),
                        "result_shape": list(result_shape),
                    },
                )
        if combined_invalid is None:
            combined_invalid = np.array(mask_array, copy=True)
        else:
            combined_invalid |= mask_array
    return combined_invalid
