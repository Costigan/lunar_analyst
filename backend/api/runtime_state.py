from __future__ import annotations

import sqlite3
import threading
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from backend.api.dependencies_constants import (
    DEFAULT_MAX_ASSISTANT_WS_EVENTS,
    DEFAULT_MAX_NOTEBOOK_RUN_INFO,
    DEFAULT_MAX_WS_EVENTS,
)


class BoundedEventBuffer:
    """Append-only event buffer with capped retention and cursor-based reads."""

    def __init__(self, *, max_items: int) -> None:
        self._max_items = max(1, int(max_items))
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._items: list[dict[str, Any]] = []
        self._base_cursor = 0
        self._next_cursor = 0

    def append(self, payload: dict[str, Any]) -> None:
        with self._condition:
            if len(self._items) >= self._max_items:
                self._items.pop(0)
                self._base_cursor += 1
            self._items.append(payload)
            self._next_cursor += 1
            self._condition.notify_all()

    def read_since(self, cursor: int) -> tuple[int, list[dict[str, Any]]]:
        with self._lock:
            local_cursor = self._normalize_cursor_locked(int(cursor))
            start = local_cursor - self._base_cursor
            return self._next_cursor, list(self._items[start:])

    def wait_for_events(self, cursor: int, timeout_seconds: float = 30.0) -> tuple[int, list[dict[str, Any]]]:
        timeout = max(0.0, float(timeout_seconds))
        with self._condition:
            local_cursor = self._normalize_cursor_locked(int(cursor))
            if local_cursor == self._next_cursor:
                self._condition.wait(timeout=timeout)
                local_cursor = self._normalize_cursor_locked(local_cursor)
            start = local_cursor - self._base_cursor
            return self._next_cursor, list(self._items[start:])

    def _normalize_cursor_locked(self, cursor: int) -> int:
        local_cursor = int(cursor)
        if local_cursor < self._base_cursor:
            return self._base_cursor
        if local_cursor > self._next_cursor:
            return self._next_cursor
        return local_cursor

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def __iter__(self):
        with self._lock:
            snapshot = list(self._items)
        return iter(snapshot)

    def __getitem__(self, index):
        with self._lock:
            return self._items[index]


class BoundedRunInfoMap(OrderedDict[str, dict[str, Any]]):
    def __init__(self, *, max_items: int) -> None:
        super().__init__()
        self._max_items = max(1, int(max_items))

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        if key in self:
            super().__delitem__(key)
        super().__setitem__(key, value)
        while len(self) > self._max_items:
            self.popitem(last=False)


def new_ws_event_buffer() -> BoundedEventBuffer:
    return BoundedEventBuffer(max_items=DEFAULT_MAX_WS_EVENTS)


def new_assistant_ws_event_buffer() -> BoundedEventBuffer:
    return BoundedEventBuffer(max_items=DEFAULT_MAX_ASSISTANT_WS_EVENTS)


def new_notebook_run_info_map() -> BoundedRunInfoMap:
    return BoundedRunInfoMap(max_items=DEFAULT_MAX_NOTEBOOK_RUN_INFO)


@contextmanager
def connect_sqlite(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()
