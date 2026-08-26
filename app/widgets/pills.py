"""Badge/pill/chip widgets for Podbye."""

from PySide6.QtWidgets import QLabel, QPushButton
from PySide6.QtCore import QSize, Qt, Signal


# Semantic color key → palette key
_VARIANT_MAP = {
    "safe":      "safe",
    "optional":  "optional",
    "review":    "review",
    "risk":      "risk",
    "locked":    "text_dim",
    "completed": "safe",
    "running":   "review",
    "idle":      "text_faint",
    "info":      "text_dim",
    "awaiting_review": "review",
    "cleaned":   "safe",
    "partial_halted": "risk",
    "protected": "risk",
}

_VARIANT_BG_MAP = {
    "safe":      "safe_soft",
    "optional":  "accent_soft",
    "review":    "review_soft",
    "risk":      "risk_soft",
    "protected": "risk_soft",
}


class Badge(QLabel):
    """A small colored badge/pill label, theme-aware."""

    # Horizontal padding + border from refresh_style() below. QLabel's own
    # sizeHint does not reliably account for stylesheet padding, so a badge
    # could be laid out narrower than its own text: "5 SUR 5 SÉLECTIONNÉS"
    # wanted 142px and was given 136.
    _CHROME = 9 * 2 + 2

    def __init__(self, text: str, variant: str = "info", parent=None):
        super().__init__(text.upper(), parent)
        self._variant = variant
        # Fixed height + centered text → every badge sits on the same
        # baseline regardless of variant or text length.
        self.setFixedHeight(22)
        self.setAlignment(Qt.AlignCenter)
        self.refresh_style()

    def _text_width(self) -> int:
        return self.fontMetrics().horizontalAdvance(self.text()) + self._CHROME

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(max(hint.width(), self._text_width()), 22)

    def minimumSizeHint(self):
        """A badge is a label, not a container — it must never be squeezed
        below the word it exists to show."""
        return QSize(self._text_width(), 22)

    def refresh_style(self):
        fg = self._resolve_fg()
        bg = self._resolve_bg()
        self.setStyleSheet(
            f"color: {fg}; padding: 0px 9px; "
            f"font-family: 'JetBrains Mono'; font-size: 10px; letter-spacing: 1px; "
            f"border: 1px solid {fg}; background: {bg}; "
            f"border-radius: 2px;"
        )

    def set_badge(self, text: str, variant: str):
        self._variant = variant
        self.setText(text.upper())
        self.refresh_style()

    def _resolve_fg(self) -> str:
        try:
            from app.themes.theme_manager import get_palette, _current_theme_key
            palette = get_palette(_current_theme_key)
            key = _VARIANT_MAP.get(self._variant, "text_dim")
            return palette.get(key, "#8a9b8f")
        except Exception:
            fallback = {
                "safe": "#7cc596", "review": "#d8b46a", "risk": "#d68a78",
                "text_dim": "#8a9b8f", "text_faint": "#57685e",
            }
            key = _VARIANT_MAP.get(self._variant, "text_dim")
            return fallback.get(key, "#8a9b8f")

    def _resolve_bg(self) -> str:
        try:
            from app.themes.theme_manager import get_palette, _current_theme_key
            palette = get_palette(_current_theme_key)
            key = _VARIANT_BG_MAP.get(self._variant, "")
            if key:
                return palette.get(key, "transparent")
            return "transparent"
        except Exception:
            fallback = {
                "safe": "#1c2e22", "review": "#2c2516", "risk": "#2e1f1c",
            }
            return fallback.get(self._variant, "transparent")


class Chip(QPushButton):
    """A filter chip / tag button that can be toggled."""

    toggled_sig = Signal(str, bool)

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self._active = False
        self.chip_text = text
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setObjectName("Subtle")
        self.setStyleSheet("padding: 4px 10px; font-size: 11px; font-family: 'Inter';")
        self.clicked.connect(self._on_toggle)

    def _on_toggle(self, checked=False):
        self._active = self.isChecked()
        self.toggled_sig.emit(self.chip_text, self._active)
