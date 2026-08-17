"""Quick Cleanup screen — real browser/temp/cache detection and Recycle Bin cleanup."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from app.models.finding import _format_size, split_size
from app.widgets.controls import TacticalCheckBox
from app.services.cleanup_engine import CleanupWorker
from app.services.cleanup_result_classifier import (
    STATE_FAILED,
    STATE_IN_USE,
    STATE_PARTIAL,
    CleanupAssessment,
    assess_cleanup_counts,
)
from app.services.quick_cleanup_detector import QuickCleanupCategory, QuickCleanupDetector
from app.themes.theme_manager import get_palette, theme_signaller
from app.widgets.panels import Panel, SectionHeader, apply_tactical_label
from app.widgets.pills import Badge
from app.i18n import tr


_IDLE     = "idle"
_SCANNING = "scanning"
_READY    = "ready"
_CLEANING = "cleaning"
_DONE     = "done"


# ── Static explanations (one per category key) ────────────────────

_EXPLANATIONS: dict[str, str] = {
    "user_temp": (
        "Temporary files created by Windows and applications during normal use — "
        "partial downloads, installer scratch space, and app buffers. "
        "These are never cleared automatically, but any file that is still actively "
        "in use will simply fail to move and stay in place. Everything else is "
        "safe to send to the Recycle Bin."
    ),
    "windows_temp": (
        "The system-wide Temp folder used by Windows services, background tasks, "
        "and installers. Clearing it frees space left behind after updates and "
        "software installs. Files locked by a running process are automatically "
        "skipped — no active operations are interrupted."
    ),
    "browser_cache": (
        "Web assets your browser downloaded and stored locally so pages load faster "
        "on repeat visits. Clearing the cache does not affect bookmarks, saved "
        "passwords, cookies, or browsing history. Your browser will simply "
        "re-download assets as you browse, and the cache will rebuild over time."
    ),
    "thumbnail_cache": (
        "Preview images Windows Explorer generates the first time you open a folder "
        "containing pictures or videos. These database files consume space but hold "
        "no original content — deleting them causes no data loss. Thumbnails are "
        "rebuilt automatically the next time you browse those folders."
    ),
    "windows_update": (
        "Installer packages downloaded by Windows Update and kept on disk so updates "
        "can be rolled back if something goes wrong. Once your system is stable and "
        "up to date these files are redundant. Removing them is a standard "
        "maintenance step and is safe to do at any time."
    ),
}

_EXPLANATION_FALLBACK = (
    "These files are safe to remove. They will be sent to the Recycle Bin and "
    "can be fully restored if needed."
)



# ── Category row ──────────────────────────────────────────────────


# The hand-painted copy that used to live here is now shared — Settings and
# the dialogs were drawing a different checkbox entirely.
_SelectionCheckBox = TacticalCheckBox


class _CategoryRow(QFrame):
    clicked = Signal()
    _VALUE_WIDTH = 88
    _STATUS_WIDTH = 132

    def __init__(self, cat: QuickCleanupCategory, on_toggle, parent=None):
        super().__init__(parent)
        self._cat = cat
        self._expanded = False
        self._hovered = False
        self._focused = False
        self._assessment: CleanupAssessment | None = None
        self.setObjectName("CategoryRow")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(52)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 12, 8)
        lay.setSpacing(10)

        self._check = _SelectionCheckBox()
        self._check.setChecked(True)
        self._check.toggled.connect(on_toggle)
        self._check.toggled.connect(self._apply_state_style)
        self._check.setCursor(Qt.ArrowCursor)
        lay.addWidget(self._check)

        info = QVBoxLayout()
        info.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)

        # The detector's labels are English keys; the locale files carry
        # them. Without tr() the category list was the only English left
        # on an otherwise translated screen.
        self._name_lbl = QLabel(tr(cat.label))
        self._name_lbl.setStyleSheet("font-size: 13px; font-weight: bold;")
        title_row.addWidget(self._name_lbl)

        self._items_lbl = QLabel(tr("{n:,} items", n=cat.file_count))
        self._items_lbl.setObjectName("Muted")
        self._items_lbl.setStyleSheet("font-size: 11px;")
        title_row.addWidget(self._items_lbl)

        title_row.addStretch()
        info.addLayout(title_row)

        self._sub_lbl = QLabel(cat.subtitle)
        self._sub_lbl.setObjectName("Muted")
        self._sub_lbl.setStyleSheet("font-size: 10px; font-family: 'JetBrains Mono';")
        self._sub_lbl.setWordWrap(False)
        info.addWidget(self._sub_lbl)

        lay.addLayout(info, stretch=1)

        self._size_lbl = QLabel(cat.size_display)
        self._size_lbl.setStyleSheet(
            "font-size: 13px; font-family: 'JetBrains Mono'; font-weight: bold;"
        )
        self._size_lbl.setFixedWidth(self._VALUE_WIDTH)
        self._size_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._size_lbl.setWordWrap(False)
        lay.addWidget(self._size_lbl)

        self._expand_icon = QLabel("▸")
        self._expand_icon.setObjectName("Dim")
        self._expand_icon.setStyleSheet("font-size: 10px;")
        self._expand_icon.setFixedWidth(12)
        self._expand_icon.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._expand_icon)

        self._apply_state_style()

    def _set_metric_text(self, text: str, status_mode: bool = False):
        self._size_lbl.setText(text)
        self._size_lbl.setFixedWidth(
            self._STATUS_WIDTH if status_mode else self._VALUE_WIDTH
        )

    def mousePressEvent(self, event):
        self.setFocus(Qt.MouseFocusReason)
        # Only emit click if the checkbox wasn't hit
        checkbox_rect = self._check.geometry()
        if not checkbox_rect.contains(event.position().toPoint()):
            self.clicked.emit()
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self._apply_state_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._apply_state_style()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self._focused = True
        self._apply_state_style()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._focused = False
        self._apply_state_style()
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def set_expanded(self, expanded: bool):
        self._expanded = expanded
        self._expand_icon.setText("▾" if expanded else "▸")
        self._apply_state_style()

    @property
    def is_checked(self) -> bool:
        return self._check.isChecked()

    @property
    def category(self) -> QuickCleanupCategory:
        return self._cat

    @property
    def assessment(self) -> CleanupAssessment | None:
        return self._assessment

    def set_interactive(self, enabled: bool):
        self._check.setEnabled(enabled)
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)

    def _apply_state_style(self):
        p = get_palette()
        bg = p.get("panel", "#141d18")
        hover_bg = p.get("tint_bg", "#0f1914")
        active_bg = p.get("panel_alt", "#18241e")
        border = p.get("border", "#213028")
        border_alt = p.get("border_alt", "#2b3d33")
        text = p.get("text", "#d6e2da")
        dim = p.get("text_dim", "#8a9b8f")
        faint = p.get("text_faint", "#57685e")

        row_bg = bg
        row_border = border

        if self._expanded:
            row_bg = active_bg
            row_border = border_alt
        elif self._hovered:
            row_bg = hover_bg
        elif self._focused:
            row_border = border_alt

        # Selected/expanded rows get an accent left edge for clear readability.
        edge = p.get("accent", "#7cc596") if self._expanded else row_border
        self.setStyleSheet(
            f"QFrame#CategoryRow {{"
            f"background: {row_bg};"
            f"border: 1px solid {row_border};"
            f"border-left: 2px solid {edge};"
            f"border-radius: 2px;"
            f"}}"
        )
        self._expand_icon.setStyleSheet(f"font-size: 10px; color: {dim};")
        self._name_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {text};"
        )
        self._items_lbl.setStyleSheet(f"font-size: 11px; color: {dim};")
        self._sub_lbl.setStyleSheet(
            f"font-size: 10px; font-family: 'JetBrains Mono'; color: {faint};"
        )
        self._check.update()

    def show_result(self, assessment: CleanupAssessment):
        p = get_palette()
        safe   = p.get("safe", "#7cc596")
        warn   = p.get("review", "#d8b46a")
        danger = p.get("risk", "#d68a78")
        dim    = p.get("text_dim", "#8a9b8f")
        self._assessment = assessment

        if assessment.state not in (STATE_PARTIAL, STATE_IN_USE, STATE_FAILED):
            status_color = safe
        elif assessment.state == STATE_FAILED:
            status_color = danger
        else:
            status_color = warn

        self._set_metric_text(assessment.short_label, status_mode=True)
        self._size_lbl.setStyleSheet(
            f"font-size: 11px; font-family: 'JetBrains Mono'; "
            f"font-weight: bold; color: {status_color};"
        )
        self._items_lbl.setText(assessment.item_label)
        self._items_lbl.setStyleSheet(f"font-size: 11px; color: {status_color};")

        if assessment.state == STATE_FAILED:
            self._name_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {danger};")
        elif assessment.state in (STATE_PARTIAL, STATE_IN_USE):
            self._name_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {warn};")
        else:
            self._name_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {dim};")


# ── Screen ────────────────────────────────────────────────────────

class QuickCleanupScreen(QWidget):
    navigate_to = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = _IDLE
        self._rows: list[_CategoryRow] = []
        self._sep_frames: list[QFrame] = []
        self._expanded_index: int = -1
        self._detector: QuickCleanupDetector | None = None
        self._worker: CleanupWorker | None = None
        self._settings_store = None
        self._cleanup_start_time: float = 0.0
        self._cleaning_rows: list[_CategoryRow] = []
        self._theme_checks: list[QLabel] = []
        self._row_assessments: dict[str, CleanupAssessment] = {}

        self._build_ui()
        theme_signaller().theme_changed.connect(self._on_theme_changed)

    def set_settings_store(self, store):
        self._settings_store = store

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("border: none;")

        content = QWidget()
        main_lay = QVBoxLayout(content)
        main_lay.setContentsMargins(22, 16, 22, 22)
        main_lay.setSpacing(12)

        # ── Header ────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(14)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel(tr("QUICK CLEANUP"))
        apply_tactical_label(title, font_size=16, letter_spacing=4)
        title_col.addWidget(title)
        self._subtitle_lbl = QLabel(tr("scanning for safe categories…"))
        self._subtitle_lbl.setObjectName("Dim")
        self._subtitle_lbl.setStyleSheet("font-size: 12px;")
        title_col.addWidget(self._subtitle_lbl)
        header.addLayout(title_col, stretch=1)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        self._sel_badge = Badge(tr("0 selected"), "info")
        # Minimum, not fixed: 136px was measured against "5 of 5 selected",
        # and "5 SUR 5 SÉLECTIONNÉS" needs 160. The minimum still keeps the
        # header from jittering as the count changes.
        self._sel_badge.setMinimumWidth(136)
        self._sel_badge.setAlignment(Qt.AlignCenter)
        self._sel_badge.setVisible(False)
        actions.addWidget(self._sel_badge)

        self._btn_rescan = QPushButton(tr("↻ Scan"))
        self._btn_rescan.setCursor(Qt.PointingHandCursor)
        self._btn_rescan.setEnabled(False)
        self._btn_rescan.setMinimumWidth(88)
        self._btn_rescan.clicked.connect(self._start_scan)
        actions.addWidget(self._btn_rescan)

        self._btn_clean = QPushButton(tr("Scanning…"))
        self._btn_clean.setObjectName("Primary")
        self._btn_clean.setEnabled(False)
        self._btn_clean.setCursor(Qt.PointingHandCursor)
        self._btn_clean.setMinimumWidth(132)
        self._btn_clean.clicked.connect(self._on_clean_clicked)
        actions.addWidget(self._btn_clean)

        header.addLayout(actions)

        main_lay.addLayout(header)

        # ── Body ──────────────────────────────────────────────────
        body = QHBoxLayout()
        body.setSpacing(12)

        # LEFT: categories
        left_panel = Panel()
        left_lay = left_panel.with_layout(vertical=True, margins=(16, 14, 16, 14), spacing=6)

        self._left_hdr_frame = QFrame()
        self._left_hdr_frame.setFixedHeight(24)
        left_hdr = QHBoxLayout(self._left_hdr_frame)
        left_hdr.setContentsMargins(0, 0, 0, 0)
        sh = SectionHeader(tr("Safe Categories"))
        apply_tactical_label(sh, font_size=9, letter_spacing=2)
        left_hdr.addWidget(sh)
        self._cat_summary_lbl = QLabel(tr("// scanning…"))
        self._cat_summary_lbl.setObjectName("Muted")
        self._cat_summary_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px;")
        left_hdr.addWidget(self._cat_summary_lbl)
        left_hdr.addStretch()
        left_lay.addWidget(self._left_hdr_frame)
        self._left_hdr_sep = QFrame()
        self._left_hdr_sep.setFixedHeight(1)
        left_lay.addWidget(self._left_hdr_sep)

        self._scan_placeholder = QLabel(tr("Scanning for reclaimable categories…"))
        self._scan_placeholder.setObjectName("Dim")
        self._scan_placeholder.setStyleSheet("font-size: 12px; padding: 12px 0;")
        left_lay.addWidget(self._scan_placeholder)

        self._rows_widget = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        left_lay.addWidget(self._rows_widget)

        # ── Selected-category explanation ────────────────────────
        # Reserved bottom section inside Safe Categories so the
        # overall module stays structurally stable.
        self._exp_panel = QFrame()
        self._exp_panel.setFixedHeight(126)
        exp_lay = QVBoxLayout(self._exp_panel)
        exp_lay.setContentsMargins(12, 10, 12, 10)
        exp_lay.setSpacing(5)

        exp_hdr = QHBoxLayout()
        exp_hdr.setSpacing(8)
        self._exp_hdr_lbl = QLabel(tr("EXPLANATION"))
        self._exp_hdr_lbl.setObjectName("Muted")
        self._exp_hdr_lbl.setStyleSheet(
            "font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; letter-spacing: 1px;"
        )
        exp_hdr.addWidget(self._exp_hdr_lbl)
        self._exp_name_lbl = QLabel()
        self._exp_name_lbl.setObjectName("Dim")
        self._exp_name_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
        exp_hdr.addWidget(self._exp_name_lbl)
        exp_hdr.addStretch()
        exp_lay.addLayout(exp_hdr)

        self._exp_text_lbl = QLabel()
        self._exp_text_lbl.setWordWrap(True)
        self._exp_text_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._exp_text_lbl.setStyleSheet("font-size: 12px; line-height: 1.5;")
        self._exp_scroll = QScrollArea()
        self._exp_scroll.setWidgetResizable(True)
        self._exp_scroll.setFixedHeight(72)
        self._exp_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._exp_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._exp_scroll.setStyleSheet("border: none; background: transparent;")
        self._exp_scroll.setWidget(self._exp_text_lbl)
        exp_lay.addWidget(self._exp_scroll)

        self._empty_lbl = QLabel(
            tr("No reclaimable categories found.\nYour temp folders and browser caches appear empty.")
        )
        self._empty_lbl.setObjectName("Dim")
        self._empty_lbl.setStyleSheet("font-size: 12px; padding: 12px 0;")
        self._empty_lbl.setAlignment(Qt.AlignTop)
        self._empty_lbl.setWordWrap(True)
        self._empty_lbl.setVisible(False)
        left_lay.addWidget(self._empty_lbl)
        # Explanation sits directly under the last category row as an
        # integrated details section. Hidden until a category is selected,
        # so the panel ends right after the last row.
        left_lay.addWidget(self._exp_panel)
        self._reset_explanation()

        left_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        body.addWidget(left_panel, stretch=4, alignment=Qt.AlignTop)

        # RIGHT: summary + results
        right_panel = Panel()
        right_lay = right_panel.with_layout(
            vertical=True, margins=(16, 14, 16, 14), spacing=6
        )

        self._right_hdr_frame = QFrame()
        self._right_hdr_frame.setFixedHeight(24)
        wc_hdr = QHBoxLayout(self._right_hdr_frame)
        wc_hdr.setContentsMargins(0, 0, 0, 0)
        self._right_section_lbl = SectionHeader(tr("Will Clean"))
        apply_tactical_label(self._right_section_lbl, font_size=9, letter_spacing=2)
        wc_hdr.addWidget(self._right_section_lbl)
        self._wc_sub = QLabel(tr("// scanning"))
        self._wc_sub.setObjectName("Muted")
        self._wc_sub.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px;")
        wc_hdr.addWidget(self._wc_sub)
        wc_hdr.addStretch()
        right_lay.addWidget(self._right_hdr_frame)
        self._right_hdr_sep = QFrame()
        self._right_hdr_sep.setFixedHeight(1)
        right_lay.addWidget(self._right_hdr_sep)

        self._total_hdr = QLabel(tr("TOTAL RECLAIMABLE"))
        self._total_hdr.setObjectName("Muted")
        self._total_hdr.setStyleSheet(
            "font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; letter-spacing: 1px;"
        )
        right_lay.addWidget(self._total_hdr)

        total_val_row = QHBoxLayout()
        total_val_row.setSpacing(4)
        total_val_row.setAlignment(Qt.AlignBottom)
        self._total_num = QLabel("—")
        self._total_num.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 32px; font-weight: bold;"
        )
        total_val_row.addWidget(self._total_num)
        self._total_unit = QLabel("")
        self._total_unit.setObjectName("Dim")
        self._total_unit.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 14px; padding-bottom: 4px;"
        )
        total_val_row.addWidget(self._total_unit)
        total_val_row.addStretch()
        right_lay.addLayout(total_val_row)

        self._info_key_labels: dict[str, QLabel] = {}
        self._info_rows: dict[str, QLabel] = {}
        for key, label in [
            ("items",      tr("items removed")),
            ("categories", tr("categories")),
            ("duration",   tr("est. duration")),
        ]:
            row = QHBoxLayout()
            row.setSpacing(8)
            kl = QLabel(label)
            kl.setObjectName("Dim")
            kl.setStyleSheet("font-size: 12px;")
            row.addWidget(kl)
            row.addStretch()
            vl = QLabel("—")
            vl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
            row.addWidget(vl)
            right_lay.addLayout(row)
            self._info_key_labels[key] = kl
            self._info_rows[key] = vl

        right_lay.addSpacing(4)
        self._progress_lbl = QLabel("")
        self._progress_lbl.setObjectName("Dim")
        self._progress_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        self._progress_lbl.setWordWrap(True)
        self._progress_lbl.setVisible(False)
        right_lay.addWidget(self._progress_lbl)

        # Results breakdown (visible after done)
        self._breakdown_sep = QFrame()
        self._breakdown_sep.setFixedHeight(1)
        self._breakdown_sep.setStyleSheet(
            f"background: {get_palette().get('border', '#213028')};"
        )
        self._breakdown_sep.setVisible(False)
        right_lay.addWidget(self._breakdown_sep)

        self._breakdown_hdr = QLabel(tr("PER CATEGORY"))
        self._breakdown_hdr.setObjectName("Muted")
        self._breakdown_hdr.setStyleSheet(
            "font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; letter-spacing: 1px;"
        )
        self._breakdown_hdr.setVisible(False)
        right_lay.addWidget(self._breakdown_hdr)

        self._breakdown_container = QWidget()
        self._breakdown_layout = QVBoxLayout(self._breakdown_container)
        self._breakdown_layout.setContentsMargins(0, 2, 0, 0)
        self._breakdown_layout.setSpacing(3)
        self._breakdown_container.setVisible(False)
        right_lay.addWidget(self._breakdown_container)

        self._recovery_lbl = QLabel(
            tr("Items are in the Recycle Bin and can be fully restored.")
        )
        self._recovery_lbl.setObjectName("Dim")
        self._recovery_lbl.setStyleSheet("font-size: 11px;")
        self._recovery_lbl.setWordWrap(True)
        self._recovery_lbl.setVisible(False)
        right_lay.addWidget(self._recovery_lbl)

        right_lay.addSpacing(6)

        # Guarantees
        g_hdr = SectionHeader(tr("Guarantees"))
        g_hdr.setStyleSheet(
            "font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 9px; letter-spacing: 2px; padding: 0;"
        )
        right_lay.addWidget(g_hdr)

        self._theme_checks = []
        for text in [
            tr("Universally safe categories only — no app data, no documents."),
            tr("Protected paths cannot be selected here, ever."),
            tr("Items go to the Recycle Bin — fully recoverable."),
        ]:
            g_row = QHBoxLayout()
            g_row.setSpacing(8)
            g_row.setAlignment(Qt.AlignTop)
            check_dot = QLabel("✓")
            check_dot.setStyleSheet(
                f"font-size: 11px; color: {get_palette().get('safe', '#7cc596')}; "
                "background: transparent; border: none;"
            )
            check_dot.setFixedWidth(14)
            self._theme_checks.append(check_dot)
            g_row.addWidget(check_dot)
            g_text = QLabel(text)
            g_text.setStyleSheet("font-size: 12px;")
            g_text.setWordWrap(True)
            g_row.addWidget(g_text, stretch=1)
            right_lay.addLayout(g_row)
            right_lay.addSpacing(2)

        # ── Recycle Bin ───────────────────────────────────────────
        # Cleaning here MOVES files to the Recycle Bin, and a move on the same
        # volume frees nothing. Without this block a user cleans 1.5 GB, sees
        # no change in free space, and cleans again next week — one real
        # machine had 16.7 GB sitting in the bin across 795 items. Emptying
        # stays their own explicit decision; the number just has to be visible.
        right_lay.addSpacing(8)
        self._bin_sep = QFrame()
        self._bin_sep.setFixedHeight(1)
        self._bin_sep.setStyleSheet(
            f"background: {get_palette().get('border', '#213028')};")
        right_lay.addWidget(self._bin_sep)
        right_lay.addSpacing(6)

        self._bin_hdr = QLabel(tr("IN THE RECYCLE BIN"))
        self._bin_hdr.setObjectName("Muted")
        self._bin_hdr.setStyleSheet(
            "font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; "
            "letter-spacing: 1px;")
        right_lay.addWidget(self._bin_hdr)

        self._bin_lbl = QLabel("—")
        self._bin_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 13px; font-weight: bold;")
        right_lay.addWidget(self._bin_lbl)

        self._bin_note = QLabel(
            tr("Cleaning moves files here, which does not free disk space on "
               "its own. Emptying the bin is what gives the space back — and "
               "it cannot be undone."))
        self._bin_note.setObjectName("Dim")
        self._bin_note.setStyleSheet("font-size: 11px;")
        self._bin_note.setWordWrap(True)
        right_lay.addWidget(self._bin_note)

        self._btn_empty_bin = QPushButton(tr("Empty Recycle Bin"))
        self._btn_empty_bin.setObjectName("Subtle")
        self._btn_empty_bin.setCursor(Qt.PointingHandCursor)
        self._btn_empty_bin.setStyleSheet("font-size: 11px; padding: 5px 12px;")
        self._btn_empty_bin.clicked.connect(self._on_empty_recycle_bin)
        right_lay.addWidget(self._btn_empty_bin, alignment=Qt.AlignLeft)

        right_lay.addSpacing(4)
        switch_row = QHBoxLayout()
        switch_row.setSpacing(6)
        switch_lbl = QLabel(tr("Need deeper review?"))
        switch_lbl.setObjectName("Dim")
        switch_lbl.setStyleSheet("font-size: 11px;")
        switch_row.addWidget(switch_lbl)
        self._btn_switch_analyze = QPushButton(tr("Open Analyze →"))
        self._btn_switch_analyze.setObjectName("LinkButton")
        self._btn_switch_analyze.setCursor(Qt.PointingHandCursor)
        self._btn_switch_analyze.setStyleSheet(
            "padding: 0px; font-size: 11px; font-family: 'JetBrains Mono';"
        )
        self._btn_switch_analyze.clicked.connect(
            lambda: self.navigate_to.emit("Analyze")
        )
        switch_row.addWidget(self._btn_switch_analyze)
        switch_row.addStretch()
        right_lay.addLayout(switch_row)

        right_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        body.addWidget(right_panel, stretch=3, alignment=Qt.AlignTop)
        self._left_panel = left_panel
        self._right_panel = right_panel

        # Both panels are top-aligned and content-sized: Will Clean keeps its
        # own height when the explanation expands the Safe Categories panel.
        main_lay.addLayout(body)
        main_lay.addStretch()

        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll, stretch=1)
        self._apply_quick_cleanup_styles()

    def _apply_quick_cleanup_styles(self):
        p = get_palette()
        header_border = p.get("border", "#213028")
        for frame in (self._left_hdr_frame, self._right_hdr_frame):
            frame.setStyleSheet(
                "background: transparent; border: none;"
            )
        sep_qss = f"background: {header_border}; border: none;"
        self._left_hdr_sep.setStyleSheet(sep_qss)
        self._right_hdr_sep.setStyleSheet(sep_qss)
        self._exp_panel.setStyleSheet(
            f"background: {p.get('panel_alt', '#18241e')};"
            f"border: none;"
        )
        self._btn_rescan.setStyleSheet(
            "QPushButton {"
            f"background: {p.get('panel_alt', '#18241e')};"
            f"border: 1px solid {p.get('border_alt', '#2b3d33')};"
            f"color: {p.get('text', '#d6e2da')};"
            "padding: 7px 14px; font-size: 12px; font-weight: 500; min-height: 28px; border-radius: 2px;"
            "}"
            "QPushButton:hover {"
            f"background: {p.get('panel_hover', '#1d2c25')};"
            f"border-color: {p.get('border_hover', '#3a5648')};"
            "}"
            "QPushButton:pressed {"
            f"background: {p.get('tint_bg', '#0f1914')};"
            "}"
            "QPushButton:disabled {"
            f"background: {p.get('bg_deep', '#080d0a')};"
            f"border-color: {p.get('border', '#213028')};"
            f"color: {p.get('text_faint', '#57685e')};"
            "}"
        )

    # ── Auto-scan ─────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_recycle_bin()
        if self._state == _IDLE:
            self._start_scan()

    # ── Scan lifecycle ────────────────────────────────────────────

    def _start_scan(self):
        if self._detector and self._detector.isRunning():
            self._detector.cancel()
            self._detector.wait(2000)

        self._state = _SCANNING
        self._expanded_index = -1
        self._row_assessments.clear()
        self._clear_rows()
        self._hide_results_panel()

        self._subtitle_lbl.setText(tr("scanning for safe categories…"))
        self._btn_rescan.setEnabled(False)
        self._btn_clean.setText(tr("Scanning…"))
        self._btn_clean.setEnabled(False)
        self._sel_badge.setVisible(False)
        self._cat_summary_lbl.setText(tr("// scanning…"))
        self._scan_placeholder.setVisible(True)
        self._empty_lbl.setVisible(False)
        self._reset_explanation()
        self._wc_sub.setText(tr("// scanning"))
        self._right_section_lbl.setText(tr("Will Clean"))
        self._total_hdr.setText(tr("TOTAL RECLAIMABLE"))
        self._total_num.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 32px; font-weight: bold;"
        )
        self._total_num.setText("—")
        self._total_unit.setText("")
        self._info_key_labels["items"].setText(tr("items removed"))
        self._info_key_labels["categories"].setText(tr("categories"))
        self._info_key_labels["duration"].setText(tr("est. duration"))
        for v in self._info_rows.values():
            v.setText("—")
            v.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
        self._progress_lbl.setVisible(False)

        self._detector = QuickCleanupDetector(parent=self)
        self._detector.category_found.connect(self._on_category_found)
        self._detector.finished.connect(self._on_scan_done)
        self._detector.start()

    # ── Recycle Bin ───────────────────────────────────────────────

    def refresh_recycle_bin(self):
        """Show what is sitting in the bin, waiting to actually be freed."""
        from app.services.recycle_bin import recycle_bin_status
        size_bytes, items = recycle_bin_status()
        self._bin_bytes = size_bytes
        has_content = size_bytes > 0 or items > 0
        self._bin_lbl.setText(
            tr("{size} · {n:,} items", size=_format_size(size_bytes), n=items)
            if has_content else tr("Empty"))
        self._btn_empty_bin.setEnabled(has_content)
        # The nudge only makes sense when there is something to reclaim.
        self._bin_note.setVisible(has_content)

    def _on_empty_recycle_bin(self):
        from PySide6.QtWidgets import QMessageBox
        from app.services.recycle_bin import empty_recycle_bin, recycle_bin_status

        size_bytes, items = recycle_bin_status()
        if not (size_bytes or items):
            self.refresh_recycle_bin()
            return
        # The one irreversible thing Vigil can do, so it says so plainly.
        reply = QMessageBox.question(
            self, tr("Empty Recycle Bin"),
            tr("Permanently delete {n:,} items and free {size}?\n\n"
               "This cannot be undone — everything Vigil cleaned is in here, "
               "and emptying is the step that actually frees the space.",
               n=items, size=_format_size(size_bytes)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        ok, message = empty_recycle_bin()
        self.refresh_recycle_bin()
        if not ok:
            QMessageBox.warning(self, tr("Empty Recycle Bin"), message)

    # ── Background work ───────────────────────────────────────────

    def busy_reason(self) -> str:
        """Why this screen must not be torn down right now, or "".

        A deletion in flight is the important case: rebuilding the shell for a
        language change destroys this screen, and with it the running
        CleanupWorker — which crashes the process outright.
        """
        if self._worker is not None and self._worker.isRunning():
            return tr("a cleanup is removing files")
        if self._detector is not None and self._detector.isRunning():
            return tr("a quick scan is running")
        return ""

    def stop_background_work(self, timeout_ms: int = 3000) -> bool:
        from app.services.workers import stop_all
        return stop_all(self._worker, self._detector, timeout_ms=timeout_ms)

    def _clear_rows(self):
        # hide(), not setParent(None): unparenting stops the outgoing row
        # painting over the new list, but it also promotes it to a top-level
        # *window* — the same pattern in the Startups list put a row on screen
        # as a blank 200x64 frame. Hiding stops the paint and keeps it a child
        # until the deferred delete runs.
        for sep in self._sep_frames:
            sep.hide()
            sep.deleteLater()
        for row in self._rows:
            row.hide()
            row.deleteLater()
        self._rows.clear()
        self._sep_frames.clear()
        self._reset_explanation()

    def _hide_results_panel(self):
        self._breakdown_sep.setVisible(False)
        self._breakdown_hdr.setVisible(False)
        self._breakdown_container.setVisible(False)
        self._recovery_lbl.setVisible(False)
        while self._breakdown_layout.count():
            item = self._breakdown_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()   # hide, then delete — never unparent a live widget
                widget.deleteLater()

    # ── Category arrival ──────────────────────────────────────────

    def _on_category_found(self, cat: QuickCleanupCategory):
        self._scan_placeholder.setVisible(False)
        idx = len(self._rows)

        row = _CategoryRow(cat, self._on_checkbox_changed, parent=self._rows_widget)
        row.clicked.connect(lambda i=idx: self._on_row_clicked(i))
        self._rows_layout.addWidget(row)
        self._rows.append(row)

        self._update_summary()

    # ── Explanation toggle ────────────────────────────────────────

    def _reset_explanation(self):
        """Collapse the explanation so Safe Categories ends after the last row."""
        self._exp_panel.hide()

    def _on_row_clicked(self, idx: int):
        if self._state in (_CLEANING,):
            return

        if self._expanded_index == idx:
            # Deselect — explanation section reverts to its inactive state.
            self._rows[idx].set_expanded(False)
            self._expanded_index = -1
            self._reset_explanation()
            return

        # Deselect previous, select new — populate the explanation in place.
        if self._expanded_index >= 0:
            self._rows[self._expanded_index].set_expanded(False)
        self._rows[idx].set_expanded(True)
        cat = self._rows[idx].category
        self._exp_name_lbl.setText(f"· {tr(cat.label)}")
        assessment = self._rows[idx].assessment
        if self._state == _DONE and assessment is not None:
            self._exp_hdr_lbl.setText(tr("WHAT HAPPENED & HOW TO FINISH"))
            self._exp_text_lbl.setText(assessment.explanation_text)
            # Result explanations carry why + step-by-step actions, so give
            # them room to show without hiding the steps below the fold.
            self._exp_scroll.setFixedHeight(150)
            self._exp_panel.setFixedHeight(204)
        else:
            self._exp_hdr_lbl.setText(tr("EXPLANATION"))
            self._exp_text_lbl.setText(tr(_EXPLANATIONS.get(cat.key, _EXPLANATION_FALLBACK)))
            self._exp_scroll.setFixedHeight(72)
            self._exp_panel.setFixedHeight(126)
        self._exp_text_lbl.setStyleSheet("font-size: 12px; line-height: 1.5;")
        self._exp_panel.show()
        self._expanded_index = idx

    # ── Scan done ─────────────────────────────────────────────────

    def _sync_panel_heights(self):
        """Equalise the two containers at their no-explanation height.

        Both panels start at the same height; selecting a category expands
        only Safe Categories above this baseline — Will Clean stays put."""
        if not hasattr(self, "_left_panel"):
            return
        self._left_panel.setMinimumHeight(0)
        self._right_panel.setMinimumHeight(0)
        base = max(
            self._left_panel.sizeHint().height(),
            self._right_panel.sizeHint().height(),
        )
        self._left_panel.setMinimumHeight(base)
        self._right_panel.setMinimumHeight(base)

    def _on_scan_done(self):
        self._state = _READY

        if not self._rows:
            self._scan_placeholder.setVisible(False)
            self._empty_lbl.setVisible(True)
            self._subtitle_lbl.setText(tr("no reclaimable categories found"))
            self._btn_clean.setText(tr("Nothing to clean"))
            self._btn_clean.setEnabled(False)
            self._btn_rescan.setEnabled(True)
            self._wc_sub.setText(tr("// empty"))
            self._total_num.setText("0")
            self._total_unit.setText("MB")
            QTimer.singleShot(0, self._sync_panel_heights)
            return

        self._subtitle_lbl.setText(tr("Click a category to learn more · safe categories only"))
        self._btn_rescan.setEnabled(True)
        self._update_summary()
        QTimer.singleShot(0, self._sync_panel_heights)

    # ── Summary ───────────────────────────────────────────────────

    def _on_checkbox_changed(self):
        if self._state in (_CLEANING, _DONE):
            return
        self._update_summary()

    def _update_summary(self):
        selected = [r for r in self._rows if r.is_checked]
        total_bytes = sum(r.category.size_bytes for r in selected)
        total_items = sum(r.category.file_count for r in selected)
        n_sel, n_total = len(selected), len(self._rows)

        self._sel_badge.set_badge(
            tr("{n} of {total} selected", n=n_sel, total=n_total), "info")
        self._sel_badge.setVisible(n_total > 0)
        size_str = _format_size(total_bytes) if selected else "0 MB"
        self._cat_summary_lbl.setText(tr("// {n} selected · {size} ready",
                                         n=n_sel, size=size_str))

        num_str, unit_str = split_size(total_bytes)
        self._total_num.setText(num_str)
        self._total_unit.setText(unit_str)

        self._info_rows["items"].setText(f"{total_items:,}")
        self._info_rows["categories"].setText(f"{n_sel} of {n_total}")

        if total_items > 0:
            est = max(1, total_items // 1500)
            self._info_rows["duration"].setText(
                f"~ {est}s" if est < 60 else f"~ {est // 60}m {est % 60}s"
            )
        else:
            self._info_rows["duration"].setText("—")

        self._wc_sub.setText(tr("// scanning") if self._state == _SCANNING else "// summary")

        if selected and self._state == _READY:
            self._btn_clean.setText(tr("Clean {size}").format(size=_format_size(total_bytes)))
            self._btn_clean.setEnabled(True)
        elif self._state == _READY:
            self._btn_clean.setText(tr("Select categories"))
            self._btn_clean.setEnabled(False)

    # ── Cleanup ───────────────────────────────────────────────────

    def _on_clean_clicked(self):
        selected = [r for r in self._rows if r.is_checked]
        if not selected:
            return

        all_paths: list = []
        for row in selected:
            all_paths.extend(row.category.paths)
        if not all_paths:
            return

        self._state = _CLEANING
        self._cleaning_rows = selected
        self._cleanup_start_time = time.monotonic()

        # Collapse any open explanation
        if self._expanded_index >= 0:
            self._rows[self._expanded_index].set_expanded(False)
            self._expanded_index = -1
        self._reset_explanation()

        self._btn_clean.setEnabled(False)
        self._btn_clean.setText(tr("Cleaning…"))
        self._btn_rescan.setEnabled(False)
        for row in self._rows:
            row.set_interactive(False)

        total_items = sum(r.category.file_count for r in selected)
        self._progress_lbl.setText(tr("Moving 0 / {total:,}…", total=total_items))
        self._progress_lbl.setVisible(True)
        self._wc_sub.setText(tr("// cleaning"))

        perm_delete = (
            self._settings_store.get("perm_delete_enabled", False)
            if self._settings_store else False
        )
        self._worker = CleanupWorker(
            paths=all_paths,
            mode=CleanupWorker.MODE_RECYCLE,
            perm_delete_enabled=perm_delete,
            parent=self,
        )
        self._worker.progress.connect(self._on_cleanup_progress)
        self._worker.finished.connect(self._on_cleanup_done)
        self._worker.start()

    def _on_cleanup_progress(self, done: int, total: int, path: str):
        if path:
            name = os.path.basename(path) or path
            self._progress_lbl.setText(tr("Moving {done} / {total:,} — {name}",
                                          done=done + 1, total=total, name=name))
        else:
            self._progress_lbl.setText(tr("Finalising… ({n:,} processed)", n=total))

    def _on_cleanup_done(self, result):
        self._state = _DONE
        elapsed = time.monotonic() - self._cleanup_start_time

        n_ok  = len(result.succeeded)
        n_in_use = len(result.in_use)
        n_fail = len(result.failed)
        freed = result.total_bytes_freed
        p     = get_palette()
        overall = assess_cleanup_counts(
            succeeded_count=n_ok,
            in_use_count=n_in_use,
            failed_count=n_fail,
            skipped_count=len(result.skipped_protected),
            category_label="Quick Cleanup",
        )

        self.refresh_recycle_bin()
        self._right_section_lbl.setText(tr("Results"))
        elapsed_str = f"{elapsed:.0f}s" if elapsed < 60 else f"{elapsed / 60:.1f}m"
        self._wc_sub.setText(tr("// done in {elapsed}", elapsed=elapsed_str))

        safe_color = p.get("safe", "#7cc596")
        warn_color = p.get("review", "#d8b46a")
        danger_color = p.get("risk", "#d68a78")
        self._total_hdr.setText(tr("TOTAL FREED"))
        self._total_num.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 32px; "
            f"font-weight: bold; color: {safe_color};"
        )
        freed_num, freed_unit = split_size(freed)
        self._total_num.setText(freed_num)
        self._total_unit.setText(freed_unit)

        self._info_key_labels["items"].setText(tr("items moved"))
        self._info_key_labels["categories"].setText(overall.summary_key_label)
        self._info_key_labels["duration"].setText(tr("completed in"))

        self._info_rows["items"].setText(f"{n_ok:,}")
        if overall.state == STATE_FAILED:
            self._info_rows["categories"].setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 12px; color: {danger_color};"
            )
            self._info_rows["categories"].setText(overall.summary_value)
        elif overall.state in (STATE_PARTIAL, STATE_IN_USE):
            self._info_rows["categories"].setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 12px; color: {warn_color};"
            )
            self._info_rows["categories"].setText(overall.summary_value)
        else:
            self._info_rows["categories"].setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 12px; color: {safe_color};"
            )
            self._info_rows["categories"].setText(overall.summary_value)
        self._info_rows["duration"].setText(elapsed_str)
        self._progress_lbl.setVisible(False)

        # Per-category breakdown
        succeeded_set = set(result.succeeded)
        in_use_set    = set(result.in_use)
        failed_set    = set(result.failed)

        cat_rows: list[tuple] = []
        for row in self._cleaning_rows:
            paths = row.category.paths
            cat_ok   = [p2 for p2 in paths if p2 in succeeded_set]
            cat_in_use = [p2 for p2 in paths if p2 in in_use_set]
            cat_fail = [p2 for p2 in paths if p2 in failed_set]
            cat_freed = int(freed * len(cat_ok) / n_ok) if n_ok > 0 and freed > 0 else 0
            assessment = assess_cleanup_counts(
                succeeded_count=len(cat_ok),
                in_use_count=len(cat_in_use),
                failed_count=len(cat_fail),
                category_key=row.category.key,
                category_label=row.category.label,
            )
            self._row_assessments[row.category.key] = assessment
            cat_rows.append((row.category.label, cat_freed, assessment))
            row.show_result(assessment)

        for label, cat_freed, assessment in cat_rows:
            b_row = QHBoxLayout()
            b_row.setSpacing(6)

            dot = QLabel("●")
            if assessment.state == STATE_FAILED:
                dot.setStyleSheet(f"font-size: 9px; color: {danger_color};")
            elif assessment.state in (STATE_PARTIAL, STATE_IN_USE):
                dot.setStyleSheet(f"font-size: 9px; color: {warn_color};")
            else:
                dot.setStyleSheet(f"font-size: 9px; color: {safe_color};")
            dot.setFixedWidth(12)
            b_row.addWidget(dot)

            name_lbl = QLabel(label)
            name_lbl.setStyleSheet("font-size: 11px;")
            b_row.addWidget(name_lbl, stretch=1)

            freed_lbl = QLabel(_format_size(cat_freed) if cat_freed > 0 else "—")
            freed_lbl.setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 11px; color: {safe_color};"
            )
            freed_lbl.setAlignment(Qt.AlignRight)
            b_row.addWidget(freed_lbl)

            status_lbl = QLabel(f"  {assessment.breakdown_label}")
            if assessment.state == STATE_FAILED:
                status_lbl.setStyleSheet(f"font-size: 10px; color: {danger_color};")
            elif assessment.state in (STATE_PARTIAL, STATE_IN_USE):
                status_lbl.setStyleSheet(f"font-size: 10px; color: {warn_color};")
            else:
                status_lbl.setStyleSheet(f"font-size: 10px; color: {safe_color};")
            b_row.addWidget(status_lbl)

            container = QWidget()
            container.setLayout(b_row)
            self._breakdown_layout.addWidget(container)

        self._breakdown_sep.setVisible(True)
        self._breakdown_hdr.setVisible(True)
        self._breakdown_container.setVisible(True)
        self._recovery_lbl.setVisible(True)

        self._btn_clean.setText(tr("Cleanup complete"))
        self._btn_clean.setEnabled(False)
        self._btn_rescan.setEnabled(True)

        n_cleaned = sum(1 for _, _, assessment in cat_rows if assessment.succeeded_count > 0)
        if overall.state in (STATE_PARTIAL, STATE_IN_USE):
            self._subtitle_lbl.setText(tr(
                "{cleaned} categories cleaned · {locked} files still in use — "
                "steps to finish are shown below",
                cleaned=n_cleaned, locked=n_in_use))
        elif overall.state == STATE_FAILED:
            self._subtitle_lbl.setText(tr(
                "{cleaned} categories cleaned · some items need attention — "
                "details are shown below", cleaned=n_cleaned))
        else:
            self._subtitle_lbl.setText(tr(
                "{cleaned} categories cleaned · {size} freed",
                cleaned=n_cleaned, size=_format_size(freed)))
        self._sel_badge.setVisible(False)

        # Proactively surface WHY a category didn't fully clean and HOW to fix
        # it. A beginner won't know to click a row to find the "close the
        # browser" guidance, so auto-expand the first category needing
        # attention and reveal its result explanation in place.
        attention_states = (STATE_PARTIAL, STATE_IN_USE, STATE_FAILED)
        attention_idx = next(
            (i for i, r in enumerate(self._rows)
             if r.assessment is not None and r.assessment.state in attention_states),
            -1,
        )
        if attention_idx >= 0:
            self._on_row_clicked(attention_idx)

        self._write_history(result)

    def _write_history(self, result):
        if not (result.succeeded or result.in_use or result.failed):
            return
        try:
            from app.state.session_store import save_cleanup_record
            items = []
            for row in self._cleaning_rows:
                for path in row.category.paths:
                    items.append({
                        "path":     path,
                        "name":     row.category.label,
                        "size":     0,
                        "risk":     "Safe",
                        "category": row.category.label,
                    })
            save_cleanup_record(
                session_id="quick_cleanup",
                items=items,
                result=result,
                mode=CleanupWorker.MODE_RECYCLE,
            )
        except Exception:
            pass

    # ── Theme ─────────────────────────────────────────────────────

    def _on_theme_changed(self, _key: str = ""):
        if not hasattr(self, "_breakdown_sep"):
            return
        p = get_palette()
        safe_color   = p.get("safe",   "#7cc596")
        border_color = p.get("border", "#213028")

        check_qss = (
            f"font-size: 11px; color: {safe_color}; background: transparent; border: none;"
        )
        for c in self._theme_checks:
            c.setStyleSheet(check_qss)

        sep_qss = f"background: {border_color};"
        for sep in self._sep_frames:
            sep.setStyleSheet(sep_qss)
        self._breakdown_sep.setStyleSheet(sep_qss)
        for row in self._rows:
            row._apply_state_style()
        self._apply_quick_cleanup_styles()
