from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class McpSseSession:
    session_id: str
    created_at_utc: str
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=200))


class McpSseSessionManager:
    def __init__(self, *, max_sessions: int = 256) -> None:
        self._max_sessions = int(max(1, max_sessions))
        self._sessions: dict[str, McpSseSession] = {}
        self._session_order: list[str] = []
        self._lock = threading.RLock()

    def create(self) -> McpSseSession:
        session_id = f"mcp_sse_{uuid4().hex}"
        session = McpSseSession(session_id=session_id, created_at_utc=_utc_now())
        with self._lock:
            self._sessions[session_id] = session
            self._session_order.append(session_id)
            self._evict_if_needed()
        return session

    def get(self, session_id: str) -> McpSseSession | None:
        sid = str(session_id).strip()
        if not sid:
            return None
        with self._lock:
            return self._sessions.get(sid)

    def remove(self, session_id: str) -> None:
        sid = str(session_id).strip()
        if not sid:
            return
        with self._lock:
            self._sessions.pop(sid, None)
            self._session_order = [item for item in self._session_order if item != sid]

    def publish(self, session_id: str, payload: dict[str, Any]) -> bool:
        session = self.get(session_id)
        if session is None:
            return False
        queue = session.queue
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(dict(payload))
        return True

    def _evict_if_needed(self) -> None:
        while len(self._sessions) > self._max_sessions and self._session_order:
            oldest = self._session_order.pop(0)
            self._sessions.pop(oldest, None)


def encode_sse_event(*, event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=True, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def encode_sse_keepalive() -> str:
    return ": ping\n\n"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
