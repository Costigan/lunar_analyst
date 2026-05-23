from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ThreadSnapshot:
    non_daemon_names: tuple[str, ...]


def snapshot_non_daemon_threads() -> ThreadSnapshot:
    names = sorted(
        thread.name
        for thread in threading.enumerate()
        if thread.is_alive() and not thread.daemon and thread is not threading.main_thread()
    )
    return ThreadSnapshot(non_daemon_names=tuple(names))


def assert_no_new_non_daemon_threads(before: ThreadSnapshot, *, timeout_seconds: float = 2.0) -> None:
    deadline = time.time() + max(0.1, timeout_seconds)
    while time.time() < deadline:
        after = snapshot_non_daemon_threads()
        extras = sorted(set(after.non_daemon_names) - set(before.non_daemon_names))
        if not extras:
            return
        time.sleep(0.05)
    after = snapshot_non_daemon_threads()
    extras = sorted(set(after.non_daemon_names) - set(before.non_daemon_names))
    if extras:
        raise AssertionError(f"Detected leaked non-daemon threads: {extras}")


def assert_process_exits(proc: subprocess.Popen[object], *, timeout_seconds: float = 5.0) -> None:
    deadline = time.time() + max(0.1, timeout_seconds)
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.05)
    if proc.poll() is None:
        raise AssertionError(f"Process did not terminate within {timeout_seconds:.1f}s: pid={proc.pid}")
