"""Shut background threads down before the widgets that own them go away.

Every long job in Podbye is a QThread parented to the screen that started it —
``CleanupWorker(parent=self)``, ``QuickCleanupDetector(parent=self)``,
``StartupAIWorker(parent=self)``. That parenting is what makes changing the
language mid-work fatal: switching language rebuilds the whole shell and
deletes the outgoing widget tree, Qt destroys the running QThread along with
its parent, and ``QThread::~QThread`` calls ``std::terminate`` on a thread
that has not finished. Windows reports it as 0xC0000409 (__fastfail) — the
process vanishes with no traceback, which is exactly what "app crashes" looked
like when a Quick Cleanup deletion was in flight.

The rule this module enforces: a widget tree is never destroyed while one of
its threads is still running. Ask nicely first (cancel + wait); if a thread
will not stop in time, cut it loose from its parent and keep a reference here
so the widget can die without taking a live thread with it.
"""
from __future__ import annotations

# Threads that outlived their owner. Holding a Python reference matters as
# much as the reparenting: drop the last one and PySide destroys the C++
# QThread, which is the very crash this module exists to prevent.
_ORPHANED: list = []


def _is_running(worker) -> bool:
    try:
        return bool(worker.isRunning())
    except RuntimeError:
        return False        # the C++ object is already gone


def stop_worker(worker, timeout_ms: int = 3000) -> bool:
    """Stop *worker*, returning True if it actually finished.

    A False return is not a failure to handle — the thread has been disowned
    and is safe to leave running; it simply had not finished yet.
    """
    if worker is None or not _is_running(worker):
        return True

    cancel = getattr(worker, "cancel", None)
    if callable(cancel):
        try:
            cancel()
        except RuntimeError:
            return True
    try:
        worker.requestInterruption()
        if worker.wait(timeout_ms):
            return True
        # Would not stop. Disown it: the parent widget must be free to be
        # deleted, and a parentless QThread is not destroyed with it.
        worker.setParent(None)
        _ORPHANED.append(worker)
    except RuntimeError:
        return True
    _prune()
    return False


def retire_worker(worker, timeout_ms: int = 3000) -> None:
    """Finish with *worker* so its Python reference can safely be dropped.

    stop_worker() returns early for a thread that is no longer running, which
    is right when the owner is being torn down — but not when the *reference*
    is about to be replaced. A QThread whose run() has returned still reports
    isRunning() False while never having been joined, and destroying it in that
    state calls std::terminate exactly as if it were mid-run.

    Reproduced on an ordinary sequence: start a scan, stop it, start another.
    The second ScanWorker overwrote self._worker, the first wrapper became
    garbage, and the next collection took the process down with 0xC0000409 —
    no traceback, nothing in the log, and nowhere near the click that caused
    it.

    wait() on an already-finished thread returns immediately, so this costs
    nothing in the normal case. A thread that will not stop is disowned into
    _ORPHANED, which keeps the Python reference alive on purpose.
    """
    if worker is None:
        return
    try:
        if _is_running(worker):
            for name in ("cancel", "halt"):
                stop = getattr(worker, name, None)
                if callable(stop):
                    stop()
                    break
            worker.requestInterruption()
        if not worker.wait(timeout_ms):
            worker.setParent(None)
            _ORPHANED.append(worker)
            _prune()
    except (RuntimeError, AttributeError):
        pass        # already gone, or never a QThread; nothing left to retire


def _prune() -> None:
    """Forget orphans that have since finished."""
    global _ORPHANED
    _ORPHANED = [w for w in _ORPHANED if _is_running(w)]


def orphaned_workers() -> int:
    """How many disowned threads are still running (diagnostics and tests)."""
    _prune()
    return len(_ORPHANED)


def stop_all(*workers, timeout_ms: int = 3000) -> bool:
    """Stop several workers; True only if every one of them finished."""
    # Cancel everything first so the waits overlap instead of adding up.
    for worker in workers:
        if worker is not None and _is_running(worker):
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except RuntimeError:
                    pass
    return all(stop_worker(w, timeout_ms) for w in workers)
