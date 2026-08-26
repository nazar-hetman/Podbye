"""Progress bar and stage indicator widgets for Podbye."""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QDialog, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
)


class StageProgress(QFrame):
    """A pipeline stage progress indicator."""

    def __init__(self, stages: list[tuple[str, int]], parent=None):
        """stages: list of (stage_name, percent_complete)."""
        super().__init__(parent)
        self.setObjectName("Panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        self._bars = []
        for name, pct in stages:
            row = QHBoxLayout()
            row.setSpacing(8)

            lbl = QLabel(name)
            lbl.setObjectName("Dim")
            lbl.setStyleSheet("font-size: 13px;")
            lbl.setFixedWidth(160)
            row.addWidget(lbl)

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setTextVisible(True)
            bar.setFixedHeight(18)
            row.addWidget(bar)

            layout.addLayout(row)
            self._bars.append((lbl, bar))

    def set_stage(self, index: int, percent: int):
        if 0 <= index < len(self._bars):
            self._bars[index][1].setValue(percent)


class _CallableThread(QThread):
    """Run a plain callable off the UI thread and keep its result."""

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self.result = None

    def run(self):
        try:
            self.result = self._fn()
        except Exception:
            self.result = None


class BusyDialog(QDialog):
    """Modal 'working…' dialog that keeps the UI alive during a blocking call.

    Reading a session snapshot can take seconds on a large scan, and doing it
    inline froze the whole window with no indication that anything was
    happening. Running it here keeps the event loop turning, so the app stays
    painted and the user can see it is busy rather than broken.
    """

    def __init__(self, message: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(message)
        self.setModal(True)
        # No close button: the work cannot be cancelled part-way, and a dialog
        # the user can dismiss while the thread runs invites a use-after-free.
        self.setWindowFlags(
            Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint
        )
        self.setFixedWidth(320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        label = QLabel(message)
        label.setWordWrap(True)
        layout.addWidget(label)

        bar = QProgressBar()
        bar.setRange(0, 0)          # indeterminate
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        layout.addWidget(bar)

    def reject(self):
        """Ignore Esc — see the window-flags note above."""

    def run(self, fn, show_after_ms: int = 200):
        """Execute *fn* in a worker thread; return its result when it finishes.

        Nothing is shown for work that finishes inside *show_after_ms* — most
        sessions are small, and flashing a modal open and shut on every click
        looks like a glitch.
        """
        thread = _CallableThread(fn, self)
        thread.finished.connect(self.accept)
        thread.start()
        if not thread.wait(show_after_ms):
            # Still going: show the dialog and let the event loop run. accept()
            # arrives on this thread via the queued finished signal, including
            # when the worker finished during the gap before exec() started.
            self.exec()
        thread.wait()
        return thread.result


def run_busy(parent, message: str, fn):
    """Run *fn* off the UI thread behind a modal busy dialog; return its result."""
    return BusyDialog(message, parent).run(fn)
