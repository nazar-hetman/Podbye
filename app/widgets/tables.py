"""Reusable table helpers for Vigil."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
)
from PySide6.QtCore import Qt, QEvent, QObject, QTimer
from PySide6.QtGui import QBrush, QFontMetrics


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


# QHeaderView::section reserves horizontal padding before any text is drawn.
# The app stylesheet uses 10px a side; History's local sheet uses 6px. Taking
# the larger keeps one number honest for both.
_HEADER_PADDING = 22


def fit_header_widths(table: QTableWidget) -> None:
    """Grow any non-stretch column too narrow for its own heading.

    Every column width in this app was chosen against the English heading, and
    a translated one is routinely wider — "ITEMS" becomes "ЕЛЕМЕНТИ" or
    "ÉLÉMENTS". Qt elides the overflow without a word, so the Analyze table's
    ITEMS column read "ЛЕМЕНТ" and History's read the same.

    Call this after the table is polished, not while building it: before the
    stylesheet is applied the header still carries the application default
    font, and the measurement comes out too small.
    """
    # QTableWidget exposes horizontalHeader(); QTreeWidget calls it header().
    getter = getattr(table, "horizontalHeader", None) or getattr(table, "header")
    hdr = getter()
    fm = QFontMetrics(hdr.font())
    for col in range(hdr.count()):
        if hdr.sectionResizeMode(col) == QHeaderView.Stretch:
            continue
        text = _header_text(table, col)
        if not text.strip():
            continue
        need = fm.horizontalAdvance(text) + _HEADER_PADDING
        if need > table.columnWidth(col):
            table.setColumnWidth(col, need)


def _header_text(table, col: int) -> str:
    """The heading in *col*, for either a table or a tree."""
    if hasattr(table, "horizontalHeaderItem"):
        item = table.horizontalHeaderItem(col)
        return item.text() if item is not None else ""
    item = table.headerItem()
    return item.text(col) if item is not None else ""


class _HeaderFitFilter(QObject):
    """Re-run fit_header_widths whenever the table is shown or restyled."""

    def __init__(self, table: QTableWidget):
        super().__init__(table)
        self._table = table
        table.installEventFilter(self)

    def eventFilter(self, obj, event):
        # getattr, not self._table: the filter is parented to the table, so
        # during interpreter teardown Python can clear this object's __dict__
        # while Qt still delivers a queued event through the C++ side. Reading
        # the attribute directly raised AttributeError out of the override.
        table = getattr(self, "_table", None)
        if table is not None and obj is table and event.type() in (
                QEvent.Show, QEvent.StyleChange, QEvent.FontChange):
            # Deferred, not inline. Resizing a column repaints, and a repaint
            # dispatched from inside an event filter re-enters widgets that
            # are still being constructed — it reached a half-built
            # TacticalComboBox and raised out of a C++ virtual.
            QTimer.singleShot(0, self, self._fit)
        return False

    def _fit(self):
        table = getattr(self, "_table", None)
        if table is None:
            return
        try:
            fit_header_widths(table)
        except RuntimeError:
            pass        # the C++ table went away on a screen rebuild


def install_header_fit(table: QTableWidget) -> None:
    """Keep every heading in *table* readable, in any language."""
    table._header_fit_filter = _HeaderFitFilter(table)


def create_table(columns: list[tuple[str, int]], row_count: int = 0,
                 align_right: list[int] | None = None) -> QTableWidget:
    """Create a styled QTableWidget.

    columns: list of (header_text, width_or_stretch).
             Use -1 for stretch, positive int for fixed width.
    align_right: column indices whose cells are right-aligned — the heading is
             aligned to match. Qt centres header text by default, which leaves
             every heading adrift of the values it names.
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
    align_header(table, align_right)
    install_header_fit(table)
    return table


def align_header(table: QTableWidget, align_right: list[int] | None = None) -> None:
    """Align each heading the way that column's cells are aligned."""
    right = set(align_right or [])
    for col in range(table.columnCount()):
        item = table.horizontalHeaderItem(col)
        if item is not None:
            item.setTextAlignment(
                (Qt.AlignRight if col in right else Qt.AlignLeft) | Qt.AlignVCenter)


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
