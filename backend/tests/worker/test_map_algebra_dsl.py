from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.jobs.map_algebra import (
    AlignedRaster,
    MapAlgebraError,
    TargetGrid,
    evaluate_expression,
    evaluate_expression_for_variables,
    finalize_output_array,
    parse_validate_expression,
)


def test_parse_validate_expression_collects_usage() -> None:
    parsed = parse_validate_expression(
        "where((a > 5) & (b < 10), slope(a), b)",
        allowed_variables={"a", "b"},
    )
    assert parsed.semantic == "continuous"
    assert parsed.used_variables == {"a", "b"}
    assert parsed.used_functions == {"where", "slope"}
    assert "&" in parsed.used_operators
    assert ">" in parsed.used_operators
    assert "<" in parsed.used_operators


def test_parse_validate_expression_rejects_attribute_access() -> None:
    with pytest.raises(MapAlgebraError) as exc:
        parse_validate_expression("a.__class__", allowed_variables={"a"})
    assert exc.value.code == "map_algebra_disallowed_syntax"


def test_parse_validate_expression_rejects_unknown_variable() -> None:
    with pytest.raises(MapAlgebraError) as exc:
        parse_validate_expression("a + c", allowed_variables={"a", "b"})
    assert exc.value.code == "map_algebra_unknown_variable"


def test_evaluate_expression_and_finalize_mask_dtype() -> None:
    parsed = parse_validate_expression("(a > 1) & (b < 3)", allowed_variables={"a", "b"})
    aligned = {
        "a": AlignedRaster(
            variable="a",
            path=Path(__file__),
            data=np.array([[0, 2], [3, 1]], dtype=np.float32),
            nodata_mask=np.array([[False, False], [False, False]], dtype=bool),
            reprojected=False,
            source_crs="EPSG:4326",
            source_transform=(0, 1, 0, 0, 0, -1),
            source_nodata=None,
        ),
        "b": AlignedRaster(
            variable="b",
            path=Path(__file__),
            data=np.array([[2, 2], [5, 1]], dtype=np.float32),
            nodata_mask=np.array([[False, False], [False, False]], dtype=bool),
            reprojected=False,
            source_crs="EPSG:4326",
            source_transform=(0, 1, 0, 0, 0, -1),
            source_nodata=None,
        ),
    }
    target = TargetGrid(
        crs="EPSG:4326",
        transform=(0, 1, 0, 0, 0, -1),
        width=2,
        height=2,
        dem_path=Path(__file__),
    )
    evaluated = evaluate_expression(parsed=parsed, aligned_inputs=aligned, target_grid=target)
    out, dtype_name, nodata = finalize_output_array(
        result=evaluated,
        semantic=parsed.semantic,
        used_variables=parsed.used_variables,
        aligned_inputs=aligned,
    )
    assert dtype_name == "uint8"
    assert nodata is None
    assert out.dtype == np.uint8
    assert out.tolist() == [[0, 1], [0, 0]]


def test_temporal_reducer_avg_returns_2d_raster() -> None:
    parsed = parse_validate_expression("avg(light)", allowed_variables={"light"})
    result = evaluate_expression_for_variables(
        parsed=parsed,
        variables={
            "light": np.array(
                [
                    [[0.0, 10.0], [20.0, 30.0]],
                    [[10.0, 30.0], [40.0, 50.0]],
                ],
                dtype=np.float32,
            )
        },
        target_shape=(2, 2),
    )
    assert result.shape == (2, 2)
    assert float(result[0, 1]) == pytest.approx(20.0)


def test_temporal_reducer_requires_3d_input() -> None:
    parsed = parse_validate_expression("std(a)", allowed_variables={"a"})
    with pytest.raises(MapAlgebraError) as exc:
        evaluate_expression_for_variables(
            parsed=parsed,
            variables={"a": np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)},
            target_shape=(2, 2),
        )
    assert exc.value.code == "map_algebra_invalid_argument"


def test_hillshade_handles_nan_nodata_values() -> None:
    parsed = parse_validate_expression("hillshade(dem, 315, 45)", allowed_variables={"dem"})
    dem = np.array(
        [
            [np.nan, np.nan, np.nan],
            [np.nan, 10.0, np.nan],
            [np.nan, np.nan, np.nan],
        ],
        dtype=np.float32,
    )
    result = evaluate_expression_for_variables(
        parsed=parsed,
        variables={"dem": dem},
        target_shape=(3, 3),
    )
    out, dtype_name, nodata = finalize_output_array(
        result=result,
        semantic=parsed.semantic,
        used_variables=parsed.used_variables,
    )
    assert dtype_name == "uint8"
    assert nodata is None
    assert out.shape == (3, 3)


@pytest.mark.parametrize("fn_name", ["nodata", "nan", "null"])
def test_nodata_aliases_produce_continuous_output_with_nodata(fn_name: str) -> None:
    parsed = parse_validate_expression(
        f"where(a > 1, 1, {fn_name}())",
        allowed_variables={"a"},
    )
    result = evaluate_expression_for_variables(
        parsed=parsed,
        variables={"a": np.array([[0.0, 2.0], [3.0, 1.0]], dtype=np.float32)},
        target_shape=(2, 2),
    )
    out, dtype_name, nodata = finalize_output_array(
        result=result,
        semantic=parsed.semantic,
        used_variables=parsed.used_variables,
    )
    assert parsed.semantic == "continuous"
    assert fn_name in parsed.used_functions
    assert dtype_name == "float32"
    assert nodata == -9999.0
    assert out.dtype == np.float32
    assert out.tolist() == [[-9999.0, 1.0], [1.0, -9999.0]]


def test_label_regions_returns_label_semantic_and_int_output() -> None:
    parsed = parse_validate_expression("label_regions(mask)", allowed_variables={"mask"})
    assert parsed.semantic == "labels"
    result = evaluate_expression_for_variables(
        parsed=parsed,
        variables={
            "mask": np.array(
                [
                    [1, 1, 0, 0],
                    [0, 1, 0, 1],
                    [0, 0, 0, 1],
                ],
                dtype=np.uint8,
            )
        },
        target_shape=(3, 4),
    )
    out, dtype_name, nodata = finalize_output_array(
        result=result,
        semantic=parsed.semantic,
        used_variables=parsed.used_variables,
    )
    assert dtype_name == "int32"
    assert nodata is None
    assert out.dtype == np.int32
    # 8-connectivity: left cluster gets id 1, right cluster gets id 2.
    assert out.tolist() == [
        [1, 1, 0, 0],
        [0, 1, 0, 2],
        [0, 0, 0, 2],
    ]


def test_find_borders_returns_inner_border_mask() -> None:
    parsed = parse_validate_expression("find_borders(mask)", allowed_variables={"mask"})
    assert parsed.semantic == "mask"
    result = evaluate_expression_for_variables(
        parsed=parsed,
        variables={
            "mask": np.array(
                [
                    [0, 0, 0, 0, 0],
                    [0, 1, 1, 1, 0],
                    [0, 1, 1, 1, 0],
                    [0, 1, 1, 1, 0],
                    [0, 0, 0, 0, 0],
                ],
                dtype=np.uint8,
            )
        },
        target_shape=(5, 5),
    )
    out, dtype_name, nodata = finalize_output_array(
        result=result,
        semantic=parsed.semantic,
        used_variables=parsed.used_variables,
    )
    assert dtype_name == "uint8"
    assert nodata is None
    assert out.tolist() == [
        [0, 0, 0, 0, 0],
        [0, 1, 1, 1, 0],
        [0, 1, 0, 1, 0],
        [0, 1, 1, 1, 0],
        [0, 0, 0, 0, 0],
    ]


def test_label_regions_with_erosion_breaks_one_pixel_bridge() -> None:
    parsed = parse_validate_expression(
        'label_regions(mask, "erosion", 1)',
        allowed_variables={"mask"},
    )
    result = evaluate_expression_for_variables(
        parsed=parsed,
        variables={
            "mask": np.array(
                [
                    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                    [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                    [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                    [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0],
                    [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                    [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                ],
                dtype=np.uint8,
            )
        },
        target_shape=(7, 13),
    )
    assert result.dtype == np.int32
    assert int(result.max()) == 2
    assert int(result[3, 3]) != int(result[3, 9])


def test_label_regions_rejects_invalid_cleanup_mode() -> None:
    with pytest.raises(MapAlgebraError) as exc:
        parse_validate_expression(
            'label_regions(mask, "bad_mode", 1)',
            allowed_variables={"mask"},
        )
    assert exc.value.code == "map_algebra_invalid_argument"
    assert "cleanup_mode" in exc.value.message


def test_region_sizes_returns_component_size_raster() -> None:
    parsed = parse_validate_expression("region_sizes(mask)", allowed_variables={"mask"})
    assert parsed.semantic == "labels"
    result = evaluate_expression_for_variables(
        parsed=parsed,
        variables={
            "mask": np.array(
                [
                    [1, 1, 0, 0],
                    [0, 1, 0, 1],
                    [0, 0, 0, 1],
                ],
                dtype=np.uint8,
            )
        },
        target_shape=(3, 4),
    )
    assert result.dtype == np.int32
    assert result.tolist() == [
        [3, 3, 0, 0],
        [0, 3, 0, 2],
        [0, 0, 0, 2],
    ]


def test_region_sizes_with_erosion_breaks_one_pixel_bridge() -> None:
    parsed = parse_validate_expression(
        'region_sizes(mask, "erosion", 1)',
        allowed_variables={"mask"},
    )
    result = evaluate_expression_for_variables(
        parsed=parsed,
        variables={
            "mask": np.array(
                [
                    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                    [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                    [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                    [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0],
                    [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                    [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                ],
                dtype=np.uint8,
            )
        },
        target_shape=(7, 13),
    )
    # Each retained component is a 3x3 block after one erosion.
    assert int(result[3, 3]) == 9
    assert int(result[3, 9]) == 9
    assert int(result[3, 6]) == 0


def test_region_sizes_rejects_invalid_cleanup_mode() -> None:
    with pytest.raises(MapAlgebraError) as exc:
        parse_validate_expression(
            'region_sizes(mask, "bad_mode", 1)',
            allowed_variables={"mask"},
        )
    assert exc.value.code == "map_algebra_invalid_argument"
    assert "cleanup_mode" in exc.value.message


def test_filter_regions_by_size_gte_keeps_large_component() -> None:
    parsed = parse_validate_expression(
        'filter_regions_by_size(mask, 3, ">=")',
        allowed_variables={"mask"},
    )
    assert parsed.semantic == "mask"
    result = evaluate_expression_for_variables(
        parsed=parsed,
        variables={
            "mask": np.array(
                [
                    [1, 1, 0, 0],
                    [0, 1, 0, 1],
                    [0, 0, 0, 1],
                ],
                dtype=np.uint8,
            )
        },
        target_shape=(3, 4),
    )
    assert result.dtype == np.bool_
    assert result.tolist() == [
        [True, True, False, False],
        [False, True, False, False],
        [False, False, False, False],
    ]


def test_filter_regions_by_size_lte_keeps_small_component() -> None:
    parsed = parse_validate_expression(
        'filter_regions_by_size(mask, 2, "<=")',
        allowed_variables={"mask"},
    )
    result = evaluate_expression_for_variables(
        parsed=parsed,
        variables={
            "mask": np.array(
                [
                    [1, 1, 0, 0],
                    [0, 1, 0, 1],
                    [0, 0, 0, 1],
                ],
                dtype=np.uint8,
            )
        },
        target_shape=(3, 4),
    )
    assert result.tolist() == [
        [False, False, False, False],
        [False, False, False, True],
        [False, False, False, True],
    ]


def test_filter_regions_by_size_with_cleanup_preserves_original_shape() -> None:
    parsed = parse_validate_expression(
        'filter_regions_by_size(mask, 9, ">=", "erosion", 1)',
        allowed_variables={"mask"},
    )
    result = evaluate_expression_for_variables(
        parsed=parsed,
        variables={
            "mask": np.array(
                [
                    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                    [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                    [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                    [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0],
                    [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                    [0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                ],
                dtype=np.uint8,
            )
        },
        target_shape=(7, 13),
    )
    # Cleanup breaks the 1-pixel bridge for selection, but output keeps full original blobs.
    assert bool(result[3, 6]) is True
    assert bool(result[1, 1]) is True
    assert bool(result[1, 11]) is True


@pytest.mark.parametrize(
    ("fn_name", "expected_message"),
    [
        (
            "label_regions",
            "label_regions() requires 1 to 3 arguments: mask[, cleanup_mode[, cleanup_iterations]].",
        ),
        (
            "region_sizes",
            "region_sizes() requires 1 to 3 arguments: mask[, cleanup_mode[, cleanup_iterations]].",
        ),
        (
            "filter_regions_by_size",
            (
                "filter_regions_by_size() requires 3 to 5 arguments: "
                "mask, threshold, comparator[, cleanup_mode[, cleanup_iterations]]."
            ),
        ),
        ("find_borders", "find_borders() requires exactly 1 argument."),
    ],
)
def test_new_region_functions_enforce_arity(fn_name: str, expected_message: str) -> None:
    with pytest.raises(MapAlgebraError) as exc:
        parse_validate_expression(f"{fn_name}()", allowed_variables={"mask"})
    assert exc.value.code == "map_algebra_invalid_argument"
    assert expected_message in exc.value.message
