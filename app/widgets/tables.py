"""Reusable table helpers for Vigil."""
from __future__ import annotations

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
from PySide6.QtCore import Qt


def create_table(columns: list[tuple[str, int]], row_count: int = 0) -> QTableWidget:
    """Create a styled QTableWidget.

    columns: list of (header_text, width_or_stretch).
             Use -1 for stretch, positive int for fixed width.
    """
    table = QTableWidget()
    table.setColumnCount(len(columns))
    table.setRowCount(row_count)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)
    table.verticalHeader().setDefaultSectionSize(36)

    headers = []
    for i, (text, width) in enumerate(columns):
        headers.append(text)
        if width == -1:
            table.horizontalHeader().setSectionResizeMode(i, QHeaderView.Stretch)
        else:
            table.setColumnWidth(i, width)

    table.setHorizontalHeaderLabels(headers)
    return table


def set_row(table: QTableWidget, row: int, values: list[str], align_right: list[int] = None):
    """Populate a row with string values."""
    align_right = align_right or []
    for col, val in enumerate(values):
        item = QTableWidgetItem(val)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        if col in align_right:
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        else:
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.setItem(row, col, item)
