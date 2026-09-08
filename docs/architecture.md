# Architecture

## Overview

```
┌──────────────────────────────────────────────────────────┐
│  port-bridge process                                     │
│                                                          │
│  ┌─────────────┐   asyncio loop   ┌──────────────────┐  │
│  │ Serial port │◄────────────────►│ TCP :5000        │  │
│  └─────────────┘                  └──────────────────┘  │
│                                                          │
│  ┌─────────────┐   asyncio loop   ┌──────────────────┐  │
│  │ CAN adapter │◄────────────────►│ TCP :5001        │  │
│  └─────────────┘                  └──────────────────┘  │
└──────────────────────────────────────────────────────────┘
         ▲                                   ▲
         │ USB / PCI                         │ TCP
         │                                   │
   Host hardware                     Docker / remote client
```

## asyncio event loop

Both bridges share a single `asyncio` event loop. Blocking hardware calls (`serial.read`, `bus.recv`, `bus.send`) run in the default `ThreadPoolExecutor` via `run_in_executor(None, ...)` so they never stall the event loop.

```
asyncio.run(main())
  ├── SerialOverTcpServer.start()
  │     ├── serial.Serial(port, baudrate)           ← blocking, done at startup
  │     └── asyncio.start_server(handle_client, ...)
  │           └── _handle_client(reader, writer)
  │                 ├── _serial_to_tcp()  ← run_in_executor(serial.read)
  │                 └── _tcp_to_serial()  ← run_in_executor(serial.write)
  └── CanBusOverTcpServer.start()
        ├── can.Bus(...)                            ← blocking, done at startup
        └── asyncio.start_server(handle_client, ...)
              └── _handle_client(reader, writer)
                    ├── _can_to_tcp()   ← run_in_executor(bus.recv)
                    └── _tcp_to_can()   ← run_in_executor(bus.send)
```

### Shutdown sequence

1. SIGINT / SIGTERM sets `stop_event`.
2. `await stop_event.wait()` returns.
3. `finally:` block calls `await srv.stop()` for each server in order.
4. `stop()` closes the TCP writer, closes the `asyncio.Server`, then shuts down the hardware adapter.

## GUI thread model

```
Main thread (Qt)                  Daemon thread (asyncio)
─────────────────                 ───────────────────────
QApplication.exec()               asyncio.run(bridge_main())
  QTimer(100ms) ──drain queue──►  logging.Handler ──put──► SimpleQueue
  Start button  ──────────────►  loop.call_soon_threadsafe(stop_event.set)... wait
  Stop button   ──────────────►  asyncio.run_coroutine_threadsafe(stop(), loop)
```

- The asyncio loop runs in a `threading.Thread(daemon=True)`.
- Log records are enqueued by a custom `QueueHandler` in the asyncio thread.
- A `QTimer` on the main thread drains the queue at 100 ms intervals and appends text to `QPlainTextEdit`.
- Qt signals from `BridgeController` notify the UI of `started`, `stopped`, and `error` events.

## CAN wire protocol

Each CAN frame is 16 bytes, little-endian (`struct` format `"<IBxxx8s"`):

```
Offset  Size  Field
0       4     CAN ID (uint32):
                bits 0–28 = arbitration ID
                bit  29   = RTR flag
                bit  30   = error flag
                bit  31   = extended-frame flag
4       1     DLC (0–8)
5       3     padding (zeroed)
8       8     data (zero-padded to 8 bytes)
```

The format is byte-compatible with Linux `struct can_frame`. Clients on any OS decode the same bytes.

## Module responsibilities

| Module | Responsibility |
|--------|---------------|
| `bridge_server.py` | CLI arg parsing; creates and starts servers; owns the stop event |
| `serial_server.py` | `SerialOverTcpServer`: pyserial ↔ TCP byte passthrough |
| `can_server.py` | `CanBusOverTcpServer`: python-can ↔ TCP 16-byte CAN frames |
| `candle_bus.py` | `CandleBus`: thin wrapper around `candle_driver` (Windows Candle API) |
| `list_can_interfaces.py` | Enumerate serial ports + CAN adapters across python-can, candle_driver, pyserial |
| `server_errors.py` | `BridgeServerError`, `HardwareUnavailableError`, `PortUnavailableError` |
| `gui/bridge_controller.py` | `BridgeController`: asyncio ↔ Qt bridge using daemon thread + `SimpleQueue` |
| `gui/main_window.py` | `MainWindow`: config panel, status indicators, log panel |
| `gui/__main__.py` | GUI entry point |
