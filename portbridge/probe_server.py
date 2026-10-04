"""DebugProbeServer: runs a J-Link, ST-LINK or OpenOCD GDB server as a managed child process.

The GDB server itself owns the TCP ports; port-bridge only locates the tool, starts it
with ports and bind address matching the rest of the bridge, forwards its output to
``logging`` and terminates it on shutdown.

Clients flash and debug over the GDB remote protocol, so the firmware image never has to
exist on the host::

    arm-none-eabi-gdb fw.elf -batch \\
        -ex "target extended-remote host.docker.internal:3333" \\
        -ex "monitor reset halt" -ex load -ex "monitor reset run"

Supported probes:
    - SEGGER J-Link (J-Link Software Pack must be installed): kind=jlink
    - ST-LINK (ST-LINK_gdbserver from STM32CubeCLT or STM32CubeIDE): kind=stlink
    - OpenOCD (any adapter it supports, e.g. TI ICDI on Tiva LaunchPads): kind=openocd
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .server_errors import HardwareUnavailableError, PortUnavailableError, ToolNotFoundError

logger = logging.getLogger(__name__)

ProbeKind = Literal["jlink", "stlink", "openocd"]
PROBE_KINDS: tuple[ProbeKind, ...] = ("jlink", "stlink", "openocd")

# (GDB port, telnet port); ST-LINK_gdbserver has no telnet interface.
DEFAULT_PORTS: dict[str, tuple[int, int | None]] = {
    "jlink": (2331, 2333),
    "stlink": (61234, None),
    "openocd": (3333, 4444),
}
DEFAULT_JLINK_SPEED_KHZ = 4000
JLINK_INTERFACES = ("SWD", "JTAG")
STLINK_INTERFACES = ("SWD", "JTAG")

OPENOCD_PRESETS: dict[str, str] = {
    "ek-tm4c123gxl": "board/ek-tm4c123gxl.cfg",
    "ek-tm4c1294xl": "board/ek-tm4c1294xl.cfg",
}

_READY_PATTERNS: dict[str, re.Pattern[str]] = {
    "jlink": re.compile(r"Waiting for GDB connection", re.IGNORECASE),
    # ST-LINK_gdbserver prints "Waiting for debugger connection..." and "Waiting for connection
    # on port N..."; "Listening at" is the st-util wording. Any of them means GDB can connect.
    "stlink": re.compile(r"Waiting for (?:debugger )?connection|Listening at", re.IGNORECASE),
    "openocd": re.compile(r"Listening on port \d+ for gdb connections", re.IGNORECASE),
}

_DISPLAY_NAMES: dict[str, str] = {
    "jlink": "J-Link GDB server",
    "stlink": "ST-LINK GDB server",
    "openocd": "OpenOCD",
}

# (vendor id, product id or None for any) -> (probe kind, description)
_USB_PROBES: dict[tuple[int, int | None], tuple[str, str]] = {
    (0x1CBE, 0x00FD): ("openocd", "TI Stellaris/Tiva In-Circuit Debug Interface (ICDI)"),
    (0x0451, 0xBEF3): ("openocd", "TI XDS110"),
    (0x0483, 0x3748): ("stlink", "ST-LINK/V2"),
    (0x0483, 0x374B): ("stlink", "ST-LINK/V2-1"),
    (0x0483, 0x374E): ("stlink", "STLINK-V3"),
    (0x0483, 0x374F): ("stlink", "STLINK-V3"),
    (0x0483, 0x3753): ("stlink", "STLINK-V3"),
    (0x1366, None): ("jlink", "SEGGER J-Link"),
}

_TAIL_LINES = 10


@dataclass
class ProbeConfig:
    kind: ProbeKind
    executable: str | None = None
    bind_address: str = "127.0.0.1"
    gdb_port: int | None = None
    telnet_port: int | None = None
    speed_khz: int | None = None
    # J-Link / ST-LINK
    device: str | None = None
    interface: str = "SWD"
    serial_number: str | None = None
    # ST-LINK: STM32CubeProgrammer "bin" folder (auto-detected when None)
    programmer_path: str | None = None
    # OpenOCD
    configs: list[str] = field(default_factory=list)
    search_dirs: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    @property
    def resolved_gdb_port(self) -> int:
        return self.gdb_port if self.gdb_port is not None else DEFAULT_PORTS[self.kind][0]

    @property
    def resolved_telnet_port(self) -> int | None:
        return self.telnet_port if self.telnet_port is not None else DEFAULT_PORTS[self.kind][1]


def resolve_openocd_config(name: str) -> str:
    """Map a preset name (e.g. ``ek-tm4c123gxl``) to its config script; pass others through."""
    return OPENOCD_PRESETS.get(name, name)


# ----------------------------------------------------------------------
# Tool discovery
# ----------------------------------------------------------------------


def _natural_key(text: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def _jlink_install_dirs() -> list[Path]:
    if sys.platform == "win32":
        roots = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ]
    elif sys.platform == "darwin":
        roots = ["/Applications"]
    else:
        roots = ["/opt"]

    found: list[Path] = []
    for root in roots:
        try:
            found.extend(p for p in (Path(root) / "SEGGER").glob("JLink*") if p.is_dir())
        except OSError:
            continue

    # The unversioned "JLink" folder is what current installers create (and what the Linux
    # package symlinks to the latest version); prefer it, then the newest versioned folder.
    return sorted(found, key=lambda p: (p.name == "JLink", _natural_key(p.name)), reverse=True)


def _resolve_override(override: str, exe_name: str) -> str | None:
    path = Path(override)
    if path.is_dir():
        path = path / exe_name
    found = shutil.which(str(path))
    if found:
        return found
    return str(path) if path.is_file() else None


def _find_tool(
    exe_name: str,
    override: str | None,
    search_dirs: list[Path],
    display_name: str,
    hint: str,
) -> str:
    if override:
        resolved = _resolve_override(override, exe_name)
        if resolved is None:
            raise ToolNotFoundError(f"{display_name} not found at '{override}'")
        return resolved

    found = shutil.which(exe_name)
    if found:
        return found

    for directory in search_dirs:
        candidate = directory / exe_name
        if candidate.is_file():
            return str(candidate)

    raise ToolNotFoundError(f"{display_name} ('{exe_name}') not found. {hint}")


_STLINK_PLUGIN = "com.st.stm32cube.ide.mcu.externaltools.stlink-gdb-server.*"
_CUBEPROGRAMMER_PLUGIN = "com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.*"


def _glob_dirs(base: Path, patterns: list[str]) -> list[Path]:
    # Glob below a literal base: since Python 3.12, Windows matches every pattern part against
    # directory listings, so a base containing 8.3 short names (RUNNER~1) would never match.
    found: list[Path] = []
    for pattern in patterns:
        try:
            found.extend(p for p in base.glob(pattern) if p.is_dir())
        except (OSError, ValueError):
            continue
    unique = list(dict.fromkeys(found))
    return sorted(unique, key=lambda p: _natural_key(str(p)), reverse=True)


def _st_roots() -> list[Path]:
    if sys.platform == "win32":
        return [
            Path(r"C:\ST"),
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "STMicroelectronics",
        ]
    if sys.platform == "darwin":
        return [Path("/opt/ST"), Path("/Applications")]
    return [Path("/opt/st"), Path("/opt/ST")]


def _stlink_install_dirs() -> list[Path]:
    patterns = [
        "STM32CubeCLT*/STLink-gdb-server/bin",
        "stm32cubeclt*/STLink-gdb-server/bin",
        f"STM32CubeIDE*/STM32CubeIDE/plugins/{_STLINK_PLUGIN}/tools/bin",
        f"stm32cubeide*/plugins/{_STLINK_PLUGIN}/tools/bin",
        f"STM32CubeIDE*.app/Contents/Eclipse/plugins/{_STLINK_PLUGIN}/tools/bin",
    ]
    found: list[Path] = []
    for root in _st_roots():
        found += _glob_dirs(root, patterns)
    return list(dict.fromkeys(found))


def _cubeprogrammer_install_dirs() -> list[Path]:
    if sys.platform == "win32":
        roots = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        ]
        return [
            r / "STMicroelectronics" / "STM32Cube" / "STM32CubeProgrammer" / "bin" for r in roots
        ]
    if sys.platform == "darwin":
        return [
            Path(
                "/Applications/STMicroelectronics/STM32Cube/STM32CubeProgrammer/"
                "STM32CubeProgrammer.app/Contents/MacOs/bin"
            )
        ]
    return [
        Path.home() / "STMicroelectronics" / "STM32Cube" / "STM32CubeProgrammer" / "bin",
        Path("/usr/local/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin"),
        Path("/opt/st/STM32CubeProgrammer/bin"),
    ]


_JLINK_HINT = "Install the SEGGER J-Link Software Pack or pass its location with --probe-path."
_STLINK_HINT = "Install STM32CubeCLT (or STM32CubeIDE) or pass its location with --probe-path."
_CUBEPROGRAMMER_HINT = (
    "Install STM32CubeProgrammer (bundled with STM32CubeCLT) or pass its bin folder "
    "with --stlink-programmer."
)
_OPENOCD_HINT = "Install OpenOCD and put it on PATH, or pass its location with --probe-path."


def _jlink_exe_name(base: str) -> str:
    return f"{base}.exe" if sys.platform == "win32" else f"{base}Exe"


def find_jlink_gdb_server(override: str | None = None) -> str:
    """Locate the J-Link command-line GDB server executable."""
    return _find_tool(
        _jlink_exe_name("JLinkGDBServerCL"),
        override,
        _jlink_install_dirs(),
        "J-Link GDB server",
        _JLINK_HINT,
    )


def find_jlink_commander(override: str | None = None) -> str:
    """Locate J-Link Commander; ``override`` may point at the GDB server or its folder."""
    exe_name = "JLink.exe" if sys.platform == "win32" else "JLinkExe"
    if override and Path(override).is_file():
        override = str(Path(override).parent)
    return _find_tool(exe_name, override, _jlink_install_dirs(), "J-Link Commander", _JLINK_HINT)


def _stlink_exe_name() -> str:
    return "ST-LINK_gdbserver.exe" if sys.platform == "win32" else "ST-LINK_gdbserver"


def _cubeprogrammer_exe_name() -> str:
    return "STM32_Programmer_CLI.exe" if sys.platform == "win32" else "STM32_Programmer_CLI"


def find_stlink_gdb_server(override: str | None = None) -> str:
    """Locate ST's ST-LINK_gdbserver executable."""
    return _find_tool(
        _stlink_exe_name(),
        override,
        _stlink_install_dirs(),
        "ST-LINK GDB server",
        _STLINK_HINT,
    )


def find_stm32cubeprogrammer(override: str | None = None, gdbserver: str | None = None) -> str:
    """Locate the STM32CubeProgrammer ``bin`` folder that ST-LINK_gdbserver needs (``-cp``).

    Looks next to ``gdbserver`` first (STM32CubeCLT and STM32CubeIDE ship both tools side by
    side), then in the standalone STM32CubeProgrammer install locations.
    """
    exe_name = _cubeprogrammer_exe_name()
    if override:
        path = Path(override)
        if path.is_file():
            return str(path.parent)
        if (path / exe_name).is_file():
            return str(path)
        raise ToolNotFoundError(f"STM32CubeProgrammer not found at '{override}'")

    candidates: list[Path] = []
    if gdbserver:
        bin_dir = Path(gdbserver).resolve().parent
        candidates.append(bin_dir.parent.parent / "STM32CubeProgrammer" / "bin")
        plugins = bin_dir.parent.parent.parent
        candidates += _glob_dirs(plugins, [f"{_CUBEPROGRAMMER_PLUGIN}/tools/bin"])
    found = shutil.which(exe_name)
    if found:
        candidates.append(Path(found).parent)
    candidates += _cubeprogrammer_install_dirs()

    for directory in candidates:
        if (directory / exe_name).is_file():
            return str(directory)
    raise ToolNotFoundError(f"STM32CubeProgrammer ('{exe_name}') not found. {_CUBEPROGRAMMER_HINT}")


def find_openocd(override: str | None = None) -> str:
    """Locate the OpenOCD executable."""
    exe_name = "openocd.exe" if sys.platform == "win32" else "openocd"
    return _find_tool(exe_name, override, [], "OpenOCD", _OPENOCD_HINT)


def find_probe_tool(cfg: ProbeConfig) -> str:
    if cfg.kind == "jlink":
        return find_jlink_gdb_server(cfg.executable)
    if cfg.kind == "stlink":
        return find_stlink_gdb_server(cfg.executable)
    return find_openocd(cfg.executable)


# ----------------------------------------------------------------------
# Command builders
# ----------------------------------------------------------------------


def _is_loopback(address: str) -> bool:
    if address == "localhost":
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def build_jlink_command(cfg: ProbeConfig, executable: str) -> list[str]:
    if not cfg.device:
        raise ValueError("J-Link requires a target device name (e.g. TM4C123GH6PM)")
    interface = cfg.interface.upper()
    if interface not in JLINK_INTERFACES:
        raise ValueError(f"J-Link interface must be SWD or JTAG, got '{cfg.interface}'")

    cmd = [
        executable,
        "-device",
        cfg.device,
        "-if",
        interface,
        "-speed",
        str(cfg.speed_khz or DEFAULT_JLINK_SPEED_KHZ),
        "-port",
        str(cfg.resolved_gdb_port),
        "-telnetport",
        str(cfg.resolved_telnet_port),
    ]
    if cfg.serial_number:
        cmd += ["-select", f"USB={cfg.serial_number}"]
    cmd += ["-LocalhostOnly", "1" if _is_loopback(cfg.bind_address) else "0"]
    return cmd


def build_stlink_command(cfg: ProbeConfig, executable: str, programmer_dir: str) -> list[str]:
    interface = cfg.interface.upper()
    if interface not in STLINK_INTERFACES:
        raise ValueError(f"ST-LINK interface must be SWD or JTAG, got '{cfg.interface}'")

    # -e keeps the server alive across GDB sessions, matching J-Link and OpenOCD behaviour.
    cmd = [executable, "-p", str(cfg.resolved_gdb_port), "-cp", programmer_dir, "-e"]
    if interface == "SWD":
        cmd.append("-d")
    if cfg.serial_number:
        cmd += ["-i", cfg.serial_number]
    if cfg.speed_khz:
        cmd += ["--frequency", str(cfg.speed_khz)]
    return cmd


def build_openocd_command(cfg: ProbeConfig, executable: str) -> list[str]:
    if not cfg.configs:
        raise ValueError(
            "OpenOCD requires at least one config script "
            f"(e.g. {', '.join(OPENOCD_PRESETS.values())})"
        )

    cmd = [executable]
    for directory in cfg.search_dirs:
        cmd += ["-s", directory]
    cmd += [
        "-c",
        f"bindto {cfg.bind_address}",
        "-c",
        f"gdb_port {cfg.resolved_gdb_port}",
        "-c",
        f"telnet_port {cfg.resolved_telnet_port}",
        "-c",
        "tcl_port disabled",
    ]
    for config in cfg.configs:
        cmd += ["-f", resolve_openocd_config(config)]
    if cfg.speed_khz:
        cmd += ["-c", f"adapter speed {cfg.speed_khz}"]
    for command in cfg.commands:
        cmd += ["-c", command]
    return cmd


def build_probe_command(cfg: ProbeConfig, executable: str) -> list[str]:
    if cfg.kind == "jlink":
        return build_jlink_command(cfg, executable)
    if cfg.kind == "stlink":
        programmer_dir = find_stm32cubeprogrammer(cfg.programmer_path, executable)
        return build_stlink_command(cfg, executable, programmer_dir)
    return build_openocd_command(cfg, executable)


def _no_window_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _line_level(line: str) -> int:
    lowered = line.lstrip().lower()
    if lowered.startswith(("error", "fatal")):
        return logging.ERROR
    if lowered.startswith("warn"):
        return logging.WARNING
    if lowered.startswith("debug"):
        return logging.DEBUG
    return logging.INFO


def _connect_host(bind_address: str) -> str:
    if bind_address in ("", "0.0.0.0"):
        return "127.0.0.1"
    if bind_address == "::":
        return "::1"
    return bind_address


async def tcp_port_open(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.open_connection(host, port)
    except OSError:
        return False
    writer.close()
    with contextlib.suppress(OSError, RuntimeError):
        await writer.wait_closed()
    return True


# ----------------------------------------------------------------------
# Server
# ----------------------------------------------------------------------


class DebugProbeServer:
    def __init__(
        self,
        config: ProbeConfig,
        process_factory: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
        port_probe: Callable[[str, int], Awaitable[bool]] = tcp_port_open,
        tool_finder: Callable[[ProbeConfig], str] = find_probe_tool,
        startup_timeout: float = 15.0,
        stop_timeout: float = 5.0,
        poll_interval: float = 0.25,
    ):
        self.config = config
        self._process_factory = process_factory
        self._port_probe = port_probe
        self._tool_finder = tool_finder
        self._startup_timeout = startup_timeout
        self._stop_timeout = stop_timeout
        self._poll_interval = poll_interval
        self._name = _DISPLAY_NAMES[config.kind]
        self._output_logger = logging.getLogger(
            f"portbridge.probe.{config.kind}.{config.resolved_gdb_port}"
        )
        self._process: Any = None
        self._pump_task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._exited = asyncio.Event()
        self._tail: deque[str] = deque(maxlen=_TAIL_LINES)
        self._running = False
        self._stopping = False

    @property
    def is_running(self) -> bool:
        return self._running and not self._exited.is_set()

    async def start(self) -> None:
        cfg = self.config
        executable = self._tool_finder(cfg)
        try:
            argv = build_probe_command(cfg, executable)
        except ValueError as exc:
            raise HardwareUnavailableError(str(exc)) from exc

        host = _connect_host(cfg.bind_address)
        telnet_port = cfg.resolved_telnet_port
        for port in (cfg.resolved_gdb_port, telnet_port):
            if port is not None and await self._port_probe(host, port):
                raise PortUnavailableError(
                    f"Cannot start {self._name}: TCP port {port} is already in use"
                )

        if cfg.kind == "jlink" and not _is_loopback(cfg.bind_address):
            logger.warning(
                "%s cannot bind to a single address; listening on all interfaces", self._name
            )
        elif cfg.kind == "stlink":
            logger.warning(
                "%s has no bind option: --bind %s is not applied to GDB port %d, which may be "
                "reachable from other machines; restrict it with a firewall if needed",
                self._name,
                cfg.bind_address,
                cfg.resolved_gdb_port,
            )

        logger.info("Starting %s: %s", self._name, subprocess.list2cmdline(argv))
        try:
            self._process = await self._process_factory(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **_no_window_kwargs(),
            )
        except OSError as exc:
            raise HardwareUnavailableError(f"Cannot launch {self._name}: {exc}") from exc

        self._pump_task = asyncio.create_task(self._pump_output())
        try:
            done = await self._wait_until_ready(host, telnet_port)
        except BaseException:
            await self._terminate()
            raise

        if self._exited.is_set():
            await self._finish_pump()
            code = self._process.returncode
            self._process = None
            raise HardwareUnavailableError(
                f"{self._name} exited with code {code} during startup: {self._tail_text()}"
            )

        if not done:
            await self._terminate()
            raise HardwareUnavailableError(
                f"{self._name} did not become ready within {self._startup_timeout:g} s: "
                f"{self._tail_text()}"
            )

        self._running = True
        telnet_text = f", telnet port {telnet_port}" if telnet_port is not None else ""
        logger.info(
            "%s listening on %s: GDB port %d%s",
            self._name,
            cfg.bind_address,
            cfg.resolved_gdb_port,
            telnet_text,
        )

    async def _wait_until_ready(self, host: str, port: int | None) -> bool:
        # The GDB port is never polled: some servers accept only one connection and would
        # treat the probe as a debugger session. Without a telnet port, rely on the output.
        waiters: set[asyncio.Task[Any]] = {
            asyncio.create_task(self._ready.wait()),
            asyncio.create_task(self._exited.wait()),
        }
        if port is not None:
            waiters.add(asyncio.create_task(self._wait_for_port(host, port)))
        try:
            done, _ = await asyncio.wait(
                waiters, timeout=self._startup_timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in waiters:
                task.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
        return bool(done)

    async def stop(self) -> None:
        self._stopping = True
        try:
            await self._terminate()
        finally:
            self._running = False

    async def _terminate(self) -> None:
        proc = self._process
        if proc is None:
            return

        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), self._stop_timeout)
            except asyncio.TimeoutError:
                logger.warning("%s did not exit after terminate; killing it", self._name)
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()

        await self._finish_pump()
        self._process = None
        logger.info("%s stopped", self._name)

    async def _finish_pump(self) -> None:
        task = self._pump_task
        if task is None:
            return
        self._pump_task = None
        if not task.done():
            _, pending = await asyncio.wait({task}, timeout=1.0)
            for pending_task in pending:
                pending_task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _wait_for_port(self, host: str, port: int) -> None:
        while not await self._port_probe(host, port):
            await asyncio.sleep(self._poll_interval)

    async def _pump_output(self) -> None:
        proc = self._process
        stream: asyncio.StreamReader | None = proc.stdout
        ready_pattern = _READY_PATTERNS[self.config.kind]
        try:
            while stream is not None:
                try:
                    raw = await stream.readline()
                except ValueError:
                    raw = await stream.read(65536)
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                self._tail.append(line)
                self._output_logger.log(_line_level(line), "%s", line)
                if ready_pattern.search(line):
                    self._ready.set()
            await proc.wait()
        finally:
            self._exited.set()

        if self._running and not self._stopping:
            logger.error(
                "%s exited unexpectedly with code %s: %s",
                self._name,
                proc.returncode,
                self._tail_text(),
            )
            self._running = False

    def _tail_text(self) -> str:
        return " | ".join(self._tail) if self._tail else "(no output)"


# ----------------------------------------------------------------------
# Probe discovery
# ----------------------------------------------------------------------

_EMU_LINE = re.compile(r"J-Link\[(?P<index>\d+)\]:\s*(?P<rest>.+)")


def parse_jlink_emu_list(output: str) -> list[dict[str, Any]]:
    """Parse J-Link Commander ``ShowEmuList`` output into probe entries."""
    results: list[dict[str, Any]] = []
    for match in _EMU_LINE.finditer(output):
        fields: dict[str, str] = {}
        for part in match.group("rest").split(","):
            key, sep, value = part.partition(":")
            if sep:
                fields[key.strip().lower()] = value.strip()
        details = fields.get("productname", "J-Link")
        connection = fields.get("connection")
        if connection:
            details = f"{details} ({connection})"
        results.append(
            {
                "probe": "jlink",
                "serial": fields.get("serial number", ""),
                "details": details,
                "source": "jlink-commander",
            }
        )
    return results


def detect_jlink_probes(executable: str | None = None) -> list[dict[str, Any]]:
    """Enumerate connected J-Links via J-Link Commander's ``ShowEmuList``."""
    try:
        commander = find_jlink_commander(executable)
    except ToolNotFoundError as exc:
        logger.debug("%s", exc)
        return []

    try:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "showemulist.jlink"
            script.write_text("ShowEmuList\nexit\n", encoding="ascii")
            completed = subprocess.run(
                [commander, "-NoGui", "1", "-CommanderScript", str(script)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=15,
                check=False,
                **_no_window_kwargs(),
            )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("J-Link Commander failed: %s", exc)
        return []

    return parse_jlink_emu_list(completed.stdout + completed.stderr)


def detect_openocd_tool(executable: str | None = None) -> list[dict[str, Any]]:
    """Report the OpenOCD installation (OpenOCD cannot list adapters without a config)."""
    try:
        openocd = find_openocd(executable)
    except ToolNotFoundError as exc:
        logger.debug("%s", exc)
        return []

    version = ""
    try:
        completed = subprocess.run(
            [openocd, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            check=False,
            **_no_window_kwargs(),
        )
        lines = (completed.stdout + completed.stderr).strip().splitlines()
        version = lines[0].strip() if lines else ""
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("OpenOCD --version failed: %s", exc)

    return [
        {
            "probe": "openocd",
            "serial": "",
            "details": f"{version or 'OpenOCD'} at {openocd}",
            "source": "tool",
        }
    ]


def detect_usb_probes() -> list[dict[str, Any]]:
    """Scan USB for well-known debug adapters (ICDI, XDS110, ST-LINK, J-Link)."""
    try:
        import usb.core
        import usb.util
    except ImportError:
        logger.debug("pyusb not installed; skipping USB probe detection.")
        return []

    try:
        from .can_server import _ensure_libusb_backend

        _ensure_libusb_backend()
    except ImportError:
        pass

    try:
        devices = list(usb.core.find(find_all=True) or [])
    except Exception as exc:
        logger.debug("USB enumeration failed: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for dev in devices:
        vid = getattr(dev, "idVendor", None)
        pid = getattr(dev, "idProduct", None)
        match = _USB_PROBES.get((vid, pid)) or _USB_PROBES.get((vid, None))  # type: ignore[arg-type]
        if match is None:
            continue
        serial = ""
        try:
            if getattr(dev, "iSerialNumber", 0):
                serial = str(usb.util.get_string(dev, dev.iSerialNumber) or "")
        except Exception:
            logger.debug("Cannot read USB serial number", exc_info=True)
        kind, description = match
        results.append(
            {
                "probe": kind,
                "serial": serial,
                "details": f"{description} [{vid:04x}:{pid:04x}]",
                "source": "usb",
            }
        )
    return results


def detect_stlink_tool(executable: str | None = None) -> list[dict[str, Any]]:
    """Report the ST-LINK GDB server installation (probes themselves come from the USB scan)."""
    try:
        server = find_stlink_gdb_server(executable)
    except ToolNotFoundError as exc:
        logger.debug("%s", exc)
        return []
    return [
        {
            "probe": "stlink",
            "serial": "",
            "details": f"ST-LINK_gdbserver at {server}",
            "source": "tool",
        }
    ]


def list_probes(
    jlink_path: str | None = None,
    openocd_path: str | None = None,
    stlink_path: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate installed tools and connected debug probes."""
    jlinks = detect_jlink_probes(jlink_path)
    usb_probes = detect_usb_probes()
    if jlinks:
        usb_probes = [p for p in usb_probes if p["probe"] != "jlink"]
    return jlinks + detect_stlink_tool(stlink_path) + detect_openocd_tool(openocd_path) + usb_probes


PROBE_COLUMNS = (
    ("Probe", "probe"),
    ("Serial", "serial"),
    ("Details", "details"),
    ("Source", "source"),
)


def format_probe_table(probes: list[dict[str, Any]]) -> str:
    from .list_can_interfaces import format_table

    return format_table(
        probes,
        columns=PROBE_COLUMNS,
        empty_message="No debug probes or probe tools detected.",
    )
