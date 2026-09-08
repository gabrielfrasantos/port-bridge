"""MainWindow: the port-bridge GUI main window.

Layout
------
┌─────────────────────────────────────────────────────────┐
│  Serial ──────────────────────────────────────────────  │
│  Port [___________▼]  Baud [______]  TCP port [_____]   │
│  CAN ────────────────────────────────────────────────   │
│  Interface [______▼]  Channel [___]  Bitrate [______]   │
│            TTY baud [______]         TCP port [_____]   │
│  Bind address [_______________]  Log level [_______▼]   │
│                                                         │
│  ●─ Serial  ●─ CAN        [ Start ]                    │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │  log output                                       │  │
│  │  ...                                              │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Slot
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from portbridge.gui.bridge_controller import BridgeConfig, BridgeController
from portbridge.list_can_interfaces import gather_all

_CAN_INTERFACES = ["socketcan", "pcan", "slcan", "gs_usb", "candle"]
_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]

_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: "#888888",
    logging.INFO: "#dddddd",
    logging.WARNING: "#f0c060",
    logging.ERROR: "#ff6060",
}


def _status_dot(color: str) -> QLabel:
    label = QLabel("●")
    label.setStyleSheet(f"color: {color}; font-size: 18px;")
    return label


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("port-bridge")
        self.setMinimumWidth(600)

        self._controller = BridgeController(self)
        self._controller.started.connect(self._on_started)
        self._controller.stopped.connect(self._on_stopped)
        self._controller.error.connect(self._on_error)
        self._controller.log_record.connect(self._on_log_record)

        self._serial_dot = _status_dot("#444444")
        self._can_dot = _status_dot("#444444")

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)

        layout.addWidget(self._build_serial_group())
        layout.addWidget(self._build_can_group())
        layout.addWidget(self._build_general_group())
        layout.addWidget(self._build_status_bar())
        layout.addWidget(self._build_log_panel(), stretch=1)

        self._refresh_serial_ports()

    # ------------------------------------------------------------------
    # Builder helpers
    # ------------------------------------------------------------------

    def _build_serial_group(self) -> QGroupBox:
        box = QGroupBox("Serial")
        form = QFormLayout(box)

        self._serial_port_combo = QComboBox()
        self._serial_port_combo.setEditable(True)
        self._serial_port_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        self._serial_baud_edit = QLineEdit("921600")
        self._serial_baud_edit.setMaximumWidth(100)

        self._serial_tcp_edit = QLineEdit("5000")
        self._serial_tcp_edit.setMaximumWidth(80)

        row = QHBoxLayout()
        row.addWidget(self._serial_port_combo, stretch=2)
        row.addWidget(QLabel("Baud"))
        row.addWidget(self._serial_baud_edit)
        row.addWidget(QLabel("TCP port"))
        row.addWidget(self._serial_tcp_edit)

        refresh_btn = QPushButton("⟳")
        refresh_btn.setMaximumWidth(32)
        refresh_btn.setToolTip("Refresh port list")
        refresh_btn.clicked.connect(self._refresh_serial_ports)
        row.addWidget(refresh_btn)

        form.addRow("Port:", row)
        return box

    def _build_can_group(self) -> QGroupBox:
        box = QGroupBox("CAN bus")
        form = QFormLayout(box)

        self._can_iface_combo = QComboBox()
        self._can_iface_combo.addItem("(disabled)")
        self._can_iface_combo.addItems(_CAN_INTERFACES)
        self._can_iface_combo.currentTextChanged.connect(self._on_can_interface_changed)

        self._can_channel_edit = QLineEdit()
        self._can_channel_edit.setPlaceholderText("can0 / PCAN_USBBUS1 / 0")

        self._can_bitrate_edit = QLineEdit("125000")
        self._can_bitrate_edit.setMaximumWidth(100)

        self._can_tty_baud_edit = QLineEdit("115200")
        self._can_tty_baud_edit.setMaximumWidth(100)
        self._can_tty_baud_edit.setEnabled(False)

        self._can_tcp_edit = QLineEdit("5001")
        self._can_tcp_edit.setMaximumWidth(80)

        row1 = QHBoxLayout()
        row1.addWidget(self._can_iface_combo)
        row1.addWidget(QLabel("Channel"))
        row1.addWidget(self._can_channel_edit, stretch=1)
        row1.addWidget(QLabel("Bitrate"))
        row1.addWidget(self._can_bitrate_edit)
        row1.addWidget(QLabel("TCP port"))
        row1.addWidget(self._can_tcp_edit)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("TTY baud (slcan):"))
        row2.addWidget(self._can_tty_baud_edit)
        row2.addStretch()

        detect_btn = QPushButton("Detect hardware")
        detect_btn.clicked.connect(self._detect_can)
        row2.addWidget(detect_btn)

        form.addRow("Interface:", row1)
        form.addRow("", row2)
        return box

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
            "QPlainTextEdit { background: #1e1e1e; color: #dddddd; font-family: monospace; font-size: 11px; }"
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
        else:
            self._controller.start(self._build_config())
            self._start_btn.setEnabled(False)

    @Slot()
    def _on_started(self) -> None:
        self._start_btn.setText("Stop")
        self._start_btn.setEnabled(True)
        cfg = self._build_config()
        if cfg.serial_port:
            self._serial_dot.setStyleSheet("color: #44dd44; font-size: 18px;")
        if cfg.can_interface:
            self._can_dot.setStyleSheet("color: #44dd44; font-size: 18px;")

    @Slot()
    def _on_stopped(self) -> None:
        self._start_btn.setText("Start")
        self._start_btn.setEnabled(True)
        self._serial_dot.setStyleSheet("color: #444444; font-size: 18px;")
        self._can_dot.setStyleSheet("color: #444444; font-size: 18px;")

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self._start_btn.setText("Start")
        self._start_btn.setEnabled(True)
        self._serial_dot.setStyleSheet("color: #ff4444; font-size: 18px;")
        self._can_dot.setStyleSheet("color: #ff4444; font-size: 18px;")
        self._append_log_line(f"[ERROR] {message}", "#ff6060")

    @Slot(object)
    def _on_log_record(self, record: logging.LogRecord) -> None:
        color = _LEVEL_COLORS.get(record.levelno, "#dddddd")
        self._append_log_line(self._format_record(record), color)

    @Slot(str)
    def _on_can_interface_changed(self, text: str) -> None:
        self._can_tty_baud_edit.setEnabled(text == "slcan")

    @Slot()
    def _refresh_serial_ports(self) -> None:
        try:
            from serial.tools.list_ports import comports
            ports = [p.device for p in comports()]
        except ImportError:
            ports = []
        current = self._serial_port_combo.currentText()
        self._serial_port_combo.clear()
        self._serial_port_combo.addItem("")
        self._serial_port_combo.addItems(ports)
        if current:
            idx = self._serial_port_combo.findText(current)
            self._serial_port_combo.setCurrentIndex(idx if idx >= 0 else 0)

    @Slot()
    def _detect_can(self) -> None:
        self._append_log_line("[INFO] Scanning for CAN hardware...", "#888888")
        try:
            configs = gather_all()
        except Exception as exc:
            self._append_log_line(f"[ERROR] Detection failed: {exc}", "#ff6060")
            return
        if not configs:
            self._append_log_line("[INFO] No CAN hardware detected.", "#888888")
            return
        for cfg in configs:
            self._append_log_line(
                f"  {cfg.get('interface', '')}  {cfg.get('channel', '')}  {cfg.get('details', '')}",
                "#aaddff",
            )
        first = configs[0]
        iface = first.get("interface", "")
        idx = self._can_iface_combo.findText(iface)
        if idx >= 0:
            self._can_iface_combo.setCurrentIndex(idx)
        self._can_channel_edit.setText(first.get("channel", ""))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_config(self) -> BridgeConfig:
        iface_text = self._can_iface_combo.currentText()
        can_iface = iface_text if iface_text != "(disabled)" else None
        serial_port_text = self._serial_port_combo.currentText().strip() or None

        return BridgeConfig(
            serial_port=serial_port_text,
            serial_baudrate=int(self._serial_baud_edit.text() or "921600"),
            serial_tcp_port=int(self._serial_tcp_edit.text() or "5000"),
            can_interface=can_iface,
            can_channel=self._can_channel_edit.text().strip() or None,
            can_bitrate=int(self._can_bitrate_edit.text() or "125000"),
            can_tty_baudrate=int(self._can_tty_baud_edit.text() or "115200"),
            can_tcp_port=int(self._can_tcp_edit.text() or "5001"),
            bind_address=self._bind_edit.text().strip() or "127.0.0.1",
            log_level=self._log_level_combo.currentText(),
        )

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
