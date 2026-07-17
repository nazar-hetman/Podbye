"""Generate a multi-resolution Windows .ico for the packaged Vigil exe.

Renders the theme-accent cube logo (app/assets/logo.svg) at several sizes and
writes app/assets/vigil.ico. Run with the venv Python before PyInstaller.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtGui import QGuiApplication
from app.widgets.logo import logo_pixmap

ICON_COLOR = "#E8B339"  # amber accent — matches the default theme mark
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "app", "assets", "vigil.ico")


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    pixmaps = [logo_pixmap(ICON_COLOR, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    # QPixmap.save with .ico writes a single image; build a multi-size ICO by
    # hand from the rendered PNG frames instead.
    import struct
    from PySide6.QtCore import QBuffer, QByteArray

    frames = []
    for px in pixmaps:
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QBuffer.WriteOnly)
        px.save(buf, "PNG")
        buf.close()
        frames.append((px.width(), px.height(), bytes(ba.data())))

    with open(OUT, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(frames)))  # ICONDIR
        offset = 6 + 16 * len(frames)
        for w, h, data in frames:
            bw = 0 if w >= 256 else w
            bh = 0 if h >= 256 else h
            f.write(struct.pack("<BBBBHHII", bw, bh, 0, 0, 1, 32,
                                len(data), offset))
            offset += len(data)
        for _, _, data in frames:
            f.write(data)

    print(f"wrote {OUT} ({len(frames)} sizes)")
    del app


if __name__ == "__main__":
    main()
