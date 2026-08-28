"""The size column stays where it is.

A contents row draws a name, a size, and an action. The action is one button
with two labels — "Ask AI" before an answer exists, "View result" after — and
the second is six characters wider than the first. The button sized itself, so
the moment a row acquired an answer the whole size column slid left, in the one
column a person reads straight down.

The fix is a slot: a fixed width the row reserves for whatever it can do,
measured from the widest label the button can hold rather than a constant that
a translation or a font change would invalidate. A drillable row reserves only
its chevron — items and contents never share a list, so the column still lines
up down the section and a list of items keeps its width for names.
"""
import pytest
from PySide6.QtWidgets import QVBoxLayout, QWidget

import app.screens.findings_dashboard as fd
from app.models.entity_contents import ContentRow
from app.themes.theme_manager import build_qss


ROW = ContentRow(label="Installed games", size_bytes=10 ** 11,
                 path="C:/Steam/steamapps/common")


@pytest.fixture
def host(qapp):
    qapp.setStyleSheet(build_qss("forest"))
    host = QWidget()
    QVBoxLayout(host)
    host.resize(420, 200)
    host.show()
    yield host
    host.deleteLater()
    qapp.processEvents()


def _row(qapp, host, *, analysed=False, drillable=False, row=ROW):
    w = fd.ContentRowWidget()
    host.layout().addWidget(w)
    w.bind(row, selectable=False, drillable=drillable, analysed=analysed)
    qapp.processEvents()
    return w


def _size_edge(host, w):
    """Where the size column ends, in the host's coordinates."""
    return w._size.mapTo(host, w._size.rect().topRight()).x()


def test_an_answer_does_not_move_the_size_column(qapp, host):
    """The reported symptom: 'View result' shifts the size."""
    before = _row(qapp, host, analysed=False)
    after = _row(qapp, host, analysed=True)

    assert before._btn_ask.text() != after._btn_ask.text(), "not the two states"
    assert _size_edge(host, before) == _size_edge(host, after)


def test_the_slot_is_the_same_width_in_both_states(qapp, host):
    before = _row(qapp, host, analysed=False)
    after = _row(qapp, host, analysed=True)

    assert before._action_slot.width() == after._action_slot.width()


def test_the_slot_holds_the_wider_label_without_growing(qapp, host):
    """Measured from the button itself, so the reservation is real."""
    w = _row(qapp, host, analysed=False)
    w._btn_ask.setText("View result")

    assert w._btn_ask.sizeHint().width() <= w._action_slot.width()


def test_revealing_the_button_does_not_move_the_size(qapp, host):
    """The row must not reflow under the pointer as it arrives."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QEnterEvent

    w = _row(qapp, host)
    at_rest = _size_edge(host, w)
    w.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    qapp.processEvents()

    assert _size_edge(host, w) == at_rest


def test_a_row_with_no_action_keeps_the_column_aligned(qapp, host):
    """The catch-all bucket has nothing to ask about, and still lines up with
    the rows above it."""
    other = ContentRow(label="", size_bytes=1234, path="")
    askable = _row(qapp, host)
    catch_all = _row(qapp, host, row=other)

    assert _size_edge(host, askable) == _size_edge(host, catch_all)


def test_a_list_of_items_keeps_its_width_for_names(qapp, host):
    """A drillable row reserves a chevron, not a button it can never show."""
    drillable = _row(qapp, host, drillable=True)
    askable = _row(qapp, host)

    assert drillable._action_slot.width() < askable._action_slot.width()
