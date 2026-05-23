from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
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


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _json_dumps_list(payload: list[Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _json_loads(raw: str | None, *, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    if raw is None:
        return dict(fallback or {})
    try:
        parsed = json.loads(raw)
    except Exception:
        return dict(fallback or {})
    if not isinstance(parsed, dict):
        return dict(fallback or {})
    return parsed


def _json_loads_list(raw: str | None, *, fallback: list[Any] | None = None) -> list[Any]:
    if raw is None:
        return list(fallback or [])
    try:
        parsed = json.loads(raw)
    except Exception:
        return list(fallback or [])
    if not isinstance(parsed, list):
        return list(fallback or [])
    return parsed


class AssistantSessionStore:
    """SQLite-backed assistant persistence with optional legacy JSON import."""

    def __init__(self, db_path: Path, *, legacy_json_path: Path | None = None) -> None:
        self._db_path = Path(db_path).expanduser().resolve()
        self._legacy_json_path = legacy_json_path.expanduser().resolve() if legacy_json_path is not None else None
        self._lock = threading.RLock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path),
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def __del__(self) -> None:  # pragma: no cover - best effort cleanup
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[None]:
        self._conn.execute("BEGIN")
        try:
            yield
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")

    def _initialize(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(
                """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    last_message_at_utc TEXT,
    policy_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    turn_id TEXT,
    metadata_json TEXT NOT NULL,
    outputs_json TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_message_id TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_id TEXT,
    model_id TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    error TEXT,
    usage_json TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    result_json TEXT NOT NULL,
    error TEXT,
    action_type TEXT,
    outputs_json TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY (turn_id) REFERENCES turns(turn_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS confirmations (
    confirmation_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at_utc TEXT NOT NULL,
    resolved_at_utc TEXT,
    resolution TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY (turn_id) REFERENCES turns(turn_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS compactions (
    compaction_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    summary_message_id TEXT NOT NULL,
    compacted_count INTEGER NOT NULL,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meta (
    meta_key TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_last_message ON sessions(last_message_at_utc DESC, created_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages(session_id, created_at_utc);
CREATE INDEX IF NOT EXISTS idx_turns_session_created ON turns(session_id, created_at_utc);
CREATE INDEX IF NOT EXISTS idx_tool_calls_turn_created ON tool_calls(turn_id, created_at_utc);
CREATE INDEX IF NOT EXISTS idx_compactions_session_created ON compactions(session_id, created_at_utc);
                """
            )
            self._ensure_column("messages", "outputs_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column("tool_calls", "outputs_json", "TEXT NOT NULL DEFAULT '[]'")
            self._migrate_legacy_json_if_needed()

    def _ensure_column(self, table_name: str, column_name: str, column_ddl: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        known = {str(row[1]) for row in rows}
        if column_name in known:
            return
        self._conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_ddl}")

    def _resolve_legacy_json_path(self) -> Path | None:
        if self._legacy_json_path is not None:
            return self._legacy_json_path
        candidate = self._db_path.with_suffix(".json")
        return candidate if candidate.exists() else None

    def _has_existing_rows(self) -> bool:
        for table_name in ("sessions", "messages", "turns", "tool_calls", "confirmations", "compactions"):
            row = self._conn.execute(f"SELECT 1 FROM {table_name} LIMIT 1").fetchone()
            if row is not None:
                return True
        return False

    def _migrate_legacy_json_if_needed(self) -> None:
        if self._has_existing_rows():
            return
        legacy_path = self._resolve_legacy_json_path()
        if legacy_path is None or (not legacy_path.exists()):
            return
        try:
            payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return

        sessions = payload.get("sessions", {})
        messages = payload.get("messages", {})
        turns = payload.get("turns", {})
        tool_calls = payload.get("tool_calls", {})
        confirmations = payload.get("confirmations", {})
        compactions = payload.get("compactions", {})
        if not all(isinstance(item, dict) for item in (sessions, messages, turns, tool_calls, confirmations, compactions)):
            return

        now = _utc_now()
        with self._tx():
            for row in sessions.values():
                if not isinstance(row, dict):
                    continue
                session_id = str(row.get("session_id", "")).strip() or f"as_{uuid4().hex}"
                policy = row.get("policy")
                policy_payload = policy if isinstance(policy, dict) else AssistantPolicy().model_dump(mode="json")
                self._conn.execute(
                    """
INSERT OR REPLACE INTO sessions (
    session_id, title, created_at_utc, updated_at_utc, last_message_at_utc, policy_json
) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        str(row.get("title", "Untitled Session")),
                        str(row.get("created_at_utc") or now),
                        str(row.get("updated_at_utc") or now),
                        str(row.get("last_message_at_utc")) if row.get("last_message_at_utc") is not None else None,
                        _json_dumps(policy_payload),
                    ),
                )

            for row in messages.values():
                if not isinstance(row, dict):
                    continue
                metadata = row.get("metadata")
                outputs = row.get("outputs")
                self._conn.execute(
                    """
INSERT OR REPLACE INTO messages (
    message_id, session_id, role, content, created_at_utc, turn_id, metadata_json, outputs_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row.get("message_id", "")).strip() or f"msg_{uuid4().hex}",
                        str(row.get("session_id", "")).strip(),
                        str(row.get("role", AssistantRole.USER.value)),
                        str(row.get("content", "")),
                        str(row.get("created_at_utc") or now),
                        str(row.get("turn_id")) if row.get("turn_id") is not None else None,
                        _json_dumps(metadata if isinstance(metadata, dict) else {}),
                        _json_dumps_list(outputs if isinstance(outputs, list) else []),
                    ),
                )

            for row in turns.values():
                if not isinstance(row, dict):
                    continue
                usage = row.get("usage")
                self._conn.execute(
                    """
INSERT OR REPLACE INTO turns (
    turn_id, session_id, user_message_id, status, provider_id, model_id, created_at_utc, updated_at_utc, error, usage_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row.get("turn_id", "")).strip() or f"turn_{uuid4().hex}",
                        str(row.get("session_id", "")).strip(),
                        str(row.get("user_message_id", "")).strip(),
                        str(row.get("status", AssistantTurnStatus.QUEUED.value)),
                        str(row.get("provider_id")) if row.get("provider_id") is not None else None,
                        str(row.get("model_id")) if row.get("model_id") is not None else None,
                        str(row.get("created_at_utc") or now),
                        str(row.get("updated_at_utc") or now),
                        str(row.get("error")) if row.get("error") is not None else None,
                        _json_dumps(usage if isinstance(usage, dict) else {}),
                    ),
                )

            for row in tool_calls.values():
                if not isinstance(row, dict):
                    continue
                arguments = row.get("arguments")
                result = row.get("result")
                outputs = row.get("outputs")
                self._conn.execute(
                    """
INSERT OR REPLACE INTO tool_calls (
    tool_call_id, session_id, turn_id, tool_name, arguments_json, status, created_at_utc,
    completed_at_utc, result_json, error, action_type, outputs_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row.get("tool_call_id", "")).strip() or f"call_{uuid4().hex}",
                        str(row.get("session_id", "")).strip(),
                        str(row.get("turn_id", "")).strip(),
                        str(row.get("tool_name", "")),
                        _json_dumps(arguments if isinstance(arguments, dict) else {}),
                        str(row.get("status", "started")),
                        str(row.get("created_at_utc") or now),
                        str(row.get("completed_at_utc")) if row.get("completed_at_utc") is not None else None,
                        _json_dumps(result if isinstance(result, dict) else {}),
                        str(row.get("error")) if row.get("error") is not None else None,
                        str(row.get("action_type")) if row.get("action_type") is not None else None,
                        _json_dumps_list(outputs if isinstance(outputs, list) else []),
                    ),
                )

            for row in confirmations.values():
                if not isinstance(row, dict):
                    continue
                arguments = row.get("arguments")
                self._conn.execute(
                    """
INSERT OR REPLACE INTO confirmations (
    confirmation_id, session_id, turn_id, action_type, tool_name, arguments_json,
    status, requested_at_utc, resolved_at_utc, resolution
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row.get("confirmation_id", "")).strip() or f"cnf_{uuid4().hex}",
                        str(row.get("session_id", "")).strip(),
                        str(row.get("turn_id", "")).strip(),
                        str(row.get("action_type", AssistantConfirmationActionType.LAUNCH_JOB.value)),
                        str(row.get("tool_name", "")),
                        _json_dumps(arguments if isinstance(arguments, dict) else {}),
                        str(row.get("status", "pending")),
                        str(row.get("requested_at_utc") or now),
                        str(row.get("resolved_at_utc")) if row.get("resolved_at_utc") is not None else None,
                        str(row.get("resolution")) if row.get("resolution") is not None else None,
                    ),
                )

            for row in compactions.values():
                if not isinstance(row, dict):
                    continue
                self._conn.execute(
                    """
INSERT OR REPLACE INTO compactions (
    compaction_id, session_id, summary_message_id, compacted_count, created_at_utc
) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(row.get("compaction_id", "")).strip() or f"cmp_{uuid4().hex}",
                        str(row.get("session_id", "")).strip(),
                        str(row.get("summary_message_id", "")).strip(),
                        int(row.get("compacted_count", 0) or 0),
                        str(row.get("created_at_utc") or now),
                    ),
                )

            self._conn.execute(
                "INSERT OR REPLACE INTO meta (meta_key, meta_value) VALUES (?, ?)",
                ("migrated_from_json", str(legacy_path)),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (meta_key, meta_value) VALUES (?, ?)",
                ("migrated_at_utc", now),
            )

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
            with self._tx():
                self._conn.execute(
                    """
INSERT INTO sessions (session_id, title, created_at_utc, updated_at_utc, last_message_at_utc, policy_json)
VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["session_id"],
                        row["title"],
                        row["created_at_utc"],
                        row["updated_at_utc"],
                        row["last_message_at_utc"],
                        _json_dumps(row["policy"]),
                    ),
                )
        return AssistantSession.model_validate(row)

    def list_sessions(self, *, limit: int = 200) -> list[AssistantSession]:
        with self._lock:
            rows = self._conn.execute(
                """
SELECT session_id, title, created_at_utc, updated_at_utc, last_message_at_utc, policy_json
FROM sessions
ORDER BY COALESCE(last_message_at_utc, created_at_utc) DESC
LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [self._coerce_session(row) for row in rows]

    def get_session(self, session_id: str) -> AssistantSession:
        with self._lock:
            row = self._conn.execute(
                """
SELECT session_id, title, created_at_utc, updated_at_utc, last_message_at_utc, policy_json
FROM sessions
WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Assistant session not found: {session_id}")
        return self._coerce_session(row)

    def update_policy(self, session_id: str, policy: AssistantPolicy) -> AssistantPolicy:
        now = _utc_now()
        with self._lock:
            with self._tx():
                current = self._conn.execute(
                    "SELECT session_id FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if current is None:
                    raise KeyError(f"Assistant session not found: {session_id}")
                self._conn.execute(
                    "UPDATE sessions SET policy_json = ?, updated_at_utc = ? WHERE session_id = ?",
                    (_json_dumps(policy.model_dump(mode="json")), now, session_id),
                )
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
            "metadata": dict(metadata),
            "outputs": list(outputs),
        }
        with self._lock:
            with self._tx():
                self._conn.execute(
                    """
INSERT INTO messages (message_id, session_id, role, content, created_at_utc, turn_id, metadata_json, outputs_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["message_id"],
                        row["session_id"],
                        row["role"],
                        row["content"],
                        row["created_at_utc"],
                        row["turn_id"],
                        _json_dumps(row["metadata"]),
                        _json_dumps_list(row["outputs"]),
                    ),
                )
                self._conn.execute(
                    """
UPDATE sessions
SET updated_at_utc = ?, last_message_at_utc = ?
WHERE session_id = ?
                    """,
                    (now, now, session_id),
                )
        return AssistantMessage.model_validate(row)

    def list_messages(self, session_id: str, *, limit: int = 400) -> list[AssistantMessage]:
        with self._lock:
            rows = self._conn.execute(
                """
SELECT message_id, session_id, role, content, created_at_utc, turn_id, metadata_json, outputs_json
FROM messages
WHERE session_id = ?
ORDER BY created_at_utc ASC
LIMIT ?
                """,
                (session_id, int(limit)),
            ).fetchall()
        return [self._coerce_message(row) for row in rows]

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
            with self._tx():
                self._conn.execute(
                    """
INSERT INTO turns (
    turn_id, session_id, user_message_id, status, provider_id, model_id,
    created_at_utc, updated_at_utc, error, usage_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["turn_id"],
                        row["session_id"],
                        row["user_message_id"],
                        row["status"],
                        row["provider_id"],
                        row["model_id"],
                        row["created_at_utc"],
                        row["updated_at_utc"],
                        row["error"],
                        _json_dumps(row["usage"]),
                    ),
                )
        return AssistantTurn.model_validate(row)

    def update_turn(
        self,
        turn_id: str,
        *,
        status: AssistantTurnStatus,
        error: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> AssistantTurn:
        now = _utc_now()
        with self._lock:
            existing = self._conn.execute(
                "SELECT usage_json FROM turns WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(f"Assistant turn not found: {turn_id}")
            usage_json = existing["usage_json"] if usage is None else _json_dumps(dict(usage))
            with self._tx():
                self._conn.execute(
                    """
UPDATE turns
SET status = ?, updated_at_utc = ?, error = ?, usage_json = ?
WHERE turn_id = ?
                    """,
                    (status.value, now, error, usage_json, turn_id),
                )
                row = self._conn.execute(
                    """
SELECT turn_id, session_id, user_message_id, status, provider_id, model_id,
       created_at_utc, updated_at_utc, error, usage_json
FROM turns
WHERE turn_id = ?
                    """,
                    (turn_id,),
                ).fetchone()
        if row is None:
            raise KeyError(f"Assistant turn not found: {turn_id}")
        return self._coerce_turn(row)

    def get_turn(self, turn_id: str) -> AssistantTurn:
        with self._lock:
            row = self._conn.execute(
                """
SELECT turn_id, session_id, user_message_id, status, provider_id, model_id,
       created_at_utc, updated_at_utc, error, usage_json
FROM turns
WHERE turn_id = ?
                """,
                (turn_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Assistant turn not found: {turn_id}")
        return self._coerce_turn(row)

    def list_turns(self, session_id: str, *, limit: int = 20) -> list[AssistantTurn]:
        with self._lock:
            rows = self._conn.execute(
                """
SELECT turn_id, session_id, user_message_id, status, provider_id, model_id,
       created_at_utc, updated_at_utc, error, usage_json
FROM turns
WHERE session_id = ?
ORDER BY created_at_utc DESC
LIMIT ?
                """,
                (session_id, int(limit)),
            ).fetchall()
        return [self._coerce_turn(row) for row in rows]

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
            with self._tx():
                self._conn.execute(
                    """
INSERT INTO tool_calls (
    tool_call_id, session_id, turn_id, tool_name, arguments_json, status,
    created_at_utc, completed_at_utc, result_json, error, action_type, outputs_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["tool_call_id"],
                        row["session_id"],
                        row["turn_id"],
                        row["tool_name"],
                        _json_dumps(row["arguments"]),
                        row["status"],
                        row["created_at_utc"],
                        row["completed_at_utc"],
                        _json_dumps(row["result"]),
                        row["error"],
                        row["action_type"],
                        _json_dumps_list(row["outputs"]),
                    ),
                )
        return self._coerce_tool_call_from_values(row)

    def complete_tool_call(
        self,
        tool_call_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        outputs: list[dict[str, Any]] | None = None,
    ) -> AssistantToolCall:
        completed_at = _utc_now()
        outputs = outputs or []
        with self._lock:
            row = self._conn.execute(
                "SELECT tool_call_id FROM tool_calls WHERE tool_call_id = ?",
                (tool_call_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Assistant tool call not found: {tool_call_id}")
            with self._tx():
                self._conn.execute(
                    """
UPDATE tool_calls
SET status = ?, completed_at_utc = ?, result_json = ?, error = ?, outputs_json = ?
WHERE tool_call_id = ?
                    """,
                    (status, completed_at, _json_dumps(dict(result or {})), error, _json_dumps_list(outputs), tool_call_id),
                )
                updated = self._conn.execute(
                    """
SELECT tool_call_id, session_id, turn_id, tool_name, arguments_json, status,
       created_at_utc, completed_at_utc, result_json, error, action_type, outputs_json
FROM tool_calls
WHERE tool_call_id = ?
                    """,
                    (tool_call_id,),
                ).fetchone()
        if updated is None:
            raise KeyError(f"Assistant tool call not found: {tool_call_id}")
        return self._coerce_tool_call(updated)

    def list_turn_tool_calls(self, turn_id: str) -> list[AssistantToolCall]:
        with self._lock:
            rows = self._conn.execute(
                """
SELECT tool_call_id, session_id, turn_id, tool_name, arguments_json, status,
       created_at_utc, completed_at_utc, result_json, error, action_type, outputs_json
FROM tool_calls
WHERE turn_id = ?
ORDER BY created_at_utc ASC
                """,
                (turn_id,),
            ).fetchall()
        return [self._coerce_tool_call(row) for row in rows]

    def get_tool_call(self, tool_call_id: str) -> AssistantToolCall:
        with self._lock:
            row = self._conn.execute(
                """
SELECT tool_call_id, session_id, turn_id, tool_name, arguments_json, status,
       created_at_utc, completed_at_utc, result_json, error, action_type, outputs_json
FROM tool_calls
WHERE tool_call_id = ?
                """,
                (tool_call_id,),
            ).fetchone()
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
            with self._tx():
                self._conn.execute(
                    """
INSERT INTO confirmations (
    confirmation_id, session_id, turn_id, action_type, tool_name,
    arguments_json, status, requested_at_utc, resolved_at_utc, resolution
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["confirmation_id"],
                        row["session_id"],
                        row["turn_id"],
                        row["action_type"],
                        row["tool_name"],
                        _json_dumps(row["arguments"]),
                        row["status"],
                        row["requested_at_utc"],
                        row["resolved_at_utc"],
                        row["resolution"],
                    ),
                )
        return self._coerce_confirmation_from_values(row)

    def resolve_confirmation(
        self,
        confirmation_id: str,
        *,
        decision: AssistantConfirmationDecision,
        status: str,
    ) -> AssistantConfirmation:
        resolved_at = _utc_now()
        with self._lock:
            row = self._conn.execute(
                "SELECT confirmation_id FROM confirmations WHERE confirmation_id = ?",
                (confirmation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Assistant confirmation not found: {confirmation_id}")
            with self._tx():
                self._conn.execute(
                    """
UPDATE confirmations
SET status = ?, resolved_at_utc = ?, resolution = ?
WHERE confirmation_id = ?
                    """,
                    (status, resolved_at, decision.value, confirmation_id),
                )
                updated = self._conn.execute(
                    """
SELECT confirmation_id, session_id, turn_id, action_type, tool_name,
       arguments_json, status, requested_at_utc, resolved_at_utc, resolution
FROM confirmations
WHERE confirmation_id = ?
                    """,
                    (confirmation_id,),
                ).fetchone()
        if updated is None:
            raise KeyError(f"Assistant confirmation not found: {confirmation_id}")
        return self._coerce_confirmation(updated)

    def get_confirmation(self, confirmation_id: str) -> AssistantConfirmation:
        with self._lock:
            row = self._conn.execute(
                """
SELECT confirmation_id, session_id, turn_id, action_type, tool_name,
       arguments_json, status, requested_at_utc, resolved_at_utc, resolution
FROM confirmations
WHERE confirmation_id = ?
                """,
                (confirmation_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Assistant confirmation not found: {confirmation_id}")
        return self._coerce_confirmation(row)

    def list_session_pending_confirmations(self, session_id: str) -> list[AssistantConfirmation]:
        with self._lock:
            rows = self._conn.execute(
                """
SELECT confirmation_id, session_id, turn_id, action_type, tool_name,
       arguments_json, status, requested_at_utc, resolved_at_utc, resolution
FROM confirmations
WHERE session_id = ? AND status = 'pending'
ORDER BY requested_at_utc DESC
                """,
                (session_id,),
            ).fetchall()
        return [self._coerce_confirmation(row) for row in rows]

    def record_compaction(
        self,
        *,
        session_id: str,
        summary_message_id: str,
        compacted_count: int,
    ) -> None:
        compaction_id = f"cmp_{uuid4().hex}"
        with self._lock:
            with self._tx():
                self._conn.execute(
                    """
INSERT INTO compactions (
    compaction_id, session_id, summary_message_id, compacted_count, created_at_utc
) VALUES (?, ?, ?, ?, ?)
                    """,
                    (compaction_id, session_id, summary_message_id, int(compacted_count), _utc_now()),
                )

    @staticmethod
    def _coerce_session(row: sqlite3.Row) -> AssistantSession:
        payload = {
            "session_id": str(row["session_id"]),
            "title": str(row["title"]),
            "created_at_utc": str(row["created_at_utc"]),
            "updated_at_utc": str(row["updated_at_utc"]),
            "last_message_at_utc": str(row["last_message_at_utc"]) if row["last_message_at_utc"] is not None else None,
            "policy": _json_loads(str(row["policy_json"]) if row["policy_json"] is not None else None, fallback={}),
        }
        return AssistantSession.model_validate(payload)

    @staticmethod
    def _coerce_message(row: sqlite3.Row) -> AssistantMessage:
        payload = {
            "message_id": str(row["message_id"]),
            "session_id": str(row["session_id"]),
            "role": str(row["role"]),
            "content": str(row["content"]),
            "created_at_utc": str(row["created_at_utc"]),
            "turn_id": str(row["turn_id"]) if row["turn_id"] is not None else None,
            "metadata": _json_loads(str(row["metadata_json"]) if row["metadata_json"] is not None else None, fallback={}),
            "outputs": _json_loads_list(str(row["outputs_json"]) if row["outputs_json"] is not None else None, fallback=[]),
        }
        return AssistantMessage.model_validate(payload)

    @staticmethod
    def _coerce_turn(row: sqlite3.Row) -> AssistantTurn:
        payload = {
            "turn_id": str(row["turn_id"]),
            "session_id": str(row["session_id"]),
            "user_message_id": str(row["user_message_id"]),
            "status": str(row["status"]),
            "provider_id": str(row["provider_id"]) if row["provider_id"] is not None else None,
            "model_id": str(row["model_id"]) if row["model_id"] is not None else None,
            "created_at_utc": str(row["created_at_utc"]),
            "updated_at_utc": str(row["updated_at_utc"]),
            "error": str(row["error"]) if row["error"] is not None else None,
            "usage": _json_loads(str(row["usage_json"]) if row["usage_json"] is not None else None, fallback={}),
        }
        return AssistantTurn.model_validate(payload)

    @staticmethod
    def _coerce_tool_call(row: sqlite3.Row) -> AssistantToolCall:
        payload = {
            "tool_call_id": str(row["tool_call_id"]),
            "session_id": str(row["session_id"]),
            "turn_id": str(row["turn_id"]),
            "tool_name": str(row["tool_name"]),
            "arguments": _json_loads(str(row["arguments_json"]) if row["arguments_json"] is not None else None, fallback={}),
            "status": str(row["status"]),
            "created_at_utc": str(row["created_at_utc"]),
            "completed_at_utc": str(row["completed_at_utc"]) if row["completed_at_utc"] is not None else None,
            "result": _json_loads(str(row["result_json"]) if row["result_json"] is not None else None, fallback={}),
            "error": str(row["error"]) if row["error"] is not None else None,
            "action_type": (
                AssistantConfirmationActionType(str(row["action_type"])) if row["action_type"] is not None else None
            ),
            "outputs": _json_loads_list(str(row["outputs_json"]) if row["outputs_json"] is not None else None, fallback=[]),
        }
        return AssistantToolCall.model_validate(payload)

    @staticmethod
    def _coerce_tool_call_from_values(row: dict[str, Any]) -> AssistantToolCall:
        payload = dict(row)
        action = payload.get("action_type")
        payload["action_type"] = AssistantConfirmationActionType(str(action)) if action is not None else None
        payload["outputs"] = list(payload.get("outputs") or [])
        return AssistantToolCall.model_validate(payload)

    @staticmethod
    def _coerce_confirmation(row: sqlite3.Row) -> AssistantConfirmation:
        payload = {
            "confirmation_id": str(row["confirmation_id"]),
            "session_id": str(row["session_id"]),
            "turn_id": str(row["turn_id"]),
            "action_type": AssistantConfirmationActionType(str(row["action_type"])),
            "tool_name": str(row["tool_name"]),
            "arguments": _json_loads(str(row["arguments_json"]) if row["arguments_json"] is not None else None, fallback={}),
            "status": str(row["status"]),
            "requested_at_utc": str(row["requested_at_utc"]),
            "resolved_at_utc": str(row["resolved_at_utc"]) if row["resolved_at_utc"] is not None else None,
            "resolution": AssistantConfirmationDecision(str(row["resolution"])) if row["resolution"] is not None else None,
        }
        return AssistantConfirmation.model_validate(payload)

    @staticmethod
    def _coerce_confirmation_from_values(row: dict[str, Any]) -> AssistantConfirmation:
        payload = dict(row)
        payload["action_type"] = AssistantConfirmationActionType(str(payload["action_type"]))
        resolution = payload.get("resolution")
        payload["resolution"] = AssistantConfirmationDecision(str(resolution)) if resolution is not None else None
        return AssistantConfirmation.model_validate(payload)
