from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.assistant.rag_index import RagIndex


def test_rag_external_file_source_rejects_escape(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (corpus_root / "descriptor.md").write_text(
        "\n".join(
            [
                "title: Escape Test",
                "channel: procedural",
                "source_kind: file",
                "source_ref: ../outside.txt",
            ]
        ),
        encoding="utf-8",
    )
    index = RagIndex(
        db_path=tmp_path / "global_rag.db",
        corpus_root=corpus_root,
        allow_external_file_sources=True,
        external_source_allow_roots=[corpus_root],
    )
    with pytest.raises(PermissionError):
        index.refresh()
