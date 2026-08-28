"""Podbye logo — renders the monochrome `currentColor` cube SVG in any colour.

Qt's QSvgRenderer does not resolve CSS `currentColor`, so we substitute it with
a concrete hex colour before rendering. Per-path opacity is preserved, which is
what gives the cube its shaded faces. Used for both the sidebar mark and the
window / taskbar icon, recoloured to the active theme accent.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_LOGO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "logo.svg"
)

_template: str | None = None


def _svg_template() -> str:
    global _template
    if _template is None:
        with open(_LOGO_PATH, "r", encoding="utf-8") as fh:
            _template = fh.read()
    return _template


def logo_pixmap(color: str, size: int, dpr: float = 1.0) -> QPixmap:
    """Render the mark at `size` logical px in `color`, crisp for the given dpr."""
    svg = _svg_template().replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    px = QPixmap(max(1, round(size * dpr)), max(1, round(size * dpr)))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return px


def logo_icon(color: str, sizes=(16, 24, 32, 48, 64, 128, 256)) -> QIcon:
    """Build a multi-resolution window / taskbar icon in `color`."""
    icon = QIcon()
    for s in sizes:
        icon.addPixmap(logo_pixmap(color, s))
    return icon


# ── Small UI glyphs ───────────────────────────────────────────────
# Inline rather than a file under app/assets: podbye.spec bundles data files
# one by one, so a new asset is a new line in the spec and a missing-file bug
# in the frozen build if anyone forgets it. A 12px glyph does not need to be
# a shipped file.
#
# Not an emoji: 🔒 renders in whatever the platform emoji font decides, in
# colour, at its own weight — beside 11px mono text that is a sticker, not an
# icon. This draws in the palette colour it is handed, like every other mark
# in the app.
_LOCK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    '<path fill="none" stroke="currentColor" stroke-width="1.6" '
    'stroke-linecap="round" d="M5.1 7V5.2a2.9 2.9 0 0 1 5.8 0V7"/>'
    '<rect x="3.4" y="7" width="9.2" height="6.4" rx="1.3" fill="currentColor"/>'
    "</svg>"
)


def lock_pixmap(color: str, size: int = 12, dpr: float = 1.0) -> QPixmap:
    """A small padlock in *color* — the mark for "the user is keeping this"."""
    svg = _LOCK_SVG.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    px = QPixmap(max(1, round(size * dpr)), max(1, round(size * dpr)))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return px
