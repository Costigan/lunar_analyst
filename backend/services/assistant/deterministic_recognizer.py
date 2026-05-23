from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from backend.services.assistant.action_router_config import EntityKindRoutingRule
from backend.services.assistant.command_router import HybridCommandRouter, PlannedAgentStep
from backend.services.assistant.entity_reference_resolver import SegmentEntityResolution
from backend.services.assistant.prompt_classifier import SegmentArgument, SegmentClassification


@dataclass(frozen=True)
class DeterministicRecognitionTrace:
    segment_id: str
    status: str
    matched_rule_id: str | None
    reason: str
    operation_candidates: list[str]
    target_kind_by_operation: dict[str, str | None]

    def as_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "status": self.status,
            "matched_rule_id": self.matched_rule_id,
            "reason": self.reason,
            "operation_candidates": list(self.operation_candidates),
            "target_kind_by_operation": dict(self.target_kind_by_operation),
        }


class UnifiedDeterministicRecognizer:
    def __init__(
        self,
        *,
        command_router: HybridCommandRouter,
        entity_kind_rules: list[EntityKindRoutingRule],
        entity_kind_routing_enabled: bool = True,
    ) -> None:
        _ = entity_kind_routing_enabled
        self._command_router = command_router
        self._entity_kind_rules = list(entity_kind_rules)

    def promote(
        self,
        *,
        classifications: list[SegmentClassification],
        resolutions: dict[str, SegmentEntityResolution],
        scenario_id: str | None,
    ) -> tuple[list[SegmentClassification], dict[str, DeterministicRecognitionTrace], list[str]]:
        promoted: list[SegmentClassification] = []
        traces: dict[str, DeterministicRecognitionTrace] = {}
        semantic_fallback_segment_ids: list[str] = []

        for classification in classifications:
            if classification.segment_class != "other":
                promoted.append(classification)
                traces[classification.segment_id] = DeterministicRecognitionTrace(
                    segment_id=classification.segment_id,
                    status="not_applicable",
                    matched_rule_id=None,
                    reason=f"segment_class:{classification.segment_class}",
                    operation_candidates=[],
                    target_kind_by_operation={},
                )
                continue

            resolution = resolutions.get(classification.segment_id)
            command_match = self._plan_segment(text=classification.text, scenario_id=scenario_id)
            if command_match is not None and not any(isinstance(step, PlannedAgentStep) for step in command_match.steps):
                args = [
                    SegmentArgument(name=str(key), value=value)
                    for key, value in sorted(command_match.slots.items())
                    if str(key) != "segment"
                ]
                promoted_item = replace(
                    classification,
                    segment_class="command",
                    command=command_match.action_id,
                    args=args,
                    matched_action_ids=[command_match.action_id],
                    classification_origin="deterministic_recognizer:command_router",
                    validation_status="validated",
                    downgrade_reason=None,
                )
                promoted.append(promoted_item)
                traces[classification.segment_id] = DeterministicRecognitionTrace(
                    segment_id=classification.segment_id,
                    status="matched",
                    matched_rule_id=f"command_router:{command_match.action_id}",
                    reason="regex_rule_match",
                    operation_candidates=self._operation_candidates(resolution),
                    target_kind_by_operation=self._target_kinds_by_operation(resolution),
                )
                continue

            recognized = self._recognize_entity_kind_route(
                classification=classification,
                resolution=resolution,
            )
            if recognized is not None:
                promoted_item, trace = recognized
                promoted.append(promoted_item)
                traces[classification.segment_id] = trace
                continue

            promoted.append(classification)
            semantic_fallback_segment_ids.append(classification.segment_id)
            traces[classification.segment_id] = DeterministicRecognitionTrace(
                segment_id=classification.segment_id,
                status="no_match",
                matched_rule_id=None,
                reason="deterministic_no_match",
                operation_candidates=self._operation_candidates(resolution),
                target_kind_by_operation=self._target_kinds_by_operation(resolution),
            )

        return promoted, traces, semantic_fallback_segment_ids

    def _recognize_entity_kind_route(
        self,
        *,
        classification: SegmentClassification,
        resolution: SegmentEntityResolution | None,
    ) -> tuple[SegmentClassification, DeterministicRecognitionTrace] | None:
        if resolution is None:
            return None
        operation_candidates = self._operation_candidates(resolution)
        if not operation_candidates:
            return None

        resolved_kinds = {
            str(item.kind).strip().lower()
            for item in resolution.mentions
            if str(item.kind).strip() and str(item.resolved_id or "").strip()
        }
        fallback_target_kind = str(resolution.target_kind or "").strip().lower()
        fallback_target_id = str(resolution.target_resolved_id or "").strip()
        if fallback_target_kind and fallback_target_id:
            resolved_kinds.add(fallback_target_kind)

        target_kind_by_operation = self._target_kinds_by_operation(resolution)
        for rule in self._entity_kind_rules:
            for operation in operation_candidates:
                if operation not in set(rule.required_verbs):
                    continue
                target = self._select_target_for_operation(operation=operation, resolution=resolution)
                target_kind = str(target.get("kind") or "").strip().lower()
                candidate_resolved_kinds = set(resolved_kinds)
                if target_kind and str(target.get("resolved_id") or "").strip():
                    candidate_resolved_kinds.add(target_kind)
                missing = [kind for kind in rule.required_entity_kinds if kind not in candidate_resolved_kinds]
                if missing:
                    continue
                if not rule.allow_ambiguity and resolution.ambiguities:
                    continue

                family, props = self._intent_payload(
                    operation=operation,
                    target_kind=target_kind,
                    target_mention=str(target.get("mention_text") or "").strip(),
                    allowed_families=rule.intent_families,
                )
                if not family:
                    continue
                promoted_item = replace(
                    classification,
                    segment_class="intent_family",
                    intent_family=family,
                    intent_properties=props,
                    classification_origin=f"deterministic_recognizer:{rule.rule_id}",
                    validation_status="validated",
                    downgrade_reason=None,
                )
                trace = DeterministicRecognitionTrace(
                    segment_id=classification.segment_id,
                    status="matched",
                    matched_rule_id=rule.rule_id,
                    reason=f"entity_kind_rule:{rule.rule_id}",
                    operation_candidates=operation_candidates,
                    target_kind_by_operation=target_kind_by_operation,
                )
                return promoted_item, trace
        return None

    def _intent_payload(
        self,
        *,
        operation: str,
        target_kind: str,
        target_mention: str,
        allowed_families: list[str],
    ) -> tuple[str, dict[str, Any]]:
        allowed = set(str(item).strip() for item in allowed_families if str(item).strip())

        if operation in {"goto", "search"} and target_kind == "feature":
            family = "location_navigation"
            if allowed and family not in allowed:
                return "", {}
            return family, {"operation": operation, "feature_ref": target_mention}

        if operation == "show" and target_kind == "feature":
            family = "location_navigation"
            if allowed and family not in allowed:
                return "", {}
            return family, {"operation": "goto", "feature_ref": target_mention}

        if operation in {"show", "hide"} and target_kind in {"layer", "file"}:
            family = "layer_visibility_update"
            if allowed and family not in allowed:
                return "", {}
            return family, {
                "operation": operation,
                "target": {"layer_ref": target_mention},
            }

        if operation == "set_current" and target_kind == "scenario":
            family = "scenario_context_management"
            if allowed and family not in allowed:
                return "", {}
            return family, {"operation": operation, "scenario_ref": target_mention}

        return "", {}

    @staticmethod
    def _operation_candidates(resolution: SegmentEntityResolution | None) -> list[str]:
        if resolution is None:
            return []
        verb = resolution.verb_normalization
        candidates = list(verb.operation_candidates or verb.candidates or [])
        if not candidates and str(verb.canonical_operation or "").strip():
            candidates = [str(verb.canonical_operation).strip()]
        return sorted(set(str(item).strip().lower() for item in candidates if str(item).strip()))

    @staticmethod
    def _target_kinds_by_operation(resolution: SegmentEntityResolution | None) -> dict[str, str | None]:
        if resolution is None:
            return {}
        return {
            op: str(UnifiedDeterministicRecognizer._select_target_for_operation(operation=op, resolution=resolution).get("kind") or "") or None
            for op in UnifiedDeterministicRecognizer._operation_candidates(resolution)
        }

    @staticmethod
    def _select_target_for_operation(
        *,
        operation: str,
        resolution: SegmentEntityResolution,
    ) -> dict[str, str | None]:
        resolved_mentions = [item for item in resolution.mentions if str(item.resolved_id or "").strip()]
        if not resolved_mentions:
            fallback_kind = str(resolution.target_kind or "").strip() or None
            fallback_mention = str(resolution.target_mention or "").strip() or None
            fallback_id = str(resolution.target_resolved_id or "").strip() or None
            return {"kind": fallback_kind, "mention_text": fallback_mention, "resolved_id": fallback_id}

        object_mentions = [item for item in resolved_mentions if item.dep_role in {"dobj", "obj", "pobj"}]
        ranked_pool = object_mentions or resolved_mentions
        preferred_kinds: list[str] = []
        if operation in {"goto", "search"}:
            preferred_kinds = ["feature", "scenario"]
        elif operation in {"show", "hide"}:
            preferred_kinds = ["layer", "file", "feature"]
        elif operation in {"set_current"}:
            preferred_kinds = ["scenario"]

        for preferred in preferred_kinds:
            preferred_match = next((item for item in ranked_pool if item.kind == preferred), None)
            if preferred_match is not None:
                return {
                    "kind": preferred_match.kind,
                    "mention_text": preferred_match.mention_text,
                    "resolved_id": preferred_match.resolved_id,
                }

        top = sorted(
            ranked_pool,
            key=lambda item: (float(item.confidence), 1 if item.dep_role in {"dobj", "obj", "pobj"} else 0),
            reverse=True,
        )[0]
        return {
            "kind": top.kind,
            "mention_text": top.mention_text,
            "resolved_id": top.resolved_id,
        }

    def _plan_segment(self, *, text: str, scenario_id: str | None) -> Any | None:
        planner = getattr(self._command_router, "_plan_segment", None)
        if callable(planner):
            return planner(segment=text, scenario_id=scenario_id)
        command_plan = self._command_router.plan(prompt=text, scenario_id=scenario_id)
        if command_plan.actions:
            return command_plan.actions[0]
        return None
