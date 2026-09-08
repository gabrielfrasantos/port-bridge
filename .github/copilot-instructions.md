# GitHub Copilot Instructions — port-bridge

Canonical rules: **[AGENTS.md](../AGENTS.md)** (shared with Claude).

Essentials (full detail in AGENTS.md):
- **asyncio** — all bridge I/O is async; blocking hardware calls go in `run_in_executor`. Never block the event loop.
- **GUI** — PySide6 on main thread, asyncio in a daemon thread. Log messages via `queue.SimpleQueue` + `QTimer`. No `qasync`.
- **Cross-platform** — guard `candle_driver`, `socketcan`, and signal APIs. Serial: `COM*` on Windows, `/dev/tty*` on Linux.
- **Style** — PEP 8, 100-char lines, type annotations, `ruff` authoritative, `mypy --strict`.
- **Errors** — `BridgeServerError` subclasses at library boundary; `sys.exit(1)` only in CLI entry point.
- **Tests** — `pytest`, hardware stubbed via `sys.modules`. No physical devices needed.
- **Packaging** — core deps + optional `[gui]` extra for `PySide6>=6.6`. Entry points: `port-bridge`, `port-bridge-gui`.
- **Release** — Conventional Commits + `release-please` + PyPI OIDC.
- **Be terse** — minimal prose; report file paths + pass/fail.
