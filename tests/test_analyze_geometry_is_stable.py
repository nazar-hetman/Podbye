"""The Analyze progress row must not move while a scan reports progress.

Reported as parts of the screen jumping when the window is resized during an
active run. The cause was not the resize: the row updates several times a
second, and two of its widgets grew with their own text. The count label goes
"—" then "1,234" then "123,456", and everything to its right - the "items"
suffix, the size, the whole pipeline chip strip - moved with it. The elapsed
column sits after a stretch and was sized by the longest path it had ever
been shown, so its left edge jumped on every tick too.

Resizing made it obvious rather than causing it: the stretch redistributes at
the same moment the content changes.
"""
import pytest

from PySide6.QtWidgets import QLabel

from app.screens.analyze import AnalyzeScreen


PATHS = [
    "C:/W",
    "C:/Users/nazar/AppData/Local/Temp/x.tmp",
    "C:/Users/nazar/AppData/Local/Packages/Microsoft.WindowsStore_8wekyb3d8bbwe/"
    "LocalCache/Local/Microsoft/Windows/INetCache/deep/deeper/file.dat",
    "D:/a.txt",
]
COUNTS = [0, 7, 1234, 98765, 4321098]


@pytest.fixture
def screen(qapp):
    s = AnalyzeScreen()
    s.resize(1400, 900)
    s.show()
    for _ in range(6):
        qapp.processEvents()
    yield s
    s.deleteLater()
    qapp.processEvents()


def _x_of(widget, root):
    return widget.mapTo(root, widget.rect().topLeft()).x()


def _tracked(s):
    """The widgets a moving count would push around."""
    return {
        "items suffix": s._items_suffix,
        "size": s._size_lbl,
        "elapsed": s._elapsed_lbl,
    }


def test_the_row_does_not_move_as_the_count_grows(screen, qapp):
    before = {k: _x_of(w, screen) for k, w in _tracked(screen).items()}
    for count in COUNTS:
        screen._on_progress(count, PATHS[count % len(PATHS)])
        for _ in range(3):
            qapp.processEvents()
    after = {k: _x_of(w, screen) for k, w in _tracked(screen).items()}
    moved = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not moved, f"widgets moved while the count grew: {moved}"


def test_a_long_path_does_not_widen_the_elapsed_column(screen, qapp):
    before = _x_of(screen._elapsed_lbl, screen)
    for path in PATHS:
        screen._on_progress(500, path)
        for _ in range(3):
            qapp.processEvents()
    assert _x_of(screen._elapsed_lbl, screen) == before


def test_the_path_label_elides_instead_of_demanding_width(screen, qapp):
    """A path is one unbreakable word; a plain QLabel reports it as a minimum."""
    from app.widgets.controls import ElidedLabel

    assert isinstance(screen._current_path_lbl, ElidedLabel)
    screen._on_progress(1, PATHS[2])
    for _ in range(3):
        qapp.processEvents()
    assert screen._current_path_lbl.width() <= 260


def test_the_row_survives_a_resize_mid_run(screen, qapp):
    """The reported symptom: resize while progress keeps arriving."""
    screen._on_progress(1234, PATHS[2])
    for _ in range(3):
        qapp.processEvents()

    seen = []
    for width in (1100, 1600, 1280, 1400):
        screen.resize(width, 900)
        screen._on_progress(99999, PATHS[2])
        for _ in range(4):
            qapp.processEvents()
        # distance from the count to the chips must not depend on the count
        seen.append(_x_of(screen._items_suffix, screen)
                    - _x_of(screen._count_lbl, screen))
    assert len(set(seen)) == 1, f"gap after the count changed with width: {seen}"


def test_no_label_in_the_row_is_clipped_after_updates(screen, qapp):
    screen._on_progress(4321098, PATHS[2])
    for _ in range(4):
        qapp.processEvents()
    clipped = [l.text() for l in screen.findChildren(QLabel)
               if l.isVisibleTo(screen) and l.text().strip()
               and not l.wordWrap()
               and l.__class__.__name__ != "ElidedLabel"
               and l.sizeHint().width() > l.width() + 1]
    assert not clipped, f"clipped after progress updates: {clipped}"
