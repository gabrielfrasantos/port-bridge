"""Channel rows: one editable row per serial port, CAN bus or debug probe.

``ChannelSection`` is a group box holding any number of rows of one kind; its "+ Add …" button
appends a row and each row's "−" button removes it (the last row cannot be removed; leave it
empty or "(disabled)" to turn the channel off). Rows talk to the window through ``RowHost`` so they
can pick TCP ports and devices no other row is using.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QRegularExpression, Signal, Slot
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from portbridge.bridge_config import (
    DEFAULT_CAN_BITRATE,
    DEFAULT_CAN_TCP_PORT,
    DEFAULT_CAN_TTY_BAUDRATE,
    DEFAULT_SERIAL_BAUDRATE,
    DEFAULT_SERIAL_TCP_PORT,
    CanConfig,
    SerialConfig,
    can_channel_or_default,
)
from portbridge.list_can_interfaces import gather_all
from portbridge.probe_server import (
    DEFAULT_JLINK_SPEED_KHZ,
    DEFAULT_PORTS,
    JLINK_INTERFACES,
    OPENOCD_PRESETS,
    PROBE_KINDS,
    STLINK_CONNECT_MODES,
    STLINK_DEVICES,
    STLINK_INTERFACES,
    ProbeConfig,
    list_probes,
)

DISABLED = "(disabled)"
CAN_INTERFACES = ["socketcan", "pcan", "slcan", "gs_usb", "candle"]

_INFO_COLOR = "#888888"
_ERROR_COLOR = "#ff6060"
_RESULT_COLOR = "#aaddff"


class RowHost(Protocol):
    def used_ports(self, exclude: QWidget | None = None) -> set[int]: ...

    def used_devices(self, exclude: QWidget | None = None) -> set[str]: ...

    def log(self, text: str, color: str) -> None: ...


def next_free_port(start: int, used: set[int], step: int = 1) -> int:
    port = start
    while port in used:
        port += step
    return port


def _optional_int(edit: QLineEdit) -> int | None:
    text = edit.text().strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"'{text}' is not a valid number") from None


def _int_or(edit: QLineEdit, default: int) -> int:
    value = _optional_int(edit)
    return default if value is None else value


def _optional_int_safe(edit: QLineEdit) -> int | None:
    try:
        return _optional_int(edit)
    except ValueError:
        return None


def _is_selectable_serial(serial: str) -> bool:
    # ST-LINK/V2 reports its USB serial as raw bytes; only pin rows to printable serials.
    return bool(serial) and serial.isascii() and serial.isalnum()


def _small_button(text: str, tooltip: str) -> QPushButton:
    button = QPushButton(text)
    button.setMaximumWidth(32)
    button.setToolTip(tooltip)
    return button


class ChannelRow(QFrame):
    """Base class for one channel; subclasses fill ``self.form``."""

    remove_requested = Signal(QWidget)

    def __init__(self, host: RowHost) -> None:
        super().__init__()
        self._host = host
        self.setFrameShape(QFrame.Shape.StyledPanel)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        self.form = QFormLayout()
        outer.addLayout(self.form, stretch=1)

        self._remove_btn = _small_button("−", "Remove this entry")
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        outer.addWidget(self._remove_btn)

    def set_removable(self, removable: bool) -> None:
        self._remove_btn.setEnabled(removable)

    def used_ports(self) -> set[int]:
        """TCP ports this row will listen on if enabled."""
        return set()

    def used_devices(self) -> set[str]:
        """Hardware identifiers (serial device, CAN channel, probe serial) this row claims."""
        return set()

    def suggest_ports(self) -> None:
        """Called once after the row is added: pick TCP ports no other row uses."""


class SerialRow(ChannelRow):
    def __init__(self, host: RowHost) -> None:
        super().__init__(host)

        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.port_combo.lineEdit().setPlaceholderText("disabled")

        self.baud_edit = QLineEdit(str(DEFAULT_SERIAL_BAUDRATE))
        self.baud_edit.setMaximumWidth(100)

        self.tcp_edit = QLineEdit(str(DEFAULT_SERIAL_TCP_PORT))
        self.tcp_edit.setMaximumWidth(80)

        refresh_btn = _small_button("⟳", "Refresh port list")
        refresh_btn.clicked.connect(self.refresh_ports)

        row = QHBoxLayout()
        row.addWidget(self.port_combo, stretch=2)
        row.addWidget(QLabel("Baud"))
        row.addWidget(self.baud_edit)
        row.addWidget(QLabel("TCP port"))
        row.addWidget(self.tcp_edit)
        row.addWidget(refresh_btn)
        self.form.addRow("Port:", row)

        self.refresh_ports()

    def suggest_ports(self) -> None:
        used = self._host.used_ports(exclude=self)
        self.tcp_edit.setText(str(next_free_port(DEFAULT_SERIAL_TCP_PORT, used)))

    def used_ports(self) -> set[int]:
        port = _optional_int_safe(self.tcp_edit)
        return {port} if port is not None else set()

    def used_devices(self) -> set[str]:
        port = self.port_combo.currentText().strip()
        return {f"serial:{port}"} if port else set()

    def to_config(self) -> SerialConfig | None:
        port = self.port_combo.currentText().strip()
        if not port:
            return None
        return SerialConfig(
            port=port,
            baudrate=_int_or(self.baud_edit, DEFAULT_SERIAL_BAUDRATE),
            tcp_port=_int_or(self.tcp_edit, DEFAULT_SERIAL_TCP_PORT),
        )

    @Slot()
    def refresh_ports(self) -> None:
        try:
            from serial.tools.list_ports import comports

            ports = [p.device for p in comports()]
        except ImportError:
            ports = []
        current = self.port_combo.currentText()
        self.port_combo.clear()
        self.port_combo.addItem("")
        self.port_combo.addItems(ports)
        if current:
            idx = self.port_combo.findText(current)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
            else:
                self.port_combo.setEditText(current)


class CanRow(ChannelRow):
    def __init__(self, host: RowHost) -> None:
        super().__init__(host)

        self.iface_combo = QComboBox()
        self.iface_combo.addItem(DISABLED)
        self.iface_combo.addItems(CAN_INTERFACES)
        self.iface_combo.currentTextChanged.connect(self._on_interface_changed)

        self.channel_edit = QLineEdit()
        self.channel_edit.setPlaceholderText("can0 / PCAN_USBBUS1 / 0")

        self.bitrate_edit = QLineEdit(str(DEFAULT_CAN_BITRATE))
        self.bitrate_edit.setMaximumWidth(100)

        self.tty_baud_edit = QLineEdit(str(DEFAULT_CAN_TTY_BAUDRATE))
        self.tty_baud_edit.setMaximumWidth(100)
        self.tty_baud_edit.setEnabled(False)

        self.tcp_edit = QLineEdit(str(DEFAULT_CAN_TCP_PORT))
        self.tcp_edit.setMaximumWidth(80)

        row1 = QHBoxLayout()
        row1.addWidget(self.iface_combo)
        row1.addWidget(QLabel("Channel"))
        row1.addWidget(self.channel_edit, stretch=1)
        row1.addWidget(QLabel("Bitrate"))
        row1.addWidget(self.bitrate_edit)
        row1.addWidget(QLabel("TCP port"))
        row1.addWidget(self.tcp_edit)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("TTY baud (slcan):"))
        row2.addWidget(self.tty_baud_edit)
        row2.addStretch()
        detect_btn = QPushButton("Detect hardware")
        detect_btn.clicked.connect(self._detect)
        row2.addWidget(detect_btn)

        self.form.addRow("Interface:", row1)
        self.form.addRow("", row2)

    def suggest_ports(self) -> None:
        used = self._host.used_ports(exclude=self)
        self.tcp_edit.setText(str(next_free_port(DEFAULT_CAN_TCP_PORT, used)))

    def _interface(self) -> str | None:
        text = self.iface_combo.currentText()
        return None if text == DISABLED else text

    def used_ports(self) -> set[int]:
        port = _optional_int_safe(self.tcp_edit)
        return {port} if port is not None else set()

    def used_devices(self) -> set[str]:
        interface = self._interface()
        if interface is None:
            return set()
        return {f"can:{interface}:{self.channel_edit.text().strip() or '0'}"}

    def to_config(self) -> CanConfig | None:
        interface = self._interface()
        if interface is None:
            return None
        return CanConfig(
            interface=interface,
            channel=can_channel_or_default(interface, self.channel_edit.text().strip() or None),
            bitrate=_int_or(self.bitrate_edit, DEFAULT_CAN_BITRATE),
            tcp_port=_int_or(self.tcp_edit, DEFAULT_CAN_TCP_PORT),
            tty_baudrate=_int_or(self.tty_baud_edit, DEFAULT_CAN_TTY_BAUDRATE)
            if interface == "slcan"
            else None,
        )

    @Slot(str)
    def _on_interface_changed(self, text: str) -> None:
        self.tty_baud_edit.setEnabled(text == "slcan")

    @Slot()
    def _detect(self) -> None:
        self._host.log("[INFO] Scanning for CAN hardware...", _INFO_COLOR)
        try:
            configs = gather_all()
        except Exception as exc:
            self._host.log(f"[ERROR] Detection failed: {exc}", _ERROR_COLOR)
            return
        if not configs:
            self._host.log("[INFO] No CAN hardware detected.", _INFO_COLOR)
            return
        for cfg in configs:
            self._host.log(
                f"  {cfg.get('interface', '')}  {cfg.get('channel', '')}  {cfg.get('details', '')}",
                _RESULT_COLOR,
            )
        # Fill this row with the first adapter no other row is already bridging.
        taken = self._host.used_devices(exclude=self)
        free = [
            cfg
            for cfg in configs
            if f"can:{cfg.get('interface', '')}:{cfg.get('channel', '') or '0'}" not in taken
        ]
        if not free:
            self._host.log("[INFO] Every detected CAN channel is already in use.", _INFO_COLOR)
            return
        first = free[0]
        idx = self.iface_combo.findText(str(first.get("interface", "")))
        if idx >= 0:
            self.iface_combo.setCurrentIndex(idx)
        self.channel_edit.setText(str(first.get("channel", "")))


class ProbeRow(ChannelRow):
    def __init__(self, host: RowHost) -> None:
        super().__init__(host)

        self.kind_combo = QComboBox()
        self.kind_combo.addItem(DISABLED)
        self.kind_combo.addItems(list(PROBE_KINDS))
        self.kind_combo.currentTextChanged.connect(self._on_kind_changed)

        self.speed_edit = QLineEdit()
        self.speed_edit.setMaximumWidth(80)
        self.gdb_edit = QLineEdit()
        self.gdb_edit.setMaximumWidth(80)
        self.telnet_edit = QLineEdit()
        self.telnet_edit.setMaximumWidth(80)

        row1 = QHBoxLayout()
        row1.addWidget(self.kind_combo)
        row1.addStretch()
        row1.addWidget(QLabel("Speed (kHz)"))
        row1.addWidget(self.speed_edit)
        row1.addWidget(QLabel("GDB port"))
        row1.addWidget(self.gdb_edit)
        row1.addWidget(QLabel("Telnet port"))
        row1.addWidget(self.telnet_edit)

        self.jlink_device_edit = QLineEdit()
        self.jlink_device_edit.setPlaceholderText("TM4C123GH6PM")
        self.jlink_if_combo = QComboBox()
        self.jlink_if_combo.addItems(list(JLINK_INTERFACES))
        self.jlink_serial_edit = QLineEdit()
        self.jlink_serial_edit.setPlaceholderText("any")
        self.jlink_serial_edit.setMaximumWidth(120)

        jlink_row = QHBoxLayout()
        jlink_row.addWidget(QLabel("Device"))
        jlink_row.addWidget(self.jlink_device_edit, stretch=1)
        jlink_row.addWidget(QLabel("Interface"))
        jlink_row.addWidget(self.jlink_if_combo)
        jlink_row.addWidget(QLabel("Serial"))
        jlink_row.addWidget(self.jlink_serial_edit)

        # Editable list of STM32 parts; the validator keeps out non-ST names (TI, NXP, ...).
        self.stlink_device_combo = QComboBox()
        self.stlink_device_combo.setEditable(True)
        self.stlink_device_combo.addItems(list(STLINK_DEVICES))
        self.stlink_device_combo.setCurrentIndex(-1)
        self.stlink_device_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.stlink_device_combo.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(
                    "STM32[A-Z0-9]*", QRegularExpression.PatternOption.CaseInsensitiveOption
                ),
                self.stlink_device_combo,
            )
        )
        stlink_device_line = self.stlink_device_combo.lineEdit()
        if stlink_device_line is not None:
            stlink_device_line.setPlaceholderText("any STM32")
        self.stlink_device_combo.setToolTip(
            "Expected STM32 target (STMicroelectronics parts only). Optional; when set, the "
            "attached chip is read with STM32CubeProgrammer and the GDB server is not started "
            "if it differs. Pick one or type any STM32 part name."
        )
        self.stlink_device_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.stlink_if_combo = QComboBox()
        self.stlink_if_combo.addItems(list(STLINK_INTERFACES))
        self.stlink_connect_combo = QComboBox()
        self.stlink_connect_combo.addItems(list(STLINK_CONNECT_MODES))
        self.stlink_connect_combo.setToolTip(
            "normal: software reset, then halt. under-reset: connect while NRST is held, for "
            "firmware in a low-power mode or with the debug pins disabled ('Target not halted'). "
            "hotplug: attach to the running target without a reset."
        )
        self.stlink_ap_edit = QLineEdit()
        self.stlink_ap_edit.setPlaceholderText("auto")
        self.stlink_ap_edit.setMaximumWidth(50)
        self.stlink_ap_edit.setToolTip(
            "Debug access port of the core (ST-LINK_gdbserver -m). auto: 1 for STM32WBA, H5, "
            "H7R/S, C5 and V8 when the device is set, else 0"
        )
        self.stlink_serial_edit = QLineEdit()
        self.stlink_serial_edit.setPlaceholderText("any")
        self.stlink_serial_edit.setMinimumWidth(120)
        self.stlink_programmer_edit = QLineEdit()
        self.stlink_programmer_edit.setPlaceholderText("auto-detect")
        self.stlink_programmer_edit.setToolTip(
            "STM32CubeProgrammer bin folder (ST-LINK_gdbserver -cp); found automatically "
            "next to ST-LINK_gdbserver in STM32CubeCLT / STM32CubeIDE"
        )
        programmer_btn = _small_button("…", "Select the STM32CubeProgrammer bin folder")
        programmer_btn.clicked.connect(self._browse_programmer)

        stlink_row = QHBoxLayout()
        stlink_row.addWidget(QLabel("Device"))
        stlink_row.addWidget(self.stlink_device_combo, stretch=1)
        stlink_row.addWidget(QLabel("Interface"))
        stlink_row.addWidget(self.stlink_if_combo)
        stlink_row.addWidget(QLabel("Connect"))
        stlink_row.addWidget(self.stlink_connect_combo)
        stlink_row.addWidget(QLabel("AP"))
        stlink_row.addWidget(self.stlink_ap_edit)
        stlink_row.addWidget(QLabel("Serial"))
        stlink_row.addWidget(self.stlink_serial_edit, stretch=1)
        stlink_row.addWidget(QLabel("CubeProgrammer"))
        stlink_row.addWidget(self.stlink_programmer_edit, stretch=1)
        stlink_row.addWidget(programmer_btn)

        self.openocd_config_combo = QComboBox()
        self.openocd_config_combo.setEditable(True)
        self.openocd_config_combo.addItems(list(OPENOCD_PRESETS.values()))
        self.openocd_config_combo.setToolTip(
            "OpenOCD config script(s) passed with -f; separate several with ';'"
        )
        self.openocd_config_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.openocd_search_edit = QLineEdit()
        self.openocd_search_edit.setPlaceholderText("optional")

        openocd_row = QHBoxLayout()
        openocd_row.addWidget(QLabel("Config"))
        openocd_row.addWidget(self.openocd_config_combo, stretch=2)
        openocd_row.addWidget(QLabel("Search dir"))
        openocd_row.addWidget(self.openocd_search_edit, stretch=1)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("auto-detect (PATH / standard install folders)")
        browse_btn = _small_button(
            "…", "Select JLinkGDBServerCL, ST-LINK_gdbserver or openocd executable"
        )
        browse_btn.clicked.connect(self._browse_path)
        detect_btn = QPushButton("Detect probes")
        detect_btn.clicked.connect(self._detect)

        exe_row = QHBoxLayout()
        exe_row.addWidget(self.path_edit, stretch=1)
        exe_row.addWidget(browse_btn)
        exe_row.addWidget(detect_btn)

        self._common_widgets: list[QWidget] = [
            self.speed_edit,
            self.gdb_edit,
            self.telnet_edit,
            self.path_edit,
            browse_btn,
        ]
        self._kind_widgets: dict[str, list[QWidget]] = {
            "jlink": [self.jlink_device_edit, self.jlink_if_combo, self.jlink_serial_edit],
            "stlink": [
                self.stlink_device_combo,
                self.stlink_if_combo,
                self.stlink_connect_combo,
                self.stlink_ap_edit,
                self.stlink_serial_edit,
                self.stlink_programmer_edit,
                programmer_btn,
            ],
            "openocd": [self.openocd_config_combo, self.openocd_search_edit],
        }

        self.form.addRow("Type:", row1)
        self.form.addRow("J-Link:", jlink_row)
        self.form.addRow("ST-Link:", stlink_row)
        self.form.addRow("OpenOCD:", openocd_row)
        self.form.addRow("Executable:", exe_row)

        self._auto_ports: dict[QLineEdit, str] = {}
        self._on_kind_changed(self.kind_combo.currentText())

    def _kind(self) -> str | None:
        text = self.kind_combo.currentText()
        return text if text in PROBE_KINDS else None

    def _serial_edit(self) -> QLineEdit | None:
        return {"jlink": self.jlink_serial_edit, "stlink": self.stlink_serial_edit}.get(
            self._kind() or ""
        )

    def used_ports(self) -> set[int]:
        kind = self._kind()
        if kind is None:
            return set()
        gdb_default, telnet_default = DEFAULT_PORTS[kind]
        ports = {_optional_int_safe(self.gdb_edit) or gdb_default}
        if telnet_default is not None:
            ports.add(_optional_int_safe(self.telnet_edit) or telnet_default)
        return ports

    def used_devices(self) -> set[str]:
        edit = self._serial_edit()
        serial = edit.text().strip() if edit is not None else ""
        return {f"probe:{self._kind()}:{serial}"} if serial else set()

    def to_config(self, bind_address: str) -> ProbeConfig | None:
        kind = self._kind()
        if kind is None:
            return None

        configs = [
            c.strip() for c in self.openocd_config_combo.currentText().split(";") if c.strip()
        ]
        search_dir = self.openocd_search_edit.text().strip()
        serial_edit = self._serial_edit()
        if kind == "stlink":
            interface_combo = self.stlink_if_combo
            device = self.stlink_device_combo.currentText().strip().upper()
        else:
            interface_combo = self.jlink_if_combo
            device = self.jlink_device_edit.text().strip()
        return ProbeConfig(
            kind=kind,  # type: ignore[arg-type]
            executable=self.path_edit.text().strip() or None,
            bind_address=bind_address,
            gdb_port=_optional_int(self.gdb_edit),
            telnet_port=_optional_int(self.telnet_edit) if kind != "stlink" else None,
            speed_khz=_optional_int(self.speed_edit),
            device=device or None,
            interface=interface_combo.currentText(),
            serial_number=(serial_edit.text().strip() or None) if serial_edit else None,
            programmer_path=self.stlink_programmer_edit.text().strip() or None,
            connect_mode=self.stlink_connect_combo.currentText() if kind == "stlink" else "normal",
            access_port=_optional_int(self.stlink_ap_edit) if kind == "stlink" else None,
            configs=configs,
            search_dirs=[search_dir] if search_dir else [],
        )

    @Slot(str)
    def _on_kind_changed(self, text: str) -> None:
        # Ports this row picked for the previous kind are meaningless for the new one.
        for edit, value in self._auto_ports.items():
            if edit.text() == value:
                edit.clear()
        self._auto_ports.clear()

        enabled = text in PROBE_KINDS
        for widget in self._common_widgets:
            widget.setEnabled(enabled)
        for kind, widgets in self._kind_widgets.items():
            for widget in widgets:
                widget.setEnabled(text == kind)

        gdb_port, telnet_port = DEFAULT_PORTS.get(text, (None, None))
        self.gdb_edit.setPlaceholderText(str(gdb_port) if gdb_port is not None else "")
        self.telnet_edit.setPlaceholderText(str(telnet_port) if telnet_port is not None else "")
        if enabled and telnet_port is None:
            self.telnet_edit.clear()
            self.telnet_edit.setPlaceholderText("n/a")
            self.telnet_edit.setEnabled(False)
        speed_hint = {"jlink": str(DEFAULT_JLINK_SPEED_KHZ)}.get(text, "tool" if enabled else "")
        self.speed_edit.setPlaceholderText(speed_hint)

        if enabled:
            self._avoid_port_clash(text)

    def _avoid_port_clash(self, kind: str) -> None:
        """Give a second probe of the same kind its own ports instead of the defaults."""
        used = self._host.used_ports(exclude=self)
        gdb_default, telnet_default = DEFAULT_PORTS[kind]
        for edit, default in ((self.gdb_edit, gdb_default), (self.telnet_edit, telnet_default)):
            if default is None or edit.text().strip() or default not in used:
                continue
            value = str(next_free_port(default, used, step=10))
            edit.setText(value)
            self._auto_ports[edit] = value

    @Slot()
    def _browse_path(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select GDB server executable")
        if path:
            self.path_edit.setText(path)

    @Slot()
    def _browse_programmer(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select STM32CubeProgrammer bin folder")
        if path:
            self.stlink_programmer_edit.setText(path)

    @Slot()
    def _detect(self) -> None:
        self._host.log("[INFO] Scanning for debug probes...", _INFO_COLOR)
        kind = self._kind()
        path = self.path_edit.text().strip() or None
        try:
            probes = list_probes(
                jlink_path=path if kind == "jlink" else None,
                openocd_path=path if kind == "openocd" else None,
                stlink_path=path if kind == "stlink" else None,
            )
        except Exception as exc:
            self._host.log(f"[ERROR] Probe detection failed: {exc}", _ERROR_COLOR)
            return
        if not probes:
            self._host.log("[INFO] No debug probes or probe tools detected.", _INFO_COLOR)
            return
        for probe in probes:
            self._host.log(
                f"  {probe.get('probe', '')}  {probe.get('serial', '')}  "
                f"{probe.get('details', '')}",
                _RESULT_COLOR,
            )

        taken = self._host.used_devices(exclude=self)
        free = [
            p
            for p in probes
            if _is_selectable_serial(str(p.get("serial", "")))
            and f"probe:{p.get('probe')}:{p.get('serial')}" not in taken
        ]
        if kind is None:
            first = free[0] if free else probes[0]
            idx = self.kind_combo.findText(str(first.get("probe", "")))
            if idx >= 0:
                self.kind_combo.setCurrentIndex(idx)
            kind = self._kind()

        # Pin this row to a probe no other row uses, so rows added later pick a different one.
        serial_edit = self._serial_edit()
        if serial_edit is not None and not serial_edit.text().strip():
            free_same = [p for p in free if p.get("probe") == kind]
            if free_same:
                serial_edit.setText(str(free_same[0].get("serial", "")))


class ChannelSection(QGroupBox):
    """Group box with a "+" button that holds one or more rows of the same kind."""

    rows_changed = Signal()

    def __init__(self, title: str, add_text: str, factory: Callable[[], ChannelRow]) -> None:
        super().__init__(title)
        self._factory = factory
        self.rows: list[ChannelRow] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(4)
        layout.addLayout(self._rows_layout)

        footer = QHBoxLayout()
        self.add_btn = QPushButton(f"+  {add_text}")
        self.add_btn.setToolTip(f"{add_text}; each one gets its own TCP port")
        self.add_btn.clicked.connect(self._on_add_clicked)
        footer.addWidget(self.add_btn)
        footer.addStretch()
        layout.addLayout(footer)

        self.add_row(suggest_ports=False)

    def add_row(self, suggest_ports: bool = True) -> ChannelRow:
        row = self._factory()
        row.remove_requested.connect(self._remove_row)
        self.rows.append(row)
        self._rows_layout.addWidget(row)
        if suggest_ports:
            row.suggest_ports()
        self._update_removable()
        self.rows_changed.emit()
        return row

    @Slot()
    def _on_add_clicked(self) -> None:
        self.add_row()

    @Slot(QWidget)
    def _remove_row(self, row: QWidget) -> None:
        if len(self.rows) <= 1 or row not in self.rows:
            return
        self.rows.remove(row)  # type: ignore[arg-type]
        self._rows_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        self._update_removable()
        self.rows_changed.emit()

    def _update_removable(self) -> None:
        for row in self.rows:
            row.set_removable(len(self.rows) > 1)
