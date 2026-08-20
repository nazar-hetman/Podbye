"""The Analyze screen's event filter must survive its own table being destroyed.

The filter is installed on the partial-findings table's viewport but lives on
the screen, so it outlives the table whenever teardown goes child-first. It
reached through the dead wrapper unguarded and raised "Internal C++ object
already deleted" — Qt catches that, so nothing crashed, but the event was
dropped and a traceback printed on every teardown.
"""
import pytest
from PySide6.QtCore import QEvent


def test_the_filter_declines_events_once_its_table_is_gone(qapp):
    import shiboken6
    from app.screens.analyze import AnalyzeScreen
    from app.state.scan_state import ScanState

    screen = AnalyzeScreen(scan_state=ScanState())
    table = screen._pf_table
    table.setParent(None)
    table.deleteLater()
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not shiboken6.isValid(table), "the table should be gone by now"

    # Would raise RuntimeError out of the override before the guard.
    assert screen.eventFilter(screen, QEvent(QEvent.Leave)) is False

    screen.close()
    screen.deleteLater()
    qapp.processEvents()
