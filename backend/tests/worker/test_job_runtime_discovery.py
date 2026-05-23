from __future__ import annotations

from backend.api.job_runtime import INCLUDE_DRAFT_TOOL_IMPLEMENTATIONS_ENV, discover_tool_implementations
from backend.jobs.handlers import ToolImplementations


def test_discover_tool_implementations_excludes_drafts_by_default(monkeypatch) -> None:
    monkeypatch.delenv(INCLUDE_DRAFT_TOOL_IMPLEMENTATIONS_ENV, raising=False)

    handlers = discover_tool_implementations()

    for name in ToolImplementations.DRAFT_HANDLER_NAMES:
        assert name not in handlers


def test_discover_tool_implementations_includes_drafts_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv(INCLUDE_DRAFT_TOOL_IMPLEMENTATIONS_ENV, "1")

    handlers = discover_tool_implementations()

    for name in ToolImplementations.DRAFT_HANDLER_NAMES:
        assert name in handlers
