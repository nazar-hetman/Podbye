"""Operator feed / log widget for Vigil."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QPlainTextEdit
from PySide6.QtCore import Qt, QTimer
from app.i18n import tr

# Maximum lines retained in the feed before old ones are discarded
_MAX_LINES = 1000

# Batch append interval (ms) — buffer rapid-fire log lines
_BATCH_MS = 120


class OperatorFeed(QFrame):
    """A scrollable stdout-style log feed using a single QPlainTextEdit
    instead of per-line QLabel widgets.  Lines are batched and appended
    at a throttled interval so rapid-fire log output cannot freeze the UI.
    """

    def __init__(self, parent=None, show_header: bool = True):
        super().__init__(parent)
        if show_header:
            self.setObjectName("Panel")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if show_header:
            header = QLabel("  " + tr("OPERATOR FEED"))
            header.setObjectName("SectionHeader")
            header.setFixedHeight(28)
            header.setStyleSheet(
                "font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 9px; letter-spacing: 2px; padding-left: 12px;"
            )
            outer.addWidget(header)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setUndoRedoEnabled(False)
        self._text.setMaximumBlockCount(_MAX_LINES)
        self._text.setStyleSheet(
            "QPlainTextEdit {"
            "  font-family: 'JetBrains Mono'; font-size: 11px;"
            "  border: none; background: transparent;"
            "  padding: 6px 10px;"
            "}"
        )
        self._text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Says what it is waiting for, like the findings table beside it does.
        # An empty panel next to one reading "Waiting for scan" looks like the
        # feed failed rather than like it has nothing to report yet.
        self._text.setPlaceholderText(tr("Scan output appears here."))
        outer.addWidget(self._text)

        # Batch buffer
        self._buffer: list[str] = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(_BATCH_MS)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush)

        self._line_count = 0

    def add_line(self, text: str, dim: bool = False):
        """Queue a line for display. Lines are flushed in batches."""
        self._buffer.append(text)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def load_lines(self, lines: list[str]):
        """Bulk-load lines (e.g. restoring from history)."""
        self._buffer.extend(lines)
        self._flush()

    def _flush(self):
        """Append buffered lines to the text widget in one shot."""
        if not self._buffer:
            return
        chunk = "\n".join(self._buffer)
        self._buffer.clear()
        self._text.appendPlainText(chunk)
        # Auto-scroll to bottom
        vbar = self._text.verticalScrollBar()
        vbar.setValue(vbar.maximum())
