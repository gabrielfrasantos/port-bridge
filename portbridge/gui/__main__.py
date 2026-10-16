"""GUI entry point: ``python -m portbridge.gui`` or ``port-bridge-gui``."""

from __future__ import annotations

import signal
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


def main() -> None:
    try:
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon
    except ImportError:
        print(
            'PySide6 is required for the GUI. Install it with:\n    pip install "port-bridge[gui]"',
            file=sys.stderr,
        )
        sys.exit(1)

    # File logging must be set up before anything else so even early crashes
    # are captured on disk.
    from portbridge.gui import log_file
    from portbridge.gui.main_window import MainWindow
    from portbridge.gui.tray import SystemTrayIcon, create_app_icon
    from portbridge.gui.updater import UpdateChecker, UpdateDialog

    log_path = log_file.setup()

    app = QApplication(sys.argv)
    app.setApplicationName("port-bridge")
    app.setOrganizationName("gabrielfrasantos")
    app.setWindowIcon(create_app_icon())
    # Don't quit when the last window is hidden (we live in the tray).
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow(log_path=log_path)

    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = SystemTrayIcon(window, app)
        tray.show()
        window.set_tray(tray)
    else:
        # No system tray — closing the window should quit the app.
        app.setQuitOnLastWindowClosed(True)

    window.show()

    # Update checker — fires automatically 3 s after startup and on demand.
    checker = UpdateChecker(app)

    def _on_update_available(version: str, url: str) -> None:
        window._restore_check_button()  # re-enable before showing the dialog
        if isinstance(tray, SystemTrayIcon):
            tray._check_updates_action.setEnabled(True)
        dlg = UpdateDialog(version, url, window)
        dlg.exec()

    def _on_check_done() -> None:
        window._restore_check_button()
        if isinstance(tray, SystemTrayIcon):
            tray._check_updates_action.setEnabled(True)

    checker.update_available.connect(_on_update_available)
    checker.check_done.connect(_on_check_done)

    window.set_checker(checker)
    if isinstance(tray, SystemTrayIcon):
        tray.set_checker(checker)

    from PySide6.QtCore import QTimer

    QTimer.singleShot(3000, checker.check_in_background)

    # Every way out of the app (tray Quit, last window closed, signals) ends in
    # QApplication.quit(), so this is the one place the bridge and its probe children stop.
    app.aboutToQuit.connect(window.shutdown)
    _quit_on_signals(app)

    sys.exit(app.exec())


def _quit_on_signals(app: QApplication) -> None:
    from PySide6.QtCore import QTimer

    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGHUP"):
        signals.append(signal.SIGHUP)
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    for sig in signals:
        signal.signal(sig, lambda *_: app.quit())

    # Python signal handlers only run while the interpreter executes bytecode, which a
    # blocked Qt event loop never does; a no-op timer hands control back regularly.
    wake = QTimer(app)
    wake.timeout.connect(lambda: None)
    wake.start(250)


if __name__ == "__main__":
    main()
