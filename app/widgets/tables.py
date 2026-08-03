"""Reusable table helpers for Vigil."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush


class RowHighlightDelegate(QStyledItemDelegate):
    """Paint whole-row backgrounds that a stylesheet cannot suppress.

    As soon as ANY QSS rule targets ``QTableWidget::item`` — and the app theme
    always sets padding and a border there — Qt's stylesheet style takes over
    ``PE_PanelItemViewItem`` and ignores both ``QTableWidgetItem.setBackground()``
    and the view's ``backgroundBrush``. Model-side tinting then paints nothing
    at all, silently: the code looks right and the highlight never appears.
    Filling the rect here, before handing the item to ``drawControl``, is the
    only approach that survives.

    ``row_color(row) -> QColor | None`` decides each row's colour; None leaves
    the row to the normal painting path. Rows Qt considers selected are always
    left alone so the real selection highlight still wins.
    """

    def __init__(self, row_color, parent=None):
        super().__init__(parent)
        self._row_color = row_color

    def _fill_for(self, option, index):
        if option.state & QStyle.State_Selected:
            return None
        return self._row_color(index.row())

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if self._fill_for(option, index) is not None:
            # Drop the per-cell MouseOver flag, or the platform style repaints
            # just the cell under the cursor with its own highlight on top of
            # our fill and that one cell looks different from the rest of the
            # row. (Operate on the flag enum directly — it is not int().)
            option.state &= ~QStyle.State_MouseOver

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        color = self._fill_for(opt, index)
        if color is not None:
            painter.fillRect(opt.rect, color)
            opt.backgroundBrush = QBrush()   # NoBrush — no platform background
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)


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
