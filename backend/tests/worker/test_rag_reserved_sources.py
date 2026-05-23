from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.services.assistant import rag_index
from backend.services.assistant.rag_index import RagIndex


def test_descriptor_reserves_referenced_pdf_from_standalone_indexing(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    (corpus_root / "lunar_source_book.pdf").write_bytes(b"%PDF-1.7 fake")
    (corpus_root / "lunar_source_book.txt").write_text(
        "\n".join(
            [
                "title: Lunar Source Book",
                "channel: domain",
                "source_kind: file",
                "source_ref: lunar_source_book.pdf",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        rag_index,
        "_extract_pdf_text_file",
        lambda path: "Lunar Source Book external content for retrieval",
    )

    db_path = tmp_path / "global_rag.db"
    index = RagIndex(
        db_path=db_path,
        corpus_root=corpus_root,
        allow_external_file_sources=True,
        external_source_allow_roots=[corpus_root],
    )
    stats = index.refresh()
    assert stats["scanned"] == 2
    assert stats["added"] == 1

    with sqlite3.connect(db_path) as conn:
        docs = conn.execute("SELECT relative_path FROM documents ORDER BY relative_path").fetchall()
    assert [row[0] for row in docs] == ["lunar_source_book.txt"]
