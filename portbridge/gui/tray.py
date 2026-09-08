"""SystemTrayIcon: keeps port-bridge accessible from the notification area."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


def create_app_icon() -> QIcon:
    """Create the application icon programmatically — no external file needed."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#1565C0"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    font = QFont("Arial", 17, QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "PB")
    painter.end()
    return QIcon(pixmap)


class SystemTrayIcon(QSystemTrayIcon):
    """System-tray presence for port-bridge.

    Single-click or double-click toggles the main window.
    Right-click shows a context menu with Show/Hide and Quit.
    """

    def __init__(self, window: object, parent: QObject | None = None) -> None:
        super().__init__(create_app_icon(), parent)
        self._window = window

        menu = QMenu()
        self._toggle_action = menu.addAction("Hide")
        self._toggle_action.triggered.connect(self._toggle_window)
        menu.addSeparator()
        log_action = menu.addAction("Open Log File")
        log_action.triggered.connect(self._open_log)
        menu.addSeparator()
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(QApplication.quit)
        self.setContextMenu(menu)
        self.setToolTip("port-bridge")
        self.activated.connect(self._on_activated)

    def _open_log(self) -> None:
        from portbridge.gui.log_file import open_log_file  # noqa: PLC0415

        open_log_file()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle_window()

    def _toggle_window(self) -> None:
        from PySide6.QtWidgets import QWidget

        if not isinstance(self._window, QWidget):
            return
        if self._window.isVisible():
            self._window.hide()
            self._toggle_action.setText("Show")
        else:
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()
            self._toggle_action.setText("Hide")

    def notify_bridge_started(self) -> None:
        self.showMessage(
            "port-bridge", "Bridge is running.", QSystemTrayIcon.MessageIcon.Information, 2000
        )

    def notify_bridge_stopped(self) -> None:
        self.showMessage(
            "port-bridge", "Bridge stopped.", QSystemTrayIcon.MessageIcon.Information, 2000
        )
