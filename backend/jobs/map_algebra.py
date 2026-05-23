from __future__ import annotations

import ast
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from backend.core.crs_semantics import crs_semantically_equivalent


MAX_EXPRESSION_CHARS = 4096
MAX_AST_DEPTH = 64
MAX_AST_NODES = 1024
NODATA_FUNCTION_ALIASES = frozenset({"nodata", "nan", "null"})
MASK_CLEANUP_MODES = frozenset({"none", "erosion", "opening"})
logger = logging.getLogger(__name__)


class MapAlgebraError(Exception):
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


@dataclass(frozen=True)
class ParsedExpression:
    tree: ast.Expression
    normalized_expression: str
    semantic: str
    used_variables: set[str]
    used_functions: set[str]
    used_operators: set[str]


@dataclass(frozen=True)
class TargetGrid:
    crs: Any
    transform: Any
    width: int
    height: int
    dem_path: Path


@dataclass(frozen=True)
class AlignedRaster:
    variable: str
    path: Path
    data: np.ndarray
    nodata_mask: np.ndarray
    reprojected: bool
    source_crs: str | None
    source_transform: tuple[float, ...] | None
    source_nodata: float | None


def compute_ast_hash(normalized_expression: str) -> str:
    return hashlib.sha256(normalized_expression.encode("utf-8")).hexdigest()


def parse_validate_expression(expression: str, *, allowed_variables: set[str]) -> ParsedExpression:
    raw = str(expression or "").strip()
    if not raw:
        raise MapAlgebraError(
            code="map_algebra_parse_error",
            message="Expression is required.",
        )
    if len(raw) > MAX_EXPRESSION_CHARS:
        raise MapAlgebraError(
            code="map_algebra_disallowed_syntax",
            message="Expression exceeds maximum length.",
            details={"max_chars": MAX_EXPRESSION_CHARS},
        )
    try:
        tree = ast.parse(raw, mode="eval")
    except SyntaxError as exc:
        raise MapAlgebraError(
            code="map_algebra_parse_error",
            message="Invalid map algebra expression syntax.",
            details={
                "lineno": int(exc.lineno or 0),
                "offset": int(exc.offset or 0),
                "text": (exc.text or "").strip(),
            },
        ) from exc

    state = _ValidationState(allowed_variables=allowed_variables)
    semantic = _validate_node(tree.body, state=state, depth=1)
    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > MAX_AST_NODES:
        raise MapAlgebraError(
            code="map_algebra_disallowed_syntax",
            message="Expression AST exceeds complexity limit.",
            details={"max_nodes": MAX_AST_NODES, "node_count": node_count},
        )
    normalized = ast.unparse(tree) if hasattr(ast, "unparse") else raw
    return ParsedExpression(
        tree=tree,
        normalized_expression=normalized,
        semantic=semantic,
        used_variables=set(state.used_variables),
        used_functions=set(state.used_functions),
        used_operators=set(state.used_operators),
    )


def load_target_grid_from_dem(dem_path: Path) -> TargetGrid:
    import rasterio

    with rasterio.open(dem_path) as ds:
        crs = ds.crs
        transform = ds.transform
        width = int(ds.width)
        height = int(ds.height)
    if crs is None:
        raise MapAlgebraError(
            code="map_algebra_grid_alignment_failed",
            message="Scenario DEM has no CRS.",
            details={"dem_path": str(dem_path)},
        )
    return TargetGrid(
        crs=crs,
        transform=transform,
        width=width,
        height=height,
        dem_path=dem_path,
    )


def align_inputs_to_target(
    *,
    input_paths: dict[str, Path],
    target_grid: TargetGrid,
    resampling_name: str,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    is_cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, AlignedRaster]:
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    if not input_paths:
        raise MapAlgebraError(
            code="map_algebra_invalid_argument",
            message="At least one raster input is required.",
        )
    resampling = _parse_resampling(resampling_name)
    aligned: dict[str, AlignedRaster] = {}
    total = len(input_paths)

    for index, (name, path) in enumerate(sorted(input_paths.items(), key=lambda item: item[0]), start=1):
        _raise_if_cancelled(is_cancel_requested)
        logger.info(
            "map_algebra align input start input=%s path=%s index=%s total=%s",
            name,
            path,
            index,
            total,
        )
        if on_progress is not None:
            on_progress(
                {
                    "percent": 15.0 + (index - 1) * (25.0 / max(total, 1)),
                    "message": f"Aligning raster input '{name}'.",
                    "stage": "reproject_align",
                    "input_name": name,
                }
            )
        if not path.exists() or not path.is_file():
            raise MapAlgebraError(
                code="map_algebra_input_not_found",
                message=f"Input raster does not exist: {name}",
                details={"input_name": name, "path": str(path)},
                status_code=404,
            )
        try:
            import rasterio

            with rasterio.open(path) as src:
                logger.info(
                    "map_algebra align input opened input=%s width=%s height=%s crs=%s",
                    name,
                    int(src.width),
                    int(src.height),
                    str(src.crs),
                )
                source = src.read(1, masked=True)
                src_mask = np.ma.getmaskarray(source).astype(bool, copy=False)
                src_nodata = _to_optional_float(src.nodata)
                src_data = np.asarray(
                    source.filled(src_nodata if src_nodata is not None else 0),
                    dtype=np.float32,
                )
                source_transform = src.transform
                source_crs = src.crs
                aligned_to_target = (
                    crs_semantically_equivalent(source_crs, target_grid.crs)
                    and int(src.width) == int(target_grid.width)
                    and int(src.height) == int(target_grid.height)
                    and _transform_close(source_transform, target_grid.transform)
                )
                if aligned_to_target:
                    data = np.asarray(src_data, dtype=np.float32)
                    nodata_mask = np.asarray(src_mask, dtype=bool)
                    aligned[name] = AlignedRaster(
                        variable=name,
                        path=path,
                        data=data,
                        nodata_mask=nodata_mask,
                        reprojected=False,
                        source_crs=str(source_crs) if source_crs is not None else None,
                        source_transform=tuple(float(v) for v in source_transform),
                        source_nodata=src_nodata,
                    )
                    logger.info("map_algebra align input complete input=%s reprojected=%s", name, False)
                    continue

                if source_crs is None:
                    raise MapAlgebraError(
                        code="map_algebra_crs_transform_failed",
                        message=f"Input raster has no CRS: {name}",
                        details={"input_name": name, "path": str(path)},
                    )

                data = np.zeros((target_grid.height, target_grid.width), dtype=np.float32)
                reproject(
                    source=src_data,
                    destination=data,
                    src_transform=source_transform,
                    src_crs=source_crs,
                    src_nodata=src_nodata,
                    dst_transform=target_grid.transform,
                    dst_crs=target_grid.crs,
                    dst_nodata=src_nodata,
                    resampling=resampling,
                )
                src_valid = (~src_mask).astype(np.uint8)
                dst_valid = np.zeros((target_grid.height, target_grid.width), dtype=np.uint8)
                reproject(
                    source=src_valid,
                    destination=dst_valid,
                    src_transform=source_transform,
                    src_crs=source_crs,
                    src_nodata=0,
                    dst_transform=target_grid.transform,
                    dst_crs=target_grid.crs,
                    dst_nodata=0,
                    resampling=Resampling.nearest,
                )
                nodata_mask = dst_valid == 0
                aligned[name] = AlignedRaster(
                    variable=name,
                    path=path,
                    data=data,
                    nodata_mask=nodata_mask,
                    reprojected=True,
                    source_crs=str(source_crs),
                    source_transform=tuple(float(v) for v in source_transform),
                    source_nodata=src_nodata,
                )
                logger.info("map_algebra align input complete input=%s reprojected=%s", name, True)
        except MapAlgebraError:
            raise
        except Exception as exc:
            raise MapAlgebraError(
                code="map_algebra_crs_transform_failed",
                message=f"Failed to align input raster: {name}",
                details={"input_name": name, "path": str(path), "error": str(exc)},
            ) from exc
    return aligned


def evaluate_expression(
    *,
    parsed: ParsedExpression,
    aligned_inputs: dict[str, AlignedRaster],
    target_grid: TargetGrid,
) -> np.ndarray:
    variables = {name: record.data for name, record in aligned_inputs.items()}
    return evaluate_expression_for_variables(
        parsed=parsed,
        variables=variables,
        target_shape=(target_grid.height, target_grid.width),
        transform=target_grid.transform,
    )


def evaluate_expression_for_variables(
    *,
    parsed: ParsedExpression,
    variables: dict[str, Any],
    target_shape: tuple[int, int],
    transform: Any | None = None,
) -> np.ndarray:
    evaluator = _ExpressionEvaluator(
        variables=variables,
        target_shape=target_shape,
        transform=transform,
    )
    value = evaluator.eval(parsed.tree.body)
    if isinstance(value, np.ndarray):
        array = value
    else:
        array = np.full(target_shape, value, dtype=np.float64)
    if array.ndim == 2:
        if array.shape != target_shape:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="Expression result does not match target grid shape.",
                details={
                    "result_shape": list(array.shape),
                    "target_shape": [target_shape[0], target_shape[1]],
                },
            )
        return array
    if array.ndim == 3:
        if array.shape[1:] != target_shape:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="Temporal expression result does not match target grid shape.",
                details={
                    "result_shape": list(array.shape),
                    "target_shape": [target_shape[0], target_shape[1]],
                },
            )
        return array
    raise MapAlgebraError(
        code="map_algebra_invalid_argument",
        message="Expression result must be scalar, 2D raster, or 3D temporal raster.",
        details={"result_ndim": int(array.ndim)},
    )


def finalize_output_array(
    *,
    result: np.ndarray,
    semantic: str,
    used_variables: set[str],
    aligned_inputs: dict[str, AlignedRaster] | None = None,
    nodata_masks: dict[str, np.ndarray] | None = None,
) -> tuple[np.ndarray, str, float | None]:
    semantic_key = semantic if semantic in {"mask", "byte", "labels"} else "continuous"
    if semantic_key in {"mask", "byte"}:
        if semantic_key == "mask":
            out = np.where(np.asarray(result, dtype=bool), 1, 0).astype(np.uint8)
        else:
            out = np.clip(np.rint(np.asarray(result, dtype=np.float64)), 0, 255).astype(np.uint8)
        return out, "uint8", None
    if semantic_key == "labels":
        out = np.asarray(result, dtype=np.int32)
        return out, "int32", None

    out = np.asarray(result, dtype=np.float32)
    nodata_value = float(-9999.0)
    combined_mask: np.ndarray | None = None
    for variable in used_variables:
        mask: np.ndarray | None = None
        if nodata_masks is not None:
            mask = nodata_masks.get(variable)
        elif aligned_inputs is not None:
            aligned = aligned_inputs.get(variable)
            mask = None if aligned is None else aligned.nodata_mask
        if mask is None:
            continue
        mask_array = np.asarray(mask, dtype=bool)
        if mask_array.shape != out.shape:
            if mask_array.ndim == 2 and out.ndim == 3 and out.shape[1:] == mask_array.shape:
                mask_array = np.broadcast_to(mask_array, out.shape)
            else:
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message="Nodata mask shape does not match expression result shape.",
                    details={
                        "variable": variable,
                        "mask_shape": list(mask_array.shape),
                        "result_shape": list(out.shape),
                    },
                )
        if combined_mask is None:
            combined_mask = np.array(mask_array, copy=True)
        else:
            combined_mask |= mask_array
    if combined_mask is not None:
        out = np.array(out, copy=True)
        out[combined_mask] = np.float32(nodata_value)
    invalid = ~np.isfinite(out)
    if bool(np.any(invalid)):
        out = np.array(out, copy=True)
        out[invalid] = np.float32(nodata_value)
    return out, "float32", nodata_value


def write_output_raster(
    *,
    output_path: Path,
    target_grid: TargetGrid,
    array: np.ndarray,
    nodata_value: float | None = None,
    overwrite: bool,
) -> int:
    import rasterio

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
    return int(output_path.stat().st_size) if output_path.exists() else 0


def expression_digest(*, expression: str, inputs: dict[str, Path]) -> str:
    payload = "|".join(
        [
            expression.strip(),
            *[f"{name}={path.as_posix()}" for name, path in sorted(inputs.items(), key=lambda item: item[0])],
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _parse_resampling(name: str) -> Any:
    from rasterio.enums import Resampling

    key = str(name or "").strip().lower() or "bilinear"
    if key == "nearest":
        return Resampling.nearest
    if key == "bilinear":
        return Resampling.bilinear
    if key == "cubic":
        return Resampling.cubic
    raise MapAlgebraError(
        code="map_algebra_invalid_argument",
        message=f"Unsupported resampling mode: {name!r}",
        details={"allowed": ["nearest", "bilinear", "cubic"]},
    )


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _raise_if_cancelled(is_cancel_requested: Callable[[], bool] | None) -> None:
    if is_cancel_requested is None:
        return
    if bool(is_cancel_requested()):
        raise MapAlgebraError(
            code="map_algebra_canceled",
            message="Raster calculation canceled.",
            status_code=409,
        )


def _transform_close(left: Any, right: Any, *, tol: float = 1e-9) -> bool:
    try:
        left_values = tuple(float(v) for v in left)
        right_values = tuple(float(v) for v in right)
    except Exception:
        return False
    if len(left_values) != len(right_values):
        return False
    return all(abs(a - b) <= tol for a, b in zip(left_values, right_values))


@dataclass
class _ValidationState:
    allowed_variables: set[str]
    used_variables: set[str] = field(default_factory=set)
    used_functions: set[str] = field(default_factory=set)
    used_operators: set[str] = field(default_factory=set)


def _validate_node(node: ast.AST, *, state: _ValidationState, depth: int) -> str:
    if depth > MAX_AST_DEPTH:
        raise MapAlgebraError(
            code="map_algebra_disallowed_syntax",
            message="Expression exceeds maximum depth.",
            details={"max_depth": MAX_AST_DEPTH},
        )
    if isinstance(node, ast.Name):
        identifier = str(node.id)
        if identifier not in state.allowed_variables:
            raise MapAlgebraError(
                code="map_algebra_unknown_variable",
                message=f"Unknown raster variable: {identifier}",
                details={"variable": identifier},
            )
        state.used_variables.add(identifier)
        return "continuous"
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool, str)):
            return "scalar"
        raise MapAlgebraError(
            code="map_algebra_invalid_argument",
            message="Only numeric, boolean, and string constants are supported.",
        )
    if isinstance(node, ast.BinOp):
        left_sem = _validate_node(node.left, state=state, depth=depth + 1)
        right_sem = _validate_node(node.right, state=state, depth=depth + 1)
        op_symbol = _operator_symbol(node.op)
        state.used_operators.add(op_symbol)
        if isinstance(node.op, (ast.BitAnd, ast.BitOr)):
            return "mask"
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            if left_sem == "byte" and right_sem == "byte":
                return "byte"
            return "continuous"
        raise MapAlgebraError(
            code="map_algebra_disallowed_syntax",
            message=f"Unsupported operator: {type(node.op).__name__}",
        )
    if isinstance(node, ast.UnaryOp):
        child_sem = _validate_node(node.operand, state=state, depth=depth + 1)
        op_symbol = _operator_symbol(node.op)
        state.used_operators.add(op_symbol)
        if isinstance(node.op, ast.Invert):
            return "mask"
        if isinstance(node.op, (ast.UAdd, ast.USub)):
            return child_sem
        raise MapAlgebraError(
            code="map_algebra_disallowed_syntax",
            message=f"Unsupported unary operator: {type(node.op).__name__}",
        )
    if isinstance(node, ast.Compare):
        _validate_node(node.left, state=state, depth=depth + 1)
        if len(node.comparators) != 1 or len(node.ops) != 1:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="Chained comparisons are not supported.",
            )
        _validate_node(node.comparators[0], state=state, depth=depth + 1)
        op = node.ops[0]
        _ = _operator_symbol(op)
        state.used_operators.add(_operator_symbol(op))
        return "mask"
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise MapAlgebraError(
                code="map_algebra_disallowed_syntax",
                message="Only direct function calls are supported.",
            )
        if node.keywords:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="Keyword arguments are not supported in map algebra functions.",
            )
        fn = str(node.func.id)
        state.used_functions.add(fn)
        if fn in NODATA_FUNCTION_ALIASES:
            if node.args:
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message=f"{fn}() requires exactly 0 arguments.",
                )
            return "continuous"
        if fn == "where":
            if len(node.args) != 3:
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message="where() requires exactly 3 arguments.",
                )
            _validate_node(node.args[0], state=state, depth=depth + 1)
            true_sem = _validate_node(node.args[1], state=state, depth=depth + 1)
            false_sem = _validate_node(node.args[2], state=state, depth=depth + 1)
            return true_sem if true_sem == false_sem else "continuous"
        if fn in {"slope", "aspect"}:
            if len(node.args) != 1:
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message=f"{fn}() requires exactly 1 argument.",
                )
            _validate_node(node.args[0], state=state, depth=depth + 1)
            return "continuous"
        if fn == "label_regions":
            if len(node.args) < 1 or len(node.args) > 3:
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message="label_regions() requires 1 to 3 arguments: mask[, cleanup_mode[, cleanup_iterations]].",
                )
            _validate_node(node.args[0], state=state, depth=depth + 1)
            if len(node.args) >= 2:
                mode_arg = node.args[1]
                if not isinstance(mode_arg, ast.Constant) or not isinstance(mode_arg.value, str):
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message="label_regions() cleanup_mode must be a string constant: 'none', 'erosion', or 'opening'.",
                    )
                mode_key = str(mode_arg.value).strip().lower()
                if mode_key not in MASK_CLEANUP_MODES:
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message="label_regions() cleanup_mode must be one of: 'none', 'erosion', 'opening'.",
                        details={"cleanup_mode": mode_arg.value},
                    )
            if len(node.args) == 3:
                iter_arg = node.args[2]
                if not isinstance(iter_arg, ast.Constant) or not isinstance(iter_arg.value, (int, float)):
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message="label_regions() cleanup_iterations must be a numeric constant >= 0.",
                    )
                iteration_value = float(iter_arg.value)
                iteration_int = int(iteration_value)
                if iteration_value != float(iteration_int) or iteration_int < 0:
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message="label_regions() cleanup_iterations must be an integer >= 0.",
                        details={"cleanup_iterations": iter_arg.value},
                    )
            return "labels"
        if fn == "region_sizes":
            if len(node.args) < 1 or len(node.args) > 3:
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message="region_sizes() requires 1 to 3 arguments: mask[, cleanup_mode[, cleanup_iterations]].",
                )
            _validate_node(node.args[0], state=state, depth=depth + 1)
            if len(node.args) >= 2:
                mode_arg = node.args[1]
                if not isinstance(mode_arg, ast.Constant) or not isinstance(mode_arg.value, str):
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message="region_sizes() cleanup_mode must be a string constant: 'none', 'erosion', or 'opening'.",
                    )
                mode_key = str(mode_arg.value).strip().lower()
                if mode_key not in MASK_CLEANUP_MODES:
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message="region_sizes() cleanup_mode must be one of: 'none', 'erosion', 'opening'.",
                        details={"cleanup_mode": mode_arg.value},
                    )
            if len(node.args) == 3:
                iter_arg = node.args[2]
                if not isinstance(iter_arg, ast.Constant) or not isinstance(iter_arg.value, (int, float)):
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message="region_sizes() cleanup_iterations must be a numeric constant >= 0.",
                    )
                iteration_value = float(iter_arg.value)
                iteration_int = int(iteration_value)
                if iteration_value != float(iteration_int) or iteration_int < 0:
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message="region_sizes() cleanup_iterations must be an integer >= 0.",
                        details={"cleanup_iterations": iter_arg.value},
                    )
            return "labels"
        if fn == "filter_regions_by_size":
            if len(node.args) < 3 or len(node.args) > 5:
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message=(
                        "filter_regions_by_size() requires 3 to 5 arguments: "
                        "mask, threshold, comparator[, cleanup_mode[, cleanup_iterations]]."
                    ),
                )
            _validate_node(node.args[0], state=state, depth=depth + 1)
            _validate_node(node.args[1], state=state, depth=depth + 1)
            cmp_arg = node.args[2]
            if not isinstance(cmp_arg, ast.Constant) or not isinstance(cmp_arg.value, str):
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message="filter_regions_by_size() comparator must be a string constant: '>=' or '<='.",
                )
            comparator = str(cmp_arg.value).strip()
            if comparator not in {">=", "<="}:
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message="filter_regions_by_size() comparator must be one of: '>=', '<='.",
                    details={"comparator": cmp_arg.value},
                )
            if len(node.args) >= 4:
                mode_arg = node.args[3]
                if not isinstance(mode_arg, ast.Constant) or not isinstance(mode_arg.value, str):
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message=(
                            "filter_regions_by_size() cleanup_mode must be a string constant: "
                            "'none', 'erosion', or 'opening'."
                        ),
                    )
                mode_key = str(mode_arg.value).strip().lower()
                if mode_key not in MASK_CLEANUP_MODES:
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message="filter_regions_by_size() cleanup_mode must be one of: 'none', 'erosion', 'opening'.",
                        details={"cleanup_mode": mode_arg.value},
                    )
            if len(node.args) == 5:
                iter_arg = node.args[4]
                if not isinstance(iter_arg, ast.Constant) or not isinstance(iter_arg.value, (int, float)):
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message="filter_regions_by_size() cleanup_iterations must be a numeric constant >= 0.",
                    )
                iteration_value = float(iter_arg.value)
                iteration_int = int(iteration_value)
                if iteration_value != float(iteration_int) or iteration_int < 0:
                    raise MapAlgebraError(
                        code="map_algebra_invalid_argument",
                        message="filter_regions_by_size() cleanup_iterations must be an integer >= 0.",
                        details={"cleanup_iterations": iter_arg.value},
                    )
            return "mask"
        if fn == "find_borders":
            if len(node.args) != 1:
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message="find_borders() requires exactly 1 argument.",
                )
            _validate_node(node.args[0], state=state, depth=depth + 1)
            return "mask"
        if fn == "hillshade":
            if len(node.args) != 3:
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message="hillshade() requires exactly 3 arguments: raster, azimuth_deg, elevation_deg.",
                )
            _validate_node(node.args[0], state=state, depth=depth + 1)
            _validate_node(node.args[1], state=state, depth=depth + 1)
            _validate_node(node.args[2], state=state, depth=depth + 1)
            return "byte"
        if fn in {"min", "max", "avg", "std"}:
            if len(node.args) != 1:
                raise MapAlgebraError(
                    code="map_algebra_invalid_argument",
                    message=f"{fn}() requires exactly 1 argument.",
                )
            _validate_node(node.args[0], state=state, depth=depth + 1)
            return "continuous"
        raise MapAlgebraError(
            code="map_algebra_unknown_function",
            message=f"Unsupported function: {fn}",
            details={"function": fn},
        )
    if isinstance(node, ast.Attribute):
        raise MapAlgebraError(
            code="map_algebra_disallowed_syntax",
            message="Attribute access is not allowed in map algebra expressions.",
        )
    if isinstance(node, (ast.BoolOp, ast.IfExp, ast.Subscript, ast.Dict, ast.List, ast.Tuple, ast.Set)):
        raise MapAlgebraError(
            code="map_algebra_disallowed_syntax",
            message=f"Unsupported syntax node: {type(node).__name__}",
        )
    raise MapAlgebraError(
        code="map_algebra_disallowed_syntax",
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
        raise MapAlgebraError(
            code="map_algebra_disallowed_syntax",
            message=f"Unsupported operator: {key.__name__}",
        )
    return mapping[key]


class _ExpressionEvaluator:
    def __init__(self, *, variables: dict[str, np.ndarray], target_shape: tuple[int, int], transform: Any) -> None:
        self._variables = variables
        self._target_shape = target_shape
        self._transform = transform

    def eval(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Name):
            return self._variables[str(node.id)]
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return bool(node.value)
            if isinstance(node.value, str):
                return str(node.value)
            return float(node.value)
        if isinstance(node, ast.BinOp):
            left = self.eval(node.left)
            right = self.eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow):
                return left ** right
            if isinstance(node.op, ast.BitAnd):
                return np.logical_and(np.asarray(left, dtype=bool), np.asarray(right, dtype=bool))
            if isinstance(node.op, ast.BitOr):
                return np.logical_or(np.asarray(left, dtype=bool), np.asarray(right, dtype=bool))
            raise MapAlgebraError(
                code="map_algebra_disallowed_syntax",
                message=f"Unsupported operator: {type(node.op).__name__}",
            )
        if isinstance(node, ast.UnaryOp):
            value = self.eval(node.operand)
            if isinstance(node.op, ast.Invert):
                return np.logical_not(np.asarray(value, dtype=bool))
            if isinstance(node.op, ast.UAdd):
                return +value
            if isinstance(node.op, ast.USub):
                return -value
            raise MapAlgebraError(
                code="map_algebra_disallowed_syntax",
                message=f"Unsupported unary operator: {type(node.op).__name__}",
            )
        if isinstance(node, ast.Compare):
            left = self.eval(node.left)
            right = self.eval(node.comparators[0])
            op = node.ops[0]
            if isinstance(op, ast.Gt):
                return left > right
            if isinstance(op, ast.GtE):
                return left >= right
            if isinstance(op, ast.Lt):
                return left < right
            if isinstance(op, ast.LtE):
                return left <= right
            if isinstance(op, ast.Eq):
                return left == right
            if isinstance(op, ast.NotEq):
                return left != right
            raise MapAlgebraError(
                code="map_algebra_disallowed_syntax",
                message=f"Unsupported comparison operator: {type(op).__name__}",
            )
        if isinstance(node, ast.Call):
            fn_name = str(node.func.id) if isinstance(node.func, ast.Name) else ""
            if fn_name in NODATA_FUNCTION_ALIASES:
                return np.float64(np.nan)
            if fn_name == "where":
                cond = np.asarray(self.eval(node.args[0]), dtype=bool)
                true_value = self.eval(node.args[1])
                false_value = self.eval(node.args[2])
                return np.where(cond, true_value, false_value)
            if fn_name == "slope":
                return self._slope(self.eval(node.args[0]))
            if fn_name == "aspect":
                return self._aspect(self.eval(node.args[0]))
            if fn_name == "hillshade":
                return self._hillshade(
                    self.eval(node.args[0]),
                    self.eval(node.args[1]),
                    self.eval(node.args[2]),
                )
            if fn_name == "label_regions":
                cleanup_mode = "none"
                cleanup_iterations = 1
                if len(node.args) >= 2:
                    cleanup_mode = str(self.eval(node.args[1]))
                if len(node.args) == 3:
                    cleanup_iterations = int(np.asarray(self.eval(node.args[2]), dtype=np.int64))
                return self._label_regions(
                    self.eval(node.args[0]),
                    cleanup_mode=cleanup_mode,
                    cleanup_iterations=cleanup_iterations,
                )
            if fn_name == "region_sizes":
                cleanup_mode = "none"
                cleanup_iterations = 1
                if len(node.args) >= 2:
                    cleanup_mode = str(self.eval(node.args[1]))
                if len(node.args) == 3:
                    cleanup_iterations = int(np.asarray(self.eval(node.args[2]), dtype=np.int64))
                return self._region_sizes(
                    self.eval(node.args[0]),
                    cleanup_mode=cleanup_mode,
                    cleanup_iterations=cleanup_iterations,
                )
            if fn_name == "filter_regions_by_size":
                cleanup_mode = "none"
                cleanup_iterations = 1
                if len(node.args) >= 4:
                    cleanup_mode = str(self.eval(node.args[3]))
                if len(node.args) == 5:
                    cleanup_iterations = int(np.asarray(self.eval(node.args[4]), dtype=np.int64))
                return self._filter_regions_by_size(
                    self.eval(node.args[0]),
                    self.eval(node.args[1]),
                    str(self.eval(node.args[2])),
                    cleanup_mode=cleanup_mode,
                    cleanup_iterations=cleanup_iterations,
                )
            if fn_name == "find_borders":
                return self._find_borders(self.eval(node.args[0]))
            if fn_name == "min":
                return self._reduce_time(self.eval(node.args[0]), np.nanmin, "min")
            if fn_name == "max":
                return self._reduce_time(self.eval(node.args[0]), np.nanmax, "max")
            if fn_name == "avg":
                return self._reduce_time(self.eval(node.args[0]), np.nanmean, "avg")
            if fn_name == "std":
                return self._reduce_time(self.eval(node.args[0]), np.nanstd, "std")
            raise MapAlgebraError(
                code="map_algebra_unknown_function",
                message=f"Unsupported function: {fn_name}",
                details={"function": fn_name},
            )
        raise MapAlgebraError(
            code="map_algebra_disallowed_syntax",
            message=f"Unsupported syntax node at evaluation: {type(node).__name__}",
        )

    def _xres_yres(self) -> tuple[float, float]:
        xres = abs(float(getattr(self._transform, "a", 1.0) or 1.0))
        yres = abs(float(getattr(self._transform, "e", -1.0) or 1.0))
        if xres <= 0.0:
            xres = 1.0
        if yres <= 0.0:
            yres = 1.0
        return xres, yres

    def _gdal_geotransform(self) -> tuple[float, float, float, float, float, float]:
        transform = self._transform
        try:
            return (
                float(getattr(transform, "c")),
                float(getattr(transform, "a")),
                float(getattr(transform, "b")),
                float(getattr(transform, "f")),
                float(getattr(transform, "d")),
                float(getattr(transform, "e")),
            )
        except Exception:
            return (0.0, 1.0, 0.0, 0.0, 0.0, -1.0)

    def _terrain_derivative_with_gdal(
        self,
        *,
        operation: str,
        arr: np.ndarray,
        azimuth_deg: float | None = None,
        elevation_deg: float | None = None,
    ) -> np.ndarray | None:
        try:
            from osgeo import gdal
        except Exception:
            return None
        try:
            gdal.UseExceptions()
            src = np.asarray(arr, dtype=np.float32)
            invalid_mask = ~np.isfinite(src)
            src_nodata = -9999.0
            if bool(np.any(invalid_mask)):
                src = np.array(src, copy=True)
                src[invalid_mask] = np.float32(src_nodata)
            src_ds = gdal.GetDriverByName("MEM").Create("", int(src.shape[1]), int(src.shape[0]), 1, gdal.GDT_Float32)
            if src_ds is None:
                return None
            src_ds.SetGeoTransform(self._gdal_geotransform())
            src_band = src_ds.GetRasterBand(1)
            src_band.WriteArray(src)
            src_band.SetNoDataValue(float(src_nodata))
            options_kwargs: dict[str, Any] = {"format": "MEM", "computeEdges": True}
            if operation == "hillshade":
                options_kwargs["azimuth"] = float(azimuth_deg if azimuth_deg is not None else 315.0)
                options_kwargs["altitude"] = float(elevation_deg if elevation_deg is not None else 45.0)
            elif operation == "slope":
                options_kwargs["slopeFormat"] = "degree"
            out_ds = gdal.DEMProcessing("", src_ds, operation, options=gdal.DEMProcessingOptions(**options_kwargs))
            if out_ds is None:
                return None
            out_band = out_ds.GetRasterBand(1)
            out = out_band.ReadAsArray()
            if out is None:
                return None
            out_nodata = out_band.GetNoDataValue()
            if operation == "hillshade":
                out_u8 = np.asarray(out, dtype=np.uint8)
                if out_nodata is not None:
                    out_u8 = np.array(out_u8, copy=True)
                    out_u8[np.asarray(out) == out_nodata] = np.uint8(0)
                return out_u8
            out_f32 = np.asarray(out, dtype=np.float32)
            if out_nodata is not None:
                out_f32 = np.array(out_f32, copy=True)
                out_f32[np.asarray(out) == out_nodata] = np.float32(np.nan)
            return out_f32
        except Exception as exc:
            logger.debug("gdal terrain derivative fallback operation=%s reason=%s", operation, str(exc))
            return None

    def _slope(self, value: Any) -> np.ndarray:
        arr = np.asarray(value, dtype=np.float64)
        if arr.ndim != 2:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="slope() expects a 2D raster input.",
                details={"ndim": int(arr.ndim)},
            )
        gdal_result = self._terrain_derivative_with_gdal(operation="slope", arr=arr)
        if gdal_result is not None:
            return np.asarray(gdal_result, dtype=np.float32)
        xres, yres = self._xres_yres()
        dz_dy, dz_dx = np.gradient(arr, yres, xres)
        slope_rad = np.arctan(np.hypot(dz_dx, dz_dy))
        return np.degrees(slope_rad).astype(np.float32)

    def _aspect(self, value: Any) -> np.ndarray:
        arr = np.asarray(value, dtype=np.float64)
        if arr.ndim != 2:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="aspect() expects a 2D raster input.",
                details={"ndim": int(arr.ndim)},
            )
        gdal_result = self._terrain_derivative_with_gdal(operation="aspect", arr=arr)
        if gdal_result is not None:
            return np.asarray(gdal_result, dtype=np.float32)
        xres, yres = self._xres_yres()
        dz_dy, dz_dx = np.gradient(arr, yres, xres)
        aspect = 90.0 - np.degrees(np.arctan2(dz_dy, -dz_dx))
        aspect = np.where(aspect < 0.0, aspect + 360.0, aspect)
        aspect = np.where(aspect >= 360.0, aspect - 360.0, aspect)
        return aspect.astype(np.float32)

    def _hillshade(self, value: Any, azimuth_deg: Any, elevation_deg: Any) -> np.ndarray:
        logger.warning("_hillshade entered value=%s azimuth_deg=%s elevation_deg=%s", value, azimuth_deg, elevation_deg )
        arr = np.asarray(value, dtype=np.float64)
        if arr.ndim != 2:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="hillshade() expects a 2D raster input.",
                details={"ndim": int(arr.ndim)},
            )
        azimuth = float(np.asarray(azimuth_deg, dtype=np.float64))
        elevation = float(np.asarray(elevation_deg, dtype=np.float64))
        gdal_result = self._terrain_derivative_with_gdal(
            operation="hillshade",
            arr=arr,
            azimuth_deg=azimuth,
            elevation_deg=elevation,
        )
        if gdal_result is not None:
            return np.asarray(gdal_result, dtype=np.uint8)
        xres, yres = self._xres_yres()
        dz_dy, dz_dx = np.gradient(arr, yres, xres)
        slope_rad = np.arctan(np.hypot(dz_dx, dz_dy))
        aspect_rad = np.arctan2(dz_dy, -dz_dx)
        azimuth_math = np.radians(360.0 - azimuth + 90.0)
        zenith_rad = np.radians(90.0 - elevation)
        shaded = (
            np.cos(zenith_rad) * np.cos(slope_rad)
            + np.sin(zenith_rad) * np.sin(slope_rad) * np.cos(azimuth_math - aspect_rad)
        )
        output = np.clip(255.0 * shaded, 0.0, 255.0)
        logger.warning("finished _hillshade")
        return output.astype(np.uint8)

    def _label_regions(
        self,
        value: Any,
        *,
        cleanup_mode: str = "none",
        cleanup_iterations: int = 1,
    ) -> np.ndarray:
        arr = np.asarray(value)
        if arr.ndim != 2:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="label_regions() expects a 2D raster input.",
                details={"ndim": int(arr.ndim)},
            )
        from scipy import ndimage  # type: ignore

        mask = np.asarray(arr, dtype=bool)
        mode_key = str(cleanup_mode or "none").strip().lower() or "none"
        if mode_key not in MASK_CLEANUP_MODES:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="label_regions() cleanup_mode must be one of: 'none', 'erosion', 'opening'.",
                details={"cleanup_mode": cleanup_mode},
            )
        iterations = int(cleanup_iterations or 0)
        if iterations < 0:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="label_regions() cleanup_iterations must be an integer >= 0.",
                details={"cleanup_iterations": cleanup_iterations},
            )
        if mode_key in {"erosion", "opening"} and iterations > 0:
            structure_bool = np.ones((3, 3), dtype=bool)
            if mode_key == "erosion":
                mask = ndimage.binary_erosion(mask, structure=structure_bool, iterations=iterations)
            else:
                mask = ndimage.binary_opening(mask, structure=structure_bool, iterations=iterations)
        structure = np.ones((3, 3), dtype=np.uint8)
        labels, _ = ndimage.label(mask, structure=structure)
        return np.asarray(labels, dtype=np.int32)

    def _find_borders(self, value: Any) -> np.ndarray:
        arr = np.asarray(value)
        if arr.ndim != 2:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="find_borders() expects a 2D raster input.",
                details={"ndim": int(arr.ndim)},
            )
        from scipy import ndimage  # type: ignore

        mask = np.asarray(arr, dtype=bool)
        structure = np.ones((3, 3), dtype=bool)
        eroded = ndimage.binary_erosion(mask, structure=structure, border_value=0)
        borders = np.logical_and(mask, np.logical_not(eroded))
        return borders

    def _region_sizes(
        self,
        value: Any,
        *,
        cleanup_mode: str = "none",
        cleanup_iterations: int = 1,
    ) -> np.ndarray:
        labels = self._label_regions(
            value,
            cleanup_mode=cleanup_mode,
            cleanup_iterations=cleanup_iterations,
        )
        counts = np.bincount(labels.ravel())
        sized = counts[labels]
        sized[labels == 0] = 0
        return np.asarray(sized, dtype=np.int32)

    def _filter_regions_by_size(
        self,
        value: Any,
        threshold: Any,
        comparator: str,
        *,
        cleanup_mode: str = "none",
        cleanup_iterations: int = 1,
    ) -> np.ndarray:
        arr = np.asarray(value)
        if arr.ndim != 2:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="filter_regions_by_size() expects a 2D raster input.",
                details={"ndim": int(arr.ndim)},
            )
        threshold_value = float(np.asarray(threshold, dtype=np.float64))
        if threshold_value < 0.0:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="filter_regions_by_size() threshold must be >= 0.",
                details={"threshold": threshold},
            )
        comp = str(comparator).strip()
        if comp not in {">=", "<="}:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message="filter_regions_by_size() comparator must be one of: '>=', '<='.",
                details={"comparator": comparator},
            )
        from scipy import ndimage  # type: ignore

        mask_original = np.asarray(arr, dtype=bool)
        labels_original, _ = ndimage.label(mask_original, structure=np.ones((3, 3), dtype=np.uint8))
        if labels_original.size == 0:
            return np.zeros_like(mask_original, dtype=bool)

        labels_seed = self._label_regions(
            value,
            cleanup_mode=cleanup_mode,
            cleanup_iterations=cleanup_iterations,
        )
        seed_counts = np.bincount(labels_seed.ravel())
        keep_seed_ids = seed_counts >= threshold_value if comp == ">=" else seed_counts <= threshold_value
        if keep_seed_ids.size > 0:
            keep_seed_ids[0] = False
        keep_seed_pixels = keep_seed_ids[labels_seed]
        kept_original_ids = np.unique(labels_original[keep_seed_pixels])
        if kept_original_ids.size == 0:
            return np.zeros_like(mask_original, dtype=bool)
        keep_original_ids = np.zeros(int(labels_original.max()) + 1, dtype=bool)
        keep_original_ids[kept_original_ids] = True
        keep_original_ids[0] = False
        return keep_original_ids[labels_original]

    def _reduce_time(
        self,
        value: Any,
        reducer: Callable[..., np.ndarray],
        reducer_name: str,
    ) -> np.ndarray:
        arr = np.asarray(value, dtype=np.float64)
        if arr.ndim != 3:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message=f"{reducer_name}() expects a 3D temporal raster input [time, height, width].",
                details={"ndim": int(arr.ndim)},
            )
        try:
            reduced = reducer(arr, axis=0)
        except Exception as exc:
            raise MapAlgebraError(
                code="map_algebra_invalid_argument",
                message=f"{reducer_name}() failed to reduce temporal raster.",
                details={"error": str(exc)},
            ) from exc
        return np.asarray(reduced, dtype=np.float32)
