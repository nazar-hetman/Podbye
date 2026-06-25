"""Reusable input controls for Vigil."""

from __future__ import annotations

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import QComboBox, QWidget


class _DropdownArrow(QWidget):
    """Triangle dropdown indicator painted directly with QPainter.

    Covers the full ::drop-down zone of the combo box (18 px wide) so
    nothing Qt might draw from the native style leaks through. The
    triangle flips between ▼ and ▲ when the popup is open.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._open = False
        self._color = QColor("#d6e2da")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def set_open(self, opened: bool):
        if self._open != opened:
            self._open = opened
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        # Triangle dimensions: 9px wide, 6px tall — recognisably a triangle,
        # not a flat sliver. Centred horizontally and vertically.
        tri_w = 9
        tri_h = 6
        cx = w / 2
        cy = h / 2
        if self._open:
            pts = [
                QPointF(cx - tri_w / 2, cy + tri_h / 2),
                QPointF(cx + tri_w / 2, cy + tri_h / 2),
                QPointF(cx, cy - tri_h / 2),
            ]
        else:
            pts = [
                QPointF(cx - tri_w / 2, cy - tri_h / 2),
                QPointF(cx + tri_w / 2, cy - tri_h / 2),
                QPointF(cx, cy + tri_h / 2),
            ]
        p.setPen(Qt.NoPen)
        p.setBrush(self._color)
        p.drawPolygon(QPolygonF(pts))


class TacticalComboBox(QComboBox):
    """Reference dropdown matching the Settings mockup field treatment.

    The arrow is a custom painted triangle that flips when the popup opens
    — fully font-independent. Sized to cover the whole 18 px drop-down
    zone so Qt's native arrow never bleeds through.
    """

    _DROP_W = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self._arrow = _DropdownArrow(self)
        self._arrow.raise_()

    def showPopup(self):
        self._arrow.set_open(True)
        super().showPopup()

    def hidePopup(self):
        self._arrow.set_open(False)
        super().hidePopup()

    def apply_reference_style(self, palette: dict[str, str], *, compact: bool = False):
        border = palette.get("border_alt", "#2b3d33")
        text = palette.get("text", "#d6e2da")
        text_dim = palette.get("text_dim", "#8a9b8f")
        panel = palette.get("panel", "#141d18")
        panel_alt = palette.get("panel_alt", "#18241e")
        panel_hover = palette.get("panel_hover", "#1d2c25")
        border_hover = palette.get("border_hover", "#3a5648")
        accent = palette.get("accent", "#7cc596")
        accent_soft = palette.get("accent_soft", "#1b2e22")
        bg_deep = palette.get("bg_deep", "#080d0a")
        padding = "5px 24px 5px 12px" if compact else "8px 28px 8px 14px"
        min_height = "30px" if compact else "34px"
        self._arrow.set_color(text)

        self.setStyleSheet(
            "QComboBox { "
            f"background: {panel_alt}; "
            f"color: {text}; "
            f"border: 1px solid {border}; "
            f"padding: {padding}; min-height: {min_height}; border-radius: 2px; "
            "font-family: 'JetBrains Mono'; font-size: 11px; font-weight: 500; "
            "}"
            f"QComboBox:hover {{ background: {panel_hover}; border-color: {border_hover}; }}"
            f"QComboBox:focus {{ border-color: {accent}; }}"
            f"QComboBox:disabled {{ background: {bg_deep}; color: {palette.get('text_faint', '#57685e')}; }}"
            "QComboBox::drop-down { "
            f"border: 0; background: transparent; width: {self._DROP_W}px; "
            "}"
            "QComboBox::down-arrow { "
            "image: none; background: transparent; border: 0; width: 0px; height: 0px; "
            "}"
            f"QComboBox QAbstractItemView {{ background: {panel}; color: {text}; "
            f"border: 1px solid {border}; selection-background-color: {accent_soft}; "
            f"selection-color: {text}; padding: 6px; }}"
        )
        self._reposition_arrow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_arrow()

    def _reposition_arrow(self):
        # Cover the entire 18 px ::drop-down zone on the right edge so no
        # native arrow can show through underneath.
        h = max(0, self.height() - 2)
        self._arrow.setFixedSize(self._DROP_W, h)
        self._arrow.move(self.width() - self._DROP_W, 1)
