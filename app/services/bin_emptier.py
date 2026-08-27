"""Empty the Recycle Bin off the UI thread, without pretending it can stop.

``SHEmptyRecycleBinW`` is a synchronous shell call with no cancel handle. On a
bin holding tens of thousands of files it blocks for as long as the delete
takes, and run from a click handler that means the window paints "Not
Responding" for the duration.

Moving it to a thread is easy. The part that needs care is that it does not
fit the contract every other background job in Podbye is built on. See
``app/services/workers.py``: that module's rule is *cancel, wait a few seconds,
and cut the thread loose from its parent if it will not stop*. Two of those
three are unavailable here — there is nothing to cancel, and "cut it loose" is
how you end up deleting a widget tree while a shell call is still walking
``$Recycle.Bin``. So this is deliberately not a cancellable worker. It is an
operation the app is *busy with*, and the only correct response to a shutdown
is to wait for it.

Which is also why it does not belong to a screen. A widget-owned thread dies
with its widget — that is the crash workers.py exists to prevent — and the one
job here that genuinely cannot be interrupted is the worst candidate for that
ownership. The worker is held at module level, parented to nothing, and
outlives any screen that started it.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from app.i18n import tr


class _EmptyWorker(QThread):
    """Runs one SHEmptyRecycleBinW call. No cancel — see the module docstring."""

    done = Signal(bool, str)        # ok, message

    def __init__(self, drive: str | None = None):
        super().__init__(None)      # no parent: nothing may destroy this
        self._drive = drive

    def run(self):
        from app.services.recycle_bin import empty_recycle_bin
        try:
            ok, message = empty_recycle_bin(self._drive)
        except Exception as exc:                      # never take the app down
            ok, message = False, str(exc)
        self.done.emit(ok, message)


class _Signaller(QObject):
    """Module-level broadcaster, so screens can follow the operation without
    owning it. Same shape as theme_manager's theme_signaller()."""

    started = Signal()
    finished = Signal(bool, str)    # ok, message


_signaller = _Signaller()
# A Python reference is as load-bearing as the missing parent: drop the last
# one and PySide destroys the C++ QThread mid-run.
_worker: _EmptyWorker | None = None


def signaller() -> _Signaller:
    """Subscribe once, in a constructor, and stay subscribed."""
    return _signaller


def is_emptying() -> bool:
    try:
        return _worker is not None and _worker.isRunning()
    except RuntimeError:            # C++ side already gone
        return False


def busy_reason() -> str:
    """Why the app must not be torn down right now, or ""."""
    return tr("the Recycle Bin is being emptied") if is_emptying() else ""


def start(drive: str | None = None) -> bool:
    """Begin emptying. False when one is already running.

    The caller is expected to have asked the user first: this is the one
    genuinely irreversible thing Podbye can do.
    """
    global _worker
    if is_emptying():
        return False

    worker = _EmptyWorker(drive)
    worker.done.connect(_on_done)
    _worker = worker
    _signaller.started.emit()
    worker.start()
    return True


def _on_done(ok: bool, message: str):
    _signaller.finished.emit(ok, message)


def wait(timeout_ms: int | None = None) -> bool:
    """Block until the emptying finishes. True when nothing is left running.

    ``timeout_ms=None`` waits indefinitely, and that is the default on
    purpose. The usual three-second worker timeout is a cancellation deadline:
    ask a thread to stop, give it a moment, orphan it if it refuses. None of
    that applies to a call that cannot be asked to stop — a timeout here would
    only mean "carry on tearing down while the shell is still deleting", which
    is the crash rather than the fix.
    """
    if not is_emptying():
        return True
    try:
        if timeout_ms is None:
            _worker.wait()
            return True
        return bool(_worker.wait(timeout_ms))
    except RuntimeError:
        return True


def reset_for_tests() -> None:
    """Drop the retained worker. Tests only."""
    global _worker
    _worker = None
