"""Font loader for Podbye — registers bundled TTF fonts with Qt."""

import os
import glob
from PySide6.QtGui import QFontDatabase

FONT_DIR = os.path.dirname(os.path.abspath(__file__))

# Font family names after loading
FONT_UI      = "Inter"
FONT_MONO    = "JetBrains Mono"
FONT_PIXEL   = "Silkscreen"

_loaded = False


def load_fonts():
    """Register all bundled .ttf fonts with Qt's font database."""
    global _loaded
    if _loaded:
        return
    for ttf in glob.glob(os.path.join(FONT_DIR, "*.ttf")):
        font_id = QFontDatabase.addApplicationFont(ttf)
        if font_id < 0:
            print(f"[podbye] WARNING: failed to load font: {ttf}")
    _loaded = True
