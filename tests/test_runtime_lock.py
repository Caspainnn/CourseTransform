"""Cross-process guarantees of the PowerPoint export lock."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from webapp import runtime_lock

ROOT = Path(__file__).resolve().parents[1]


class CrossProcessLock(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.lock = Path(self._temp.name) / "export.lock"

    def tearDown(self):
        self._temp.cleanup()

    def test_os_lock_is_per_handle(self):
        first = os.open(self.lock, os.O_CREAT | os.O_RDWR)
        second = os.open(self.lock, os.O_CREAT | os.O_RDWR)
        try:
            runtime_lock._lock_handle(first)
            with self.assertRaises(OSError):
                runtime_lock._lock_handle(second)
        finally:
            runtime_lock._unlock_handle(first)
            os.close(first)
            os.close(second)

    def test_second_process_is_refused_while_the_first_holds_the_lock(self):
        marker = Path(self._temp.name) / "held.marker"
        code = (
            "import sys, time\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "from webapp.runtime_lock import powerpoint_exclusive\n"
            "with powerpoint_exclusive(timeout=5, path=__import__('pathlib').Path("
            f"{str(self.lock)!r})):\n"
            f"    open({str(marker)!r}, 'w').write('held')\n"
            "    time.sleep(2.5)\n"
        )
        holder = subprocess.Popen([sys.executable, "-c", code])
        try:
            deadline = time.time() + 8
            while not marker.exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "holder process did not take the lock")
            with self.assertRaises(runtime_lock.ResourceBusy):
                with runtime_lock.powerpoint_exclusive(timeout=0.3, poll=0.05, path=self.lock):
                    self.fail("second process must not acquire the export lock")
        finally:
            holder.wait(timeout=15)

    def test_instance_lock_refuses_a_second_owner(self):
        path = Path(tempfile.gettempdir()) / f"pptnative-test-instance-{os.getpid()}.lock"
        try:
            self.assertTrue(runtime_lock.acquire_instance_lock(path))
            self.assertFalse(runtime_lock.acquire_instance_lock(path))
        finally:
            runtime_lock.release_instance_locks()
            try:
                path.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()