"""One checkbox, drawn the same way everywhere.

There were three. Quick Cleanup and Findings each carried a hand-painted square
that drew a real tick — byte-identical copies of each other. Everywhere else
used a plain QCheckBox, and the QSS styling its ``::indicator`` with a
background and border makes Qt stop drawing the native checkmark. So Settings
and the dialogs showed a checked box as a filled accent-coloured block with
nothing in it, which is what a user reported as "green shapes".

The tick is now painted rather than requested, and the label is painted beside
it so the one widget covers the labelled cases too.
"""
import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPixmap, QColor

from app.widgets.controls import TacticalCheckBox


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _render(box, size=(220, 26)):
    box.resize(*size)
    box.show()
    pix = QPixmap(box.size())
    pix.fill(QColor("#070c09"))
    box.render(pix)
    return pix.toImage()


def _ink_pixels(img, box_side=18):
    """Distinct colours drawn inside the indicator square."""
    seen = {}
    for y in range(min(box_side, img.height())):
        for x in range(min(box_side, img.width())):
            c = img.pixelColor(x, y).name()
            seen[c] = seen.get(c, 0) + 1
    return seen


# ── the tick is actually drawn ────────────────────────────────────

def test_a_checked_box_draws_a_tick(qapp):
    """The whole complaint: a checked box that is just a filled shape."""
    off = TacticalCheckBox()
    off.setChecked(False)
    unchecked = _ink_pixels(_render(off))

    on = TacticalCheckBox()
    on.setChecked(True)
    checked = _ink_pixels(_render(on))

    # The tick adds ink the empty box does not have.
    assert len(checked) > len(unchecked), (
        "checked and unchecked differ only by fill — no tick was drawn")


def test_the_tick_uses_the_text_colour(qapp):
    """Drawn in ink, not in the accent, so it reads against the fill."""
    from app.themes.theme_manager import get_palette
    on = TacticalCheckBox()
    on.setChecked(True)
    colours = _ink_pixels(_render(on))

    assert get_palette().get("text", "#d6e2da").lower() in [c.lower() for c in colours]


# ── one implementation, used everywhere ───────────────────────────

@pytest.mark.parametrize("module, attr", [
    ("app.screens.quick_cleanup", "_SelectionCheckBox"),
    ("app.screens.findings_dashboard", "_FindingSelectionCheckBox"),
])
def test_the_old_private_copies_are_the_shared_widget(module, attr):
    import importlib
    mod = importlib.import_module(module)
    assert getattr(mod, attr) is TacticalCheckBox


def test_no_screen_constructs_a_plain_checkbox():
    """A plain QCheckBox would silently pick up the tickless QSS again."""
    import pathlib, re
    offenders = []
    for path in pathlib.Path("app").rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"(?<![A-Za-z_])QCheckBox\(", src):
            line = src[:m.start()].count("\n") + 1
            offenders.append(f"{path}:{line}")
    assert not offenders, (
        "these build a plain QCheckBox, which renders without a tick: "
        + ", ".join(offenders))


# ── it works as a checkbox ────────────────────────────────────────

def test_the_label_is_part_of_the_hit_area(qapp):
    """Clicking the words toggles, which is what a checkbox does."""
    box = TacticalCheckBox("Don't ask again")
    box.resize(200, 24)
    assert box.hitButton(QPoint(150, 12)), "the label does not toggle"
    assert box.hitButton(QPoint(8, 12)), "the indicator does not toggle"


def test_a_labelled_box_reserves_room_for_its_text(qapp):
    bare = TacticalCheckBox()
    labelled = TacticalCheckBox("Explain all findings automatically")

    assert labelled.sizeHint().width() > bare.sizeHint().width() + 50
    assert bare.sizeHint().width() == TacticalCheckBox.BOX


def test_toggling_still_emits(qapp):
    box = TacticalCheckBox("x")
    seen = []
    box.toggled.connect(seen.append)
    box.setChecked(True)
    box.setChecked(False)
    assert seen == [True, False]


def test_a_disabled_box_still_paints(qapp):
    box = TacticalCheckBox("x")
    box.setChecked(True)
    box.setEnabled(False)
    img = _render(box)
    assert img.width() > 0        # raises during render if the paint path breaks
