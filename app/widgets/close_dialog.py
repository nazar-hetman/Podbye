"""Close-while-busy dialog.

Shown when the user closes the window while a scan / cleanup / AI job is still
running. Offers three outcomes:

    OUTCOME_QUIT        — stop the running work and exit the app
    OUTCOME_BACKGROUND  — hide to the system tray and keep working
    OUTCOME_CANCEL      — dismiss the dialog, leave the app open and visible
                          (this is what the titlebar ✕ / Esc map to)

The dialog never touches workers or the tray itself; it only reports the user's
choice. The caller acts on it. A "Don't ask again" checkbox lets the user
persist the choice as the default :data:`close_behavior` setting.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
)

from app.widgets.controls import TacticalCheckBox
from app.i18n import tr

# Outcome constants — what the user chose.
OUTCOME_CANCEL = "cancel"
OUTCOME_BACKGROUND = "background"
OUTCOME_QUIT = "quit"

# Maps a non-cancel outcome to the persisted `close_behavior` setting value.
_OUTCOME_TO_SETTING = {
    OUTCOME_BACKGROUND: "background",
    OUTCOME_QUIT: "quit",
}

_BACKGROUND_HELP = (
    "Podbye keeps running in the system tray — near the clock, usually under the "
    "▲ \"hidden icons\" arrow. Your work finishes there in the background.\n\n"
    "To fully close Podbye later, right-click the tray icon and choose Quit."
)


class CloseRunningDialog(QDialog):
    """Ask the user what to do when closing with work still running."""

    def __init__(self, activity_label: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Podbye is still working"))
        self.setModal(True)
        self.setMinimumWidth(460)

        self._outcome = OUTCOME_CANCEL
        self._activity_label = activity_label
        self._build_ui()

    # ── public API ────────────────────────────────────────────────

    @property
    def outcome(self) -> str:
        """One of the OUTCOME_* constants."""
        return self._outcome

    @property
    def remember_choice(self) -> bool:
        """True if the user ticked 'Don't ask again'."""
        return self._dont_ask_cb.isChecked()

    def persisted_setting(self) -> str | None:
        """The `close_behavior` value to save, or None if nothing to remember.

        Only a remembered non-cancel choice maps to a persisted default.
        """
        if not self.remember_choice:
            return None
        return _OUTCOME_TO_SETTING.get(self._outcome)

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(0)

        header = QLabel(tr("Podbye is still working"))
        header.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 15px; font-weight: bold; "
            "letter-spacing: 1px; margin-bottom: 6px;"
        )
        root.addWidget(header)

        body_text = (
            tr("{activity} is still running.").format(activity=self._activity_label)
            if self._activity_label
            else tr("A task is still running.")
        )
        body = QLabel(
            body_text + " " + tr("What would you like to do?")
        )
        body.setObjectName("Dim")
        body.setStyleSheet("font-size: 12px; margin-bottom: 14px;")
        body.setWordWrap(True)
        root.addWidget(body)

        # ── "Don't ask again" ─────────────────────────────────────
        self._dont_ask_cb = TacticalCheckBox(tr("Don't ask again — remember my choice"))
        self._dont_ask_cb.setStyleSheet("font-size: 12px;")
        root.addWidget(self._dont_ask_cb)
        root.addSpacing(14)

        # ── Button row ────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_quit = QPushButton(tr("Stop && Quit"))
        self._btn_quit.setObjectName("Subtle")
        self._btn_quit.clicked.connect(self._on_quit)
        btn_row.addWidget(self._btn_quit)

        btn_row.addStretch()

        # Info hint next to the primary action. Not U+24D8 (ⓘ): neither
        # bundled font has it and it drew as a .notdef box.
        info = QLabel("?")
        info.setToolTip(tr(_BACKGROUND_HELP))
        info.setCursor(Qt.WhatsThisCursor)
        info.setStyleSheet("font-size: 14px; padding: 0 4px;")
        btn_row.addWidget(info)

        self._btn_background = QPushButton(tr("Run in background"))
        self._btn_background.setObjectName("Primary")
        self._btn_background.setToolTip(tr(_BACKGROUND_HELP))
        self._btn_background.setDefault(True)
        self._btn_background.clicked.connect(self._on_background)
        btn_row.addWidget(self._btn_background)

        root.addLayout(btn_row)

    # ── handlers ──────────────────────────────────────────────────

    def _on_quit(self):
        self._outcome = OUTCOME_QUIT
        self.accept()

    def _on_background(self):
        self._outcome = OUTCOME_BACKGROUND
        self.accept()
