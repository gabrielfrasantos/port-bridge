# port-bridge

A cross-platform bridge that exposes serial and CAN bus hardware over TCP, so clients running inside Docker containers or on remote machines can access physical hardware on the host.

## How it works

```
┌─────────────────────────────────────────┐      ┌──────────────────────┐
│  Host machine (port-bridge running)     │      │  Docker / remote     │
│                                         │      │                      │
│  Serial device ──► TCP :5000 (serial)  │◄────►│  C++ / Python client │
│  CAN adapter   ──► TCP :5001 (CAN)     │◄────►│                      │
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
(`127.0.0.1`, the default) unless you are on a fully isolated, trusted network.

## Running tests

```bash
pytest
```

Tests stub all hardware dependencies and run without any physical devices.

## License

MIT
