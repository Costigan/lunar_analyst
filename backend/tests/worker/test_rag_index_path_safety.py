from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.assistant.rag_index import RagIndex


def test_rag_index_refresh_and_retrieve(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    doc = corpus_root / "terrain.md"
    doc.write_text(
        "# Terrain Constraints\n\nMax slope threshold is 8 degrees for this mission.\n",
        encoding="utf-8",
    )
    index = RagIndex(db_path=tmp_path / "global_rag.db", corpus_root=corpus_root)
    stats = index.refresh()
    assert stats["scanned"] == 1
    bundle = index.retrieve(query="max slope threshold", top_k=3, max_context_chars=2000)
    assert bundle.chunks
    assert "8 degrees" in bundle.context_text
    assert bundle.references()[0]["relative_path"] == "terrain.md"


def test_rag_index_rejects_path_escape(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    index = RagIndex(db_path=tmp_path / "global_rag.db", corpus_root=corpus_root)
    with pytest.raises(PermissionError):
        index._sanitize_relative_path("../escape.txt")  # noqa: SLF001

