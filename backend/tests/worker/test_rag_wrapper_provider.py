from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from backend.services.assistant.providers.base import ProviderCompletion
from backend.services.assistant.providers.rag_wrapper_provider import RagWrapperProvider
from backend.services.assistant.rag_index import RetrievalBundle, RetrievedChunk


@dataclass
class _BaseProvider:
    provider_id: str = "base"
    captured: dict[str, Any] = field(default_factory=dict)

    def list_models(self) -> list[str]:
        return ["model-a"]

    def complete(
        self,
        *,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        session_id: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        cache_context: dict[str, str] | None = None,
        tool_schema: list[dict[str, object]] | None = None,
        max_output_tokens: int | None = None,
        thinking: bool | str | None = None,
    ) -> ProviderCompletion:
        self.captured = {
            "model_id": model_id,
            "system_prompt": system_prompt,
            "conversation": conversation,
            "session_id": session_id,
            "cache_context": cache_context,
            "tool_schema": tool_schema,
            "max_output_tokens": max_output_tokens,
            "thinking": thinking,
        }
        if callable(on_delta):
            on_delta("x")
        return ProviderCompletion(text="ok", finish_reason="stop")


@dataclass
class _BaseProviderNoThinking:
    provider_id: str = "base"
    captured: dict[str, Any] = field(default_factory=dict)

    def list_models(self) -> list[str]:
        return ["model-a"]

    def complete(
        self,
        *,
        model_id: str,
        system_prompt: str,
        conversation: list[dict[str, str]],
        session_id: str | None = None,
        on_delta: Callable[[str], None] | None = None,
        cache_context: dict[str, str] | None = None,
        tool_schema: list[dict[str, object]] | None = None,
        max_output_tokens: int | None = None,
    ) -> ProviderCompletion:
        self.captured = {
            "model_id": model_id,
            "system_prompt": system_prompt,
            "conversation": conversation,
            "session_id": session_id,
            "cache_context": cache_context,
            "tool_schema": tool_schema,
            "max_output_tokens": max_output_tokens,
        }
        if callable(on_delta):
            on_delta("x")
        return ProviderCompletion(text="ok", finish_reason="stop")


@dataclass(frozen=True)
class _Retriever:
    def retrieve(
        self,
        *,
        query: str,
        top_k: int,
        max_context_chars: int,
        scenario_id: str | None = None,
        channel: str | None = None,
        max_query_terms: int = 24,
        fallback_query_mode: str = "and_then_or",
    ) -> RetrievalBundle:
        del top_k, max_context_chars, scenario_id, channel, max_query_terms, fallback_query_mode
        assert "slope" in query
        return RetrievalBundle(
            chunks=[
                RetrievedChunk(
                    chunk_id="terrain.md:0",
                    relative_path="terrain.md",
                    content="Max slope threshold is 8 degrees.",
                    score=1.0,
                    snippet="Max slope threshold is 8 degrees.",
                )
            ],
            context_text="[src#1 path=terrain.md chunk=terrain.md:0]\nMax slope threshold is 8 degrees.",
        )


@dataclass
class _CapturingRetriever:
    queries: list[tuple[str, str | None]] = field(default_factory=list)

    def retrieve(
        self,
        *,
        query: str,
        top_k: int,
        max_context_chars: int,
        scenario_id: str | None = None,
        channel: str | None = None,
        max_query_terms: int = 24,
        fallback_query_mode: str = "and_then_or",
    ) -> RetrievalBundle:
        del top_k, max_context_chars, scenario_id, max_query_terms, fallback_query_mode
        self.queries.append((query, channel))
        if "Layer Visibility Intent Policy" in query:
            return RetrievalBundle(
                chunks=[
                    RetrievedChunk(
                        chunk_id="guidance_layer_visibility_fewshot.txt:0",
                        relative_path="guidance_layer_visibility_fewshot.txt",
                        content="Use layer.update_state for show/hide.",
                        score=2.0,
                        snippet="Use layer.update_state for show/hide.",
                    )
                ],
                context_text="[src#1 path=guidance_layer_visibility_fewshot.txt chunk=guidance_layer_visibility_fewshot.txt:0]\nUse layer.update_state for show/hide.",
            )
        return RetrievalBundle(chunks=[], context_text="")


@dataclass(frozen=True)
class _LongRetriever:
    def retrieve(
        self,
        *,
        query: str,
        top_k: int,
        max_context_chars: int,
        scenario_id: str | None = None,
        channel: str | None = None,
        max_query_terms: int = 24,
        fallback_query_mode: str = "and_then_or",
    ) -> RetrievalBundle:
        del query, top_k, max_context_chars, scenario_id, channel, max_query_terms, fallback_query_mode
        long_text = "A" * 300
        return RetrievalBundle(
            chunks=[
                RetrievedChunk(
                    chunk_id="a.md:0",
                    relative_path="a.md",
                    content=long_text,
                    score=1.0,
                    snippet="A",
                ),
                RetrievedChunk(
                    chunk_id="b.md:0",
                    relative_path="b.md",
                    content=long_text,
                    score=0.9,
                    snippet="B",
                ),
            ],
            context_text="",
        )


@dataclass(frozen=True)
class _UnicodeRetriever:
    def retrieve(
        self,
        *,
        query: str,
        top_k: int,
        max_context_chars: int,
        scenario_id: str | None = None,
        channel: str | None = None,
        max_query_terms: int = 24,
        fallback_query_mode: str = "and_then_or",
    ) -> RetrievalBundle:
        del query, top_k, max_context_chars, scenario_id, channel, max_query_terms, fallback_query_mode
        return RetrievalBundle(
            chunks=[
                RetrievedChunk(
                    chunk_id="u.md:0",
                    relative_path="u.md",
                    content="Line one with unicode \u2014 and snowman \u2603.\nline two\n\n\nline three.",
                    score=1.0,
                    snippet="unicode",
                )
            ],
            context_text="",
        )


def test_rag_wrapper_injects_context_and_references() -> None:
    base = _BaseProvider()
    wrapper = RagWrapperProvider(
        provider_id="rag_openai",
        base_provider=base,
        retriever=_Retriever(),
        default_model="model-a",
        models=["model-a"],
    )
    deltas: list[str] = []
    result = wrapper.complete(
        model_id="model-a",
        system_prompt="You are assistant.\nActive scenario_id: scn_1",
        conversation=[{"role": "user", "content": "What is max slope?"}],
        session_id="s1",
        on_delta=deltas.append,
        cache_context={"stable_prefix_hash": "x"},
        tool_schema=[{"type": "function"}],
        max_output_tokens=512,
        thinking="high",
    )
    assert "Retrieved context" in base.captured["system_prompt"]
    assert result.text == "ok"
    assert result.references
    assert result.references[0]["relative_path"] == "terrain.md"
    assert deltas == ["x"]


def test_rag_wrapper_handles_base_provider_without_thinking_param() -> None:
    base = _BaseProviderNoThinking()
    wrapper = RagWrapperProvider(
        provider_id="ollama",
        base_provider=base,
        retriever=_Retriever(),
        default_model="model-a",
        models=["model-a"],
    )
    result = wrapper.complete(
        model_id="model-a",
        system_prompt="You are assistant.",
        conversation=[{"role": "user", "content": "What is max slope?"}],
        thinking="high",
    )
    assert result.text == "ok"
    assert "system_prompt" in base.captured


def test_rag_wrapper_applies_visibility_guidance_trigger() -> None:
    base = _BaseProvider()
    retriever = _CapturingRetriever()
    wrapper = RagWrapperProvider(
        provider_id="ollama",
        base_provider=base,
        retriever=retriever,
        default_model="model-a",
        models=["model-a"],
    )
    result = wrapper.complete(
        model_id="model-a",
        system_prompt="You are assistant.\nActive scenario_id: scn_1",
        conversation=[{"role": "user", "content": "show slope"}],
    )
    assert result.text == "ok"
    assert any("Layer Visibility Intent Policy" in query for query, _channel in retriever.queries)


def test_rag_wrapper_references_match_injected_context_when_capped() -> None:
    base = _BaseProvider()
    wrapper = RagWrapperProvider(
        provider_id="rag_openai",
        base_provider=base,
        retriever=_LongRetriever(),
        default_model="model-a",
        models=["model-a"],
        max_context_chars=380,
    )
    result = wrapper.complete(
        model_id="model-a",
        system_prompt="You are assistant.\nActive scenario_id: scn_1",
        conversation=[{"role": "user", "content": "slope"}],
    )
    assert result.references
    assert len(result.references) == 1
    assert result.references[0]["chunk_id"] == "a.md:0"
    assert "rag_context_text" in result.metadata
    assert "[src#1 path=a.md chunk=a.md:0]" in str(result.metadata["rag_context_text"])


def test_rag_wrapper_sanitizes_context_to_ascii_and_compacts_newlines() -> None:
    base = _BaseProvider()
    wrapper = RagWrapperProvider(
        provider_id="ollama",
        base_provider=base,
        retriever=_UnicodeRetriever(),
        default_model="model-a",
        models=["model-a"],
        max_context_chars=1000,
    )
    result = wrapper.complete(
        model_id="model-a",
        system_prompt="You are assistant.",
        conversation=[{"role": "user", "content": "slope"}],
    )
    context_text = str(result.metadata.get("rag_context_text", ""))
    assert context_text
    assert all(ord(ch) < 128 for ch in context_text)
    assert "\n\n\n" not in context_text
    assert "Line one with unicode" in context_text
    assert "line two" in context_text
    assert "line three." in context_text
