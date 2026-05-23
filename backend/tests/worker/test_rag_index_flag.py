from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.services.assistant.rag_index import RagIndex


def test_rag_index_false_front_matter_skips_document(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    (corpus_root / "README.md").write_text(
        "\n".join(
            [
                "index: false",
                "",
                "# Operational Notes",
                "",
                "This should not be indexed.",
            ]
        ),
        encoding="utf-8",
    )
    index = RagIndex(db_path=tmp_path / "global_rag.db", corpus_root=corpus_root)
    stats = index.refresh()
    assert stats["scanned"] == 1
    assert stats["added"] == 0
    assert stats["skipped"] == 1

    with sqlite3.connect(tmp_path / "global_rag.db") as conn:
        count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert int(count) == 0
