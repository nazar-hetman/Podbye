"""The General settings page: one column for labels, one axis for controls.

Reviewed for alignment. The column turned out to be consistent already — what
was actually wrong was a description long enough to wrap eight times in a narrow
column beside a single 42px dropdown, which is what made the row look adrift.
These pin the axis so it stays put, and the Apply button's state machine.
"""
import pytest
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QScrollArea

from app.screens.settings import (SettingsScreen, _LABEL_COL_WIDTH,
                                  _setting_row)


@pytest.fixture
def general(qapp):
    s = SettingsScreen()
    s.resize(1200, 900)
    s.show()
    qapp.processEvents()
    page = next(sc for sc in s.findChildren(QScrollArea)
                if "Interface language" in [l.text() for l in sc.findChildren(QLabel)])
    yield s, page
    s.deleteLater()


def _x(widget, root):
    return widget.mapTo(root, widget.rect().topLeft()).x()


# ── one vertical axis ─────────────────────────────────────────────

def test_every_control_starts_on_the_same_axis(general):
    """Theme buttons, the language dropdown and the close-behaviour dropdown
    all begin at the same x, so the page reads as one column of controls."""
    _s, page = general
    xs = {_x(c, page) for c in page.findChildren(QComboBox)}
    theme_btn = next(b for b in page.findChildren(QPushButton) if b.text() == "Forest")
    xs.add(_x(theme_btn, page))
    assert len(xs) == 1, f"controls start at different x positions: {sorted(xs)}"


def test_the_label_column_is_one_width_everywhere(general):
    """Both the label and its description are pinned to the same column, or
    the control axis would move from row to row."""
    _s, page = general
    for key in ("Theme", "Interface language", "When closing while busy"):
        lbl = next(l for l in page.findChildren(QLabel) if l.text() == key)
        assert lbl.width() == _LABEL_COL_WIDTH, f"{key!r} label is {lbl.width()}px"


def test_descriptions_share_the_label_column(general):
    _s, page = general
    starts = ("Color palette", "Affects UI labels", "Podbye can ask")
    found = [l for l in page.findChildren(QLabel) if l.text().startswith(starts)]
    assert len(found) == 3, "a description went missing"
    for d in found:
        assert d.width() == _LABEL_COL_WIDTH


@pytest.mark.parametrize("width", [860, 1000, 1200, 1600])
def test_the_axis_survives_a_resize(qapp, width):
    """The column is fixed, so widening the window must not shift the controls."""
    s = SettingsScreen()
    s.show()
    s.resize(width, 900)
    qapp.processEvents()
    try:
        page = next(sc for sc in s.findChildren(QScrollArea)
                    if "Interface language" in [l.text() for l in sc.findChildren(QLabel)])
        xs = {_x(c, page) for c in page.findChildren(QComboBox)}
        assert len(xs) == 1, f"at {width}px the controls split across {sorted(xs)}"
    finally:
        s.deleteLater()


# ── the tightened description ─────────────────────────────────────

def test_the_close_behaviour_copy_still_offers_all_three_choices(general):
    """Shorter, not vaguer: each option in the dropdown must still be named."""
    _s, page = general
    desc = next(l for l in page.findChildren(QLabel)
                if l.text().startswith("Podbye can ask"))
    text = desc.text().lower()
    assert "ask" in text
    assert "tray" in text
    assert "quit" in text


def test_the_close_behaviour_copy_does_not_restate_its_own_label(general):
    """The label directly above already says "When closing while busy"."""
    _s, page = general
    desc = next(l for l in page.findChildren(QLabel)
                if l.text().startswith("Podbye can ask"))
    assert desc.height() < 70, (
        f"description is {desc.height()}px tall — it used to be 110px, which is "
        f"what made the row look unaligned beside a 42px dropdown")


# ── Apply reflects unapplied changes, nothing else ────────────────

class _Store:
    def __init__(self, language="English"):
        self._d = {"ui_language": language}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set_and_save(self, key, value):
        self._d[key] = value


@pytest.fixture
def lang(general):
    s, _page = general
    s._store = _Store()
    s._lang_combo.blockSignals(True)
    s._lang_combo.setCurrentText("English")
    s._lang_combo.blockSignals(False)
    s._on_language_changed()
    return s


def test_apply_is_disabled_when_nothing_has_changed(lang):
    assert not lang._btn_apply_lang.isEnabled()


def test_apply_wakes_up_for_an_unapplied_change(lang):
    lang._lang_combo.setCurrentText("Ukrainian")
    assert lang._btn_apply_lang.isEnabled()


def test_apply_goes_back_to_sleep_when_the_change_is_undone(lang):
    """Picking a language and picking the saved one again leaves nothing to
    apply — the button must not stay lit on the strength of having been used."""
    lang._lang_combo.setCurrentText("Ukrainian")
    lang._lang_combo.setCurrentText("English")
    assert not lang._btn_apply_lang.isEnabled()


def test_apply_disables_itself_once_the_change_lands(lang):
    lang._lang_combo.setCurrentText("French")
    lang._apply_language()
    assert not lang._btn_apply_lang.isEnabled()
    assert lang._store.get("ui_language") == "French"


def test_applying_with_nothing_pending_writes_nothing(lang):
    """The guard behind the disabled state, in case it is ever reached."""
    before = lang._store.get("ui_language")
    lang._apply_language()
    assert lang._store.get("ui_language") == before


def test_the_disabled_look_is_not_the_enabled_look(lang):
    """Disabled has to read as disabled, or the button lies about its state."""
    qss = lang._btn_apply_lang.styleSheet()
    assert "QPushButton:disabled" in qss
    assert "text_faint" not in qss          # resolved, not a literal key
