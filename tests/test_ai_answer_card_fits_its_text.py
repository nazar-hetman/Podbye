"""A short answer gets a short card.

The inline reasoning block read as an oversized empty text field, and it was
one: the app-wide stylesheet gives every QTextEdit ``padding: 12px 14px`` for
the input fields it was written for, and the answer box inherited it. The block
already has its own padding, so the answer sat indented inside a second box
with 24 px of nothing under two lines of prose.

The height was measured from the document alone, which does not know about that
padding — so the same box was simultaneously too tall to look right and too
short to show its own last line.

Pinned here: the card tracks its text, with a two-line floor so the section
does not resize under the reader when a shorter answer replaces a longer one,
and nothing inside the panel scrolls.
"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollBar

from app.themes.theme_manager import build_qss


ENTITY = {
    "path": "C:/Users/n/AppData/Local/Temp", "name": "Temp", "risk": "Optional",
    "entity_type": "cache", "category": "Cache", "actionability": "recycle",
    "size": "1.2 GB", "size_bytes": 1_288_490_188,
    "file_count": 4213, "folder_count": 210,
}
ONE_LINE = "Temp holds scratch files."
SHORT = "Temp holds files Windows and apps write while they work. Safe to remove."
LONG = ("Temp is where Windows and the apps you run write scratch files while "
        "they work, and they are not always tidied up afterwards. ") * 6


@pytest.fixture
def sidebar(qapp):
    from app.screens.findings_dashboard import RightSidebar
    qapp.setStyleSheet(build_qss("forest"))
    side = RightSidebar(open_cb=lambda p: None, copy_cb=lambda p: None,
                        ask_ai_cb=lambda e, regenerate=False: "")
    side.resize(440, 820)
    side.show()
    qapp.processEvents()
    yield side
    side.deleteLater()
    qapp.processEvents()


def _answer(qapp, side, text):
    side.populate({**ENTITY, "ai_status": "ready", "ai_explanation": text})
    for _ in range(3):
        qapp.processEvents()
    return side.detail_widget._ai_text


def test_a_short_answer_is_not_cut_off(qapp, sidebar):
    """The half-line that used to disappear under the inherited padding."""
    text = _answer(qapp, sidebar, SHORT)

    assert text.viewport().height() >= text.document().size().height() - 1


def test_a_long_answer_is_not_cut_off_either(qapp, sidebar):
    text = _answer(qapp, sidebar, LONG)

    assert text.viewport().height() >= text.document().size().height() - 1


def test_the_card_has_no_room_left_over(qapp, sidebar):
    """Sized to the content, not to a field. One line of slack, no more."""
    text = _answer(qapp, sidebar, SHORT)
    line = text.fontMetrics().lineSpacing()

    assert text.height() - text.document().size().height() < line


def test_a_one_line_answer_keeps_a_two_line_floor(qapp, sidebar):
    """So the section does not jump when a shorter answer replaces a longer."""
    text = _answer(qapp, sidebar, ONE_LINE)
    two_lines = text.fontMetrics().lineSpacing() * 2

    assert text.height() >= two_lines


def test_a_short_answer_makes_a_shorter_card_than_a_long_one(qapp, sidebar):
    short_h = _answer(qapp, sidebar, SHORT).height()
    long_h = _answer(qapp, sidebar, LONG).height()

    assert short_h < long_h


def test_the_answer_is_not_drawn_as_an_input_field(qapp, sidebar):
    """The inherited padding is what made it look like one."""
    text = _answer(qapp, sidebar, SHORT)

    assert "padding: 0" in text.styleSheet()


def test_nothing_inside_the_panel_scrolls(qapp, sidebar):
    _answer(qapp, sidebar, LONG)
    bars = [b for b in sidebar.findChildren(QScrollBar)
            if b.orientation() == Qt.Vertical and b.isVisible()]

    assert len(bars) <= 1


def test_ask_again_stays_in_the_header(qapp, sidebar):
    """Next to the answer's own caption, not below the prose."""
    panel = sidebar.detail_widget
    _answer(qapp, sidebar, SHORT)

    def _top(w):
        return w.mapTo(panel._ai_frame, w.rect().topLeft()).y()

    assert panel._ai_ask_btn.text() == "Ask again"
    assert _top(panel._ai_ask_btn) < _top(panel._ai_text)
