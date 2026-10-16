import asyncio
import importlib
import logging
import sys
import threading
import time
import types
import unittest
from unittest import mock

from portbridge import process_guard, serial_server
from portbridge.bridge_config import BridgeConfig, SerialConfig
from portbridge.server_errors import HardwareUnavailableError


class BoundSignal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)

    def connect(self, _callback):
        pass


class Signal:
    def __init__(self, *_types):
        pass

    def __set_name__(self, _owner, name):
        self._name = f"_signal_{name}"

    def __get__(self, obj, _owner=None):
        if obj is None:
            return self
        return obj.__dict__.setdefault(self._name, BoundSignal())


class QObject:
    def __init__(self, parent=None):
        pass


class QTimer:
    def __init__(self, parent=None):
        self.timeout = BoundSignal()

    def setInterval(self, _msec):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def load_bridge_controller():
    qt_core = types.ModuleType("PySide6.QtCore")
    qt_core.QObject = QObject
    qt_core.QTimer = QTimer
    qt_core.Signal = Signal
    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qt_core
    modules = {"PySide6": pyside, "PySide6.QtCore": qt_core}
    with mock.patch.dict(sys.modules, modules):
        sys.modules.pop("portbridge.gui.bridge_controller", None)
        module = importlib.import_module("portbridge.gui.bridge_controller")
    sys.modules.pop("portbridge.gui.bridge_controller", None)
    return module


bridge_controller = load_bridge_controller()


def fake_server(log, name, *, stop_error=None, start_error=None, hang=False):
    class Server:
        def __init__(self, **_kwargs):
            pass

        async def start(self):
            log.append(f"{name} start")
            if start_error is not None:
                raise start_error
            if hang:
                await asyncio.sleep(30)

        async def stop(self):
            log.append(f"{name} stop")
            if stop_error is not None:
                raise stop_error

    return Server


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestBridgeController(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.controller = bridge_controller.BridgeController()
        self.config = BridgeConfig(serials=[SerialConfig("COM3")])
        self.addCleanup(self.controller.shutdown, 5)

    def patch_serial(self, server_class):
        return mock.patch.object(serial_server, "SerialOverTcpServer", server_class)

    def test_shutdown_stops_servers_and_waits_for_the_bridge_thread(self):
        with self.patch_serial(fake_server(self.log, "serial")):
            self.controller.start(self.config)
            self.assertTrue(wait_for(lambda: self.controller.started.emitted))

            self.controller.shutdown(timeout=5)

        self.assertEqual(self.log, ["serial start", "serial stop"])
        self.assertFalse(self.controller.is_running)
        self.assertTrue(self.controller.stopped.emitted)
        self.assertFalse(self.controller.error.emitted)

    def test_shutdown_kills_remaining_children_as_a_last_resort(self):
        with mock.patch.object(process_guard, "kill_all") as kill_all:
            self.controller.shutdown(timeout=1)

        kill_all.assert_called_once_with()

    def test_stop_during_startup_cancels_it_and_cleans_up(self):
        with self.patch_serial(fake_server(self.log, "serial", hang=True)):
            self.controller.start(self.config)
            self.controller.stop()
            self.controller._thread.join(5)

        self.assertFalse(self.controller.is_running)
        self.assertFalse(self.controller.started.emitted)
        self.assertFalse(self.controller.error.emitted)
        self.assertNotIn("serial stop", self.log)

    def test_stop_right_after_start_is_not_lost(self):
        with self.patch_serial(fake_server(self.log, "serial", hang=True)):
            self.controller.start(self.config)
            self.controller.stop()
            self.assertTrue(wait_for(lambda: not self.controller.is_running))

        self.assertFalse(self.controller.started.emitted)

    def test_failing_stop_does_not_skip_the_other_servers(self):
        config = BridgeConfig(serials=[SerialConfig("COM3"), SerialConfig("COM4", tcp_port=5002)])
        servers = iter(
            [
                fake_server(self.log, "first"),
                fake_server(self.log, "second", stop_error=RuntimeError("busy")),
            ]
        )
        with self.patch_serial(lambda **kwargs: next(servers)(**kwargs)):
            self.controller.start(config)
            self.assertTrue(wait_for(lambda: self.controller.started.emitted))
            with self.assertLogs("portbridge.lifecycle", level="ERROR"):
                self.controller.shutdown(timeout=5)

        self.assertEqual(self.log[-2:], ["second stop", "first stop"])
        self.assertFalse(self.controller.is_running)

    def test_startup_error_is_reported_and_started_servers_are_stopped(self):
        config = BridgeConfig(serials=[SerialConfig("COM3"), SerialConfig("COM4", tcp_port=5002)])
        servers = iter(
            [
                fake_server(self.log, "first"),
                fake_server(self.log, "second", start_error=HardwareUnavailableError("no port")),
            ]
        )
        with self.patch_serial(lambda **kwargs: next(servers)(**kwargs)):
            self.controller.start(config)
            self.assertTrue(wait_for(lambda: self.controller.error.emitted))
            self.controller._thread.join(5)

        self.assertEqual(self.controller.error.emitted, [("no port",)])
        self.assertIn("first stop", self.log)
        handlers = logging.getLogger("portbridge").handlers
        self.assertNotIn(self.controller._queue_handler, handlers)

    def test_shutdown_from_the_bridge_thread_does_not_deadlock(self):
        self.controller._thread = threading.current_thread()

        self.controller.shutdown(timeout=0.1)


if __name__ == "__main__":
    unittest.main()
