from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.jobs.handlers import ToolImplementations


@dataclass
class _FakeIndex:
    db_path: Path
    seen: dict[str, object]

    def refresh(self, *, relative_root, rebuild, extensions, respect_directives, progress):  # noqa: ANN001
        self.seen = {
            "relative_root": relative_root,
            "rebuild": rebuild,
            "extensions": extensions,
            "respect_directives": respect_directives,
        }
        progress(
            {
                "stage": "index",
                "message": "done",
                "scanned": 2,
                "added": 1,
                "updated": 0,
                "skipped": 1,
                "deleted": 0,
            }
        )
        return {"scanned": 2, "added": 1, "updated": 0, "skipped": 1, "deleted": 0}


def test_assistant_rag_ingest_handler_returns_stats(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    fake = _FakeIndex(db_path=tmp_path / "global_rag.db", seen={})
    monkeypatch.setattr("backend.jobs.handlers.create_default_rag_index", lambda **kwargs: fake)
    result = ToolImplementations.assistant_rag_ingest(
        scenario_id="global",
        relative_root="docs/rag_corpus",
        rebuild=True,
        extensions=["md", "txt"],
    )
    assert result.scanned == 2
    assert result.added == 1
    assert str(result.db_path).endswith("global_rag.db")
    assert fake.seen["relative_root"] == "docs/rag_corpus"
    assert fake.seen["rebuild"] is True
    assert fake.seen["respect_directives"] is True
