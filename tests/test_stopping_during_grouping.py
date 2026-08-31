"""Stopping during entity detection left Findings on the loading screen forever.

Reported from a real 2TB run: the walk had finished, grouping was in progress,
the user pressed Stop. The feed printed

    [smart] semantic grouping complete · 1684 entities created · 1,453,811
            files grouped · 102% coverage
    [smart] entities ready for dashboard
    [perf]  entity detection: 16602.9ms
    [smart] entity detection cancelled

and Findings then sat on "PREPARING STORAGE OVERVIEW · Grouping into semantic
categories…" with COVERAGE 0%, showing nothing, with no way out. Two separate
faults produced that:

1. ``stop_all()`` set the stopped phase only ``if was_running``, and
   ``_is_running`` is the *filesystem walk*, which had already finished. So the
   phase stayed "entity_detection" — the loading state — permanently.

2. ``_apply_entity_results()`` returned early whenever the cancel flag was set,
   throwing away 1,684 fully-built entities. The worker returns without
   emitting when *it* sees the cancel, so anything reaching that slot is a
   finished grouping; the flag had merely been set in the window between the
   worker's emit and the main thread applying it.

Fixing (2) alone would have been worse than the bug: the completion path below
it badges the run Complete, starts duplicate detection, and calls
save_session_final("completed") — overwriting the preserved "stopped" session
that Resume reads. So the stopped case is tested all the way through.
"""
import pytest

import app.screens.findings_dashboard as fd
from app.models.finding import Finding
from app.models.smart_entity import SmartEntity
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


def _entities(n):
    out = []
    for i in range(n):
        e = SmartEntity(path=f"C:/x/g{i}", name=f"g{i}", entity_type="dev_artifact")
        e.size_bytes, e.file_count, e.risk = 1024 ** 2, 10, "Optional"
        out.append(e)
    return out


def _grouping(found=40):
    """The walk has finished; entity detection is running.

    The flags are set directly rather than by letting set_running(False) fall
    through to _run_entity_detection(): that starts a real background thread,
    which finishes on this much data before the test can stop it and makes the
    outcome depend on a race.
    """
    ss = ScanState()
    ss.set_running(True, "C:/")
    ss.add_findings(_findings(found))
    ss._is_running = False
    ss._entity_detection_running = True
    ss._set_phase("entity_detection", "grouping storage into semantic entities…")
    return ss


# ── 1. a stop during grouping is a stop ───────────────────────────

def test_stopping_during_grouping_reaches_the_stopped_phase():
    """The whole failure in one assertion: this was "entity_detection"."""
    ss = _grouping()

    ss.stop_all()

    assert ss.current_phase == "stopped"


def test_stopping_during_the_walk_still_reaches_it():
    ss = ScanState()
    ss.set_running(True, "C:/")
    ss.add_findings(_findings(10))

    ss.stop_all()

    assert ss.current_phase == "stopped"


def test_an_idle_state_is_not_marked_stopped():
    """Nothing was running, so there is nothing to have stopped. Marking it
    would put a stopped screen in front of a user who never started a scan."""
    ss = ScanState()

    ss.stop_all()

    assert ss.current_phase != "stopped"


def test_interrupting_only_the_ai_does_not_make_the_map_partial():
    """Deliberately outside the rule. Every file was visited and grouped —
    only some explanations are missing — so sending a complete storage map to
    the stopped screen would be a lie in the other direction."""
    ss = ScanState()
    ss.set_running(True, "C:/")
    ss.add_findings(_findings(10))
    ss._is_running = False
    ss._entities = _entities(4)
    ss._set_phase("ai_classification", "explaining…")

    ss.stop_all()

    assert ss.current_phase == "ai_classification"


# ── 2. finished grouping is not thrown away ───────────────────────

def test_a_cancel_landing_after_the_worker_keeps_its_entities():
    """1,684 entities, computed and delivered, dropped because the flag was
    set between the emit and this slot running."""
    ss = _grouping()
    ss._pending_entities = _entities(5)

    ss.stop_all()
    ss._apply_entity_results()

    assert ss.entity_count == 5
    assert ss.current_phase == "stopped"


def test_the_kept_grouping_is_announced_so_the_ui_can_leave_the_loader():
    ss = _grouping()
    ss._pending_entities = _entities(5)
    fired = []
    ss.entities_ready.connect(lambda: fired.append(1))

    ss.stop_all()
    ss._apply_entity_results()

    assert fired == [1], "nothing told the UI, so it stays on the loader"


def test_a_stopped_run_does_not_go_on_to_the_model():
    """Keeping the map must not restart the pipeline: a stopped run does not
    then spend minutes in the model the user just stopped."""
    class _Explainer:
        is_running = False

        def __init__(self):
            self.started = []

        def enqueue_entities(self, entities):
            self.started.append(entities)

        def start(self, run_mode=None):
            self.started.append("start")

        def stop(self):
            pass

    ss = _grouping()
    ss._ai_explainer = _Explainer()
    ss._settings_store = type("S", (), {"get": lambda self, k, d=None: True})()
    ss._pending_entities = _entities(5)

    ss.stop_all()
    ss._apply_entity_results()

    assert ss._ai_explainer.started == []
    assert ss.current_phase == "stopped"


def test_a_cancel_with_nothing_built_still_aborts():
    """The detector returns without emitting when it sees the cancel itself,
    so an empty delivery is a genuine abort and stays one."""
    ss = _grouping()
    ss._pending_entities = []

    ss.stop_all()
    ss._apply_entity_results()

    assert ss.entity_count == 0


def test_an_uncancelled_run_is_unaffected():
    """The ordinary path must still complete."""
    ss = ScanState()
    ss.set_running(True, "C:/")
    ss.add_findings(_findings(10))
    ss._is_running = False
    ss._entity_detection_running = True
    ss._pending_entities = _entities(3)

    ss._apply_entity_results()

    assert ss.entity_count == 3
    assert ss.current_phase != "stopped"


# ── 3. and Findings comes off the loading screen ──────────────────

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


def test_findings_waits_while_grouping_is_really_running(dash):
    d = dash(_grouping())

    assert d._current_view == "loading"


def test_findings_stops_waiting_when_the_run_is_stopped(dash, dressed):
    """The reported symptom, end to end."""
    ss = _grouping()
    d = dash(ss)
    assert d._current_view == "loading"

    ss._pending_entities = _entities(5)
    ss.stop_all()
    ss._apply_entity_results()
    for _ in range(8):
        dressed.processEvents()

    assert d._current_view == "stopped", "still preparing a storage overview"


def test_the_kept_grouping_is_offered_as_partial_results(dash, dressed):
    ss = _grouping()
    d = dash(ss)
    ss._pending_entities = _entities(5)
    ss.stop_all()
    ss._apply_entity_results()
    for _ in range(8):
        dressed.processEvents()

    assert d._stopped_view._partial_btn.isVisible()

    d._on_view_partial_results()
    for _ in range(6):
        dressed.processEvents()

    assert d._current_view == "dashboard"
    assert d._partial_notice.isVisible()


def test_a_partial_map_is_never_shown_as_a_finished_one(dash, dressed):
    """entities_ready used to mean "the scan is done". It now also fires for
    a stopped run, and that must not walk straight onto the dashboard with no
    sign the map has holes in it."""
    ss = _grouping()
    d = dash(ss)
    ss._pending_entities = _entities(5)
    ss.stop_all()
    ss._apply_entity_results()
    for _ in range(8):
        dressed.processEvents()

    assert d._current_view != "dashboard" or d._partial_notice.isVisible()


# ── 4. and Analyze does not call it complete ──────────────────────

def _analyze(dressed, scan_state):
    """The screen with a scan under way.

    entities_ready is connected inside _start_scan, not set_scan_state, so a
    screen that was only handed the state hears nothing. Connecting it here is
    what the screen does at line 986 — starting a real scan would walk the
    machine.
    """
    from app.screens.analyze import AnalyzeScreen

    s = AnalyzeScreen()
    s.resize(1500, 900)
    s.set_scan_state(scan_state)
    scan_state.entities_ready.connect(s._on_entities_ready)
    s.show()
    for _ in range(6):
        dressed.processEvents()
    return s


def test_a_stopped_run_does_not_overwrite_the_preserved_session(dressed):
    """The one that would have hurt. save_session_final("completed") replaces
    the "stopped" session Resume continues from — the partial data would be
    unrecoverable, from a code path added to preserve it."""
    ss = _grouping()
    saved = []
    ss.save_session_final = lambda status, **kw: saved.append(status)
    s = _analyze(dressed, ss)
    try:
        ss._pending_entities = _entities(5)
        ss.stop_all()
        ss._apply_entity_results()
        for _ in range(8):
            dressed.processEvents()

        assert "completed" not in saved
    finally:
        s.deleteLater()
        dressed.processEvents()


def test_a_stopped_run_is_not_badged_complete(dressed):
    ss = _grouping()
    s = _analyze(dressed, ss)
    try:
        ss._pending_entities = _entities(5)
        ss.stop_all()
        ss._apply_entity_results()
        for _ in range(8):
            dressed.processEvents()

        assert s._pipeline_state == "stopped"
        assert s._scan_prog_lbl.text() != "100%"
    finally:
        s.deleteLater()
        dressed.processEvents()


def test_the_partial_findings_table_fills_in(dressed):
    """Empty in the report — 3,707,189 items and no rows — because the
    category summary is built from entities, and the entities were discarded."""
    ss = _grouping()
    s = _analyze(dressed, ss)
    try:
        ss._pending_entities = _entities(5)
        ss.stop_all()
        ss._apply_entity_results()
        for _ in range(8):
            dressed.processEvents()

        assert s._pf_table.rowCount() > 0
        assert "Partial" in s._pf_sub.text(), s._pf_sub.text()
    finally:
        s.deleteLater()
        dressed.processEvents()
