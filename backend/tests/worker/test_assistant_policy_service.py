from __future__ import annotations

from backend.contracts.assistant_models import (
    AssistantConfirmationActionType,
    AssistantConfirmationDecision,
    AssistantPolicy,
)
from backend.services.assistant.policy_service import AssistantPolicyService


def test_policy_requires_confirmation_by_default() -> None:
    svc = AssistantPolicyService(require_confirmation_for_mutations=True)
    policy = AssistantPolicy(always_allow_action_types=[])
    assert svc.requires_confirmation(
        action_type=AssistantConfirmationActionType.LAUNCH_JOB,
        policy=policy,
    )


def test_policy_always_allow_action_type() -> None:
    svc = AssistantPolicyService(require_confirmation_for_mutations=True)
    policy = AssistantPolicy(always_allow_action_types=[])
    updated = svc.apply_decision(
        action_type=AssistantConfirmationActionType.IMPORT_FILE,
        decision=AssistantConfirmationDecision.ALWAYS_ALLOW_ACTION_TYPE,
        policy=policy,
    )
    assert AssistantConfirmationActionType.IMPORT_FILE in updated.always_allow_action_types
    assert not svc.requires_confirmation(
        action_type=AssistantConfirmationActionType.IMPORT_FILE,
        policy=updated,
    )
