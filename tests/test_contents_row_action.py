"""A contents row says what it can do.

Clicking a row started an AI analysis. The row was drawn with an arrow cursor,
no chevron, and a tooltip that named only the path - the file already owned the
whole vocabulary for "this is clickable" (chevron, pointing cursor, an action
tooltip) and withheld every part of it in the one case where the click did
something surprising. The model started because you touched a list.

The chevron could not simply be added to those rows: it already means "drill
into this" on the same list, so two rows would look identical and do entirely
different things. The action is a button instead - the same one the Entity
inspector and Startups use - revealed on hover and on keyboard focus, which
answers the objection that put it in hiding originally: a button on every row
at rest made the list loud.
"""
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QFocusEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

import app.screens.findings_dashboard as fd
from app.models.entity_contents import ContentRow
from app.themes.theme_manager import build_qss


@pytest.fixture
def rows(qapp):
    """One row of each kind, in a host that takes the initial focus itself."""
    qapp.setStyleSheet(build_qss("forest"))
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.addWidget(QLineEdit())
    made = {}
    for name, drillable in (("askable", False), ("drillable", True)):
        w = fd.ContentRowWidget()
        lay.addWidget(w)
        w.bind(ContentRow(label="Chrome", size_bytes=10 ** 9, path="C:/x/Chrome"),
               selectable=False, drillable=drillable)
        made[name] = w
    host.resize(500, 160)
    host.show()
    qapp.processEvents()
    yield made
    host.deleteLater()
    qapp.processEvents()


def _hover(w):
    w.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))


def _unhover(w):
    w.leaveEvent(QEvent(QEvent.Leave))


def _click(w):
    w.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5),
                                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


def _press(w, key=Qt.Key_Return):
    w.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))


# -- the action is visible when reached for ------------------------

def test_the_button_is_out_of_the_way_at_rest(rows, qapp):
    """A button on every row is what the previous design rejected, and it was
    right to: twenty rows of buttons is a loud list."""
    assert not rows["askable"]._btn_ask.isVisible()


def test_hovering_reveals_it(rows, qapp):
    _hover(rows["askable"])
    qapp.processEvents()
    assert rows["askable"]._btn_ask.isVisible()


def test_leaving_hides_it_again(rows, qapp):
    _hover(rows["askable"])
    _unhover(rows["askable"])
    qapp.processEvents()
    assert not rows["askable"]._btn_ask.isVisible()


def test_keyboard_focus_reveals_it_too(rows, qapp):
    """A hover-only affordance is a different kind of hidden."""
    rows["askable"].focusInEvent(QFocusEvent(QEvent.FocusIn))
    qapp.processEvents()
    assert rows["askable"]._btn_ask.isVisible()


def test_the_row_does_not_reflow_when_it_appears(rows):
    """The space is held open while hidden, so a row does not shift under the
    pointer as it arrives."""
    assert rows["askable"]._btn_ask.sizePolicy().retainSizeWhenHidden()


def test_it_says_what_it_will_do(rows):
    btn = rows["askable"]._btn_ask
    assert btn.text() == "Ask AI"
    assert "AI" in btn.toolTip() and "Chrome" in btn.toolTip()


def test_it_looks_like_every_other_ask_ai_button(rows):
    """Consistency with the Entity inspector was the point of using a button
    rather than inventing a popup."""
    from app.widgets.controls import ask_ai_button_qss
    assert rows["askable"]._btn_ask.styleSheet() == ask_ai_button_qss()


# -- the row itself no longer runs a model -------------------------

def test_clicking_the_row_starts_nothing(rows, qapp):
    seen = []
    rows["askable"].ask_requested.connect(lambda p: seen.append(p))
    rows["askable"].clicked.connect(lambda p: seen.append(p))
    _click(rows["askable"])
    assert seen == [], "the bare row still triggers something"


def test_the_button_is_what_asks(rows, qapp):
    seen = []
    rows["askable"].ask_requested.connect(lambda p: seen.append(p))
    rows["askable"]._btn_ask.click()
    qapp.processEvents()
    assert seen == ["C:/x/Chrome"]


def test_enter_on_the_row_asks(rows, qapp):
    """The keyboard reaches the same action the pointer does."""
    seen = []
    rows["askable"].ask_requested.connect(lambda p: seen.append(p))
    _press(rows["askable"])
    assert seen == ["C:/x/Chrome"]


# -- a drillable row is untouched ----------------------------------

def test_a_drillable_row_still_navigates_on_click(rows, qapp):
    seen = []
    rows["drillable"].clicked.connect(lambda p: seen.append(p))
    _click(rows["drillable"])
    assert seen == ["C:/x/Chrome"]


def test_a_drillable_row_keeps_its_chevron_and_never_shows_the_button(rows, qapp):
    """The chevron already means "drill into this". Two rows that look the
    same must not do different things."""
    assert rows["drillable"]._chevron.isVisible()
    _hover(rows["drillable"])
    qapp.processEvents()
    assert not rows["drillable"]._btn_ask.isVisible()


def test_a_drillable_row_reserves_no_space_for_the_button(rows):
    assert not rows["drillable"]._btn_ask.sizePolicy().retainSizeWhenHidden()


def test_a_drillable_row_refuses_to_ask_even_if_invoked(rows, qapp):
    """Guarded, not merely hidden."""
    seen = []
    rows["drillable"].ask_requested.connect(lambda p: seen.append(p))
    rows["drillable"]._btn_ask.click()
    qapp.processEvents()
    assert seen == []


# -- a row with nothing addressable --------------------------------

def test_the_other_row_offers_no_action(qapp):
    """The catch-all bucket is several folders at once, so there is nothing to
    ask about."""
    qapp.setStyleSheet(build_qss("forest"))
    host = QWidget()
    lay = QVBoxLayout(host)
    w = fd.ContentRowWidget()
    lay.addWidget(w)
    w.bind(ContentRow(label="", size_bytes=1234, path=""),
           selectable=False, drillable=False)
    host.show()
    qapp.processEvents()
    try:
        _hover(w)
        qapp.processEvents()
        assert not w._btn_ask.isVisible()
        seen = []
        w.ask_requested.connect(lambda p: seen.append(p))
        _press(w)
        assert seen == []
    finally:
        host.deleteLater()
        qapp.processEvents()
