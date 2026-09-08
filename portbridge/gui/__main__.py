"""GUI entry point: ``python -m portbridge.gui`` or ``port-bridge-gui``."""

from __future__ import annotations

import sys


def main() -> None:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "PySide6 is required for the GUI. Install it with:\n"
            '    pip install "port-bridge[gui]"',
            file=sys.stderr,
        )
        sys.exit(1)

    from portbridge.gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("port-bridge")
    app.setOrganizationName("gabrielfrasantos")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
