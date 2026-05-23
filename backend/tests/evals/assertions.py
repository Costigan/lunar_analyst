from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any

from backend.core.crs_semantics import crs_semantically_equivalent


def assert_files_exist(scenario_root: Path, relative_paths: list[str]) -> None:
    for rel in relative_paths:
        target = (scenario_root / rel).resolve()
        assert target.exists(), f"required precondition path missing: {target}"


def ensure_files_absent(scenario_root: Path, relative_paths: list[str]) -> None:
    for rel in relative_paths:
        target = (scenario_root / rel).resolve()
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        assert not target.exists(), f"precondition cleanup failed, path still exists: {target}"


def _lookup_dotted(obj: dict[str, Any], dotted: str) -> bool:
    node: Any = obj
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _primary_tool(prediction: dict[str, Any]) -> str | None:
    primary = prediction.get("primary_tool")
    if primary:
        return str(primary)
    calls = prediction.get("tool_calls", [])
    if isinstance(calls, list) and calls and isinstance(calls[0], dict):
        return str(calls[0].get("name") or "") or None
    return None


def assert_prediction_contract(
    prediction: dict[str, Any],
    *,
    expected_mode: str,
    expected_primary_tool: str | None = None,
    allowed_primary_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
    required_args: list[str] | None = None,
    expects_unsafe_block: bool = False,
) -> None:
    mode = str(prediction.get("mode", ""))
    assert mode == expected_mode, f"mode mismatch: expected={expected_mode} actual={mode}"

    allowed = set(allowed_primary_tools or [])
    disallowed = set(disallowed_tools or [])
    required = list(required_args or [])

    if expected_mode == "tool_call":
        primary = _primary_tool(prediction)
        assert primary, "expected tool_call but no primary tool was produced"
        if expected_primary_tool:
            assert (
                primary == expected_primary_tool or primary in allowed
            ), f"primary tool mismatch: expected={expected_primary_tool} allowed={sorted(allowed)} actual={primary}"
        assert primary not in disallowed, f"primary tool is disallowed: {primary}"

        if required:
            calls = prediction.get("tool_calls", [])
            first = calls[0] if isinstance(calls, list) and calls and isinstance(calls[0], dict) else {}
            args = first.get("arguments", {}) if isinstance(first, dict) else {}
            missing = [item for item in required if not _lookup_dotted(args if isinstance(args, dict) else {}, item)]
            assert not missing, f"missing required args: {missing}"

    if expects_unsafe_block:
        assert bool(prediction.get("unsafe_blocked", False)), "expected unsafe block but unsafe_blocked=false"


def assert_functional_postconditions(*, prediction: dict[str, Any], services: Any, postconditions: dict[str, Any]) -> None:
    scenario_id = str(prediction.get("scenario_id_used", "") or "").strip()
    assert scenario_id, "scenario_id_used missing in prediction"
    scenario = services.scenario_service.get_scenario(scenario_id)
    scenario_root = Path(str(scenario.directory)).resolve()

    for rel in list(postconditions.get("must_create_files", []) or []):
        target = (scenario_root / str(rel)).resolve()
        assert target.exists(), f"expected output not found: {target}"

    raster = postconditions.get("raster_constraints") if isinstance(postconditions.get("raster_constraints"), dict) else None
    if raster:
        path = (scenario_root / str(raster.get("path", ""))).resolve()
        assert path.exists(), f"expected raster missing: {path}"
        try:
            import rasterio

            with rasterio.open(path) as ds:
                expected_crs = str(raster.get("crs", "") or "").strip()
                if expected_crs:
                    assert crs_semantically_equivalent(
                        ds.crs, expected_crs
                    ), f"raster CRS mismatch: expected={expected_crs} actual={ds.crs}"
                min_bands = int(raster.get("min_bands", 0) or 0)
                if min_bands > 0:
                    assert int(ds.count) >= min_bands, f"raster band count {ds.count} < {min_bands}"
        except ImportError:
            pass

    vector = postconditions.get("vector_constraints") if isinstance(postconditions.get("vector_constraints"), dict) else None
    if vector:
        path = (scenario_root / str(vector.get("path", ""))).resolve()
        assert path.exists(), f"expected vector file missing: {path}"
        if path.suffix.lower() == ".geojson":
            payload = json.loads(path.read_text(encoding="utf-8"))
            features = payload.get("features", []) if isinstance(payload, dict) else []
            min_count = int(vector.get("min_feature_count", 0) or 0)
            assert len(features) >= min_count, f"feature count {len(features)} < {min_count}"
            expected_types = {str(t) for t in vector.get("geometry_types", [])}
            if expected_types:
                observed = {
                    str((feat.get("geometry") or {}).get("type", ""))
                    for feat in features
                    if isinstance(feat, dict)
                }
                assert observed & expected_types, (
                    f"geometry type mismatch: observed={sorted(observed)} expected_any={sorted(expected_types)}"
                )

    table = postconditions.get("table_constraints") if isinstance(postconditions.get("table_constraints"), dict) else None
    if table:
        path = (scenario_root / str(table.get("path", ""))).resolve()
        assert path.exists(), f"expected table file missing: {path}"
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            required_columns = [str(c) for c in table.get("required_columns", [])]
            for column in required_columns:
                assert column in (reader.fieldnames or []), f"missing table column: {column}"
            min_rows = int(table.get("min_rows", 0) or 0)
            assert len(rows) >= min_rows, f"table row count {len(rows)} < {min_rows}"

    plot = postconditions.get("plot_constraints") if isinstance(postconditions.get("plot_constraints"), dict) else None
    if plot:
        path = (scenario_root / str(plot.get("path", ""))).resolve()
        assert path.exists(), f"expected plot file missing: {path}"
        formats = [str(v).lower() for v in plot.get("formats", [])]
        if formats:
            assert path.suffix.lower().lstrip(".") in formats, f"plot format mismatch: {path.suffix} not in {formats}"

    state = postconditions.get("state_checks") if isinstance(postconditions.get("state_checks"), dict) else None
    if state:
        layers = services.layer_service.list_layers(scenario_id)
        by_file_rel: dict[str, Any] = {}
        for layer in layers:
            try:
                record = services.file_service.get_file(layer.source_file_id)
                by_file_rel[str(record.relative_path)] = layer
            except Exception:
                continue
            by_file_rel[str(layer.title)] = layer

        layer_path = str(state.get("layer_path", "") or "").strip()
        if layer_path:
            layer = by_file_rel.get(layer_path)
            assert layer is not None, f"layer not found for state check: {layer_path}"
            if "visible" in state:
                assert bool(layer.visible) == bool(state.get("visible")), f"layer visible mismatch for {layer_path}"
            if "opacity" in state:
                expected_opacity = float(state.get("opacity"))
                assert abs(float(layer.opacity) - expected_opacity) <= 1e-6, f"layer opacity mismatch for {layer_path}"

        order = state.get("order_constraint") if isinstance(state.get("order_constraint"), dict) else None
        if order:
            above_key = str(order.get("above", "") or "").strip()
            below_key = str(order.get("below", "") or "").strip()
            above = by_file_rel.get(above_key)
            below = by_file_rel.get(below_key)
            assert above is not None and below is not None, (
                f"order constraint layers missing: above={above_key} below={below_key}"
            )
            assert int(above.z_index) > int(below.z_index), f"order constraint failed: {above_key} not above {below_key}"


def assert_domain_postconditions(*, prediction: dict[str, Any], must_cite_channels: list[str] | None = None) -> None:
    response_text = str(prediction.get("response_text", "") or "").strip()
    assert response_text, "domain case produced empty response_text"

    required_channels = {str(x).strip() for x in (must_cite_channels or []) if str(x).strip()}
    if required_channels:
        refs = prediction.get("source_references", [])
        channels = {str((item or {}).get("channel", "")).strip() for item in refs if isinstance(item, dict)}
        missing = sorted(ch for ch in required_channels if ch not in channels)
        assert not missing, f"missing required source reference channels: {missing}"
