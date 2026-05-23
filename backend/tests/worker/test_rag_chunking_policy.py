from __future__ import annotations

from pathlib import Path

from backend.services.assistant.rag_index import RagIndex


def test_single_chunk_directive_for_markdown(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            "RAG_CHUNKING: single",
            "# Long Note",
            "",
            "Line 1",
            "",
            "Line 2",
        ]
    )
    (corpus_root / "note.md").write_text(content, encoding="utf-8")
    index = RagIndex(db_path=tmp_path / "global_rag.db", corpus_root=corpus_root)
    index.refresh()
    bundle = index.retrieve(query="long note line 1 line 2", top_k=3, max_context_chars=4000)
    assert len(bundle.chunks) == 1
    assert "RAG_CHUNKING: single" not in bundle.chunks[0].content


def test_csv_chunking_preserves_header_row(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    lines = ["id,value"]
    for idx in range(1, 65):
        lines.append(f"{idx},{idx * 2}")
    (corpus_root / "table.csv").write_text("\n".join(lines), encoding="utf-8")
    index = RagIndex(db_path=tmp_path / "global_rag.db", corpus_root=corpus_root)
    index.refresh()
    bundle = index.retrieve(query="id value 64", top_k=5, max_context_chars=6000)
    assert bundle.chunks
    assert "id,value" in bundle.chunks[0].content

