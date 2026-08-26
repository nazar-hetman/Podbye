"""Reusable input controls for Podbye."""

from __future__ import annotations

from PySide6.QtCore import Qt, QPointF, QRect, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QSizePolicy, QWidget

from app.themes.theme_manager import get_palette


# ── Shared button styling ─────────────────────────────────────────
# Three screens drew an 'Ask AI' button and two of them carried their own
# byte-identical copy of this function, so a change to one silently left
# the other behind. Both live here now, beside the controls they style.


def ask_ai_button_qss() -> str:
    """Accent-tinted style so an 'Ask AI' button reads clearly as an action,
    not as a run of plain text. Themed via the live palette, with a filled
    hover state."""
    p = get_palette()
    accent = p.get("accent", "#7cc596")
    soft = p.get("accent_soft", "#1b2e22")
    bg = p.get("panel", "#141d18")
    faint = p.get("text_faint", "#57685e")
    border = p.get("border", "#213028")
    return (
        f"QPushButton {{ background: {soft}; color: {accent}; "
        f"border: 1px solid {accent}; border-radius: 3px; "
        f"padding: 3px 12px; font-size: 11px; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {accent}; color: {bg}; }}"
        f"QPushButton:pressed {{ background: {accent}; color: {bg}; }}"
        f"QPushButton:disabled {{ background: transparent; color: {faint}; "
        f"border-color: {border}; }}"
    )


def ask_ai_quiet_qss() -> str:
    """The same action, one rung down the hierarchy.

    A per-file 'Ask AI' rendered in the outlined style above out-shouted the
    file it referred to: five 800-byte icons became five bordered accent
    buttons and one faint filename each. This keeps the affordance on every
    row — no hover-to-reveal, so nothing becomes undiscoverable, and no layout
    shift — but draws it as text until the pointer is on it. The bucket header
    keeps the loud version, so the pattern is still taught somewhere.
    """
    p = get_palette()
    accent = p.get("accent", "#7cc596")
    soft = p.get("accent_soft", "#1b2e22")
    faint = p.get("text_faint", "#57685e")
    return (
        f"QPushButton {{ background: transparent; color: {faint}; "
        f"border: 1px solid transparent; border-radius: 3px; "
        f"padding: 1px 6px; font-size: 10px; font-weight: 500; }}"
        f"QPushButton:hover {{ background: {soft}; color: {accent}; "
        f"border-color: {accent}; }}"
        f"QPushButton:pressed {{ background: {soft}; color: {accent}; }}"
    )


class TacticalCheckBox(QCheckBox):
    """The one checkbox for the whole app — a square box with a real tick.

    Styling ``QCheckBox::indicator`` with a background and border makes Qt stop
    drawing the native checkmark; the QSS that did so carried a comment saying
    the opposite. So a checked box in Settings and the dialogs was a filled
    accent-coloured block with nothing in it, while Quick Cleanup and Findings
    each carried their own byte-identical hand-painted copy that did draw a
    tick. Three appearances for one control.

    Painting the indicator here means the tick is drawn, not requested, and the
    label is drawn alongside it so this can replace the plain QCheckBox
    everywhere — labelled or not.
    """

    BOX = 18        # indicator side, matching the hand-painted originals
    GAP = 8         # space between the box and its label

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setAttribute(Qt.WA_Hover, True)

    def sizeHint(self) -> QSize:
        text = self.text()
        if not text:
            return QSize(self.BOX, self.BOX)
        fm = self.fontMetrics()
        return QSize(self.BOX + self.GAP + fm.horizontalAdvance(text) + 2,
                     max(self.BOX, fm.height()))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, pos) -> bool:
        """The label toggles too, which is what a user expects of a checkbox."""
        return self.rect().contains(pos)

    def _box_rect(self) -> QRect:
        top = (self.height() - self.BOX) // 2
        return QRect(0, max(0, top), self.BOX, self.BOX)

    def paintEvent(self, event):
        del event
        p = get_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        checked, enabled = self.isChecked(), self.isEnabled()
        hovered = self.underMouse()

        border = p.get("border_alt", "#2b3d33")
        fill = p.get("bg_deep", "#080d0a")
        if checked:
            border = p.get("accent", "#7cc596")
            fill = p.get("accent_soft", "#1b2e22")
        elif hovered:
            border = p.get("border_hover", "#3a5648")
            fill = p.get("panel_hover", "#1d2c25")
        if not enabled:
            border = p.get("border", "#213028")
            fill = p.get("bg_deep", "#080d0a")

        box = self._box_rect()
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(fill))
        painter.drawRect(box.adjusted(1, 1, -1, -1))

        if checked:
            tick = p.get("text", "#d6e2da") if enabled else p.get("text_faint", "#57685e")
            painter.setPen(QPen(QColor(tick), 2))
            x, y = box.left(), box.top()
            painter.drawLine(x + 4, y + 9, x + 7, y + 12)
            painter.drawLine(x + 7, y + 12, x + 13, y + 6)

        text = self.text()
        if text:
            painter.setPen(QColor(p.get("text", "#d6e2da") if enabled
                                  else p.get("text_faint", "#57685e")))
            painter.setFont(self.font())
            painter.drawText(
                QRect(box.right() + self.GAP, 0,
                      self.width() - box.right() - self.GAP, self.height()),
                Qt.AlignLeft | Qt.AlignVCenter, text)

        painter.end()


class ElidedLabel(QLabel):
    """QLabel that shrinks to '…' instead of forcing its container wider.

    A filesystem path is one unbreakable word. A word-wrapping QLabel reports
    that whole word as its *minimum* width, and a QScrollArea honours the
    minimum — so one long launch path demanded 936 px inside a 500 px panel,
    and every other row in it was clipped at the panel edge (measured on the
    Startups inspector: 1056 px minimum against a ~500 px sidebar).

    Ignored horizontal size policy is what breaks that chain: the label accepts
    whatever width the layout gives it, and elides the text to fit. The full
    value stays available as the tooltip.
    """

    def __init__(self, text: str = "", parent=None, mode=Qt.ElideRight):
        super().__init__(parent)
        self._full = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(text)
        super().setText(text)

    def setText(self, text: str):
        """Overridden so callers do not need to know the label elides."""
        self.set_full_text(text)

    def full_text(self) -> str:
        return self._full

    def set_full_text(self, text: str):
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def sizeHint(self):
        """The width the *full* text wants, not the width it was cut down to.

        QLabel derives its hint from the text it currently holds, which here is
        already elided — so a label that shrank once reported a smaller hint,
        was given less room, and never grew back even when the panel widened.
        Layouts that honour the hint (anything but Ignored) need the real
        figure to hand the label its natural width when there is room.
        """
        hint = super().sizeHint()
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 4,
                     hint.height())

    def _elide(self):
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, self._mode, max(1, self.width() - 2)))


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
        self._arrow = None      # set before base init: see _set_arrow_open()
        super().__init__(parent)
        self._arrow = _DropdownArrow(self)
        self._arrow.raise_()

    def _set_arrow_open(self, is_open: bool):
        """Flip the painted arrow, tolerating a half-built widget.

        showPopup/hidePopup are Qt virtuals, and Qt calls hidePopup() from
        inside QComboBox's own constructor — before the line below that creates
        the arrow has run. Reaching for the attribute there raised
        AttributeError straight out of the C++ override, which surfaces as an
        access violation rather than a Python traceback.
        """
        # getattr, not self._arrow: Qt can reach this from inside QComboBox's
        # own construction, before __init__ has bound the attribute at all —
        # a bare access raises AttributeError out of a C++ virtual, which
        # surfaces as an access violation rather than a traceback.
        arrow = getattr(self, "_arrow", None)
        if arrow is not None:
            arrow.set_open(is_open)

    def showPopup(self):
        self._set_arrow_open(True)
        super().showPopup()

    def hidePopup(self):
        self._set_arrow_open(False)
        super().hidePopup()

    def apply_reference_style(self, palette: dict[str, str], *, compact: bool = False):
        border = palette.get("border_alt", "#2b3d33")
        text = palette.get("text", "#d6e2da")
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
