# port-bridge

A cross-platform bridge that exposes serial and CAN bus hardware over TCP, and runs J-Link or
OpenOCD GDB servers for flashing and debugging, so clients running inside Docker containers or
on remote machines can access physical hardware on the host.

## How it works

```
┌─────────────────────────────────────────┐      ┌──────────────────────┐
│  Host machine (port-bridge running)     │      │  Docker / remote     │
│                                         │      │                      │
│  Serial device ──► TCP :5000 (serial)  │◄────►│  C++ / Python client │
│  CAN adapter   ──► TCP :5001 (CAN)     │◄────►│                      │
│  J-Link / ICDI ──► TCP :2331/3333 (GDB)│◄────►│  arm-none-eabi-gdb   │
└─────────────────────────────────────────┘      └──────────────────────┘
```

Serial data is forwarded as a transparent byte stream. CAN frames use a fixed
16-byte SocketCAN-compatible wire format so no framing library is needed on
the client side.

## Supported hardware

| Interface | Adapter examples | Platform |
|-----------|-----------------|----------|
| `socketcan` | Any SocketCAN interface | Linux |
| `pcan` | PEAK PCAN-USB | Windows, Linux |
| `slcan` | CANable (slcan firmware) | Windows, Linux |
| `gs_usb` | CANable 2.0 (candleLight firmware) | Windows, Linux |
| `candle` | CANable 2.0 (Candle API driver) | Windows |

Serial ports work on `COM*` (Windows) and `/dev/tty*` (Linux) transparently via pyserial.

## Installation

```bash
pip install port-bridge
```

For development:

```bash
git clone https://github.com/gabrielfrasantos/port-bridge.git
cd port-bridge
pip install -e ".[dev]"
```

## Usage

```bash
# Serial only
port-bridge --serial-port COM3
port-bridge --serial-port /dev/ttyACM0 --serial-baudrate 921600

# CAN only
port-bridge --can-interface socketcan --can-channel can0
port-bridge --can-interface pcan --can-channel PCAN_USBBUS1
port-bridge --can-interface slcan --can-channel /dev/ttyACM0
port-bridge --can-interface gs_usb --can-channel 0
port-bridge --can-interface candle --can-channel 0   # Windows, no WinUSB swap needed

# Both serial and CAN
port-bridge \
    --serial-port /dev/ttyACM0 --serial-baudrate 921600 \
    --can-interface socketcan --can-channel can0 --can-bitrate 500000

# Discover available CAN hardware
port-bridge --list-can
port-bridge --list-can --json

# Bind on all interfaces (trusted network only — no auth or TLS)
port-bridge --serial-port COM3 --bind 0.0.0.0

# As a Python module
python -m portbridge --serial-port COM3
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--serial-port` | — | Serial device (`COM3`, `/dev/ttyACM0`). Omit to disable. |
| `--serial-baudrate` | 921600 | Serial baud rate |
| `--serial-tcp-port` | 5000 | TCP port for the serial bridge |
| `--can-interface` | — | python-can interface type. Omit to disable. |
| `--can-channel` | — | CAN channel (required when `--can-interface` is set) |
| `--can-bitrate` | 125000 | CAN bitrate in bits/s |
| `--can-tty-baudrate` | 115200 | TTY baud rate for `slcan` adapters |
| `--can-tcp-port` | 5001 | TCP port for the CAN bridge |
| `--bind` | 127.0.0.1 | Listen address |
| `--log-level` | INFO | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `--list-can` | — | Print detected CAN hardware and exit |
| `--list-can --json` | — | Same, as JSON |

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

## Wire protocol

**Serial bridge (default port 5000):** transparent byte passthrough — no framing.

**CAN bridge (default port 5001):** each frame is 16 bytes, little-endian:

```
Offset  Size  Field
0       4     CAN ID (uint32): bits 0-28 = arbitration ID,
                               bit 29 = RTR, bit 30 = error, bit 31 = extended-frame flag
4       1     DLC (0-8)
5       3     padding (zeroed)
8       8     data (zero-padded if DLC < 8)
```

The format string is `"<IBxxx8s"` — identical to Linux `struct can_frame`.

## Security note

The bridge has no authentication or transport security. Bind to loopback
(`127.0.0.1`, the default) unless you are on a fully isolated, trusted network. This matters
most for the debug-probe GDB port, which gives full read/write control of the target's memory
and flash.

## Running tests

```bash
pytest
```

Tests stub all hardware dependencies and run without any physical devices.

## License

MIT
