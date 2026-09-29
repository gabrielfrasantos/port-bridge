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

    # List debug probes and probe tools detected on this machine
    python -m portbridge --list-probes
"""

import argparse
import asyncio
import logging
import signal
import sys

from .can_server import CanBusOverTcpServer
from .probe_server import (
    JLINK_INTERFACES,
    OPENOCD_PRESETS,
    PROBE_KINDS,
    DebugProbeServer,
    ProbeConfig,
)
from .serial_server import SerialOverTcpServer
from .server_errors import BridgeServerError

logger = logging.getLogger(__name__)


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
        "Run a J-Link or OpenOCD GDB server; clients flash and debug via GDB over TCP.",
    )
    probe_group.add_argument(
        "--probe",
        choices=PROBE_KINDS,
        help="Debug probe GDB server to run (jlink or openocd). Omit to disable.",
    )
    probe_group.add_argument(
        "--probe-path",
        help="Path to JLinkGDBServerCL / openocd executable or its folder "
        "(default: search PATH and standard install locations).",
    )
    probe_group.add_argument(
        "--probe-gdb-port",
        type=int,
        help="GDB server TCP port (default: 2331 for jlink, 3333 for openocd)",
    )
    probe_group.add_argument(
        "--probe-telnet-port",
        type=int,
        help="Telnet TCP port (default: 2333 for jlink, 4444 for openocd)",
    )
    probe_group.add_argument(
        "--probe-speed",
        type=int,
        help="Debug interface speed in kHz (default: 4000 for jlink, config default for openocd)",
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

    return ProbeConfig(
        kind=probe,
        executable=args.probe_path,
        bind_address=args.bind,
        gdb_port=args.probe_gdb_port,
        telnet_port=args.probe_telnet_port,
        speed_khz=args.probe_speed,
        device=args.jlink_device,
        interface=args.jlink_interface,
        serial_number=args.jlink_serial,
        configs=configs,
        search_dirs=list(args.openocd_search),
        commands=list(args.openocd_command),
    )


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
        )
        if args.output_json:
            print(_json.dumps(probes, indent=2))
        else:
            print(format_probe_table(probes))
        return

    probe_config = _build_probe_config(args)

    if args.serial_port is None and args.can_interface is None and probe_config is None:
        logger.error("At least one of --serial-port, --can-interface or --probe must be specified.")
        sys.exit(1)

    if args.can_interface is not None and args.can_channel is None:
        if args.can_interface in ("gs_usb", "candle"):
            args.can_channel = "0"
        else:
            logger.error("--can-channel is required when --can-interface is specified.")
            sys.exit(1)

    servers: list[SerialOverTcpServer | CanBusOverTcpServer | DebugProbeServer] = []

    try:
        if args.serial_port is not None:
            serial_srv = SerialOverTcpServer(
                serial_port=args.serial_port,
                baudrate=args.serial_baudrate,
                tcp_port=args.serial_tcp_port,
                bind_address=args.bind,
            )
            await serial_srv.start()
            servers.append(serial_srv)

        if args.can_interface is not None:
            can_srv = CanBusOverTcpServer(
                interface=args.can_interface,
                channel=args.can_channel,
                bitrate=args.can_bitrate,
                tcp_port=args.can_tcp_port,
                tty_baudrate=args.can_tty_baudrate if args.can_interface == "slcan" else None,
                bind_address=args.bind,
            )
            await can_srv.start()
            servers.append(can_srv)

        if probe_config is not None:
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
