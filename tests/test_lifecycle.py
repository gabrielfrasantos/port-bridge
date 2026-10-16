import asyncio
import unittest

from portbridge.lifecycle import run_startup, stop_servers


class RecordingServer:
    def __init__(self, name, log, error=None):
        self.name = name
        self.log = log
        self.error = error

    async def stop(self):
        self.log.append(self.name)
        if self.error is not None:
            raise self.error


class TestStopServers(unittest.IsolatedAsyncioTestCase):
    async def test_stops_in_reverse_start_order(self):
        log = []

        await stop_servers([RecordingServer(n, log) for n in "abc"])

        self.assertEqual(log, ["c", "b", "a"])

    async def test_failing_stop_does_not_skip_remaining_servers(self):
        log = []
        servers = [
            RecordingServer("probe", log),
            RecordingServer("can", log, error=RuntimeError("boom")),
            RecordingServer("serial", log),
        ]

        with self.assertLogs("portbridge.lifecycle", level="ERROR"):
            await stop_servers(servers)

        self.assertEqual(log, ["serial", "can", "probe"])

    async def test_cancellation_still_stops_remaining_servers_then_propagates(self):
        log = []
        servers = [
            RecordingServer("probe", log),
            RecordingServer("can", log, error=asyncio.CancelledError()),
        ]

        with self.assertRaises(asyncio.CancelledError):
            await stop_servers(servers)

        self.assertEqual(log, ["can", "probe"])


class TestRunStartup(unittest.IsolatedAsyncioTestCase):
    async def test_returns_true_when_startup_completes(self):
        async def start():
            await asyncio.sleep(0)

        self.assertTrue(await run_startup(start(), asyncio.Event()))

    async def test_propagates_startup_errors(self):
        async def start():
            raise ValueError("bad port")

        with self.assertRaisesRegex(ValueError, "bad port"):
            await run_startup(start(), asyncio.Event())

    async def test_stop_signal_cancels_startup_and_returns_false(self):
        cancelled = asyncio.Event()
        stop_event = asyncio.Event()

        async def start():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(run_startup(start(), stop_event))
        await asyncio.sleep(0.01)
        stop_event.set()

        self.assertFalse(await task)
        self.assertTrue(cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
