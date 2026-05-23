from __future__ import annotations

from pathlib import Path

import pytest

from backend.api.dependencies import _resolve_assistant_legacy_json_path
from backend.api.dependencies import _resolve_assistant_store_path


def test_assistant_store_defaults_to_workspace_root(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()

    sqlite_path = _resolve_assistant_store_path(workspace, {})
    legacy_json_path = _resolve_assistant_legacy_json_path(
        workspace,
        {},
        assistant_store_path=sqlite_path,
    )

    assert sqlite_path == (workspace / ".assistant" / "assistant_sessions.db").resolve()
    assert legacy_json_path == (workspace / ".assistant" / "assistant_sessions.json").resolve()


def test_assistant_store_relative_paths_are_workspace_relative(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    cfg = {
        "session_store_path": ".assistant/custom_sessions.db",
        "session_store_legacy_json_path": ".assistant/custom_sessions.json",
    }

    sqlite_path = _resolve_assistant_store_path(workspace, cfg)
    legacy_json_path = _resolve_assistant_legacy_json_path(
        workspace,
        cfg,
        assistant_store_path=sqlite_path,
    )

    assert sqlite_path == (workspace / ".assistant" / "custom_sessions.db").resolve()
    assert legacy_json_path == (workspace / ".assistant" / "custom_sessions.json").resolve()


def test_assistant_store_rejects_workspace_escape(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()

    with pytest.raises(PermissionError):
        _resolve_assistant_store_path(
            workspace,
            {"session_store_path": "../outside/assistant.db"},
        )
