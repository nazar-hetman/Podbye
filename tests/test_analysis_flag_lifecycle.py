"""The "analysis is running" flag has to come down however detection ends.

Reported: the language cannot be switched with nothing running, straight after
opening Findings.

is_analysis_active is read directly off _entity_detection_running, and the
language switch consults it before anything else:

    busy = ("an analysis is running" if self._scan_state.is_analysis_active
            else self._busy_reason())

so once that flag is stuck the switch is refused for the rest of the session,
with a reason that is not true - and _busy_reason(), where every screen answers
for its own threads, is never even reached.

Two exits of _apply_entity_results left it up. The early return when
_pending_entities is None, and the method's own except block - which exists to
unblock the UI after a failed commit and cleared everything except the one flag
that keeps it blocked.
"""
import pytest

from app.state.scan_state import ScanState


@pytest.fixture
def state(qapp):
    s = ScanState()
    s._entity_detection_running = True      # detection is under way
    return s


def _assert_idle(state):
    assert state._entity_detection_running is False
    assert state.is_analysis_active is False, (
        "the app still believes an analysis is running")


# -- every way this method can end --------------------------------

def test_a_normal_commit_ends_the_analysis(state):
    state._pending_entities = []
    state._apply_entity_results()
    _assert_idle(state)


def test_a_cancelled_detection_ends_it(state):
    state._entity_detection_cancelled = True
    state._pending_entities = []
    state._apply_entity_results()
    _assert_idle(state)


def test_missing_results_end_it(state):
    """The early return that logged "CRITICAL BUG" and left the flag up."""
    state._pending_entities = None
    state._apply_entity_results()
    _assert_idle(state)


def test_a_commit_that_raises_ends_it(state):
    """The except block unblocks the UI, and used to leave behind the one flag
    that keeps it blocked."""
    class _Boom(list):
        def __len__(self):
            raise RuntimeError("commit blew up")

    state._pending_entities = _Boom()
    state._apply_entity_results()
    _assert_idle(state)


def test_a_commit_that_raises_still_reports_an_error_phase(state):
    """Clearing the flag must not turn a failure into a silent success."""
    class _Boom(list):
        def __len__(self):
            raise RuntimeError("commit blew up")

    state._pending_entities = _Boom()
    state._apply_entity_results()
    assert state.current_phase == "error"


# -- and the consequence the user actually saw --------------------

def test_the_language_gate_reopens_after_a_failed_commit(state):
    """The exact expression main.py evaluates before allowing a switch."""
    class _Boom(list):
        def __len__(self):
            raise RuntimeError("commit blew up")

    state._pending_entities = _Boom()
    state._apply_entity_results()
    busy = "an analysis is running" if state.is_analysis_active else ""
    assert busy == "", "the language switch is still refused"


def test_a_real_analysis_still_blocks(state):
    """The guard has to keep working: this is not "always allow"."""
    assert state.is_analysis_active is True
    busy = "an analysis is running" if state.is_analysis_active else ""
    assert busy == "an analysis is running"


def test_a_running_scan_still_blocks(qapp):
    state = ScanState()
    state._is_running = True
    assert state.is_analysis_active is True


def test_an_explicit_stop_still_clears_it(qapp):
    """stop_all() already handled its own path; it must keep doing so."""
    state = ScanState()
    state._entity_detection_running = True
    state.stop_all()
    assert state.is_analysis_active is False
