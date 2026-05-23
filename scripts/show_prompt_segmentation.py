#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.api.dependencies import build_service_container
from backend.services.assistant.entity_reference_resolver import EntityReferenceResolver
from backend.services.assistant.command_router import HybridCommandRouter
from backend.services.assistant.canonical_recipe_catalog import get_recipe, recipe_ids_for_product_type
from backend.services.assistant.intent_to_tool_planner import IntentToToolPlanner
from backend.services.assistant.prompt_classifier import PromptClassifier
from backend.services.assistant.prompt_segmenter import PromptSegmenter
from backend.services.assistant.segment_intent_extractor import SegmentIntentExtractor


def _parse_args() -> argparse.Namespace:
    default_input = REPO_ROOT / "scripts" / "sample_prompts_for_segmentation.txt"
    parser = argparse.ArgumentParser(
        description=(
            "Read prompts from a text file, one prompt per line, then run "
            "assistant segmentation and segment classification on each prompt."
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
        help="Optional scenario_id to pass into classification/router matching.",
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
        help="Known product reference to provide as non-command classification context. May be supplied multiple times.",
    )
    parser.add_argument(
        "--use-ollama",
        action="store_true",
        help="Use the configured Ollama-backed non-command classifier instead of offline heuristics.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON records instead of a human-readable report.",
    )
    return parser.parse_args()


def _load_prompts(path: Path) -> list[str]:
    prompts: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        prompt = raw_line.strip()
        if not prompt:
            continue
        if prompt.startswith("#"):
            continue
        prompts.append(prompt)
    return prompts


def _build_report(
    *,
    prompt: str,
    scenario_id: str | None,
    segmenter: PromptSegmenter,
    classifier: PromptClassifier,
    router: HybridCommandRouter,
    constraints_text: str | None,
    known_products: list[str],
    resolver: EntityReferenceResolver | None,
) -> dict[str, object]:
    intent_planner = IntentToToolPlanner()
    segments = segmenter.segment(prompt)
    classifications = classifier.classify(
        segments=segments,
        scenario_id=scenario_id,
        router=router,
        constraints_text=constraints_text,
        known_products=known_products,
    )
    resolved_by_segment = (
        resolver.resolve_segments(classifications=classifications, scenario_id=scenario_id)
        if resolver is not None
        else {}
    )
    class_by_id = {item.segment_id: item for item in classifications}
    router_plan = router.plan(prompt=prompt, scenario_id=scenario_id)

    segment_rows: list[dict[str, object]] = []
    for segment in segments:
        classification = class_by_id.get(segment.segment_id)
        intent_to_tool_plan: dict[str, object] | None = None
        if classification is not None and classification.segment_class == "intent_family":
            mapped = intent_planner.map(
                classification=classification,
                scenario_id=scenario_id,
            )
            if mapped is None:
                intent_to_tool_plan = {
                    "status": "not_plannable",
                    "intent_family": classification.intent_family,
                    "reason": "no deterministic planner for this intent family",
                }
            elif mapped.requires_clarification:
                intent_to_tool_plan = {
                    "status": "clarification_required",
                    "intent_family": mapped.intent_family,
                    "blocking_reason_code": mapped.blocking_reason_code,
                    "clarification_message": mapped.clarification_message,
                }
            else:
                intent_to_tool_plan = {
                    "status": "planned",
                    "intent_family": mapped.intent_family,
                    "tool_steps": [
                        {"tool_name": step.tool_name, "arguments": step.arguments}
                        for step in mapped.tool_steps
                    ],
                }
        recipe_matches: list[dict[str, object]] = []
        if classification is not None and classification.segment_class == "create_product":
            product_type = str(classification.product_type or "").strip()
            if product_type:
                for recipe_id in recipe_ids_for_product_type(product_type):
                    recipe = get_recipe(recipe_id)
                    recipe_matches.append(
                        {
                            "recipe_id": recipe.recipe_id,
                            "product_type": recipe.product_type,
                            "requires": list(recipe.requires),
                            "execution_ref": recipe.execution_ref,
                            "required_parameters": list(recipe.required_parameters),
                            "reuse_keys": list(recipe.reuse_keys),
                            "expression_template": recipe.expression_template,
                            "default_output_relative_path": recipe.default_output_relative_path,
                        }
                    )
        segment_rows.append(
            {
                "segment_id": segment.segment_id,
                "text": segment.text,
                "start_char": segment.start_char,
                "end_char": segment.end_char,
                "is_imperative_candidate": segment.is_imperative_candidate,
                "has_complexity_guard": segment.has_complexity_guard,
                "segmentation_confidence": segment.segmentation_confidence,
                "classification": (
                    {
                        "class": classification.segment_class,
                        "confidence": classification.confidence,
                        "command": classification.command,
                        "args": [
                            {"name": argument.name, "value": argument.value}
                            for argument in classification.args
                        ],
                        "product_type": classification.product_type,
                        "intent_family": classification.intent_family,
                        "intent_properties": dict(classification.intent_properties),
                        "pixel_type": classification.pixel_type,
                        "semantics": classification.semantics,
                        "sources": list(classification.sources),
                        "matched_action_ids": classification.matched_action_ids,
                        "missing_required_slots": classification.missing_required_slots,
                        "blocking_reason_code": classification.blocking_reason_code,
                        "requires_clarification": classification.requires_clarification,
                        "classification_origin": classification.classification_origin,
                        "validation_status": classification.validation_status,
                        "downgrade_reason": classification.downgrade_reason,
                        "matching_recipes": recipe_matches,
                        "intent_to_tool_plan": intent_to_tool_plan,
                        "entity_resolution": (
                            resolved_by_segment[classification.segment_id].as_dict()
                            if classification.segment_id in resolved_by_segment
                            else None
                        ),
                    }
                    if classification is not None
                    else None
                ),
            }
        )

    return {
        "prompt": prompt,
        "scenario_id": scenario_id,
        "constraints_text": constraints_text or "",
        "known_products": list(known_products),
        "segment_count": len(segments),
        "segments": segment_rows,
        "router_plan": {
            "action_count": len(router_plan.actions),
            "actions": [
                {
                    "action_id": action.action_id,
                    "segment": action.segment,
                    "slots": action.slots,
                    "steps": [
                        (
                            {
                                "kind": "tool",
                                "tool_name": step.tool_name,
                                "arguments": step.arguments,
                            }
                            if hasattr(step, "tool_name")
                            else {
                                "kind": "agent",
                                "objective": step.objective,
                                "allowed_tools": step.allowed_tools,
                            }
                        )
                        for step in action.steps
                    ],
                }
                for action in router_plan.actions
            ],
            "unmatched_segments": router_plan.unmatched_segments,
            "is_fully_matched": router_plan.is_fully_matched,
        },
    }


def _print_human_report(report: dict[str, object], *, index: int) -> None:
    print(f"Prompt {index}")
    print(f"  Text: {report['prompt']}")
    if report["scenario_id"] is not None:
        print(f"  scenario_id: {report['scenario_id']}")
    if report.get("constraints_text"):
        print(f"  constraints: {report['constraints_text']}")
    known_products = report.get("known_products") or []
    if known_products:
        print(f"  known_products: {known_products}")
    print(f"  Segments: {report['segment_count']}")

    for segment in report["segments"]:
        assert isinstance(segment, dict)
        classification = segment.get("classification") or {}
        print(f"  - {segment['segment_id']}: {segment['text']}")
        print(
            "    offsets="
            f"{segment['start_char']}:{segment['end_char']} "
            f"imperative={segment['is_imperative_candidate']} "
            f"complexity_guard={segment['has_complexity_guard']} "
            f"seg_conf={segment['segmentation_confidence']:.2f}"
        )
        print(
            "    class="
            f"{classification.get('class')} "
            f"class_conf={classification.get('confidence')} "
            f"origin={classification.get('classification_origin')} "
            f"validation_status={classification.get('validation_status')} "
            f"downgrade_reason={classification.get('downgrade_reason')}"
        )
        if classification.get("command"):
            print(
                "    command="
                f"{classification.get('command')} "
                f"args={json.dumps(classification.get('args', []), ensure_ascii=True, sort_keys=True)}"
            )
        if classification.get("product_type") or classification.get("pixel_type") or classification.get("sources"):
            print(
                "    product="
                f"type={classification.get('product_type')} "
                f"pixel_type={classification.get('pixel_type')} "
                f"sources={classification.get('sources')}"
            )
            print(f"    semantics={classification.get('semantics')}")
        if classification.get("intent_family"):
            print(
                "    intent="
                f"family={classification.get('intent_family')} "
                f"properties={json.dumps(classification.get('intent_properties', {}), ensure_ascii=True, sort_keys=True)}"
            )
            intent_plan = classification.get("intent_to_tool_plan")
            if isinstance(intent_plan, dict):
                print(
                    "    intent_plan="
                    f"status={intent_plan.get('status')} "
                    f"details={json.dumps(intent_plan, ensure_ascii=True, sort_keys=True)}"
                )
        entity_resolution = classification.get("entity_resolution")
        if isinstance(entity_resolution, dict):
            print(
                "    entity_resolution="
                f"{json.dumps(entity_resolution, ensure_ascii=True, sort_keys=True)}"
            )
        recipe_matches = classification.get("matching_recipes") or []
        if recipe_matches:
            print(f"    matching_recipes={len(recipe_matches)}")
            for recipe in recipe_matches:
                assert isinstance(recipe, dict)
                print(
                    "      recipe="
                    f"id={recipe.get('recipe_id')} "
                    f"execution_ref={recipe.get('execution_ref')} "
                    f"requires={recipe.get('requires')} "
                    f"required_parameters={recipe.get('required_parameters')} "
                    f"default_output={recipe.get('default_output_relative_path')}"
                )
        if classification.get("matched_action_ids") or classification.get("blocking_reason_code"):
            print(
                "    router="
                f"matched_action_ids={classification.get('matched_action_ids')} "
                f"blocking_reason_code={classification.get('blocking_reason_code')} "
                f"requires_clarification={classification.get('requires_clarification')}"
            )

    router_plan = report["router_plan"]
    assert isinstance(router_plan, dict)
    print(
        "  Router plan: "
        f"actions={router_plan['action_count']} "
        f"is_fully_matched={router_plan['is_fully_matched']} "
        f"unmatched={router_plan['unmatched_segments']}"
    )
    actions = router_plan.get("actions", [])
    assert isinstance(actions, list)
    for action in actions:
        assert isinstance(action, dict)
        print(f"    * action_id={action['action_id']} segment={action['segment']}")
        print(f"      slots={json.dumps(action['slots'], ensure_ascii=True, sort_keys=True)}")
    print()


def main() -> int:
    args = _parse_args()
    input_path = Path(args.prompts_file).resolve()
    prompts = _load_prompts(input_path)
    if not prompts:
        print(f"No prompts found in {input_path}", file=sys.stderr)
        return 1

    segmenter = PromptSegmenter(model_name=args.segmenter_model)
    extractor = SegmentIntentExtractor() if args.use_ollama else None
    classifier = PromptClassifier(extractor=extractor)
    router = HybridCommandRouter(enabled=True)
    services = build_service_container()
    def _scenario_dir_resolver(sid: str | None) -> Path | None:
        if not sid or not str(sid).strip():
            return None
        try:
            scenario = services.scenario_service.get_scenario(str(sid))
        except Exception:
            return None
        return Path(str(scenario.directory)).resolve()

    resolver = EntityReferenceResolver(
        tool_services=services,
        scenario_directory_resolver=_scenario_dir_resolver,
    )

    reports = [
        _build_report(
            prompt=prompt,
            scenario_id=args.scenario_id,
            segmenter=segmenter,
            classifier=classifier,
            router=router,
            constraints_text=str(args.constraints or "").strip() or None,
            known_products=[str(item).strip() for item in args.known_product if str(item).strip()],
            resolver=resolver,
        )
        for prompt in prompts
    ]

    if args.json:
        print(json.dumps(reports, ensure_ascii=True, indent=2))
        return 0

    print(f"Loaded {len(reports)} prompt(s) from {input_path}")
    print()
    for idx, report in enumerate(reports, start=1):
        _print_human_report(report, index=idx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
