"""BridgeController: runs the asyncio bridge loop in a daemon thread and posts events to Qt.

Thread model
------------
- Qt main thread owns all widgets.
- A single daemon thread runs ``asyncio.run(bridge_main(...))``.
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
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal


@dataclass
class BridgeConfig:
    serial_port: str | None = None
    serial_baudrate: int = 921600
    serial_tcp_port: int = 5000
    can_interface: str | None = None
    can_channel: str | None = None
    can_bitrate: int = 125000
    can_tty_baudrate: int = 115200
    can_tcp_port: int = 5001
    bind_address: str = "127.0.0.1"
    log_level: str = "INFO"


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

        self._thread = threading.Thread(
            target=self._run_asyncio,
            args=(config,),
            daemon=True,
            name="port-bridge-asyncio",
        )
        self._drain_timer.start()
        self._thread.start()

    def stop(self) -> None:
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)

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
        from portbridge.serial_server import SerialOverTcpServer  # noqa: PLC0415
        from portbridge.server_errors import BridgeServerError  # noqa: PLC0415

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()

        root_logger = logging.getLogger("portbridge")
        root_logger.setLevel(getattr(logging, config.log_level, logging.INFO))
        root_logger.addHandler(self._queue_handler)

        servers: list[SerialOverTcpServer | CanBusOverTcpServer] = []
        try:
            if config.serial_port:
                srv = SerialOverTcpServer(
                    serial_port=config.serial_port,
                    baudrate=config.serial_baudrate,
                    tcp_port=config.serial_tcp_port,
                    bind_address=config.bind_address,
                )
                await srv.start()
                servers.append(srv)

            if config.can_interface:
                channel = config.can_channel or "0"
                srv_can = CanBusOverTcpServer(
                    interface=config.can_interface,
                    channel=channel,
                    bitrate=config.can_bitrate,
                    tcp_port=config.can_tcp_port,
                    tty_baudrate=config.can_tty_baudrate if config.can_interface == "slcan" else None,  # noqa: E501
                    bind_address=config.bind_address,
                )
                await srv_can.start()
                servers.append(srv_can)
        except BridgeServerError as exc:
            for s in reversed(servers):
                await s.stop()
            root_logger.removeHandler(self._queue_handler)
            self.error.emit(str(exc))
            return
        except Exception as exc:
            for s in reversed(servers):
                await s.stop()
            root_logger.removeHandler(self._queue_handler)
            self.error.emit(f"Unexpected error: {exc}")
            return

        self.started.emit()

        await self._stop_event.wait()

        for s in servers:
            await s.stop()
        root_logger.removeHandler(self._queue_handler)

    def _drain_log_queue(self) -> None:
        while True:
            try:
                record = self._log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_record.emit(record)
