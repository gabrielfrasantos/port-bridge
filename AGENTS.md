# port-bridge — Agent Rules (canonical)

Single source of truth for **Claude, Copilot, and sub-agents**. `CLAUDE.md` and `.github/copilot-instructions.md` point here.

Cross-platform Python bridge that exposes serial and CAN bus hardware over TCP, with an optional PySide6 GUI. Runs on Windows and Linux.

## Architecture

```
portbridge/
  bridge_server.py      — CLI entry point: parse args, start servers, wait for SIGINT/SIGTERM
  serial_server.py      — SerialOverTcpServer: pyserial ↔ TCP byte stream (asyncio)
  can_server.py         — CanBusOverTcpServer: python-can ↔ TCP CAN frames (asyncio)
  candle_bus.py         — CandleBus: candle_driver wrapper (Windows Candle API)
  list_can_interfaces.py — detect serial ports + CAN adapters on the host
  server_errors.py      — BridgeServerError, HardwareUnavailableError, PortUnavailableError
  gui/
    __main__.py         — GUI entry point
    main_window.py      — MainWindow (QMainWindow): config panel + log panel + status indicators
    bridge_controller.py — asyncio ↔ Qt bridge: runs event loop in a daemon thread
```

## asyncio design

- All bridge code is `async`/`await` with `asyncio.run()` as the root.
- Blocking hardware calls (`serial.read`, `bus.recv`, `bus.send`) run in `asyncio.get_running_loop().run_in_executor(None, ...)` — never block the event loop directly.
- Shutdown: a `stop_event = asyncio.Event()` is set by signal handlers; all servers `await srv.stop()` in the `finally` block.
- One TCP client per channel at a time; a second connection is immediately closed.

## GUI design

- GUI runs on the Qt main thread. The bridge asyncio loop runs in a `threading.Thread` (daemon).
- Communication Qt→asyncio: `asyncio.run_coroutine_threadsafe(coro, loop)`.
- Communication asyncio→Qt: a `queue.SimpleQueue` populated by a custom `logging.Handler`; a `QTimer(interval=100ms)` drains it into `QPlainTextEdit` on the main thread.
- No `qasync` dependency — the thread-based pattern is simpler and already proven in the original C++ `BridgeController`.

## Cross-platform constraints

| Feature | Linux | Windows |
|---------|-------|---------|
| SocketCAN | `socketcan` interface | not available |
| Candle API | not available | `candle_driver` |
| Serial | `/dev/tty*` | `COM*` |
| SIGTERM handler | `loop.add_signal_handler` | `signal.signal` fallback |

- Never assume a specific path separator, device naming, or signal API. Guard with `sys.platform` or `try/except`.
- `candle_driver` is imported lazily and guarded with `try/except ImportError`.

## Style

- PEP 8. Line length 100. 4-space indent.
- Type annotations on all public functions and methods. `from __future__ import annotations` at the top of every module.
- `ruff` for linting/formatting (authoritative). `mypy --strict` for type checking.
- No comments except non-obvious *why*. No `TODO`/`FIXME`.
- Prefer `pathlib.Path` over `os.path`. Prefer `logging` over `print`.

## Error handling

- No exceptions propagated to the user as tracebacks. Catch at the boundary and log with `logger.error(...)` or raise `BridgeServerError` subclasses.
- `asyncio.CancelledError` must always be re-raised or left to propagate — never swallowed silently.
- Use `sys.exit(1)` only in the CLI entry point (`bridge_server.main`), never inside library code.

## Testing

- `pytest` with `unittest.IsolatedAsyncioTestCase` for async tests.
- Stub all hardware dependencies at the module level via `sys.modules` patching — no real serial port or CAN adapter required.
- `StrictMock`-equivalent discipline: use `unittest.mock.Mock` with explicit `spec=` or `autospec=True` where possible.
- Tests must pass with no physical hardware present.
- Run: `python -m pytest tests/` or `python -m unittest discover -s tests`.

## Packaging

- `pyproject.toml` with `setuptools` build backend.
- Core dependencies: `pyserial`, `python-can`, `gs_usb`, `libusb`, `libusb-package`.
- Optional `[gui]` extra: `PySide6>=6.6`.
- Entry points: `port-bridge` (CLI) and `port-bridge-gui` (GUI).

## Release

- Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `ci:`, etc.).
- `release-please` automates changelog, version bump in `pyproject.toml`, and GitHub release.
- PyPI publish via OIDC trusted publisher — no long-lived tokens.

## Agent routing

- **analyst** — investigate bugs, trace asyncio shutdown issues, audit cross-platform gaps; read-only
- **planner** — design new features (e.g. GUI panels, new CAN backend, protocol changes)
- **executor** — implement planned changes, fix bugs with a clear root cause
- **reviewer** — review PRs against these rules

## Assistant behavior

- Minimal prose. Report file paths + pass/fail.
- Don't add GUI-only dependencies to the core install; put them in `[gui]` extras.
- Don't block the asyncio event loop — always use `run_in_executor` for blocking I/O.
