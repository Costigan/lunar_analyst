from __future__ import annotations

from backend.services.assistant.provider_registry import AssistantProviderRegistry


def test_provider_catalog_includes_rag_wrappers_when_enabled(tmp_path) -> None:  # noqa: ANN001
    registry = AssistantProviderRegistry(
        config={
            "default_provider": "ollama",
            "default_model": "gpt-oss:20b",
            "ollama": {
                "enabled": True,
                "base_url": "http://127.0.0.1:11434",
                "model": "gpt-oss:20b",
                "models": ["gpt-oss:20b"],
                "discover_models": False,
            },
            "remote": {
                "openai": {
                    "enabled": True,
                    "api_key_env": "OPENAI_API_KEY",
                    "base_url": "https://api.openai.com",
                    "model": "gpt-5-mini",
                    "models": ["gpt-5-mini"],
                }
            },
            "rag": {
                "enabled": True,
                "apply_to_providers": ["ollama", "openai"],
                "global_index_relative_path": ".assistant/rag/global_rag.db",
                "corpus_relative_root": "docs/rag_corpus",
                "auto_refresh_on_startup": False,
            },
        },
        workspace_root=str(tmp_path / "workspace"),
    )
    catalog = registry.catalog()
    providers = {item.provider_id: item for item in catalog.providers}
    assert "ollama" in providers
    assert "openai" in providers
    assert "rag_ollama" not in providers
    assert "rag_openai" not in providers
    assert providers["ollama"].execution_mode == "tool_loop"
    assert providers["openai"].execution_mode == "tool_loop"
