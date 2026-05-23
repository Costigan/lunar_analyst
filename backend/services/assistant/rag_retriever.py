from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.services.assistant.rag_index import RagIndex, RetrievalBundle


class RagRetriever(Protocol):
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
        ...


@dataclass(frozen=True)
class Fts5RagRetriever:
    index: RagIndex

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
        del scenario_id
        return self.index.retrieve(
            query=query,
            top_k=top_k,
            max_context_chars=max_context_chars,
            channel=channel,
            max_query_terms=max_query_terms,
            fallback_query_mode=fallback_query_mode,
        )
