"""The inspector's Files tab: what an entity actually holds, grouped by kind.

Split out of ``findings_dashboard`` once the tab grew past a screenful of its
own logic. It owns everything below the tab bar — the bucket rows, the file
rows, the selection and the recycle button — and talks to the rest of the
screen through a small surface:

    panel.load(entity) -> int    # how many files; 0 means "no tab worth showing"
    panel.repaint_rows()         # after a theme change
    recycle_cb(item)             # the user asked to recycle the selection
    ask_ai_file_cb(path)         # the user asked about one file

What goes in which bucket is decided in ``app.models.file_grouping``; this is
only the view of it.
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from app.i18n import tr
from app.models.file_grouping import default_expanded, group_files, stat_files
from app.models.finding import _format_size
from app.themes.theme_manager import get_palette
from app.widgets.controls import (
    ElidedLabel, TacticalCheckBox, ask_ai_button_qss, ask_ai_quiet_qss,
)


class _FileGroupRow(QWidget):
    """Header row for one file bucket — the whole row toggles it.

    Not a QPushButton carrying the bucket name: a button reports its label as
    its minimum width, and "Programmes et bibliothèques" plus a French
    "Demander à l'IA" pushed the row 33 px past a 365 px inspector, where the
    scrollbar is off and the overflow is simply cut. An ElidedLabel accepts
    whatever width it is given; the click target moves to the row, which is
    also what the entity list does one level up — a 10 px chevron is an unkind
    target for the row's primary action.

    Child widgets that accept the press (the checkbox, Ask AI) keep it; labels
    do not, so they fall through to here.
    """

    def __init__(self, on_toggle, parent=None):
        super().__init__(parent)
        self._on_toggle = on_toggle
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_toggle()
        super().mousePressEvent(event)


class FileListPanel(QWidget):
    """The Files tab. One entity's files, bucketed by kind and selectable."""

    # How many files of one bucket are drawn before "show more" takes over.
    SLICE = 50
    # Below this many files there is nothing to inspect that the row above
    # does not already say, so the caller hides the tab entirely.
    MIN_FILES = 2

    def __init__(self, recycle_cb=None, ask_ai_file_cb=None, parent=None):
        super().__init__(parent)
        self._recycle_cb = recycle_cb
        self._ask_ai_file_cb = ask_ai_file_cb
        self._entity: dict = {}

        # Every path the entity stands for, in the order it was collected.
        self._all_file_paths: list = []
        self._selected_files: set = set()
        self._file_checks: list = []    # (QCheckBox, path) for the drawn rows
        # Grouping state. `_groups` is the ordered bucket list, `_expanded` the
        # kinds currently open, `_limit` how far into each open bucket the list
        # is drawn so far.
        self._groups: list = []
        self._stats: dict = {}
        self._expanded: set = set()
        self._limit: dict = {}
        self._group_checks: list = []   # (QCheckBox, kind) for the drawn rows

        self._build_ui()

    # ── construction ──────────────────────────────────────────────

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._count_lbl = QLabel(tr("0 selected"))
        self._count_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 12px; font-weight: bold;")
        top.addWidget(self._count_lbl)
        top.addStretch()
        # Not "Select page": buckets are sliced individually, so there is no
        # page for it to mean.
        self._btn_select_shown = QPushButton(tr("Select shown"))
        self._btn_select_shown.setObjectName("Subtle")
        self._btn_select_shown.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        self._btn_select_shown.setCursor(Qt.PointingHandCursor)
        self._btn_select_shown.clicked.connect(self.select_shown)
        top.addWidget(self._btn_select_shown)
        self._btn_clear = QPushButton(tr("Clear"))
        self._btn_clear.setObjectName("Subtle")
        self._btn_clear.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        self._btn_clear.setCursor(Qt.PointingHandCursor)
        self._btn_clear.clicked.connect(self.clear_selection)
        top.addWidget(self._btn_clear)
        lay.addLayout(top)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { border: none; }")
        self._container = QWidget()
        self._clay = QVBoxLayout(self._container)
        self._clay.setContentsMargins(0, 0, 0, 0)
        self._clay.setSpacing(2)
        self._clay.addStretch()
        self._scroll.setWidget(self._container)
        lay.addWidget(self._scroll, stretch=1)

        self._btn_recycle = QPushButton(tr("Recycle selected files"))
        self._btn_recycle.setObjectName("Primary")
        self._btn_recycle.setCursor(Qt.PointingHandCursor)
        self._btn_recycle.setEnabled(False)
        self._btn_recycle.clicked.connect(self.recycle_selected)
        lay.addWidget(self._btn_recycle)

    # ── loading ───────────────────────────────────────────────────

    def load(self, entity: dict, paths: list) -> int:
        """Show *paths* for *entity*. Returns how many files are on offer.

        Fewer than MIN_FILES means there is nothing here worth a tab, and the
        panel resets to empty so a later theme repaint has nothing stale to
        redraw.
        """
        self._entity = entity or {}
        self._selected_files = set()
        self._limit = {}
        # Normalise before anything counts it. Two sources feed this — the
        # stored path list and a live folder listing — and neither promises
        # the result is clean:
        #   * A blank or whitespace-only entry drew a nameless, tickable row
        #     offering to delete " ".
        #   * A repeated path was counted twice by the tab label and the
        #     "n of total" counter, which then could never reach its own
        #     total however much the user selected, because selection is a set.
        # dict.fromkeys keeps first-seen order, which is the order the rest of
        # the grouping treats as the caller's.
        paths = list(dict.fromkeys(p for p in paths if p and p.strip()))
        if len(paths) < self.MIN_FILES:
            self._all_file_paths = []
            self._file_checks = []
            self._group_checks = []
            self._groups = []
            self._stats = {}
            self._expanded = set()
            return 0

        self._all_file_paths = list(paths)
        # One stat pass for the whole list — it feeds the bucket order, the
        # per-file order and every size label, so the per-row getsize on each
        # re-render is gone with it.
        self._stats = stat_files(self._all_file_paths)
        self._groups = group_files(self._all_file_paths, self._stats)
        self._expanded = default_expanded(self._groups)
        self.render_rows()
        return len(self._all_file_paths)

    # ── what is drawn ─────────────────────────────────────────────

    def group_by_kind(self, kind: str):
        for g in self._groups:
            if g.kind == kind:
                return g
        return None

    def shown_in(self, group) -> int:
        """How many of this bucket's files are drawn right now."""
        if group.kind not in self._expanded:
            return 0
        return min(group.count, self._limit.get(group.kind, self.SLICE))

    def _rows_to_draw(self) -> list:
        """Every bucket header, plus the slice of files each one is showing.

        Buckets are capped separately instead of the list being cut into
        global pages. Under global paging one large bucket owned every early
        page: on a folder of 713 DLLs beside 33 images and 7 config files the
        other buckets did not appear until page fifteen, so a user looking at
        page one had no way to know they were there. Per-bucket slices keep
        every header on screen from the first frame, and "show more" extends
        one bucket without moving any of the others.
        """
        rows: list = []
        for g in self._groups:
            rows.append(("header", g))
            shown = self.shown_in(g)
            rows.extend(("file", path) for path in g.paths[:shown])
            if shown and shown < g.count:
                rows.append(("more", g))
        return rows

    def render_rows(self):
        """Draw every bucket header and its current slice; selection persists."""
        while self._clay.count() > 1:
            item = self._clay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()   # else the old rows paint over the new ones
                w.deleteLater()
        self._file_checks = []
        self._group_checks = []

        builders = {"header": self._make_group_header,
                    "file": self._make_file_row,
                    "more": self._make_more_row}
        for kind, payload in self._rows_to_draw():
            self._clay.insertWidget(self._clay.count() - 1, builders[kind](payload))
        self._update_counter()

    # ── the three kinds of row ────────────────────────────────────

    def _make_group_header(self, group) -> QWidget:
        """One bucket's row: what it is, how much of it, and how old."""
        p = get_palette()
        accent = p.get("accent", "#7cc596")
        open_now = group.kind in self._expanded

        row = _FileGroupRow(lambda kind=group.kind: self.toggle_group(kind))
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 4, 0, 2)
        rl.setSpacing(8)

        cb = TacticalCheckBox("")
        cb.setChecked(self._group_fully_selected(group))
        cb.setToolTip(tr("Select every file in this group"))
        cb.toggled.connect(
            lambda checked, kind=group.kind: self.set_group_selected(kind, checked))
        rl.addWidget(cb, alignment=Qt.AlignVCenter)

        # U+25BC / U+25B6, matching the entity list's chevron: the "small
        # triangle" pair renders at roughly half height at any font size and
        # disappears, which the entity list already learned the hard way.
        hint = (tr("Hide the {n} items inside", n=group.count) if open_now
                else tr("Show the {n} items inside", n=group.count))
        chevron = QLabel("▼" if open_now else "▶")
        chevron.setFixedWidth(12)
        chevron.setStyleSheet(f"font-size: 9px; color: {accent};")
        chevron.setToolTip(hint)
        rl.addWidget(chevron, alignment=Qt.AlignVCenter)

        # Name and count in ONE elidable label, not two side by side. Two
        # ElidedLabels in a row both carry the Ignored size policy, so the
        # layout hands the stretch to one and collapses the other to nothing —
        # and the one that vanished at a 365 px inspector was the bucket name,
        # the only part of the row that says what any of it is. The age is the
        # least decision-relevant of the three facts, so it moves to the
        # tooltip rather than competing for the same pixels.
        name_lbl = ElidedLabel(self.group_title(group))
        name_lbl.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {accent};")
        name_lbl.setToolTip(self._group_tooltip(group, hint))
        rl.addWidget(name_lbl, stretch=1)

        size_lbl = QLabel(_format_size(group.total_bytes))
        size_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 11px; font-weight: 700;")
        size_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        rl.addWidget(size_lbl)

        # The loud Ask AI lives here, not on every file. "What are these 312
        # .pak files" is a question worth an answer; "what is 00042.pak" is
        # the same answer, asked 312 times.
        if self._ask_ai_file_cb is not None and group.paths:
            ask = QPushButton(tr("Ask AI"))
            ask.setStyleSheet(ask_ai_button_qss())
            ask.setCursor(Qt.PointingHandCursor)
            ask.setToolTip(tr("Explain this file with AI"))
            ask.clicked.connect(
                lambda _c=False, path=group.paths[0]: self._ask_ai_file_cb(path))
            rl.addWidget(ask)

        self._group_checks.append((cb, group.kind))
        return row

    def group_title(self, group) -> str:
        """'Images  ·  23 files' — what it is and how much of it there is.

        A bucket of one says nothing with its count: the single row directly
        underneath already is the count, and "1 file(s)" was only ever there to
        keep the format uniform.

        A bucket named after an extension Vigil does not recognise renders as
        "PCM files" — the identity key stays the raw extension so expansion
        and selection survive a language change.
        """
        kind = (tr("{ext} files", ext=group.ext.lstrip(".").upper())
                if getattr(group, "ext", "") else tr(group.kind))
        if group.count <= 1:
            return kind
        return f"{kind}  ·  " + tr("{n} file(s)", n=group.count)

    def _group_tooltip(self, group, hint: str) -> str:
        if not group.newest_mtime:
            return hint
        stamp = time.strftime("%b %Y", time.localtime(group.newest_mtime))
        return hint + "\n" + tr("newest {date}", date=stamp)

    def _make_file_row(self, path: str) -> QWidget:
        faint = get_palette().get("text_faint", "#57685e")
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(22, 0, 0, 0)   # indent under the bucket header
        rl.setSpacing(8)

        # The name goes in an ElidedLabel beside a text-less box, not in the
        # checkbox itself. TacticalCheckBox reports its full label as its
        # *minimum* width, so a long filename — or a translation of "Ask AI"
        # wider than the English one, which Ukrainian is — pushed the row past
        # the panel edge and the size and the button were simply cut off.
        cb = TacticalCheckBox("")
        cb.setToolTip(path)
        cb.setChecked(path in self._selected_files)
        cb.toggled.connect(lambda checked, p=path: self._on_file_toggle(p, checked))
        rl.addWidget(cb, alignment=Qt.AlignVCenter)

        name_lbl = ElidedLabel(os.path.basename(path) or path, mode=Qt.ElideMiddle)
        name_lbl.setToolTip(path)
        name_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        rl.addWidget(name_lbl, stretch=1)

        size_lbl = QLabel(self.file_size_str(path))
        size_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; color: {faint};")
        size_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        rl.addWidget(size_lbl)

        # Still one per row — a single odd file in a bucket is exactly the case
        # where the per-file answer is the useful one — but drawn quietly, so
        # the filename outweighs the button rather than the other way round.
        if self._ask_ai_file_cb is not None:
            ask = QPushButton(tr("Ask AI"))
            ask.setStyleSheet(ask_ai_quiet_qss())
            ask.setCursor(Qt.PointingHandCursor)
            ask.setToolTip(tr("Explain this file with AI"))
            ask.clicked.connect(
                lambda _checked=False, p=path: self._ask_ai_file_cb(p))
            rl.addWidget(ask)

        self._file_checks.append((cb, path))
        return row

    def _make_more_row(self, group) -> QWidget:
        """The "show N more" line at the foot of a truncated bucket."""
        remaining = group.count - self.shown_in(group)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(22, 0, 0, 6)
        rl.setSpacing(8)
        btn = QPushButton(tr("Show {n} more", n=remaining))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(ask_ai_quiet_qss())
        btn.clicked.connect(lambda _c=False, kind=group.kind: self.show_more(kind))
        rl.addWidget(btn)
        rl.addStretch()
        return row

    def _size_of(self, path: str) -> int:
        """This file's size, measuring it now if the stat budget ran out.

        Memoised into the same table, so a path costs at most one stat however
        often it is drawn or re-totalled. Without this the button previewing a
        selection totalled 0 for everything past the budget while the confirm
        dialog — which measures every target — reported the real figure, and
        the two screens disagreed about the same files.
        """
        hit = self._stats.get(path)
        if hit is None:
            try:
                st = os.stat(path)
                hit = (st.st_size, st.st_mtime)
            except OSError:
                hit = (0, 0.0)
            self._stats[path] = hit
        return hit[0]

    def file_size_str(self, path: str) -> str:
        """The size of one row, measured on demand past the stat budget."""
        if path not in self._stats:
            try:
                os.stat(path)
            except OSError:
                return "—"
        return _format_size(self._size_of(path))

    # ── expanding ─────────────────────────────────────────────────

    def toggle_group(self, kind: str):
        """Open or close a bucket. Every other one stays where it is."""
        if kind in self._expanded:
            self._expanded.discard(kind)
        else:
            self._expanded.add(kind)
        self.render_rows()

    def show_more(self, kind: str):
        group = self.group_by_kind(kind)
        if group is None:
            return
        self._limit[kind] = self.shown_in(group) + self.SLICE
        self.render_rows()

    # ── selecting ─────────────────────────────────────────────────

    def _group_fully_selected(self, group) -> bool:
        return bool(group.paths) and self._selected_files.issuperset(group.paths)

    def set_group_selected(self, kind: str, checked: bool):
        """Tick a bucket → tick every file in it, drawn or not."""
        group = self.group_by_kind(kind)
        if group is None:
            return
        members = set(group.paths)
        if checked:
            self._selected_files |= members
        else:
            self._selected_files -= members
        for cb, path in self._file_checks:
            if path in members:
                cb.blockSignals(True)
                cb.setChecked(checked)
                cb.blockSignals(False)
        self._update_counter()

    def _sync_group_checks(self):
        """Reflect member selection on the bucket rows without re-rendering."""
        for cb, kind in self._group_checks:
            group = self.group_by_kind(kind)
            want = self._group_fully_selected(group) if group else False
            if cb.isChecked() != want:
                cb.blockSignals(True)
                cb.setChecked(want)
                cb.blockSignals(False)

    def _on_file_toggle(self, path: str, checked: bool):
        if checked:
            self._selected_files.add(path)
        else:
            self._selected_files.discard(path)
        self._update_counter()

    def select_shown(self):
        """Tick every file row currently drawn, across all open buckets."""
        for cb, _path in self._file_checks:
            if not cb.isChecked():
                cb.setChecked(True)   # toggled → adds to _selected_files
        self._update_counter()

    def clear_selection(self):
        self._selected_files = set()
        for cb, _path in self._file_checks:
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self._update_counter()

    def _update_counter(self):
        n = len(self._selected_files)
        total = len(self._all_file_paths)
        self._count_lbl.setText(
            tr("{n} of {total} selected").format(n=n, total=total))
        self._sync_group_checks()
        self._btn_recycle.setEnabled(n > 0 and self._recycle_cb is not None)
        size = sum(self._size_of(p) for p in self._selected_files)
        # The count alone never said whether the selection was worth making.
        # Ticking 40 files is a different decision at 12 KB than at 12 GB, and
        # this button is the last place before the confirm dialog to say so.
        self._btn_recycle.setText(
            tr("Recycle {n} selected file(s) · {size}", n=n, size=_format_size(size))
            if n and size else
            tr("Recycle {n} selected file(s)").format(n=n) if n
            else tr("Recycle selected files")
        )

    def recycle_selected(self):
        selected = sorted(self._selected_files)
        if not selected or not self._recycle_cb:
            return
        item = dict(self._entity)
        item["removable_file_paths"] = selected
        item["entity_type"] = self._entity.get("entity_type", "")
        item["name"] = tr("{n} file(s) from {group}").format(
            n=len(selected), group=self._entity.get("name", "group"))
        self._recycle_cb(item)

    # ── theming ───────────────────────────────────────────────────

    def repaint_rows(self):
        """Redraw against the palette that is live *now*.

        Every row bakes the palette in as it is built — the bucket accent, the
        faint size column, both Ask AI styles — and nothing re-applied it, so a
        theme switch left the list wearing the previous theme until the user
        clicked a different entity.
        """
        if self._groups:
            self.render_rows()

    def schedule_repaint(self):
        """Repaint after Qt has finished with the widgets, never during.

        setStyleSheet() re-polishes every live widget, and the StyleChange that
        announces it arrives *while* Qt is walking the tree. Rebuilding the
        rows there — hide(), deleteLater(), fifty fresh widgets — pulls
        children out from under that walk and faults with an access violation
        (0xC0000005), which is how the whole test suite once started
        segfaulting. A zero-delay timer puts the rebuild after the polish.

        `self` is passed as the timer's *context*, which is load-bearing: a
        theme switch immediately followed by the screen closing left the timer
        pending on a panel whose C++ half was already gone, and it faulted the
        same way on the way back in. With a context object Qt drops the
        pending call when that object is destroyed. A try/except around the
        call is not equivalent — a destroyed QWidget does not reliably raise
        before it faults.
        """
        QTimer.singleShot(0, self, self._repaint_safely)

    def _repaint_safely(self):
        # Belt and braces: the context binding above stops the common case,
        # this covers a wrapper that outlives its C++ object for any other
        # reason.
        try:
            self.repaint_rows()
        except RuntimeError:
            pass
