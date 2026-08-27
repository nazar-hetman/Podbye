"""What happens to an AI answer after it arrives.

Three things ended where they should have continued. The Ask AI button
disappeared the moment an answer landed - so the one point at which re-asking
becomes useful, after switching models or changing tone, was the point the
control went away. A row in Contents went on saying "Ask AI" over a path it had
already explained, inviting the user to pay twice for the same text. And the
section carrying Podbye's own category, risk tier and removal advice was
labelled AI, attributing the verdict to the part of the system least
responsible for it.
"""
import inspect

import pytest
from PySide6.QtWidgets import QVBoxLayout, QWidget

import app.screens.findings_dashboard as fd
import app.services.ai_explainer as ax
from app.models.entity_contents import ContentRow
from app.themes.theme_manager import build_qss

_ENTITY = {"path": "C:/x/Chrome", "name": "Chrome", "size": "1 GB",
           "size_bytes": 10 ** 9, "risk": "Safe", "entity_type": "cache_folder",
           "category": "Cache & Temp", "file_count": 3, "folder_count": 0,
           "reclaimable_bytes": 10 ** 9}


@pytest.fixture
def panel(qapp):
    qapp.setStyleSheet(build_qss("forest"))
    sig = inspect.signature(fd._PreallocDetailPanel.__init__)
    kw = {n: (lambda *a, **k: None)
          for n in list(sig.parameters)[1:] if n != "parent"}
    kw["asked"] = None
    kw.pop("asked")
    p = fd._PreallocDetailPanel(**kw)
    p.resize(420, 900)
    p.show()
    qapp.processEvents()
    yield p
    p.deleteLater()
    qapp.processEvents()


def _show(panel, qapp, **extra):
    panel._current_signature = None          # defeat the same-entity guard
    panel.populate(dict(_ENTITY, **extra))
    qapp.processEvents()


# -- the section is Podbye's, not the model's ---------------------

def test_the_assessment_is_attributed_to_podbye(panel):
    """The category, the risk tier, the confidence and the removal advice under
    this heading come from Podbye's rules and stand whether or not a model ever
    runs. Only the prose inside is the model's."""
    assert panel._ai_title.text() == "PODBYE ASSESSMENT"
    assert "AI ASSESSMENT" not in panel._ai_title.text()


# -- ask, then ask again ------------------------------------------

def test_the_button_offers_a_first_answer(panel, qapp):
    _show(panel, qapp, ai_status="none")
    assert panel._ai_ask_btn.isVisible()
    assert panel._ai_ask_btn.text() == "Ask AI"


def test_the_button_stays_and_offers_a_new_one(panel, qapp):
    """It used to vanish once an answer existed."""
    _show(panel, qapp, ai_status="ready", ai_explanation="A browser cache.")
    assert panel._ai_ask_btn.isVisible(), "no way to regenerate an answer"
    assert panel._ai_ask_btn.text() == "Ask again"


def test_the_button_says_what_ask_again_costs(panel, qapp):
    _show(panel, qapp, ai_status="ready", ai_explanation="A browser cache.")
    assert "replace" in panel._ai_ask_btn.toolTip().lower()


def test_nothing_is_offered_while_a_request_is_in_flight(panel, qapp):
    _show(panel, qapp, ai_status="analyzing")
    assert not panel._ai_ask_btn.isVisible()


def test_a_failed_answer_can_be_retried_as_a_first_ask(panel, qapp):
    """Nothing was produced, so there is nothing to replace."""
    _show(panel, qapp, ai_status="failed", ai_error="timed out")
    assert panel._ai_ask_btn.isVisible()
    assert panel._ai_ask_btn.text() == "Ask AI"


@pytest.mark.parametrize("state,expected", [
    ({"ai_status": "none"}, False),
    ({"ai_status": "ready", "ai_explanation": "A browser cache."}, True),
])
def test_the_click_says_which_of_the_two_it_is(qapp, state, expected):
    qapp.setStyleSheet(build_qss("forest"))
    seen = {}
    sig = inspect.signature(fd._PreallocDetailPanel.__init__)
    kw = {n: (lambda *a, **k: None)
          for n in list(sig.parameters)[1:] if n != "parent"}
    kw["ask_ai_cb"] = lambda entity, regenerate=False: (
        seen.__setitem__("regenerate", regenerate), "")[1]
    p = fd._PreallocDetailPanel(**kw)
    p.resize(420, 900)
    p.show()
    try:
        p._current_signature = None
        p.populate(dict(_ENTITY, **state))
        qapp.processEvents()
        p._on_ask_ai_clicked()
        assert seen.get("regenerate") is expected
    finally:
        p.deleteLater()
        qapp.processEvents()


# -- the explanation area has a floor and no ceiling --------------

def test_a_short_answer_still_gets_two_lines(panel, qapp):
    """A one-line answer collapsed the box to that line, so the section changed
    size under the reader whenever a shorter answer replaced a longer one."""
    _show(panel, qapp, ai_status="ready", ai_explanation="Short.")
    floor = panel._ai_text.fontMetrics().lineSpacing() * 2
    assert panel._ai_text.height() >= floor


def test_a_long_answer_is_not_capped(panel, qapp):
    """No ceiling on purpose: this block does not scroll - it used to be a
    scroll area inside the panel's own, which put two scrollbars side by side -
    so it grows and the panel scrolls. Nothing is cut off."""
    short = "One sentence."
    _show(panel, qapp, ai_status="ready", ai_explanation=short)
    small = panel._ai_text.height()
    _show(panel, qapp, ai_status="ready",
          ai_explanation=("A much longer explanation. " * 60))
    assert panel._ai_text.height() > small


def test_the_answer_box_never_scrolls_on_its_own(panel):
    from PySide6.QtCore import Qt
    assert panel._ai_text.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff


# -- a Contents row that has an answer offers to show it ----------

def _row(qapp, analysed):
    host = QWidget()
    lay = QVBoxLayout(host)
    w = fd.ContentRowWidget()
    lay.addWidget(w)
    w.bind(ContentRow(label="Chrome", size_bytes=10 ** 9, path="C:/x/Chrome"),
           selectable=False, drillable=False, analysed=analysed)
    host.show()
    qapp.processEvents()
    return host, w


def test_an_unexplained_row_offers_to_ask(qapp):
    qapp.setStyleSheet(build_qss("forest"))
    host, w = _row(qapp, False)
    try:
        assert w._btn_ask.text() == "Ask AI"
    finally:
        host.deleteLater(); qapp.processEvents()


def test_an_explained_row_offers_to_show(qapp):
    """Saying "Ask AI" over a path already explained invites paying twice for
    the same text."""
    qapp.setStyleSheet(build_qss("forest"))
    host, w = _row(qapp, True)
    try:
        assert w._btn_ask.text() == "View result"
        assert "saved" in w._btn_ask.toolTip().lower()
    finally:
        host.deleteLater(); qapp.processEvents()


def test_both_states_use_the_same_control(qapp):
    """One button, two labels - not a second control appearing beside it."""
    qapp.setStyleSheet(build_qss("forest"))
    a_host, a = _row(qapp, False)
    b_host, b = _row(qapp, True)
    try:
        assert a._btn_ask.objectName() == b._btn_ask.objectName()
        assert a._btn_ask.styleSheet() == b._btn_ask.styleSheet()
    finally:
        a_host.deleteLater(); b_host.deleteLater(); qapp.processEvents()


# -- regeneration steps over the cache, then replaces it ----------

def test_ask_again_marks_the_item_for_regeneration():
    src = inspect.getsource(ax.AIExplainer.explain_item)
    assert "force_refresh" in inspect.signature(ax.AIExplainer.explain_item).parameters
    assert "ai_force_refresh = True" in src


def test_a_forced_ask_skips_the_stored_answer():
    """Without this a re-ask returns the same text instantly - right for
    reopening a result, useless for regenerating one."""
    src = inspect.getsource(ax.AIExplainer)
    assert "if use_cache and not force_refresh" in src


def test_a_forced_ask_still_writes_the_result_back():
    """"Ask again should explicitly generate and replace the cached result" -
    so the write must not be skipped along with the read."""
    src = inspect.getsource(ax.AIExplainer)
    for guard in ("if use_cache:\n", ):
        assert guard in src, "the cache write disappeared"
    assert "if use_cache and not force_refresh:" not in src, (
        "the write was gated on force too, so the old answer would survive")


def test_the_flag_does_not_outlive_one_request():
    """Cleared in the worker, or every later pass over the same item would
    bypass the cache as well."""
    src = inspect.getsource(ax.AIExplainer)
    assert "ai_force_refresh = False" in src
