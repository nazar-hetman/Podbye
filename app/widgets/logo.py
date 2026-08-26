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
