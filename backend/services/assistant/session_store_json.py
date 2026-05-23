from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.contracts.assistant_models import (
    AssistantConfirmation,
    AssistantConfirmationActionType,
    AssistantConfirmationDecision,
    AssistantMessage,
    AssistantPolicy,
    AssistantRole,
    AssistantSession,
    AssistantToolCall,
    AssistantTurn,
    AssistantTurnStatus,
)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


class AssistantSessionStore:
    """Disk-backed assistant store using JSON state.

    SQLite is avoided here because some execution environments deny SQLite writes.
    The API remains stable so callers do not depend on the persistence backend.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {
            "sessions": {},
            "messages": {},
            "turns": {},
            "tool_calls": {},
            "confirmations": {},
            "compactions": {},
        }
        self._load()

    def _load(self) -> None:
        with self._lock:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._db_path.exists():
                self._save()
                return
            try:
                payload = json.loads(self._db_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    for key in self._state.keys():
                        value = payload.get(key, {})
                        self._state[key] = value if isinstance(value, dict) else {}
            except Exception:
                # Preserve corrupted source for debugging and reset state.
                backup = self._db_path.with_suffix(self._db_path.suffix + ".corrupt")
                try:
                    backup.write_text(self._db_path.read_text(encoding="utf-8"), encoding="utf-8")
                except Exception:
                    pass
                self._state = {
                    "sessions": {},
                    "messages": {},
                    "turns": {},
                    "tool_calls": {},
                    "confirmations": {},
                    "compactions": {},
                }
                self._save()

    def _save(self) -> None:
        raw = json.dumps(self._state, indent=2, sort_keys=True)
        self._db_path.write_text(raw, encoding="utf-8")

    def create_session(self, title: str) -> AssistantSession:
        now = _utc_now()
        session_id = f"as_{uuid4().hex}"
        row = {
            "session_id": session_id,
            "title": title,
            "created_at_utc": now,
            "updated_at_utc": now,
            "last_message_at_utc": None,
            "policy": AssistantPolicy().model_dump(mode="json"),
        }
        with self._lock:
            self._state["sessions"][session_id] = row
            self._save()
        return AssistantSession.model_validate(row)

    def list_sessions(self, *, limit: int = 200) -> list[AssistantSession]:
        with self._lock:
            rows = list(self._state["sessions"].values())
        rows.sort(
            key=lambda item: str(item.get("last_message_at_utc") or item.get("created_at_utc") or ""),
            reverse=True,
        )
        return [AssistantSession.model_validate(item) for item in rows[: int(limit)]]

    def get_session(self, session_id: str) -> AssistantSession:
        with self._lock:
            row = self._state["sessions"].get(session_id)
        if row is None:
            raise KeyError(f"Assistant session not found: {session_id}")
        return AssistantSession.model_validate(row)

    def update_policy(self, session_id: str, policy: AssistantPolicy) -> AssistantPolicy:
        with self._lock:
            row = self._state["sessions"].get(session_id)
            if row is None:
                raise KeyError(f"Assistant session not found: {session_id}")
            row["policy"] = policy.model_dump(mode="json")
            row["updated_at_utc"] = _utc_now()
            self._save()
        return policy

    def add_message(
        self,
        *,
        session_id: str,
        role: AssistantRole,
        content: str,
        turn_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        outputs: list[dict[str, Any]] | None = None,
    ) -> AssistantMessage:
        metadata = metadata or {}
        outputs = outputs or []
        now = _utc_now()
        message_id = f"msg_{uuid4().hex}"
        row = {
            "message_id": message_id,
            "session_id": session_id,
            "role": role.value,
            "content": content,
            "created_at_utc": now,
            "turn_id": turn_id,
            "metadata": metadata,
            "outputs": list(outputs),
        }
        with self._lock:
            self._state["messages"][message_id] = row
            session = self._state["sessions"].get(session_id)
            if session is not None:
                session["updated_at_utc"] = now
                session["last_message_at_utc"] = now
            self._save()
        return AssistantMessage.model_validate(row)

    def list_messages(self, session_id: str, *, limit: int = 400) -> list[AssistantMessage]:
        with self._lock:
            rows = [item for item in self._state["messages"].values() if str(item.get("session_id")) == session_id]
        rows.sort(key=lambda item: str(item.get("created_at_utc", "")))
        rows = rows[: int(limit)]
        return [AssistantMessage.model_validate(item) for item in rows]

    def create_turn(
        self,
        *,
        session_id: str,
        user_message_id: str,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> AssistantTurn:
        now = _utc_now()
        turn_id = f"turn_{uuid4().hex}"
        row = {
            "turn_id": turn_id,
            "session_id": session_id,
            "user_message_id": user_message_id,
            "status": AssistantTurnStatus.QUEUED.value,
            "provider_id": provider_id,
            "model_id": model_id,
            "created_at_utc": now,
            "updated_at_utc": now,
            "error": None,
            "usage": {},
        }
        with self._lock:
            self._state["turns"][turn_id] = row
            self._save()
        return AssistantTurn.model_validate(row)

    def update_turn(
        self,
        turn_id: str,
        *,
        status: AssistantTurnStatus,
        error: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> AssistantTurn:
        with self._lock:
            row = self._state["turns"].get(turn_id)
            if row is None:
                raise KeyError(f"Assistant turn not found: {turn_id}")
            row["status"] = status.value
            row["updated_at_utc"] = _utc_now()
            row["error"] = error
            if usage is not None:
                row["usage"] = dict(usage)
            self._save()
            out = dict(row)
        return AssistantTurn.model_validate(out)

    def get_turn(self, turn_id: str) -> AssistantTurn:
        with self._lock:
            row = self._state["turns"].get(turn_id)
        if row is None:
            raise KeyError(f"Assistant turn not found: {turn_id}")
        return AssistantTurn.model_validate(row)

    def list_turns(self, session_id: str, *, limit: int = 20) -> list[AssistantTurn]:
        with self._lock:
            rows = [item for item in self._state["turns"].values() if str(item.get("session_id")) == session_id]
        rows.sort(key=lambda item: str(item.get("created_at_utc", "")), reverse=True)
        return [AssistantTurn.model_validate(item) for item in rows[: int(limit)]]

    def create_tool_call(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        status: str,
        action_type: AssistantConfirmationActionType | None = None,
    ) -> AssistantToolCall:
        now = _utc_now()
        tool_call_id = f"call_{uuid4().hex}"
        row = {
            "tool_call_id": tool_call_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "tool_name": tool_name,
            "arguments": dict(arguments),
            "status": status,
            "created_at_utc": now,
            "completed_at_utc": None,
            "result": {},
            "error": None,
            "action_type": action_type.value if action_type is not None else None,
            "outputs": [],
        }
        with self._lock:
            self._state["tool_calls"][tool_call_id] = row
            self._save()
        return self._coerce_tool_call(row)

    def complete_tool_call(
        self,
        tool_call_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        outputs: list[dict[str, Any]] | None = None,
    ) -> AssistantToolCall:
        outputs = outputs or []
        with self._lock:
            row = self._state["tool_calls"].get(tool_call_id)
            if row is None:
                raise KeyError(f"Assistant tool call not found: {tool_call_id}")
            row["status"] = status
            row["completed_at_utc"] = _utc_now()
            row["result"] = dict(result or {})
            row["error"] = error
            row["outputs"] = list(outputs)
            self._save()
            out = dict(row)
        return self._coerce_tool_call(out)

    def list_turn_tool_calls(self, turn_id: str) -> list[AssistantToolCall]:
        with self._lock:
            rows = [item for item in self._state["tool_calls"].values() if str(item.get("turn_id")) == turn_id]
        rows.sort(key=lambda item: str(item.get("created_at_utc", "")))
        return [self._coerce_tool_call(item) for item in rows]

    def get_tool_call(self, tool_call_id: str) -> AssistantToolCall:
        with self._lock:
            row = self._state["tool_calls"].get(tool_call_id)
        if row is None:
            raise KeyError(f"Assistant tool call not found: {tool_call_id}")
        return self._coerce_tool_call(row)

    def create_confirmation(
        self,
        *,
        session_id: str,
        turn_id: str,
        action_type: AssistantConfirmationActionType,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> AssistantConfirmation:
        confirmation_id = f"cnf_{uuid4().hex}"
        row = {
            "confirmation_id": confirmation_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "action_type": action_type.value,
            "tool_name": tool_name,
            "arguments": dict(arguments),
            "status": "pending",
            "requested_at_utc": _utc_now(),
            "resolved_at_utc": None,
            "resolution": None,
        }
        with self._lock:
            self._state["confirmations"][confirmation_id] = row
            self._save()
        return self._coerce_confirmation(row)

    def resolve_confirmation(
        self,
        confirmation_id: str,
        *,
        decision: AssistantConfirmationDecision,
        status: str,
    ) -> AssistantConfirmation:
        with self._lock:
            row = self._state["confirmations"].get(confirmation_id)
            if row is None:
                raise KeyError(f"Assistant confirmation not found: {confirmation_id}")
            row["status"] = status
            row["resolved_at_utc"] = _utc_now()
            row["resolution"] = decision.value
            self._save()
            out = dict(row)
        return self._coerce_confirmation(out)

    def get_confirmation(self, confirmation_id: str) -> AssistantConfirmation:
        with self._lock:
            row = self._state["confirmations"].get(confirmation_id)
        if row is None:
            raise KeyError(f"Assistant confirmation not found: {confirmation_id}")
        return self._coerce_confirmation(row)

    def list_session_pending_confirmations(self, session_id: str) -> list[AssistantConfirmation]:
        with self._lock:
            rows = [
                dict(item)
                for item in self._state["confirmations"].values()
                if str(item.get("session_id", "")) == session_id and str(item.get("status", "")) == "pending"
            ]
        rows.sort(key=lambda item: str(item.get("requested_at_utc", "")), reverse=True)
        return [self._coerce_confirmation(item) for item in rows]

    def record_compaction(
        self,
        *,
        session_id: str,
        summary_message_id: str,
        compacted_count: int,
    ) -> None:
        compaction_id = f"cmp_{uuid4().hex}"
        row = {
            "compaction_id": compaction_id,
            "session_id": session_id,
            "summary_message_id": summary_message_id,
            "compacted_count": int(compacted_count),
            "created_at_utc": _utc_now(),
        }
        with self._lock:
            self._state["compactions"][compaction_id] = row
            self._save()

    @staticmethod
    def _coerce_tool_call(row: dict[str, Any]) -> AssistantToolCall:
        payload = dict(row)
        action = payload.get("action_type")
        payload["action_type"] = (
            AssistantConfirmationActionType(str(action)) if action is not None else None
        )
        payload["outputs"] = list(payload.get("outputs") or [])
        return AssistantToolCall.model_validate(payload)

    @staticmethod
    def _coerce_confirmation(row: dict[str, Any]) -> AssistantConfirmation:
        payload = dict(row)
        resolution = payload.get("resolution")
        payload["resolution"] = (
            AssistantConfirmationDecision(str(resolution)) if resolution is not None else None
        )
        payload["action_type"] = AssistantConfirmationActionType(str(payload.get("action_type")))
        return AssistantConfirmation.model_validate(payload)
