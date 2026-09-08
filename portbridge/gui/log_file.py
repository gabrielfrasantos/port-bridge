"""Persistent file logging for port-bridge.

Sets up a RotatingFileHandler so every run appends to a log file that
survives crashes and window closures. Call setup() once at startup.

Log locations
-------------
Windows : %APPDATA%\\port-bridge\\port-bridge.log
Linux   : $XDG_CACHE_HOME/port-bridge/port-bridge.log
          (falls back to ~/.cache/port-bridge/port-bridge.log)
macOS   : ~/Library/Logs/port-bridge/port-bridge.log
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 3  # keep port-bridge.log + .1 .2 .3


def log_dir() -> Path:
    """Return the platform-appropriate directory for the log file."""
    if sys.platform == "win32":
        app_data = os.environ.get("APPDATA", "")
        base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs"
    else:
        xdg = os.environ.get("XDG_CACHE_HOME", "")
        base = Path(xdg) if xdg else Path.home() / ".cache"
    d = base / "port-bridge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path() -> Path:
    return log_dir() / "port-bridge.log"


def setup() -> Path:
    """Attach a RotatingFileHandler to the root logger and install an
    uncaught-exception hook.  Returns the path to the log file."""
    path = log_path()

    handler = RotatingFileHandler(
        path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
        delay=True,
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET:
        root.setLevel(logging.DEBUG)

    _install_excepthook()

    logger.info("File logging active: %s", path)
    return path


def _install_excepthook() -> None:
    """Log unhandled exceptions to the file before Python prints them."""
    original = sys.excepthook

    def _hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: object,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            original(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("port-bridge").critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _hook


def open_log_file() -> None:
    """Open the log file in the system's default text viewer."""
    path = log_path()
    if not path.exists():
        logger.debug("Log file does not exist yet: %s", path)
        return
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as exc:
        logger.warning("Could not open log file: %s", exc)


def open_log_dir() -> None:
    """Open the directory containing the log file in the file manager."""
    d = log_dir()
    try:
        if sys.platform == "win32":
            os.startfile(str(d))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(d)], check=False)
        else:
            subprocess.run(["xdg-open", str(d)], check=False)
    except Exception as exc:
        logger.warning("Could not open log directory: %s", exc)
