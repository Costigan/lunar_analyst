from __future__ import annotations

import logging
from pathlib import Path

import backend.services.assistant.rag_index as rag_index_module
from backend.services.assistant.rag_index import RagIndex


def test_rag_refresh_logs_added_and_updated_documents(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    doc = corpus_root / "guide.md"
    doc.write_text("title: Guide\nchannel: procedural\n\nInitial text.", encoding="utf-8")
    index = RagIndex(db_path=tmp_path / "global_rag.db", corpus_root=corpus_root)
    messages: list[str] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = _CaptureHandler(level=logging.INFO)
    logger = rag_index_module.logger
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        index.refresh()
        assert any("assistant rag index document action=added relative_path=guide.md" in message for message in messages)
        assert any("assistant rag index refresh scanned=1 added=1 updated=0" in message for message in messages)

        messages.clear()
        doc.write_text("title: Guide\nchannel: procedural\n\nUpdated text.", encoding="utf-8")
        index.refresh()
        assert any("assistant rag index document action=updated relative_path=guide.md" in message for message in messages)
        assert any("assistant rag index refresh scanned=1 added=0 updated=1" in message for message in messages)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
