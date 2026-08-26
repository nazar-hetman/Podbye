"""Every deferred callback must die with the object it reaches into.

`QTimer.singleShot(msec, functor)` keeps firing after the widget the functor
touches has been destroyed. The three-argument form takes a *context* QObject
and Qt drops the pending call when that object dies.

This is not theoretical and a try/except is not a substitute — a destroyed
QWidget does not reliably raise before it faults. One unbound 0 ms timer,
scheduled from a theme change and landing after the screen closed, segfaulted
this whole suite non-deterministically, in whichever test happened to run next.
The riskiest are the delayed ones: `_clear_toast` fires five seconds later,
which is long enough to change screen twice.
"""
import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def _unbound_single_shots(path: pathlib.Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "singleShot"):
            continue
        # (msec, functor) is the unbound form; (msec, context, functor) is not.
        if len(node.args) < 3:
            bad.append(f"{path.relative_to(APP).as_posix()}:{node.lineno}")
    return bad


def test_no_timer_outlives_what_it_touches():
    offenders = [hit for path in APP.rglob("*.py")
                 if "__pycache__" not in str(path)
                 for hit in _unbound_single_shots(path)]
    assert not offenders, (
        "QTimer.singleShot without a context object — pass the owning widget "
        "as the second argument so Qt cancels it on destruction:\n  "
        + "\n  ".join(offenders))


def test_a_bound_timer_really_is_dropped_on_destruction(qapp):
    """Proves the mechanism the rule depends on, rather than assuming it."""
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtWidgets import QWidget
    import shiboken6

    for bound, expected in ((True, []), (False, [1])):
        fired = []
        widget = QWidget()
        if bound:
            QTimer.singleShot(0, widget, lambda: fired.append(1))
        else:
            QTimer.singleShot(0, lambda: fired.append(1))
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not shiboken6.isValid(widget)
        for _ in range(3):
            qapp.processEvents()
        assert fired == expected, (
            "bound timer fired after its context died" if bound else
            "unbound timer did not fire — the test proves nothing")
