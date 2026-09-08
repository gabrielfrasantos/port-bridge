# Supported Adapters

## CAN adapters

| Interface | Adapter examples | Linux | Windows | Notes |
|-----------|----------------|-------|---------|-------|
| `socketcan` | Any SocketCAN interface | Yes | No | Kernel native; no extra driver needed |
| `pcan` | PEAK PCAN-USB | Yes | Yes | Requires PEAK driver |
| `slcan` | CANable 1.x (slcan firmware) | Yes | Yes | Serial-line CAN; specify `--can-channel /dev/ttyACM0` or `COM3` |
| `gs_usb` | CANable 2.0 (candleLight firmware) | Yes | Yes | Requires WinUSB/Zadig on Windows; `libusb-package` handles DLL lookup |
| `candle` | CANable 2.0 (Candle API driver) | No | Yes | Windows only; no WinUSB swap needed; install CANgaroo |

### Selecting a channel

```bash
# socketcan — use the interface name
port-bridge --can-interface socketcan --can-channel can0

# pcan — use PCAN channel name
port-bridge --can-interface pcan --can-channel PCAN_USBBUS1

# slcan — use the serial port the adapter is on
port-bridge --can-interface slcan --can-channel /dev/ttyACM0 --can-tty-baudrate 115200

# gs_usb / candle — use zero-based device index (default 0 if omitted)
port-bridge --can-interface gs_usb --can-channel 0
port-bridge --can-interface candle --can-channel 0
```

### Discovering available hardware

```bash
port-bridge --list-can
```

Output:

```
Interface  Channel        Details                       Source
---------  -------------  ----------------------------  -----------
socketcan  can0                                         python-can
slcan      /dev/ttyACM0   CANable USB to CAN adapter    serial-ports
```

## Serial adapters

Any adapter supported by `pyserial` works. Common examples:

| Adapter | Linux path | Windows path |
|---------|-----------|--------------|
| USB-UART (CP2102, CH340) | `/dev/ttyUSB0` | `COM3` |
| USB-CDC (STM32, TI) | `/dev/ttyACM0` | `COM5` |
| Bluetooth SPP | `/dev/rfcomm0` | `COM8` |

Baudrate defaults to 921600. Override with `--serial-baudrate`.

## Windows driver notes

### CANable 2.0 (gs_usb)

1. Install [Zadig](https://zadig.akeo.ie/) and replace the CANable driver with **WinUSB**.
2. `port-bridge` then uses `gs_usb` + `libusb` automatically.

### CANable 2.0 (candle — no driver swap)

1. Install [CANgaroo](https://github.com/HubertD/cangaroo) to get the Candle API driver.
2. Use `--can-interface candle`. No Zadig step needed.

### PEAK PCAN-USB

1. Install the PEAK System driver from [peak-system.com](https://www.peak-system.com/).
2. Use `--can-interface pcan --can-channel PCAN_USBBUS1`.
