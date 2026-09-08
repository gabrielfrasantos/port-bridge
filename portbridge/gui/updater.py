"""UpdateChecker: polls GitHub Releases for newer versions of port-bridge.

Runs in a daemon thread so it never blocks the Qt event loop.
Emits Qt signals to hand results back to the main thread.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
import webbrowser

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from portbridge import __version__

logger = logging.getLogger(__name__)

_RELEASES_API = "https://api.github.com/repos/gabrielfrasantos/port-bridge/releases/latest"
_REQUEST_TIMEOUT = 10


def _parse_version(v: str) -> tuple[int, ...]:
    """Convert 'v1.2.3' or '1.2.3' to (1, 2, 3). Returns (0,) on parse error."""
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except ValueError:
        return (0,)


class UpdateChecker(QObject):
    """Checks GitHub Releases in a background thread and signals the result.

    Signals
    -------
    update_available(latest_version, release_url):
        Emitted when a newer release is found. Both arguments are strings.
    check_done:
        Emitted after every check completes (whether or not an update was found),
        so callers can re-enable UI controls that were disabled during the check.
    """

    update_available: Signal = Signal(str, str)
    check_done: Signal = Signal()

    def check_in_background(self) -> None:
        threading.Thread(target=self._check, daemon=True, name="update-check").start()

    def _check(self) -> None:
        try:
            req = urllib.request.Request(
                _RELEASES_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"port-bridge/{__version__}",
                },
            )
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                data: dict[str, object] = json.loads(resp.read())

            tag = str(data.get("tag_name", "")).lstrip("v")
            html_url = str(data.get("html_url", ""))

            if not tag or not html_url:
                return

            if _parse_version(tag) > _parse_version(__version__):
                logger.info("Update available: v%s (current: v%s)", tag, __version__)
                self.update_available.emit(tag, html_url)
            else:
                logger.debug("No update found (latest: v%s)", tag)

        except urllib.error.URLError as exc:
            logger.debug("Update check network error: %s", exc)
        except Exception as exc:
            logger.debug("Update check failed: %s", exc)
        finally:
            self.check_done.emit()


class UpdateDialog(QDialog):
    """Asks the user whether to open the release page for the new version."""

    def __init__(
        self, latest_version: str, release_url: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._release_url = release_url

        self.setWindowTitle("Update Available")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>port-bridge {latest_version}</b> is available."))
        layout.addWidget(QLabel(f"You are running version {__version__}."))
        layout.addWidget(QLabel(""))
        layout.addWidget(QLabel("Open the release page to download the update?"))

        buttons = QDialogButtonBox()
        update_btn = QPushButton("Open Release Page")
        later_btn = QPushButton("Later")
        buttons.addButton(update_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(later_btn, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._open_release_page)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open_release_page(self) -> None:
        webbrowser.open(self._release_url)
        self.accept()
