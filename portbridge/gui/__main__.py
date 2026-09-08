"""GUI entry point: ``python -m portbridge.gui`` or ``port-bridge-gui``."""

from __future__ import annotations

import sys


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

    # Check for updates 3 seconds after startup so the window has rendered.
    checker = UpdateChecker(app)

    def _on_update_available(version: str, url: str) -> None:
        dlg = UpdateDialog(version, url, window)
        dlg.exec()

    checker.update_available.connect(_on_update_available)

    from PySide6.QtCore import QTimer

    QTimer.singleShot(3000, checker.check_in_background)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
