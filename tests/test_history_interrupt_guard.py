"""History must not take the running scan's state out from under it.

"Open findings" calls ``restore_from_session()`` on the very ScanState the scan
is writing into: the running results are gone, and the restored ones are then
overwritten by whatever the worker emits next. Neither outcome is one the user
asked for, and nothing said a word about it. "Re-run with same target" reaches
past the running work the same way.

Driven through a stub rather than a real VigilWindow — building the whole
window starts every screen's timers and background work, which hangs a test
run. The three methods under test are borrowed onto a plain object, the same
way test_file_group_affordance does it.
"""
import pytest
from PySide6.QtWidgets import QMessageBox

from app.main import VigilWindow


class _State:
    def __init__(self):
        self.restored = []
        self._run_mode = ""

    def restore_from_session(self, data):
        self.restored.append(data)


class _Screen:
    def __init__(self, log):
        self._log = log

    def stop_background_work(self, timeout_ms=3000):
        self._log.append("stop")
        return True


def _window(busy: bool):
    """A stand-in carrying only what the three methods actually touch."""
    log = []

    class _Win:
        _confirm_interrupting_running_work = \
            VigilWindow._confirm_interrupting_running_work
        _on_open_findings_requested = VigilWindow._on_open_findings_requested
        _on_rerun_from_history = VigilWindow._on_rerun_from_history

        def __init__(self):
            self.log = log
            self._scan_state = _State()
            self._screens = {"Analyze": _Screen(log), "Findings": _Screen(log)}

        def _is_busy(self):
            return busy

        def _activity_label(self):
            return "A scan"

        def _navigate(self, name):
            log.append(f"navigate:{name}")

    win = _Win()
    # restore_from_session is on the stub state; record its order in the log too.
    original = win._scan_state.restore_from_session

    def _record(data):
        log.append("restore")
        original(data)

    win._scan_state.restore_from_session = _record
    return win


@pytest.fixture
def no_dialog(monkeypatch):
    """Fail loudly if a real modal is ever raised in a test run."""
    def _boom(*args, **kwargs):
        raise AssertionError("a real QMessageBox was shown")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(_boom))


def _answer(monkeypatch, button):
    seen = []

    def _question(*args, **kwargs):
        seen.append(args[1] if len(args) > 1 else "")
        return button

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
    return seen


def test_nothing_is_asked_when_nothing_is_running(no_dialog):
    """The guard must be invisible in the normal case."""
    assert _window(busy=False)._confirm_interrupting_running_work() is True


def test_declining_leaves_the_running_work_alone(monkeypatch):
    _answer(monkeypatch, QMessageBox.No)
    win = _window(busy=True)
    assert win._confirm_interrupting_running_work() is False
    assert "stop" not in win.log, "stopped the scan despite the user declining"


def test_open_findings_does_not_replace_a_live_scan_state(monkeypatch):
    """The actual damage this guards."""
    _answer(monkeypatch, QMessageBox.No)
    win = _window(busy=True)
    win._on_open_findings_requested({"session_id": "x"})
    assert win._scan_state.restored == [], "the live scan's state was replaced"
    assert "navigate:Findings" not in win.log


def test_rerun_does_not_navigate_when_declined(monkeypatch):
    _answer(monkeypatch, QMessageBox.No)
    win = _window(busy=True)
    win._on_rerun_from_history("C:/x")
    assert win.log == []


def test_accepting_stops_the_work_before_replacing_its_state(monkeypatch):
    """Order matters: a worker still running would write into the state that
    was just restored."""
    _answer(monkeypatch, QMessageBox.Yes)
    win = _window(busy=True)
    win._on_open_findings_requested({"session_id": "x"})
    assert "restore" in win.log, "never restored"
    assert win.log.index("stop") < win.log.index("restore"), \
        "restored the session while a worker was still writing into it"
    assert win.log[-1] == "navigate:Findings"


def test_the_question_names_what_is_running(monkeypatch):
    seen = _answer(monkeypatch, QMessageBox.Yes)
    _window(busy=True)._confirm_interrupting_running_work()
    assert seen and "still running" in seen[0].lower()


def test_both_history_actions_are_guarded():
    """A new entry point that skips the check would be the whole bug again."""
    import inspect
    for name in ("_on_open_findings_requested", "_on_rerun_from_history"):
        src = inspect.getsource(getattr(VigilWindow, name))
        assert "_confirm_interrupting_running_work" in src, f"{name} is unguarded"
