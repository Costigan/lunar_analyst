from __future__ import annotations

from pathlib import Path

from backend.services.assistant.rag_index import RagIndex


def test_rag_retrieval_filters_by_channel(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    (corpus_root / "proc.md").write_text(
        "\n".join(
            [
                "title: Raster Procedure",
                "channel: procedural",
                "",
                "Use raster.transform with output_relative_path for geotiff creation.",
            ]
        ),
        encoding="utf-8",
    )
    (corpus_root / "domain.md").write_text(
        "\n".join(
            [
                "title: Lunar Thermal Range",
                "channel: domain",
                "",
                "Typical lunar surface temperatures vary widely by location and illumination.",
            ]
        ),
        encoding="utf-8",
    )
    index = RagIndex(db_path=tmp_path / "global_rag.db", corpus_root=corpus_root)
    index.refresh()

    procedural = index.retrieve(
        query="geotiff output path transform",
        top_k=4,
        max_context_chars=4000,
        channel="procedural",
    )
    assert procedural.chunks
    assert all(chunk.channel == "procedural" for chunk in procedural.chunks)

    domain = index.retrieve(
        query="lunar surface temperatures illumination",
        top_k=4,
        max_context_chars=4000,
        channel="domain",
    )
    assert domain.chunks
    assert all(chunk.channel == "domain" for chunk in domain.chunks)


def test_rag_retrieval_channel_includes_mixed_fallback(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    (corpus_root / "mixed.md").write_text(
        "\n".join(
            [
                "title: Kaguya Summary",
                "channel: mixed",
                "",
                "Kaguya SELENE was a Japanese lunar orbiter mission.",
            ]
        ),
        encoding="utf-8",
    )
    index = RagIndex(db_path=tmp_path / "global_rag.db", corpus_root=corpus_root)
    index.refresh()

    bundle = index.retrieve(
        query="kaguya mission",
        top_k=4,
        max_context_chars=4000,
        channel="domain",
    )
    assert bundle.chunks
    assert any(chunk.channel == "mixed" for chunk in bundle.chunks)
