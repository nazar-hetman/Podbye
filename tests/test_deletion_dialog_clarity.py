"""The deletion dialog is the last thing between the user and losing files.

So it has to say exactly what will go, in the user's own language, without
padding the window with empty space that hides the list.
"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea

from app.i18n import get_language, set_language
from app.screens.cleanup_dialog import CleanupConfirmDialog
from app.themes.theme_manager import build_qss
from app.widgets.controls import ElidedLabel

PHOTOSHOP = r"C:\Program Files\Adobe\Adobe Photoshop 2024"


def _item(path=PHOTOSHOP, risk="Review", size=12 * 1024 ** 3):
    return {"path": path, "name": path.rsplit("\\", 1)[-1],
            "category": "Applications", "risk": risk,
            "size_bytes": size, "reclaimable_bytes": size,
            "entity_type": "application", "actionability": "uninstall"}


@pytest.fixture
def app(qapp):
    from app.fonts import load_fonts
    load_fonts()
    qapp.setStyleSheet(build_qss("forest"))
    original = get_language()
    yield qapp
    set_language(original)


def _dialog(app, items):
    dlg = CleanupConfirmDialog(items)
    dlg.show()
    app.processEvents()
    return dlg


def _close(dlg, app):
    dlg.close()
    dlg.deleteLater()
    app.processEvents()


def _rows(dlg):
    return [w for w in dlg.findChildren(ElidedLabel)]


def test_the_list_shows_the_full_path_not_just_a_name(app):
    """"Adobe Photoshop 2024" does not say which copy, or where. A dialog
    that is about to delete something has to name the thing exactly."""
    dlg = _dialog(app, [_item()])
    texts = [r.full_text() for r in _rows(dlg)]
    assert any(PHOTOSHOP in t for t in texts), texts
    _close(dlg, app)


def test_the_path_is_elided_from_the_middle_with_the_whole_thing_on_hover(app):
    """Both the drive and the leaf matter; trimming either end loses one."""
    dlg = _dialog(app, [_item()])
    row = next(r for r in _rows(dlg) if PHOTOSHOP in r.full_text())
    assert row.toolTip() == row.full_text()
    _close(dlg, app)


def test_the_risk_word_is_translated_like_the_rest_of_the_dialog(app):
    """The row read "Review" in an otherwise Ukrainian dialog."""
    set_language("Ukrainian")
    dlg = _dialog(app, [_item()])
    texts = " ".join(r.full_text() for r in _rows(dlg))
    assert "Review" not in texts, f"untranslated risk token in {texts!r}"
    assert "Перевірити" in texts
    _close(dlg, app)


def test_one_item_does_not_reserve_a_scrolling_box(app):
    """A single 13px row was given a 79px box, leaving a hole mid-dialog."""
    dlg = _dialog(app, [_item()])
    areas = [s for s in dlg.findChildren(QScrollArea) if s.isVisible()]
    assert areas
    for area in areas:
        needed = area.widget().sizeHint().height()
        assert area.height() <= needed + 8, (
            f"scroll area is {area.height()}px for {needed}px of content")
    _close(dlg, app)


def test_a_long_list_is_still_capped_so_the_buttons_stay_reachable(app):
    items = [_item(path=rf"C:\stuff\item_{i}", size=1024 ** 2) for i in range(60)]
    dlg = _dialog(app, items)
    areas = [s for s in dlg.findChildren(QScrollArea) if s.isVisible()]
    assert areas
    for area in areas:
        assert area.height() <= 140
    _close(dlg, app)


def test_a_safe_selection_is_listed_too(app):
    """The plan lists everything it will move, not only the risky part.

    It used to show the review-tier targets alone, so a selection of 384 items
    was four counts and a partial list. Reported as: it is unclear what exactly
    he is deleting. A Safe item is still an item leaving the disk.
    """
    dlg = _dialog(app, [_item(risk="Safe", path=r"C:\stuff\thumbs")])
    texts = " ".join(l.text() for l in dlg.findChildren(QLabel))
    texts += " ".join(l.text() for l in dlg.findChildren(ElidedLabel))
    assert "thumbs" in texts, "a Safe target was not named anywhere in the plan"
    _close(dlg, app)


def test_nothing_to_move_means_no_empty_list_at_all(app):
    """No targets, no box \u2014 the rule that box was added for."""
    dlg = _dialog(app, [_item(risk="Protected")])
    visible = [s for s in dlg.findChildren(QScrollArea) if s.isVisible()]
    assert not visible
    _close(dlg, app)
