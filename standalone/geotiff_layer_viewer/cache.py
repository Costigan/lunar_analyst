from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class CacheEntry:
    value: object
    size_bytes: int


class LruCache:
    def __init__(self, max_items: int = 256, max_bytes: int = 1_500_000_000) -> None:
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._bytes = 0

    @property
    def size_bytes(self) -> int:
        return self._bytes

    def get(self, key: str) -> object | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        return entry.value

    def put(self, key: str, value: object, size_bytes: int) -> None:
        old = self._entries.pop(key, None)
        if old is not None:
            self._bytes -= old.size_bytes

        self._entries[key] = CacheEntry(value=value, size_bytes=size_bytes)
        self._bytes += size_bytes
        self._entries.move_to_end(key)
        self._evict_if_needed()

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0

    def _evict_if_needed(self) -> None:
        while self._entries and (len(self._entries) > self.max_items or self._bytes > self.max_bytes):
            _, removed = self._entries.popitem(last=False)
            self._bytes -= removed.size_bytes
