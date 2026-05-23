# Codex Review 1

Reviewed commit: `b78581b56ac0926da8ecbc483c9bfea9be162e85`

## Findings

- [P2] Preserve user-selected model when options refresh — `backend/web/lunar_analyst/src/hooks/useAssistantSession.ts:310-314`
  The model selection effect now returns `providerDefaultModel` before checking the current selection, so any catalog/provider refresh can silently reset a valid user-picked model back to the provider default. This regression only appears when the provider list/options update after the user has switched to a non-default model, but in that case the UI will keep undoing the user choice.

- [P2] Keep routed fan-out budgets consistent with small `top_k` — `backend/services/assistant/providers/rag_wrapper_provider.py:188-195`
  In routed retrieval, both `procedural_k` and `domain_k` are clamped to at least 1, so when `top_k` is 1 the overflow logic cannot reduce either side and the total allocation remains 2. The merge then truncates to one result in procedural-first order, which can return the wrong channel for domain-intent queries under low `top_k` configurations.
