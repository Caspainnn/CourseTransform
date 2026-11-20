"""Cross-process and cross-thread guards for shared Windows resources.

PowerPoint is a single-instance COM server. The legacy bench and the production
studio both drive it, and a second service process can be started by mistake
(Windows even allows two sockets to bind the same port with SO_REUSEADDR), so a
thread lock alone is not enough: the export path also takes an OS-level file lock
that every PPTNative process honours.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import threading
import time
from pathlib import Path

# In-process guard: cheap, and avoids two threads queueing on the OS lock.
POWERPOINT_RENDER_LOCK = threading.Lock()

LOCK_PATH = Path(tempfile.gettempdir()) / "pptnative-powerpoint-export.lock"


class ResourceBusy(RuntimeError):
    """Another PPTNative process holds the shared resource."""


def _lock_handle(handle: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover - deployment is Windows-only, kept for tests on POSIX
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_handle(handle: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_UN)


@contextlib.contextmanager
def powerpoint_exclusive(timeout: float = 1800.0, poll: float = 0.5, path: Path | None = None):
    """Hold PowerPoint exclusively across threads and processes.

    Raises ``ResourceBusy`` when another process keeps the lock longer than
    ``timeout``; callers translate that into a user-facing failure instead of
    silently exporting through a shared COM instance.
    """
    lock_file = Path(path) if path else LOCK_PATH
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with POWERPOINT_RENDER_LOCK:
        handle = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        acquired = False
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    _lock_handle(handle)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ResourceBusy(
                            f"PowerPoint export is busy in another PPTNative process ({lock_file})") from None
                    time.sleep(poll)
            os.lseek(handle, 0, os.SEEK_SET)
            os.truncate(handle, 0)
            os.write(handle, str(os.getpid()).encode())
            yield
        finally:
            if acquired:
                try:
                    _unlock_handle(handle)
                except OSError:
                    pass
            os.close(handle)


_HELD_INSTANCE_LOCKS: list[int] = []


def release_instance_locks() -> None:
    """Release process-lifetime locks (tests and clean shutdown)."""
    while _HELD_INSTANCE_LOCKS:
        handle = _HELD_INSTANCE_LOCKS.pop()
        try:
            _unlock_handle(handle)
        except OSError:
            pass
        os.close(handle)


def acquire_instance_lock(path: Path) -> bool:
    """Take a process-lifetime lock; False when another live process owns it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_CREAT | os.O_RDWR)
    try:
        _lock_handle(handle)
    except OSError:
        os.close(handle)
        return False
    os.lseek(handle, 0, os.SEEK_SET)
    os.truncate(handle, 0)
    os.write(handle, str(os.getpid()).encode())
    _HELD_INSTANCE_LOCKS.append(handle)
    return True


__all__ = ["POWERPOINT_RENDER_LOCK", "ResourceBusy", "powerpoint_exclusive", "acquire_instance_lock", "LOCK_PATH"]