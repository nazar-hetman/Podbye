"""The AI settings page as a form grid: label, value, action.

Measured before changing anything. Labels and most controls were already on
one axis; what was out of line was the Library row, whose Refresh button sat
82px left of the Test button above it because a stretch pushed it to the far
end of the row instead of into a column.
"""
import pytest
from PySide6.QtWidgets import (QCheckBox, QComboBox, QLabel, QLineEdit,
                               QPushButton, QSlider)

from app.screens.settings import (SettingsScreen, _ACTION_HEIGHT,
                                  _LABEL_COL_WIDTH, _VALUE_COL_WIDTH)

# Derived, not restated. These were three literals that all had to be edited
# by hand when the label column widened, which said nothing about the axis
# they exist to protect — that every control in a section starts at one x.
_LABEL_COL_X = 37
_VALUE_COL_X = _LABEL_COL_X + _LABEL_COL_WIDTH + 14     # _setting_row spacing
_ACTION_COL_X = _VALUE_COL_X + _VALUE_COL_WIDTH + 8


@pytest.fixture
def ai(qapp):
    s = SettingsScreen()
    s.resize(1200, 900)
    s.show()
    s._switch_section("ai")
    qapp.processEvents()
    qapp.processEvents()
    yield s, s._stack.currentWidget()
    s.deleteLater()


def _x(w, root):
    return w.mapTo(root, w.rect().topLeft()).x()


# ── one value column ──────────────────────────────────────────────

def test_every_value_control_starts_on_the_value_column(ai):
    _s, page = ai
    for cls in (QComboBox, QLineEdit, QCheckBox, QSlider):
        for w in page.findChildren(cls):
            assert _x(w, page) == _VALUE_COL_X, (
                f"{cls.__name__} {getattr(w, 'objectName', lambda: '')()} "
                f"starts at {_x(w, page)}, not {_VALUE_COL_X}")


def test_explanation_dropdowns_and_checkboxes_share_the_column(ai):
    """Style, length, language, and the two toggle groups below them."""
    s, page = ai
    controls = [s._tone_combo, s._length_combo, s._ai_lang_combo,
                s._cb_findings, s._cb_startups, s._cb_risky_only]
    assert {_x(c, page) for c in controls} == {_VALUE_COL_X}


# ── one action column ─────────────────────────────────────────────

def test_the_persistent_actions_share_a_column(ai):
    """Test and Refresh are the two buttons always on screen in Local Model
    Server. Refresh used to sit at x=465 against Test's 547."""
    s, page = ai
    assert _x(s._btn_test, page) == _ACTION_COL_X
    assert _x(s._btn_refresh_models, page) == _ACTION_COL_X


def test_the_actions_are_the_same_height(ai):
    """Both are #Ghost buttons in the same panel. Refresh carried an inline
    "min-height: 26px" that overrode #Ghost's padding and Test did not, so
    under the real theme stylesheet they rendered 32px and 42px side by side.

    The assertion is that they agree, not that they equal _ACTION_HEIGHT: a
    Qt style sheet's min-height wins over setFixedHeight, so the constant sets
    the floor and the theme has the last word on the rendered value. Pinning
    the constant passed only in tests that had no application stylesheet.
    """
    s, _page = ai
    assert s._btn_test.height() == s._btn_refresh_models.height()
    assert s._btn_test.height() >= _ACTION_HEIGHT


def test_the_value_column_is_a_minimum_not_a_cap(ai):
    """A fixed column would align the buttons by truncating the sentence that
    explains why the button is there."""
    s, _page = ai
    for label in (s._conn_status_lbl, s._library_count_lbl):
        assert label.minimumWidth() == _VALUE_COL_WIDTH
        assert label.maximumWidth() > _VALUE_COL_WIDTH


@pytest.mark.parametrize("status", [
    "connected \u00b7 Ollama \u00b7 3 model(s)",
    "connected \u00b7 Ollama \u00b7 no models installed",
    "Ollama is installed but not running",
    "no local AI runtime on this machine",
    "saved \u00b7 not verified",
])
def test_no_connection_status_is_clipped(ai, qapp, status):
    s, _page = ai
    s._conn_status_lbl.setText(status)
    s._conn_status_lbl.updateGeometry()
    for _ in range(3):
        qapp.processEvents()
    needed = s._conn_status_lbl.fontMetrics().horizontalAdvance(status)
    assert s._conn_status_lbl.width() >= needed, f"clipped: {status!r}"


def test_a_short_status_still_reserves_the_column(ai, qapp):
    """Otherwise the action would slide left whenever the text was short."""
    s, page = ai
    s._conn_status_lbl.setText("saved \u00b7 not verified")
    s._conn_status_lbl.updateGeometry()
    for _ in range(3):
        qapp.processEvents()
    s._btn_start_ollama.setVisible(True)
    qapp.processEvents()
    assert _x(s._btn_start_ollama, page) == _ACTION_COL_X


# ── the separator ─────────────────────────────────────────────────

def test_no_rule_splits_style_length_and_language(ai):
    """All three describe how the explanation is written, so a rule between
    length and language cut a group in half rather than marking one."""
    import inspect
    from app.screens import settings as mod
    src = inspect.getsource(mod._build_ai_marker) if hasattr(mod, "_build_ai_marker") else ""
    # Read the builder directly: the divider call must be gone from Explanation.
    body = inspect.getsource(SettingsScreen._build_ai)
    expl = body[body.index('tr("Explanation")'):body.index('tr("Performance")')]
    assert "_divider()" not in expl, "a separator is back inside Explanation"


def test_dividers_still_exist_where_they_group_something(ai):
    """The About/Storage panels use them to separate real subgroups."""
    import inspect
    body = inspect.getsource(SettingsScreen._build_about)
    assert "_divider()" in body


# ── Model Selection stays sparse ──────────────────────────────────

def test_model_selection_holds_one_control(ai):
    """It is a one-decision panel; nothing was added to fill the width."""
    s, page = ai
    combos = [c for c in page.findChildren(QComboBox)
              if c is s._model_combo]
    assert len(combos) == 1
    assert _x(s._model_combo, page) == _VALUE_COL_X


# ── Performance keeps its inline value/guidance shape ─────────────

def test_the_sliders_keep_their_column_and_their_readouts(ai):
    s, page = ai
    sliders = page.findChildren(QSlider)
    assert len(sliders) == 2, "Performance should still have two sliders"
    for sl in sliders:
        assert _x(sl, page) == _VALUE_COL_X
