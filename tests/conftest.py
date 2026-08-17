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
