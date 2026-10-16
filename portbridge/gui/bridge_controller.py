"""BridgeController: runs the asyncio bridge loop in a daemon thread and posts events to Qt.

Thread model
------------
- Qt main thread owns all widgets.
- A single daemon thread runs ``asyncio.run(bridge_main(...))``. Being a daemon it is killed
  at interpreter exit, so ``shutdown()`` must be called first (see ``aboutToQuit``) to let the
  servers, and the probe child processes they own, stop cleanly.
- asyncio → Qt: log records are placed in a ``queue.SimpleQueue`` by a
  ``logging.Handler``; a ``QTimer`` on the main thread drains the queue.
- Qt → asyncio stop: ``loop.call_soon_threadsafe(stop_event.set)`` is safe
  to call from any thread.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading

from PySide6.QtCore import QObject, QTimer, Signal

from portbridge import process_guard
from portbridge.bridge_config import BridgeConfig
from portbridge.lifecycle import run_startup, stop_servers

logger = logging.getLogger(__name__)


class _QueueHandler(logging.Handler):
    def __init__(self, log_queue: queue.SimpleQueue[logging.LogRecord]) -> None:
        super().__init__()
        self._queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        self._queue.put_nowait(record)


class BridgeController(QObject):
    """Manages the bridge asyncio loop lifecycle from the Qt main thread.

    Signals
    -------
    started:
        Emitted when the bridge is running and listening on all configured ports.
    stopped:
        Emitted when the bridge has shut down cleanly.
    error(str):
        Emitted when the bridge fails to start or crashes unexpectedly.
    log_record(logging.LogRecord):
        Emitted for each log record produced by the bridge.
    """

    started = Signal()
    stopped = Signal()
    error = Signal(str)
    log_record: Signal = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._stop_requested = threading.Event()
        self._log_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
        self._queue_handler = _QueueHandler(self._log_queue)

        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(100)
        self._drain_timer.timeout.connect(self._drain_log_queue)

    # ------------------------------------------------------------------
    # Public API (call from Qt main thread)
    # ------------------------------------------------------------------

    def start(self, config: BridgeConfig) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_requested.clear()
        self._thread = threading.Thread(
            target=self._run_asyncio,
            args=(config,),
            daemon=True,
            name="port-bridge-asyncio",
        )
        self._drain_timer.start()
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested.set()
        loop, stop_event = self._loop, self._stop_event
        if loop is not None and stop_event is not None:
            try:
                loop.call_soon_threadsafe(stop_event.set)
            except RuntimeError:
                logger.debug("Event loop already closed", exc_info=True)

    def shutdown(self, timeout: float = 30.0) -> None:
        """Stop the bridge and wait for it, so no child process outlives the application."""
        self.stop()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                logger.error("Bridge did not stop within %g s; killing child processes", timeout)
        process_guard.kill_all()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_asyncio(self, config: BridgeConfig) -> None:
        try:
            asyncio.run(self._bridge_main(config))
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self._loop = None
            self._stop_event = None
            self._drain_timer.stop()
            self.stopped.emit()

    async def _bridge_main(self, config: BridgeConfig) -> None:
        from portbridge.can_server import CanBusOverTcpServer  # noqa: PLC0415
        from portbridge.probe_server import DebugProbeServer  # noqa: PLC0415
        from portbridge.serial_server import SerialOverTcpServer  # noqa: PLC0415
        from portbridge.server_errors import BridgeServerError  # noqa: PLC0415

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()

        root_logger = logging.getLogger("portbridge")
        root_logger.setLevel(getattr(logging, config.log_level, logging.INFO))
        root_logger.addHandler(self._queue_handler)

        servers: list[SerialOverTcpServer | CanBusOverTcpServer | DebugProbeServer] = []

        async def start_servers() -> None:
            for serial in config.serials:
                srv = SerialOverTcpServer(
                    serial_port=serial.port,
                    baudrate=serial.baudrate,
                    tcp_port=serial.tcp_port,
                    bind_address=config.bind_address,
                )
                await srv.start()
                servers.append(srv)

            for can in config.cans:
                srv_can = CanBusOverTcpServer(
                    interface=can.interface,
                    channel=can.channel,
                    bitrate=can.bitrate,
                    tcp_port=can.tcp_port,
                    tty_baudrate=can.tty_baudrate,
                    bind_address=config.bind_address,
                )
                await srv_can.start()
                servers.append(srv_can)

            for probe in config.probes:
                srv_probe = DebugProbeServer(probe)
                await srv_probe.start()
                servers.append(srv_probe)

        try:
            if self._stop_requested.is_set():
                self._stop_event.set()
            try:
                if not await run_startup(start_servers(), self._stop_event):
                    return
            except BridgeServerError as exc:
                self.error.emit(str(exc))
                return
            except Exception as exc:
                self.error.emit(f"Unexpected error: {exc}")
                return

            self.started.emit()
            await self._stop_event.wait()
        finally:
            await stop_servers(servers)
            root_logger.removeHandler(self._queue_handler)

    def _drain_log_queue(self) -> None:
        while True:
            try:
                record = self._log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_record.emit(record)
