# Usage

## Installation

```bash
# Core (CLI only)
pip install port-bridge

# With GUI
pip install "port-bridge[gui]"
```

## CLI

```bash
# Serial only
port-bridge --serial-port COM3
port-bridge --serial-port /dev/ttyACM0 --serial-baudrate 921600

# CAN only
port-bridge --can-interface socketcan --can-channel can0
port-bridge --can-interface pcan --can-channel PCAN_USBBUS1
port-bridge --can-interface slcan --can-channel /dev/ttyACM0 --can-tty-baudrate 115200
port-bridge --can-interface gs_usb --can-channel 0 --can-bitrate 500000
port-bridge --can-interface candle --can-channel 0   # Windows, Candle API driver

# Both bridges at once
port-bridge \
    --serial-port /dev/ttyACM0 --serial-baudrate 921600 \
    --can-interface socketcan --can-channel can0 --can-bitrate 500000

# Discover available CAN hardware
port-bridge --list-can
port-bridge --list-can --json

# Bind on all interfaces (trusted/isolated network only — no auth)
port-bridge --serial-port COM3 --bind 0.0.0.0

# Verbose logging
port-bridge --serial-port COM3 --log-level DEBUG

# As a Python module
python -m portbridge --serial-port COM3
```

### All options

| Flag | Default | Description |
|------|---------|-------------|
| `--serial-port` | — | Serial device. Omit to disable serial bridge. |
| `--serial-baudrate` | 921600 | Baud rate |
| `--serial-tcp-port` | 5000 | TCP port for the serial bridge |
| `--can-interface` | — | python-can interface type. Omit to disable CAN bridge. |
| `--can-channel` | — | CAN channel (required when `--can-interface` is set) |
| `--can-bitrate` | 125000 | CAN bitrate in bits/s |
| `--can-tty-baudrate` | 115200 | TTY baud rate for `slcan` adapters |
| `--can-tcp-port` | 5001 | TCP port for the CAN bridge |
| `--bind` | 127.0.0.1 | Listen address |
| `--log-level` | INFO | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `--list-can` | — | Print detected CAN hardware and exit |
| `--list-can --json` | — | Same output as JSON |

## Debug probes (J-Link, OpenOCD)

port-bridge can run the GDB server of a debug probe and expose it over TCP, so a client
in Docker or on another machine can flash and debug the target with `gdb`. The firmware
image is sent over the GDB connection, so it never has to be on the host.

The tool must be installed on the host:

| `--probe` | Tool | Found via |
|-----------|------|-----------|
| `jlink` | SEGGER J-Link Software Pack (`JLinkGDBServerCL`) | `--probe-path`, `PATH`, `C:\Program Files\SEGGER\JLink*`, `/opt/SEGGER/JLink*`, `/Applications/SEGGER/JLink*` |
| `openocd` | OpenOCD (e.g. distro package, xPack OpenOCD on Windows) | `--probe-path`, `PATH` |

```bash
# TM4C123 LaunchPad (EK-TM4C123GXL) through its on-board ICDI — GDB on :3333
port-bridge --probe openocd --openocd-board ek-tm4c123gxl

# TM4C1294 Connected LaunchPad (EK-TM4C1294XL)
port-bridge --probe openocd --openocd-board ek-tm4c1294xl

# Custom TM4C12x board behind an ICDI (or any other OpenOCD adapter)
port-bridge --probe openocd \
    --openocd-config interface/ti-icdi.cfg --openocd-config target/stellaris.cfg

# SEGGER J-Link — GDB on :2331
port-bridge --probe jlink --jlink-device TM4C123GH6PM
port-bridge --probe jlink --jlink-device TM4C1294NCPDT \
    --jlink-interface JTAG --probe-speed 1000 --jlink-serial 801012345

# Serial + CAN + probe from one process
port-bridge --serial-port /dev/ttyACM0 \
    --can-interface socketcan --can-channel can0 \
    --probe openocd --openocd-board ek-tm4c123gxl

# Discover connected probes and installed tools
port-bridge --list-probes
port-bridge --list-probes --json
```

Flash from the client (e.g. inside Docker, with `--bind 0.0.0.0` on the host):

```bash
# OpenOCD
arm-none-eabi-gdb firmware.elf -batch \
    -ex "target extended-remote host.docker.internal:3333" \
    -ex "monitor reset halt" -ex load -ex "monitor reset run" -ex detach

# J-Link
arm-none-eabi-gdb firmware.elf -batch \
    -ex "target remote host.docker.internal:2331" \
    -ex "monitor reset" -ex load -ex "monitor reset" -ex "monitor go" -ex detach
```

The telnet port (`4444` for OpenOCD, `2333` for J-Link) is exposed as well. J-Link can only
listen on loopback or on all interfaces: any `--bind` other than loopback makes it listen on
all interfaces.

| Flag | Default | Description |
|------|---------|-------------|
| `--probe` | — | `jlink` or `openocd`. Omit to disable. |
| `--probe-path` | auto | Tool executable or its folder |
| `--probe-gdb-port` | 2331 / 3333 | GDB server TCP port (jlink / openocd) |
| `--probe-telnet-port` | 2333 / 4444 | Telnet TCP port (jlink / openocd) |
| `--probe-speed` | 4000 / cfg | Interface speed in kHz (jlink / openocd config default) |
| `--jlink-device` | — | Target device, e.g. `TM4C123GH6PM` (required for jlink) |
| `--jlink-interface` | SWD | `SWD` or `JTAG` |
| `--jlink-serial` | — | Select a J-Link by USB serial number |
| `--openocd-board` | — | Preset: `ek-tm4c123gxl`, `ek-tm4c1294xl` |
| `--openocd-config` | — | Config script (`-f`), repeatable |
| `--openocd-search` | — | Script search directory (`-s`), repeatable |
| `--openocd-command` | — | Extra command (`-c`) after the configs, repeatable |
| `--list-probes` | — | Print detected probes and tools and exit (`--json` supported) |

## GUI

```bash
port-bridge-gui
# or
python -m portbridge.gui
```

The GUI window shows:

- **Configuration panel** — serial port, baudrate, CAN interface/channel/bitrate, TCP ports, bind address
- **Debug probe panel** — J-Link or OpenOCD, J-Link device/interface/serial, OpenOCD config (TM4C LaunchPad presets), speed, GDB/telnet ports, tool path, **Detect probes**
- **Status row** — green/red indicators for serial bridge, CAN bridge and debug probe
- **Start / Stop** button
- **Log panel** — scrolling log of all bridge events (INFO level by default)
- **Log level** selector — switch between DEBUG, INFO, WARNING, ERROR without restart

### First launch

1. Open the GUI with `port-bridge-gui`.
2. Select your serial port from the dropdown (auto-detected) or type it in.
3. Select your CAN interface and channel (or leave blank to disable that bridge).
4. Optionally select a debug probe (`jlink` needs a device name such as `TM4C123GH6PM`;
   `openocd` needs a config such as `board/ek-tm4c123gxl.cfg`).
5. Adjust TCP ports if the defaults (5000/5001, 2331/3333) conflict with existing services.
6. Click **Start**. Status indicators turn green when the bridge is listening.
7. Click **Stop** to shut down cleanly. Hardware is released immediately.

## Using from Docker

Run `port-bridge` on the host and connect from inside Docker:

```bash
# On the host — bind so Docker can reach it
port-bridge --serial-port /dev/ttyACM0 --bind 0.0.0.0

# Inside Docker — connect to host.docker.internal
nc host.docker.internal 5000   # serial
arm-none-eabi-gdb fw.elf -ex "target extended-remote host.docker.internal:3333"   # OpenOCD
```

The C++ HIL test client in e-foc connects with `--bridge-host host.docker.internal --serial-port 5000 --can-port 5001`.
