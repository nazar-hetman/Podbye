"""FindingsTableModel — QAbstractTableModel for semantic entity display.

Provides:
- FindingsTableModel: model backed by entity dict list; tracks checkbox state
  at the source level so filtering never loses selections.
- FindingsFilterProxy: filter + sort proxy (search text, risk filter, sort key).
- FindingsDelegate: custom painting for the risk-badge column and checkbox
  toggle via editorEvent.
"""
from __future__ import annotations

import re

from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel,
    Qt, QSize, QEvent,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QStyledItemDelegate, QStyle

from app.models.risk import normalize_risk, risk_sort_index, risk_colors as _risk_colors
from app.themes.theme_manager import get_palette
from app.i18n import tr, get_language

# ── Column layout ──────────────────────────────────────────────────

COL_CHECK  = 0
COL_EXPAND = 1
COL_NAME   = 2
COL_SIZE   = 3
COL_ITEMS  = 4
COL_RISK   = 5
COL_AI     = 6
COL_AGE    = 7

_HEADER_KEYS = ("SELECT", "", "NAME", "SIZE", "ITEMS", "STATUS", "AI", "AGE")
_headers_cache: tuple[str, tuple[str, ...]] | None = None


def _headers():
    """Translated column headers, cached per active language.

    Qt calls columnCount()/headerData() constantly while laying out and painting
    the table, and each miss re-ran eight tr() lookups (a dict hit plus str
    formatting) to rebuild an identical list. Caching keyed on the active
    language keeps the live language switch working — _build_ui recreates the
    table after set_language(), and the next call rebuilds on the key change.
    """
    global _headers_cache
    lang = get_language()
    if _headers_cache is None or _headers_cache[0] != lang:
        _headers_cache = (
            lang,
            tuple(tr(k) if k else "" for k in _HEADER_KEYS),
        )
    return _headers_cache[1]

# ── Lookup tables ──────────────────────────────────────────────────

# U+25D4, not U+25D0: neither bundled font carries the half-filled circle, so
# every in-progress row showed a .notdef box where its status should be.
_AI_SYMBOL = {
    "ready": "✓", "done": "✓",
    "pending": "◔", "analyzing": "◔",
    "failed": "✗", "error": "✗",
    "none": "—", "disabled": "⊘",
}

# Risk ordering comes from the canonical app.models.risk.risk_sort_index —
# this module used to keep its own reversed copy.

# "Safe cleanup" sort: least deliberation first. Deliberately NOT
# risk_sort_index, which orders by severity for the Status sort — here
# Protected must sink to the bottom because it cannot be cleaned at all,
# whereas Status puts it first as the thing most worth knowing about.
_SAFE_CLEANUP_ORDER = {"Safe": 0, "Optional": 1, "Review": 2, "Protected": 3}

# Risk badge colours come from the canonical app.models.risk.risk_colors
# (imported above as _risk_colors) so every screen agrees.


# ── Source model ───────────────────────────────────────────────────

class FindingsTableModel(QAbstractTableModel):
    """Flat entity list model with source-level checkbox tracking.

    Checkbox state is stored in ``_checked`` as a set of *source* row indices,
    so filtering via the proxy never discards checked items.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entities: list = []
        self._checked: set = set()
        self._row_by_path: dict[str, list[int]] = {}

    # ── Data management ───────────────────────────────────────────

    def _reindex(self):
        """Rebuild the path → rows lookup used by update_entity_by_path.

        A list per path, not a single row: one folder legitimately produces
        several entities when its loose files are split by content type, so
        C:/Users/<u>/Downloads appears as an archive_group, a document_folder,
        a media_collection and a mixed_folder — four rows, one path. Keyed
        one-to-one, three of those were unreachable, and a streamed AI
        explanation landed on whichever happened to win the dict.
        """
        index: dict[str, list[int]] = {}
        for row, e in enumerate(self._entities):
            path = e.get("path", "")
            if path:
                index.setdefault(path, []).append(row)
        self._row_by_path = index

    def set_entities(self, entities: list):
        self.beginResetModel()
        self._entities = list(entities)
        self._checked.clear()
        self._reindex()
        self.endResetModel()

    def get_entity(self, source_row: int):
        if 0 <= source_row < len(self._entities):
            return self._entities[source_row]
        return None

    def row_for_entity(self, entity: dict) -> int:
        """Source row holding *this exact* entity object, or -1.

        Identity, not path: several rows legitimately share one path (a
        folder's loose files split by content type), so a path lookup would
        return the wrong sibling.
        """
        for row, e in enumerate(self._entities):
            if e is entity:
                return row
        return -1

    def remove_cleaned(self, succeeded_paths) -> int:
        """Drop or shrink rows whose files were just recycled.

        An entity is removed when its own path was cleaned, or when every file
        it represented (``removable_file_paths``) is gone. A bucket that was
        only *partially* cleaned keeps its survivors but drops the recycled
        files (and updates ``file_count``) so the already-deleted files don't
        reappear when it is reopened. Returns the count of fully-removed rows.
        Uses a model reset (same as set_entities) so the proxy/table stay
        consistent.
        """
        if not succeeded_paths:
            return 0
        norm = {p.replace("\\", "/").lower().rstrip("/") for p in succeeded_paths}
        full = {p.replace("\\", "/").lower() for p in succeeded_paths}
        survivors = []
        changed = False
        for e in self._entities:
            ep = (e.get("path", "") or "").replace("\\", "/").lower().rstrip("/")
            if ep in norm:
                changed = True
                continue
            rfp = [p for p in (e.get("removable_file_paths") or []) if p]
            if rfp:
                remaining = [p for p in rfp if p.replace("\\", "/").lower() not in full]
                if not remaining:
                    changed = True
                    continue  # every file this entity represented is gone
                if len(remaining) != len(rfp):
                    # Partial cleanup: keep the bucket but drop the cleaned files
                    # so they don't show up again when the bucket is reopened.
                    e = dict(e)
                    e["removable_file_paths"] = remaining
                    if "file_count" in e:
                        e["file_count"] = len(remaining)
                    changed = True
            survivors.append(e)
        if not changed:
            return 0
        removed = len(self._entities) - len(survivors)
        self.beginResetModel()
        self._entities = survivors
        self._checked.clear()
        self._reindex()
        self.endResetModel()
        return removed

    def update_entity_by_path(self, entity: dict) -> int:
        """Replace the row(s) for *entity*'s path in place. Returns the first
        row updated, or -1.

        Indexed rather than scanned: AI explanations stream back one signal per
        entity, so a linear search here made refreshing a full result set
        quadratic in the number of entities.

        Where one path has several rows (see _reindex), only the row of the
        same entity_type is replaced — the content-split siblings describe
        different things and must not inherit each other's explanation.
        """
        path = entity.get("path", "")
        if not path:
            return -1
        rows = self._row_by_path.get(path) or []
        etype = entity.get("entity_type")
        first = -1
        for row in rows:
            if row >= len(self._entities):
                continue
            if len(rows) > 1 and self._entities[row].get("entity_type") != etype:
                continue
            self._entities[row] = entity
            self.dataChanged.emit(self.index(row, 0),
                                  self.index(row, self.columnCount() - 1), [])
            if first < 0:
                first = row
        return first

    # ── QAbstractTableModel overrides ──────────────────────────────

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._entities)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_headers())

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            h = _headers()
            return h[section] if section < len(h) else None
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == COL_CHECK:
            f |= Qt.ItemIsUserCheckable
        return f

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        col = index.column()
        if row >= len(self._entities):
            return None
        entity = self._entities[row]

        if role == Qt.CheckStateRole:
            if col == COL_CHECK:
                return Qt.Checked if row in self._checked else Qt.Unchecked
            return None

        if role == Qt.DisplayRole:
            if col == COL_EXPAND:
                return ""
            if col == COL_NAME:
                return entity.get("name", entity.get("path", "Unknown"))
            if col == COL_SIZE:
                return entity.get("size", "—")
            if col == COL_ITEMS:
                total = entity.get("file_count", 0) + entity.get("folder_count", 0)
                return f"{total:,}"
            if col == COL_RISK:
                return normalize_risk(entity.get("risk", "Review"))
            if col == COL_AI:
                return _AI_SYMBOL.get(entity.get("ai_status", "none"), "—")
            if col == COL_AGE:
                age = entity.get("age", "")
                return age if age and age != "0d" else "—"
            return None

        if role == Qt.UserRole:
            return entity

        if role == Qt.ToolTipRole and col == COL_NAME:
            return entity.get("path", "")

        if role == Qt.TextAlignmentRole:
            if col in (COL_SIZE, COL_ITEMS, COL_AGE):
                return Qt.AlignRight | Qt.AlignVCenter
            if col in (COL_EXPAND, COL_AI):
                return Qt.AlignCenter | Qt.AlignVCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return None

    def setData(self, index, value, role=Qt.EditRole) -> bool:
        if not index.isValid():
            return False
        if role == Qt.CheckStateRole and index.column() == COL_CHECK:
            row = index.row()
            if value == Qt.Checked:
                self._checked.add(row)
            else:
                self._checked.discard(row)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
        return False

    # ── Selection helpers ─────────────────────────────────────────

    def is_checked(self, row: int) -> bool:
        return row in self._checked

    def set_checked_rows(self, rows, checked: bool = True):
        """Bulk check/uncheck source rows with a single dataChanged emit.

        Used by "Select all" — avoids one signal per row so the view can sync
        in one pass instead of N.
        """
        changed = False
        for row in rows:
            if not (0 <= row < len(self._entities)):
                continue
            if checked and row not in self._checked:
                self._checked.add(row)
                changed = True
            elif not checked and row in self._checked:
                self._checked.discard(row)
                changed = True
        if changed and self._entities:
            top = self.index(0, COL_CHECK)
            bottom = self.index(len(self._entities) - 1, COL_CHECK)
            self.dataChanged.emit(top, bottom, [Qt.CheckStateRole])

    def checked_entities(self) -> list:
        return [
            self._entities[i]
            for i in sorted(self._checked)
            if i < len(self._entities)
        ]

    def checked_size(self) -> int:
        return sum(
            self._entities[i].get("size_bytes", 0)
            for i in self._checked
            if i < len(self._entities)
        )

    def clear_checked(self):
        if not self._checked:
            return
        self._checked.clear()
        if self._entities:
            top = self.index(0, COL_CHECK)
            bottom = self.index(len(self._entities) - 1, COL_CHECK)
            self.dataChanged.emit(top, bottom, [Qt.CheckStateRole])


# ── Filter + sort proxy ───────────────────────────────────────────

class FindingsFilterProxy(QSortFilterProxyModel):
    """Combines search-text filter, risk-level filter, and custom sort.

    SORT_KEYS is the single source of truth for what the Findings sort
    dropdown offers — the screen builds the combo from it. It used to keep its
    own separate list, and the two drifted: the dropdown advertised "Safe
    cleanup" and "Last accessed", neither of which lessThan() had a branch
    for, so both silently fell through to size-descending and behaved exactly
    like "Largest first". Meanwhile the "oldest" sort that *was* implemented
    was never offered. Every key here must have a branch in lessThan(), and
    test_sorting_semantics proves it.
    """

    SORT_KEYS: list[tuple[str, str]] = [
        ("largest",      "Largest first"),
        ("smallest",     "Smallest first"),
        ("reclaimable",  "Reclaimable size"),
        ("safe_cleanup", "Safe cleanup first"),
        ("risk",         "Status"),
        ("last_access",  "Least recently used"),
        ("oldest",       "Oldest first"),
        ("ai_analyzed",  "AI analyzed"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_text = ""
        # How far the current query has to reach to find anything. Recomputed
        # with the query, not per row — see _narrowest_tier.
        self._search_tier = 2
        self._risk_filter: set | None = None  # None = show all
        self._sort_key = "largest"

    def set_search(self, text: str):
        self._search_text = text.strip().lower()
        self._search_tier = self._narrowest_tier()
        self.invalidateFilter()

    # How hard the filter has to look. A query is answered from the narrowest
    # of these that answers it at all, so a name never competes with a path.
    _TIER_WORD = 0        # the query starts a word in the name
    _TIER_NAME = 1        # the query is anywhere in the name
    _TIER_BROAD = 2       # path, category, duplicate locations, child names

    @staticmethod
    def _name_words(name: str) -> list:
        return [w for w in re.split(r"[^a-z0-9]+", (name or "").lower()) if w]

    def _narrowest_tier(self) -> int:
        """One pass over the source to see how far the query has to reach.

        Cheap: this model holds the entities, ~1,200 of them after a full C:/
        scan, not the 1.8M findings behind them. And it runs once per keystroke,
        not once per row.
        """
        if not self._search_text:
            return self._TIER_BROAD
        model = self.sourceModel()
        if model is None:
            return self._TIER_BROAD
        query = self._search_text
        found_in_name = False
        for row in range(model.rowCount()):
            entity = model.data(model.index(row, COL_NAME), Qt.UserRole)
            if not entity:
                continue
            name = entity.get("name", "").lower()
            if any(word.startswith(query) for word in self._name_words(name)):
                return self._TIER_WORD
            if query in name:
                found_in_name = True
        return self._TIER_NAME if found_in_name else self._TIER_BROAD

    def set_risk_filter(self, risks: set | None):
        self._risk_filter = risks
        self.invalidateFilter()

    def set_sort_key(self, key: str):
        self._sort_key = key
        self.invalidate()

    def sort_key(self) -> str:
        """The active sort. The Findings list needs it to rank app groups by
        their aggregate rather than by their largest single member."""
        return self._sort_key

    # ── QSortFilterProxyModel overrides ───────────────────────────

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        model = self.sourceModel()
        entity = model.data(model.index(source_row, COL_NAME), Qt.UserRole)
        if entity is None:
            return True

        if self._risk_filter is not None:
            if normalize_risk(entity.get("risk", "Review")) not in self._risk_filter:
                return False

        if self._search_text and not self._matches_search(entity):
            return False

        return True

    def _matches_search(self, entity: dict) -> bool:
        """Name first, and only then everything else.

        Everything used to be one concatenated haystack — name, path, category,
        every duplicate location and every sampled child name — searched by
        plain substring. Measured against a real 706-entity C:/ scan, typing
        "d" matched 700 of them, because nearly every *path* contains a d:
        AppData, ProgramData, Windows, Downloads. "di" still matched 204. You
        had to reach "disc" before Discord stopped being buried, which is what
        was reported.

        Names are what people type, so the query is answered from the narrowest
        place that answers it at all: words in a name, then anywhere in a name,
        then everything else. "di" reaches the first tier and returns the nine
        rows whose names begin a word with it, rather than the 204 that contain
        those letters somewhere — NVI*di*A, Stu*di*o, and every path with a
        "di" in it. Nothing is unreachable: a query only found in a path, like
        "appdata" or "steamapps", falls through to the last tier and still
        works.

        The fields are also joined with a separator now. Concatenated raw, a
        query could match across a boundary that does not exist — a name ending
        in "npm" followed by a path beginning "c:/" matched "npmc".
        """
        query = self._search_text
        name = entity.get("name", "").lower()
        if self._search_tier == self._TIER_WORD:
            return any(word.startswith(query) for word in self._name_words(name))
        if self._search_tier == self._TIER_NAME:
            return query in name

        parts = [entity.get("path", ""), entity.get("category", "")]
        for loc in entity.get("duplicate_locations", []) or []:
            parts.append(loc.get("path", "") if isinstance(loc, dict)
                         else str(loc))
        parts.extend(str(path) for path in entity.get("children_sample", []) or [])
        return query in "\n".join(p.lower() for p in parts if p)

    def lessThan(self, left, right) -> bool:
        le = self.sourceModel().data(left, Qt.UserRole)
        re = self.sourceModel().data(right, Qt.UserRole)
        if le is None or re is None:
            return False

        k = self._sort_key
        if k == "largest":
            return le.get("size_bytes", 0) > re.get("size_bytes", 0)
        if k == "smallest":
            return le.get("size_bytes", 0) < re.get("size_bytes", 0)
        if k == "risk":
            lo = risk_sort_index(le.get("risk"))
            ro = risk_sort_index(re.get("risk"))
            if lo != ro:
                return lo < ro
            return le.get("size_bytes", 0) > re.get("size_bytes", 0)
        if k == "ai_analyzed":
            la = le.get("ai_status", "none") in ("ready", "done")
            ra = re.get("ai_status", "none") in ("ready", "done")
            if la != ra:
                return la > ra
            return le.get("size_bytes", 0) > re.get("size_bytes", 0)
        if k == "reclaimable":
            lr = le.get("reclaimable_bytes", 0)
            rr = re.get("reclaimable_bytes", 0)
            if lr != rr:
                return lr > rr
            return le.get("size_bytes", 0) > re.get("size_bytes", 0)
        if k == "safe_cleanup":
            # What can go with the least thought, biggest win first. Safe and
            # Optional lead; Review follows; Protected sinks to the bottom
            # because it cannot be cleaned at all.
            lo = _SAFE_CLEANUP_ORDER.get(normalize_risk(le.get("risk")), 9)
            ro = _SAFE_CLEANUP_ORDER.get(normalize_risk(re.get("risk")), 9)
            if lo != ro:
                return lo < ro
            return le.get("reclaimable_bytes", 0) > re.get("reclaimable_bytes", 0)
        if k == "last_access":
            # Least recently *used* first — the strongest "you don't need
            # this" signal there is. Items with no atime sort last rather
            # than pretending to be ancient.
            la = le.get("accessed") or float("inf")
            ra = re.get("accessed") or float("inf")
            if la != ra:
                return la < ra
            return le.get("size_bytes", 0) > re.get("size_bytes", 0)
        if k == "oldest":
            # Oldest *modified* first (smallest mtime = oldest).
            lm = le.get("modified") or float("inf")
            rm = re.get("modified") or float("inf")
            if lm != rm:
                return lm < rm
            return le.get("size_bytes", 0) > re.get("size_bytes", 0)
        return le.get("size_bytes", 0) > re.get("size_bytes", 0)


# ── Item delegate ─────────────────────────────────────────────────

class FindingsDelegate(QStyledItemDelegate):
    """Paints the risk badge and handles checkbox toggle via editorEvent."""

    def paint(self, painter, option, index):
        col = index.column()
        p = get_palette()
        hovered = bool(option.state & QStyle.State_MouseOver)
        selected = bool(option.state & QStyle.State_Selected)
        # Flat row fills — calmer than the old left-to-right gradients.
        row_bg = None
        if selected:
            row_bg = p.get("accent_soft", "#1a2e22")
        elif hovered:
            row_bg = p.get("tint_bg", "#0f1914")

        if row_bg is not None:
            painter.save()
            painter.fillRect(option.rect, QColor(row_bg))
            painter.restore()

        if col == COL_CHECK:
            painter.save()
            rect = option.rect.adjusted(12, 9, -12, -9)
            checked = int(index.data(Qt.CheckStateRole) or 0) != 0
            border = QColor(p.get("border_hover" if hovered or selected else "border_alt", "#2b3d33"))
            fill = QColor(p.get("accent", "#7cc596")) if checked else QColor(p.get("panel_alt", "#18241e"))
            painter.setPen(border)
            painter.setBrush(fill)
            painter.drawRect(rect)
            if checked:
                painter.setPen(QColor(p.get("bg_deep", "#080d0a")))
                font = QFont("JetBrains Mono", 8)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(rect, Qt.AlignCenter, "✓")
            painter.restore()
            return

        if col == COL_RISK:
            painter.save()
            entity = index.data(Qt.UserRole) or {}
            risk = index.data(Qt.DisplayRole) or "Review"
            bg_hex, border_hex = _risk_colors(risk)

            r = option.rect
            badge = r.adjusted(6, 5, -6, -5)
            painter.setBrush(QColor(bg_hex))
            painter.setPen(QColor(border_hex))
            painter.drawRect(badge)

            f = QFont("Silkscreen", 7)
            painter.setFont(f)
            painter.setPen(QColor(border_hex))
            painter.drawText(badge, Qt.AlignCenter, risk.upper())

            # Cloud-sync indicator in the top-right of the badge. U+21E7, not
            # U+2601: no font on the box has the cloud, not even Segoe UI
            # Symbol, so it drew as a .notdef box on every synced row.
            if entity.get("cloud_sync_provider"):
                painter.setFont(QFont("Segoe UI", 7))
                painter.setPen(QColor(get_palette().get("optional", "#6e93a8")))
                painter.drawText(badge.adjusted(0, 0, -2, 0), Qt.AlignRight | Qt.AlignTop, "⇧")

            painter.restore()
            return

        super().paint(painter, option, index)

    def editorEvent(self, event, model, option, index):
        """Toggle checkbox when the check column is clicked."""
        if index.column() == COL_CHECK:
            if event.type() == QEvent.MouseButtonRelease:
                current = model.data(index, Qt.CheckStateRole)
                new_state = Qt.Unchecked if int(current) != 0 else Qt.Checked
                model.setData(index, new_state, Qt.CheckStateRole)
                return True
            if event.type() == QEvent.MouseButtonPress:
                return True  # eat press to avoid selection highlight flicker
        return super().editorEvent(event, model, option, index)

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 34)
