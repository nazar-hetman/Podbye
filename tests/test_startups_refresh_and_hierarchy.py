"""Startups: a list that is true when you open it, and states you can see.

The page never refreshed itself, so a list built on a previous visit described
a machine that had since had software installed and removed. Enable/Disable -
the one control the screen exists to offer - was styled as bare text. A
disabled row was tinted rgba(138, 155, 143, 9): alpha 9 of 255, a fixed
forest-ish colour, invisible on every theme and wrong on three of them.
"""
import pytest
from PySide6.QtWidgets import QLabel

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


def test_an_unanalysed_page_walks_the_registry_for_itself(screen, qapp, monkeypatch):
    """Reversed deliberately. This used to assert the opposite — that the first
    read stays an explicit click — on the grounds that a third of a second is
    too much to spend for a user who has not asked for anything.

    It buys nothing: the click has no decision behind it, because the page has
    nothing to show until it is pressed, so the only possible answer is yes.
    The cost that argument was protecting is 275ms, measured over three runs.
    What must stay behind a decision is the model, which costs minutes and
    sometimes money — see the test below.
    """
    calls = {"n": 0}

    def _detect():
        calls["n"] += 1
        return [_entry("Alpha")]

    monkeypatch.setattr(det, "detect_startup_entries", _detect)
    assert not screen._entries
    screen.show()
    for _ in range(10):
        qapp.processEvents()
    assert calls["n"] == 1
    assert [e.name for e in screen._entries] == ["Alpha"]


def test_opening_the_page_twice_reads_the_machine_once_per_visit(screen, qapp, monkeypatch):
    """The auto-detect is a first-visit path, not a second refresh on top of
    the one showEvent already did."""
    calls = {"n": 0}

    def _detect():
        calls["n"] += 1
        return [_entry("Alpha")]

    monkeypatch.setattr(det, "detect_startup_entries", _detect)
    screen.show()
    for _ in range(10):
        qapp.processEvents()

    assert calls["n"] == 1, "detected twice on one open"


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
    """alpha 9 of 255 was not a state, it was a rounding error.

    Measured against PanelAlt, which is the surface the list actually draws on
    — this compared against Panel, one step off, and the value that comfortably
    cleared the old bar was bg_deep, reported from a screenshot as an odd black
    shape sitting in the list. bg is the step between: 1.148 forest, 1.145
    amber, 1.188 mono, 1.076 paper.
    """
    tm._current_theme_key = theme
    qapp.setStyleSheet(build_qss(theme))
    row = st.StartupListRow(_entry("Off", enabled=False))
    bg = row.styleSheet().split("background:")[1].split(";")[0].strip()
    assert bg != "transparent"
    p = tm.PALETTES[theme]
    assert _contrast(bg, p["panel_alt"]) >= 1.07, (
        theme + ": disabled row is not distinguishable from the list")
    assert _contrast(bg, p["panel_alt"]) < _contrast(p["bg_deep"], p["panel_alt"]), (
        theme + ": as dark as the black-puck value it replaced")


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
#
# It has done this three ways now. The reasoning was the only boxed section,
# which put the frame around the explanation and left the verdict looking like
# a caption above it; the box then moved to the verdict, tinted in the accent,
# with a rule left behind to fence off the reasoning. That made the verdict the
# loudest thing on the screen — louder than the entry's own name — and unlike
# anything in Findings, which states the same kind of verdict as plain prose
# with the severity on a small outlined chip.
#
# So neither is boxed. The verdict leads by position and weight, the chip
# carries the colour, and the reasoning follows it as the last section of the
# same column.


@pytest.mark.parametrize("theme", _THEMES)
def test_the_verdict_is_not_a_box(qapp, theme):
    tm._current_theme_key = theme
    qapp.setStyleSheet(build_qss(theme))
    panel = st.StartupInspectorPanel()
    try:
        qss = panel._recommendation_frame.styleSheet()
        assert "border: none" in qss, theme + ": the verdict is boxed again"
        assert "rgba" not in qss, theme + ": the verdict is tinted again"
    finally:
        panel.deleteLater()


@pytest.mark.parametrize("theme", _THEMES)
def test_the_assessment_is_grouped_not_fenced(qapp, theme):
    """Reported: with every section drawn as a heading over prose, the
    assessment blended into IMPACT, RECOMMENDATION and the metadata above it.

    A left rule was the wrong answer — it fenced the block off as though it
    came from somewhere else. It takes the same box the Findings inspector
    gives its generated lists, which is what the rest of the app already means
    by "this part was produced for you".
    """
    tm._current_theme_key = theme
    qapp.setStyleSheet(build_qss(theme))
    panel = st.StartupInspectorPanel()
    try:
        qss = panel._explanation_host.styleSheet()
        assert "1px solid" in qss, theme + ": the assessment has no container"
        assert "border-left" not in qss, theme + ": fenced rather than grouped"
        assert "rgba" not in qss, theme + ": tinted like the old verdict box"
    finally:
        panel.deleteLater()


def test_only_the_assessment_is_boxed(qapp):
    """One box is a grouping; four is the wallpaper this panel started with."""
    panel = st.StartupInspectorPanel()
    try:
        for frame in (panel._impact_frame, panel._recommendation_frame,
                      panel._action_frame):
            assert "border: none" in frame.styleSheet()
    finally:
        panel.deleteLater()


def test_the_verdict_still_outranks_the_prose_that_supports_it(qapp):
    """Position and weight do it now, in place of a container."""
    panel = st.StartupInspectorPanel()
    try:
        assert "font-weight: 650" in panel._rec_text_lbl.styleSheet()
        assert "font-weight" not in panel._explanation_lbl.styleSheet()
    finally:
        panel.deleteLater()


def test_the_verdict_colour_follows_the_verdict(qapp):
    """A risky entry and a safe one must not read the same. The colour moved
    from a tinted panel to the chip that states the verdict, which was already
    the thing saying BOOT IMPACT or NEEDS REVIEW in that exact colour."""
    from PySide6.QtGui import QColor

    tm._current_theme_key = "forest"
    qapp.setStyleSheet(build_qss("forest"))
    panel = st.StartupInspectorPanel()
    try:
        for accent in (tm.PALETTES["forest"]["safe"], tm.PALETTES["forest"]["risk"]):
            panel._apply_recommendation_style(accent)
            assert accent in panel._rec_status_lbl.styleSheet()
            q = QColor(accent)
            channels = f"{q.red()}, {q.green()}, {q.blue()}"
            assert channels in panel._rec_status_lbl.styleSheet(), "chip lost its border"
    finally:
        panel.deleteLater()


# -- the panel says who is talking ---------------------------------

def test_the_recommendation_is_not_attributed_to_the_model(qapp):
    """_startup_recommendation() reads the entry's risk, whether the publisher
    could be verified, and whether the role works at login. Rules, all of it,
    and it answers whether AI ran or not.

    Headed "AI RECOMMENDATIONS" the panel said two contradictory things on the
    same screen: a recommendation credited to AI, above a line reading "AI
    disabled for this entry".
    """
    panel = st.StartupInspectorPanel()
    try:
        headings = [w.text() for w in panel.findChildren(QLabel)]
        assert "RECOMMENDATION" in headings
        assert not any("AI RECOMMENDATION" in h for h in headings)
    finally:
        panel.deleteLater()


def test_the_rules_still_speak_when_ai_is_off(qapp):
    """Which is the point of them: the verdict does not depend on the model."""
    entry = _entry("Grammarly", risk="Optional", ai="disabled")
    panel = st.StartupInspectorPanel()
    try:
        panel.set_entry(entry)

        assert "Recommendation" in panel._rec_text_lbl.text()
        assert panel._rec_status_lbl.text()
    finally:
        panel.deleteLater()


def test_the_assessment_section_is_named_like_findings(qapp):
    """Both screens hold the same kind of text there — the model's when it ran,
    the rules' when it did not — so both say PODBYE ASSESSMENT and let the
    state beside the heading name the author."""
    panel = st.StartupInspectorPanel()
    try:
        headings = [w.text() for w in panel.findChildren(QLabel)]
        assert "PODBYE ASSESSMENT" in headings
    finally:
        panel.deleteLater()


# -- asking again ---------------------------------------------------

def test_a_startup_with_an_answer_can_be_asked_again(qapp):
    """It used to hide the button the moment an answer arrived, so the point
    at which re-asking becomes useful was the point the control went away."""
    entry = _entry("Grammarly", ai="ready")
    panel = st.StartupInspectorPanel(ask_ai_cb=lambda e: "")
    try:
        panel.set_entry(entry)

        assert panel._ask_ai_btn.isVisible() or panel._ask_ai_visible_for(entry)
        assert panel._ask_ai_btn.text() == "Ask again"
        assert "replace" in panel._ask_ai_btn.toolTip().lower()
    finally:
        panel.deleteLater()


def test_an_unexplained_startup_is_asked_not_re_asked(qapp):
    entry = _entry("Grammarly", ai="none")
    panel = st.StartupInspectorPanel(ask_ai_cb=lambda e: "")
    try:
        panel.set_entry(entry)

        assert panel._ask_ai_btn.text() == "Ask AI"
    finally:
        panel.deleteLater()


def test_nothing_is_offered_while_it_is_already_running(qapp):
    panel = st.StartupInspectorPanel(ask_ai_cb=lambda e: "")
    try:
        for state in ("analyzing", "pending"):
            assert not panel._ask_ai_visible_for(_entry("X", ai=state))
    finally:
        panel.deleteLater()


def test_re_asking_regenerates_rather_than_reusing(qapp):
    """The startup worker has no cache to bypass — it calls the model every
    time — so the guarantee is that the entry is cleared and re-run."""
    import inspect
    src = inspect.getsource(st.StartupAIWorker.run)

    assert "_load_cached" not in src and "cached" not in src
