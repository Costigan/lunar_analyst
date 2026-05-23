from __future__ import annotations

from pathlib import Path

from backend.services.assistant import rag_index
from backend.services.assistant.rag_index import RagIndex


def test_rag_external_html_and_json_ingest(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    (corpus_root / "guide.html").write_text(
        "<html><body><h1>Lunar Guide</h1><p>Use this workflow.</p></body></html>",
        encoding="utf-8",
    )
    (corpus_root / "dataset.json").write_text('{"dataset":"lunar_temp","min":40,"max":390}', encoding="utf-8")
    (corpus_root / "html_descriptor.md").write_text(
        "\n".join(
            [
                "title: HTML Source",
                "channel: procedural",
                "source_kind: file",
                "source_ref: guide.html",
            ]
        ),
        encoding="utf-8",
    )
    (corpus_root / "json_descriptor.md").write_text(
        "\n".join(
            [
                "title: JSON Source",
                "channel: domain",
                "source_kind: file",
                "source_ref: dataset.json",
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
    index.refresh()
    html_bundle = index.retrieve(query="workflow lunar guide", top_k=3, max_context_chars=3000, channel="procedural")
    json_bundle = index.retrieve(query="lunar_temp max", top_k=3, max_context_chars=3000, channel="domain")
    assert html_bundle.chunks
    assert json_bundle.chunks


def test_rag_external_pdf_ingest_uses_pdf_parser(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir(parents=True, exist_ok=True)
    (corpus_root / "book.pdf").write_bytes(b"%PDF-1.7 fake")
    (corpus_root / "pdf_descriptor.md").write_text(
        "\n".join(
            [
                "title: PDF Source",
                "channel: domain",
                "source_kind: file",
                "source_ref: book.pdf",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(rag_index, "_extract_pdf_text_file", lambda path: "Lunar Source Book chapter about temperatures")
    index = RagIndex(
        db_path=tmp_path / "global_rag.db",
        corpus_root=corpus_root,
        allow_external_file_sources=True,
        external_source_allow_roots=[corpus_root],
    )
    index.refresh()
    bundle = index.retrieve(query="source book temperatures", top_k=3, max_context_chars=3000, channel="domain")
    assert bundle.chunks
