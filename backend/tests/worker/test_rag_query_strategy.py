from __future__ import annotations

from pathlib import Path

from backend.services.assistant.rag_index import RagIndex


def test_rag_query_strategy_falls_back_to_or_for_long_queries(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    (corpus_root / "temps.md").write_text(
        "\n".join(
            [
                "title: Thermal Dataset Note",
                "channel: domain",
                "",
                "lunar_temp_dataset includes typical temperature range values.",
            ]
        ),
        encoding="utf-8",
    )
    index = RagIndex(db_path=tmp_path / "global_rag.db", corpus_root=corpus_root)
    index.refresh()

    bundle = index.retrieve(
        query="alpha1 alpha2 alpha3 alpha4 alpha5 alpha6 alpha7 alpha8 lunar_temp_dataset",
        top_k=3,
        max_context_chars=3000,
        channel="domain",
        max_query_terms=24,
        fallback_query_mode="and_then_or",
    )
    assert bundle.chunks
    assert "lunar_temp_dataset" in bundle.context_text
