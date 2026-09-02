"""Nothing on Startups reports a change to Windows that Podbye did not make.

The row carried a Disable/Enable button. Clicking it ran::

    entry.enabled = enabled     # in memory
    self._show_results()        # repaint

so the button flipped to "Enable" — the UI asserting the entry was now
disabled — and Windows was untouched. It could not have been otherwise:
``app/`` contains no registry write of any kind. The caption that once said
"recommendation only · Podbye does not modify startup entries" was still in
every locale file but had stopped being rendered.

The column now states the entry's actual state, and the one route to a real
change is Task Manager, offered in the inspector where the user has just read
what the entry does.

This file is the guard for the general rule rather than for one button: the
screen may claim only what it can do.
"""
import time

import pytest
from PySide6.QtWidgets import QAbstractButton, QLabel, QPushButton

import app.screens.startups as st
from app.models.startup_entry import StartupEntry


def _entry(name="Program", enabled=True, risk="Optional"):
    entry = StartupEntry(
        name=name, command="C:/p.exe", path="C:/p.exe", publisher="Acme",
        source="run_hkcu", source_label="User startup registry",
        enabled=enabled, risk=risk, risk_reason="r", impact="Light utility")
    entry.target_modified = time.time() - 86400
    return entry


# ── the capability that makes all of this true ────────────────────

def test_podbye_cannot_write_to_the_registry_at_all():
    """The floor under every claim on this screen. If a write ever appears,
    the honest UI above it has to be revisited in the same change — not
    discovered later by a user whose startup entry moved on its own.
    """
    import pathlib
    import re

    app_dir = pathlib.Path("app")
    writers = re.compile(r"\b(SetValue|SetValueEx|DeleteValue|DeleteKey|CreateKey|"
                         r"CreateKeyEx|KEY_WRITE|KEY_SET_VALUE|KEY_ALL_ACCESS)\b")
    offenders = []
    for path in app_dir.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if writers.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{path}:{number}: {line.strip()}")
    assert offenders == [], "a registry write appeared:\n  " + "\n  ".join(offenders)


# ── the row states, and does not act ──────────────────────────────

def test_the_state_column_is_not_a_control(qapp):
    row = st.StartupListRow(_entry(enabled=True))

    assert not isinstance(row._state_badge, QAbstractButton)


def test_the_row_reports_the_real_state(qapp):
    on = st.StartupListRow(_entry(enabled=True))
    off = st.StartupListRow(_entry(enabled=False))

    assert on._state_badge.text() == "ON"
    assert off._state_badge.text() == "OFF"


def test_no_row_button_offers_to_change_windows(qapp):
    """Whatever a row grows later, it may not offer an action Podbye cannot
    perform. The state *filter* chips live on the screen, not on a row."""
    row = st.StartupListRow(_entry())
    verbs = ("disable", "enable", "turn off", "turn on", "stop", "remove")

    for button in row.findChildren(QAbstractButton):
        label = (button.text() or "").lower()
        assert not any(v in label for v in verbs), f"row offers {button.text()!r}"


def test_the_screen_has_no_way_to_flip_an_entry(qapp):
    """The mutation path is gone, not merely disconnected."""
    assert not hasattr(st.StartupsScreen, "_set_entry_enabled")
    assert not hasattr(st.StartupListRow, "toggle_requested")
    assert not hasattr(st.StartupListRow, "_on_toggle_clicked")


def test_clicking_through_a_row_leaves_the_entry_untouched(qapp):
    """The property, exercised rather than reasoned about: press everything a
    row offers and the entry's state must be exactly as it started."""
    entry = _entry(enabled=True)
    row = st.StartupListRow(entry)
    row.show()
    qapp.processEvents()
    try:
        for button in row.findChildren(QAbstractButton):
            button.click()
            qapp.processEvents()

        assert entry.enabled is True, "a row control changed the entry"
    finally:
        row.deleteLater()
        qapp.processEvents()


# ── the real route is offered, and named ──────────────────────────

@pytest.fixture
def inspector(qapp):
    panel = st.StartupInspectorPanel()
    panel.resize(420, 800)
    panel.show()
    panel.set_entry(_entry())
    for _ in range(4):
        qapp.processEvents()
    yield panel
    panel.deleteLater()
    qapp.processEvents()


def test_the_inspector_points_at_task_manager(inspector):
    labels = [b.text() for b in inspector.findChildren(QPushButton)
              if b.isVisibleTo(inspector) and b.text()]

    assert any("Task Manager" in t for t in labels), labels


def test_that_button_asks_rather_than_acts(inspector, qapp):
    """It emits a request the screen turns into "open Task Manager". It must
    not quietly become something that edits the entry."""
    asked = []
    inspector.task_manager_requested.connect(lambda: asked.append(1))
    entry = inspector._current_entry

    inspector._tm_btn.click()
    qapp.processEvents()

    assert asked == [1]
    assert entry.enabled is True


def test_the_inspector_says_who_makes_the_change(inspector):
    said = " ".join(l.text() for l in inspector.findChildren(QLabel)
                    if l.isVisibleTo(inspector))

    assert "does not change startup entries" in said
    assert "Windows makes the change" in said


def test_no_inspector_button_claims_to_change_windows(inspector):
    verbs = ("disable", "enable", "turn off", "turn on")
    for button in inspector.findChildren(QPushButton):
        label = (button.text() or "").lower()
        if "task manager" in label:
            continue        # names where the change is made, and goes there
        assert not any(v in label for v in verbs), f"inspector offers {button.text()!r}"


# ── and the classification behind it is untouched ─────────────────

def test_the_state_column_did_not_disturb_risk_or_ai(qapp):
    row = st.StartupListRow(_entry(risk="Review"))
    entry = row._entry

    assert row._risk_badge.text() == "REVIEW"
    assert entry.ai_status == "none"


@pytest.mark.parametrize("language", ["Ukrainian", "German", "Polish",
                                      "Spanish", "French"])
def test_the_state_fits_its_column_in_every_language(qapp, language):
    from PySide6.QtGui import QFont

    from app.fonts import FONT_UI, load_fonts
    from app.i18n import set_language

    previous = qapp.font()
    load_fonts()
    qapp.setFont(QFont(FONT_UI, 10))
    set_language(language)
    try:
        for enabled in (True, False):
            # Held: an unreferenced row is collected and its badge's C++
            # object goes with it, mid-assertion.
            row = st.StartupListRow(_entry(enabled=enabled))
            badge = row._state_badge
            needed = badge.fontMetrics().horizontalAdvance(badge.text())
            assert needed <= st.StartupListRow._ACTION_W - 8, (
                f"{language}: {badge.text()!r} needs {needed}px")
            row.deleteLater()
    finally:
        set_language("English")
        qapp.setFont(previous)
