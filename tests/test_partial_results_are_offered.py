"""A stopped analysis has results; it just does not have all of them.

`stop_all()` logs "partial results preserved" and the session is written with
status "stopped", but Findings only ever offered Resume — the findings the walk
had already collected were reachable from nowhere. A user who stopped a scan on
a 4TB drive after twenty minutes could either spend twenty more or see nothing.

The other half of the problem is the opposite risk. Those results are a floor,
not a total: a category showing 2GB may hold 200GB in the part never visited,
and an entity absent here may exist on disk. So the partial view is the
*ordinary* Findings UI — same dashboard, same category lists, same folder tree,
reading the same scan state — with one persistent strip saying what it is.

Two things are therefore tested together: that the door exists, and that
walking through it never lets the results pass as a completed scan.
"""
import pytest
from PySide6.QtWidgets import QLabel, QPushButton

import app.screens.findings_dashboard as fd
from app.models.finding import Finding
from app.state.scan_state import ScanState
from app.themes.theme_manager import build_qss


@pytest.fixture
def dressed(qapp):
    qapp.setStyleSheet(build_qss("forest"))
    return qapp


def _findings(n):
    return [Finding(path=f"C:/x/f{i}.tmp", name=f"f{i}.tmp", is_dir=False,
                    size_bytes=1024 ** 2, extension=".tmp", modified=1,
                    accessed=1, parent="C:/x") for i in range(n)]


def _stopped_state(found=40):
    """A scan that ran, collected *found* findings, and was stopped.

    Deliberately goes through stop_all() rather than setting the phase by
    hand: it is what cancels entity detection, which is why a stopped run
    normally has findings and no entities at all.
    """
    ss = ScanState()
    ss.set_running(True, "C:/")
    if found:
        ss.add_findings(_findings(found))
    ss.stop_all()
    ss.set_running(False)
    return ss


@pytest.fixture
def dash(dressed):
    made = []

    def build(scan_state):
        d = fd.FindingsDashboard(scan_state=scan_state)
        d.resize(1400, 900)
        d.show()
        for _ in range(8):
            dressed.processEvents()
        made.append(d)
        return d

    yield build
    for d in made:
        d.deleteLater()
    dressed.processEvents()


def _saved_session(monkeypatch, status="stopped"):
    """The preserved session stop_all() writes, without touching the disk."""
    data = {"status": status, "target": "C:/", "scan_mode": "smart",
            "scanned_count": 40, "total_size": 40 * 1024 ** 2}
    monkeypatch.setattr("app.state.session_store.load_session_summary",
                        lambda: dict(data))
    return data


# ── the stopped screen offers both paths ──────────────────────────

def test_resume_is_still_the_primary_action(dash):
    """Finishing the walk is what the screen is for. The new action must not
    become the one that reads as the answer."""
    d = dash(_stopped_state())
    view = d._stopped_view

    assert view._resume_btn.objectName() == "Primary"
    assert view._partial_btn.objectName() != "Primary"


def test_preserved_results_are_reachable(dash):
    d = dash(_stopped_state())

    assert d._current_view == "stopped"
    assert d._stopped_view._partial_btn.isVisible()
    assert d._stopped_view._partial_btn.text() == "View partial results"


def test_the_screen_says_how_much_was_preserved(dash):
    """"Partial results have been preserved" gave the user nothing to decide
    on. Forty items and forty megabytes does."""
    d = dash(_stopped_state(40))
    said = d._stopped_view._desc.text()

    assert "40" in said
    assert "MB" in said


def test_a_stop_that_preserved_nothing_offers_no_second_action(dash):
    """A stop in the first seconds of a walk leaves nothing behind, and a
    button leading to an empty dashboard is worse than no button."""
    d = dash(_stopped_state(found=0))

    assert not d._stopped_view._partial_btn.isVisible()
    assert "Nothing was found" in d._stopped_view._desc.text()


def test_the_empty_case_cannot_be_opened_even_if_the_action_fires(dash):
    """Visibility is a hint, not a guard — the handler refuses on its own."""
    d = dash(_stopped_state(found=0))

    d._on_view_partial_results()

    assert d._current_view == "stopped"
    assert not d._partial_notice.isVisible()


# ── what opens is the normal UI, marked ───────────────────────────

def test_partial_results_open_the_ordinary_dashboard(dash):
    """Not a separate results mode: the same view the completed scan uses,
    reading the same scan state."""
    d = dash(_stopped_state())

    d._stopped_view._partial_btn.click()

    assert d._current_view == "dashboard"
    assert d._stack.currentWidget() is d._dashboard_container


def test_the_notice_says_the_results_are_incomplete(dash):
    d = dash(_stopped_state())
    d._on_view_partial_results()

    assert d._partial_notice.isVisible()
    assert d._partial_notice._badge.text() == "PARTIAL RESULTS"
    said = d._partial_notice._text.text()
    assert "stopped" in said.lower()
    assert "missing" in said.lower() or "never scanned" in said.lower()


def test_the_notice_states_what_is_actually_on_screen(dash):
    """A count makes the gap a quantity. Without one the strip is a mood."""
    d = dash(_stopped_state(40))
    d._on_view_partial_results()

    assert "40" in d._partial_notice._text.text()


def test_the_notice_offers_the_resume_the_user_deferred(dash, monkeypatch):
    d = dash(_stopped_state())
    d._on_view_partial_results()
    _saved_session(monkeypatch)
    resumed = []
    d.resume_requested.connect(lambda data: resumed.append(data))

    d._partial_notice._resume_btn.click()

    assert len(resumed) == 1
    assert resumed[0]["status"] == "stopped"


def test_resume_continues_the_run_rather_than_starting_one(dash, monkeypatch):
    """The reason this is a signal and not a navigation.

    "Resume Analysis →" only ever called navigate_to_analyze, and Analyze
    greets an arrival with "Start scan" — which begins a fresh walk and
    throws away the preserved findings the button was offering to keep. It
    now goes through the same handler as the Home screen's Resume Scan, which
    is the one path that hands the saved session to resume_scan().
    """
    d = dash(_stopped_state())
    _saved_session(monkeypatch)
    resumed, navigated = [], []
    d.resume_requested.connect(lambda data: resumed.append(data))
    d.navigate_to_analyze.connect(lambda: navigated.append(1))

    d._stopped_view._resume_btn.click()

    assert len(resumed) == 1, "resume fell back to a fresh scan"
    assert navigated == [], "both fired; Analyze would be told two things"


def test_both_resume_actions_are_the_same_flow(dash, monkeypatch):
    """One resume path, reached from two places."""
    d = dash(_stopped_state())
    _saved_session(monkeypatch)
    resumed = []
    d.resume_requested.connect(lambda data: resumed.append(data))

    d._stopped_view._resume_btn.click()
    d._on_view_partial_results()
    d._partial_notice._resume_btn.click()

    assert len(resumed) == 2
    assert resumed[0] == resumed[1]


def test_resume_still_navigates_when_no_session_was_saved(dash, monkeypatch):
    """A stop big enough to be written in the background may not have landed.
    A button that does nothing is worse than one that opens Analyze."""
    d = dash(_stopped_state())
    monkeypatch.setattr("app.state.session_store.load_session_summary",
                        lambda: None)
    resumed, navigated = [], []
    d.resume_requested.connect(lambda data: resumed.append(data))
    d.navigate_to_analyze.connect(lambda: navigated.append(1))

    d._stopped_view._resume_btn.click()

    assert resumed == []
    assert navigated == [1]


def test_a_finished_session_is_not_resumed(dash, monkeypatch):
    """Only stopped and running sessions can be continued. A completed one on
    disk must not turn Resume into a replay of a finished scan."""
    d = dash(_stopped_state())
    monkeypatch.setattr("app.state.session_store.load_session_summary",
                        lambda: {"status": "completed", "target": "C:/"})
    resumed, navigated = [], []
    d.resume_requested.connect(lambda data: resumed.append(data))
    d.navigate_to_analyze.connect(lambda: navigated.append(1))

    d._stopped_view._resume_btn.click()

    assert resumed == []
    assert navigated == [1]


# ── the notice does not come off ──────────────────────────────────

def test_drilling_into_a_folder_keeps_the_notice(dash):
    """It lives above the view stack for this reason: a notice inside the
    dashboard would be gone at the first click into anything."""
    d = dash(_stopped_state())
    d._on_view_partial_results()

    d._show_tree()

    assert d._current_view == "tree"
    assert d._partial_notice.isVisible()


def test_opening_a_category_keeps_the_notice(dash, dressed):
    d = dash(_stopped_state())
    d._on_view_partial_results()

    d._show_category("Temporary Files")
    for _ in range(4):
        dressed.processEvents()

    assert d._current_view == "category"
    assert d._partial_notice.isVisible()


def test_returning_to_findings_stays_on_the_partial_results(dash):
    """Having chosen to look at them, the user must not be handed the stopped
    screen again every time they come back to the tab."""
    d = dash(_stopped_state())
    d._on_view_partial_results()

    d._update_for_current_state()

    assert d._current_view == "dashboard"
    assert d._partial_notice.isVisible()


# ── and it goes away exactly when it stops being true ─────────────

def test_a_resumed_scan_takes_the_notice_away(dash, dressed):
    d = dash(_stopped_state())
    d._on_view_partial_results()
    assert d._partial_notice.isVisible()

    d._scan_state.set_running(True, "C:/")
    for _ in range(6):
        dressed.processEvents()

    assert d._current_view == "loading"
    assert not d._partial_notice.isVisible()
    assert not d._viewing_partial, "a finished resume would re-enter partial"


def test_a_completed_scan_carries_no_notice(dash, dressed):
    """The notice is driven from the phase, not from the click that raised
    it, so nothing has to remember to take it down."""
    ss = _stopped_state()
    d = dash(ss)
    d._on_view_partial_results()

    ss._set_phase("complete", "analysis complete")
    d._show_dashboard()
    for _ in range(4):
        dressed.processEvents()

    assert not d._partial_notice.isVisible()


def test_a_scan_that_was_never_stopped_never_shows_it(dash, dressed):
    ss = ScanState()
    ss.set_running(True, "C:/")
    ss.add_findings(_findings(10))
    ss.set_running(False)
    for _ in range(4):
        dressed.processEvents()

    d = dash(ss)

    assert not d._partial_notice.isVisible()


def test_the_stopped_screen_itself_carries_no_notice(dash):
    """It already says so in full, in the middle of the page."""
    d = dash(_stopped_state())

    assert d._current_view == "stopped"
    assert not d._partial_notice.isVisible()


# ── and it is readable ────────────────────────────────────────────

def _edges(w):
    img = w.grab().toImage()
    W, H = img.width(), img.height()
    return {img.pixelColor(x, y).name() for x, y in
            [(1, 1), (W - 2, 1), (1, H - 2), (W - 2, H - 2), (W // 2, 0), (0, H // 2)]}


def test_the_notice_does_not_flatten_its_own_controls(dash):
    """A selector-less widget stylesheet cascades to every descendant and
    outranks the application stylesheet — which is how a container strips the
    border off the button inside it. See test_actions_look_like_actions.py."""
    d = dash(_stopped_state())
    d._on_view_partial_results()

    assert _edges(d._partial_notice._resume_btn) - {"#000000"}, "resume paints no chrome"


def test_the_notice_survives_a_theme_switch(dressed, dash):
    d = dash(_stopped_state())
    d._on_view_partial_results()

    dressed.setStyleSheet(build_qss("amber"))
    d._on_theme_changed("amber")
    for _ in range(4):
        dressed.processEvents()

    try:
        assert _edges(d._partial_notice._resume_btn) - {"#000000"}
        assert d._partial_notice._text.text()
    finally:
        dressed.setStyleSheet(build_qss("forest"))


def test_the_notice_fits_a_narrow_window(dressed, dash):
    """main() enforces a 1100x700 minimum; the sidebar takes 196 of it."""
    d = dash(_stopped_state())
    d._on_view_partial_results()
    d.resize(884, 620)
    for _ in range(6):
        dressed.processEvents()

    text = d._partial_notice._text
    assert text.wordWrap(), "a fixed one-line notice would clip"
    for btn in d._partial_notice.findChildren(QPushButton):
        assert btn.sizeHint().width() <= btn.width() + 1, btn.text()
    for lbl in d._partial_notice.findChildren(QLabel):
        if lbl.text() and not lbl.wordWrap():
            assert lbl.sizeHint().width() <= lbl.width() + 1, lbl.text()
