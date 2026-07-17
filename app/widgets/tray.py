"""System-tray presence for Vigil.

A thin wrapper around :class:`QSystemTrayIcon` used when the user chooses to keep
the app running in the background while a scan / cleanup / AI job finishes. The
tray icon is created on demand (only while it is needed) and exposes two intents
back to the window:

    show_requested  — restore and raise the main window
    quit_requested  — really exit the app (stop workers, close)

It deliberately knows nothing about workers or settings; the window drives those.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

from app.i18n import tr


class VigilTray(QSystemTrayIcon):
    """Tray icon with a Show / Quit menu and quiet notifications."""

    show_requested = Signal()
    quit_requested = Signal()

    def __init__(self, icon: QIcon, parent=None):
        super().__init__(icon, parent)
        self.setToolTip("Vigil")
        self._build_menu()
        # Left-click / double-click on the tray icon restores the window.
        self.activated.connect(self._on_activated)

    # ── availability ──────────────────────────────────────────────

    @staticmethod
    def is_available() -> bool:
        """True when the OS exposes a usable system tray."""
        return QSystemTrayIcon.isSystemTrayAvailable()

    # ── menu ──────────────────────────────────────────────────────

    def _build_menu(self):
        menu = QMenu()
        self._show_action = menu.addAction(tr("Show Vigil"))
        self._show_action.triggered.connect(self.show_requested.emit)
        menu.addSeparator()
        self._quit_action = menu.addAction(tr("Quit Vigil"))
        self._quit_action.triggered.connect(self.quit_requested.emit)
        # Keep a reference so the menu is not garbage-collected.
        self._menu = menu
        self.setContextMenu(menu)

    def _on_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_requested.emit()

    # ── appearance / feedback ─────────────────────────────────────

    def update_icon(self, icon: QIcon):
        """Recolour the tray mark (e.g. after a theme change)."""
        self.setIcon(icon)

    def set_status(self, text: str):
        """Reflect current activity in the tray tooltip."""
        self.setToolTip(f"Vigil — {text}" if text else "Vigil")

    def notify(self, title: str, message: str):
        """Show a quiet, short-lived completion notification.

        Uses NoIcon and a brief timeout so it informs without demanding
        attention.
        """
        if not self.supportsMessages():
            return
        self.showMessage(
            title, message, QSystemTrayIcon.MessageIcon.NoIcon, 4000
        )
