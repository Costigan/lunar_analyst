from __future__ import annotations

import threading
import time

from backend.api.dependencies import BoundedEventBuffer, BoundedRunInfoMap


def test_bounded_event_buffer_retains_recent_items_and_supports_cursor_reads() -> None:
    events = BoundedEventBuffer(max_items=3)
    for idx in range(5):
        events.append({"idx": idx})

    assert len(events) == 3
    assert [item["idx"] for item in events] == [2, 3, 4]

    cursor, payloads = events.read_since(0)
    assert cursor == 5
    assert [item["idx"] for item in payloads] == [2, 3, 4]

    events.append({"idx": 5})
    next_cursor, next_payloads = events.read_since(cursor)
    assert next_cursor == 6
    assert [item["idx"] for item in next_payloads] == [5]


def test_bounded_run_info_map_evicts_oldest_entries() -> None:
    run_info = BoundedRunInfoMap(max_items=2)
    run_info["job-a"] = {"status": "queued"}
    run_info["job-b"] = {"status": "running"}
    run_info["job-c"] = {"status": "completed"}

    assert list(run_info.keys()) == ["job-b", "job-c"]

    run_info["job-b"] = {"status": "completed"}
    assert list(run_info.keys()) == ["job-c", "job-b"]


def test_bounded_event_buffer_wait_for_events_blocks_until_append() -> None:
    events = BoundedEventBuffer(max_items=4)
    result: dict[str, object] = {}

    def _waiter() -> None:
        cursor, payloads = events.wait_for_events(0, 1.0)
        result["cursor"] = cursor
        result["payloads"] = payloads

    thread = threading.Thread(target=_waiter, daemon=True)
    thread.start()
    time.sleep(0.05)
    events.append({"idx": 1})
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert result["cursor"] == 1
    assert result["payloads"] == [{"idx": 1}]
