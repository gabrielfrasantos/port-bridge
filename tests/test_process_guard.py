import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from portbridge import process_guard

posix_only = unittest.skipIf(sys.platform == "win32", "POSIX process groups")


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        # An orphaned zombie only waits for init to reap it; it is not running any more.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def read_pid(path):
    wait_for(lambda: path.exists() and path.read_text().strip())
    return int(path.read_text())


class TestAdopt(unittest.TestCase):
    def test_test_doubles_are_ignored(self):
        self.assertIsNone(process_guard.adopt(mock.Mock(pid=4242), "probe"))
        self.assertIsNone(process_guard.adopt(object(), "probe"))


@posix_only
class TestProcessTree(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pidfile = Path(self._tmp.name) / "grandchild.pid"
        self.script = f"sleep 300 & echo $! > {self.pidfile}; wait"

    def tearDown(self):
        process_guard.kill_all()
        self._tmp.cleanup()

    async def _spawn(self):
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", self.script, **process_guard.spawn_kwargs()
        )
        guard = process_guard.adopt(proc, "probe")
        self.assertIsNotNone(guard)
        grandchild = read_pid(self.pidfile)
        self.assertTrue(pid_alive(grandchild))
        return proc, guard, grandchild

    async def test_child_gets_its_own_process_group(self):
        proc, guard, _ = await self._spawn()

        self.assertEqual(os.getpgid(proc.pid), proc.pid)
        self.assertNotEqual(os.getpgid(proc.pid), os.getpgid(0))
        guard.kill()
        await proc.wait()
        guard.release()

    async def test_terminate_reaches_grandchildren(self):
        proc, guard, grandchild = await self._spawn()

        self.assertTrue(guard.terminate())
        await asyncio.wait_for(proc.wait(), 5)

        self.assertTrue(wait_for(lambda: not pid_alive(grandchild)))
        guard.release()

    async def test_kill_reaches_grandchildren_that_ignore_sigterm(self):
        self.script = f"trap '' TERM; sleep 300 & echo $! > {self.pidfile}; wait"
        proc, guard, grandchild = await self._spawn()

        guard.kill()
        await asyncio.wait_for(proc.wait(), 5)

        self.assertTrue(wait_for(lambda: not pid_alive(grandchild)))
        guard.release()

    async def test_kill_all_filters_by_kind(self):
        proc, guard, grandchild = await self._spawn()

        process_guard.kill_all("verifier")
        await asyncio.sleep(0.1)
        self.assertIsNone(proc.returncode)

        process_guard.kill_all("probe")
        await asyncio.wait_for(proc.wait(), 5)
        self.assertTrue(wait_for(lambda: not pid_alive(grandchild)))
        guard.release()

    async def test_release_forgets_the_child(self):
        proc, guard, _ = await self._spawn()
        guard.release()

        process_guard.kill_all()
        await asyncio.sleep(0.1)

        self.assertIsNone(proc.returncode)
        os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()

    async def test_atexit_hook_reaps_live_children(self):
        proc, _guard, grandchild = await self._spawn()

        process_guard._reap_at_exit()
        await asyncio.wait_for(proc.wait(), 5)

        self.assertTrue(wait_for(lambda: not pid_alive(grandchild)))


class TestRunTracked(unittest.TestCase):
    def test_returns_completed_process(self):
        completed = process_guard.run_tracked(
            [sys.executable, "-c", "print('out')"], kind="verifier", timeout=10
        )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), "out")

    def test_timeout_kills_the_child_and_raises(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            process_guard.run_tracked(
                [sys.executable, "-c", "import time; time.sleep(60)"], kind="verifier", timeout=0.2
            )

        self.assertEqual(process_guard._live, {})

    def test_kill_all_from_another_thread_ends_a_running_child(self):
        results = []

        def run():
            results.append(
                process_guard.run_tracked(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    kind="verifier",
                    timeout=60,
                )
            )

        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(wait_for(lambda: bool(process_guard._live)))

        process_guard.kill_all("verifier")
        thread.join(10)

        self.assertFalse(thread.is_alive())
        self.assertNotEqual(results[0].returncode, 0)
        self.assertEqual(process_guard._live, {})


if __name__ == "__main__":
    unittest.main()
