"""Chip/filter bar widget for Podbye."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout
from PySide6.QtCore import Signal
from app.widgets.pills import Chip


class ChipBar(QFrame):
    """A horizontal row of toggleable filter chips."""

    filter_changed = Signal(list)

    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(6)

        self._chips: list[Chip] = []
        for text in labels:
            chip = Chip(text)
            chip.toggled_sig.connect(self._on_chip_toggled)
            self._chips.append(chip)
            layout.addWidget(chip)

        layout.addStretch()

    def _on_chip_toggled(self, text: str, active: bool):
        active_filters = [c.chip_text for c in self._chips if c.isChecked()]
        self.filter_changed.emit(active_filters)

    def active_filters(self) -> list[str]:
        return [c.chip_text for c in self._chips if c.isChecked()]
