from __future__ import annotations

import json
from pathlib import Path

from backend.contracts.assistant_models import AssistantConfirmationActionType, AssistantRole
from backend.services.assistant.session_store import AssistantSessionStore


def test_session_store_roundtrip(tmp_path: Path) -> None:
    store = AssistantSessionStore(tmp_path / "assistant.db")
    session = store.create_session("Store Test")
    assert session.session_id.startswith("as_")

    message = store.add_message(
        session_id=session.session_id,
        role=AssistantRole.USER,
        content="hello",
        outputs=[
            {
                "output_id": "out_msg",
                "kind": "artifact_card",
                "mime_type": "application/vnd.lunar-analyst.artifact-card+json",
                "storage": "inline",
                "data": {"name": "hello.txt"},
                "metadata": {},
            }
        ],
    )
    assert message.session_id == session.session_id
    assert message.outputs[0].output_id == "out_msg"

    turn = store.create_turn(session_id=session.session_id, user_message_id=message.message_id)
    assert turn.status.value == "queued"
    turn = store.update_turn(turn.turn_id, status=turn.status.RUNNING)
    assert turn.status.value == "running"

    call = store.create_tool_call(
        session_id=session.session_id,
        turn_id=turn.turn_id,
        tool_name="scenario.list",
        arguments={},
        status="proposed",
        action_type=None,
    )
    assert call.tool_name == "scenario.list"
    completed = store.complete_tool_call(
        call.tool_call_id,
        status="completed",
        result={"summary_text": "ok"},
        outputs=[
            {
                "output_id": "out_call",
                "kind": "table",
                "mime_type": "application/vnd.lunar-analyst.table+json",
                "storage": "inline",
                "data": {"columns": [], "rows": []},
                "metadata": {},
            }
        ],
    )
    assert completed.outputs[0].output_id == "out_call"

    confirmation = store.create_confirmation(
        session_id=session.session_id,
        turn_id=turn.turn_id,
        action_type=AssistantConfirmationActionType.LAUNCH_JOB,
        tool_name="job.launch",
        arguments={"handler_name": "ping", "params": {}},
    )
    assert confirmation.status == "pending"

    sessions = store.list_sessions()
    assert any(item.session_id == session.session_id for item in sessions)


def test_session_store_creates_missing_parent_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "workspace" / ".assistant" / "assistant_sessions.db"

    store = AssistantSessionStore(db_path)
    try:
        assert db_path.exists()
        assert db_path.parent.is_dir()
    finally:
        store.close()


def test_session_store_initialization_error_includes_database_path(tmp_path: Path) -> None:
    db_path = tmp_path / "assistant_store_dir"
    db_path.mkdir()

    try:
        AssistantSessionStore(db_path)
    except RuntimeError as exc:
        message = str(exc)
        assert str(db_path.resolve()) in message
        assert "failed to initialize assistant session store" in message
    else:  # pragma: no cover - sqlite should not open a directory as a database
        raise AssertionError("expected assistant store initialization to fail for a directory path")


def test_session_store_migrates_legacy_json(tmp_path: Path) -> None:
    legacy_path = tmp_path / "assistant_sessions.json"
    legacy_path.write_text(
        json.dumps(
            {
                "sessions": {
                    "as_legacy": {
                        "session_id": "as_legacy",
                        "title": "Legacy Session",
                        "created_at_utc": "2026-03-03T01-00-00",
                        "updated_at_utc": "2026-03-03T01-00-00",
                        "last_message_at_utc": "2026-03-03T01-00-01",
                        "policy": {"always_allow_action_types": []},
                    }
                },
                "messages": {
                    "msg_legacy": {
                        "message_id": "msg_legacy",
                        "session_id": "as_legacy",
                        "role": "user",
                        "content": "legacy hello",
                        "created_at_utc": "2026-03-03T01-00-01",
                        "turn_id": None,
                        "metadata": {},
                    }
                },
                "turns": {},
                "tool_calls": {},
                "confirmations": {},
                "compactions": {},
            }
        ),
        encoding="utf-8",
    )
    store = AssistantSessionStore(tmp_path / "assistant_sessions.db", legacy_json_path=legacy_path)

    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id == "as_legacy"
    assert sessions[0].title == "Legacy Session"
    messages = store.list_messages("as_legacy")
    assert len(messages) == 1
    assert messages[0].message_id == "msg_legacy"
    assert messages[0].content == "legacy hello"
    assert messages[0].outputs == []
