"""Startups: a list that is true when you open it, and states you can see.

The page never refreshed itself, so a list built on a previous visit described
a machine that had since had software installed and removed. Enable/Disable -
the one control the screen exists to offer - was styled as bare text. A
disabled row was tinted rgba(138, 155, 143, 9): alpha 9 of 255, a fixed
forest-ish colour, invisible on every theme and wrong on three of them.
"""
import pytest

import app.screens.startups as st
import app.services.startup_detector as det
from app.models.startup_entry import StartupEntry
from app.themes import theme_manager as tm
from app.themes.theme_manager import build_qss

_THEMES = ["forest", "amber", "mono", "paper"]


def _entry(name, enabled=True, ai="ready", risk="Optional"):
    e = StartupEntry(
        name=name, command="C:/" + name + ".exe", path="C:/" + name + ".exe",
        publisher="Acme", source="run_hkcu", source_label="Registry (User)",
        enabled=enabled, risk=risk, risk_reason="r", impact="i")
    e.ai_status = ai
    e.ai_explanation = "Because."
    return e


class _Store:
    """Startup AI off, so no worker is ever spawned by these tests."""

    def get(self, key, default=None):
        return False if key == "ai_startups_enabled" else default


@pytest.fixture(autouse=True)
def _fresh_refresh_clock():
    """The refresh rate limit is class-level, so it outlives an instance.

    That is deliberate in the app - the limit belongs to the machine's startup
    list, not to a widget - but it means one test's refresh would suppress the
    next one's. Each test starts with the clock cleared.
    """
    st.StartupsScreen._last_refresh = 0.0
    yield
    st.StartupsScreen._last_refresh = 0.0


@pytest.fixture
def screen(qapp):
    s = st.StartupsScreen()
    s._settings_store = _Store()
    yield s
    s.deleteLater()
    qapp.processEvents()


# -- the list refreshes itself ------------------------------------

def test_opening_the_page_re_reads_the_machine(screen, qapp, monkeypatch):
    calls = {"n": 0}

    def _detect():
        calls["n"] += 1
        return [_entry("Alpha")]

    monkeypatch.setattr(det, "detect_startup_entries", _detect)
    screen._entries = [_entry("Alpha")]      # already analysed once
    screen._reapply_filters()
    screen.show()
    qapp.processEvents()
    assert calls["n"] >= 1, "the page did not refresh on open"


def test_an_unanalysed_page_does_not_walk_the_registry_on_its_own(screen, qapp, monkeypatch):
    """The first read stays the explicit action the idle page already offers.

    Otherwise merely opening the tab spends a third of a second in the
    registry and the Startup folders for a user who has not asked for
    anything yet.
    """
    calls = {"n": 0}

    def _detect():
        calls["n"] += 1
        return [_entry("Alpha")]

    monkeypatch.setattr(det, "detect_startup_entries", _detect)
    assert not screen._entries
    screen.show()
    qapp.processEvents()
    assert calls["n"] == 0


def test_a_refresh_keeps_the_rows_that_are_still_there(screen, qapp, monkeypatch):
    """Nothing is cleared first: reopening must not blank a list mid-read."""
    screen._entries = [_entry("Alpha"), _entry("Beta")]
    screen._reapply_filters()
    screen._show_results()
    qapp.processEvents()

    monkeypatch.setattr(det, "detect_startup_entries",
                        lambda: [_entry("Alpha", ai="none"), _entry("Gamma", ai="none")])
    screen._refresh_entries()
    assert sorted(e.name for e in screen._entries) == ["Alpha", "Gamma"]


def test_a_refresh_does_not_throw_away_ai_verdicts(screen, monkeypatch):
    """Re-running the model on every entry is what Re-analyze is for."""
    screen._entries = [_entry("Alpha", ai="ready")]
    screen._reapply_filters()
    monkeypatch.setattr(det, "detect_startup_entries",
                        lambda: [_entry("Alpha", ai="none")])
    screen._refresh_entries()
    alpha = next(e for e in screen._entries if e.name == "Alpha")
    assert alpha.ai_status == "ready"
    assert alpha.ai_explanation == "Because."


def test_a_refresh_never_starts_the_model(screen, monkeypatch):
    """An automatic refresh may not start work that costs money and minutes.

    The worker analyses every entry it is handed and has no skip for one that
    already has a verdict, so a refresh that kicked it off would quietly do
    the expensive thing Re-analyze exists to do - on every visit to the page.
    """
    started = []
    monkeypatch.setattr(screen, "_start_ai", lambda entries=None: started.append(entries))
    screen._entries = [_entry("Alpha", ai="ready")]
    screen._reapply_filters()
    monkeypatch.setattr(det, "detect_startup_entries",
                        lambda: [_entry("Alpha", ai="none"), _entry("Gamma", ai="none")])
    screen._refresh_entries()
    assert not started, "the automatic refresh started an AI run"
    assert sorted(e.name for e in screen._entries) == ["Alpha", "Gamma"]


def test_repeated_shows_do_not_re_read_the_registry(screen, qapp, monkeypatch):
    """Reading the registry and the Startup folders costs ~0.3s, and a screen
    can be shown several times while a window settles."""
    calls = {"n": 0}

    def _detect():
        calls["n"] += 1
        return [_entry("Alpha")]

    monkeypatch.setattr(det, "detect_startup_entries", _detect)
    screen._entries = [_entry("Alpha")]      # already analysed once
    screen._reapply_filters()
    screen.show()
    qapp.processEvents()
    screen.hide()
    screen.show()
    qapp.processEvents()
    assert calls["n"] == 1, "the registry was re-read on a rapid re-show"


def test_a_selection_that_no_longer_exists_is_dropped(screen, monkeypatch):
    screen._entries = [_entry("Alpha")]
    screen._reapply_filters()
    screen._selected_key = screen._entries[0].key
    monkeypatch.setattr(det, "detect_startup_entries", lambda: [_entry("Gamma")])
    screen._refresh_entries()
    assert screen._selected_key is None


def test_a_refresh_never_breaks_the_page(screen, monkeypatch):
    """It runs because the page opened, not because anyone asked."""
    screen._entries = [_entry("Alpha")]
    screen._reapply_filters()

    def _boom():
        raise OSError("registry unavailable")

    monkeypatch.setattr(det, "detect_startup_entries", _boom)
    screen._refresh_entries()
    assert [e.name for e in screen._entries] == ["Alpha"]


def test_re_analyze_is_still_the_full_run(screen, monkeypatch):
    """The button re-reads and re-analyses everything, subset or not."""
    sent = {}
    monkeypatch.setattr(screen, "_start_ai", lambda entries=None: sent.update(e=entries))
    monkeypatch.setattr(det, "detect_startup_entries",
                        lambda: [_entry("Alpha", ai="ready"), _entry("Beta", ai="ready")])
    screen._analyze()
    assert sent.get("e", "missing") is None, "Re-analyze must not pass a subset"


# -- the toggle is a control --------------------------------------

@pytest.mark.parametrize("theme", _THEMES)
@pytest.mark.parametrize("enabled", [True, False])
def test_enable_disable_reads_as_a_button(qapp, theme, enabled):
    tm._current_theme_key = theme
    qapp.setStyleSheet(build_qss(theme))
    qss = st.StartupListRow._toggle_style(enabled)
    assert "border: 1px solid" in qss, theme + ": no border, still reads as text"
    assert "background: transparent" not in qss, theme + ": no fill"
    assert "QPushButton:hover" in qss, theme + ": no hover affordance"


def test_the_toggle_still_says_what_it_will_do(qapp):
    assert st.StartupListRow(_entry("On", enabled=True))._toggle_btn.text() == "Disable"
    assert st.StartupListRow(_entry("Off", enabled=False))._toggle_btn.text() == "Enable"


# -- disabled and selected are visible states ---------------------

def _luminance(hex_color):
    h = hex_color.lstrip("#")
    ch = []
    for i in (0, 2, 4):
        v = int(h[i:i + 2], 16) / 255
        ch.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def _contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", _THEMES)
def test_a_disabled_row_is_visibly_recessed(qapp, theme):
    """alpha 9 of 255 was not a state, it was a rounding error."""
    tm._current_theme_key = theme
    qapp.setStyleSheet(build_qss(theme))
    row = st.StartupListRow(_entry("Off", enabled=False))
    bg = row.styleSheet().split("background:")[1].split(";")[0].strip()
    assert bg != "transparent"
    p = tm.PALETTES[theme]
    assert _contrast(bg, p["panel"]) >= 1.08, theme + ": disabled row is not distinguishable"


@pytest.mark.parametrize("theme", _THEMES)
def test_an_enabled_row_stays_plain(qapp, theme):
    tm._current_theme_key = theme
    qapp.setStyleSheet(build_qss(theme))
    row = st.StartupListRow(_entry("On", enabled=True))
    assert "transparent" in row.styleSheet()


@pytest.mark.parametrize("theme", _THEMES)
def test_the_selected_row_is_marked_by_its_accent(qapp, theme):
    """Not by a hairline in the same border colour every other state uses."""
    tm._current_theme_key = theme
    qapp.setStyleSheet(build_qss(theme))
    row = st.StartupListRow(_entry("On"))
    row.set_selected(True)
    qss = row.styleSheet()
    accent = tm.PALETTES[theme]["accent"]
    assert "border-left: 4px solid" in qss
    assert accent in qss, theme + ": selection does not carry the accent"


@pytest.mark.parametrize("theme", _THEMES)
def test_selection_separates_from_an_ordinary_row_in_every_theme(theme):
    p = tm.PALETTES[theme]
    assert _contrast(p["accent_soft"], p["panel"]) >= 1.15, (
        theme + ": a selected row barely differs from an unselected one")


# -- the conclusion outranks the explanation ----------------------

@pytest.mark.parametrize("theme", _THEMES)
def test_the_recommendation_carries_the_container(qapp, theme):
    """It had none while contextual reasoning sat in a bordered box, which put
    the frame around the explanation and left the verdict looking like a
    caption above it."""
    tm._current_theme_key = theme
    qapp.setStyleSheet(build_qss(theme))
    panel = st.StartupInspectorPanel()
    try:
        qss = panel._recommendation_frame.styleSheet()
        assert "border: 1px solid" in qss, theme + ": recommendation has no frame"
        assert "background: rgba" in qss, theme + ": recommendation has no tint"
    finally:
        panel.deleteLater()


@pytest.mark.parametrize("theme", _THEMES)
def test_contextual_reasoning_steps_back(qapp, theme):
    """Supporting material: a left rule keeps it as one passage without
    boxing it like a conclusion."""
    tm._current_theme_key = theme
    qapp.setStyleSheet(build_qss(theme))
    panel = st.StartupInspectorPanel()
    try:
        qss = panel._explanation_host.styleSheet()
        assert "border: none" in qss, theme + ": reasoning is still boxed"
        assert "border-left" in qss, theme + ": reasoning lost its grouping rule"
    finally:
        panel.deleteLater()


def test_the_recommendation_tint_follows_the_verdict(qapp):
    """A risky entry and a safe one should not carry the same weight."""
    from PySide6.QtGui import QColor

    tm._current_theme_key = "forest"
    qapp.setStyleSheet(build_qss("forest"))
    panel = st.StartupInspectorPanel()
    try:
        for accent in (tm.PALETTES["forest"]["safe"], tm.PALETTES["forest"]["risk"]):
            panel._apply_recommendation_style(accent)
            q = QColor(accent)
            channels = str(q.red()) + ", " + str(q.green()) + ", " + str(q.blue())
            assert channels in panel._recommendation_frame.styleSheet()
    finally:
        panel.deleteLater()
