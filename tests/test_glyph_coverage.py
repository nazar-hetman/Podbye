"""Every symbol Vigil draws must exist in a font on the machine.

A codepoint with no glyph anywhere renders as .notdef — a small hollow box.
It looks like a corrupted build, and nothing in the code hints at it: the
source reads "◷" and the user sees ▯. Six of them shipped at once — the
History nav icon, the info hint in the close dialog, the AI in-progress
marker (twice), the cloud-sync badge and a smiley in the "that's me" text.

The check renders each symbol and compares it against a codepoint that is
guaranteed to be unassigned. Identical pixels mean both fell back to .notdef.
"""
import ast
import pathlib
import unicodedata

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QImage, QPainter

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# Every family Vigil asks for by name.
FAMILIES = ("Inter", "JetBrains Mono", "Silkscreen", "Segoe UI")

# U+FFFFF is in a private-use plane and permanently unassigned, so whatever it
# renders as *is* this system's .notdef box.
_UNASSIGNED = "\U000FFFFF"


@pytest.fixture(scope="module")
def fonts(qapp):
    from app.fonts import load_fonts
    load_fonts()
    return qapp


def _render(ch: str, family: str) -> bytes:
    img = QImage(40, 40, QImage.Format_ARGB32)
    img.fill(Qt.white)
    painter = QPainter(img)
    font = QFont(family)
    font.setPixelSize(24)
    painter.setFont(font)
    painter.drawText(img.rect(), Qt.AlignCenter, ch)
    painter.end()
    return bytes(img.constBits())


def _has_glyph(ch: str, family: str) -> bool:
    """QFontMetrics.inFont() reports the family alone and ignores fallback,
    which makes it useless here — it says False for characters that render
    perfectly. Comparing pixels is what tells the truth."""
    return _render(ch, family) != _render(_UNASSIGNED, family)


def _symbols_in_source() -> dict[str, list[str]]:
    """Non-letter, non-ASCII characters inside string literals, by location."""
    found: dict[str, list[str]] = {}
    for path in APP.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            for ch in node.value:
                if ord(ch) > 127 and not unicodedata.category(ch).startswith("L"):
                    found.setdefault(ch, []).append(f"{path.name}:{node.lineno}")
    return found


def test_every_symbol_in_the_ui_has_a_glyph(fonts):
    missing = []
    for ch, where in sorted(_symbols_in_source().items()):
        if not any(_has_glyph(ch, family) for family in FAMILIES):
            name = unicodedata.name(ch, "unnamed")
            missing.append(f"U+{ord(ch):04X} {ch!r} ({name}) at {where[0]}")
    assert not missing, (
        "symbols with no glyph in any available font — these draw as .notdef "
        "boxes:\n  " + "\n  ".join(missing))


def test_the_check_can_actually_detect_a_missing_glyph(fonts):
    """A check that never fails is worthless."""
    assert not any(_has_glyph(_UNASSIGNED, family) for family in FAMILIES)
    assert _has_glyph("A", "Inter")
