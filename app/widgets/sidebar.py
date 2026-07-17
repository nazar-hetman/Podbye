"""Sidebar navigation widget for Vigil — matches mockup design."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QApplication,
)
from PySide6.QtCore import Signal, Qt
from app.i18n import tr
from app.widgets.panels import apply_tactical_label
from app.widgets.logo import logo_pixmap
from app.themes.theme_manager import get_palette, theme_signaller


class SidebarButton(QPushButton):
    """A sidebar navigation button with icon + shortcut hint."""

    def __init__(self, text: str, icon_char: str = "", shortcut: str = "", parent=None):
        super().__init__(parent)
        self.screen_name = text
        self._icon_char = icon_char
        self._shortcut = shortcut
        self.setObjectName("SidebarBtn")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(34)
        self._setup_layout(text, icon_char, shortcut)

    def _setup_layout(self, text, icon_char, shortcut):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 14, 0)
        lay.setSpacing(10)

        icon = QLabel(icon_char)
        icon.setFixedWidth(14)
        icon.setStyleSheet("font-size: 12px; background: transparent; border: none;")
        icon.setAlignment(Qt.AlignCenter)
        # Children must not absorb mouse events — otherwise QPushButton:hover
        # only fires when the cursor is in the narrow gaps between the icon
        # and the text labels, which is what makes the sidebar feel like
        # hover "works from time to time".
        icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(icon)
        self._icon_lbl = icon

        name = QLabel(text)
        name.setStyleSheet(
            "font-size: 13px; letter-spacing: 0.4px; background: transparent; border: none;"
        )
        name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(name, stretch=1)
        self._name_lbl = name

        if shortcut:
            hint = QLabel(shortcut)
            hint.setObjectName("Muted")
            hint.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px; background: transparent; border: none;")
            hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            lay.addWidget(hint)
            self._hint_lbl = hint

    def set_active(self, active: bool):
        self.setObjectName("SidebarBtnActive" if active else "SidebarBtn")
        self.style().unpolish(self)
        self.style().polish(self)


class Sidebar(QFrame):
    """Left navigation sidebar with brand, nav sections, and compact status block."""

    screen_changed = Signal(str)

    NAV_ITEMS = {
        "WORKSTATION": [
            ("Home",          "⌂",  "⌘1"),
            ("Quick Cleanup", "⚡", "⌘2"),
            ("Analyze",       "◎",  "⌘3"),
            ("Findings",      "≡",  "⌘4"),
            ("Startups",      "⏻",  "⌘5"),
            ("History",       "◷",  "⌘6"),
        ],
        "SYSTEM": [
            ("Settings",      "⚙",  "⌘7"),
        ],
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(196)
        self._buttons: list[SidebarButton] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Brand area
        brand_frame = QFrame()
        brand_layout = QHBoxLayout(brand_frame)
        brand_layout.setContentsMargins(14, 6, 14, 16)
        brand_layout.setSpacing(10)

        # Brand mark — the Vigil cube, recoloured to the active theme accent.
        self._logo_lbl = QLabel()
        self._logo_lbl.setStyleSheet("background: transparent; border: none;")
        self._logo_lbl.setFixedSize(26, 26)
        self._logo_lbl.setAlignment(Qt.AlignCenter)
        self._render_logo()
        theme_signaller().theme_changed.connect(self._render_logo)
        brand_layout.addWidget(self._logo_lbl)

        brand_text = QVBoxLayout()
        brand_text.setSpacing(4)
        wordmark = QLabel("VIGIL")
        wordmark.setObjectName("Wordmark")
        brand_text.addWidget(wordmark)

        version = QLabel("v1.0")
        version.setObjectName("Muted")
        version.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px; letter-spacing: 1px;")
        brand_text.addWidget(version)
        brand_layout.addLayout(brand_text)
        brand_layout.addStretch()

        layout.addWidget(brand_frame)
        layout.addSpacing(8)

        # Navigation sections
        for index, (section, items) in enumerate(self.NAV_ITEMS.items()):
            if index > 0:
                layout.addSpacing(12)
                divider = QFrame()
                divider.setObjectName("SidebarDivider")
                divider.setFixedHeight(1)
                layout.addWidget(divider)
                layout.addSpacing(8)
            sec_lbl = QLabel(tr(section))
            sec_lbl.setObjectName("SectionHeader")
            apply_tactical_label(
                sec_lbl,
                font_size=8,
                letter_spacing=3,
                padding="14px 16px 6px 16px",
            )
            sec_lbl.setStyleSheet(sec_lbl.styleSheet() + " background: transparent; border: none;")
            sec_lbl.setFixedHeight(32)
            layout.addWidget(sec_lbl)

            for name, icon, shortcut in items:
                btn = SidebarButton(tr(name), icon, shortcut)
                btn.screen_name = name  # keep English key for routing
                btn.clicked.connect(lambda checked=False, n=name: self._on_click(n))
                self._buttons.append(btn)
                layout.addWidget(btn)

        layout.addStretch()

        # Status block — compact live app state
        status = QFrame()
        status.setObjectName("StatusBlock")
        status_layout = QVBoxLayout(status)
        status_layout.setContentsMargins(14, 12, 14, 12)
        status_layout.setSpacing(0)

        op_row = QHBoxLayout()
        op_row.setSpacing(6)
        dot = QLabel("●")
        dot.setObjectName("Accent")
        dot.setStyleSheet("font-size: 6px; background: transparent; border: none;")
        dot.setFixedWidth(10)
        op_row.addWidget(dot)
        op_lbl = QLabel(tr("Ready"))
        op_lbl.setObjectName("Muted")
        op_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 10px; "
            "letter-spacing: 1px; background: transparent; border: none;"
        )
        op_lbl.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        op_row.addWidget(op_lbl)
        op_row.addStretch()
        status_layout.addLayout(op_row)
        self._status_lbl = op_lbl

        layout.addWidget(status)

    def _render_logo(self, theme_key: str = None):
        """Paint the brand cube in the active theme accent (re-runs on theme change)."""
        accent = get_palette(theme_key)["accent"]
        dpr = 1.0
        screen = QApplication.primaryScreen()
        if screen is not None:
            dpr = screen.devicePixelRatio()
        self._logo_lbl.setPixmap(logo_pixmap(accent, 26, dpr))

    def _on_click(self, name: str):
        self._set_active(name)
        self.screen_changed.emit(name)

    def _set_active(self, name: str):
        for btn in self._buttons:
            btn.set_active(btn.screen_name == name)

    def set_screen(self, name: str):
        self._set_active(name)

    def update_status(self, status: str = ""):
        """Refresh the compact footer status."""
        self._status_lbl.setText(status or tr("Ready"))
