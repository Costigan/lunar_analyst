from __future__ import annotations

from backend.contracts.assistant_models import (
    AssistantConfirmationActionType,
    AssistantConfirmationDecision,
    AssistantPolicy,
)


class AssistantPolicyService:
    def __init__(self, *, require_confirmation_for_mutations: bool = True) -> None:
        self._require_confirmation = bool(require_confirmation_for_mutations)

    def requires_confirmation(
        self,
        *,
        action_type: AssistantConfirmationActionType | None,
        policy: AssistantPolicy,
    ) -> bool:
        if action_type is None:
            return False
        if not self._require_confirmation:
            return False
        return action_type not in set(policy.always_allow_action_types)

    def apply_decision(
        self,
        *,
        action_type: AssistantConfirmationActionType,
        decision: AssistantConfirmationDecision,
        policy: AssistantPolicy,
    ) -> AssistantPolicy:
        if decision != AssistantConfirmationDecision.ALWAYS_ALLOW_ACTION_TYPE:
            return policy
        current = list(policy.always_allow_action_types)
        if action_type not in current:
            current.append(action_type)
        return AssistantPolicy(always_allow_action_types=current)
