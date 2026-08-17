from __future__ import annotations

"""Home screen — semantic analysis hub.

Shows Analysis session states only (no Quick Cleanup or Startups):
1. NO ANALYSIS YET - empty state with Start Analysis
2. ANALYSIS IN PROGRESS - live scan panel
3. ANALYSIS PAUSED - resume panel with progress
4. ANALYSIS COMPLETE - results panel with Open Findings + semantic summary
"""

import time
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer

from app.widgets.panels import Panel, apply_tactical_label
from app.widgets.pills import Badge
from app.state.session_store import (
    clear_session,
    load_history,
    load_session,
    load_session_by_id,
    load_session_summary,
    load_summary,
)
from app.models.finding import _format_size, split_size
from app.models.risk import normalize_risk, normalized_risk_totals
from app.themes.theme_manager import get_palette, theme_signaller
from app.i18n import tr


# ── Helpers ──────────────────────────────────────────────────────

_MONO  = "font-family: 'JetBrains Mono';"
_PIXEL = "font-family: 'Silkscreen', 'JetBrains Mono';"


def _ts_str(ts: float) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _duration_str(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _display_items(s: dict) -> list[dict]:
    """Prefer semantic entities over raw findings for Home screen metrics."""
    entities = s.get("entities", [])
    return entities if entities else s.get("findings", [])


def _session_display_count(s: dict) -> int:
    if "display_count" in s:
        return int(s.get("display_count", 0) or 0)
    return len(_display_items(s))


def _session_display_unit(s: dict) -> str:
    if s.get("display_unit"):
        return str(s.get("display_unit"))
    return "entities" if s.get("entities") else "files"


def _session_ai_counts(s: dict) -> tuple[int, int]:
    if "ai_ready_count" in s or "ai_total_count" in s:
        ready = int(s.get("ai_ready_count", 0) or 0)
        total = int(s.get("ai_total_count", _session_display_count(s)) or 0)
        return ready, total
    items = _display_items(s)
    ready = sum(1 for it in items if it.get("ai_status") in ("ready", "done"))
    return ready, len(items)


def _session_risk_totals(s: dict) -> dict[str, int]:
    risk_totals = s.get("risk_totals", {})
    if risk_totals:
        return normalized_risk_totals(risk_totals)
    return normalized_risk_totals(_risk_from_items(_display_items(s)))


def _display_mode(mode: str) -> str:
    if mode == "smart":
        return tr("Adaptive")
    if not mode:
        return tr("Adaptive")
    return mode.replace("_", " ").title()


def _risk_from_items(items: list[dict]) -> dict[str, int]:
    """Compute entity-level risk distribution from display items."""
    counts: dict[str, int] = {}
    for it in items:
        r = it.get("risk", "Safe")
        counts[r] = counts.get(r, 0) + 1
    return counts


class _ColorBar(QFrame):
    """Stacked horizontal color bar (safe/review/risk)."""

    def __init__(self, safe_pct, review_pct, risk_pct, parent=None):
        super().__init__(parent)
        self.setFixedHeight(8)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        p = get_palette()
        for pct, key in [(safe_pct, "safe"), (review_pct, "review"), (risk_pct, "risk")]:
            color = p.get(key, {"safe": "#7cc596", "review": "#d8b46a", "risk": "#d68a78"}[key])
            seg = QFrame()
            seg.setFixedHeight(8)
            seg.setStyleSheet(f"background: {color}; border: none;")
            lay.addWidget(seg, stretch=max(int(pct), 1))


# ── Main screen ─────────────────────────────────────────────────

class HomeScreen(QWidget):
    navigate_to = Signal(str)
    resume_requested = Signal(dict)
    start_new_requested = Signal()
    stop_requested = Signal()
    open_findings_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scan_state = None
        self._live_poll = QTimer(self)
        self._live_poll.setInterval(1000)
        self._live_poll.timeout.connect(self._tick_live)
        self._build_ui()

    # ─── Public API ──────────────────────────────────────────

    def set_scan_state(self, scan_state):
        self._scan_state = scan_state
        scan_state.scan_started.connect(self._on_scan_started)
        scan_state.scan_finished.connect(self._on_scan_ended)
        scan_state.scan_halted.connect(self._on_scan_ended)

    def refresh(self):
        self._rebuild_dynamic()

    def _rebuild_styles(self, theme_key: str = ""):
        self.refresh()

    # ─── UI scaffold ─────────────────────────────────────────

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("border: none;")

        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(22, 16, 22, 22)
        self._content_layout.setSpacing(12)

        # ─── Screen header ───
        header = QHBoxLayout()
        header.setSpacing(10)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel(tr("HOME"))
        title.setObjectName("SectionHeader")
        apply_tactical_label(title, font_size=16, letter_spacing=4)
        title_col.addWidget(title)
        self._header_sub = QLabel(tr("Session overview"))
        self._header_sub.setObjectName("Dim")
        self._header_sub.setStyleSheet("font-size: 12px;")
        title_col.addWidget(self._header_sub)
        header.addLayout(title_col, stretch=1)

        self._btn_header_new = QPushButton(tr("Start New Scan"))
        self._btn_header_new.setObjectName("Primary")
        self._btn_header_new.setCursor(Qt.PointingHandCursor)
        self._btn_header_new.setStyleSheet(
            "padding: 8px 16px; font-size: 11px; min-height: 30px;"
        )
        self._btn_header_new.clicked.connect(self._on_start_new_clicked)
        self._btn_header_new.hide()
        header.addWidget(self._btn_header_new)

        self._content_layout.addLayout(header)

        # ─── Dynamic area (rebuilt on navigate) ───
        self._dynamic_container = QVBoxLayout()
        self._dynamic_container.setSpacing(12)
        self._content_layout.addLayout(self._dynamic_container)

        self._content_layout.addStretch()
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._rebuild_dynamic()
        theme_signaller().theme_changed.connect(self._rebuild_styles)

    # ─── Dynamic content rebuild ─────────────────────────────

    def _clear_dynamic(self):
        while self._dynamic_container.count():
            item = self._dynamic_container.takeAt(0)
            w = item.widget()
            if w:
                self._drop(w)
            elif item.layout():
                self._clear_layout(item.layout())

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                self._drop(w)
            elif item.layout():
                self._clear_layout(item.layout())

    @staticmethod
    def _drop(w):
        """Hide before deleting.

        deleteLater() only queues the delete; until it runs, the widget is
        still a visible child sitting at its old geometry, so the outgoing
        state paints on top of the incoming one. Home rebuilds this area on
        every navigation, which made that a flash of two overlaid layouts.

        hide() rather than setParent(None): unparenting also stops the
        painting, but it promotes the widget to a top-level *window*, and
        those turn up on screen as blank frames.
        """
        w.hide()
        w.deleteLater()

    def _rebuild_dynamic(self):
        self._clear_dynamic()
        self._btn_header_new.hide()

        # All-time impact banner sits above every state so the user always
        # sees the cumulative space Vigil has reclaimed for them.
        self._build_lifetime_banner()

        if self._scan_state and self._scan_state.is_running:
            self._build_live_panel()
            previous = self._latest_completed_session()
            if previous:
                self._build_previous_analysis_panel(previous)
            self._update_header_live()
            return

        session = load_session_summary()

        if session:
            status = session.get("status", "completed")
            self._btn_header_new.show()
            if status in ("stopped", "running"):
                self._build_resume_panel(session)
                self._update_header_session(session, paused=True)
            else:
                self._build_completed_panel(session)
                self._update_header_session(session, paused=False)
        else:
            self._build_empty_state()
            self._header_sub.setText(tr("No analysis sessions"))

    # ─── All-time impact banner ──────────────────────────────

    @staticmethod
    def _mini_stat(value: str, label: str) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(2)
        # Number centered over its label so each stat reads as one tidy block.
        v = QLabel(value)
        v.setStyleSheet(f"{_MONO} font-size: 18px; font-weight: bold;")
        v.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
        # A shared minimum width keeps the four stats evenly spaced and stops a
        # wide value (e.g. "3556.1 GB") from crowding its neighbour. Rolling
        # over to TB caps the width the all-time counter can ever reach.
        v.setMinimumWidth(78)
        col.addWidget(v)
        l = QLabel(label)
        l.setObjectName("Muted")
        l.setStyleSheet("font-size: 10px;")
        l.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        l.setMinimumWidth(78)
        col.addWidget(l)
        return col

    def _build_lifetime_banner(self):
        """Cumulative 'all-time impact' card: space freed + usage counts.

        Reads the running totals from summary.json. Hidden on a fresh install
        (nothing freed, scanned, or cleaned yet) so it never shows an empty brag.
        """
        s = load_summary()
        freed    = int(s.get("total_recovered_bytes", 0) or 0)
        scans    = int(s.get("analyze_sessions", 0) or 0)
        cleanups = int(s.get("cleanup_sessions", 0) or 0)
        items    = int(s.get("total_cleanup_items", 0) or 0)
        analyzed = int(s.get("total_scanned_bytes", 0) or 0)
        if not (freed or scans or cleanups):
            return

        accent = get_palette().get("safe", "#7cc596")

        panel = Panel()
        lay = panel.with_layout(vertical=True, margins=(16, 12, 16, 14), spacing=8)
        hdr = QLabel(tr("ALL-TIME IMPACT"))
        hdr.setObjectName("SectionHeader")
        apply_tactical_label(hdr, font_size=9, letter_spacing=2)
        lay.addWidget(hdr)

        row = QHBoxLayout()
        row.setSpacing(20)

        # Hero — total space freed, in the safe/accent colour.
        freed_num, freed_unit = split_size(freed)
        hero = QVBoxLayout()
        hero.setSpacing(0)
        hero_val = QHBoxLayout()
        hero_val.setSpacing(4)
        hero_val.setAlignment(Qt.AlignBottom)
        hero_num = QLabel(freed_num)
        hero_num.setStyleSheet(f"{_MONO} font-size: 34px; font-weight: bold; color: {accent};")
        hero_val.addWidget(hero_num)
        hero_unit = QLabel(freed_unit)
        hero_unit.setObjectName("Dim")
        hero_unit.setStyleSheet(f"{_MONO} font-size: 14px; padding-bottom: 5px;")
        hero_val.addWidget(hero_unit)
        hero.addLayout(hero_val)
        hero_lbl = QLabel(tr("freed all-time"))
        hero_lbl.setObjectName("Dim")
        hero_lbl.setStyleSheet("font-size: 11px;")
        hero.addWidget(hero_lbl)
        row.addLayout(hero)
        row.addStretch()

        for value, label in [
            (f"{scans:,}",            tr("scans")),
            (f"{cleanups:,}",         tr("cleanups")),
            (f"{items:,}",            tr("items removed")),
            (_format_size(analyzed),  tr("analyzed")),
        ]:
            row.addLayout(self._mini_stat(value, label))

        lay.addLayout(row)
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._dynamic_container.addWidget(panel)

    # ─── Header helpers ──────────────────────────────────────

    def _update_header_session(self, s: dict, paused: bool = False):
        target = s.get("target", "")
        ts = _ts_str(s.get("start_time", 0))
        mode = _display_mode(s.get("scan_mode", "smart"))
        ai_ready, ai_total = _session_ai_counts(s)
        ai_complete = ai_total == 0 or ai_ready >= ai_total
        status = tr("Paused") if paused else (tr("Completed") if ai_complete else tr("Analysis Ready"))
        n = _session_display_count(s)
        unit = _session_display_unit(s)
        self._header_sub.setText(f"{status} · {n:,} {unit} · {ts} · {mode} · {target}")

    def _update_header_live(self):
        self._header_sub.setText(tr("Scan in progress…"))

    def _latest_completed_session(self) -> dict | None:
        for record in load_history():
            if record.get("status") == "completed":
                return record
        summary = load_session_summary()
        if summary and summary.get("status") == "completed":
            return summary
        return None

    # ─── Empty state (NO ANALYSIS YET) ──────────────────────

    def _build_empty_state(self):
        self._build_metrics_row(total_size=0, findings=0, ai_ready=0,
                                risk_totals={}, cat_totals={})

        panel = Panel()
        lay = panel.with_layout(vertical=True, margins=(20, 24, 20, 24), spacing=16)

        hdr = QLabel(tr("NO PREVIOUS ANALYSIS"))
        hdr.setStyleSheet(
            f"{_PIXEL} font-size: 11px; letter-spacing: 2px; "
            f"color: {get_palette().get('text_faint', '#57685e')};"
        )
        hdr.setAlignment(Qt.AlignCenter)
        lay.addWidget(hdr)

        msg = QLabel(tr("Start a new analysis to scan a folder and identify storage usage."))
        msg.setObjectName("Dim")
        msg.setStyleSheet("font-size: 12px;")
        msg.setAlignment(Qt.AlignCenter)
        msg.setWordWrap(True)
        lay.addWidget(msg)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()
        btn_analyze = QPushButton(tr("Start Analysis"))
        btn_analyze.setObjectName("Primary")
        btn_analyze.setCursor(Qt.PointingHandCursor)
        btn_analyze.setStyleSheet("padding: 12px 28px; font-size: 13px;")
        btn_analyze.clicked.connect(self._on_start_new_clicked)
        btn_row.addWidget(btn_analyze)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._dynamic_container.addWidget(panel)

    # ─── Metrics row helper ──────────────────────────────────

    def _build_metrics_row(self, total_size: int, findings: int, ai_ready: int,
                           risk_totals: dict, cat_totals: dict,
                           ai_total: int = 0):
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)

        size_str = _format_size(total_size)
        parts = size_str.split(" ", 1)
        size_num = parts[0] if parts else "0"
        size_unit = parts[1] if len(parts) > 1 else "B"

        risk_totals = normalized_risk_totals(risk_totals)
        safe_n   = risk_totals.get("Safe", 0)
        review_n = risk_totals.get("Optional", 0) + risk_totals.get("Review", 0)
        risk_n   = risk_totals.get("Protected", 0)
        total_n  = max(safe_n + review_n + risk_n, 1)

        rc = Panel()
        rc_lay = rc.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=6)
        rc_hdr = QLabel(tr("TOTAL SIZE"))
        rc_hdr.setObjectName("SectionHeader")
        apply_tactical_label(rc_hdr, font_size=9, letter_spacing=2)
        rc_lay.addWidget(rc_hdr)
        rc_val_row = QHBoxLayout()
        rc_val_row.setSpacing(4)
        rc_val_row.setAlignment(Qt.AlignBottom)
        rc_num = QLabel(size_num)
        rc_num.setStyleSheet(f"{_MONO} font-size: 32px; font-weight: bold;")
        rc_val_row.addWidget(rc_num)
        rc_unit = QLabel(size_unit)
        rc_unit.setObjectName("Dim")
        rc_unit.setStyleSheet(f"{_MONO} font-size: 14px; padding-bottom: 4px;")
        rc_val_row.addWidget(rc_unit)
        rc_val_row.addStretch()
        rc_lay.addLayout(rc_val_row)

        if total_size > 0:
            bar = _ColorBar(
                safe_n / total_n * 100,
                review_n / total_n * 100,
                risk_n / total_n * 100,
            )
            rc_lay.addWidget(bar)

            _p = get_palette()
            legend = QHBoxLayout()
            legend.setSpacing(12)
            for lbl, val, color in [
                (tr("Safe"),   str(safe_n),   _p.get("safe",   "#7cc596")),
                (tr("Review"), str(review_n), _p.get("review", "#d8b46a")),
                (tr("Protected"), str(risk_n), _p.get("risk",   "#d68a78")),
            ]:
                dot = QLabel("■")
                dot.setStyleSheet(f"color: {color}; font-size: 8px; background: transparent; border: none;")
                dot.setFixedWidth(10)
                legend.addWidget(dot)
                leg_txt = QLabel(f"{lbl} {val}")
                leg_txt.setObjectName("Muted")
                leg_txt.setStyleSheet("font-size: 10px;")
                legend.addWidget(leg_txt)
            legend.addStretch()
            rc_lay.addLayout(legend)
        rc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        cards_row.addWidget(rc, stretch=2)

        # FINDINGS card
        fc = Panel()
        fc_lay = fc.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=4)
        fc_hdr = QLabel(tr("FINDINGS"))
        fc_hdr.setObjectName("SectionHeader")
        apply_tactical_label(fc_hdr, font_size=9, letter_spacing=2)
        fc_lay.addWidget(fc_hdr)
        fc_num = QLabel(f"{findings:,}")
        fc_num.setStyleSheet(f"{_MONO} font-size: 32px; font-weight: bold;")
        fc_lay.addWidget(fc_num)
        n_cats = len(cat_totals)
        fc_sub = QLabel(tr("across {n_cats} categories", n_cats=n_cats) if n_cats else tr("no categories"))
        fc_sub.setObjectName("Dim")
        fc_sub.setStyleSheet("font-size: 11px;")
        fc_lay.addWidget(fc_sub)
        fc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        cards_row.addWidget(fc, stretch=1)

        # AI PROCESSING card
        ac = Panel()
        ac_lay = ac.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=4)
        ac_hdr = QLabel(tr("AI PROCESSING"))
        ac_hdr.setObjectName("SectionHeader")
        apply_tactical_label(ac_hdr, font_size=9, letter_spacing=2)
        ac_lay.addWidget(ac_hdr)
        # Show progress (ready / total) rather than a bare "0" that reads as
        # "done, nothing explained" while the queue is still running.
        ai_in_progress = ai_total > 0 and ai_ready < ai_total
        ac_num = QLabel(f"{ai_ready:,} / {ai_total:,}" if ai_total else f"{ai_ready:,}")
        ac_num.setStyleSheet(f"{_MONO} font-size: 32px; font-weight: bold;")
        ac_lay.addWidget(ac_num)
        ac_sub = QLabel(tr("analyzing…") if ai_in_progress else tr("explanations ready"))
        ac_sub.setObjectName("Dim")
        ac_sub.setStyleSheet("font-size: 11px;")
        ac_lay.addWidget(ac_sub)
        ac.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        cards_row.addWidget(ac, stretch=1)

        self._dynamic_container.addLayout(cards_row)

    # ─── Completed analysis panel ────────────────────────────

    def _build_completed_panel(self, s: dict):
        total_size = s.get("total_size", 0)
        cat_totals = s.get("category_totals", {})
        risk_totals = _session_risk_totals(s)
        display_count = _session_display_count(s)
        ai_ready, ai_total = _session_ai_counts(s)
        ai_pct     = min(100, int((ai_ready / max(ai_total, 1)) * 100))
        ai_complete = ai_total == 0 or ai_ready >= ai_total

        self._build_metrics_row(
            total_size=total_size,
            findings=display_count,
            ai_ready=ai_ready,
            ai_total=ai_total,
            risk_totals=risk_totals,
            cat_totals=cat_totals,
        )

        panel = Panel()
        lay = panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        h_title = QLabel(tr("ANALYSIS COMPLETE") if ai_complete else tr("ANALYSIS READY"))
        h_title.setStyleSheet(f"{_PIXEL} font-size: 9px; letter-spacing: 2px;")
        hdr.addWidget(h_title)
        hdr.addStretch()
        hdr.addWidget(Badge(tr("COMPLETE") if ai_complete else tr("AI ACTIVE"), "completed" if ai_complete else "running"))
        lay.addLayout(hdr)

        mode = _display_mode(s.get("scan_mode", "smart"))
        start_t  = s.get("start_time", 0)
        last_t   = s.get("last_update", 0)
        duration = _duration_str(last_t - start_t) if (start_t and last_t) else "—"
        count    = s.get("scanned_count", 0)

        _p = get_palette()
        details = QLabel(
            tr("Type: {mode} ANALYSIS", mode=mode.upper()) + "\n" +
            tr("Core folder: {target}", target=s.get("target", "—")) + "\n" +
            tr("Files scanned: {count:,} | Size analyzed: {size}",
               count=count, size=_format_size(s.get("total_size", 0))) + "\n" +
            tr("Duration: {duration}", duration=duration)
        )
        details.setStyleSheet(f"{_MONO} font-size: 11px; color: {_p.get('text_dim', '#8a9b8f')};")
        details.setWordWrap(True)
        lay.addWidget(details)

        prog_grid = QHBoxLayout()
        prog_grid.setSpacing(16)

        scan_lbl = QLabel(tr("SCAN: COMPLETE"))
        scan_lbl.setStyleSheet(f"{_PIXEL} font-size: 9px; color: {_p.get('safe', '#7cc596')};")
        prog_grid.addWidget(scan_lbl)

        ai_progress = tr("COMPLETE") if ai_pct >= 100 else f"{ai_pct}%"
        ai_lbl = QLabel(tr("AI ANALYSIS: {progress}", progress=ai_progress))
        ai_color = (
            _p.get("safe", "#7cc596") if ai_pct >= 100
            else _p.get("review", "#d8b46a") if ai_pct > 50
            else _p.get("risk", "#d68a78")
        )
        ai_lbl.setStyleSheet(f"{_PIXEL} font-size: 9px; color: {ai_color};")
        prog_grid.addWidget(ai_lbl)
        prog_grid.addStretch()
        lay.addLayout(prog_grid)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        btn_open = QPushButton(tr("Open Findings"))
        btn_open.setObjectName("Primary")
        btn_open.setCursor(Qt.PointingHandCursor)
        btn_open.setStyleSheet("padding: 10px 18px; font-size: 12px;")
        btn_open.clicked.connect(lambda: self._on_open_findings(s))
        btn_row.addWidget(btn_open)

        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._build_semantic_summary(lay, s)

        self._dynamic_container.addWidget(panel)

    # ─── Resume panel (ANALYSIS PAUSED) ─────────────────────

    def _build_resume_panel(self, data: dict):
        risk_totals = _session_risk_totals(data)
        cat_totals  = data.get("category_totals", {})
        ai_ready, ai_total = _session_ai_counts(data)

        self._build_metrics_row(
            total_size=data.get("total_size", 0),
            findings=_session_display_count(data),
            ai_ready=ai_ready,
            ai_total=ai_total,
            risk_totals=risk_totals,
            cat_totals=cat_totals,
        )

        panel = Panel()
        lay = panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        h_title = QLabel(tr("RESUME LAST RUN"))
        h_title.setStyleSheet(f"{_PIXEL} font-size: 9px; letter-spacing: 2px;")
        hdr.addWidget(h_title)
        hdr.addStretch()
        status_text = data.get("status", "stopped")
        badge_var   = "partial_halted" if status_text in ("stopped", "running") else "info"
        badge_label = tr("PAUSED") if status_text == "stopped" else tr("INTERRUPTED")
        hdr.addWidget(Badge(badge_label, badge_var))
        lay.addLayout(hdr)

        mode = _display_mode(data.get("scan_mode", "smart"))
        target = data.get("target", "—")
        count = data.get("scanned_count", 0)
        last_update = data.get("last_update", 0)

        info = QLabel(
            tr("Type: {mode} ANALYSIS", mode=mode.upper()) + "\n" +
            tr("Core folder: {target}", target=target) + "\n" +
            tr("Files scanned: {count:,} | Size: {size}",
               count=count, size=_format_size(data.get("total_size", 0))) + "\n" +
            tr("Last update: {timestamp}", timestamp=_ts_str(last_update))
        )
        info.setStyleSheet(f"{_MONO} font-size: 11px; color: {get_palette().get('text_dim', '#8a9b8f')};")
        info.setWordWrap(True)
        lay.addWidget(info)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        btn_resume = QPushButton(tr("Resume Scan"))
        btn_resume.setObjectName("Primary")
        btn_resume.setCursor(Qt.PointingHandCursor)
        btn_resume.setStyleSheet("padding: 10px 18px; font-size: 12px;")
        btn_resume.clicked.connect(lambda: self._on_resume_clicked(data))
        btn_row.addWidget(btn_resume)

        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._dynamic_container.addWidget(panel)

    # ─── Semantic summary panel ──────────────────────────────

    def _build_semantic_summary(self, parent_layout: QVBoxLayout, s: dict):
        """Compact insight row integrated into the completed analysis panel."""
        items = _display_items(s)
        if not items:
            return

        _p = get_palette()

        # Aggregate insights from semantic items
        review_n    = sum(1 for it in items if normalize_risk(it.get("risk")) in ("Review", "Protected"))
        reclaimable = sum(it.get("reclaimable_bytes", 0) for it in items)
        ai_done     = sum(1 for it in items if it.get("ai_status") in ("ready", "done"))

        # Largest category from saved totals or recomputed
        cat_totals = s.get("category_totals", {})
        if not cat_totals and items:
            cs: dict[str, int] = {}
            for it in items:
                cat = it.get("category", "Unknown")
                cs[cat] = cs.get(cat, 0) + it.get("size_bytes", 0)
            cat_totals = {c: {"size_bytes": v} for c, v in cs.items()}

        largest_cat  = max(cat_totals, key=lambda c: cat_totals[c].get("size_bytes", 0)) if cat_totals else None
        largest_size = cat_totals[largest_cat]["size_bytes"] if largest_cat else 0

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {_p.get('border', '#213028')}; border: none;")
        parent_layout.addWidget(divider)

        hdr_lbl = QLabel(tr("SEMANTIC SUMMARY"))
        hdr_lbl.setObjectName("Muted")
        hdr_lbl.setStyleSheet(f"{_PIXEL} font-size: 7px; letter-spacing: 2px;")
        parent_layout.addWidget(hdr_lbl)

        row = QHBoxLayout()
        row.setSpacing(28)

        if largest_cat:
            self._add_stat(row, tr("LARGEST"),
                           f"{tr(largest_cat)} · {_format_size(largest_size)}",
                           _p.get("text", "#d6e2da"))

        if review_n > 0:
            self._add_stat(row, tr("NEEDS REVIEW"),
                           str(review_n),
                           _p.get("review", "#d8b46a"))

        if reclaimable > 0:
            self._add_stat(row, tr("SAFE CLEANUP"),
                           f"~{_format_size(reclaimable)}",
                           _p.get("safe", "#7cc596"))

        self._add_stat(row, "AI",
                       f"{ai_done} / {len(items)} {tr('analyzed')}",
                       _p.get("text_dim", "#8a9b8f"))

        row.addStretch()
        parent_layout.addLayout(row)

    @staticmethod
    def _add_stat(layout: QHBoxLayout, label: str, value: str, color: str):
        """Append a labeled stat pair (vertical) to a horizontal layout."""
        col = QVBoxLayout()
        col.setSpacing(1)
        k = QLabel(label)
        k.setObjectName("Muted")
        k.setStyleSheet(
            "font-family: 'Silkscreen', 'JetBrains Mono'; "
            "font-size: 7px; letter-spacing: 1px;"
        )
        col.addWidget(k)
        v = QLabel(value)
        v.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 11px; font-weight: 500; color: {color};"
        )
        col.addWidget(v)
        layout.addLayout(col)

    # ─── Live scan panel ─────────────────────────────────────

    def _build_live_panel(self):
        panel = Panel()
        lay = panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=8)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        h_title = QLabel(tr("ACTIVE ANALYSIS"))
        h_title.setStyleSheet(f"{_PIXEL} font-size: 9px; letter-spacing: 2px;")
        hdr.addWidget(h_title)
        hdr.addStretch()
        hdr.addWidget(Badge(tr("SCANNING"), "running"))
        lay.addLayout(hdr)

        self._live_target_lbl = QLabel("")
        self._live_target_lbl.setStyleSheet(f"{_MONO} font-size: 12px;")
        self._live_target_lbl.setWordWrap(True)
        lay.addWidget(self._live_target_lbl)

        self._live_stats_lbl = QLabel("")
        self._live_stats_lbl.setObjectName("Dim")
        self._live_stats_lbl.setStyleSheet(f"{_MONO} font-size: 12px;")
        lay.addWidget(self._live_stats_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_goto = QPushButton(tr("Go to Analyze"))
        btn_goto.setObjectName("Primary")
        btn_goto.setCursor(Qt.PointingHandCursor)
        btn_goto.setStyleSheet("padding: 8px 18px; font-size: 12px;")
        btn_goto.clicked.connect(lambda: self.navigate_to.emit("Analyze"))
        btn_row.addWidget(btn_goto)

        btn_stop = QPushButton(tr("Stop analysis"))
        btn_stop.setObjectName("Subtle")
        btn_stop.setCursor(Qt.PointingHandCursor)
        _rp = get_palette()
        btn_stop.setStyleSheet(
            f"padding: 8px 18px; font-size: 12px; "
            f"color: {_rp.get('risk', '#d68a78')}; "
            f"border-color: {_rp.get('risk', '#d68a78')}60;"
        )
        btn_stop.clicked.connect(self._on_stop_clicked)
        btn_row.addWidget(btn_stop)

        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._dynamic_container.addWidget(panel)
        self._tick_live()
        self._live_poll.start()

    def _build_previous_analysis_panel(self, summary: dict):
        panel = Panel(alt=True)
        lay = panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=8)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        h_title = QLabel(tr("LAST COMPLETED ANALYSIS"))
        h_title.setStyleSheet(
            f"{_PIXEL} font-size: 8px; letter-spacing: 2px; color: {get_palette().get('text_dim', '#8a9b8f')};"
        )
        hdr.addWidget(h_title)
        hdr.addStretch()
        hdr.addWidget(Badge(tr("REFERENCE"), "info"))
        lay.addLayout(hdr)

        total_size = int(summary.get("total_size", 0) or 0)
        display_count = _session_display_count(summary)
        display_unit = _session_display_unit(summary)
        start_t = summary.get("start_time", 0)
        saved_t = summary.get("saved_at", 0) or summary.get("last_update", 0)
        mode = _display_mode(summary.get("scan_mode", "smart"))

        details = QLabel(
            tr("Target: {target}", target=summary.get("target", "—")) + "\n"
            + tr("Summary: {count:,} {unit} · {size}", count=display_count, unit=display_unit, size=_format_size(total_size)) + "\n"
            + tr("Completed: {timestamp} · {mode} mode", timestamp=_ts_str(saved_t or start_t), mode=mode)
        )
        details.setObjectName("Dim")
        details.setStyleSheet(f"{_MONO} font-size: 11px; color: {get_palette().get('text_dim', '#8a9b8f')};")
        details.setWordWrap(True)
        lay.addWidget(details)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_history = QPushButton(tr("Open History"))
        btn_history.setObjectName("Subtle")
        btn_history.setCursor(Qt.PointingHandCursor)
        btn_history.setStyleSheet("padding: 7px 14px; font-size: 11px;")
        btn_history.clicked.connect(lambda: self.navigate_to.emit("History"))
        btn_row.addWidget(btn_history)

        session_id = summary.get("session_id", "")
        if session_id:
            btn_findings = QPushButton(tr("Open Findings"))
            btn_findings.setObjectName("Ghost")
            btn_findings.setCursor(Qt.PointingHandCursor)
            btn_findings.setStyleSheet("padding: 7px 14px; font-size: 11px;")
            btn_findings.clicked.connect(lambda checked=False, sid=session_id: self._open_history_session(sid))
            btn_row.addWidget(btn_findings)

        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._dynamic_container.addWidget(panel)

    def _tick_live(self):
        if not self._scan_state or not self._scan_state.is_running:
            self._live_poll.stop()
            return
        target  = self._scan_state.target
        # Use semantic display count — entity count when grouping complete, else raw files
        count   = self._scan_state.display_count()
        size    = self._scan_state.total_size_str
        mode    = _display_mode(self._scan_state.scan_mode)
        elapsed = int(time.time() - self._scan_state.start_time) if self._scan_state.start_time else 0
        unit    = "entities" if self._scan_state.has_entities else "items"

        self._live_target_lbl.setText(tr("Target: {target}", target=target))
        self._live_stats_lbl.setText(tr(
            "{count:,} {unit} · {size} · {mode} mode · {duration}",
            count=count, unit=unit, size=size, mode=mode,
            duration=_duration_str(elapsed)))

    # ─── Signal handlers ─────────────────────────────────────

    def _on_scan_started(self, target):
        self._rebuild_dynamic()

    def _on_scan_ended(self):
        self._live_poll.stop()
        QTimer.singleShot(300, self._rebuild_dynamic)

    def _load_busy(self, fn):
        """Read a session off the UI thread — see BusyDialog for why.

        A completed C:/ scan is a multi-hundred-MB snapshot; reading it inline
        froze the window for long enough to look like a crash.
        """
        from app.widgets.progress import run_busy
        return run_busy(self, tr("Opening session…"), fn)

    def _on_resume_clicked(self, data: dict):
        full = self._load_busy(load_session)
        if full:
            self.resume_requested.emit(full)

    def _on_start_new_clicked(self):
        clear_session()
        self.start_new_requested.emit()

    def _on_stop_clicked(self):
        self.stop_requested.emit()

    def _on_open_findings(self, session_data: dict):
        full = self._load_busy(load_session)
        if full:
            self.open_findings_requested.emit(full)
            self.navigate_to.emit("Findings")

    def _open_history_session(self, session_id: str):
        session = self._load_busy(lambda: load_session_by_id(session_id))
        if session:
            self.open_findings_requested.emit(session)
            self.navigate_to.emit("Findings")
