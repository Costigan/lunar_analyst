from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.services.assistant.rag_index import RagIndex


def test_rag_front_matter_metadata_persisted(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    (corpus_root / "guide.md").write_text(
        "\n".join(
            [
                "title: Generate GeoTIFF Procedure",
                "channel: procedural",
                "tags: geotiff, workflow",
                "",
                "Use raster.transform to generate output geotiff products.",
            ]
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "global_rag.db"
    index = RagIndex(db_path=db_path, corpus_root=corpus_root)
    index.refresh()
    bundle = index.retrieve(
        query="generate output geotiff products",
        top_k=3,
        max_context_chars=3000,
        channel="procedural",
    )
    assert bundle.chunks
    assert bundle.chunks[0].title == "Generate GeoTIFF Procedure"
    assert bundle.chunks[0].channel == "procedural"

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT title, channel FROM documents WHERE relative_path = ?",
            ("guide.md",),
        ).fetchone()
    assert row is not None
    assert row[0] == "Generate GeoTIFF Procedure"
    assert row[1] == "procedural"
