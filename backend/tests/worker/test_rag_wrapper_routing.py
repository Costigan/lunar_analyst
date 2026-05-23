from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from backend.services.assistant.providers.base import ProviderCompletion
from backend.services.assistant.providers.rag_wrapper_provider import RagWrapperProvider
from backend.services.assistant.query_router import ChannelBudget
from backend.services.assistant.rag_index import RetrievalBundle, RetrievedChunk


@dataclass
class _BaseProvider:
    captured_system_prompt: str = ""

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
        del model_id, conversation, session_id, on_delta, cache_context, tool_schema, max_output_tokens, thinking
        self.captured_system_prompt = system_prompt
        return ProviderCompletion(text="ok", finish_reason="stop")


@dataclass
class _RoutingRetriever:
    calls: list[dict[str, Any]] = field(default_factory=list)

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
        del scenario_id, max_context_chars, max_query_terms, fallback_query_mode
        self.calls.append({"query": query, "top_k": top_k, "channel": channel})
        channel_value = str(channel or "mixed")
        chunk = RetrievedChunk(
            chunk_id=f"{channel_value}.md:0",
            relative_path=f"{channel_value}.md",
            content=f"{channel_value} guidance for {query}",
            score=1.0,
            snippet=f"{channel_value} guidance",
            title=f"{channel_value} title",
            channel=channel_value,
        )
        return RetrievalBundle(chunks=[chunk], context_text="")


def test_rag_wrapper_routing_fans_out_across_channels() -> None:
    base = _BaseProvider()
    retriever = _RoutingRetriever()
    wrapper = RagWrapperProvider(
        provider_id="rag_openai",
        base_provider=base,
        retriever=retriever,
        default_model="model-a",
        models=["model-a"],
        routing_enabled=True,
        budget_procedural=ChannelBudget(0.8, 0.2),
        budget_domain=ChannelBudget(0.2, 0.8),
        budget_mixed=ChannelBudget(0.5, 0.5),
    )
    result = wrapper.complete(
        model_id="model-a",
        system_prompt="You are assistant.\nActive scenario_id: scn_1",
        conversation=[{"role": "user", "content": "How do I produce a new geotiff file?"}],
    )
    channels = {call["channel"] for call in retriever.calls}
    assert channels == {"procedural", "domain"}
    assert result.references
    assert "Retrieved context" in base.captured_system_prompt
