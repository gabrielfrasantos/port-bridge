"""MainWindow: the port-bridge GUI main window.

Layout
------
┌─────────────────────────────────────────────────────────┐
│  Serial ──────────────────────────────────────────────  │
│  Port [___________▼]  Baud [______]  TCP port [_____]   │
│  CAN ────────────────────────────────────────────────   │
│  Interface [______▼]  Channel [___]  Bitrate [______]   │
│            TTY baud [______]         TCP port [_____]   │
│  Debug probe ────────────────────────────────────────   │
│  Type [______▼]  Speed [____]  GDB [____]  Telnet [___] │
│  J-Link   Device [_________]  Interface [SWD▼] Serial [] │
│  OpenOCD  Config [_______________▼]  Search dir [_____]  │
│  Executable [________________] [...] [Detect probes]    │
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
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Slot
from PySide6.QtGui import QCloseEvent, QColor, QTextCharFormat, QTextCursor

if TYPE_CHECKING:
    from portbridge.gui.tray import SystemTrayIcon
    from portbridge.gui.updater import UpdateChecker

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from portbridge.gui.bridge_controller import BridgeConfig, BridgeController
from portbridge.list_can_interfaces import gather_all
from portbridge.probe_server import (
    DEFAULT_JLINK_SPEED_KHZ,
    DEFAULT_PORTS,
    JLINK_INTERFACES,
    OPENOCD_PRESETS,
    PROBE_KINDS,
    ProbeConfig,
    list_probes,
)

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
    def __init__(self, log_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("port-bridge")
        self.setMinimumWidth(600)

        self._tray: SystemTrayIcon | None = None
        self._log_path = log_path
        self._checker: UpdateChecker | None = None

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

        layout.addWidget(self._build_serial_group())
        layout.addWidget(self._build_can_group())
        layout.addWidget(self._build_probe_group())
        layout.addWidget(self._build_general_group())
        layout.addWidget(self._build_status_bar())
        layout.addWidget(self._build_log_panel(), stretch=1)

        self._refresh_serial_ports()
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

    def _build_probe_group(self) -> QGroupBox:
        box = QGroupBox("Debug probe")
        form = QFormLayout(box)

        self._probe_kind_combo = QComboBox()
        self._probe_kind_combo.addItem("(disabled)")
        self._probe_kind_combo.addItems(list(PROBE_KINDS))
        self._probe_kind_combo.currentTextChanged.connect(self._on_probe_kind_changed)

        self._probe_speed_edit = QLineEdit()
        self._probe_speed_edit.setMaximumWidth(80)
        self._probe_gdb_edit = QLineEdit()
        self._probe_gdb_edit.setMaximumWidth(80)
        self._probe_telnet_edit = QLineEdit()
        self._probe_telnet_edit.setMaximumWidth(80)

        row1 = QHBoxLayout()
        row1.addWidget(self._probe_kind_combo)
        row1.addStretch()
        row1.addWidget(QLabel("Speed (kHz)"))
        row1.addWidget(self._probe_speed_edit)
        row1.addWidget(QLabel("GDB port"))
        row1.addWidget(self._probe_gdb_edit)
        row1.addWidget(QLabel("Telnet port"))
        row1.addWidget(self._probe_telnet_edit)

        self._jlink_device_edit = QLineEdit()
        self._jlink_device_edit.setPlaceholderText("TM4C123GH6PM")
        self._jlink_if_combo = QComboBox()
        self._jlink_if_combo.addItems(list(JLINK_INTERFACES))
        self._jlink_serial_edit = QLineEdit()
        self._jlink_serial_edit.setPlaceholderText("any")
        self._jlink_serial_edit.setMaximumWidth(120)

        jlink_row = QHBoxLayout()
        jlink_row.addWidget(QLabel("Device"))
        jlink_row.addWidget(self._jlink_device_edit, stretch=1)
        jlink_row.addWidget(QLabel("Interface"))
        jlink_row.addWidget(self._jlink_if_combo)
        jlink_row.addWidget(QLabel("Serial"))
        jlink_row.addWidget(self._jlink_serial_edit)

        self._openocd_config_combo = QComboBox()
        self._openocd_config_combo.setEditable(True)
        self._openocd_config_combo.addItems(list(OPENOCD_PRESETS.values()))
        self._openocd_config_combo.setToolTip(
            "OpenOCD config script(s) passed with -f; separate several with ';'"
        )
        self._openocd_config_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._openocd_search_edit = QLineEdit()
        self._openocd_search_edit.setPlaceholderText("optional")

        openocd_row = QHBoxLayout()
        openocd_row.addWidget(QLabel("Config"))
        openocd_row.addWidget(self._openocd_config_combo, stretch=2)
        openocd_row.addWidget(QLabel("Search dir"))
        openocd_row.addWidget(self._openocd_search_edit, stretch=1)

        self._probe_path_edit = QLineEdit()
        self._probe_path_edit.setPlaceholderText("auto-detect (PATH / standard install folders)")
        browse_btn = QPushButton("…")
        browse_btn.setMaximumWidth(32)
        browse_btn.setToolTip("Select JLinkGDBServerCL or openocd executable")
        browse_btn.clicked.connect(self._browse_probe_path)
        detect_btn = QPushButton("Detect probes")
        detect_btn.clicked.connect(self._detect_probes)

        exe_row = QHBoxLayout()
        exe_row.addWidget(self._probe_path_edit, stretch=1)
        exe_row.addWidget(browse_btn)
        exe_row.addWidget(detect_btn)

        self._probe_common_widgets: list[QWidget] = [
            self._probe_speed_edit,
            self._probe_gdb_edit,
            self._probe_telnet_edit,
            self._probe_path_edit,
            browse_btn,
        ]
        self._jlink_widgets: list[QWidget] = [
            self._jlink_device_edit,
            self._jlink_if_combo,
            self._jlink_serial_edit,
        ]
        self._openocd_widgets: list[QWidget] = [
            self._openocd_config_combo,
            self._openocd_search_edit,
        ]

        form.addRow("Type:", row1)
        form.addRow("J-Link:", jlink_row)
        form.addRow("OpenOCD:", openocd_row)
        form.addRow("Executable:", exe_row)

        self._on_probe_kind_changed(self._probe_kind_combo.currentText())
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
        else:
            self._controller.start(self._build_config())
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
        cfg = self._build_config()
        if cfg.serial_port:
            self._serial_dot.setStyleSheet("color: #44dd44; font-size: 18px;")
        if cfg.can_interface:
            self._can_dot.setStyleSheet("color: #44dd44; font-size: 18px;")
        if cfg.probe is not None:
            self._probe_dot.setStyleSheet("color: #44dd44; font-size: 18px;")
        from portbridge.gui.tray import SystemTrayIcon

        if isinstance(self._tray, SystemTrayIcon):
            self._tray.notify_bridge_started()

    @Slot()
    def _on_stopped(self) -> None:
        self._start_btn.setText("Start")
        self._start_btn.setEnabled(True)
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
        self._serial_dot.setStyleSheet("color: #ff4444; font-size: 18px;")
        self._can_dot.setStyleSheet("color: #ff4444; font-size: 18px;")
        self._probe_dot.setStyleSheet("color: #ff4444; font-size: 18px;")
        self._append_log_line(f"[ERROR] {message}", "#ff6060")

    @Slot(object)
    def _on_log_record(self, record: logging.LogRecord) -> None:
        color = _LEVEL_COLORS.get(record.levelno, "#dddddd")
        self._append_log_line(self._format_record(record), color)

    @Slot(str)
    def _on_can_interface_changed(self, text: str) -> None:
        self._can_tty_baud_edit.setEnabled(text == "slcan")

    @Slot(str)
    def _on_probe_kind_changed(self, text: str) -> None:
        enabled = text in PROBE_KINDS
        for widget in self._probe_common_widgets:
            widget.setEnabled(enabled)
        for widget in self._jlink_widgets:
            widget.setEnabled(text == "jlink")
        for widget in self._openocd_widgets:
            widget.setEnabled(text == "openocd")

        gdb_port, telnet_port = DEFAULT_PORTS.get(text, (0, 0))
        self._probe_gdb_edit.setPlaceholderText(str(gdb_port) if enabled else "")
        self._probe_telnet_edit.setPlaceholderText(str(telnet_port) if enabled else "")
        speed_hint = {"jlink": str(DEFAULT_JLINK_SPEED_KHZ), "openocd": "cfg"}.get(text, "")
        self._probe_speed_edit.setPlaceholderText(speed_hint)

    @Slot()
    def _browse_probe_path(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select GDB server executable")
        if path:
            self._probe_path_edit.setText(path)

    @Slot()
    def _detect_probes(self) -> None:
        self._append_log_line("[INFO] Scanning for debug probes...", "#888888")
        kind = self._probe_kind_combo.currentText()
        path = self._probe_path_edit.text().strip() or None
        try:
            probes = list_probes(
                jlink_path=path if kind == "jlink" else None,
                openocd_path=path if kind == "openocd" else None,
            )
        except Exception as exc:
            self._append_log_line(f"[ERROR] Probe detection failed: {exc}", "#ff6060")
            return
        if not probes:
            self._append_log_line("[INFO] No debug probes or probe tools detected.", "#888888")
            return
        for probe in probes:
            self._append_log_line(
                f"  {probe.get('probe', '')}  {probe.get('serial', '')}  "
                f"{probe.get('details', '')}",
                "#aaddff",
            )
        if kind not in PROBE_KINDS:
            idx = self._probe_kind_combo.findText(probes[0].get("probe", ""))
            if idx >= 0:
                self._probe_kind_combo.setCurrentIndex(idx)

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
        bind_address = self._bind_edit.text().strip() or "127.0.0.1"

        return BridgeConfig(
            serial_port=serial_port_text,
            serial_baudrate=int(self._serial_baud_edit.text() or "921600"),
            serial_tcp_port=int(self._serial_tcp_edit.text() or "5000"),
            can_interface=can_iface,
            can_channel=self._can_channel_edit.text().strip() or None,
            can_bitrate=int(self._can_bitrate_edit.text() or "125000"),
            can_tty_baudrate=int(self._can_tty_baud_edit.text() or "115200"),
            can_tcp_port=int(self._can_tcp_edit.text() or "5001"),
            bind_address=bind_address,
            log_level=self._log_level_combo.currentText(),
            probe=self._build_probe_config(bind_address),
        )

    def _build_probe_config(self, bind_address: str) -> ProbeConfig | None:
        kind = self._probe_kind_combo.currentText()
        if kind not in PROBE_KINDS:
            return None

        def _optional_int(edit: QLineEdit) -> int | None:
            text = edit.text().strip()
            return int(text) if text else None

        configs = [
            c.strip() for c in self._openocd_config_combo.currentText().split(";") if c.strip()
        ]
        search_dir = self._openocd_search_edit.text().strip()
        return ProbeConfig(
            kind="jlink" if kind == "jlink" else "openocd",
            executable=self._probe_path_edit.text().strip() or None,
            bind_address=bind_address,
            gdb_port=_optional_int(self._probe_gdb_edit),
            telnet_port=_optional_int(self._probe_telnet_edit),
            speed_khz=_optional_int(self._probe_speed_edit),
            device=self._jlink_device_edit.text().strip() or None,
            interface=self._jlink_if_combo.currentText(),
            serial_number=self._jlink_serial_edit.text().strip() or None,
            configs=configs,
            search_dirs=[search_dir] if search_dir else [],
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
