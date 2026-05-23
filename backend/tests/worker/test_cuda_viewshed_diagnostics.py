from __future__ import annotations

import pytest

from backend.tools.cuda_viewshed_diagnostics import (
    STAGE_ORDER,
    LadderConfig,
    _build_kernel_inputs,
    _parse_stage_names,
)


def test_parse_stage_names_all_returns_full_order() -> None:
    assert _parse_stage_names("all") == list(STAGE_ORDER)
    assert _parse_stage_names("*") == list(STAGE_ORDER)


def test_parse_stage_names_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown stages"):
        _parse_stage_names("k0_noop,unknown_stage")


def test_build_kernel_inputs_shapes_and_observers() -> None:
    cfg = LadderConfig(
        rows=64,
        cols=128,
        observer_count=3,
        observer_row=1.0,
        observer_col_start=10.0,
        observer_height=2.5,
        direction_count=8,
    )
    inputs = _build_kernel_inputs(cfg)
    assert inputs["dem"].shape == (64, 128)
    assert inputs["observers"].shape == (3, 3)
    assert inputs["directions"].shape == (8, 2)
    assert inputs["ray_obs_indices"].shape == (24,)
    assert inputs["ray_dir_indices"].shape == (24,)
    assert inputs["out_i32"].shape == (24,)
    assert inputs["out_f32"].shape == (24,)
    assert float(inputs["observers"][0, 2]) == pytest.approx(2.5)
    assert float(inputs["observers"][1, 1]) == pytest.approx(11.0)
