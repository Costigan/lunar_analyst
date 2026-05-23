#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import sys
from pathlib import Path
import time
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.analyst_tools.client import AnalystToolHttpClient, AnalystToolHttpClientConfig, LocalAnalystToolClient
from backend.api.dependencies import build_service_container
from backend.contracts.models import DiscoverScenariosRequest
from backend.services.assistant.create_product_planner import (
    AvailableProduct,
    CreateProductBlock,
    CreateProductPlan,
    CreateProductPlanner,
    CreateProductReuse,
)
from backend.services.assistant.prompt_classifier import PromptClassifier, SegmentClassification
from backend.services.assistant.prompt_segmenter import PromptSegment, PromptSegmenter


@dataclass(frozen=True)
class PromptInput:
    prompt_number: int
    prompt: str
    required_files_before: tuple[str, ...] = ()
    delete_files_before: tuple[str, ...] = ()
    required_files_after: tuple[str, ...] = ()


def _parse_args() -> argparse.Namespace:
    default_input = REPO_ROOT / "scripts" / "sample_planning_prompts_2.json"
    parser = argparse.ArgumentParser(
        description=(
            "Read prompts from a text file, segment/classify each prompt, and show deterministic "
            "create_product plus semantic-intent execution planning. Prompts with no plannable segments are skipped."
        )
    )
    parser.add_argument(
        "prompts_file",
        nargs="?",
        default=str(default_input),
        help=(
            "Path to a text file containing one prompt per line. "
            f"Defaults to {default_input}."
        ),
    )
    parser.add_argument(
        "--scenario-id",
        default=None,
        help="Optional scenario_id to pass into classification and deterministic planning.",
    )
    parser.add_argument(
        "--scenario-dir",
        default="/e/lunar_analyst_scenarios/test_scenario/",
        help="Scenario directory to scan for known product files.",
    )
    parser.add_argument(
        "--segmenter-model",
        default="en_core_web_sm",
        help="spaCy model name to use for prompt segmentation.",
    )
    parser.add_argument(
        "--constraints",
        default="",
        help="Optional persistent constraints text to pass into non-command classification.",
    )
    parser.add_argument(
        "--known-product",
        action="append",
        default=[],
        help="Known product reference for non-command classification context. May be supplied multiple times.",
    )
    parser.add_argument(
        "--use-ollama",
        action="store_true",
        help="Deprecated/ignored: retained for CLI compatibility; runtime-aligned classifier settings are used.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON records instead of a human-readable report.",
    )
    parser.add_argument(
        "--execution-mode",
        choices=("none", "direct", "api"),
        default="none",
        help="Execution backend. Use 'none' to skip execution (default).",
    )
    parser.add_argument(
        "--api-base-url",
        default="http://127.0.0.1:8000",
        help="FastAPI base URL for --execution-mode api.",
    )
    parser.add_argument(
        "--api-token",
        default=None,
        help="Optional API token for --execution-mode api (x-lunar-session-token).",
    )
    parser.add_argument(
        "--job-mode",
        choices=("immediate", "queued"),
        default="immediate",
        help="Mode passed to executed tools (default: immediate).",
    )
    parser.set_defaults(force_plan=True)
    parser.add_argument(
        "--force-plan",
        dest="force_plan",
        action="store_true",
        help="Disable create_product reuse detection so generated plan steps are always shown (default).",
    )
    parser.add_argument(
        "--allow-reuse",
        dest="force_plan",
        action="store_false",
        help="Enable reuse detection so existing outputs can short-circuit planning.",
    )
    return parser.parse_args()


def _load_prompts(path: Path) -> list[PromptInput]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("JSON prompt file must be a list of objects.")
        prompts: list[PromptInput] = []
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"Prompt item #{index} must be an object.")
            prompt = str(item.get("prompt", "")).strip()
            if not prompt:
                raise ValueError(f"Prompt item #{index} has empty prompt.")
            required_before = _normalize_string_list(item.get("required_files_before"), f"required_files_before#{index}")
            delete_before = _normalize_string_list(item.get("delete_files_before"), f"delete_files_before#{index}")
            required_after = _normalize_string_list(item.get("required_files_after"), f"required_files_after#{index}")
            prompts.append(
                PromptInput(
                    prompt_number=index,
                    prompt=prompt,
                    required_files_before=required_before,
                    delete_files_before=delete_before,
                    required_files_after=required_after,
                )
            )
        return prompts

    prompts: list[PromptInput] = []
    prompt_number = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        prompt = raw_line.strip()
        if not prompt:
            continue
        if prompt.startswith("#"):
            continue
        prompt_number += 1
        prompts.append(PromptInput(prompt_number=prompt_number, prompt=prompt))
    return prompts


def _normalize_string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings.")
    items: list[str] = []
    for entry in value:
        text = str(entry).strip()
        if not text:
            continue
        items.append(text)
    return tuple(items)


def _resolve_file_path_under_scenario(*, scenario_dir: Path, raw_path: str) -> tuple[Path | None, str | None]:
    text = str(raw_path or "").strip()
    if not text:
        return None, "empty_path"
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = (scenario_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()
    try:
        candidate.relative_to(scenario_dir)
    except ValueError:
        return None, "outside_scenario_directory"
    return candidate, None


def _missing_required_files(*, scenario_dir: Path, required_paths: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for raw_path in required_paths:
        resolved, error = _resolve_file_path_under_scenario(scenario_dir=scenario_dir, raw_path=raw_path)
        if error is not None:
            missing.append(f"{raw_path} ({error})")
            continue
        assert resolved is not None
        if not resolved.exists() or not resolved.is_file():
            missing.append(raw_path)
    return missing


def _delete_prompt_files(*, scenario_dir: Path, delete_paths: tuple[str, ...], quiet: bool) -> list[str]:
    notes: list[str] = []
    for raw_path in delete_paths:
        resolved, error = _resolve_file_path_under_scenario(scenario_dir=scenario_dir, raw_path=raw_path)
        if error is not None:
            notes.append(f"skip delete {raw_path}: {error}")
            continue
        assert resolved is not None
        if not resolved.exists():
            continue
        if not resolved.is_file():
            notes.append(f"skip delete {raw_path}: not a file")
            continue
        try:
            resolved.unlink()
            if not quiet:
                notes.append(f"deleted {raw_path}")
        except OSError as exc:
            notes.append(f"failed delete {raw_path}: {exc}")
    return notes


def _outcome_to_dict(outcome: CreateProductPlan | CreateProductReuse | CreateProductBlock) -> dict[str, Any]:
    if isinstance(outcome, CreateProductPlan):
        return {
            "status": "planned",
            "recipe_id": outcome.recipe_id,
            "requested_product_type": outcome.requested_product_type,
            "prerequisite_count": outcome.prerequisite_count,
            "output_relative_path": outcome.output_relative_path,
            "steps": [
                {
                    "recipe_id": step.recipe_id,
                    "tool_name": step.tool_name,
                    "tool_args": step.tool_args,
                    "output_relative_path": step.output_relative_path,
                }
                for step in outcome.steps
            ],
        }
    if isinstance(outcome, CreateProductReuse):
        return {
            "status": "reuse",
            "output_relative_path": outcome.output_relative_path,
            "product_id": outcome.product_id,
            "message": outcome.message,
        }
    return {
        "status": "blocked",
        "reason_code": outcome.reason_code,
        "message": outcome.message,
        "details": dict(outcome.details),
    }


def _intent_plan_to_dict(mapped: Any, *, classification: SegmentClassification) -> dict[str, Any]:
    if mapped is None:
        return {
            "status": "not_plannable",
            "intent_family": classification.intent_family,
            "reason": "no deterministic planner for this intent family",
        }
    if bool(getattr(mapped, "requires_clarification", False)):
        return {
            "status": "clarification_required",
            "intent_family": getattr(mapped, "intent_family", classification.intent_family),
            "blocking_reason_code": getattr(mapped, "blocking_reason_code", None),
            "clarification_message": getattr(mapped, "clarification_message", None),
        }
    tool_steps = []
    for step in list(getattr(mapped, "tool_steps", []) or []):
        tool_steps.append(
            {
                "tool_name": str(getattr(step, "tool_name", "")).strip(),
                "arguments": dict(getattr(step, "arguments", {}) or {}),
            }
        )
    return {
        "status": "planned",
        "intent_family": getattr(mapped, "intent_family", classification.intent_family),
        "tool_steps": tool_steps,
    }


def _intent_classifier_runtime(_assistant_service: AssistantService) -> dict[str, Any]:
    return {
        "deterministic_recognizer_enabled": True,
        "secondary_segment_classifier_enabled": False,
        "unmatched_segment_fallback": "primary_llm",
    }


def _intent_classifier_usage_for_segment(
    *,
    classification: SegmentClassification | None,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    if classification is None:
        return {
            "used": False,
            "path": "primary_llm_fallback",
            "reason": "missing_classification",
        }
    origin = str(classification.classification_origin or "").strip()
    if str(runtime.get("secondary_segment_classifier_enabled", True)).lower() == "true":
        path = "secondary_segment_classifier"
    else:
        path = "deterministic_recognizer"
    return {
        "used": True,
        "path": path,
        "reason": origin or "recognized_segment",
    }


def _build_report(
    *,
    prompt_number: int,
    prompt: str,
    scenario_id: str | None,
    scenario_dir: Path,
    segmenter: PromptSegmenter,
    planner: CreateProductPlanner,
    assistant_service: AssistantService,
    constraints_text: str | None,
    known_products: list[str],
    force_plan: bool,
) -> dict[str, Any] | None:
    prompt_started = time.perf_counter()
    segmentation_started = time.perf_counter()
    segments = segmenter.segment(prompt)
    segmentation_ms = int((time.perf_counter() - segmentation_started) * 1000)
    classify_resolve_started = time.perf_counter()
    classifications, resolved_by_segment, deterministic_trace = assistant_service._classify_resolve_with_unified_deterministic_recognizer(  # noqa: SLF001
        segments=segments,
        scenario_id=scenario_id,
        constraints_text=constraints_text,
        known_products=known_products,
    )
    classification_resolution_ms = int((time.perf_counter() - classify_resolve_started) * 1000)
    class_by_id = {item.segment_id: item for item in classifications}
    classifier_runtime = _intent_classifier_runtime(assistant_service)

    create_product_rows: list[dict[str, Any]] = []
    semantic_intent_rows: list[dict[str, Any]] = []
    handling_rows: list[dict[str, Any]] = []
    prior_deterministic_outcomes: list[str] = []
    segment_processing_total_ms = 0

    def _append_handling_row(*, row: dict[str, Any], segment_started: float) -> None:
        nonlocal segment_processing_total_ms
        elapsed_ms = int((time.perf_counter() - segment_started) * 1000)
        segment_processing_total_ms += elapsed_ms
        row["processing_ms"] = elapsed_ms
        handling_rows.append(row)

    for segment in segments:
        segment_started = time.perf_counter()
        classification = class_by_id.get(segment.segment_id)
        segment_classifier_usage = _intent_classifier_usage_for_segment(
            classification=classification,
            runtime=classifier_runtime,
        )
        resolution_result = resolved_by_segment.get(segment.segment_id)
        deterministic_result = deterministic_trace.get(segment.segment_id)
        
        resolved_entities: list[str] = []
        if resolution_result is not None:
            # Add exact matches or high-confidence fuzzy/alias matches to the context
            for mention in resolution_result.mentions:
                if mention.resolved_id and mention.confidence >= 0.9:
                    resolved_entities.append(f"{mention.mention_text} -> {mention.resolved_id}")

        if classification is None:
            handoff_context = assistant_service.build_handoff_context(
                prompt=segment.text,
                scenario_id=scenario_id,
                prior_segment_messages=list(prior_deterministic_outcomes),
                resolution=resolution_result,
            )
            _append_handling_row(
                row={
                    "segment": _segment_to_dict(segment),
                    "classification": None,
                    "intent_classifier": segment_classifier_usage,
                    "deterministic_recognition": deterministic_result,
                    "handling_plan": {
                        "mode": "model_loop",
                        "status": "fallback",
                        "reason": "missing_classification",
                        "model_loop_context": {
                            "scenario_id": scenario_id,
                            "scenario_dir": str(scenario_dir),
                            "prior_side_effects": list(prior_deterministic_outcomes),
                            "resolved_entities": resolved_entities,
                            "prompt_messages": handoff_context,
                        },
                    },
                },
                segment_started=segment_started,
            )
            continue

        if classification.segment_class == "intent_family":
            block_reason = assistant_service._deterministic_dispatch_block_reason(  # noqa: SLF001
                classification=classification,
                resolution=resolution_result,
            )
            if block_reason is not None:
                intent_plan = {
                    "status": "clarification_required",
                    "intent_family": str(classification.intent_family or ""),
                    "blocking_reason_code": block_reason,
                    "clarification_message": (
                        "Need clarification before deterministic execution: "
                        f"{block_reason}. Please specify the exact target entity."
                    ),
                }
            elif segment.has_complexity_guard or isinstance(classification.intent_properties.get("constraints"), dict):
                intent_plan = {
                    "status": "not_plannable",
                    "intent_family": classification.intent_family,
                    "reason": "complexity_guard",
                }
            else:
                mapped = assistant_service._intent_to_tool_planner.map(  # noqa: SLF001
                    classification=classification,
                    scenario_id=scenario_id,
                    entity_resolution=resolution_result,
                    entity_kind_routing_enabled=True,
                )
                intent_plan = _intent_plan_to_dict(mapped, classification=classification)
            semantic_intent_rows.append(
                {
                    "segment": _segment_to_dict(segment),
                    "classification": _classification_to_dict(classification),
                    "intent_classifier": segment_classifier_usage,
                    "deterministic_recognition": deterministic_result,
                    "entity_resolution": (
                        resolution_result.as_dict()
                        if resolution_result
                        else None
                    ),
                    "intent_to_tool_plan": intent_plan,
                }
            )
            if intent_plan.get("status") == "planned":
                mode = "deterministic_intent_family"
            elif intent_plan.get("status") == "clarification_required":
                mode = "deterministic_block"
            else:
                mode = "model_loop"
            handling_plan: dict[str, Any] = {
                "mode": mode,
                "status": str(intent_plan.get("status", "not_plannable")),
                "intent_plan": intent_plan,
            }
            if mode == "deterministic_intent_family":
                prior_deterministic_outcomes.append(
                    f"Executed intent '{classification.intent_family}' using tools: "
                    + ", ".join(s["tool_name"] for s in intent_plan.get("tool_steps", []))
                )
            else:
                handoff_context = assistant_service.build_handoff_context(
                    prompt=segment.text,
                    scenario_id=scenario_id,
                    prior_segment_messages=list(prior_deterministic_outcomes),
                    resolution=resolution_result,
                )
                handling_plan["model_loop_context"] = {
                    "scenario_id": scenario_id,
                    "scenario_dir": str(scenario_dir),
                    "prior_side_effects": list(prior_deterministic_outcomes),
                    "resolved_entities": resolved_entities,
                    "prompt_messages": handoff_context,
                }

            _append_handling_row(
                row={
                    "segment": _segment_to_dict(segment),
                    "classification": _classification_to_dict(classification),
                    "intent_classifier": segment_classifier_usage,
                    "deterministic_recognition": deterministic_result,
                    "handling_plan": handling_plan,
                },
                segment_started=segment_started,
            )
            continue

        if classification.segment_class == "create_product":
            outcome = planner.plan(
                classification=classification,
                scenario_id=scenario_id,
                scenario_dir=scenario_dir,
                allow_reuse=not force_plan,
            )
            plan_payload = _outcome_to_dict(outcome)
            create_product_rows.append(
                {
                    "segment": _segment_to_dict(segment),
                    "classification": _classification_to_dict(classification),
                    "intent_classifier": segment_classifier_usage,
                    "deterministic_recognition": deterministic_result,
                    "entity_resolution": (
                        resolution_result.as_dict()
                        if resolution_result
                        else None
                    ),
                    "plan": plan_payload,
                }
            )
            mode = (
                "deterministic_create_product"
                if plan_payload.get("status") in {"planned", "reuse"}
                else "model_loop"
            )
            handling_plan = {
                "mode": mode,
                "status": str(plan_payload.get("status", "blocked")),
                "create_product_plan": plan_payload,
            }
            if mode == "deterministic_create_product":
                status = plan_payload.get("status")
                if status == "reuse":
                    prior_deterministic_outcomes.append(
                        f"Reused existing product for '{classification.product_type}': {plan_payload.get('output_relative_path')}"
                    )
                else:
                    prior_deterministic_outcomes.append(
                        f"Planned creation of '{classification.product_type}' using tools: "
                        + ", ".join(s["tool_name"] for s in plan_payload.get("steps", []))
                    )
            else:
                handoff_context = assistant_service.build_handoff_context(
                    prompt=segment.text,
                    scenario_id=scenario_id,
                    prior_segment_messages=list(prior_deterministic_outcomes),
                    resolution=resolution_result,
                )
                handling_plan["model_loop_context"] = {
                    "scenario_id": scenario_id,
                    "scenario_dir": str(scenario_dir),
                    "prior_side_effects": list(prior_deterministic_outcomes),
                    "resolved_entities": resolved_entities,
                    "prompt_messages": handoff_context,
                }

            _append_handling_row(
                row={
                    "segment": _segment_to_dict(segment),
                    "classification": _classification_to_dict(classification),
                    "intent_classifier": segment_classifier_usage,
                    "deterministic_recognition": deterministic_result,
                    "handling_plan": handling_plan,
                },
                segment_started=segment_started,
            )
            continue

        if classification.segment_class == "command":
            block_reason = assistant_service._deterministic_dispatch_block_reason(  # noqa: SLF001
                classification=classification,
                resolution=resolution_result,
            )
            if block_reason is not None:
                _append_handling_row(
                    row={
                        "segment": _segment_to_dict(segment),
                        "classification": _classification_to_dict(classification),
                        "intent_classifier": segment_classifier_usage,
                        "deterministic_recognition": deterministic_result,
                        "entity_resolution": (
                            resolution_result.as_dict()
                            if resolution_result
                            else None
                        ),
                        "handling_plan": {
                            "mode": "deterministic_block",
                            "status": "clarification_required",
                            "blocking_reason_code": block_reason,
                            "clarification_message": (
                                "Need clarification before deterministic execution: "
                                f"{block_reason}. Please specify the exact target entity."
                            ),
                        },
                    },
                    segment_started=segment_started,
                )
                continue

            planned_action = PromptClassifier._plan_segment(  # noqa: SLF001
                router=assistant_service._command_router,  # noqa: SLF001
                text=segment.text,
                scenario_id=scenario_id,
            )
            steps: list[dict[str, Any]] = []
            if planned_action is not None:
                for step in planned_action.steps:
                    if hasattr(step, "tool_name"):
                        steps.append(
                            {
                                "kind": "tool",
                                "tool_name": str(getattr(step, "tool_name", "")).strip(),
                                "arguments": dict(getattr(step, "arguments", {}) or {}),
                            }
                        )
                    else:
                        steps.append(
                            {
                                "kind": "agent",
                                "objective": str(getattr(step, "objective", "")).strip(),
                                "allowed_tools": list(getattr(step, "allowed_tools", []) or []),
                            }
                        )
            if planned_action is None or any(step.get("kind") == "agent" for step in steps):
                handoff_context = assistant_service.build_handoff_context(
                    prompt=segment.text,
                    scenario_id=scenario_id,
                    prior_segment_messages=list(prior_deterministic_outcomes),
                    resolution=resolution_result,
                )
                _append_handling_row(
                    row={
                        "segment": _segment_to_dict(segment),
                        "classification": _classification_to_dict(classification),
                        "intent_classifier": segment_classifier_usage,
                        "deterministic_recognition": deterministic_result,
                        "entity_resolution": (
                            resolution_result.as_dict()
                            if resolution_result
                            else None
                        ),
                        "handling_plan": {
                            "mode": "model_loop",
                            "status": "fallback",
                            "reason": "command_router_non_tool_plan",
                            "model_loop_context": {
                                "scenario_id": scenario_id,
                                "scenario_dir": str(scenario_dir),
                                "prior_side_effects": list(prior_deterministic_outcomes),
                                "resolved_entities": resolved_entities,
                                "prompt_messages": handoff_context,
                            },
                        },
                    },
                    segment_started=segment_started,
                )
                continue

            prior_deterministic_outcomes.append(
                f"Executed command '{classification.command}' using tools: "
                + ", ".join(s["tool_name"] for s in steps if s["kind"] == "tool")
            )

            _append_handling_row(
                row={
                    "segment": _segment_to_dict(segment),
                    "classification": _classification_to_dict(classification),
                    "intent_classifier": segment_classifier_usage,
                    "deterministic_recognition": deterministic_result,
                    "entity_resolution": (
                        resolution_result.as_dict()
                        if resolution_result
                        else None
                    ),
                    "handling_plan": {
                        "mode": "deterministic_command",
                        "status": "planned",
                        "action_id": str(classification.command or "").strip(),
                        "steps": steps,
                    },
                },
                segment_started=segment_started,
            )
            continue

        handoff_context = assistant_service.build_handoff_context(
            prompt=segment.text,
            scenario_id=scenario_id,
            prior_segment_messages=list(prior_deterministic_outcomes),
            resolution=resolution_result,
        )
        _append_handling_row(
            row={
                "segment": _segment_to_dict(segment),
                "classification": _classification_to_dict(classification),
                "intent_classifier": segment_classifier_usage,
                "deterministic_recognition": deterministic_result,
                "entity_resolution": (
                    resolution_result.as_dict()
                    if resolution_result
                    else None
                ),
                "handling_plan": {
                    "mode": "model_loop",
                    "status": "fallback",
                    "reason": "non_deterministic_segment_class",
                    "model_loop_context": {
                        "scenario_id": scenario_id,
                        "scenario_dir": str(scenario_dir),
                        "prior_side_effects": list(prior_deterministic_outcomes),
                        "resolved_entities": resolved_entities,
                        "prompt_messages": handoff_context,
                    },
                },
            },
            segment_started=segment_started,
        )

    prompt_total_ms = int((time.perf_counter() - prompt_started) * 1000)
    return {
        "prompt_number": prompt_number,
        "prompt": prompt,
        "scenario_id": scenario_id,
        "scenario_dir": str(scenario_dir),
        "constraints_text": constraints_text or "",
        "known_products": list(known_products),
        "segment_count": len(segments),
        "create_product_segment_count": len(create_product_rows),
        "create_product_segments": create_product_rows,
        "semantic_intent_segment_count": len(semantic_intent_rows),
        "semantic_intent_segments": semantic_intent_rows,
        "handling_segment_count": len(handling_rows),
        "handling_segments": handling_rows,
        "intent_classifier_runtime": classifier_runtime,
        "deterministic_recognition": deterministic_trace,
        "timing": {
            "segmentation_ms": segmentation_ms,
            "classification_resolution_ms": classification_resolution_ms,
            "segment_processing_total_ms": segment_processing_total_ms,
            "segment_plus_segmentation_ms": segmentation_ms + segment_processing_total_ms,
            "prompt_total_ms": prompt_total_ms,
        },
    }


def _segment_to_dict(segment: PromptSegment) -> dict[str, Any]:
    return {
        "segment_id": segment.segment_id,
        "text": segment.text,
        "start_char": segment.start_char,
        "end_char": segment.end_char,
        "is_imperative_candidate": segment.is_imperative_candidate,
        "has_complexity_guard": segment.has_complexity_guard,
        "segmentation_confidence": segment.segmentation_confidence,
    }


def _classification_to_dict(classification: SegmentClassification) -> dict[str, Any]:
    return {
        "class": classification.segment_class,
        "confidence": classification.confidence,
        "classification_origin": classification.classification_origin,
        "product_type": classification.product_type,
        "intent_family": classification.intent_family,
        "intent_properties": dict(classification.intent_properties),
        "pixel_type": classification.pixel_type,
        "semantics": classification.semantics,
        "sources": list(classification.sources),
        "validation_status": classification.validation_status,
        "downgrade_reason": classification.downgrade_reason,
    }


def _format_intent_classifier_usage(usage: dict[str, Any]) -> str:
    used = bool(usage.get("used"))
    path = str(usage.get("path") or "unknown")
    reason = str(usage.get("reason") or "unspecified")
    return (
        "segment_routing="
        f"used={'yes' if used else 'no'} "
        f"path={path} "
        f"reason={reason}"
    )


def _format_deterministic_recognition(trace: dict[str, Any]) -> str:
    status = str(trace.get("status") or "unknown")
    rule = str(trace.get("matched_rule_id") or "none")
    reason = str(trace.get("reason") or "unknown")
    operations = ",".join(trace.get("operation_candidates", []) or []) or "none"
    targets = trace.get("target_kind_by_operation", {}) or {}
    target_summary = ",".join(
        f"{str(key)}->{str(value)}"
        for key, value in sorted(targets.items())
    ) or "none"
    return (
        "deterministic_recognizer="
        f"status={status} "
        f"rule={rule} "
        f"reason={reason} "
        f"operation_candidates={operations} "
        f"target_kinds={target_summary}"
    )


def _print_human_report(report: dict[str, Any], *, index: int) -> None:
    prompt_number = int(report.get("prompt_number", index))
    print(f"Prompt {prompt_number}")
    print(f"  Text: {report['prompt']}")
    if report["scenario_id"] is not None:
        print(f"  scenario_id: {report['scenario_id']}")
    print(f"  scenario_dir: {report['scenario_dir']}")
    pre_notes = report.get("pre_step_notes", [])
    if isinstance(pre_notes, list):
        for note in pre_notes:
            print(f"  pre_step={note}")
    print(
        "  Segments: "
        f"total={report['segment_count']} "
        f"create_product={report['create_product_segment_count']} "
        f"semantic_intent={report.get('semantic_intent_segment_count', 0)} "
        f"handled={report.get('handling_segment_count', 0)}"
    )
    timing = report.get("timing", {})
    if isinstance(timing, dict):
        print(
            "  Timing: "
            f"segmentation_ms={timing.get('segmentation_ms', 0)} "
            f"classification_resolution_ms={timing.get('classification_resolution_ms', 0)} "
            f"segment_processing_total_ms={timing.get('segment_processing_total_ms', 0)} "
            f"segment_plus_segmentation_ms={timing.get('segment_plus_segmentation_ms', 0)} "
            f"prompt_total_ms={timing.get('prompt_total_ms', 0)}"
        )
    classifier_runtime = report.get("intent_classifier_runtime", {})
    if isinstance(classifier_runtime, dict):
        print(
            "  Segment Routing Runtime: "
            f"deterministic_recognizer_enabled={classifier_runtime.get('deterministic_recognizer_enabled')} "
            f"secondary_segment_classifier_enabled={classifier_runtime.get('secondary_segment_classifier_enabled')} "
            f"unmatched_segment_fallback={classifier_runtime.get('unmatched_segment_fallback')}"
        )
    handled = report.get("handling_segments", [])
    if isinstance(handled, list):
        used_count = len(
            [
                row
                for row in handled
                if isinstance(row, dict)
                and isinstance(row.get("intent_classifier"), dict)
                and bool(row.get("intent_classifier", {}).get("used"))
            ]
        )
        print(
            "  Segment Routing Usage: "
            f"segments_with_deterministic_classification={used_count}/{len(handled)}"
        )

    for row in report["create_product_segments"]:
        assert isinstance(row, dict)
        segment = row["segment"]
        classification = row["classification"]
        plan = row["plan"]
        usage = row.get("intent_classifier", {})
        deterministic = row.get("deterministic_recognition", {})
        assert isinstance(segment, dict)
        assert isinstance(classification, dict)
        assert isinstance(plan, dict)
        print(f"  - {segment['segment_id']}: {segment['text']}")
        print(
            "    classification="
            f"class={classification['class']} "
            f"product_type={classification['product_type']} "
            f"sources={classification['sources']} "
            f"origin={classification['classification_origin']}"
        )
        if isinstance(usage, dict):
            print("    " + _format_intent_classifier_usage(usage))
        if isinstance(deterministic, dict):
            print("    " + _format_deterministic_recognition(deterministic))
        print(f"    plan_status={plan['status']}")
        entity_resolution = row.get("entity_resolution")
        if isinstance(entity_resolution, dict):
            pretty = json.dumps(entity_resolution, ensure_ascii=True, sort_keys=True, indent=2)
            pretty_lines = pretty.splitlines()
            if pretty_lines:
                print("    entity_resolution=" + pretty_lines[0])
                for line in pretty_lines[1:]:
                    print("    " + line)
        if plan["status"] == "planned":
            print(
                "    recipe="
                f"{plan['recipe_id']} "
                f"prerequisites={plan['prerequisite_count']} "
                f"output={plan['output_relative_path']}"
            )
            for step in plan["steps"]:
                assert isinstance(step, dict)
                print(
                    "      step="
                    f"tool={step['tool_name']} "
                    f"args={json.dumps(step['tool_args'], ensure_ascii=True, sort_keys=True)}"
                )
        elif plan["status"] == "reuse":
            print(
                "    reuse="
                f"product_id={plan['product_id']} "
                f"output={plan['output_relative_path']}"
            )
            print(f"    message={plan['message']}")
        else:
            print(
                "    blocked="
                f"reason_code={plan['reason_code']} "
                f"message={plan['message']} "
                f"details={json.dumps(plan.get('details', {}), ensure_ascii=True, sort_keys=True)}"
            )
        execution = row.get("execution")
        if isinstance(execution, dict):
            print(
                "    execution="
                f"status={execution.get('status')} "
                f"mode={execution.get('mode')}"
            )
            steps = execution.get("steps", [])
            if isinstance(steps, list):
                for executed in steps:
                    if not isinstance(executed, dict):
                        continue
                    if executed.get("status") == "ok":
                        print(
                            "      run="
                            f"tool={executed.get('tool_name')} "
                            f"job_id={executed.get('job_id')} "
                            f"job_status={executed.get('job_status')}"
                        )
                    else:
                        print(
                            "      run_failed="
                            f"tool={executed.get('tool_name')} "
                            f"error={executed.get('error')}"
                        )
    for row in report.get("semantic_intent_segments", []):
        assert isinstance(row, dict)
        segment = row.get("segment", {})
        classification = row.get("classification", {})
        intent_plan = row.get("intent_to_tool_plan", {})
        usage = row.get("intent_classifier", {})
        deterministic = row.get("deterministic_recognition", {})
        assert isinstance(segment, dict)
        assert isinstance(classification, dict)
        assert isinstance(intent_plan, dict)
        print(f"  - {segment.get('segment_id')}: {segment.get('text')}")
        print(
            "    classification="
            f"class={classification.get('class')} "
            f"intent_family={classification.get('intent_family')} "
            f"origin={classification.get('classification_origin')}"
        )
        if isinstance(usage, dict):
            print("    " + _format_intent_classifier_usage(usage))
        if isinstance(deterministic, dict):
            print("    " + _format_deterministic_recognition(deterministic))
        print(
            "    intent_properties="
            f"{json.dumps(classification.get('intent_properties', {}), ensure_ascii=True, sort_keys=True)}"
        )
        entity_resolution = row.get("entity_resolution")
        if isinstance(entity_resolution, dict):
            pretty = json.dumps(entity_resolution, ensure_ascii=True, sort_keys=True, indent=2)
            pretty_lines = pretty.splitlines()
            if pretty_lines:
                print("    entity_resolution=" + pretty_lines[0])
                for line in pretty_lines[1:]:
                    print("    " + line)
        print(f"    intent_plan_status={intent_plan.get('status')}")
        if intent_plan.get("status") == "planned":
            for step in intent_plan.get("tool_steps", []):
                if not isinstance(step, dict):
                    continue
                print(
                    "      step="
                    f"tool={step.get('tool_name')} "
                    f"args={json.dumps(step.get('arguments', {}), ensure_ascii=True, sort_keys=True)}"
                )
        else:
            print(f"    intent_plan={json.dumps(intent_plan, ensure_ascii=True, sort_keys=True)}")
    if report.get("handling_segments"):
        print("  Handling Summary:")
    for row in report.get("handling_segments", []):
        if not isinstance(row, dict):
            continue
        segment = row.get("segment", {})
        classification = row.get("classification")
        usage = row.get("intent_classifier", {})
        deterministic = row.get("deterministic_recognition", {})
        handling = row.get("handling_plan", {})
        if not isinstance(segment, dict) or not isinstance(handling, dict):
            continue
        segment_id = str(segment.get("segment_id", ""))
        text = str(segment.get("text", ""))
        cls_name = "unknown"
        if isinstance(classification, dict):
            cls_name = str(classification.get("class", "unknown"))
        elif classification is None:
            cls_name = "unclassified"
        print(
            "    segment="
            f"{segment_id} class={cls_name} mode={handling.get('mode')} status={handling.get('status')} "
            f"processing_ms={row.get('processing_ms', 0)} text={text}"
        )
        if isinstance(usage, dict):
            print(f"      {_format_intent_classifier_usage(usage)}")
        if isinstance(deterministic, dict):
            print(f"      {_format_deterministic_recognition(deterministic)}")
        if handling.get("mode") == "model_loop":
            context = handling.get("model_loop_context", {})
            print(f"      model_loop_context:")
            print(f"        scenario_id: {context.get('scenario_id')}")
            print(f"        scenario_dir: {context.get('scenario_dir')}")
            prior_effects = context.get("prior_side_effects", [])
            if prior_effects:
                print(f"        prior_deterministic_side_effects:")
                for effect in prior_effects:
                    print(f"          - {effect}")
            else:
                print(f"        prior_deterministic_side_effects: none")
            
            resolved_entities = context.get("resolved_entities", [])
            if resolved_entities:
                print(f"        resolved_entities:")
                for ent in resolved_entities:
                    print(f"          - {ent}")
            else:
                print(f"        resolved_entities: none")

            prompt_messages = context.get("prompt_messages", {})
            if prompt_messages:
                print(f"        llm_prompt_messages:")
                sys_prompt_path = prompt_messages.get("system_prompt_path", "unknown")
                print(f"          system_prompt_file: {sys_prompt_path}")
                
                conv = prompt_messages.get("conversation", [])
                if conv:
                    print(f"          conversation:")
                    for msg in conv:
                        print(f"            - role: {msg.get('role')}")
                        print(f"              content: |")
                        content = str(msg.get("content", ""))
                        for line in content.splitlines():
                            print(f"                {line}")

        entity_resolution = row.get("entity_resolution")
        if isinstance(entity_resolution, dict):
            pretty = json.dumps(entity_resolution, ensure_ascii=True, sort_keys=True, indent=2)
            pretty_lines = pretty.splitlines()
            if pretty_lines:
                print("      entity_resolution=" + pretty_lines[0])
                for line in pretty_lines[1:]:
                    print("      " + line)
        if isinstance(handling.get("steps"), list):
            for step in handling.get("steps", []):
                if not isinstance(step, dict):
                    continue
                if step.get("kind") == "tool":
                    print(
                        "      step="
                        f"tool={step.get('tool_name')} "
                        f"args={json.dumps(step.get('arguments', {}), ensure_ascii=True, sort_keys=True)}"
                    )
                elif step.get("kind") == "agent":
                    print(
                        "      step="
                        f"agent objective={step.get('objective')} "
                        f"allowed_tools={step.get('allowed_tools')}"
                    )
    missing_after = report.get("required_files_after_missing", [])
    if isinstance(missing_after, list):
        for missing_path in missing_after:
            print(f"  ERROR: required file not created: {missing_path}")
    overall_status = str(report.get("plan_overall_status", "")).strip()
    if overall_status:
        print(f"  overall_plan_status={overall_status.upper()}")
    print()


def _determine_report_overall_status(
    *,
    report: dict[str, Any] | None,
    planning_only: bool,
) -> str:
    if report is None:
        return "skipped"
    if planning_only:
        create_rows = report.get("create_product_segments", [])
        handling_rows = report.get("handling_segments", [])
        has_any_segments = isinstance(handling_rows, list) and bool(handling_rows)
        if not has_any_segments:
            return "skipped"

        if isinstance(create_rows, list):
            for row in create_rows:
                if not isinstance(row, dict):
                    continue
                plan = row.get("plan")
                if not isinstance(plan, dict):
                    return "failed"
                plan_status = str(plan.get("status", "")).strip()
                if plan_status == "blocked":
                    return "failed"
                if plan_status not in {"planned", "reuse"}:
                    return "failed"
        return "success"
    missing_after = report.get("required_files_after_missing", [])
    if isinstance(missing_after, list) and missing_after:
        return "failed"
    rows = report.get("create_product_segments", [])
    if not isinstance(rows, list) or not rows:
        return "skipped"
    for row in rows:
        if not isinstance(row, dict):
            continue
        plan = row.get("plan")
        if isinstance(plan, dict):
            plan_status = str(plan.get("status", "")).strip()
            if plan_status == "blocked":
                return "failed"
            if plan_status in {"reuse"}:
                continue
            if plan_status != "planned":
                return "failed"
        execution = row.get("execution")
        if not isinstance(execution, dict):
            return "failed"
        execution_status = str(execution.get("status", "")).strip()
        if execution_status != "executed":
            return "failed"
    return "success"


def _resolve_scenario_id_for_direct(*, scenario_id: str, scenario_dir: Path) -> str:
    services = build_service_container()
    normalized_dir = scenario_dir.resolve()
    for scenario in services.scenario_service.list_scenarios():
        if Path(scenario.directory).resolve() == normalized_dir:
            return scenario.scenario_id
    workspace_root = Path(services.stores.workspace_root).resolve()
    try:
        rel = str(normalized_dir.relative_to(workspace_root))
    except ValueError:
        return scenario_id
    services.scenario_service.discover_scenarios(
        DiscoverScenariosRequest(
            dry_run=False,
            include_existing=True,
            scenario_roots=[rel],
        )
    )
    for scenario in services.scenario_service.list_scenarios():
        if Path(scenario.directory).resolve() == normalized_dir:
            return scenario.scenario_id
    return scenario_id


def _resolve_scenario_id_for_api(
    *,
    scenario_id: str,
    scenario_dir: Path,
    api_base_url: str,
    api_token: str | None,
) -> str:
    headers: dict[str, str] = {}
    if api_token:
        headers["x-lunar-session-token"] = api_token
    with httpx.Client(base_url=api_base_url, timeout=30.0, headers=headers) as client:
        try:
            response = client.get(f"/api/v1/scenarios/{scenario_id}")
            if response.status_code == 200:
                return scenario_id
        except httpx.HTTPError:
            pass
        client.post(
            "/api/v1/scenarios:discover",
            json={"dry_run": False, "include_existing": True},
        ).raise_for_status()
        scenarios = client.get("/api/v1/scenarios")
        scenarios.raise_for_status()
        normalized_dir = scenario_dir.resolve()
        for item in scenarios.json():
            directory = Path(str(item.get("directory", ""))).resolve()
            if directory == normalized_dir:
                discovered_id = str(item.get("scenario_id", "")).strip()
                if discovered_id:
                    return discovered_id
    return scenario_id


def _invoke_tool_direct(*, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    services = build_service_container()
    client = LocalAnalystToolClient(services)
    run = client.invoke_tool(tool_name, arguments)
    return {
        "tool_name": run.tool_name,
        "job_id": run.job_id,
        "run_id": run.run_id,
        "job_status": str(run.job.status),
        "result": run.result,
    }


def _invoke_tool_api(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    api_base_url: str,
    api_token: str | None,
) -> dict[str, Any]:
    client = AnalystToolHttpClient(
        AnalystToolHttpClientConfig(
            base_url=api_base_url,
            api_token=api_token,
            timeout_seconds=60.0,
        )
    )
    try:
        run = client.invoke_tool(tool_name, arguments)
    finally:
        client.close()
    return {
        "tool_name": run.tool_name,
        "job_id": run.job_id,
        "run_id": run.run_id,
        "job_status": str(run.job.status),
        "result": run.result,
    }


def _execute_report(
    *,
    report: dict[str, Any],
    available_products: list[AvailableProduct],
    execution_mode: str,
    scenario_id: str,
    job_mode: str,
    api_base_url: str,
    api_token: str | None,
) -> None:
    by_product_id: dict[str, AvailableProduct] = {
        str(item.product_id): item
        for item in available_products
    }
    for row in report.get("create_product_segments", []):
        if not isinstance(row, dict):
            continue
        plan = row.get("plan")
        if not isinstance(plan, dict):
            continue
        if plan.get("status") != "planned":
            row["execution"] = {
                "status": "skipped",
                "mode": execution_mode,
                "steps": [],
            }
            continue
        executions: list[dict[str, Any]] = []
        failed = False
        steps = plan.get("steps", [])
        if not isinstance(steps, list):
            steps = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            tool_name = str(step.get("tool_name", "")).strip()
            tool_args = dict(step.get("tool_args") or {})
            tool_args["scenario_id"] = scenario_id
            tool_args["mode"] = job_mode
            if tool_name == "raster.calculate":
                raw_inputs = tool_args.get("inputs")
                if isinstance(raw_inputs, dict):
                    rewritten: dict[str, Any] = {}
                    for key, input_ref in raw_inputs.items():
                        if not isinstance(input_ref, dict):
                            rewritten[str(key)] = input_ref
                            continue
                        next_ref = dict(input_ref)
                        source_product_id = str(next_ref.get("product_id", "")).strip()
                        if source_product_id:
                            source = by_product_id.get(source_product_id)
                            relative_path = source.preferred_relative_path if source is not None else None
                            if relative_path:
                                next_ref["relative_path"] = relative_path
                                next_ref.pop("product_id", None)
                        rewritten[str(key)] = next_ref
                    tool_args["inputs"] = rewritten
            try:
                if execution_mode == "api":
                    executed = _invoke_tool_api(
                        tool_name=tool_name,
                        arguments=tool_args,
                        api_base_url=api_base_url,
                        api_token=api_token,
                    )
                else:
                    executed = _invoke_tool_direct(
                        tool_name=tool_name,
                        arguments=tool_args,
                    )
                executions.append({"status": "ok", **executed})
            except Exception as exc:
                failed = True
                executions.append(
                    {
                        "status": "failed",
                        "tool_name": tool_name,
                        "error": str(exc),
                    }
                )
                break
        row["execution"] = {
            "status": "failed" if failed else "executed",
            "mode": execution_mode,
            "steps": executions,
        }


def main() -> int:
    args = _parse_args()
    input_path = Path(args.prompts_file).resolve()
    print(f"input_path={input_path}")
    try:
        prompt_items = _load_prompts(input_path)
    except ValueError as exc:
        print(f"Invalid prompt file {input_path}: {exc}", file=sys.stderr)
        return 1
    if not prompt_items:
        print(f"No prompts found in {input_path}", file=sys.stderr)
        return 1
    scenario_dir = Path(args.scenario_dir).resolve()
    if not scenario_dir.is_dir():
        print(f"Scenario directory not found: {scenario_dir}", file=sys.stderr)
        return 1
    scenario_id = str(args.scenario_id).strip() if args.scenario_id else scenario_dir.name
    resolution_services = build_service_container()
    assistant_service = resolution_services.assistant_service

    # Keep planning behavior aligned with AssistantService runtime expectations:
    # local resolution paths assume the scenario is registered/discoverable.
    scenario_id = _resolve_scenario_id_for_direct(
        scenario_id=scenario_id,
        scenario_dir=scenario_dir,
    )
    if args.execution_mode == "api":
        scenario_id = _resolve_scenario_id_for_api(
            scenario_id=scenario_id,
            scenario_dir=scenario_dir,
            api_base_url=args.api_base_url,
            api_token=args.api_token,
        )

    segmenter = PromptSegmenter(model_name=args.segmenter_model)
    planner = assistant_service._create_product_planner  # noqa: SLF001
    if args.use_ollama and not args.json:
        print(
            "Note: --use-ollama is ignored to keep this script aligned with AssistantService runtime behavior.",
            file=sys.stderr,
        )

    known_products = [str(item).strip() for item in args.known_product if str(item).strip()]
    constraints_text = str(args.constraints or "").strip() or None

    reports: list[dict[str, Any]] = []
    planning_only = args.execution_mode == "none"
    skipped_missing_before = 0
    prompts_with_missing_after = 0
    outcome_counts = {"success": 0, "failed": 0, "skipped": 0}
    for item in prompt_items:
        missing_before = _missing_required_files(
            scenario_dir=scenario_dir,
            required_paths=item.required_files_before,
        )
        if missing_before:
            skipped_missing_before += 1
            outcome_counts["skipped"] += 1
            if not args.json:
                print(f"Prompt {item.prompt_number}")
                print(f"  Text: {item.prompt}")
                print(
                    "  skipped=missing required_files_before: "
                    f"{json.dumps(missing_before, ensure_ascii=True)}"
                )
                print()
            continue

        delete_notes: list[str] = []
        if not planning_only:
            delete_notes = _delete_prompt_files(
                scenario_dir=scenario_dir,
                delete_paths=item.delete_files_before,
                quiet=args.json,
            )

        report = _build_report(
            prompt_number=item.prompt_number,
            prompt=item.prompt,
            scenario_id=scenario_id,
            scenario_dir=scenario_dir,
            segmenter=segmenter,
            planner=planner,
            assistant_service=assistant_service,
            constraints_text=constraints_text,
            known_products=known_products,
            force_plan=bool(args.force_plan),
        )
        if report is not None and args.execution_mode != "none":
            current_available_products = planner.discover_available_products(scenario_dir=scenario_dir)
            _execute_report(
                report=report,
                available_products=current_available_products,
                execution_mode=args.execution_mode,
                scenario_id=scenario_id,
                job_mode=args.job_mode,
                api_base_url=args.api_base_url,
                api_token=args.api_token,
            )
        missing_after: list[str] = []
        if not planning_only:
            missing_after = _missing_required_files(
                scenario_dir=scenario_dir,
                required_paths=item.required_files_after,
            )
        if missing_after:
            prompts_with_missing_after += 1
        if report is not None:
            if delete_notes:
                report["pre_step_notes"] = list(delete_notes)
            if missing_after:
                report["required_files_after_missing"] = list(missing_after)
            report["plan_overall_status"] = _determine_report_overall_status(
                report=report,
                planning_only=planning_only,
            )
            outcome_counts[str(report["plan_overall_status"])] += 1
            reports.append(report)
            if not args.json:
                _print_human_report(report, index=item.prompt_number)
        else:
            outcome_counts["skipped"] += 1
            if missing_after and not args.json:
                print(f"Prompt {item.prompt_number}")
                print(f"  Text: {item.prompt}")
                for missing_path in missing_after:
                    print(f"  ERROR: required file not created: {missing_path}")
                print()

    if args.json:
        print(json.dumps(reports, ensure_ascii=True, indent=2))
        return 0

    print(f"Loaded {len(prompt_items)} prompt(s) from {input_path}")
    print(f"Prompts with reported handling plans: {len(reports)}")
    if skipped_missing_before:
        print(f"Prompts skipped for missing required_files_before: {skipped_missing_before}")
    if prompts_with_missing_after:
        print(f"Prompts with missing required_files_after: {prompts_with_missing_after}")
    print(
        "Overall plan outcomes: "
        f"SUCCESS={outcome_counts['success']} "
        f"FAILED={outcome_counts['failed']} "
        f"SKIPPED={outcome_counts['skipped']}"
    )
    print()
    if not reports:
        print("No segments were produced for planning.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
