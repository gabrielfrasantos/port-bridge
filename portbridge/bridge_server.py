#!/usr/bin/env python3
"""
Bridge Server — exposes local serial and CAN hardware over TCP.

Run on the host machine with direct hardware access. C++ clients inside
a Docker container connect via TCP to access the hardware transparently.

Usage examples:
    # Serial only
    python -m portbridge --serial-port COM3

    # CAN only (PCAN on Windows)
    python -m portbridge --can-interface pcan --can-channel PCAN_USBBUS1

    # CAN with CANable (slcan) on Linux
    python -m portbridge --can-interface slcan --can-channel /dev/ttyACM0

    # CAN with CANable (slcan) on Windows
    python -m portbridge --can-interface slcan --can-channel COM3

    # CAN with CANable (gs_usb / candleLight) on Windows/Linux
    python -m portbridge --can-interface gs_usb --can-channel 0

    # CAN with CANable (Candle API, no WinUSB driver swap needed, Windows)
    python -m portbridge --can-interface candle --can-channel 0

    # List all CAN interfaces/channels detected on this machine
    python -m portbridge --list-can
    python -m portbridge --list-can --json

    # Both serial and CAN (SocketCAN on Linux)
    python -m portbridge \\
        --serial-port /dev/ttyACM0 --serial-baudrate 921600 \\
        --can-interface socketcan --can-channel can0 --can-bitrate 500000

    # OpenOCD GDB server for a TM4C123 LaunchPad (on-board ICDI), GDB on :3333
    python -m portbridge --probe openocd --openocd-board ek-tm4c123gxl

    # SEGGER J-Link GDB server (J-Link Software Pack installed), GDB on :2331
    python -m portbridge --probe jlink --jlink-device TM4C123GH6PM

    # ST-LINK GDB server (STM32CubeCLT or STM32CubeIDE installed), GDB on :61234
    python -m portbridge --probe stlink

    # Same, but refuse to start unless the attached target is an STM32F446RE
    python -m portbridge --probe stlink --stlink-device STM32F446RE

    # Several channels of each kind: the plain flags define the first one, --add-* the rest
    python -m portbridge --serial-port COM3 \
        --add-serial port=COM4,baud=115200,tcp=5002 \
        --add-can interface=gs_usb,channel=1,bitrate=500000,tcp=5003 \
        --add-probe kind=stlink,serial=0670FF485550755187121723,gdb=61244

    # List debug probes and probe tools detected on this machine
    python -m portbridge --list-probes
"""

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Callable
from typing import TypeVar

from .bridge_config import (
    BridgeConfig,
    CanConfig,
    SerialConfig,
    can_channel_or_default,
    can_from_spec,
    find_problems,
    probe_from_spec,
    serial_from_spec,
)
from .can_server import CanBusOverTcpServer
from .probe_server import (
    JLINK_INTERFACES,
    OPENOCD_PRESETS,
    PROBE_KINDS,
    STLINK_INTERFACES,
    DebugProbeServer,
    ProbeConfig,
    normalize_st_device,
)
from .serial_server import SerialOverTcpServer
from .server_errors import BridgeServerError

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bridge serial and CAN hardware to TCP for remote access."
    )

    serial_group = parser.add_argument_group("Serial")
    serial_group.add_argument(
        "--serial-port",
        help="Serial port device (e.g. COM3, /dev/ttyACM0). Omit to disable.",
    )
    serial_group.add_argument(
        "--serial-baudrate", type=int, default=921600, help="Serial baudrate (default: 921600)"
    )
    serial_group.add_argument(
        "--serial-tcp-port",
        type=int,
        default=5000,
        help="TCP port for serial bridge (default: 5000)",
    )
    serial_group.add_argument(
        "--add-serial",
        action="append",
        default=[],
        metavar="SPEC",
        help="Add another serial bridge. Repeatable. SPEC is port=DEV[,baud=N][,tcp=N], "
        "e.g. port=COM4,baud=115200,tcp=5002",
    )

    can_group = parser.add_argument_group("CAN bus")
    can_group.add_argument(
        "--can-interface",
        help="python-can interface type (e.g. socketcan, pcan, slcan, gs_usb, candle). Omit to disable.",  # noqa: E501
    )
    can_group.add_argument(
        "--can-channel",
        default=None,
        help="CAN channel (e.g. can0, PCAN_USBBUS1, /dev/ttyACM0, COM3). Required when --can-interface is set.",  # noqa: E501
    )
    can_group.add_argument(
        "--can-bitrate", type=int, default=125000, help="CAN bitrate (default: 125000)"
    )
    can_group.add_argument(
        "--can-tty-baudrate",
        type=int,
        default=115200,
        help="Serial baudrate for slcan adapters like CANable (default: 115200)",
    )
    can_group.add_argument(
        "--can-tcp-port", type=int, default=5001, help="TCP port for CAN bridge (default: 5001)"
    )
    can_group.add_argument(
        "--add-can",
        action="append",
        default=[],
        metavar="SPEC",
        help="Add another CAN bridge. Repeatable. SPEC is interface=IF[,channel=CH]"
        "[,bitrate=N][,tcp=N][,tty-baud=N], e.g. interface=gs_usb,channel=1,tcp=5003",
    )
    can_group.add_argument(
        "--list-can",
        action="store_true",
        help="List all CAN interfaces and channels available on this machine, then exit.",
    )
    can_group.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="With --list-can or --list-probes: output as JSON array instead of a table.",
    )

    probe_group = parser.add_argument_group(
        "Debug probe",
        "Run a J-Link, ST-LINK or OpenOCD GDB server; clients flash and debug via GDB over TCP.",
    )
    probe_group.add_argument(
        "--probe",
        choices=PROBE_KINDS,
        help="Debug probe GDB server to run (jlink, stlink or openocd). Omit to disable.",
    )
    probe_group.add_argument(
        "--probe-path",
        help="Path to JLinkGDBServerCL / ST-LINK_gdbserver / openocd executable or its folder "
        "(default: search PATH and standard install locations).",
    )
    probe_group.add_argument(
        "--probe-gdb-port",
        type=int,
        help="GDB server TCP port (default: 2331 for jlink, 61234 for stlink, 3333 for openocd)",
    )
    probe_group.add_argument(
        "--probe-telnet-port",
        type=int,
        help="Telnet TCP port (default: 2333 for jlink, 4444 for openocd; stlink has none)",
    )
    probe_group.add_argument(
        "--probe-speed",
        type=int,
        help="Debug interface speed in kHz (default: 4000 for jlink, tool default otherwise)",
    )
    probe_group.add_argument(
        "--jlink-device",
        help="J-Link target device name (e.g. TM4C123GH6PM, TM4C1294NCPDT). Required for jlink.",
    )
    probe_group.add_argument(
        "--jlink-interface",
        choices=JLINK_INTERFACES,
        default="SWD",
        help="J-Link target interface (default: SWD)",
    )
    probe_group.add_argument(
        "--jlink-serial",
        help="Select a specific J-Link by USB serial number.",
    )
    probe_group.add_argument(
        "--stlink-interface",
        choices=STLINK_INTERFACES,
        default="SWD",
        help="ST-LINK target interface (default: SWD)",
    )
    probe_group.add_argument(
        "--stlink-device",
        metavar="STM32",
        help="Expected STM32 target (e.g. STM32F446RE). Optional; when set, port-bridge reads the "
        "attached chip with STM32CubeProgrammer and refuses to start if it differs. "
        "Only STM32 names are accepted.",
    )
    probe_group.add_argument(
        "--stlink-serial",
        help="Select a specific ST-LINK by serial number.",
    )
    probe_group.add_argument(
        "--stlink-programmer",
        metavar="DIR",
        help="STM32CubeProgrammer bin folder required by ST-LINK_gdbserver "
        "(default: next to ST-LINK_gdbserver or the standard install location).",
    )
    probe_group.add_argument(
        "--openocd-board",
        choices=sorted(OPENOCD_PRESETS),
        help="OpenOCD board preset (adds the matching board/*.cfg).",
    )
    probe_group.add_argument(
        "--openocd-config",
        action="append",
        default=[],
        metavar="FILE",
        help="OpenOCD config script (-f). Repeatable, e.g. interface/ti-icdi.cfg "
        "--openocd-config target/stellaris.cfg",
    )
    probe_group.add_argument(
        "--openocd-search",
        action="append",
        default=[],
        metavar="DIR",
        help="Extra OpenOCD script search directory (-s). Repeatable.",
    )
    probe_group.add_argument(
        "--openocd-command",
        action="append",
        default=[],
        metavar="CMD",
        help="Extra OpenOCD command (-c) run after the config scripts. Repeatable.",
    )
    probe_group.add_argument(
        "--add-probe",
        action="append",
        default=[],
        metavar="SPEC",
        help="Add another debug probe. Repeatable. SPEC is kind=jlink|stlink|openocd plus any of "
        "path, gdb, telnet, speed, device, interface, serial, programmer, board, config, search; "
        "e.g. kind=stlink,serial=066DFF48,gdb=61244",
    )
    probe_group.add_argument(
        "--list-probes",
        action="store_true",
        help="List debug probes and probe tools available on this machine, then exit.",
    )

    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="Address to listen on. Defaults to loopback; the bridge has no authentication or "
        "transport security, so use 0.0.0.0 only on a trusted, isolated network (default: 127.0.0.1)",  # noqa: E501
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    return parser.parse_args()


def _build_probe_config(args: argparse.Namespace) -> ProbeConfig | None:
    probe = getattr(args, "probe", None)
    if probe is None:
        return None

    if probe == "jlink" and not args.jlink_device:
        logger.error("--jlink-device is required with --probe jlink (e.g. TM4C123GH6PM).")
        sys.exit(1)

    configs: list[str] = list(args.openocd_config)
    if args.openocd_board:
        configs.insert(0, OPENOCD_PRESETS[args.openocd_board])
    if probe == "openocd" and not configs:
        logger.error("--openocd-board or --openocd-config is required with --probe openocd.")
        sys.exit(1)

    if probe == "stlink":
        interface = getattr(args, "stlink_interface", "SWD")
        serial_number = getattr(args, "stlink_serial", None)
        device = getattr(args, "stlink_device", None)
        if device:
            try:
                device = normalize_st_device(device)
            except ValueError as exc:
                logger.error("--stlink-device: %s", exc)
                sys.exit(1)
    else:
        interface = args.jlink_interface
        serial_number = args.jlink_serial
        device = args.jlink_device

    return ProbeConfig(
        kind=probe,
        executable=args.probe_path,
        bind_address=args.bind,
        gdb_port=args.probe_gdb_port,
        telnet_port=args.probe_telnet_port,
        speed_khz=args.probe_speed,
        device=device,
        interface=interface,
        serial_number=serial_number,
        programmer_path=getattr(args, "stlink_programmer", None),
        configs=configs,
        search_dirs=list(args.openocd_search),
        commands=list(args.openocd_command),
    )


def _build_bridge_config(args: argparse.Namespace) -> BridgeConfig:
    config = BridgeConfig(bind_address=args.bind, log_level=args.log_level)

    probe_config = _build_probe_config(args)

    if args.serial_port is not None:
        config.serials.append(
            SerialConfig(
                port=args.serial_port,
                baudrate=args.serial_baudrate,
                tcp_port=args.serial_tcp_port,
            )
        )

    if args.can_interface is not None:
        try:
            channel = can_channel_or_default(args.can_interface, args.can_channel)
        except ValueError:
            logger.error("--can-channel is required when --can-interface is specified.")
            sys.exit(1)
        config.cans.append(
            CanConfig(
                interface=args.can_interface,
                channel=channel,
                bitrate=args.can_bitrate,
                tcp_port=args.can_tcp_port,
                tty_baudrate=args.can_tty_baudrate if args.can_interface == "slcan" else None,
            )
        )

    if probe_config is not None:
        config.probes.append(probe_config)

    config.serials += _parse_specs(
        "--add-serial", getattr(args, "add_serial", None), serial_from_spec
    )
    config.cans += _parse_specs("--add-can", getattr(args, "add_can", None), can_from_spec)
    config.probes += _parse_specs(
        "--add-probe",
        getattr(args, "add_probe", None),
        lambda spec: probe_from_spec(spec, args.bind),
    )

    return config


def _parse_specs(flag: str, specs: list[str] | None, parse: Callable[[str], _T]) -> list[_T]:
    items: list[_T] = []
    for spec in specs or []:
        try:
            items.append(parse(spec))
        except ValueError as exc:
            logger.error("Invalid %s '%s': %s", flag, spec, exc)
            sys.exit(1)
    return items


async def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if getattr(args, "list_can", False):
        import json as _json  # noqa: PLC0415

        from .list_can_interfaces import format_table, gather_all  # noqa: PLC0415

        configs = gather_all()
        if args.output_json:
            print(_json.dumps(configs, indent=2))
        else:
            print(format_table(configs))
        return

    if getattr(args, "list_probes", False):
        import json as _json  # noqa: PLC0415

        from .probe_server import format_probe_table, list_probes  # noqa: PLC0415

        probe_path = getattr(args, "probe_path", None)
        probe = getattr(args, "probe", None)
        probes = list_probes(
            jlink_path=probe_path if probe == "jlink" else None,
            openocd_path=probe_path if probe == "openocd" else None,
            stlink_path=probe_path if probe == "stlink" else None,
        )
        if args.output_json:
            print(_json.dumps(probes, indent=2))
        else:
            print(format_probe_table(probes))
        return

    config = _build_bridge_config(args)

    if config.is_empty:
        logger.error(
            "At least one of --serial-port, --can-interface, --probe or --add-* must be specified."
        )
        sys.exit(1)

    problems = find_problems(config)
    if problems:
        for problem in problems:
            logger.error("Invalid configuration: %s", problem)
        sys.exit(1)

    servers: list[SerialOverTcpServer | CanBusOverTcpServer | DebugProbeServer] = []

    try:
        for serial in config.serials:
            serial_srv = SerialOverTcpServer(
                serial_port=serial.port,
                baudrate=serial.baudrate,
                tcp_port=serial.tcp_port,
                bind_address=config.bind_address,
            )
            await serial_srv.start()
            servers.append(serial_srv)

        for can in config.cans:
            can_srv = CanBusOverTcpServer(
                interface=can.interface,
                channel=can.channel,
                bitrate=can.bitrate,
                tcp_port=can.tcp_port,
                tty_baudrate=can.tty_baudrate,
                bind_address=config.bind_address,
            )
            await can_srv.start()
            servers.append(can_srv)

        for probe_config in config.probes:
            probe_srv = DebugProbeServer(probe_config)
            await probe_srv.start()
            servers.append(probe_srv)
    except BridgeServerError as exc:
        logger.error("Bridge server startup failed: %s", exc)
        for srv in reversed(servers):
            await srv.stop()
        sys.exit(1)
    except Exception:
        logger.exception("Unexpected bridge server startup failure")
        for srv in reversed(servers):
            await srv.stop()
        sys.exit(1)

    logger.info("Bridge server running. Press Ctrl+C to stop.")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop_event.set())

    try:
        await stop_event.wait()
    finally:
        logger.info("Shutting down...")
        for srv in servers:
            await srv.stop()


if __name__ == "__main__":
    asyncio.run(main())
