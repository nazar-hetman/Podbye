"""Shared pytest fixtures.

Qt objects need a QApplication and an offscreen platform plugin. Creating one
per test module duplicated boilerplate and previously let a plain
QCoreApplication clash with a QApplication (a hard crash, exit code 9,
depending on module collection order). Create exactly one, once, here.
"""
import os

# Must be set before any QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication


@pytest.fixture(autouse=True)
def _never_touch_the_real_session_store(tmp_path_factory, monkeypatch):
    """Every test gets its own empty %APPDATA%.

    Two reasons, and both were learned the hard way.

    *Nothing may write to the user's live store.* A test that called
    save_cleanup_record without redirecting it wrote a real record into the
    running user's cleanup history, and the store keeps only
    MAX_CLEANUP_HISTORY of them, so a long enough run would evict the user's
    own records. Redirecting the public ``sessions_dir()`` looks right and does
    nothing — the writer resolves the private ``_sessions_dir()`` — so this
    redirects %APPDATA% instead, the layer both of them read and the one
    several test modules already override for themselves.

    *Per test, not per session.* Sharing one directory made the settings file
    shared mutable state: a test that configured an AI model left it configured
    for every test that ran afterwards, and a later screen then started a real
    AI worker that outlived its test and issued live HTTP requests to Ollama
    for the rest of the run. Making a directory costs nothing next to that.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path_factory.mktemp("appdata")))


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """One QApplication for the whole test session.

    The teardown matters as much as the setup. Screens start threads on their
    own — QuickCleanupScreen.showEvent() kicks off a real disk scan — and a
    QThread still running when the interpreter tears Qt down takes the process
    with it: all tests pass, the summary prints, and then the run exits with an
    access violation. Stop everything and wait before letting Qt go.
    """
    app = QApplication.instance() or QApplication([])
    yield app

    try:
        for widget in QApplication.allWidgets():
            stop = getattr(widget, "stop_background_work", None)
            if callable(stop):
                try:
                    stop(2000)
                except (RuntimeError, TypeError):
                    pass
        from app.services.workers import _ORPHANED
        for worker in list(_ORPHANED):
            try:
                worker.wait(2000)
            except RuntimeError:
                pass
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _drain_deferred_deletes(qapp):
    """Actually destroy the widgets each test built.

    deleteLater() only *posts* a DeferredDelete event, and Qt holds those back
    until the event loop that was running when they were posted unwinds. No
    such loop exists under pytest, so processEvents() never delivers them and
    every screen a test builds stays alive for the rest of the session. Past
    ~500 widget-heavy tests the process runs out of Windows GDI/window handles
    and dies with an access violation — which surfaces as a bare traceback and
    a non-zero exit rather than a test failure, so it reads like flakiness.

    sendPostedEvents with the type named explicitly is what forces delivery.
    """
    yield
    try:
        # Stop background threads BEFORE forcing the deletes. Several screens
        # start work on their own — QuickCleanupScreen.showEvent() kicks off a
        # real disk scan — and destroying a widget that owns a running QThread
        # aborts the process (see app/services/workers.py). Draining without
        # this made the suite crash with an access violation part-way through
        # rather than merely leak.
        for widget in QApplication.allWidgets():
            stop = getattr(widget, "stop_background_work", None)
            if callable(stop):
                try:
                    stop(2000)
                except (RuntimeError, TypeError):
                    pass
        qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qapp.processEvents()
    except Exception:
        # Best-effort cleanup: a widget whose C++ side vanished mid-sweep, or
        # an application already torn down, must never turn a passing test
        # into an error.
        pass


@pytest.fixture(scope="module")
def _shared_panel():
    """One panel for the whole module, not one per test.

    _PreallocDetailPanel is the heaviest tree in the app — it pre-builds every
    widget it will ever need. Six of them per file, across three files, was
    enough to push a full run into an access violation inside the garbage
    collector, surfacing ~1500 tests away in ast.parse under a locale test.
    conftest already drains deferred deletes after every test; what it cannot
    do is stop the trees being built in the first place.

    Rebinding is what the panel does on every row click anyway, so one
    instance answers every case here.
    """
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from app.screens.findings_dashboard import _PreallocDetailPanel

    app = QApplication.instance()
    holder = {}

    def make(world):
        panel = holder.get("panel")
        if panel is None:
            panel = _PreallocDetailPanel(
                lambda *_a: None, lambda *_a: None,
                recycle_cb=lambda *_a: None,
                entities_cb=lambda: holder.get("world") or [])
            panel.resize(600, 900)
            holder["panel"] = panel
        holder["world"] = world
        return panel

    yield make

    panel = holder.get("panel")
    if panel is not None:
        stop = getattr(panel, "_stop_contents_walk", None)
        if callable(stop):
            stop(200)
        panel.close()
        panel.setParent(None)
        panel.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
