from __future__ import annotations

"""Analyze screen — folder scanning with real progress and AI explanations."""

import os
import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QProgressBar, QScrollArea, QFileDialog,
    QAbstractItemView,
)
from PySide6.QtCore import Qt, QTimer, Signal, QEvent
from PySide6.QtGui import QColor, QBrush

from app.widgets.panels import Panel
from app.widgets.panels import apply_tactical_label
from app.widgets.controls import TacticalComboBox
from app.widgets.tables import create_table, set_row
from app.widgets.feeds import OperatorFeed
from app.models.finding import _format_size
from app.i18n import tr


# ── Pipeline stages ─────────────────────────────────────────────

def _get_stages_all():
    return [
        (tr("Enumerate paths"), "paths"),
        (tr("Scan & categorize"), "scan"),
        (tr("Risk estimation"), "risk"),
        (tr("AI explanation"), "ai"),
    ]

def _get_stages_smart():
    return [
        (tr("Enumerate paths"), "paths"),
        (tr("Scan & categorize"), "scan"),
        (tr("Entity detection"), "entity"),
        (tr("AI classification"), "ai"),
    ]

def _chip_styles() -> dict:
    """Return chip QSS map for the active theme."""
    from app.themes.theme_manager import get_palette
    p = get_palette()
    border  = p.get("border",      "#213028")
    border_alt = p.get("border_alt", "#2b3d33")
    faint   = p.get("text_faint",  "#57685e")
    dim     = p.get("text_dim",    "#8a9b8f")
    review  = p.get("review",      "#d8b46a")
    safe    = p.get("safe",        "#7cc596")
    risk    = p.get("risk",        "#d68a78")
    review_soft = p.get("review_soft", "#2c2516")
    safe_soft = p.get("safe_soft", "#1c2e22")
    risk_soft = p.get("risk_soft", "#2e1f1c")
    return {
        "idle":    f"background: transparent; border: 1px solid {border};      padding: 4px 12px; color: {faint};",
        "pending": f"background: transparent; border: 1px solid {border_alt};   padding: 4px 12px; color: {dim};",
        "active":  f"background: {review_soft}; border: 1px solid {review};      padding: 4px 12px; color: {review};",
        "done":    f"background: {safe_soft}; border: 1px solid {safe}70;        padding: 4px 12px; color: {safe};",
        "failed":  f"background: {risk_soft}; border: 1px solid {risk}80;        padding: 4px 12px; color: {risk};",
    }


class _PipelineChip(QFrame):
    """A stage chip with icon prefix and optional metric subtitle."""

    def __init__(self, label: str, state: str = "idle", parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)
        self._label = label
        self._state = state

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(0)

        self._lbl = QLabel()
        self._lbl.setStyleSheet("font-size: 11px; background: transparent; border: none;")
        lay.addWidget(self._lbl)

        self._metric = QLabel()
        self._metric.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 9px; background: transparent; "
            "border: none; color: inherit; padding: 0;"
        )
        self._metric.setVisible(False)
        lay.addWidget(self._metric)

        self._apply(state)

    def _apply(self, state: str):
        self._state = state
        prefix = {"done": "✓", "active": "●", "pending": "·", "failed": "✕", "idle": "–"}.get(state, "–")
        self._lbl.setText(f" {prefix}  {self._label}")
        styles = _chip_styles()
        self.setStyleSheet(styles.get(state, styles["idle"]))
        from app.themes.theme_manager import get_palette
        p = get_palette()
        _state_color = {
            "done":    p.get("safe",       "#7cc596"),
            "active":  p.get("review",     "#d8b46a"),
            "failed":  p.get("risk",       "#d68a78"),
            "pending": p.get("text_faint", "#57685e"),
            "idle":    p.get("text_faint", "#57685e"),
        }
        metric_color = _state_color.get(state, p.get("text_faint", "#57685e"))
        self._metric.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 9px; background: transparent; "
            f"border: none; color: {metric_color}; padding: 0;"
        )

    def set_state(self, state: str, metric: str = ""):
        self._apply(state)
        if metric:
            self._metric.setText(f"  {metric}")
            self._metric.setVisible(True)
        else:
            self._metric.setVisible(False)


# ── Progress bar helpers ────────────────────────────────────────

def _bar_qss(color: str) -> str:
    from app.themes.theme_manager import get_palette
    bg = get_palette().get("bg_deep", "#080d0a")
    return (
        f"QProgressBar {{ border: none; background: {bg}; "
        f"height: 4px; min-height: 4px; max-height: 4px; }}"
        f"QProgressBar::chunk {{ background: {color}; }}"
    )


def _make_progress(color: str = None) -> QProgressBar:
    from app.themes.theme_manager import get_palette
    if color is None:
        color = get_palette().get("accent", "#7cc596")
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setFixedHeight(4)
    bar.setTextVisible(False)
    bar.setStyleSheet(_bar_qss(color))
    return bar


def _set_indeterminate(bar: QProgressBar, color: str = None):
    """Switch bar to indeterminate (pulsing) mode."""
    from app.themes.theme_manager import get_palette
    if color is None:
        color = get_palette().get("accent", "#7cc596")
    bar.setRange(0, 0)
    bar.setFixedHeight(4)
    bar.setStyleSheet(_bar_qss(color))


def _set_determinate(bar: QProgressBar, value: int, color: str = None):
    """Switch bar back to determinate mode at given 0-100 value."""
    from app.themes.theme_manager import get_palette
    if color is None:
        color = get_palette().get("accent", "#7cc596")
    bar.setRange(0, 100)
    bar.setValue(value)
    bar.setFixedHeight(4)
    bar.setStyleSheet(_bar_qss(color))


# ═══════════════════════════════════════════════════════════════

class AnalyzeScreen(QWidget):
    
    # Signal emitted when user clicks a category row in Partial Findings
    # Carries the category name to filter Findings dashboard
    category_clicked = Signal(str)

    def __init__(self, scan_state=None, parent=None):
        super().__init__(parent)
        self._scan_state = scan_state
        self._worker = None
        self._dup_worker = None
        self._start_time = 0.0
        self._selected_folder = ""
        self._scan_roots: list[str] = []   # >1 only for "Scan all drives"
        self._state_log_connected = False
        self._ai_connected = False
        self._hover_row = -1
        self._selected_pf_row = -1

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)

        self._ai_poll = QTimer(self)
        self._ai_poll.setInterval(800)
        self._ai_poll.timeout.connect(self._tick_ai_progress)

        # Real entity progress tracking
        self._entity_processed = 0
        self._entity_total = 0
        self._entity_phase = ""
        
        # Pipeline state tracking for proper button state management
        # States: idle, scanning_filesystem, grouping_entities, ai_classifying, complete, stopped
        self._pipeline_state = "idle"
        self._filesystem_complete = False
        self._grouping_complete = False
        self._ai_complete = False
        self._last_ai_snapshot = (0, 0, 0, 0)

        self._build_ui()
        from app.themes.theme_manager import theme_signaller
        theme_signaller().theme_changed.connect(self._rebuild_styles)

    def set_scan_state(self, scan_state):
        self._scan_state = scan_state

    def _apply_mode_combo_style(self):
        from app.themes.theme_manager import get_palette
        p = get_palette()
        self._mode_combo.apply_reference_style(p, compact=True)

    def _apply_target_field_style(self):
        from app.themes.theme_manager import get_palette
        p = get_palette()
        all_active = len(getattr(self, "_scan_roots", []) or []) > 1
        # Folder half dims its text when no folder is chosen — unless "ALL" is
        # the active target, in which case the folder half is the inactive one.
        folder_selected = bool(self._selected_folder) and not all_active
        text_color = p.get("text", "#d6e2da") if folder_selected else p.get("text_dim", "#8a9b8f")
        panel_alt = p.get("panel_alt", "#18241e")
        panel_hover = p.get("panel_hover", "#1d2c25")
        border_alt = p.get("border_alt", "#2b3d33")
        border_hover = p.get("border_hover", "#3a5648")
        accent = p.get("accent", "#7cc596")
        # Folder half: left-rounded, no right border (joins the ALL chip).
        self._btn_folder.setStyleSheet(
            f"QPushButton {{"
            f"background-color: {panel_alt};"
            f"border: 1px solid {border_alt}; border-right: none;"
            f"color: {text_color};"
            f"padding: 5px 12px; font-size: 11px; font-family: 'JetBrains Mono';"
            f"text-align: left;"
            f"border-top-left-radius: 2px; border-bottom-left-radius: 2px;"
            f"border-top-right-radius: 0; border-bottom-right-radius: 0;"
            f"}}"
            f"QPushButton:hover {{ background-color: {panel_hover}; color: {p.get('text', '#d6e2da')}; }}"
            f"QPushButton:disabled {{"
            f"background-color: {p.get('bg_deep', '#080d0a')};"
            f"border-color: {p.get('border', '#213028')};"
            f"color: {p.get('text_faint', '#57685e')};"
            f"}}"
        )
        # ALL chip: right-rounded. Filled with the accent when it is the active
        # target so the user can see all-drives is selected.
        if all_active:
            chip_bg, chip_fg, chip_border = accent, p.get("on_accent", "#070c09"), accent
        else:
            chip_bg, chip_fg, chip_border = panel_alt, p.get("text_dim", "#8a9b8f"), border_alt
        self._btn_scan_all.setStyleSheet(
            f"QPushButton {{"
            f"background-color: {chip_bg};"
            f"border: 1px solid {chip_border};"
            f"color: {chip_fg};"
            f"font-size: 10px; font-weight: 600; font-family: 'JetBrains Mono';"
            f"letter-spacing: 1px;"
            f"border-top-right-radius: 2px; border-bottom-right-radius: 2px;"
            f"border-top-left-radius: 0; border-bottom-left-radius: 0;"
            f"}}"
            f"QPushButton:hover {{ border-color: {border_hover}; color: {p.get('text', '#d6e2da') if not all_active else chip_fg}; }}"
        )

    def _rebuild_styles(self, theme_key: str = ""):
        from app.themes.theme_manager import get_palette
        p = get_palette()
        safe_color = p.get("safe", "#7cc596")
        review_color = p.get("review", "#d8b46a")
        faint_color = p.get("text_faint", "#57685e")
        self._scan_label.setStyleSheet(
            f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; letter-spacing: 2px; color: {safe_color};"
        )
        self._ai_label.setStyleSheet(
            f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; letter-spacing: 2px; color: {review_color};"
        )
        self._scan_bar.setStyleSheet(_bar_qss(safe_color))
        self._ai_bar.setStyleSheet(_bar_qss(review_color))
        mono_faint = f"font-family: 'JetBrains Mono'; font-size: 10px; color: {faint_color};"
        self._scan_prog_lbl.setStyleSheet(mono_faint)
        self._ai_prog_lbl.setStyleSheet(mono_faint)
        for chip in self._chips:
            chip._apply(chip._state)
        self._apply_target_field_style()
        self._apply_mode_combo_style()
        self._apply_panel_header_frames()

    # ─── UI Build ────────────────────────────────────────────

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("border: none;")

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 16, 22, 22)
        layout.setSpacing(10)

        # ─── Screen header ───
        self._header_layout = QHBoxLayout()
        header = self._header_layout
        header.setSpacing(10)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel(tr("ANALYZE"))
        apply_tactical_label(title, font_size=16, letter_spacing=4)
        title_col.addWidget(title)
        self._sub = QLabel(tr("Select a folder target to begin"))
        self._sub.setObjectName("Dim")
        self._sub.setStyleSheet("font-size: 12px;")
        title_col.addWidget(self._sub)
        # Drive capacity / type readout for the selected target (psutil-backed).
        self._drive_lbl = QLabel("")
        self._drive_lbl.setObjectName("Muted")
        self._drive_lbl.setStyleSheet("font-size: 11px;")
        self._drive_lbl.setVisible(False)
        title_col.addWidget(self._drive_lbl)
        header.addLayout(title_col, stretch=1)

        # Target control: one field-like unit — a wide folder picker plus an
        # inline "ALL" chip at the right edge that selects every fixed drive.
        target_field = QWidget()
        target_field.setFixedHeight(32)
        tf_lay = QHBoxLayout(target_field)
        tf_lay.setContentsMargins(0, 0, 0, 0)
        tf_lay.setSpacing(0)

        self._btn_folder = QPushButton()
        self._btn_folder.setCursor(Qt.PointingHandCursor)
        self._btn_folder.setFixedWidth(204)
        self._btn_folder.setFixedHeight(32)
        self._btn_folder.clicked.connect(self._pick_folder)
        tf_lay.addWidget(self._btn_folder)

        self._btn_scan_all = QPushButton(tr("ALL"))
        self._btn_scan_all.setCursor(Qt.PointingHandCursor)
        self._btn_scan_all.setFixedWidth(52)
        self._btn_scan_all.setFixedHeight(32)
        self._btn_scan_all.setToolTip(
            tr("Scan every internal (fixed) drive at once. "
               "Removable and network drives are skipped.")
        )
        self._btn_scan_all.clicked.connect(self._pick_all_drives)
        tf_lay.addWidget(self._btn_scan_all)

        header.addWidget(target_field, alignment=Qt.AlignVCenter)

        # Scan mode selector
        self._mode_combo = TacticalComboBox()
        self._mode_combo.addItem(tr("Adaptive scan"), "smart")
        self._mode_combo.addItem(tr("All files"), "all")
        self._mode_combo.setCurrentIndex(0)
        self._mode_combo.setFixedWidth(188)
        self._mode_combo.setFixedHeight(32)
        self._apply_mode_combo_style()
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        header.addWidget(self._mode_combo, alignment=Qt.AlignVCenter)

        self._btn_scan = QPushButton(tr("Start scan"))
        self._btn_scan.setObjectName("Primary")
        self._btn_scan.setCursor(Qt.PointingHandCursor)
        self._btn_scan.setEnabled(False)
        self._btn_scan.setFixedWidth(120)
        self._btn_scan.setFixedHeight(32)
        self._btn_scan.clicked.connect(self._on_scan_btn)
        self._scan_active = False  # tracks whether we are currently scanning
        self._resuming = False     # set True by resume_scan to skip clear()
        self._scan_offset = 0     # restored finding count for resume counter display
        header.addWidget(self._btn_scan, alignment=Qt.AlignVCenter)

        layout.addLayout(header)

        # ─── Pipeline + progress panel ───
        from app.themes.theme_manager import get_palette as _gp
        _p = _gp()

        pipe_panel = Panel()
        pipe_lay = pipe_panel.with_layout(vertical=True, margins=(14, 10, 14, 10), spacing=8)

        # Scan progress bar
        scan_prog_row = QHBoxLayout()
        scan_prog_row.setSpacing(8)
        self._scan_label = QLabel(tr("SCAN"))
        self._scan_label.setStyleSheet(f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; letter-spacing: 2px; color: {_p.get('safe', '#7cc596')};")
        self._scan_label.setFixedWidth(36)
        scan_prog_row.addWidget(self._scan_label)
        self._scan_bar = _make_progress(_p.get("safe", "#7cc596"))
        scan_prog_row.addWidget(self._scan_bar, stretch=1)
        self._scan_prog_lbl = QLabel("")
        self._scan_prog_lbl.setStyleSheet(f"font-family: 'JetBrains Mono'; font-size: 10px; color: {_p.get('text_faint', '#57685e')};")
        self._scan_prog_lbl.setFixedWidth(90)
        self._scan_prog_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        scan_prog_row.addWidget(self._scan_prog_lbl)
        pipe_lay.addLayout(scan_prog_row)

        # AI progress bar
        ai_prog_row = QHBoxLayout()
        ai_prog_row.setSpacing(8)
        self._ai_label = QLabel(tr("AI"))
        self._ai_label.setStyleSheet(f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; letter-spacing: 2px; color: {_p.get('review', '#d8b46a')};")
        self._ai_label.setFixedWidth(36)
        ai_prog_row.addWidget(self._ai_label)
        self._ai_bar = _make_progress(_p.get("review", "#d8b46a"))
        ai_prog_row.addWidget(self._ai_bar, stretch=1)
        self._ai_prog_lbl = QLabel("")
        self._ai_prog_lbl.setStyleSheet(f"font-family: 'JetBrains Mono'; font-size: 10px; color: {_p.get('text_faint', '#57685e')};")
        self._ai_prog_lbl.setFixedWidth(90)
        self._ai_prog_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ai_prog_row.addWidget(self._ai_prog_lbl)
        pipe_lay.addLayout(ai_prog_row)

        # Metrics + pipeline chips row
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self._count_lbl = QLabel("—")
        self._count_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 20px; font-weight: bold;")
        top_row.addWidget(self._count_lbl)
        self._items_suffix = QLabel(tr("items"))
        self._items_suffix.setObjectName("Dim")
        self._items_suffix.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px; padding-bottom: 2px;")
        top_row.addWidget(self._items_suffix)

        self._size_lbl = QLabel("")
        self._size_lbl.setObjectName("Dim")
        self._size_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px; padding-bottom: 2px;")
        top_row.addWidget(self._size_lbl)
        top_row.addSpacing(14)

        # Pipeline chips
        self._chips_container = QHBoxLayout()
        self._chips_container.setSpacing(6)
        self._chips: list[_PipelineChip] = []
        self._rebuild_chips()
        top_row.addLayout(self._chips_container)
        top_row.addStretch()

        # Elapsed
        time_col = QVBoxLayout()
        time_col.setSpacing(0)
        self._elapsed_hdr = QLabel(tr("ELAPSED"))
        self._elapsed_hdr.setObjectName("Muted")
        self._elapsed_hdr.setStyleSheet("font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 7px; letter-spacing: 1px;")
        self._elapsed_hdr.setAlignment(Qt.AlignRight)
        time_col.addWidget(self._elapsed_hdr)
        self._elapsed_lbl = QLabel("00:00:00")
        self._elapsed_lbl.setObjectName("Dim")
        self._elapsed_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px; font-weight: 500;")
        self._elapsed_lbl.setAlignment(Qt.AlignRight)
        time_col.addWidget(self._elapsed_lbl)
        self._current_path_lbl = QLabel("")
        self._current_path_lbl.setObjectName("Muted")
        self._current_path_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px;")
        self._current_path_lbl.setAlignment(Qt.AlignRight)
        time_col.addWidget(self._current_path_lbl)
        top_row.addLayout(time_col)

        pipe_lay.addLayout(top_row)
        layout.addWidget(pipe_panel)

        # ─── Two-column: Partial Findings | Operator Feed ───
        two_col = QHBoxLayout()
        two_col.setSpacing(10)

        # Left panel: Partial Findings
        pf_panel = Panel()
        pf_lay = pf_panel.with_layout(vertical=True, margins=(12, 10, 12, 10), spacing=6)

        self._pf_hdr_frame = QFrame()
        self._pf_hdr_frame.setFixedHeight(24)
        pf_hdr = QHBoxLayout(self._pf_hdr_frame)
        pf_hdr.setContentsMargins(0, 0, 0, 0)
        pf_hdr.setSpacing(8)
        pf_title = QLabel(tr("PARTIAL FINDINGS"))
        pf_title.setStyleSheet("font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 9px; letter-spacing: 2px;")
        pf_hdr.addWidget(pf_title)
        self._pf_sub = QLabel(tr("// Waiting for scan"))
        self._pf_sub.setObjectName("Muted")
        self._pf_sub.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px;")
        pf_hdr.addWidget(self._pf_sub)
        pf_hdr.addStretch()
        self._pf_count = QLabel("")
        self._pf_count.setObjectName("Dim")
        self._pf_count.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
        pf_hdr.addWidget(self._pf_count)
        pf_lay.addWidget(self._pf_hdr_frame)
        self._pf_hdr_sep = QFrame()
        self._pf_hdr_sep.setFixedHeight(1)
        pf_lay.addWidget(self._pf_hdr_sep)

        columns = [(tr("CATEGORY"), -1), (tr("ITEMS"), 70), (tr("SIZE"), 90)]
        self._pf_table = create_table(columns, 0)
        self._pf_table.setMinimumHeight(200)
        # Make rows clickable with unified row hover/selection.
        self._pf_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._pf_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._pf_table.setCursor(Qt.PointingHandCursor)
        self._pf_table.setMouseTracking(True)
        self._pf_table.viewport().setMouseTracking(True)
        self._pf_table.setFocusPolicy(Qt.NoFocus)
        self._pf_table.setAlternatingRowColors(False)
        self._pf_table.setStyleSheet(
            "QTableWidget { border: none; background: transparent; }"
            "QTableWidget::item { border: none; padding: 0px 10px; }"
            "QTableWidget::item:hover { background: transparent; }"
            "QTableWidget::item:selected { background: transparent; }"
            "QTableWidget::item:focus { outline: none; }"
        )
        self._pf_table.viewport().installEventFilter(self)
        self._pf_table.cellEntered.connect(self._on_partial_cell_hover)
        self._pf_table.cellClicked.connect(self._on_category_row_clicked)
        pf_lay.addWidget(self._pf_table)

        two_col.addWidget(pf_panel, stretch=1)

        # Right panel: Operator Feed
        feed_panel = Panel()
        feed_lay = feed_panel.with_layout(vertical=True, margins=(12, 10, 12, 10), spacing=6)

        self._of_hdr_frame = QFrame()
        self._of_hdr_frame.setFixedHeight(24)
        of_hdr = QHBoxLayout(self._of_hdr_frame)
        of_hdr.setContentsMargins(0, 0, 0, 0)
        of_hdr.setSpacing(8)
        of_title = QLabel(tr("OPERATOR FEED"))
        of_title.setStyleSheet("font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 9px; letter-spacing: 2px;")
        of_hdr.addWidget(of_title)
        of_sub = QLabel(tr("// stdout"))
        of_sub.setObjectName("Muted")
        of_sub.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px;")
        of_hdr.addWidget(of_sub)
        of_hdr.addStretch()
        self._btn_retry_ai = QPushButton(tr("Retry failed"))
        self._btn_retry_ai.setObjectName("Ghost")
        self._btn_retry_ai.setCursor(Qt.PointingHandCursor)
        self._btn_retry_ai.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        self._btn_retry_ai.setVisible(False)
        self._btn_retry_ai.clicked.connect(self._retry_failed_ai)
        of_hdr.addWidget(self._btn_retry_ai)
        self._ai_queue_lbl = QLabel("")
        self._ai_queue_lbl.setObjectName("Dim")
        self._ai_queue_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
        of_hdr.addWidget(self._ai_queue_lbl)
        feed_lay.addWidget(self._of_hdr_frame)
        self._of_hdr_sep = QFrame()
        self._of_hdr_sep.setFixedHeight(1)
        feed_lay.addWidget(self._of_hdr_sep)

        self._feed = OperatorFeed(show_header=False)
        feed_lay.addWidget(self._feed)

        two_col.addWidget(feed_panel, stretch=1)

        layout.addLayout(two_col, stretch=1)

        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._apply_panel_header_frames()
        self._refresh_folder_button()
        self._refresh_header_meta()
        self._refresh_idle_telemetry()

    # ─── Helpers ──────────────────────────────────────────────

    def _current_scan_mode(self) -> str:
        return self._mode_combo.currentData() or "smart"

    def _apply_panel_header_frames(self):
        from app.themes.theme_manager import get_palette
        border = get_palette().get("border", "#213028")
        for frame in (self._pf_hdr_frame, self._of_hdr_frame):
            frame.setStyleSheet(
                "background: transparent; border: none;"
            )
        sep_qss = f"background: {border}; border: none;"
        self._pf_hdr_sep.setStyleSheet(sep_qss)
        self._of_hdr_sep.setStyleSheet(sep_qss)

    def _rebuild_chips(self):
        """Rebuild pipeline chips to match the current scan mode."""
        # Clear existing
        for chip in self._chips:
            self._chips_container.removeWidget(chip)
            chip.deleteLater()
        self._chips.clear()

        stages = _get_stages_smart() if self._current_scan_mode() == "smart" else _get_stages_all()
        for label, _ in stages:
            chip = _PipelineChip(label, "idle")
            self._chips.append(chip)
            self._chips_container.addWidget(chip)

    def _on_mode_changed(self, _index):
        self._rebuild_chips()
        self._refresh_header_meta()

    @staticmethod
    def _compact_text(text: str, max_len: int) -> str:
        if len(text) <= max_len:
            return text
        if max_len <= 3:
            return text[:max_len]
        return f"{text[:max_len - 1]}…"

    @staticmethod
    def _compact_path_display(path: str, max_len: int = 32) -> str:
        clean = path.replace("\\", "/").rstrip("/")
        if len(clean) <= max_len:
            return clean
        parts = [p for p in clean.split("/") if p]
        if len(parts) >= 3:
            root = parts[0]
            tail = "/".join(parts[-2:])
            candidate = f"{root}/.../{tail}"
            if len(candidate) <= max_len:
                return candidate
        keep = max(8, max_len // 2 - 2)
        return f"{clean[:keep]}...{clean[-keep:]}"

    def _format_folder_button_text(self) -> str:
        if not self._selected_folder:
            return tr("Select folder...")
        return self._compact_path_display(self._selected_folder, 34)

    def _refresh_folder_button(self):
        self._btn_folder.setText(self._format_folder_button_text())
        self._btn_folder.setToolTip(self._selected_folder or tr("Choose a scan target folder."))
        self._apply_target_field_style()

    def _header_status_meta_text(self) -> str:
        """Quiet sub-detail below the status badge.

        The badge carries the single operational state (Idle / Scanning /
        AI Active / Complete / …). This line never repeats that label — it
        shows only live AI queue counts, and is blank otherwise.
        """
        done, total, active, failed = self._last_ai_snapshot
        pending = max(total - done - active - failed, 0)
        if self._pipeline_state == "ai_classifying" and (active > 0 or pending > 0):
            parts = []
            if active > 0:
                parts.append(f"{active} active")
            parts.append(f"{pending} pending")
            return self._compact_text(" · ".join(parts), 24)
        return ""

    def _refresh_header_meta(self):
        return

    def _refresh_idle_telemetry(self):
        is_idle = self._pipeline_state == "idle"
        self._scan_prog_lbl.setVisible(not is_idle)
        self._ai_prog_lbl.setVisible(not is_idle)
        self._elapsed_hdr.setVisible(not is_idle)
        self._elapsed_lbl.setVisible(not is_idle)
        self._current_path_lbl.setVisible(not is_idle)
        self._count_lbl.setVisible(not is_idle)
        self._items_suffix.setVisible(not is_idle)
        self._size_lbl.setVisible(not is_idle)
        if is_idle:
            self._current_path_lbl.clear()
            self._scan_prog_lbl.clear()
            self._ai_prog_lbl.clear()

    def focus_target_picker(self):
        """Move keyboard focus to the target selector."""
        self._btn_folder.setFocus(Qt.FocusReason.TabFocusReason)

    def prepare_new_scan(self):
        """Reset Analyze into a ready state for picking a fresh target."""
        if self._scan_state and not self._scan_state.is_running:
            self._scan_state.clear()
        self._selected_folder = ""
        self._scan_roots = []
        self._resuming = False
        self._pipeline_state = "idle"
        self._filesystem_complete = False
        self._grouping_complete = False
        self._ai_complete = False
        self._last_ai_snapshot = (0, 0, 0, 0)
        self._sub.setText(tr("Select a folder target to begin"))
        self._btn_scan.setEnabled(False)
        self._btn_scan.setText(tr("Start scan"))
        self._refresh_folder_button()
        self._update_drive_readout()
        self._refresh_header_meta()
        self._refresh_idle_telemetry()
        self.focus_target_picker()

    def eventFilter(self, obj, event):
        if obj is self._pf_table.viewport():
            if event.type() == QEvent.Leave:
                self._hover_row = -1
                self._refresh_partial_table_row_styles()
            elif event.type() == QEvent.MouseMove:
                try:
                    pos = event.position().toPoint()
                except AttributeError:
                    pos = event.pos()
                index = self._pf_table.indexAt(pos)
                row = index.row() if index.isValid() else -1
                if row != self._hover_row:
                    self._hover_row = row
                    self._refresh_partial_table_row_styles()
        return super().eventFilter(obj, event)

    def _on_partial_cell_hover(self, row: int, _col: int):
        if row != self._hover_row:
            self._hover_row = row
            self._refresh_partial_table_row_styles()

    def _refresh_partial_table_row_styles(self):
        if not hasattr(self, "_pf_table") or self._pf_table is None:
            return
        from app.themes.theme_manager import get_palette
        p = get_palette()
        # panel_hover is a visibly distinct surface; tint_bg was so close to the
        # panel background that the hover highlight was imperceptible.
        hover_brush = QBrush(QColor(p.get("panel_hover", "#1d2c25")))
        selected_brush = QBrush(QColor(p.get("accent_soft", "#1b2e22")))
        transparent = QBrush(Qt.transparent)

        for row in range(self._pf_table.rowCount()):
            if row == self._selected_pf_row:
                brush = selected_brush
            elif row == self._hover_row:
                brush = hover_brush
            else:
                brush = transparent

            # Also tint the vertical header section so the hover reads as one
            # continuous row surface across the whole table width.
            vh_item = self._pf_table.verticalHeaderItem(row)
            if vh_item is None:
                from PySide6.QtWidgets import QTableWidgetItem
                vh_item = QTableWidgetItem("")
                self._pf_table.setVerticalHeaderItem(row, vh_item)
            vh_item.setBackground(brush)
            for col in range(self._pf_table.columnCount()):
                item = self._pf_table.item(row, col)
                if item is not None:
                    item.setBackground(brush)

    # ─── Actions ─────────────────────────────────────────────

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select scan target", "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if folder:
            self._selected_folder = folder
            self._scan_roots = []  # single-folder scan — clear any all-drives selection
            self._sub.setText(tr("Folder selected · choose mode and start"))
            self._btn_scan.setEnabled(True)
            self._refresh_folder_button()
            self._refresh_header_meta()
            self._update_drive_readout()
            self._feed.add_line(f"[scan] target selected: {folder}")

    def _pick_all_drives(self):
        """Select every fixed drive as the scan target."""
        from app.services import drives
        from app.models.finding import _format_size
        fixed = [d for d in drives.list_drives() if d.kind == "Fixed"]
        if not fixed:
            # No psutil / no classifiable drives — fall back to the current drive.
            self._feed.add_line("[scan] no fixed drives detected — pick a folder instead")
            self._sub.setText(tr("Could not detect fixed drives · choose a folder"))
            return
        self._scan_roots = [d.mountpoint for d in fixed]
        self._selected_folder = tr("All drives")
        total = sum(d.total for d in fixed)
        used = sum(d.used for d in fixed)
        letters = ", ".join(d.mountpoint.rstrip("\\/") for d in fixed)
        self._sub.setText(tr("All fixed drives selected · choose mode and start"))
        self._btn_scan.setEnabled(True)
        self._refresh_folder_button()
        self._refresh_header_meta()
        self._drive_lbl.setText(tr(
            "{drives} · {count} fixed drives · {used} used of {total}",
            drives=letters, count=len(fixed),
            used=_format_size(used), total=_format_size(total),
        ))
        self._drive_lbl.setVisible(True)
        self._feed.add_line(f"[scan] target: all fixed drives ({letters})")

    def _update_drive_readout(self):
        """Show the target drive's type and capacity (psutil-backed, optional)."""
        if not self._selected_folder:
            self._drive_lbl.clear()
            self._drive_lbl.setVisible(False)
            return
        from app.services import drives
        from app.models.finding import _format_size
        info = drives.summarize(self._selected_folder)
        if not info:
            self._drive_lbl.setVisible(False)
            return
        drive = info.mountpoint.rstrip("\\/") or info.mountpoint
        self._drive_lbl.setText(tr(
            "{drive} · {kind} · {free} free of {total}",
            drive=drive, kind=tr(info.kind),
            free=_format_size(info.free), total=_format_size(info.total),
        ))
        self._drive_lbl.setVisible(True)

    def _on_scan_btn(self):
        """Toggle between Start and Stop."""
        if self._scan_active:
            self._stop_scan()
        else:
            self._start_scan()

    def _start_scan(self):
        if not self._selected_folder or not self._scan_state:
            return

        from app.services.scanner import ScanWorker

        # Set scan mode
        mode = self._current_scan_mode()
        if not self._resuming:
            self._scan_state.set_scan_mode(mode)
        self._rebuild_chips()

        # Reset UI — skip clear when resuming (state already restored)
        if not self._resuming:
            self._scan_state.clear()
            # Fresh scan: set run_mode to "new" for clean AI state
            self._scan_state._run_mode = "new"
            # Drop the previous scan's category rows right away so they don't
            # linger on screen until the new scan produces entities.
            self._pf_table.setRowCount(0)
            self._selected_pf_row = -1
            self._pf_count.setText("")
        else:
            # Resume: set run_mode to "resume" to allow cache reuse
            self._scan_state._run_mode = "resume"
        # Capture restored count before resetting UI — for resume the counter
        # should start at the previously-found count, not at 0.
        self._scan_offset = self._scan_state.total_count if self._scan_state else 0

        self._resuming = False
        # Tell ScanState the root(s) so entity detection partitions per drive.
        # Set after clear() (which resets roots) so an all-drives scan keeps them.
        self._scan_state.set_scan_roots(self._scan_roots or [self._selected_folder])
        self._scan_state.set_running(True, self._selected_folder)
        self._set_badge(tr("Scanning"), "running")

        self._scan_active = True
        self._entity_processed = 0
        self._entity_total = 0
        self._last_ai_snapshot = (0, 0, 0, 0)

        # Initialize pipeline state
        self._pipeline_state = "scanning_filesystem"
        self._filesystem_complete = False
        self._grouping_complete = False
        self._ai_complete = False
        self._last_ai_snapshot = (0, 0, 0, 0)

        # Finalization tracking (fail-safe against infinite finalizing)
        self._finalization_started = 0.0
        self._finalization_warned = False

        self._update_scan_button_state()  # Set to Stop (red)
        self._refresh_idle_telemetry()
        self._btn_folder.setEnabled(False)
        self._mode_combo.setEnabled(False)
        # When scanning a whole drive we know the denominator up front (on-disk
        # used space), so the scan bar can show a real % by size. For sub-folder
        # targets the volume's used space is meaningless → stay indeterminate.
        self._scan_byte_budget = self._compute_byte_budget(self._selected_folder)
        if self._scan_byte_budget > 0:
            _set_determinate(self._scan_bar, 0, "#7cc596")
            self._scan_prog_lbl.setText("0%")
        else:
            _set_indeterminate(self._scan_bar, "#7cc596")
            self._scan_prog_lbl.setText("...")
        self._ai_bar.setValue(0)
        self._ai_prog_lbl.setText("0%")
        self._ai_queue_lbl.setText("")
        count_start = f"{self._scan_offset:,}" if self._scan_offset else "0"
        self._count_lbl.setText(count_start)
        self._size_lbl.setText("")
        self._pf_sub.setText(tr("// Updating live"))
        mode_label = tr("Adaptive scan running") if mode == "smart" else tr("Full scan running")
        self._sub.setText(mode_label)
        self._refresh_header_meta()

        # Set pipeline chips
        for chip in self._chips:
            chip.set_state("pending")
        self._chips[0].set_state("active")

        # Connect entity detection signals for smart mode
        if mode == "smart":
            try:
                self._scan_state.entities_ready.connect(self._on_entities_ready)
                self._scan_state.entity_progress.connect(self._on_entity_progress)
            except RuntimeError:
                pass

        # Start elapsed timer
        self._start_time = time.time()
        self._elapsed_timer.start()

        # Connect throttled UI refresh
        self._scan_state.ui_refresh.connect(self._on_ui_refresh)

        # Connect scan_state log lines to feed (once)
        if not self._state_log_connected:
            self._scan_state.log_line.connect(self._on_state_log)
            self._state_log_connected = True

        # Create and start worker (pass known paths for resume dedup).
        skip = set(self._scan_state.known_paths) if self._scan_state else set()
        store = getattr(self._scan_state, "_settings_store", None)
        cross_volumes = bool(store.get("scan_cross_volumes", False)) if store else False
        # On resume, hand the worker the frontier from the interrupted run so it
        # continues from there instead of re-walking the whole tree. The frontier
        # is only populated by restore_from_session (cleared on a fresh clear()),
        # so it's a reliable resume signal even after self._resuming was reset.
        # Consume it so a later fresh scan can't inherit a stale frontier.
        resume_stack = list(self._scan_state.resume_frontier) if self._scan_state else []
        if self._scan_state:
            self._scan_state._resume_frontier = []
        # Parented to this screen so the app-close shutdown sweep (which finds
        # live workers via findChildren(QThread)) can halt and wait for it. An
        # unparented scan thread is invisible there and would still be running
        # when the process tears down — the exact "QThread: Destroyed while
        # thread is still running" abort that sweep exists to prevent.
        # Multi-root only for "Scan all drives"; a single folder passes roots=None
        # so the worker behaves exactly as before.
        roots = self._scan_roots if len(self._scan_roots) > 1 else None
        self._worker = ScanWorker(self._selected_folder, skip_paths=skip,
                                  cross_volumes=cross_volumes,
                                  resume_stack=resume_stack,
                                  roots=roots,
                                  parent=self)
        self._worker.batch_ready.connect(self._on_batch)
        self._worker.progress.connect(self._on_progress)
        self._worker.log.connect(self._on_worker_log)
        self._worker.skipped.connect(self._on_skipped_batch)
        self._worker.finished_scan.connect(self._on_scan_finished)
        self._worker.frontier_update.connect(self._scan_state.on_frontier_update)
        self._worker.start()

    def _stop_scan(self):
        """Full stop: cancel filesystem scan + entity detection + AI queue, preserve partial results."""
        self._feed.add_line("[scan] stop requested")
        
        # Set pipeline state to stopped
        self._pipeline_state = "stopped"
        self._filesystem_complete = False
        self._grouping_complete = False
        self._ai_complete = False
        
        # CRITICAL: Stop the elapsed timer immediately when user stops
        self._elapsed_timer.stop()
        self._ai_poll.stop()
        
        if self._worker:
            self._worker.halt()
        if self._dup_worker:
            self._dup_worker.halt()
        if self._scan_state:
            self._scan_state.stop_all()
        
        # Disconnect entity detection signals so late-firing callbacks don't confuse stopped state
        if self._scan_state:
            for sig, slot in [
                (self._scan_state.entities_ready,    self._on_entities_ready),
                (self._scan_state.entity_progress,   self._on_entity_progress),
            ]:
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass

        # Reset AI connection flag so next scan reconnects signals properly
        self._ai_connected = False

        # Reset scan button and stop the progress bar animation immediately.
        # _btn_folder stays disabled until _on_scan_finished confirms the worker
        # has exited — opening the folder dialog while the worker thread is still
        # alive causes a freeze on slow/large drives.
        self._reset_scan_button()
        self._btn_scan.setEnabled(True)
        _set_determinate(self._scan_bar, 0, "#8a9b8f")
        self._scan_prog_lbl.setText("stopped")
        self._refresh_idle_telemetry()
        self._refresh_header_meta()

    def _set_badge(self, text: str, variant: str):
        """Update the status badge in place without changing layout."""
        self._refresh_header_meta()

    def _update_scan_button_state(self):
        """Set button to Stop state (red) during active scan."""
        self._btn_scan.setText(tr("Stop scan"))
        self._btn_scan.setObjectName("Danger")
        self._btn_scan.setStyleSheet("")
        self._btn_scan.setEnabled(True)
        self._refresh_header_meta()

    def _reset_scan_button(self):
        """Reset button to Start state (green) when scan is complete."""
        self._scan_active = False
        self._btn_scan.setText(tr("Start scan"))
        self._btn_scan.setObjectName("Primary")
        self._btn_scan.setStyleSheet("")  # Reset to theme default
        self._btn_scan.setEnabled(True)
        self._refresh_idle_telemetry()
        self._refresh_header_meta()

    def _on_entity_progress(self, phase: str, grouped_files: int, ungrouped_files: int, entities_created: int, coverage_pct: int = 0):
        """Handle entity detection progress updates.

        Uses real grouped_files / total_discovered_files for the progress bar
        — no artificial weighting. The bar switches from indeterminate to
        determinate as soon as entity detection begins.
        """
        if not self._scan_active:
            return

        total_files = self._scan_state.total_count if self._scan_state else 0

        _phase_labels = {
            "started":          "Detecting applications…",
            "known_dirs":       "Detecting known directories…",
            "applications":     "Detecting applications…",
            "browser_profiles": "Grouping browser profiles…",
            "cache_folders":    "Grouping cache and temp folders…",
            "protected_paths":  "Grouping system files…",
            "content_grouping": "Grouping media folders…",
            "grouping":         "Grouping remaining folders…",
            "unknown_sweep":    "Sweeping remaining folders…",
            "complete":         "Grouping complete",
        }
        stage_text = _phase_labels.get(phase, "Grouping storage…")

        if phase == "complete":
            self._grouping_complete = True
            _set_determinate(self._scan_bar, 100, "#7cc596")
            self._scan_prog_lbl.setText("100%")
            self._chips[2].set_state("done", f"{entities_created} entities")
            self._feed.add_line(f"[smart] Entity Detection complete · {entities_created} entities · {coverage_pct}% coverage")
        else:
            # Real progress: grouped_files out of total discovered files
            if total_files > 0:
                real_pct = min(99, int(100 * grouped_files / total_files))
                _set_determinate(self._scan_bar, real_pct, "#7cc596")
                count_label = f"{grouped_files:,} / {total_files:,}"
                self._scan_prog_lbl.setText(f"{real_pct}%")
            else:
                # Total not yet known — stay indeterminate
                _set_indeterminate(self._scan_bar, "#7cc596")
                count_label = f"{grouped_files:,} grouped"
                self._scan_prog_lbl.setText("...")

            chip_detail = f"{stage_text} · {count_label} · {entities_created} entities"
            self._chips[2].set_state("active", chip_detail)

    # ─── Worker callbacks ────────────────────────────────────

    def _on_batch(self, findings):
        if self._scan_state:
            self._scan_state.add_findings(findings)

    def _compute_byte_budget(self, target: str) -> int:
        """On-disk used bytes of the target volume — but only for a drive root.

        Returns total-minus-free for the volume when *target* is a drive root
        (e.g. C:\\), which is the natural denominator for a "% of the disk
        scanned" bar. For sub-folder targets the whole-volume usage is not a
        meaningful denominator, so return 0 and the bar stays indeterminate.
        """
        try:
            tail = os.path.splitdrive(os.path.normpath(target))[1]
            if tail not in ("", "\\", "/", os.sep):
                return 0  # sub-folder target — no meaningful total
            import shutil
            return shutil.disk_usage(target).used
        except (OSError, ValueError):
            return 0

    def _update_scan_size_progress(self):
        """Drive the scan bar by on-disk bytes scanned vs. the volume's used
        space. Clamped to 99% (we legitimately skip protected/system space, so
        it never naturally reaches 100); the final snap to 100 happens when the
        filesystem phase completes.
        """
        budget = getattr(self, "_scan_byte_budget", 0)
        if budget <= 0 or not self._scan_state:
            return
        scanned = self._scan_state.total_size
        pct = min(99, int(100 * scanned / budget))
        _set_determinate(self._scan_bar, pct, "#7cc596")
        self._scan_prog_lbl.setText(f"{pct}%")

    def _on_ui_refresh(self):
        """Called by ScanState throttle timer (~400ms) — safe to update UI."""
        self._update_partial_table()
        # Update live scan metrics on chips
        if self._scan_state:
            count = self._scan_state.total_count
            size = self._scan_state.total_size_str
            self._count_lbl.setText(f"{count:,}")
            self._size_lbl.setText(f"· {size}")
        # Move the scan bar by size while the filesystem walk is running.
        if self._scan_active and not self._grouping_complete:
            self._update_scan_size_progress()

    def _on_progress(self, count, current_path):
        """Filesystem discovery progress — total is unknown, so bar stays indeterminate.

        We show live discovery counts and the current path instead of a fake percentage.
        count is new files found by the worker; _scan_offset is the restored baseline.
        """
        total = count + getattr(self, "_scan_offset", 0)
        self._count_lbl.setText(f"{total:,}")
        short = current_path[-55:] if len(current_path) > 55 else current_path
        self._current_path_lbl.setText(short)

        # Drive-root scans show a real % by size; sub-folder scans have no
        # known total, so the bar stays indeterminate and we show the count.
        if getattr(self, "_scan_byte_budget", 0) > 0:
            self._update_scan_size_progress()
        else:
            self._scan_prog_lbl.setText(f"{total:,}")

        # Chip transitions
        if count > 0 and self._chips[0]._state == "active":
            self._chips[0].set_state("done", f"{total:,}")
            self._chips[1].set_state("active")
        elif count > 0 and self._chips[1]._state == "active":
            self._chips[1].set_state("active", f"{total:,} items")

        # Log milestones to feed (not every batch)
        if count > 0 and count % 10_000 == 0:
            elapsed = time.time() - self._start_time
            rate = int(count / max(elapsed, 0.01))
            size_str = self._scan_state.total_size_str if self._scan_state else ""
            self._feed.add_line(
                f"[scan] {count:,} items scanned · {size_str} · {rate:,}/s"
            )

    def _on_skipped_batch(self, entries: list):
        """Receive structured skipped/protected entries from ScanWorker."""
        if not self._scan_state or not entries:
            return
        self._scan_state.add_skipped_entries(entries)
        # Emit one feed line per unique reason (no path spam)
        from collections import Counter
        by_reason = Counter(e.get("reason", "Unknown") for e in entries)
        for reason, count in by_reason.items():
            self._feed.add_line(f"[protected] {reason}: {count} item(s) skipped")

    def _on_worker_log(self, line):
        """Log from ScanWorker — show in feed."""
        self._feed.add_line(line)

    def _on_state_log(self, line):
        """Log from ScanState (AI queue etc.) — show in feed."""
        self._feed.add_line(line)

    def _on_scan_finished(self):
        # NOTE: Don't stop elapsed timer here - entity detection may still be running
        # Timer will stop when pipeline truly completes (in _on_entities_ready or _on_ai_queue_finished)
        try:
            self._scan_state.ui_refresh.disconnect(self._on_ui_refresh)
        except RuntimeError:
            pass

        was_stopped = self._scan_state.stopped if self._scan_state else False
        halted = self._scan_state.halted if self._scan_state else False
        count = self._scan_state.total_count if self._scan_state else 0
        size_str = self._scan_state.total_size_str if self._scan_state else "0 B"
        mode = self._current_scan_mode()

        # Finalize scan chips (first two always done)
        self._chips[0].set_state("done", f"{count:,}")
        self._chips[1].set_state("done", f"{count:,}")

        # Filesystem scan complete — but entity detection may still be running
        # Don't set to 100% yet; entity detection phase will take it 30% → 100%
        # Keep current progress (max 30%) until entity detection completes

        # Final scan summary in feed
        elapsed = time.time() - self._start_time
        rate = int(count / max(elapsed, 0.01))
        self._feed.add_line(
            f"[scan] complete · {count:,} items · {size_str} · "
            f"{rate:,}/s · {elapsed:.1f}s"
        )

        if was_stopped:
            self._set_badge(tr("Stopped"), "partial_halted")
            for chip in self._chips[2:]:
                if chip._state not in ("done",):
                    chip.set_state("failed")
        elif halted:
            self._set_badge(tr("Halted"), "partial_halted")
        else:
            # Mark filesystem as complete
            self._filesystem_complete = True
            
            if mode == "smart":
                self._pipeline_state = "grouping_entities"
                self._chips[2].set_state("active", "grouping…")
                self._set_badge(tr("Scanning"), "running")
                self._feed.add_line("[scan] filesystem complete · starting entity grouping…")
                # Switch bar from indeterminate to determinate at 0%
                # Entity detection will drive it to 100% with real file counts
                _set_determinate(self._scan_bar, 0, "#7cc596")
                self._scan_prog_lbl.setText("0%")
            else:
                self._chips[2].set_state("done")
                # All-files mode: scan is fully complete now
                self._filesystem_complete = True
                self._grouping_complete = True  # No grouping in all-files mode
                self._ai_complete = not (self._scan_state and self._scan_state.ai_explainer)  # AI may still run
                self._pipeline_state = "ai_classifying" if not self._ai_complete else "complete"
                _set_determinate(self._scan_bar, 100, "#7cc596")
                self._scan_prog_lbl.setText("100%")
                # Only reset button if AI is not running
                if self._ai_complete:
                    self._reset_scan_button()

        if was_stopped or halted:
            self._sub.setText(tr("Scan stopped · partial results preserved"))
        else:
            self._sub.setText(
                tr("Scan complete · {count:,} items · {size}").format(
                    count=count, size=size_str
                )
            )
        self._pf_sub.setText(tr("// Scan stopped") if was_stopped else (tr("// Scan halted") if halted else tr("// Scan complete")))
        self._refresh_header_meta()

        # Don't reset button here for smart mode — entity detection is still running
        # Button reset happens in:
        #   - _on_entities_ready() for smart mode
        #   - Here for all-files mode (handled above)
        #   - _stop_scan() when user stops
        if mode != "smart" or was_stopped or halted:
            # Only reset button for all-files mode or if stopped/halted
            self._reset_scan_button()
        # For smart mode that completed normally, button stays as "Stop" until entities ready

        if self._scan_state:
            self._scan_state.set_running(False)
            self._update_partial_table()
            self._wire_ai_signals()

            # For smart mode, AI starts after entity detection completes
            # (handled in _on_entities_ready via start_ai_queue).
            ai = self._scan_state.ai_explainer
            if ai and ai.is_running:
                self._activate_ai_ui(mode)
            elif mode != "smart":
                # All-files mode: no entity detection and no AI queue, so
                # neither _on_entities_ready nor _on_ai_queue_finished will ever
                # fire. Finish the pipeline here or the elapsed timer ticks
                # forever and the state stays stuck in "ai_classifying".
                self._chips[3].set_state("done", tr("skipped"))
                self._ai_complete = True
                self._pipeline_state = "complete"
                self._elapsed_timer.stop()
                self._ai_poll.stop()
                if not was_stopped and not halted:
                    self._set_badge(tr("Complete"), "completed")
                    self._scan_state.save_session_final("completed", background_large=True)

        # Re-enable folder/mode controls only after set_running(False) completes
        # so the user can't open the folder dialog while cleanup is still in flight.
        self._btn_folder.setEnabled(True)
        self._mode_combo.setEnabled(True)

        # Hand the finished worker back to Qt. It is parented to this screen, so
        # dropping our reference alone would leave the C++ object (and the
        # findings it still holds) alive for the life of the screen, piling up
        # one dead QThread per scan.
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _wire_ai_signals(self):
        """Connect AI explainer signals (idempotent)."""
        if self._ai_connected:
            return
        ai = self._scan_state.ai_explainer if self._scan_state else None
        if not ai:
            return
        try:
            ai.queue_finished.connect(self._on_ai_queue_finished)
            ai.queue_progress.connect(self._on_ai_progress)
            self._ai_connected = True
        except RuntimeError:
            pass

    def _activate_ai_ui(self, mode: str = None):
        """Set up AI progress display when queue starts."""
        if mode is None:
            mode = self._current_scan_mode()
        self._chips[3].set_state("active")
        self._set_badge(tr("AI Active"), "running")
        self._ai_poll.start()

    def _on_entities_ready(self):
        """Called when entity detection finishes in smart mode."""
        if not self._scan_state:
            return

        entity_count = self._scan_state.entity_count

        self._grouping_complete = True
        self._pipeline_state = "ai_classifying"
        self._chips[2].set_state("done", f"{entity_count} entities")
        _set_determinate(self._scan_bar, 100, "#7cc596")
        self._scan_prog_lbl.setText("100%")
        self._update_partial_table()
        self._feed.add_line(f"[smart] {entity_count} semantic entities ready")

        try:
            self._scan_state.entities_ready.disconnect(self._on_entities_ready)
            self._scan_state.entity_progress.disconnect(self._on_entity_progress)
        except RuntimeError:
            pass

        ai = self._scan_state.ai_explainer
        self._wire_ai_signals()

        if ai and ai.is_running:
            self._pipeline_state = "ai_classifying"
            self._activate_ai_ui("smart")
            self._feed.add_line("[ai] AI classification in progress…")
        elif ai and not ai.is_running:
            self._ai_complete = True
            self._pipeline_state = "complete"
            self._chips[3].set_state("done", tr("skipped"))
            self._set_badge(tr("Complete"), "completed")
            self._elapsed_timer.stop()
            self._feed.add_line(f"[smart] analysis complete · {entity_count} entities")
            self._reset_scan_button()
            self._scan_state.save_session_final("completed", background_large=True)

        self._start_duplicate_detection()

    def _start_duplicate_detection(self):
        """Launch DuplicateDetector in background after entity detection completes."""
        if not self._scan_state:
            return
        from app.services.duplicate_detector import DuplicateDetector
        findings = self._scan_state.findings
        if not findings:
            return
        threshold = 10
        store = getattr(self._scan_state, "_settings_store", None)
        if store:
            threshold = store.get("scan/dedup_threshold_mb", 10)
        self._dup_worker = DuplicateDetector(findings, threshold_mb=threshold, parent=self)
        self._dup_worker.log_line.connect(self._feed.add_line)
        self._dup_worker.group_found.connect(self._on_dup_group_found)
        self._dup_worker.finished.connect(self._on_dup_finished)
        self._dup_worker.start()

    def _on_dup_group_found(self, entity):
        if self._scan_state:
            self._scan_state.add_entities([entity])

    def _on_dup_finished(self, groups: int, reclaimable: int):
        from app.models.finding import _format_size
        if groups:
            self._feed.add_line(
                f"[dedup] {groups} duplicate group{'s' if groups != 1 else ''} · "
                f"{_format_size(reclaimable)} reclaimable"
            )
        # Parented to this screen — release it explicitly so a dead detector
        # (and its findings snapshot) isn't retained until the screen dies.
        if self._dup_worker is not None:
            self._dup_worker.deleteLater()
            self._dup_worker = None

    def _on_ai_queue_finished(self):
        """Called when the AI explanation queue finishes."""
        self._ai_poll.stop()
        if not self._scan_active:
            # An out-of-band queue — e.g. a manual per-item "Ask AI" raised from
            # Findings after the run already completed. Don't re-finalize the
            # pipeline, re-badge it, or re-serialize the whole session.
            self._refresh_header_meta()
            return
        self._tick_ai_progress()  # final update
        self._chips[3].set_state("done")
        self._ai_bar.setValue(100)
        self._ai_prog_lbl.setText("100%")
        
        # Mark AI as complete
        self._ai_complete = True
        
        # Explicit operator feed log
        self._feed.add_line("[ai] AI classification finished")
        
        # Only mark COMPLETE and reset button if grouping is also done
        if self._grouping_complete:
            self._pipeline_state = "complete"
            self._set_badge(tr("Complete"), "completed")
            self._elapsed_timer.stop()  # Stop timer - pipeline truly complete
            self._feed.add_line("[smart] pipeline complete")
            self._reset_scan_button()  # All phases complete - reset button
            if self._scan_state:
                self._scan_state.save_session_final("completed", background_large=True)
        else:
            # Grouping still in progress - don't show COMPLETE yet
            self._feed.add_line("[ai] AI classification finished · waiting for entity grouping...")
        self._refresh_header_meta()
        
        if self._scan_state and self._scan_state.ai_explainer:
            try:
                self._scan_state.ai_explainer.queue_finished.disconnect(self._on_ai_queue_finished)
            except RuntimeError:
                pass
            self._ai_connected = False

    def _retry_failed_ai(self):
        """Re-enqueue entities/findings whose AI explanation failed."""
        if not self._scan_state:
            return
        ai = self._scan_state.ai_explainer
        if not ai:
            return
        entities = getattr(self._scan_state, '_entities', [])
        failed = [e for e in entities if getattr(e, 'ai_status', '') == 'failed']
        if not failed:
            return
        for e in failed:
            e.ai_status = 'none'
            e.ai_explanation = ''
            e.ai_error = ''
        self._scan_state._entity_dict_dirty = True
        self._btn_retry_ai.setVisible(False)
        ai.enqueue_entities(failed)
        if not ai.is_running:
            run_mode = getattr(self._scan_state, '_run_mode', 'new') or 'new'
            ai.start(run_mode=run_mode)
        self._activate_ai_ui()
        self._wire_ai_signals()
        self._ai_poll.start()
        self._feed.add_line(f"[ai] retrying {len(failed)} failed explanation(s)")

    def _tick_elapsed(self):
        elapsed = int(time.time() - self._start_time)
        h = elapsed // 3600
        m = (elapsed % 3600) // 60
        s = elapsed % 60
        self._elapsed_lbl.setText(f"{h:02d}:{m:02d}:{s:02d}")
        self._refresh_header_meta()

    def _on_ai_progress(self, done: int, total: int, active: int, failed: int):
        """Live telemetry from AI queue_progress signal."""
        self._update_ai_display(done, total, active, failed)

    def _tick_ai_progress(self):
        """Poll AI explainer state and update progress bar + telemetry."""
        if not self._scan_state or not self._scan_state.ai_explainer:
            return
        ai = self._scan_state.ai_explainer
        self._update_ai_display(
            ai._total_done, ai._total_queued,
            ai._active_count, ai._total_failed
        )

    def _update_ai_display(self, done: int, total: int, active: int, failed: int):
        """Update AI progress bar and telemetry labels."""
        self._last_ai_snapshot = (done, total, active, failed)
        if total > 0:
            pct = int(100 * done / total)
            self._ai_bar.setValue(pct)
            self._ai_prog_lbl.setText(f"{pct}%")
            pending = total - done - active - failed
            parts = [f"{active} active", f"{max(pending,0)} pending"]
            if failed > 0:
                parts.append(f"{failed} unavailable")
            self._ai_queue_lbl.setText(" · ".join(parts))
            self._chips[3].set_state("active", f"{done}/{total}")
            # Show retry button when there are failures and queue is idle
            self._btn_retry_ai.setVisible(failed > 0 and active == 0)
        else:
            self._ai_prog_lbl.setText("—")
            self._ai_queue_lbl.setText("")
        self._refresh_header_meta()

    def _update_partial_table(self):
        """Update Partial Findings panel with current data."""
        if not self._scan_state:
            return

        mode = self._current_scan_mode()
        entity_count = self._scan_state.entity_count

        # During active smart scan, only show counts — don't rebuild category
        # table. Clear any rows left from a previous scan so stale categories
        # don't linger until the new scan's entities are detected.
        if self._scan_active and mode == "smart" and entity_count == 0:
            total_items = self._scan_state.total_count
            self._pf_table.setRowCount(0)
            self._selected_pf_row = -1
            self._pf_count.setText(f"{total_items:,} items · {self._scan_state.total_size_str}")
            self._pf_sub.setText(tr("// Scanning — categories after entity detection"))
            return

        summary = self._scan_state.category_summary()

        if mode == "smart" and entity_count > 0:
            self._pf_count.setText(
                f"{entity_count} entities · {self._scan_state.total_count:,} raw items · {self._scan_state.total_size_str}"
            )
            self._pf_sub.setText(tr("// Semantic grouping complete"))
        else:
            self._pf_count.setText(f"{self._scan_state.total_count:,} items · {self._scan_state.total_size_str}")
            self._pf_sub.setText(tr("// Raw file mode"))

        cats = sorted(summary.items(), key=lambda x: x[1]["size_bytes"], reverse=True)
        self._pf_table.setRowCount(len(cats))
        if self._selected_pf_row >= len(cats):
            self._selected_pf_row = -1
        for i, (cat, info) in enumerate(cats):
            set_row(self._pf_table, i, [
                cat,
                f"{info['count']:,}",
                _format_size(info["size_bytes"]),
            ], align_right=[1, 2])
        self._refresh_partial_table_row_styles()

    def _on_category_row_clicked(self, row, col):
        """Emit category name when a Partial Findings row is clicked."""
        if row < 0 or row >= self._pf_table.rowCount():
            return
        self._selected_pf_row = row
        self._pf_table.clearSelection()
        self._pf_table.setCurrentCell(-1, -1)
        self._refresh_partial_table_row_styles()
        cat_item = self._pf_table.item(row, 0)
        if cat_item:
            self.category_clicked.emit(cat_item.text())

    # ─── Resume ──────────────────────────────────────────────

    def resume_scan(self, session_data: dict):
        """Resume an interrupted scan from saved session data.

        1. Restore previous findings into ScanState (dedup via known_paths).
        2. Set the target folder.
        3. Start a new scan that will skip already-known paths.
        4. After scan, AI queue picks up only unanalyzed items.
        """
        if not self._scan_state:
            return

        target = session_data.get("target", "")
        mode = session_data.get("scan_mode", "smart")
        if not target:
            return

        self._selected_folder = target
        self._sub.setText(tr("Folder selected · ready to analyze"))
        self._btn_scan.setEnabled(True)
        self._refresh_folder_button()
        self._update_drive_readout()
        self._refresh_header_meta()

        # Set mode in combo
        for i in range(self._mode_combo.count()):
            if self._mode_combo.itemData(i) == mode:
                self._mode_combo.setCurrentIndex(i)
                break

        # Restore state first
        self._scan_state.set_scan_mode(mode)
        self._scan_state.clear()
        self._scan_state.restore_from_session(session_data)

        count = self._scan_state.total_count
        size_str = self._scan_state.total_size_str
        self._feed.add_line(f"[resume] restored {count:,} findings · {size_str}")
        self._feed.add_line(f"[resume] continuing scan on {target}")
        self._update_partial_table()

        # Start a continuation scan — _resuming flag prevents clear()
        self._resuming = True
        self._start_scan()

    def set_target(self, target: str):
        """Pre-fill the scan target (called from History re-run)."""
        self._selected_folder = target
        self._sub.setText(tr("Folder selected · ready to analyze"))
        self._btn_scan.setEnabled(True)
        self._refresh_folder_button()
        self._update_drive_readout()
        self._refresh_header_meta()
        self._feed.add_line(f"[history] re-run target: {target}")
        if self._scan_state:
            self._feed.add_line(
                f"[resume] restored {self._scan_state.total_count:,} findings · {self._scan_state.total_size_str}"
            )
        self._feed.add_line(f"[resume] continuing scan on {target}")
        self._update_partial_table()
        self._resuming = True
        self._start_scan()
