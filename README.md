# port-bridge

A cross-platform bridge that exposes serial and CAN bus hardware over TCP, and runs J-Link, ST-LINK or
OpenOCD GDB servers for flashing and debugging, so clients running inside Docker containers or
on remote machines can access physical hardware on the host.

## How it works

```
┌─────────────────────────────────────────┐      ┌──────────────────────┐
│  Host machine (port-bridge running)     │      │  Docker / remote     │
│                                         │      │                      │
│  Serial device ──► TCP :5000 (serial)  │◄────►│  C++ / Python client │
│  CAN adapter   ──► TCP :5001 (CAN)     │◄────►│                      │
│  J-Link / ST-LINK / ICDI ──► TCP (GDB) │◄────►│  arm-none-eabi-gdb   │
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

## Debug probes (J-Link, ST-LINK, OpenOCD)

port-bridge can run the GDB server of a debug probe and expose it over TCP, so a client
in Docker or on another machine can flash and debug the target with `gdb`. The firmware
image is sent over the GDB connection, so it never has to be on the host.

The tool must be installed on the host:

| `--probe` | Tool | Found via |
|-----------|------|-----------|
| `jlink` | SEGGER J-Link Software Pack (`JLinkGDBServerCL`) | `--probe-path`, `PATH`, `C:\Program Files\SEGGER\JLink*`, `/opt/SEGGER/JLink*`, `/Applications/SEGGER/JLink*` |
| `stlink` | STMicroelectronics STM32CubeCLT or STM32CubeIDE (`ST-LINK_gdbserver`, plus STM32CubeProgrammer) | `--probe-path`, `PATH`, `C:\ST\STM32CubeCLT*`, `/opt/st/stm32cubeclt*`, `/opt/ST/STM32CubeCLT*`, STM32CubeIDE plugin folders |
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

# ST-LINK (Nucleo / Discovery on-board or standalone) — GDB on :61234
port-bridge --probe stlink
port-bridge --probe stlink --stlink-serial 066DFF485550755187121723 \
    --stlink-interface JTAG --probe-speed 1800 \
    --stlink-programmer "C:\ST\STM32CubeCLT_1.16.0\STM32CubeProgrammer\bin"

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

# ST-LINK
arm-none-eabi-gdb firmware.elf -batch \
    -ex "target extended-remote host.docker.internal:61234" \
    -ex "monitor reset" -ex load -ex "monitor reset" -ex detach
```

The telnet port (`4444` for OpenOCD, `2333` for J-Link) is exposed as well; ST-LINK_gdbserver
has no telnet port. J-Link can only listen on loopback or on all interfaces: any `--bind` other
than loopback makes it listen on all interfaces. ST-LINK_gdbserver has no bind option, so
`--bind` is not applied to it: its GDB port listens wherever the tool chooses, which may include
other machines on the network. port-bridge logs a warning when it starts one; use a firewall if
the port must stay private.

ST-LINK_gdbserver needs the STM32CubeProgrammer `bin` folder. port-bridge finds it next to
ST-LINK_gdbserver (STM32CubeCLT and STM32CubeIDE ship both) or in the standard
STM32CubeProgrammer install folder; pass `--stlink-programmer` to override.

### SEGGER RTT

RTT (Real-Time Transfer) streams the firmware's log output from target RAM over the debug
probe without halting the core. It is off by default; `--probe-rtt-port` serves **channel 0** on
that TCP port (19021 is the SEGGER convention) next to the GDB port, so a container reads it the
same way it reaches GDB:

```bash
# J-Link: the GDB server's own RTT telnet port
port-bridge --probe jlink --jlink-device NRF52840_XXAA --probe-rtt-port 19021 --bind 0.0.0.0

# OpenOCD (>= 0.11), also with an ST-LINK adapter; the firmware's _SEGGER_RTT block must lie in
# the searched RAM range (default 0x20000000, 0x10000 bytes)
port-bridge --probe openocd \
    --openocd-config interface/stlink.cfg --openocd-config target/stm32f4x.cfg \
    --probe-rtt-port 19021 --openocd-rtt-address 0x20000000 --openocd-rtt-size 0x20000 \
    --bind 0.0.0.0

# From the container
nc host.docker.internal 19021
```

| Probe | RTT |
|-------|-----|
| `jlink` | Supported (`-RTTTelnetPort`). The port only listens while the GDB server has a session with the target, so attach with GDB (or the J-Link tools) first. J-Link V7.70 was reported to keep this port on localhost even when "localhost only" was off; SEGGER planned a fix for V7.80c. port-bridge logs a warning for a non-loopback `--bind`, so update the J-Link Software Pack if the container cannot connect. |
| `openocd` | Supported (`rtt setup`, `rtt start`, `rtt server start`; needs OpenOCD 0.11 or newer). `--bind` applies to the RTT port. `--openocd-rtt-address` and `--openocd-rtt-size` set the RAM range searched for the control block. |
| `stlink` | **Not supported**: `ST-LINK_gdbserver` has no RTT. port-bridge refuses to start with `--probe-rtt-port`; run an `openocd` probe with `interface/stlink.cfg` instead, as above. |

Only channel 0 is forwarded. The RTT port needs its own TCP port like every other channel, and
port-bridge refuses to start if it clashes with another one.

| Flag | Default | Description |
|------|---------|-------------|
| `--probe` | — | `jlink`, `stlink` or `openocd`. Omit to disable. |
| `--probe-path` | auto | Tool executable or its folder |
| `--probe-gdb-port` | 2331 / 61234 / 3333 | GDB server TCP port (jlink / stlink / openocd) |
| `--probe-telnet-port` | 2333 / — / 4444 | Telnet TCP port (jlink / stlink has none / openocd) |
| `--probe-rtt-port` | off | Serve SEGGER RTT channel 0 on this TCP port (jlink and openocd; not stlink) |
| `--probe-speed` | 4000 / tool / cfg | Interface speed in kHz (jlink / ST-LINK default / openocd config default) |
| `--jlink-device` | — | Target device, e.g. `TM4C123GH6PM` (required for jlink) |
| `--jlink-interface` | SWD | `SWD` or `JTAG` |
| `--jlink-serial` | — | Select a J-Link by USB serial number |
| `--stlink-interface` | SWD | `SWD` or `JTAG` |
| `--stlink-serial` | — | Select an ST-LINK by serial number |
| `--stlink-connect` | normal | `normal`, `under-reset` (firmware in low-power mode / "Target not halted"; needs NRST wired) or `hotplug` (attach without reset) |
| `--stlink-ap` | auto | Debug access port of the core (`-m`); auto = 1 for STM32WBA, H5, H7R/S, C5, V8 when `--stlink-device` names one, else 0 |
| `--stlink-programmer` | auto | STM32CubeProgrammer `bin` folder |
| `--openocd-board` | — | Preset: `ek-tm4c123gxl`, `ek-tm4c1294xl` |
| `--openocd-config` | — | Config script (`-f`), repeatable |
| `--openocd-search` | — | Script search directory (`-s`), repeatable |
| `--openocd-command` | — | Extra command (`-c`) after the configs, repeatable |
| `--openocd-rtt-address` | 0x20000000 | Start of the RAM range OpenOCD searches for the RTT control block (with `--probe-rtt-port`) |
| `--openocd-rtt-size` | 0x10000 | Size of that range in bytes |
| `--list-probes` | — | Print detected probes and tools and exit (`--json` supported) |

## Multiple serial ports, CAN buses and probes

Each kind of channel can run any number of times. The plain flags (`--serial-port`,
`--can-interface`, `--probe`) define the first one; repeat `--add-serial`, `--add-can` and
`--add-probe` for the rest. Each takes `key=value` pairs separated by commas:

```bash
port-bridge \
    --serial-port COM3 \
    --add-serial port=COM4,baud=115200,tcp=5002 \
    --can-interface gs_usb --can-channel 0 \
    --add-can interface=gs_usb,channel=1,bitrate=500000,tcp=5003 \
    --probe stlink --stlink-serial 066DFF485550755187121723 \
    --add-probe kind=stlink,serial=0670FF485550755187121724,gdb=61244 \
    --add-probe kind=jlink,device=TM4C123GH6PM,serial=801012345
```

| Flag | Keys |
|------|------|
| `--add-serial` | `port` (required), `baud`, `tcp` |
| `--add-can` | `interface` (required), `channel`, `bitrate`, `tcp`, `tty-baud` |
| `--add-probe` | `kind` (required: `jlink`, `stlink`, `openocd`), `path`, `gdb`, `telnet`, `rtt`, `rtt-address`, `rtt-size`, `speed`, `device`, `interface`, `serial`, `programmer`, `connect`, `ap`, `board`, `config`, `search` (`config` and `search` repeat; `rtt` is for `jlink` and `openocd`, `rtt-address` / `rtt-size` for `openocd`) |

Every channel needs its own TCP port and device. port-bridge refuses to start when two
channels share a TCP port, a serial device or a CAN channel (an `slcan` channel counts as its
serial device), or when a TCP port is outside 1-65535 or a rate is not positive. Two probes of the same kind also
need different `gdb` (and `telnet`, and `rtt` when used) ports and should name a `serial` so each
server picks its own probe.

In the GUI, the **+ Add serial port**, **+ Add CAN bus** and **+ Add debug probe** buttons
add a row to each section and **−** removes one. A new row starts on a free TCP port. A second
probe of the same type starts on ports offset by 10, and **Detect probes** fills in the serial
number of a probe no other row is using, so each row stays on its own probe.

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
and flash. An RTT port exposes the firmware's log output and accepts input on channel 0, so
treat it the same way.

## Running tests

```bash
pytest
```

Tests stub all hardware dependencies and run without any physical devices.

## License

MIT
