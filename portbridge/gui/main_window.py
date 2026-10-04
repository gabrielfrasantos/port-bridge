"""MainWindow: the port-bridge GUI main window.

Layout (the three channel sections scroll; "+ Add" appends a row, "−" removes one)
------
┌─────────────────────────────────────────────────────────┐
│  Serial ──────────────────────────────────────────────  │
│  Port [___________▼]  Baud [______]  TCP port [_____][−]│
│  [+ Add serial port]                                    │
│  CAN bus ─────────────────────────────────────────────  │
│  Interface [______▼]  Channel [___]  Bitrate [______][−]│
│            TTY baud [______]         TCP port [_____]   │
│  [+ Add CAN bus]                                        │
│  Debug probes ────────────────────────────────────────  │
│  Type [______▼]  Speed [____]  GDB [____]  Telnet [_][−]│
│  J-Link   Device [_________]  Interface [SWD▼] Serial [] │
│  ST-Link  Interface [SWD▼] Serial [___] CubeProgrammer[] │
│  OpenOCD  Config [_______________▼]  Search dir [_____]  │
│  Executable [________________] [...] [Detect probes]    │
│  [+ Add debug probe]                                    │
│  Bind address [_______________]  Log level [_______▼]   │
│                                                         │
│  ●─ Serial  ●─ CAN  ●─ Probe   [ Start ]                │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │  log output                                       │  │
│  │  ...                                              │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from PySide6.QtCore import QTimer, Slot
from PySide6.QtGui import QCloseEvent, QColor, QTextCharFormat, QTextCursor

if TYPE_CHECKING:
    from portbridge.gui.tray import SystemTrayIcon
    from portbridge.gui.updater import UpdateChecker

from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from portbridge.bridge_config import BridgeConfig, find_conflicts
from portbridge.gui.bridge_controller import BridgeController
from portbridge.gui.channel_rows import CanRow, ChannelRow, ChannelSection, ProbeRow, SerialRow

_T = TypeVar("_T")

_MAX_SECTIONS_HEIGHT = 480
_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]

_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: "#888888",
    logging.INFO: "#dddddd",
    logging.WARNING: "#f0c060",
    logging.ERROR: "#ff6060",
}


def _row_config(label: str, build: Callable[[], _T | None]) -> _T | None:
    try:
        return build()
    except ValueError as exc:
        raise ValueError(f"{label}: {exc}") from exc


def _status_dot(color: str) -> QLabel:
    label = QLabel("●")
    label.setStyleSheet(f"color: {color}; font-size: 18px;")
    return label


class MainWindow(QMainWindow):
    def __init__(self, log_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("port-bridge")
        self.setMinimumWidth(680)

        self._tray: SystemTrayIcon | None = None
        self._log_path = log_path
        self._checker: UpdateChecker | None = None
        self._running_config: BridgeConfig | None = None

        self._controller = BridgeController(self)
        self._controller.started.connect(self._on_started)
        self._controller.stopped.connect(self._on_stopped)
        self._controller.error.connect(self._on_error)
        self._controller.log_record.connect(self._on_log_record)

        self._serial_dot = _status_dot("#444444")
        self._can_dot = _status_dot("#444444")
        self._probe_dot = _status_dot("#444444")

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)

        layout.addWidget(self._build_sections(), stretch=3)
        layout.addWidget(self._build_general_group())
        layout.addWidget(self._build_status_bar())
        layout.addWidget(self._build_log_panel(), stretch=1)

        self._setup_status_bar()

    def _setup_status_bar(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        if self._log_path is not None:
            log_label = QLabel(f"Log: {self._log_path}")
            log_label.setStyleSheet("color: #888888; font-size: 10px;")
            bar.addWidget(log_label, 1)
            open_btn = QPushButton("Open Log")
            open_btn.setFlat(True)
            open_btn.setStyleSheet("color: #aaaaff; font-size: 10px;")
            open_btn.clicked.connect(self._open_log)
            bar.addPermanentWidget(open_btn)

        self._check_updates_btn = QPushButton("Check for Updates")
        self._check_updates_btn.setFlat(True)
        self._check_updates_btn.setStyleSheet("color: #aaaaff; font-size: 10px;")
        self._check_updates_btn.clicked.connect(self._check_for_updates)
        bar.addPermanentWidget(self._check_updates_btn)

    def set_tray(self, tray: SystemTrayIcon | None) -> None:
        self._tray = tray

    def set_checker(self, checker: UpdateChecker) -> None:
        self._checker = checker

    def closeEvent(self, event: QCloseEvent) -> None:
        from PySide6.QtWidgets import QSystemTrayIcon

        if QSystemTrayIcon.isSystemTrayAvailable() and self._tray is not None:
            self.hide()
            event.ignore()
        else:
            event.accept()

    # ------------------------------------------------------------------
    # Builder helpers
    # ------------------------------------------------------------------

    def _build_sections(self) -> QScrollArea:
        self._serial_section = ChannelSection("Serial", "Add serial port", lambda: SerialRow(self))
        self._can_section = ChannelSection("CAN bus", "Add CAN bus", lambda: CanRow(self))
        self._probe_section = ChannelSection(
            "Debug probes", "Add debug probe", lambda: ProbeRow(self)
        )
        self._sections = [self._serial_section, self._can_section, self._probe_section]

        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        for section in self._sections:
            section.rows_changed.connect(self._fit_sections)
            column.addWidget(section)
        column.addStretch()

        self._sections_scroll = QScrollArea()
        self._sections_scroll.setWidgetResizable(True)
        self._sections_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._sections_scroll.setWidget(content)
        self._fit_sections()
        return self._sections_scroll

    def _build_general_group(self) -> QGroupBox:
        box = QGroupBox("General")
        row = QHBoxLayout(box)

        self._bind_edit = QLineEdit("127.0.0.1")
        self._log_level_combo = QComboBox()
        self._log_level_combo.addItems(_LOG_LEVELS)
        self._log_level_combo.setCurrentText("INFO")

        row.addWidget(QLabel("Bind address:"))
        row.addWidget(self._bind_edit)
        row.addWidget(QLabel("Log level:"))
        row.addWidget(self._log_level_combo)
        row.addStretch()
        return box

    def _build_status_bar(self) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)

        row.addWidget(self._serial_dot)
        row.addWidget(QLabel("Serial"))
        row.addSpacing(16)
        row.addWidget(self._can_dot)
        row.addWidget(QLabel("CAN"))
        row.addSpacing(16)
        row.addWidget(self._probe_dot)
        row.addWidget(QLabel("Probe"))
        row.addStretch()

        self._start_btn = QPushButton("Start")
        self._start_btn.setMinimumWidth(100)
        self._start_btn.clicked.connect(self._toggle_bridge)
        row.addWidget(self._start_btn)
        return widget

    def _build_log_panel(self) -> QPlainTextEdit:
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(2000)
        self._log_view.setStyleSheet(
            "QPlainTextEdit {"
            " background: #1e1e1e; color: #dddddd;"
            " font-family: monospace; font-size: 11px;"
            " }"
        )
        return self._log_view

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @Slot()
    def _toggle_bridge(self) -> None:
        if self._controller.is_running:
            self._controller.stop()
            self._start_btn.setEnabled(False)
            return

        try:
            config = self._build_config()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid settings", str(exc))
            return
        if config.is_empty:
            QMessageBox.information(
                self,
                "Nothing to start",
                "Select a serial port, a CAN interface or a debug probe first.",
            )
            return
        conflicts = find_conflicts(config)
        if conflicts:
            QMessageBox.warning(
                self,
                "Conflicting settings",
                "Each channel needs its own TCP port and device:\n\n" + "\n".join(conflicts),
            )
            return

        self._running_config = config
        self._set_editing_enabled(False)
        self._controller.start(config)
        self._start_btn.setEnabled(False)

    @Slot()
    def _check_for_updates(self) -> None:
        if self._checker is not None:
            self._check_updates_btn.setEnabled(False)
            self._check_updates_btn.setText("Checking…")
            self._checker.check_in_background()

    @Slot()
    def _restore_check_button(self) -> None:
        self._check_updates_btn.setEnabled(True)
        self._check_updates_btn.setText("Check for Updates")

    @Slot()
    def _open_log(self) -> None:
        from portbridge.gui.log_file import open_log_file  # noqa: PLC0415

        open_log_file()

    @Slot()
    def _on_started(self) -> None:
        self._start_btn.setText("Stop")
        self._start_btn.setEnabled(True)
        cfg = self._running_config or BridgeConfig()
        if cfg.serials:
            self._serial_dot.setStyleSheet("color: #44dd44; font-size: 18px;")
        if cfg.cans:
            self._can_dot.setStyleSheet("color: #44dd44; font-size: 18px;")
        if cfg.probes:
            self._probe_dot.setStyleSheet("color: #44dd44; font-size: 18px;")
        from portbridge.gui.tray import SystemTrayIcon

        if isinstance(self._tray, SystemTrayIcon):
            self._tray.notify_bridge_started()

    @Slot()
    def _on_stopped(self) -> None:
        self._start_btn.setText("Start")
        self._start_btn.setEnabled(True)
        self._running_config = None
        self._set_editing_enabled(True)
        self._serial_dot.setStyleSheet("color: #444444; font-size: 18px;")
        self._can_dot.setStyleSheet("color: #444444; font-size: 18px;")
        self._probe_dot.setStyleSheet("color: #444444; font-size: 18px;")
        from portbridge.gui.tray import SystemTrayIcon

        if isinstance(self._tray, SystemTrayIcon):
            self._tray.notify_bridge_stopped()

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self._start_btn.setText("Start")
        self._start_btn.setEnabled(True)
        self._running_config = None
        self._set_editing_enabled(True)
        self._serial_dot.setStyleSheet("color: #ff4444; font-size: 18px;")
        self._can_dot.setStyleSheet("color: #ff4444; font-size: 18px;")
        self._probe_dot.setStyleSheet("color: #ff4444; font-size: 18px;")
        self._append_log_line(f"[ERROR] {message}", "#ff6060")

    @Slot(object)
    def _on_log_record(self, record: logging.LogRecord) -> None:
        color = _LEVEL_COLORS.get(record.levelno, "#dddddd")
        self._append_log_line(self._format_record(record), color)

    @Slot()
    def _fit_sections(self) -> None:
        # New rows are shown by a queued event, so measure once the event loop has run.
        QTimer.singleShot(0, self._resize_sections)

    def _resize_sections(self) -> None:
        content = self._sections_scroll.widget()
        if content is None:
            return
        height = content.sizeHint().height()
        self._sections_scroll.setMinimumHeight(min(height, _MAX_SECTIONS_HEIGHT))
        self._sections_scroll.setMaximumHeight(height + 2)

    # ------------------------------------------------------------------
    # RowHost
    # ------------------------------------------------------------------

    def _all_rows(self) -> list[ChannelRow]:
        return [row for section in self._sections for row in section.rows]

    def used_ports(self, exclude: QWidget | None = None) -> set[int]:
        ports: set[int] = set()
        for row in self._all_rows():
            if row is not exclude:
                ports |= row.used_ports()
        return ports

    def used_devices(self, exclude: QWidget | None = None) -> set[str]:
        devices: set[str] = set()
        for row in self._all_rows():
            if row is not exclude:
                devices |= row.used_devices()
        return devices

    def log(self, text: str, color: str) -> None:
        self._append_log_line(text, color)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_editing_enabled(self, enabled: bool) -> None:
        for section in self._sections:
            section.setEnabled(enabled)
        self._bind_edit.setEnabled(enabled)
        self._log_level_combo.setEnabled(enabled)

    def _build_config(self) -> BridgeConfig:
        """Collect every enabled row; raises ``ValueError`` naming the bad field."""
        bind_address = self._bind_edit.text().strip() or "127.0.0.1"
        config = BridgeConfig(
            bind_address=bind_address, log_level=self._log_level_combo.currentText()
        )
        for index, row in enumerate(self._serial_section.rows, start=1):
            assert isinstance(row, SerialRow)
            serial = _row_config(f"Serial #{index}", row.to_config)
            if serial is not None:
                config.serials.append(serial)
        for index, row in enumerate(self._can_section.rows, start=1):
            assert isinstance(row, CanRow)
            can = _row_config(f"CAN bus #{index}", row.to_config)
            if can is not None:
                config.cans.append(can)
        for index, row in enumerate(self._probe_section.rows, start=1):
            assert isinstance(row, ProbeRow)
            probe = _row_config(f"Debug probe #{index}", lambda r=row: r.to_config(bind_address))
            if probe is not None:
                config.probes.append(probe)
        return config

    def _append_log_line(self, text: str, color: str) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = self._log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n", fmt)
        self._log_view.setTextCursor(cursor)
        self._log_view.ensureCursorVisible()

    @staticmethod
    def _format_record(record: logging.LogRecord) -> str:
        import time

        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        return f"[{ts}] [{record.levelname}] {record.name}: {record.getMessage()}"
