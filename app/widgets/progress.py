"""Progress bar and stage indicator widgets for Vigil."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar


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
