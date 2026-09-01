"""What survives the shell rebuild a language change performs.

``_apply_language_change`` reconstructs every screen, and the screens are
where results that were expensive to produce happen to live. The shared
ScanState survives because the window owns it. Startups did not:

* the new screen came up empty, so its showEvent re-read the registry and the
  Startup folders — 285 ms of synchronous filesystem work, on the UI thread,
  inside the switch;
* every AI explanation went with the old entries. Measured on a real machine:
  0 of 24 entries kept an answer. Those cost a local model minutes each, and
  the UI language has nothing to do with them — they are written in
  ai_explanation_language, which a UI language change does not touch.

So the entries are handed to the new screen. They carry raw values
(``source_label``, ``impact``, ``risk_reason``) and every one is passed
through tr() at render time, which is what makes this safe: the same objects
display in the new language.

Deliberately *not* carried: where the user was in the Findings category list.
It is lost too, but rebuilding those rows measured ~1.5 s on top of the
switch — spent on the very stall this work started from — to save a click.
Carrying is worth it only for state that is expensive to recreate.
"""
import time

import pytest


def _dispose(win, qapp):
    """Take the window down now, not whenever the collector gets to it.

    deleteLater() only *posts* a DeferredDelete, and processEvents() outside a
    running event loop does not deliver those — so a window built here stayed
    alive until Python collected its wrapper, and PySide then destroyed the
    C++ tree from inside the garbage collector. That is an access violation
    with no traceback, landing on whatever unrelated test the GC happened to
    run under: the full suite died in ast.parse inside a locale test.

    tests/test_no_clipped_text.py documents the same failure from the same
    cause. A PodbyeWindow is the heaviest tree in the app, so it is disposed
    of explicitly: stop the threads, close so each screen's closeEvent runs,
    then actually deliver the delete.
    """
    from PySide6.QtCore import QCoreApplication, QEvent

    from app.i18n import set_language

    # set_language() is process-global and these tests change it on purpose.
    # Leaving it changed made every later test in the run read a Ukrainian UI:
    # 57 failures across 20 files, all of the shape `assert '3 items' in
    # '3 елем. у цьому додатку'`. This file sorts first, so it poisoned the
    # whole suite.
    set_language("English")
    win._stop_all_background_work()
    win.close()
    win.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """A real shell whose startup detection is pinned to a known list."""
    import app.services.startup_detector as detector

    monkeypatch.setenv("APPDATA", str(tmp_path))
    calls = {"n": 0}

    def _detect():
        calls["n"] += 1
        return [_entry(f"Program {i}") for i in range(4)]

    monkeypatch.setattr(detector, "detect_startup_entries", _detect)

    from app.main import PodbyeWindow
    win = PodbyeWindow()
    win.resize(1400, 900)
    # Shown on purpose: showEvent is where a screen detects, adopts and
    # renders, and that is the whole subject here. Only the navigated-to
    # screen is shown, so Quick Cleanup's own auto-scan never starts.
    win.show()
    for _ in range(6):
        qapp.processEvents()
    win._detect_calls = calls
    yield win
    _dispose(win, qapp)


def _entry(name):
    from app.models.startup_entry import StartupEntry

    entry = StartupEntry(
        name=name, command="C:/p.exe", path="C:/p.exe", publisher="Acme",
        source="run_hkcu", source_label="Scheduled task (logon)", enabled=True,
        risk="Optional", risk_reason="r", impact="Remote access service")
    entry.target_modified = time.time() - 30 * 86400
    return entry


def _populate_startups(window, qapp):
    """Detect once, then give three entries an answer worth keeping."""
    window._navigate("Startups")
    for _ in range(10):
        qapp.processEvents()
    screen = window._screens["Startups"]
    assert screen._entries, "detection did not run"
    for entry in screen._entries[:3]:
        entry.ai_status = "ready"
        entry.ai_explanation = "An answer the model spent a minute on."
    screen._select_entry(screen._entries[0].key)
    for _ in range(4):
        qapp.processEvents()
    return screen


# ── the expensive things survive ──────────────────────────────────

def test_the_switch_does_not_re_read_the_machine(window, qapp):
    """285 ms of registry and Startup-folder walking, on the UI thread, for a
    list the app already had."""
    _populate_startups(window, qapp)
    window._navigate("Home")
    for _ in range(4):
        qapp.processEvents()
    before = window._detect_calls["n"]

    window._apply_language_change("Ukrainian")
    for _ in range(6):
        qapp.processEvents()

    assert window._detect_calls["n"] == before, "re-detected during the switch"


def test_the_ai_answers_survive(window, qapp):
    """The one that costs minutes to get back."""
    _populate_startups(window, qapp)
    window._navigate("Home")
    for _ in range(4):
        qapp.processEvents()

    window._apply_language_change("Ukrainian")
    for _ in range(6):
        qapp.processEvents()

    after = window._screens["Startups"]
    assert after is not None
    kept = [e for e in after._entries if getattr(e, "ai_explanation", "")]
    assert len(kept) == 3, f"{len(kept)} of 3 answers survived"


def test_the_entries_themselves_survive(window, qapp):
    before = _populate_startups(window, qapp)
    names = [e.name for e in before._entries]
    window._navigate("Home")
    for _ in range(4):
        qapp.processEvents()

    window._apply_language_change("Ukrainian")
    for _ in range(6):
        qapp.processEvents()

    assert [e.name for e in window._screens["Startups"]._entries] == names


def test_the_selection_survives(window, qapp):
    before = _populate_startups(window, qapp)
    key = before._selected_key
    assert key

    window._apply_language_change("Ukrainian")
    for _ in range(8):
        qapp.processEvents()

    assert window._screens["Startups"]._selected_key == key


# ── and they are shown in the new language ────────────────────────

def test_carried_entries_render_in_the_new_language(window, qapp):
    """The correctness risk of carrying: entries hold raw values, and if any
    display string had been baked in at detection time it would still be in
    the old language."""
    from PySide6.QtWidgets import QLabel

    _populate_startups(window, qapp)

    window._apply_language_change("Ukrainian")
    for _ in range(10):
        qapp.processEvents()
    window._navigate("Startups")
    for _ in range(8):
        qapp.processEvents()

    screen = window._screens["Startups"]
    texts = [l.text() for l in screen.findChildren(QLabel) if l.isVisibleTo(screen)]
    assert any("АВТОЗАПУСК" in t.upper() for t in texts), texts[:6]
    assert not any("Scheduled task" in t for t in texts), "raw English left on screen"


# ── without paying for screens nobody is looking at ───────────────

def test_a_screen_that_is_not_being_shown_is_not_rendered(window, qapp):
    """Drawing rows for the six screens the user is not on is what turned a
    2.4 s switch into 3.9 s."""
    screen = _populate_startups(window, qapp)
    window._navigate("Home")
    for _ in range(4):
        qapp.processEvents()

    window._apply_language_change("Ukrainian")
    for _ in range(6):
        qapp.processEvents()

    after = window._screens["Startups"]
    assert after._entries, "the data should still be there"
    assert getattr(after, "_pending_adopt_render", False), (
        "rendered a screen that is not on display")


def test_the_deferred_render_happens_on_the_way_in(window, qapp):
    _populate_startups(window, qapp)
    window._navigate("Home")
    for _ in range(4):
        qapp.processEvents()
    window._apply_language_change("Ukrainian")
    for _ in range(6):
        qapp.processEvents()

    window._navigate("Startups")
    for _ in range(8):
        qapp.processEvents()

    after = window._screens["Startups"]
    assert not after._pending_adopt_render
    assert after._row_widgets, "the rows were never drawn"


# ── and nothing is carried that should not be ─────────────────────

def test_a_screen_that_never_detected_still_detects_on_open(window, qapp):
    """The carry must not convince an empty screen that it has already run."""
    window._navigate("Home")
    for _ in range(4):
        qapp.processEvents()
    before = window._detect_calls["n"]

    window._apply_language_change("Ukrainian")
    for _ in range(6):
        qapp.processEvents()
    window._navigate("Startups")
    for _ in range(10):
        qapp.processEvents()

    assert window._detect_calls["n"] > before, "the page never read the machine"
    assert window._screens["Startups"]._entries


def test_the_scan_results_were_never_at_risk(window, qapp):
    """ScanState belongs to the window, not the screens — recorded here so a
    future rebuild that moves it is noticed."""
    from app.models.smart_entity import SmartEntity

    entity = SmartEntity(path="C:/x/e", name="E", entity_type="application")
    entity.size_bytes = 10 ** 6
    window._scan_state._entities = [entity]
    window._scan_state._entity_dict_dirty = True

    window._apply_language_change("Ukrainian")
    for _ in range(6):
        qapp.processEvents()

    assert window._scan_state.entity_count == 1
    assert window._screens["Findings"]._scan_state is window._scan_state
