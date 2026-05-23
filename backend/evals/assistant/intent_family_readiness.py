from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


_TOOL_TO_FAMILY: dict[str, str] = {
    "layer.apply_colormap": "layer_style_update",
    "colormap.list": "layer_style_update",
    "layer.update_state": "layer_visibility_update",
    "artifact.describe_geotiff": "artifact_inspection",
    "artifact.preview_geotiff": "artifact_inspection",
    "artifact.stats_geotiff": "artifact_inspection",
    "artifact.describe_plot": "artifact_inspection",
    "artifact.describe_table": "artifact_inspection",
    "scenario.set_current": "scenario_context_management",
    "scenario.list": "scenario_context_management",
    "runs.get_status": "compute_job_control",
    "runs.get_logs": "compute_job_control",
    "runs.cancel": "compute_job_control",
    "jobs.run_predefined": "compute_job_control",
    "scenario.write_script": "programmatic_workflow_authoring",
    "scenario.run_script": "programmatic_workflow_authoring",
    "scenario.write_run_script": "programmatic_workflow_authoring",
    "scenario.run_marimo_notebook": "programmatic_workflow_authoring",
    "raster.calculate": "create_product",
    "raster.transform": "create_product",
    "generate_horizons": "create_product",
    "generate_psr_raster": "create_product",
    "generate_average_sun_fraction_raster": "create_product",
    "generate_earth_above_terrain_duration_raster": "create_product",
    "generate_combined_sun_earth_max_contiguous_duration_raster": "create_product",
    "terrain.viewshed": "surface_route_planning",
    "terrain.mask_connectivity_metrics": "surface_route_planning",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _extract_family_entries(row: dict[str, Any]) -> list[tuple[str, str]]:
    segments = row.get("intent_family_segments")
    if isinstance(segments, list):
        extracted: list[tuple[str, str]] = []
        for item in segments:
            if not isinstance(item, dict):
                continue
            family = str(item.get("intent_family", "") or "").strip()
            if not family:
                continue
            validation_status = str(item.get("validation_status", "") or "").strip() or "unknown"
            extracted.append((family, validation_status))
        if extracted:
            return extracted

    tool_calls = row.get("tool_calls")
    inferred: list[tuple[str, str]] = []
    if isinstance(tool_calls, list):
        seen: set[str] = set()
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name", "") or "").strip()
            family = _TOOL_TO_FAMILY.get(name)
            if family and family not in seen:
                seen.add(family)
                inferred.append((family, "inferred"))
    return inferred


def _safe_div(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return float(num) / float(den)


def compute_intent_family_readiness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    per_family: dict[str, dict[str, Any]] = {}
    for row in rows:
        mode = str(row.get("mode", "") or "").strip()
        overall_success = bool(row.get("overall_success", False))
        handling_mode = str(row.get("turn_handling_mode", "") or "").strip().lower()
        fallback_used = bool(row.get("fallback_used", False))
        clarification = mode == "clarify"

        entries = _extract_family_entries(row)
        for family, validation_status in entries:
            agg = per_family.setdefault(
                family,
                {
                    "samples": 0,
                    "validated_segments": 0,
                    "segments_observed": 0,
                    "mapping_successes": 0,
                    "clarifications": 0,
                    "fallback_to_model_events": 0,
                    "provider_fallback_events": 0,
                },
            )
            agg["samples"] += 1
            agg["segments_observed"] += 1
            if validation_status == "validated":
                agg["validated_segments"] += 1
            if mode == "tool_call" and overall_success:
                agg["mapping_successes"] += 1
            if clarification:
                agg["clarifications"] += 1
            if handling_mode == "model_tool_loop":
                agg["fallback_to_model_events"] += 1
            if fallback_used:
                agg["provider_fallback_events"] += 1

    families: dict[str, dict[str, Any]] = {}
    for family, agg in sorted(per_family.items()):
        samples = int(agg["samples"])
        segments_observed = int(agg["segments_observed"])
        validated = int(agg["validated_segments"])
        mapping_successes = int(agg["mapping_successes"])
        clarifications = int(agg["clarifications"])
        fallback_to_model = int(agg["fallback_to_model_events"])
        provider_fallback = int(agg["provider_fallback_events"])
        families[family] = {
            "samples": samples,
            "validation_rate": _safe_div(validated, segments_observed),
            "mapping_success_rate": _safe_div(mapping_successes, samples),
            "clarification_rate": _safe_div(clarifications, samples),
            "fallback_to_model_rate": _safe_div(fallback_to_model, samples),
            "provider_fallback_rate": _safe_div(provider_fallback, samples),
            "counts": {
                "validated_segments": validated,
                "segments_observed": segments_observed,
                "mapping_successes": mapping_successes,
                "clarifications": clarifications,
                "fallback_to_model_events": fallback_to_model,
                "provider_fallback_events": provider_fallback,
            },
        }

    return {
        "total_rows": len(rows),
        "families": families,
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = ["# Intent Family Readiness", ""]
    lines.append(f"- Total rows: `{int(report.get('total_rows', 0) or 0)}`")
    lines.append("")
    lines.append("| Family | Samples | Validation | Mapping Success | Clarification | Fallback->Model | Provider Fallback |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    families = report.get("families", {})
    if isinstance(families, dict):
        for family, payload in sorted(families.items()):
            if not isinstance(payload, dict):
                continue
            lines.append(
                "| {family} | {samples} | {validation:.1%} | {mapping:.1%} | {clarify:.1%} | {fallback:.1%} | {provider_fallback:.1%} |".format(
                    family=family,
                    samples=int(payload.get("samples", 0) or 0),
                    validation=float(payload.get("validation_rate", 0.0) or 0.0),
                    mapping=float(payload.get("mapping_success_rate", 0.0) or 0.0),
                    clarify=float(payload.get("clarification_rate", 0.0) or 0.0),
                    fallback=float(payload.get("fallback_to_model_rate", 0.0) or 0.0),
                    provider_fallback=float(payload.get("provider_fallback_rate", 0.0) or 0.0),
                )
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build per-family readiness metrics from eval predictions JSONL.")
    parser.add_argument("--predictions", type=Path, required=True, help="Input predictions JSONL path.")
    parser.add_argument("--json-out", type=Path, default=None, help="Optional output JSON path.")
    parser.add_argument("--md-out", type=Path, default=None, help="Optional output Markdown path.")
    args = parser.parse_args()

    rows = _load_jsonl(args.predictions)
    report = compute_intent_family_readiness(rows)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(_render_markdown(report), encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
