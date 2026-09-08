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

## GUI

```bash
port-bridge-gui
# or
python -m portbridge.gui
```

The GUI window shows:

- **Configuration panel** — serial port, baudrate, CAN interface/channel/bitrate, TCP ports, bind address
- **Status row** — green/red indicators for serial bridge and CAN bridge
- **Start / Stop** button
- **Log panel** — scrolling log of all bridge events (INFO level by default)
- **Log level** selector — switch between DEBUG, INFO, WARNING, ERROR without restart

### First launch

1. Open the GUI with `port-bridge-gui`.
2. Select your serial port from the dropdown (auto-detected) or type it in.
3. Select your CAN interface and channel (or leave blank to disable that bridge).
4. Adjust TCP ports if the defaults (5000/5001) conflict with existing services.
5. Click **Start**. Status indicators turn green when the bridge is listening.
6. Click **Stop** to shut down cleanly. Hardware is released immediately.

## Using from Docker

Run `port-bridge` on the host and connect from inside Docker:

```bash
# On the host — bind so Docker can reach it
port-bridge --serial-port /dev/ttyACM0 --bind 0.0.0.0

# Inside Docker — connect to host.docker.internal
nc host.docker.internal 5000   # serial
```

The C++ HIL test client in e-foc connects with `--bridge-host host.docker.internal --serial-port 5000 --can-port 5001`.
