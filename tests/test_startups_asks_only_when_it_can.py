"""Startups: what it says about entries, and when it asks a model about them.

Two changes, both about not claiming more than is true.

**Protected is retired here.** In Findings that tier is enforceable — the item
cannot be selected and the recycle button is disabled. On Startups it never
meant that: Podbye contains no registry write of any kind (no ``SetValue``,
``DeleteValue``, ``CreateKey``, ``KEY_WRITE`` anywhere in ``app/``), so it
modifies no startup entry and can never refuse to. Borrowing a word that means
"Podbye will refuse" for a screen where nothing is refused taught users the
wrong thing about the screen where it is real. Those entries are Review now,
with their reasons unchanged and a ``system_managed`` flag carrying what the
tier used to.

Optional and Review stay apart on purpose. Review means *we could not identify
this* — no verifiable publisher, or launching from Temp or Downloads. Optional
means *we know exactly what this is and it is your preference*. Merging them
would tell someone to investigate OneDrive and present an unsigned binary in
Downloads as a matter of taste.

**The bulk AI pass now checks for a model.** The per-item Ask AI path always
did. The automatic one did not, so opening Startups on a stock install started
a worker that walked every entry against an endpoint that was not there: one
failed request per program, and every row ending on "failed" as though the
analysis had been tried and had gone wrong. Not configured is not a failure.
"""
import time

import pytest

import app.screens.startups as st
from app.models.startup_entry import StartupEntry


def _entry(name="Program", risk="Optional", **kw):
    entry = StartupEntry(
        name=name, command="C:/p.exe", path="C:/p.exe", publisher="Acme",
        source="run_hkcu", source_label="User startup registry", enabled=True,
        risk=risk, risk_reason="r", impact="Light utility", **kw)
    entry.target_modified = time.time() - 86400
    return entry


class _Store(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


@pytest.fixture
def screen(qapp):
    made = []

    def build(store, entries=None):
        s = st.StartupsScreen()
        s._settings_store = store
        s._entries = entries if entries is not None else [_entry(f"P{i}") for i in range(3)]
        s._filtered = list(s._entries)
        made.append(s)
        return s

    yield build
    for s in made:
        s.deleteLater()
    qapp.processEvents()


@pytest.fixture
def no_threads(monkeypatch):
    """Record worker starts instead of running them."""
    started = []
    monkeypatch.setattr(st.StartupAIWorker, "start",
                        lambda self: started.append(self))
    return started


# ── Protected is gone from this screen ────────────────────────────

def test_the_classifier_never_returns_protected():
    """Every path that used to. The reasons are what carry the warning."""
    from app.services.startup_detector import _classify_risk

    cases = [
        ("Defender", "C:/Windows/System32/x.exe", "Microsoft Corporation", ""),
        ("SomeAntivirus", "C:/Program Files/AV/av.exe", "AV Corp", "Antivirus"),
        ("ArmourySocketServer", "C:/Program Files/ASUS/a.exe", "ASUSTeK", ""),
    ]
    for name, path, publisher, product in cases:
        risk, reason = _classify_risk(name, path, publisher, product)
        assert risk != "Protected", f"{name} -> {risk} ({reason})"


def test_system_owned_entries_are_review_and_flagged():
    from app.services.startup_detector import _classify_risk, is_system_managed

    risk, reason = _classify_risk(
        "Defender", "C:/Windows/System32/x.exe", "Microsoft Corporation", "")

    assert risk == "Review"
    assert is_system_managed(reason)
    assert "leave managed by Windows" in reason


def test_the_screen_offers_no_protected_filter():
    assert "Protected" not in st.STARTUP_RISK_ORDER
    assert st.STARTUP_RISK_ORDER == ("Safe", "Optional", "Review")


def test_findings_keeps_protected():
    """It is enforceable there, and this change must not reach it."""
    from app.models.risk import RISK_ORDER

    assert "Protected" in RISK_ORDER


def test_the_system_advice_survived_the_retirement():
    """It used to be reachable only through the Protected tier, so folding
    that into Review could have replaced it with the generic review line."""
    from app.services.startup_detector import _build_recommendation

    assert "ongoing protection" in _build_recommendation("Review", "Security component")
    assert "hardware features" in _build_recommendation("Review", "Hardware utility")
    assert "publisher and path" in _build_recommendation("Review", "Startup item")


def test_optional_and_review_still_say_different_things():
    """The distinction that carries the safety weight: one is 'we could not
    identify this', the other is 'we know, and it is your call'."""
    from app.services.startup_detector import _classify_risk

    # Two ways to be unidentified, and they must both land in Review.
    # Backslashes on purpose: the suspicious-directory check matches
    # \downloads\ and the registry hands out backslash paths.
    odd_place, place_reason = _classify_risk(
        "thing", r"C:\Users\x\Downloads\t.exe", "Some Vendor Ltd", "")
    no_publisher, publisher_reason = _classify_risk(
        "thing", "C:/Program Files/Thing/t.exe", "", "")
    known, _ = _classify_risk("OneDrive", "C:/Program Files/OneDrive/o.exe",
                              "Microsoft", "OneDrive")

    assert odd_place == "Review" and "unusual location" in place_reason
    assert no_publisher == "Review" and "Publisher could not be verified" in publisher_reason
    assert known != "Review", "a known convenience is not an unidentified one"


# ── the bulk pass asks only when there is something to ask ────────

def test_a_stock_install_starts_no_worker(screen, no_threads):
    """No model chosen — there is nothing to ask, and no endpoint to ask it."""
    s = screen(_Store(ai_startups_enabled=True, ai_model=""))

    s._start_ai()

    assert no_threads == []
    assert {e.ai_status for e in s._entries} == {"unconfigured"}


def test_ai_enabled_but_model_missing_is_not_an_error(screen, no_threads):
    """It used to end on "failed", which reads as "we tried and it broke"."""
    s = screen(_Store(ai_startups_enabled=True, ai_model=None))

    s._start_ai()

    assert no_threads == []
    assert all(e.ai_status == "unconfigured" for e in s._entries)
    assert not any(e.ai_status == "failed" for e in s._entries)


def test_switched_off_is_a_different_state_from_unconfigured(screen, no_threads):
    """One is a choice the user made, the other is a step they have not taken.
    Telling them apart is the whole point of the new status."""
    s = screen(_Store(ai_startups_enabled=False, ai_model="gemma4"))

    s._start_ai()

    assert no_threads == []
    assert {e.ai_status for e in s._entries} == {"disabled"}


def test_a_configured_model_still_runs(screen, no_threads):
    """The guard must not stop the feature working."""
    s = screen(_Store(ai_startups_enabled=True, ai_model="gemma4"))

    s._start_ai()

    assert len(no_threads) == 1


def test_a_second_start_does_not_stack_workers(screen, no_threads):
    """Re-analyze, the refresh on open and an adopted list can all land here,
    and two passes over the same entries race onto the same rows."""
    s = screen(_Store(ai_startups_enabled=True, ai_model="gemma4"))

    s._start_ai()

    class _Running:
        def isRunning(self):
            return True

    s._ai_worker = _Running()
    s._start_ai()

    assert len(no_threads) == 1


def test_the_unconfigured_state_reads_as_a_state_not_a_fault(qapp):
    from app.i18n import tr

    assert tr("No AI model configured · not analyzed")
    assert "fail" not in tr("No AI model configured · not analyzed").lower()


# ── and no worker outlives the screen ─────────────────────────────

class _FakeWorker:
    def __init__(self):
        self._running = True
        self.cancelled = False

    def isRunning(self):
        return self._running

    def cancel(self):
        self.cancelled = True

    def requestInterruption(self):
        pass

    def wait(self, ms):
        self._running = False
        return True

    def setParent(self, parent):
        pass


def test_closing_the_screen_stops_a_bulk_run(screen, qapp):
    s = screen(_Store(ai_startups_enabled=True, ai_model="gemma4"))
    worker = _FakeWorker()
    s._ai_worker = worker

    s.close()
    qapp.processEvents()

    assert worker.cancelled, "a running worker outlived the screen"


def test_a_bulk_run_makes_the_screen_busy_for_a_language_switch(screen):
    """busy_reason gates the shell rebuild, and rebuilding deletes this tree
    while the thread is in it."""
    s = screen(_Store(ai_startups_enabled=True, ai_model="gemma4"))
    assert s.busy_reason() == ""

    s._ai_worker = _FakeWorker()

    assert s.busy_reason() != ""


def test_stop_background_work_covers_the_bulk_worker(screen):
    s = screen(_Store(ai_startups_enabled=True, ai_model="gemma4"))
    worker = _FakeWorker()
    s._ai_worker = worker

    s.stop_background_work(50)

    assert worker.cancelled
