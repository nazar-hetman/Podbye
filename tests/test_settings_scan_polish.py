"""The Scan settings page: section state, one statement per fact.

The kept-paths empty state must remain understandable, and File Handling must
state the Recycle Bin default and the irreversibility of emptying it without
claiming that every cleanup outcome is recoverable.
"""
import pytest
from PySide6.QtWidgets import QCheckBox, QLabel

from app.screens.settings import (SettingsScreen, _LABEL_COL_WIDTH,
                                  _SCAN_VALUE_WIDTH)
from app.services import keep_list
from app.themes import theme_manager as tm

_LABEL_COL_X = 37
_VALUE_COL_X = _LABEL_COL_X + _LABEL_COL_WIDTH + 14     # _setting_row spacing


@pytest.fixture
def scan(qapp):
    s = SettingsScreen()
    s.resize(1200, 900)
    s.show()
    s._switch_section("scan")
    qapp.processEvents()
    qapp.processEvents()
    yield s, s._stack.currentWidget()
    s.deleteLater()


def _x(w, root):
    return w.mapTo(root, w.rect().topLeft()).x()


def _labels(root):
    return [l.text() for l in root.findChildren(QLabel)]


# ── Safeguards is left alone ──────────────────────────────────────

def test_safeguards_keeps_both_switches_on_the_control_column(scan):
    s, page = scan
    for cb in (s._cb_confirm_risky, s._cb_cross_volumes):
        assert _x(cb, page) == _VALUE_COL_X
    assert isinstance(s._cb_confirm_risky, QCheckBox)


def test_safeguards_still_defaults_to_confirming(scan):
    """A safety default, not a layout detail."""
    s, _page = scan
    assert s._cb_confirm_risky.isChecked()


# ── the empty state is section state ──────────────────────────────

def test_an_empty_keep_list_replaces_the_row_rather_than_emptying_it(scan, monkeypatch):
    s, _page = scan
    monkeypatch.setattr(keep_list, "kept_paths", lambda: [])
    s._refresh_kept_paths()
    assert not s._keep_row_host.isVisible(), "a labelled row with a blank value"
    assert s._keep_empty_lbl.isVisible()


def test_the_empty_line_spans_the_panel_instead_of_sitting_in_a_column(scan, qapp, monkeypatch):
    s, page = scan
    monkeypatch.setattr(keep_list, "kept_paths", lambda: [])
    s._refresh_kept_paths()
    qapp.processEvents()
    assert _x(s._keep_empty_lbl, page) == _LABEL_COL_X, (
        "the empty state is still indented into the value column")


def test_the_empty_state_still_states_the_safety_rule(scan, monkeypatch):
    """Hiding the row hides its description, which is where the rule lived."""
    s, _page = scan
    monkeypatch.setattr(keep_list, "kept_paths", lambda: [])
    s._refresh_kept_paths()
    text = s._keep_empty_lbl.text().lower()
    assert "bulk action" in text
    assert "refuses" in text


def test_kept_paths_bring_the_row_back(scan, qapp, monkeypatch):
    s, _page = scan
    monkeypatch.setattr(keep_list, "kept_paths",
                        lambda: ["C:/Users/n/thesis", "D:/Photos/2019"])
    s._refresh_kept_paths()
    qapp.processEvents()
    assert s._keep_row_host.isVisible()
    assert not s._keep_empty_lbl.isVisible()
    shown = [l.text() for l in s._keep_list_box.findChildren(QLabel)]
    assert any("thesis" in t or "\u2026" in t for t in shown)


# ── File Handling states each fact once ───────────────────────────

def test_the_cleanup_method_is_a_read_only_value(scan):
    s, _page = scan
    assert s._method_value_lbl.text() == "Recycle Bin"


def test_the_value_sits_on_the_control_column(scan):
    s, page = scan
    assert _x(s._method_value_lbl, page) == _VALUE_COL_X


def test_cleanup_method_names_the_recycle_bin_in_its_value_and_note(scan):
    """The label and its safety note describe the same method clearly."""
    s, page = scan
    naming = [t for t in _labels(page) if "Recycle Bin" in t]
    assert naming == [
        "Recycle Bin",
        "Cleanup uses the Recycle Bin by default. Emptying it is irreversible.",
    ], naming


def test_the_explanation_is_one_line_of_prose_under_the_value(scan, page_note=None):
    s, page = scan
    note = next(l for l in page.findChildren(QLabel)
                if l.text().startswith("Cleanup uses the Recycle Bin"))
    assert _x(note, page) == _VALUE_COL_X
    assert note.width() == _SCAN_VALUE_WIDTH, (
        "a wrapping QLabel reports a narrow size hint; without a pinned width "
        "the container squeezed this to 165px and eight lines")


def test_the_explanation_keeps_all_three_guarantees(scan):
    """The setting describes the default and the irreversible Bin action."""
    s, page = scan
    note = next(l for l in page.findChildren(QLabel)
                if l.text().startswith("Cleanup uses the Recycle Bin")).text().lower()
    assert "recycle bin by default" in note
    assert "irreversible" in note


# ── muted text is readable in every theme ─────────────────────────

def _relative_luminance(hex_color):
    h = hex_color.lstrip("#")
    ch = []
    for i in (0, 2, 4):
        v = int(h[i:i + 2], 16) / 255
        ch.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def _contrast(a, b):
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", ["forest", "amber", "mono", "paper"])
def test_muted_text_is_readable_on_every_surface(theme):
    """Descriptions and the File Handling note are #Dim at 11px — normal text,
    so 4.5. Paper sat at 4.24 against its darkest surface."""
    p = tm.PALETTES[theme]
    for surface in ("bg", "panel", "panel_alt", "tint_bg"):
        ratio = _contrast(p["text_dim"], p[surface])
        assert ratio >= 4.5, (
            f"{theme}: muted text {p['text_dim']} on {surface} {p[surface]} "
            f"is {ratio:.2f}")
