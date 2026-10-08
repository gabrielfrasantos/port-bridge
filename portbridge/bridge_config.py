"""Bridge configuration shared by the CLI and the GUI: any number of serial, CAN and probe channels.

``parse_spec`` and the ``*_from_spec`` helpers turn the CLI's ``--add-serial``, ``--add-can`` and
``--add-probe`` values (``key=value,key=value``) into configs; ``find_problems`` rejects
out-of-range values and channels that would fight over the same TCP port or device before anything
starts.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .probe_server import (
    JLINK_INTERFACES,
    OPENOCD_PRESETS,
    PROBE_KINDS,
    RTT_STLINK_HINT,
    STLINK_CONNECT_MODES,
    STLINK_INTERFACES,
    STLINK_MAX_ACCESS_PORT,
    ProbeConfig,
    normalize_st_device,
)

DEFAULT_SERIAL_BAUDRATE = 921600
DEFAULT_SERIAL_TCP_PORT = 5000
DEFAULT_CAN_BITRATE = 125000
DEFAULT_CAN_TTY_BAUDRATE = 115200
DEFAULT_CAN_TCP_PORT = 5001

# Interfaces whose channel is a device index and may be omitted (defaults to the first device).
CAN_INDEXED_INTERFACES = ("gs_usb", "candle")
# Interfaces whose channel is a serial device that no serial bridge may open at the same time.
CAN_SERIAL_INTERFACES = ("slcan",)

MAX_TCP_PORT = 65535


@dataclass
class SerialConfig:
    port: str
    baudrate: int = DEFAULT_SERIAL_BAUDRATE
    tcp_port: int = DEFAULT_SERIAL_TCP_PORT


@dataclass
class CanConfig:
    interface: str
    channel: str
    bitrate: int = DEFAULT_CAN_BITRATE
    tcp_port: int = DEFAULT_CAN_TCP_PORT
    tty_baudrate: int | None = None


@dataclass
class BridgeConfig:
    serials: list[SerialConfig] = field(default_factory=list)
    cans: list[CanConfig] = field(default_factory=list)
    probes: list[ProbeConfig] = field(default_factory=list)
    bind_address: str = "127.0.0.1"
    log_level: str = "INFO"

    @property
    def is_empty(self) -> bool:
        return not (self.serials or self.cans or self.probes)


def can_channel_or_default(interface: str, channel: str | None) -> str:
    """Return ``channel``, defaulting to ``"0"`` for indexed interfaces; raise if required."""
    if channel:
        return channel
    if interface in CAN_INDEXED_INTERFACES:
        return "0"
    raise ValueError(f"CAN interface '{interface}' requires a channel")


def find_conflicts(config: BridgeConfig) -> list[str]:
    """Describe every TCP port, serial device or CAN channel claimed by more than one channel."""
    claims: dict[str, list[str]] = {}

    def claim(key: str, owner: str) -> None:
        claims.setdefault(key, []).append(owner)

    for serial in config.serials:
        owner = f"serial {serial.port}"
        claim(f"TCP port {serial.tcp_port}", owner)
        claim(f"serial device {serial.port}", owner)
    for can in config.cans:
        owner = f"CAN {can.interface}:{can.channel}"
        claim(f"TCP port {can.tcp_port}", owner)
        claim(f"CAN channel {can.interface}:{can.channel}", owner)
        if can.interface in CAN_SERIAL_INTERFACES:
            claim(f"serial device {can.channel}", owner)
    for probe in config.probes:
        owner = f"{probe.kind} probe" + (f" {probe.serial_number}" if probe.serial_number else "")
        claim(f"TCP port {probe.resolved_gdb_port}", f"{owner} (GDB)")
        telnet = probe.resolved_telnet_port
        if telnet is not None:
            claim(f"TCP port {telnet}", f"{owner} (telnet)")
        if probe.rtt_port is not None:
            claim(f"TCP port {probe.rtt_port}", f"{owner} (RTT)")

    return [
        f"{resource} is used by {' and '.join(owners)}"
        for resource, owners in claims.items()
        if len(owners) > 1
    ]


def find_invalid_values(config: BridgeConfig) -> list[str]:
    """Describe every TCP port outside 1-65535, non-positive rate or speed and non-ST ST-LINK
    device."""
    problems: list[str] = []

    def port(owner: str, value: int | None) -> None:
        if value is not None and not 1 <= value <= MAX_TCP_PORT:
            problems.append(f"{owner}: TCP port {value} is outside 1-{MAX_TCP_PORT}")

    def positive(owner: str, what: str, value: int | None) -> None:
        if value is not None and value <= 0:
            problems.append(f"{owner}: {what} must be positive, got {value}")

    for serial in config.serials:
        owner = f"serial {serial.port}"
        port(owner, serial.tcp_port)
        positive(owner, "baud rate", serial.baudrate)
    for can in config.cans:
        owner = f"CAN {can.interface}:{can.channel}"
        port(owner, can.tcp_port)
        positive(owner, "bitrate", can.bitrate)
        positive(owner, "TTY baud rate", can.tty_baudrate)
    for probe in config.probes:
        owner = f"{probe.kind} probe"
        port(f"{owner} (GDB)", probe.resolved_gdb_port)
        port(f"{owner} (telnet)", probe.resolved_telnet_port)
        port(f"{owner} (RTT)", probe.rtt_port)
        positive(owner, "speed", probe.speed_khz)
        positive(owner, "RTT search size", probe.rtt_size)
        if probe.rtt_address is not None and probe.rtt_address < 0:
            problems.append(f"{owner}: RTT search address must not be negative")
        if probe.rtt_port is not None and probe.kind == "stlink":
            problems.append(f"{owner}: {RTT_STLINK_HINT}")
        if probe.access_port is not None and not 0 <= probe.access_port <= STLINK_MAX_ACCESS_PORT:
            problems.append(
                f"{owner}: access port must be 0-{STLINK_MAX_ACCESS_PORT}, got {probe.access_port}"
            )
        if probe.kind == "stlink" and probe.device:
            try:
                normalize_st_device(probe.device)
            except ValueError as exc:
                problems.append(f"{owner}: {exc}")
    return problems


def find_problems(config: BridgeConfig) -> list[str]:
    """Everything that would make the bridge fail to start: bad values first, then conflicts."""
    return find_invalid_values(config) + find_conflicts(config)


# ----------------------------------------------------------------------
# CLI specs: "key=value,key=value"
# ----------------------------------------------------------------------

SERIAL_SPEC_KEYS = ("port", "baud", "tcp")
CAN_SPEC_KEYS = ("interface", "channel", "bitrate", "tcp", "tty-baud")
PROBE_SPEC_KEYS = (
    "kind",
    "path",
    "gdb",
    "telnet",
    "rtt",
    "rtt-address",
    "rtt-size",
    "speed",
    "device",
    "interface",
    "serial",
    "programmer",
    "connect",
    "ap",
    "board",
    "config",
    "search",
)
_REPEATABLE_KEYS = ("config", "search")


def parse_spec(text: str, allowed_keys: Iterable[str]) -> dict[str, list[str]]:
    """Parse ``key=value,key=value``; only ``config`` and ``search`` may repeat."""
    allowed = tuple(allowed_keys)
    result: dict[str, list[str]] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        key, sep, value = part.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if not sep or not value:
            raise ValueError(f"expected key=value, got '{part}'")
        if key not in allowed:
            raise ValueError(f"unknown key '{key}' (allowed: {', '.join(allowed)})")
        if key in result and key not in _REPEATABLE_KEYS:
            raise ValueError(f"key '{key}' given more than once")
        result.setdefault(key, []).append(value)
    return result


def _one(spec: dict[str, list[str]], key: str) -> str | None:
    values = spec.get(key)
    return values[-1] if values else None


def _int(spec: dict[str, list[str]], key: str) -> int | None:
    value = _one(spec, key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"'{key}' must be an integer, got '{value}'") from None


def _int_auto(spec: dict[str, list[str]], key: str) -> int | None:
    """Like ``_int`` but accepts ``0x`` prefixes, for addresses and sizes."""
    value = _one(spec, key)
    if value is None:
        return None
    try:
        return int(value, 0)
    except ValueError:
        raise ValueError(f"'{key}' must be an integer (decimal or 0x hex), got '{value}'") from None


def _required(spec: dict[str, list[str]], key: str, what: str) -> str:
    value = _one(spec, key)
    if value is None:
        raise ValueError(f"{what} requires '{key}='")
    return value


def serial_from_spec(text: str) -> SerialConfig:
    spec = parse_spec(text, SERIAL_SPEC_KEYS)
    return SerialConfig(
        port=_required(spec, "port", "--add-serial"),
        baudrate=_int(spec, "baud") or DEFAULT_SERIAL_BAUDRATE,
        tcp_port=_int(spec, "tcp") or DEFAULT_SERIAL_TCP_PORT,
    )


def can_from_spec(text: str) -> CanConfig:
    spec = parse_spec(text, CAN_SPEC_KEYS)
    interface = _required(spec, "interface", "--add-can")
    return CanConfig(
        interface=interface,
        channel=can_channel_or_default(interface, _one(spec, "channel")),
        bitrate=_int(spec, "bitrate") or DEFAULT_CAN_BITRATE,
        tcp_port=_int(spec, "tcp") or DEFAULT_CAN_TCP_PORT,
        tty_baudrate=(_int(spec, "tty-baud") or DEFAULT_CAN_TTY_BAUDRATE)
        if interface == "slcan"
        else None,
    )


def probe_from_spec(text: str, bind_address: str) -> ProbeConfig:
    spec = parse_spec(text, PROBE_SPEC_KEYS)
    kind = _required(spec, "kind", "--add-probe")
    if kind not in PROBE_KINDS:
        raise ValueError(f"probe kind must be one of {', '.join(PROBE_KINDS)}, got '{kind}'")

    interface = (_one(spec, "interface") or "SWD").upper()
    interfaces = JLINK_INTERFACES if kind == "jlink" else STLINK_INTERFACES
    if kind != "openocd" and interface not in interfaces:
        raise ValueError(f"probe interface must be one of {', '.join(interfaces)}")

    configs = list(spec.get("config", []))
    board = _one(spec, "board")
    if board is not None:
        if board not in OPENOCD_PRESETS:
            raise ValueError(f"unknown OpenOCD board '{board}'")
        configs.insert(0, OPENOCD_PRESETS[board])

    device = _one(spec, "device")
    if kind == "jlink" and not device:
        raise ValueError("a jlink probe requires 'device=' (e.g. TM4C123GH6PM)")
    if kind == "stlink" and device:
        device = normalize_st_device(device)
    if kind == "openocd" and not configs:
        raise ValueError("an openocd probe requires 'board=' or 'config='")

    connect_mode = (_one(spec, "connect") or "normal").lower()
    if kind == "stlink" and connect_mode not in STLINK_CONNECT_MODES:
        raise ValueError(f"probe connect must be one of {', '.join(STLINK_CONNECT_MODES)}")
    if kind != "stlink" and _one(spec, "connect") is not None:
        raise ValueError("'connect=' applies to stlink probes only")
    if kind != "stlink" and _one(spec, "ap") is not None:
        raise ValueError("'ap=' applies to stlink probes only")
    if kind == "stlink" and _one(spec, "rtt") is not None:
        raise ValueError(f"'rtt=' is not supported for stlink probes: {RTT_STLINK_HINT}")
    if kind != "openocd" and (
        _one(spec, "rtt-address") is not None or _one(spec, "rtt-size") is not None
    ):
        raise ValueError("'rtt-address=' and 'rtt-size=' apply to openocd probes only")

    return ProbeConfig(
        kind=kind,
        executable=_one(spec, "path"),
        bind_address=bind_address,
        gdb_port=_int(spec, "gdb"),
        telnet_port=_int(spec, "telnet"),
        rtt_port=_int(spec, "rtt"),
        rtt_address=_int_auto(spec, "rtt-address"),
        rtt_size=_int_auto(spec, "rtt-size"),
        speed_khz=_int(spec, "speed"),
        device=device,
        interface=interface,
        serial_number=_one(spec, "serial"),
        programmer_path=_one(spec, "programmer"),
        connect_mode=connect_mode,
        access_port=_int(spec, "ap"),
        configs=configs,
        search_dirs=list(spec.get("search", [])),
    )
