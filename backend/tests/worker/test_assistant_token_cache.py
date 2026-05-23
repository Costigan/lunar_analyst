from __future__ import annotations

from backend.services.assistant.token_cache import build_cache_context


def test_cache_context_stable_hash_changes_with_system_prompt() -> None:
    a = build_cache_context(
        provider_id="openai",
        model_id="gpt-test",
        system_prompt="A",
        tool_schema=[{"name": "scenario.list"}],
        scenario_id="scn_a",
        compacted_summary=None,
    )
    b = build_cache_context(
        provider_id="openai",
        model_id="gpt-test",
        system_prompt="B",
        tool_schema=[{"name": "scenario.list"}],
        scenario_id="scn_a",
        compacted_summary=None,
    )
    assert a["stable_prefix_hash"] != b["stable_prefix_hash"]
