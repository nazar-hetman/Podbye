"""History screen — dual-panel investigation console.

Two independent operational feeds, side by side:
  Left  — Cleanup Sessions  (what was deleted, when, how much freed)
  Right — Analyze Sessions  (scan runs, re-run / open findings)

Each panel owns its own table, contextual detail, and empty state.
"""
from __future__ import annotations

import datetime
import os
import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QObject, QEvent
from PySide6.QtGui import QColor, QBrush

from app.widgets.tables import RowHighlightDelegate, install_header_fit


class _RowHoverFilter(QObject):
    """Track the hovered row and repaint via delegate + cell-widget stylesheets.

    Two-part approach required because QSS makes item.setBackground() a no-op
    (see RowHighlightDelegate):
      RowHighlightDelegate — covers QTableWidgetItem cells
      _sync_widget_bgs     — covers cell widgets (e.g. MODE badge)
    """

    def __init__(self, table: "QTableWidget", color: QColor):
        super().__init__(table)
        self._table = table
        self._color = color
        self._row = -1
        table.viewport().installEventFilter(self)
        self._delegate = RowHighlightDelegate(
            lambda row: self._color if row == self._row else None, table
        )
        table.setItemDelegate(self._delegate)

    def eventFilter(self, obj, event):
        try:
            if obj is self._table.viewport():
                et = event.type()
                if et == QEvent.MouseMove:
                    try:
                        y = int(event.position().y())
                    except AttributeError:
                        y = event.pos().y()
                    new_row = self._table.rowAt(y)
                    if new_row != self._row:
                        self._sync_widget_bgs(self._row, False)
                        self._row = new_row
                        self._sync_widget_bgs(new_row, True)
                        self._table.viewport().update()
                elif et == QEvent.Leave:
                    self._sync_widget_bgs(self._row, False)
                    self._row = -1
                    self._table.viewport().update()
        except RuntimeError:
            # The underlying C++ table was destroyed on a screen rebuild while
            # this filter is still installed — a late event can arrive before
            # the filter is torn down. Nothing to do; let it pass through.
            return False
        return False

    def _sync_widget_bgs(self, row: int, hovered: bool):
        """Update cell-widget backgrounds; delegate handles QTableWidgetItems."""
        if row < 0:
            return
        p = get_palette()
        selected = self._table.selectionModel().isRowSelected(row, self._table.rootIndex())
        if selected:
            bg = p.get("accent_soft", "#1b2e22")
        elif hovered:
            bg = self._color.name()
        else:
            bg = "transparent"
        for col in range(self._table.columnCount()):
            w = self._table.cellWidget(row, col)
            if w is not None:
                w.setStyleSheet(f"background: {bg};")

from app.themes.theme_manager import get_palette
from app.models.finding import _format_size
from app.models.risk import normalized_risk_totals
from app.i18n import tr
from app.services.cleanup_result_classifier import (
    STATE_ALREADY_CLEAN,
    STATE_FAILED,
    STATE_IN_USE,
    STATE_PARTIAL,
    STATE_SKIPPED,
    STATE_SUCCESS,
    assess_cleanup_counts,
)
from app.screens.analyze import _rgba
from app.widgets.panels import apply_tactical_label
from app.widgets.controls import ElidedLabel


# ── Helpers ───────────────────────────────────────────────────────


def _format_when(ts: float) -> str:
    if not ts:
        return "—"
    now = time.time()
    days_ago = (now - ts) / 86400
    dt = datetime.datetime.fromtimestamp(ts)
    if days_ago < 1:
        return tr("Today {time}", time=dt.strftime('%H:%M'))
    if days_ago < 2:
        return tr("Yesterday {time}", time=dt.strftime('%H:%M'))
    if days_ago < 7:
        return tr("{n}d ago", n=int(days_ago))
    return dt.strftime("%Y-%m-%d")


def _format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    if seconds < 60:
        return f"{int(seconds)}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _short_target(target: str) -> str:
    if not target:
        return "—"
    parts = target.replace("\\", "/").rstrip("/").split("/")
    if len(parts) <= 3:
        return target
    return parts[0] + "/…/" + "/".join(parts[-2:])


def _risk_pcts(risk_totals: dict) -> tuple:
    risk_totals = normalized_risk_totals(risk_totals)
    total = sum(risk_totals.values())
    if not total:
        return 33, 34, 33
    safe = int(risk_totals.get("Safe", 0) * 100 / total)
    review = int((risk_totals.get("Optional", 0) + risk_totals.get("Review", 0)) * 100 / total)
    risk = max(100 - safe - review, 0)
    return safe, review, risk


def _category_totals_top(category_totals: dict, limit: int = 3) -> list[tuple[str, int, int]]:
    """Return [(category, count, size_bytes)] sorted by size, then count."""
    rows: list[tuple[str, int, int]] = []
    for cat, value in (category_totals or {}).items():
        count = 0
        size = 0
        if isinstance(value, dict):
            try:
                count = int(value.get("count", 0) or 0)
            except (TypeError, ValueError):
                count = 0
            try:
                size = int(value.get("size_bytes", 0) or 0)
            except (TypeError, ValueError):
                size = 0
        else:
            try:
                count = int(value or 0)
            except (TypeError, ValueError):
                count = 0
        if count > 0 or size > 0:
            rows.append((str(cat), count, size))
    rows.sort(key=lambda row: (row[2], row[1]), reverse=True)
    return rows[:limit]


def _item_size_bytes(item: dict) -> int:
    for key in ("reclaimable_bytes", "size_bytes", "bytes", "size"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def _cleanup_top_categories(items: list, limit: int = 3,
                            sizes_trusted: bool = True
                            ) -> list[tuple[str, int, int]]:
    """Rows of ``(label, count, size)`` for the "TOP CLEANED" block.

    *sizes_trusted* is false for records written before per-file sizes were
    measured, where a bucket's total was copied onto each of its members. Those
    add up to more than the run actually freed, and the card printed the two
    figures a few pixels apart — "CLEANED 3.9 GB" over a breakdown summing to
    5.9 GB. The counts in those records are still right, so the rows fall back
    to counts; _breakdown_rows already shows a count when there is no size.
    """
    grouped: dict[str, dict[str, int]] = {}
    for item in items:
        label = _cleanup_target_label(item)
        if label not in grouped:
            grouped[label] = {"count": 0, "size": 0}
        grouped[label]["count"] += 1
        grouped[label]["size"] += _item_size_bytes(item) if sizes_trusted else 0
    rows = [
        (label, data["count"], data["size"])
        for label, data in grouped.items()
    ]
    rows.sort(key=lambda row: (row[2], row[1]), reverse=True)
    return rows[:limit]


def _attention_count(risk_totals: dict) -> int:
    risk_totals = normalized_risk_totals(risk_totals)
    review = int(risk_totals.get("Review", 0) or 0)
    protected = int(risk_totals.get("Protected", 0) or 0)
    return review + protected


def _estimated_reclaimable(record: dict) -> int:
    explicit = record.get("reclaimable_bytes") or record.get("total_reclaimable_bytes")
    if isinstance(explicit, (int, float)):
        return int(explicit)
    return 0


def _freed_for_session(session_id: str) -> tuple[int, int]:
    """(bytes_freed, items_removed) across cleanups spawned by this scan session.

    Cleanup records store the scan session_id that spawned them, so an analyze
    session can report what was actually deleted from its findings — not just
    what was reclaimable. Best-effort; returns (0, 0) when nothing links.
    """
    if not session_id:
        return 0, 0
    from app.state.session_store import load_cleanup_records
    freed = items = 0
    for rec in load_cleanup_records():
        if rec.get("session_id") == session_id:
            freed += int(rec.get("total_bytes_freed", 0) or 0)
            items += int(rec.get("succeeded_count", 0) or 0)
    return freed, items


def _status_color(status: str, p: dict) -> str:
    """Colour for a scan session's outcome. A stopped run is not a failure —
    it is a partial result, which is the review tier, not the risk tier."""
    return {
        "completed": p.get("safe", "#7aa88a"),
        "stopped":   p.get("review", "#c7a66c"),
        "running":   p.get("review", "#c7a66c"),
    }.get(status, p.get("text_dim", "#8a9b8f"))


def _scan_mode_label(mode: str) -> str:
    return tr("Adaptive scan") if mode == "smart" else tr("All files scan")


def _cleanup_mode_label(mode: str) -> str:
    """Plain-text cleanup mode (the badge form lives in _ModeBadge)."""
    return {
        "recycle_bin": tr("Recycle Bin"),
        "permanent":   tr("Permanent"),
    }.get(mode, (mode or "").replace("_", " ").title())


def _muted_line(text: str, p: dict) -> QLabel:
    """The dim mono one-liner both history detail panels use under their
    metrics row (e.g. "Completed · Adaptive scan · scanned 423 GB")."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(
        f"font-family: 'JetBrains Mono'; font-size: 10px; "
        f"color: {p.get('text_dim', '#8a9b8f')};"
    )
    return lbl


# Path fragment → the human name for what lives there. Kept as a module
# constant so the translation-coverage test can reach the values: they are
# looked up by variable, which no static scan over tr() calls would find.
CLEANUP_TARGET_LABELS = [
    ("thumbcache", "Windows thumbnail database"),
    ("softwaredistribution", "Windows update cache"),
    ("google/chrome", "Chrome cache"),
    ("microsoft/edge", "Edge cache"),
    ("mozilla/firefox", "Firefox cache"),
    ("appdata/local/temp", "User temporary files"),
    ("/temp/", "Temporary files"),
]


def _cleanup_target_label(item: dict) -> str:
    path = item.get("path", "") or ""
    category = item.get("category", "") or ""
    name = item.get("name", "") or ""
    lowered = path.lower().replace("\\", "/")
    for token, label in CLEANUP_TARGET_LABELS:
        if token in lowered:
            return tr(label)
    if category and category != name:
        return tr(category)
    if path:
        return os.path.basename(path.rstrip("/\\")) or path
    if name:
        return name
    return tr(category) if category else "—"


# ── Shared small widgets ──────────────────────────────────────────


# The screens that needed this each grew their own version; the shared one is
# in app.widgets.controls now. Kept as an alias so this module's call sites
# keep reading the way they did.
_Elided = ElidedLabel


def _kv(key: str, val: str, p: dict, *, val_size: int = 12,
        val_color: str = "", bold: bool = False, wrap: bool = False) -> QVBoxLayout:
    """Compact key/value pair — silkscreen eyebrow above a mono value."""
    col = QVBoxLayout()
    col.setSpacing(1)
    k = QLabel(key)
    k.setStyleSheet(
        "font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; "
        f"letter-spacing: 1px; color: {p.get('text_faint', '#57685e')};"
    )
    col.addWidget(k)
    v = QLabel(val)
    style = f"font-family: 'JetBrains Mono'; font-size: {val_size}px;"
    if bold:
        style += " font-weight: bold;"
    if val_color:
        style += f" color: {val_color};"
    v.setStyleSheet(style)
    if wrap:
        v.setWordWrap(True)
    col.addWidget(v)
    return col


def _eyebrow(text: str, p: dict) -> QLabel:
    """The silkscreen section marker above a block inside a detail panel."""
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; "
        f"letter-spacing: 1px; color: {p.get('text_faint', '#57685e')};"
    )
    return lbl


def _breakdown_rows(layout: QVBoxLayout, rows: list[tuple[str, int, int]],
                    p: dict, empty_text: str):
    """The '▪ label ………… size' list both detail panels end with.

    Each panel used to head this block with a comma-joined repeat of the very
    same figures. That line wrapped to two lines in translated builds, pushing
    the rest of the panel out of view, and said nothing the rows below it did
    not already say.
    """
    faint = p.get("text_faint", "#57685e")
    if not rows:
        layout.addWidget(_muted_line(empty_text, p))
        return
    for label, count, size in rows:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        dot = QLabel("▪")
        dot.setFixedWidth(10)
        dot.setStyleSheet(f"color: {faint}; font-size: 8px;")
        row.addWidget(dot)
        name = _Elided(label)
        name.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; "
            f"color: {p.get('text', '#d6e2da')};"
        )
        row.addWidget(name, stretch=1)
        cnt = QLabel(_format_size(size) if size else f"{count:,}")
        cnt.setFixedWidth(72)
        cnt.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        cnt.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; color: {faint};"
        )
        row.addWidget(cnt)
        layout.addLayout(row)


class _DistBar(QFrame):
    """Mini stacked distribution bar (safe/review/risk)."""

    def __init__(self, safe_pct, review_pct, risk_pct, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        p = get_palette()
        defaults = {"safe": "#7aa88a", "review": "#c7a66c", "risk": "#c67a69"}
        for pct, key in [(safe_pct, "safe"), (review_pct, "review"), (risk_pct, "risk")]:
            color = p.get(key, defaults[key])
            seg = QFrame()
            seg.setFixedHeight(6)
            seg.setStyleSheet(f"background: {color}; border: none;")
            lay.addWidget(seg, stretch=max(int(pct), 1))


def _cleanup_status(record: dict) -> tuple[str, str]:
    """(label, palette key) for how a cleanup run turned out.

    Replaces the MODE column, which showed "Recycle Bin" on essentially every
    row: permanent deletion is off by default and has to be turned on
    deliberately, so the column cost a sixth of the table's width to repeat one
    constant. How the run *ended* varies row to row and is the reason someone
    opens this screen at all.

    Vocabulary is shared with the detail panel below and with the cleanup
    dialog, so one run is not described three different ways.
    """
    # The verdict reached when the run happened is stored on the record. Prefer
    # it: re-judging an old run by today's rules would let a classifier change
    # silently rewrite history. Older records predate the field, so those are
    # recomputed from the counts they do carry.
    state = record.get("result_state")
    if not state:
        state = assess_cleanup_counts(
            succeeded_count=record.get("succeeded_count", 0),
            in_use_count=record.get("in_use_count", 0),
            failed_count=record.get("failed_count", 0),
            skipped_count=record.get("skipped_protected_count", 0),
            category_label="Cleanup run",
            retry_label="the cleanup",
        ).state
    return {
        STATE_SUCCESS:       (tr("Complete"),  "safe"),
        STATE_ALREADY_CLEAN: (tr("Complete"),  "safe"),
        STATE_IN_USE:        (tr("Partial"),   "review"),
        STATE_PARTIAL:       (tr("Partial"),   "review"),
        STATE_FAILED:        (tr("Attention"), "risk"),
        STATE_SKIPPED:       (tr("Skipped"),   "text_dim"),
    }.get(state, (tr("Complete"), "safe"))


class _StatusBadge(QLabel):
    """Inline badge for how a cleanup run ended — theme-aware."""

    def __init__(self, record: dict, parent=None):
        super().__init__(parent)
        p = get_palette()
        label, key = _cleanup_status(record)
        color = p.get(key, p.get("text_dim", "#8a9b8f"))
        self.setText(label)
        self.setFixedHeight(20)
        self.setAlignment(Qt.AlignCenter)
        # rgba(), not a hex alpha suffix: "#7aa88a" + "66" is an eight-digit
        # hex and Qt reads those as #AARRGGBB, so the channels rotate and the
        # border came out a colour from no palette in the app.
        self.setStyleSheet(
            "font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 7px; "
            f"letter-spacing: 1px; color: {color}; "
            f"border: 1px solid {_rgba(color, 0.40)}; padding: 0px 6px;"
        )


# ── Detail panels ─────────────────────────────────────────────────


class CleanupRecordDetail(QFrame):
    """Compact contextual detail for a single cleanup record."""

    _MAX_GROUPS = 4

    def __init__(self, record: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("PanelAlt")
        p = get_palette()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(6)

        items = record.get("items", [])
        mode = record.get("mode", "recycle_bin")
        succeeded = record.get("succeeded_count", 0)
        in_use = record.get("in_use_count", 0)
        failed = record.get("failed_count", 0)
        skipped = record.get("skipped_protected_count", 0)
        freed = int(record.get("total_bytes_freed", 0) or 0)
        total_exceptions = in_use + failed + skipped
        status_label, status_key = _cleanup_status(record)
        assessment = assess_cleanup_counts(
            succeeded_count=succeeded,
            in_use_count=in_use,
            failed_count=failed,
            skipped_count=skipped,
            category_label="Cleanup run",
            retry_label="the cleanup",
        )

        # ── Outcome row ───────────────────────────────────────────
        # Mirrors SessionDetail's metrics row exactly (same size/spacing, bold
        # only on the two headline metrics) so the two history panels read as
        # one design — and so the values stop crowding each other.
        stats = QHBoxLayout()
        stats.setSpacing(18)
        _bold_keys = (tr("RESULT"), tr("CLEANED"))
        for lbl, val, col in [
            # Was IMPACT / "High" — a bucket computed from the freed size and
            # the exception count, both of which are printed in this very row.
            # It restated its neighbours in a word that named no unit: high
            # what? RESULT says what actually happened, in the same vocabulary
            # as the STATUS column above and the cleanup dialog.
            (tr("RESULT"), status_label, p.get(status_key, "")),
            (tr("CLEANED"), _format_size(freed), p.get("safe", "#7aa88a")),
            (tr("ITEMS"), f"{succeeded:,}", ""),
            (tr("ATTENTION"), f"{total_exceptions:,}" if total_exceptions else tr("None"),
             p.get("review", "#c7a66c") if total_exceptions else ""),
        ]:
            stats.addLayout(_kv(lbl, val, p, val_size=11,
                                bold=lbl in _bold_keys, val_color=col))
        stats.addStretch()
        layout.addLayout(stats)

        # ── Outcome line ──────────────────────────────────────────
        # Same shape as the scan panel's "Completed · Adaptive scan · scanned X".
        layout.addWidget(_muted_line(
            f"{_cleanup_mode_label(mode)} · {succeeded:,} {tr('items removed')}"
            f" · {_format_size(freed)} {tr('freed')}", p))

        # ── Status note ───────────────────────────────────────────
        note = QLabel(assessment.explanation_text)
        note.setWordWrap(True)
        note.setStyleSheet(
            f"font-size: 11px; color: {p.get('text_dim', '#8a9b8f')};"
        )
        layout.addWidget(note)

        # ── Non-zero exceptions only ───────────────────────────────
        exceptions = []
        if in_use:
            exceptions.append(tr("{n:,} in use", n=in_use))
        if failed:
            exceptions.append(tr("{n:,} failed", n=failed))
        if skipped:
            exceptions.append(tr("{n:,} protected skipped", n=skipped))
        if exceptions:
            exc = QLabel(tr("Still requires attention: {details}",
                            details=" · ".join(exceptions)))
            exc.setWordWrap(True)
            exc.setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 10px; "
                f"color: {p.get('review', '#c7a66c')};"
            )
            layout.addWidget(exc)

        # ── Targets — grouped, structured preview ─────────────────
        layout.addWidget(_eyebrow(tr("TOP CLEANED"), p))
        _breakdown_rows(
            layout,
            _cleanup_top_categories(
                items, self._MAX_GROUPS,
                sizes_trusted=record.get("item_sizes") == "measured"),
            p, tr("No cleaned categories recorded"))


class SessionDetail(QFrame):
    """Compact contextual detail for a scan session."""

    def __init__(self, record: dict, on_open, on_rerun, on_delete, parent=None):
        super().__init__(parent)
        self.setObjectName("PanelAlt")
        p = get_palette()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(6)

        safe_pct, review_pct, risk_pct = _risk_pcts(record.get("risk_totals", {}))
        duration = record.get("saved_at", 0) - record.get("start_time", 0)
        mode_label = _scan_mode_label(record.get("scan_mode", "smart"))
        status = record.get("status", "unknown")
        status_label = {
            "completed": tr("Completed"),
            "stopped":   tr("Stopped (partial)"),
            "running":   tr("In progress"),
        }.get(status, status.title())
        if record.get("scan_mode") == "smart":
            items_val = f"{record.get('display_count', 0):,}"
        else:
            items_val = f"{record.get('scanned_count', 0):,}"
        risk_totals = normalized_risk_totals(record.get("risk_totals", {}) or {})
        attention = _attention_count(risk_totals)
        reclaimable = _estimated_reclaimable(record)
        has_reclaimable = (
            "reclaimable_bytes" in record or "total_reclaimable_bytes" in record
        )

        # ── Target ────────────────────────────────────────────────
        layout.addLayout(_kv(tr("TARGET"), record.get("target", "—") or "—",
                             p, val_size=11, wrap=True))

        # ── Outcome row ───────────────────────────────────────────
        metrics = QHBoxLayout()
        metrics.setSpacing(18)
        reclaimable_text = _format_size(reclaimable) if has_reclaimable else tr("Not recorded")
        # What was actually deleted from this session's findings (cleanups link
        # back by session_id). Only shown once something has been cleaned.
        freed_bytes, freed_items = _freed_for_session(record.get("session_id", ""))
        rows = [
            # Same reasoning as the cleanup panel: IMPACT was derived from the
            # reclaimable size, the review count and the found count, all three
            # of which sit in this row already. RESULT states how the run
            # ended, which nothing else in the row says.
            (tr("RESULT"), status_label, _status_color(status, p)),
            (tr("RECLAIMABLE"), reclaimable_text,
             p.get("safe", "#7aa88a") if reclaimable else ""),
        ]
        if freed_bytes > 0 or freed_items > 0:
            rows.append((tr("FREED"),
                         f"{_format_size(freed_bytes)} · {freed_items:,}",
                         p.get("safe", "#7aa88a")))
        rows += [
            (tr("FOUND"), items_val, ""),
            (tr("REVIEW"), f"{attention:,}" if attention else tr("None"),
             p.get("review", "#c7a66c") if attention else ""),
            (tr("DURATION"), _format_duration(duration), ""),
        ]
        for k, v, col in rows:
            metrics.addLayout(_kv(k, v, p, val_size=11, bold=k in (tr("RESULT"), tr("RECLAIMABLE")), val_color=col))
        metrics.addStretch()
        layout.addLayout(metrics)

        # status_label moved up into the metrics row; repeating it here would
        # say the same word twice, six lines apart.
        layout.addWidget(_muted_line(
            f"{mode_label} · "
            f"{tr('scanned')} {_format_size(record.get('total_size', 0))}", p))

        # ── What was found ────────────────────────────────────────
        # Same eyebrow + '▪ label … size' shape as the cleanup panel, so the
        # two halves of the screen read as one design.
        layout.addWidget(_eyebrow(tr("TOP FINDINGS"), p))
        _breakdown_rows(
            layout,
            [(tr(cat), count, size)
             for cat, count, size in _category_totals_top(record.get("category_totals", {}))],
            p, tr("No categories recorded"))

        layout.addSpacing(2)
        bar = _DistBar(safe_pct, review_pct, risk_pct)
        layout.addWidget(bar)
        attention_parts = []
        review_count = int(risk_totals.get("Review", 0) or 0)
        protected_count = int(risk_totals.get("Protected", 0) or 0)
        optional_count = int(risk_totals.get("Optional", 0) or 0)
        if review_count:
            attention_parts.append(tr("{n:,} review", n=review_count))
        if protected_count:
            attention_parts.append(tr("{n:,} protected", n=protected_count))
        if optional_count:
            attention_parts.append(tr("{n:,} optional", n=optional_count))
        attention_text = (" · ".join(attention_parts) if attention_parts
                          else tr("No review-required items recorded"))
        dtext = QLabel(attention_text)
        dtext.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; "
            f"color: {p.get('text_dim', '#8a9b8f')};"
        )
        layout.addWidget(dtext)

        # ── Actions ───────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        for text, cb in [
            (tr("Open findings"),           on_open),
            (tr("Re-run with same target"), on_rerun),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("Subtle")
            btn.setStyleSheet("font-size: 10px; padding: 4px 10px;")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(cb)
            btn_row.addWidget(btn)

        # Delete carried the same weight as the two actions people actually
        # come here for, while being the only one that cannot be undone. It
        # keeps its place and its size — losing a destructive control is worse
        # than over-showing it — but drops the border and fill so the eye
        # reaches the other two first, and warms to the risk colour on hover so
        # it says what it is at the moment of pressing it.
        btn_row.addStretch()
        p_del = get_palette()
        faint = p_del.get("text_faint", "#57685e")
        risk = p_del.get("risk", "#c67a69")
        btn_del = QPushButton(tr("Delete from history"))
        btn_del.setObjectName("Ghost")
        btn_del.setStyleSheet(
            f"QPushButton#Ghost {{ font-size: 10px; padding: 4px 10px; "
            f"background: transparent; border: none; color: {faint}; }} "
            f"QPushButton#Ghost:hover {{ color: {risk}; "
            f"background: {_rgba(risk, 0.10)}; }}"
        )
        btn_del.setCursor(Qt.PointingHandCursor)
        btn_del.clicked.connect(on_delete)
        btn_row.addWidget(btn_del)
        layout.addLayout(btn_row)


# ── Main screen ───────────────────────────────────────────────────


class HistoryScreen(QWidget):

    open_session_requested = Signal(dict)   # full session data dict
    rerun_requested = Signal(str)           # target path

    _MAX_VISIBLE_ROWS = 5
    _outer = None       # so an early resizeEvent has something to test
    _is_stacked = None
    # Below this content width the two workspaces stack instead of sitting
    # side by side. A session panel needs its four fixed columns (380px) plus
    # a readable TARGET column plus panel chrome — about 560px — and there are
    # two of them, a 10px gap and 36px of screen margin.
    _STACK_BELOW = 1160

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions: list = []
        self._cleanup_records: list = []

        # Cleanup panel state
        self._cleanup_expanded_row: int = -1
        self._cleanup_table: QTableWidget | None = None
        self._cleanup_detail_area: QVBoxLayout | None = None
        self._cleanup_detail_widget: QWidget | None = None
        self._cleanup_detail_spacer = None

        # Session panel state
        self._sess_expanded_row: int = -1
        self._sess_table: QTableWidget | None = None
        self._sess_detail_area: QVBoxLayout | None = None
        self._sess_detail_widget: QWidget | None = None
        self._sess_detail_spacer = None
        self._is_stacked: bool = self._stacked_layout()

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self._build_content()

        # History has many inline-styled labels (mode badges, _kv pairs,
        # distribution bar) that don't auto-refresh from the global QSS;
        # rebuild the whole screen when the theme changes.
        from app.themes.theme_manager import theme_signaller
        theme_signaller().theme_changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _key: str = ""):
        # refresh() reloads from disk and rebuilds the layout — slightly heavy
        # but the screen is small and theme switches are rare.
        self.refresh()

    # ── Public API ────────────────────────────────────────────────

    def refresh(self):
        """Reload history + cleanup records from disk and rebuild the UI."""
        from app.state.session_store import load_history, load_cleanup_records
        self._sessions = load_history()
        self._cleanup_records = load_cleanup_records()
        self._reset_panel_state()
        self._build_content()

    # ── Responsive layout ─────────────────────────────────────────

    def _stacked_layout(self) -> bool:
        return self.width() < self._STACK_BELOW

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Rebuild only when the arrangement actually flips, not on every pixel
        # of a drag. Qt can deliver a resize before __init__ has finished, so
        # do nothing until there is a content layout to rebuild into.
        if getattr(self, "_outer", None) is None:
            return
        if self._stacked_layout() != self._is_stacked:
            self._reset_panel_state()
            self._build_content()
        else:
            # Width changes how the wrapping text inside a panel lays out, so
            # the taller of the two can swap over as the window is dragged.
            self._sync_detail_heights()

    def _reset_panel_state(self):
        """Forget the widgets _build_content is about to replace."""
        self._cleanup_expanded_row = -1
        self._cleanup_table = None
        self._cleanup_detail_area = None
        self._cleanup_detail_widget = None
        self._cleanup_detail_spacer = None
        self._sess_expanded_row = -1
        self._sess_table = None
        self._sess_detail_area = None
        self._sess_detail_widget = None
        self._sess_detail_spacer = None

    # ── Styling helpers ───────────────────────────────────────────

    def _table_qss(self) -> str:
        p = get_palette()
        return (
            f"QTableWidget#HistoryTable {{ "
            f"background: {p.get('panel_alt', '#18241e')}; "
            f"border: 1px solid {p.get('border', '#213028')}; "
            f"selection-background-color: {p.get('accent_soft', '#1b2e22')}; "
            f"selection-color: {p.get('text', '#d6e2da')}; }} "
            f"QTableWidget#HistoryTable::item {{ padding: 6px 8px; border: none; }} "
            f"QTableWidget#HistoryTable::item:selected {{ background: {p.get('accent_soft', '#1b2e22')}; }} "
            f"QHeaderView::section {{ background: {p.get('panel', '#141d18')}; "
            f"color: {p.get('text_faint', '#57685e')}; border: none; "
            f"border-bottom: 1px solid {p.get('border', '#213028')}; padding: 8px 6px; "
            f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; letter-spacing: 1px; }}"
        )

    def _section_header(self, title: str, subtitle: str) -> QHBoxLayout:
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        t = QLabel(title)
        apply_tactical_label(t, font_size=10, letter_spacing=2)
        hdr.addWidget(t)
        s = QLabel(subtitle)
        s.setObjectName("Muted")
        s.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px;")
        hdr.addWidget(s)
        hdr.addStretch()
        return hdr

    def _placeholder(self, eyebrow: str, hint: str) -> QWidget:
        """Compact empty-state helper for panels with no history yet."""
        p = get_palette()
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 10, 16, 10)
        v.setSpacing(4)
        v.addStretch()
        glyph = QLabel("◌")
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setStyleSheet(
            f"font-size: 18px; color: {p.get('text_faint', '#57685e')};"
        )
        v.addWidget(glyph)
        eb = QLabel(eyebrow)
        eb.setAlignment(Qt.AlignCenter)
        eb.setStyleSheet(
            "font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; "
            f"letter-spacing: 2px; color: {p.get('text_faint', '#57685e')};"
        )
        v.addWidget(eb)
        h = QLabel(hint)
        h.setAlignment(Qt.AlignCenter)
        h.setWordWrap(True)
        h.setStyleSheet(
            f"font-size: 10px; color: {p.get('text_faint', '#57685e')};"
        )
        v.addWidget(h)
        v.addStretch()
        return w

    def _new_table(self, headers: list, alignments: list) -> QTableWidget:
        """`alignments` is one Qt alignment per column, matching how that
        column's cells are drawn. Qt centres header text by default, which put
        every heading adrift of the values under it — WHEN sat mid-column above
        left-aligned timestamps, FREED mid-column above right-aligned sizes."""
        t = QTableWidget()
        # Mouse tracking is required so the viewport event filter receives
        # MouseMove events while no button is pressed (and so any
        # ::item:hover QSS rule would also fire, although hover is now
        # handled at the row level by _RowHoverFilter below).
        t.setMouseTracking(True)
        t.viewport().setMouseTracking(True)
        t.setObjectName("HistoryTable")
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setAlternatingRowColors(False)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.verticalHeader().setVisible(False)
        t.setShowGrid(False)
        t.verticalHeader().setDefaultSectionSize(36)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        t.setStyleSheet(self._table_qss())
        t.horizontalHeader().setMinimumSectionSize(26)
        for col, align in enumerate(alignments):
            item = t.horizontalHeaderItem(col)
            if item is not None:
                item.setTextAlignment(align | Qt.AlignVCenter)
        # Whole-row hover. Filter is parented to the table so it dies with it.
        hover_color = QColor(get_palette().get("panel_hover", "#1d2c25"))
        t._row_hover_filter = _RowHoverFilter(t, hover_color)
        # Widths below are chosen against the English headings; this widens
        # any column a translated heading would not fit in.
        install_header_fit(t)
        return t

    def _cap_table_height(self, table: QTableWidget):
        """Size the table to exactly five visible rows with no scrollbar."""
        visible = self._MAX_VISIBLE_ROWS
        header_h = table.horizontalHeader().sizeHint().height()
        row_h = table.verticalHeader().defaultSectionSize()
        frame_h = table.frameWidth() * 2
        table.setFixedHeight(header_h + visible * row_h + frame_h + 1)

    def _limited_history_note(self, total: int, noun: str) -> QLabel | None:
        """`noun` is an English key — it is translated here, not by the caller,
        which used to hand a raw English word into a translated sentence."""
        hidden = total - self._MAX_VISIBLE_ROWS
        if hidden <= 0:
            return None
        lbl = QLabel(tr("Showing latest {n} {noun}; {hidden} older hidden.",
                        n=self._MAX_VISIBLE_ROWS, noun=tr(noun), hidden=hidden))
        lbl.setObjectName("Muted")
        lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px;")
        return lbl

    # ── Content builder ───────────────────────────────────────────

    def _build_content(self):
        while self._outer.count():
            item = self._outer.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Hide first. deleteLater() only queues the delete, so the
                # outgoing screen stayed a visible child and painted over the
                # rebuilt one — History rebuilds on every navigation here.
                # hide(), not setParent(None): unparenting would promote it to
                # a top-level window, which shows up as a blank frame.
                widget.hide()
                widget.deleteLater()

        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(18, 8, 18, 10)
        root.setSpacing(8)

        # ── Top bar: title + compact inline summary ───────────────
        topbar = QHBoxLayout()
        title = QLabel(tr("HISTORY"))
        apply_tactical_label(title, font_size=15, letter_spacing=4)
        topbar.addWidget(title)
        topbar.addStretch()
        topbar.addWidget(self._summary_label())
        root.addLayout(topbar)

        # ── Dual workspaces — equal weight ────────────────────────
        # Side by side when there is room, stacked when there is not. Each
        # panel carries a four-column table, and two of them abreast in the
        # 1100px minimum window left the stretch column 15px wide: the TARGET
        # heading and every path under it collapsed to a single letter.
        if self._stacked_layout():
            workspaces = QVBoxLayout()
            workspaces.setSpacing(10)
            workspaces.addWidget(self._build_cleanup_workspace())
            workspaces.addWidget(self._build_sessions_workspace())
        else:
            workspaces = QHBoxLayout()
            workspaces.setSpacing(10)
            workspaces.addWidget(self._build_cleanup_workspace(),
                                 stretch=1, alignment=Qt.AlignTop)
            workspaces.addWidget(self._build_sessions_workspace(),
                                 stretch=1, alignment=Qt.AlignTop)
        self._is_stacked = self._stacked_layout()
        root.addLayout(workspaces, stretch=0)
        root.addStretch(1)

        # Wrap in a scroll area so expanded detail panels never crop content.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("border: none;")
        scroll.setWidget(content)
        self._outer.addWidget(scroll)

    def _summary_label(self) -> QLabel:
        from app.state.session_store import load_summary
        s = load_summary()
        lbl = QLabel(
            tr("{freed} freed  ·  {cleanups} cleanups  ·  {scanned} scanned"
               "  ·  {scans} scans",
               freed=_format_size(s.get('total_recovered_bytes', 0)),
               cleanups=s.get('cleanup_sessions', 0),
               scanned=_format_size(s.get('total_scanned_bytes', 0)),
               scans=s.get('analyze_sessions', 0))
        )
        lbl.setObjectName("Muted")
        lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
        return lbl

    # ── Cleanup workspace ─────────────────────────────────────────

    def _build_cleanup_workspace(self) -> QFrame:
        p = get_palette()
        records = self._cleanup_records
        frame = QFrame()
        frame.setObjectName("Panel")
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        total_freed = sum(r.get("total_bytes_freed", 0) for r in records)
        if records:
            subtitle = tr("// {n} operations · {size} freed",
                          n=len(records), size=_format_size(total_freed))
        else:
            subtitle = tr("// no cleanup operations yet")
        v.addLayout(self._section_header(tr("CLEANUP SESSIONS"), subtitle))

        if not records:
            v.addWidget(self._placeholder(
                tr("NO CLEANUP HISTORY"),
                tr("Use the Findings screen to move items to the Recycle Bin."),
            ))
            return frame

        # Table — WHEN / STATUS / FREED / ITEMS
        table = self._new_table(
            [tr("WHEN"), tr("STATUS"), tr("FREED"), tr("ITEMS")],
            [Qt.AlignLeft, Qt.AlignHCenter, Qt.AlignRight, Qt.AlignRight])
        hdr = table.horizontalHeader()
        for col, w in ((1, 116), (2, 100), (3, 78)):
            hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            table.setColumnWidth(col, w)
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)

        visible_records = records[:self._MAX_VISIBLE_ROWS]
        table.setRowCount(len(visible_records))
        for i, rec in enumerate(visible_records):

            when_item = QTableWidgetItem(_format_when(rec.get("timestamp", 0)))
            when_item.setFlags(when_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(i, 0, when_item)

            table.setCellWidget(i, 1, self._badge_cell(_StatusBadge(rec)))

            freed_item = QTableWidgetItem(_format_size(rec.get("total_bytes_freed", 0)))
            freed_item.setFlags(freed_item.flags() & ~Qt.ItemIsEditable)
            freed_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            freed_item.setForeground(QBrush(QColor(p.get("safe", "#7aa88a"))))
            table.setItem(i, 2, freed_item)

            items = rec.get("items", [])
            count_item = QTableWidgetItem(f"{len(items):,}")
            count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if rec.get("failed_count", 0):
                count_item.setForeground(QBrush(QColor(p.get("risk", "#c67a69"))))
            elif rec.get("in_use_count", 0):
                count_item.setForeground(QBrush(QColor(p.get("review", "#c7a66c"))))
            table.setItem(i, 3, count_item)

        table.cellClicked.connect(self._on_cleanup_cell_clicked)
        self._cleanup_table = table
        self._cap_table_height(table)
        v.addWidget(table)
        self._cleanup_detail_area = self._detail_slot()
        self._cleanup_detail_widget = None
        v.addWidget(self._cleanup_detail_area)
        # Below the detail panel, not above it. Sitting between the table and
        # the panel, this note wedged an unrelated sentence between the row you
        # clicked and the details it opened, so the two stopped reading as
        # connected. It is a footnote about the table's length; it belongs at
        # the end of the section.
        note = self._limited_history_note(len(records), "cleanup records")
        if note:
            v.addWidget(note)
        v.addStretch(1)
        return frame

    # ── Sessions workspace ────────────────────────────────────────

    def _build_sessions_workspace(self) -> QFrame:
        sessions = self._sessions
        frame = QFrame()
        frame.setObjectName("Panel")
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        if sessions:
            total_size = sum(s.get("total_size", 0) for s in sessions)
            subtitle = tr("// {n} sessions · {size} scanned",
                          n=len(sessions), size=_format_size(total_size))
        else:
            subtitle = tr("// no scan sessions yet")
        v.addLayout(self._section_header(tr("ANALYZE SESSIONS"), subtitle))

        if not sessions:
            v.addWidget(self._placeholder(
                tr("NO ANALYZE HISTORY"),
                tr("Run a scan from the Analyze screen to build session history."),
            ))
            return frame

        # Table — WHEN / TARGET / MODE / ITEMS
        table = self._new_table(
            [tr("WHEN"), tr("TARGET"), tr("MODE"), tr("ITEMS")],
            [Qt.AlignLeft, Qt.AlignLeft, Qt.AlignLeft, Qt.AlignRight])
        hdr = table.horizontalHeader()
        # ITEMS holds a formatted count plus an optional "(partial)" tag; give it
        # room so it isn't clipped to "26182 …". TARGET (Stretch) absorbs the space.
        for col, w in ((0, 128), (2, 120), (3, 132)):
            hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            table.setColumnWidth(col, w)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)

        visible_sessions = sessions[:self._MAX_VISIBLE_ROWS]
        table.setRowCount(len(visible_sessions))
        for i, s in enumerate(visible_sessions):

            status = s.get("status", "")
            raw_count = s.get("display_count", s.get("scanned_count", 0))
            try:
                items_str = f"{int(raw_count):,}"
            except (TypeError, ValueError):
                items_str = str(raw_count)
            if status == "stopped":
                items_str += tr(" (partial)")

            for col, val, align in [
                (0, _format_when(s.get("start_time", 0)), Qt.AlignLeft | Qt.AlignVCenter),
                (1, _short_target(s.get("target", "")),   Qt.AlignLeft | Qt.AlignVCenter),
                (2, _scan_mode_label(s.get("scan_mode", "smart")), Qt.AlignLeft | Qt.AlignVCenter),
                (3, items_str, Qt.AlignRight | Qt.AlignVCenter),
            ]:
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setTextAlignment(align)
                if col == 1:
                    item.setToolTip(s.get("target", ""))
                elif col == 3:
                    item.setToolTip(items_str)  # full text on hover if ever clipped
                table.setItem(i, col, item)

        table.cellClicked.connect(self._on_sess_cell_clicked)
        self._sess_table = table
        self._cap_table_height(table)
        v.addWidget(table)
        self._sess_detail_area = self._detail_slot()
        self._sess_detail_widget = None
        v.addWidget(self._sess_detail_area)
        # Below the detail panel, not above it. Sitting between the table and
        # the panel, this note wedged an unrelated sentence between the row you
        # clicked and the details it opened, so the two stopped reading as
        # connected. It is a footnote about the table's length; it belongs at
        # the end of the section.
        note = self._limited_history_note(len(sessions), "scan sessions")
        if note:
            v.addWidget(note)
        v.addStretch(1)
        return frame

    def _detail_slot(self) -> QFrame:
        p = get_palette()
        host = QFrame()
        host.setObjectName("PanelAlt")
        # No committed height. A cleanup record carries a variable number of
        # category rows and a wrapping status note, and a fixed 178px slot cut
        # the metrics eyebrows off the top and the last rows off the bottom —
        # the panel needs 195-310px depending on the record. The screen is
        # inside a QScrollArea, so growing is free.
        host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        # The table and this panel both sat on panel_alt, so the details had
        # exactly the weight of the data they annotate. tint_bg sits below the
        # workspace surface rather than above it, which reads as an inset well
        # — subordinate to the table, still clearly its own region. The border
        # softens to match; it no longer has to do the separating on its own.
        host.setStyleSheet(
            f"QFrame#PanelAlt {{ background: {p.get('tint_bg', '#101a15')}; "
            f"border: 1px solid {_rgba(p.get('border', '#213028'), 0.75)}; }}"
        )
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        host.setVisible(False)
        return host

    def _set_detail_widget(self, host: QFrame | None, current_widget: QWidget | None, widget: QWidget) -> QWidget:
        if host is None:
            return current_widget
        layout = host.layout()
        if current_widget is not None:
            layout.removeWidget(current_widget)
            current_widget.hide()   # removeWidget alone leaves it drawn
            current_widget.deleteLater()
        layout.addWidget(widget)
        host.setVisible(True)
        return widget

    def _clear_detail_widget(self, host: QFrame | None, current_widget: QWidget | None) -> None:
        if host is None:
            return
        layout = host.layout()
        if current_widget is not None:
            layout.removeWidget(current_widget)
            current_widget.hide()   # removeWidget alone leaves it drawn
            current_widget.deleteLater()
        host.setVisible(False)

    def _badge_cell(self, badge: QWidget) -> QWidget:
        """Center a badge widget within its table cell."""
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        # Let mouse events fall through to the viewport so _RowHoverFilter
        # can detect the row correctly even when the cursor is over the badge.
        holder.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        badge.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addStretch()
        lay.addWidget(badge, alignment=Qt.AlignCenter)
        lay.addStretch()
        return holder

    # ── Cleanup row interactions ──────────────────────────────────

    def _on_cleanup_cell_clicked(self, row: int, col: int):
        self._toggle_cleanup_detail(row)

    def _toggle_cleanup_detail(self, row: int):
        if row == self._cleanup_expanded_row:
            self._cleanup_expanded_row = -1
            if self._cleanup_table:
                self._cleanup_table.clearSelection()
            self._clear_detail_widget(self._cleanup_detail_area, self._cleanup_detail_widget)
            self._cleanup_detail_widget = None
            return

        self._cleanup_expanded_row = row
        if self._cleanup_table:
            self._cleanup_table.selectRow(row)
        if row < len(self._cleanup_records) and self._cleanup_detail_area:
            record = self._cleanup_records[row]
            widget = CleanupRecordDetail(record)
            widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            self._cleanup_detail_widget = self._set_detail_widget(
                self._cleanup_detail_area,
                self._cleanup_detail_widget,
                widget,
            )
        self._sync_detail_heights()

    # ── Session row interactions ──────────────────────────────────

    def _on_sess_cell_clicked(self, row: int, col: int):
        self._toggle_sess_detail(row)

    def _toggle_sess_detail(self, row: int):
        if row == self._sess_expanded_row:
            self._sess_expanded_row = -1
            if self._sess_table:
                self._sess_table.clearSelection()
            self._clear_detail_widget(self._sess_detail_area, self._sess_detail_widget)
            self._sess_detail_widget = None
            return

        self._sess_expanded_row = row
        if self._sess_table:
            self._sess_table.selectRow(row)
        if row < len(self._sessions) and self._sess_detail_area:
            record = self._sessions[row]
            sid = record.get("session_id", "")
            widget = SessionDetail(
                record,
                on_open=lambda: self._open_findings(sid),
                on_rerun=lambda: self.rerun_requested.emit(record.get("target", "")),
                on_delete=lambda: self._delete_session(sid),
            )
            widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            self._sess_detail_widget = self._set_detail_widget(
                self._sess_detail_area,
                self._sess_detail_widget,
                widget,
            )
        self._sync_detail_heights()

    def _sync_detail_heights(self):
        """Both open panels take the taller one's height.

        Side by side, two explanation panels of different heights leave a
        ragged bottom edge and read as one being unfinished — the cleanup
        panel runs to ~300px and the scan panel to ~245px purely because of
        how many category rows each happens to have. Matching them is only
        meaningful when they sit next to each other; stacked, it would add
        dead space under the shorter one for no gain.
        """
        # Keyed on "has a detail widget", not isVisible(): _build_content()
        # adds the new content widget to the layout but nothing is shown until
        # the event loop runs, so both panels still reported isVisible() False
        # at the moment the row was expanded and the sync did nothing.
        pairs = ((self._cleanup_detail_area, self._cleanup_detail_widget),
                 (self._sess_detail_area, self._sess_detail_widget))
        areas = [area for area, widget in pairs
                 if area is not None and widget is not None]
        for area in areas:
            area.setMinimumHeight(0)
        if self._is_stacked or len(areas) < 2:
            return
        for area in areas:
            if area.layout() is not None:
                area.layout().activate()
        tallest = max(area.sizeHint().height() for area in areas)
        for area in areas:
            area.setMinimumHeight(tallest)

    # ── Actions ───────────────────────────────────────────────────

    def _open_findings(self, session_id: str):
        from app.state.session_store import load_session_by_id
        from app.widgets.progress import run_busy
        # Off the UI thread: a completed drive scan is a multi-hundred-MB
        # snapshot, and reading it inline froze the window (see BusyDialog).
        data = run_busy(self, tr("Opening session…"),
                        lambda: load_session_by_id(session_id))
        if not data:
            QMessageBox.warning(self, tr("Not found"), tr("Full session data is unavailable."))
            return
        self.open_session_requested.emit(data)

    def _delete_session(self, session_id: str):
        reply = QMessageBox.question(
            self,
            tr("Delete session"),
            tr("Remove this session from history? This cannot be undone."),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        from app.state.session_store import delete_session_from_history
        delete_session_from_history(session_id)
        self.refresh()
