"""FindingDetail — expanded detail widget for a single finding/entity.

Used as a subview inside CategoryDetailView when a table row is expanded.
Not a standalone screen — navigation lives in findings_dashboard.py.
"""

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
)
from PySide6.QtCore import Qt

from app.widgets.pills import Badge
from app.models.finding import _format_size
from app.i18n import tr


class FindingDetail(QFrame):
    """Expanded detail for a finding row."""

    def __init__(self, finding: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("PanelAlt")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)

        # ── Two-column: WHY FLAGGED + RECOMMENDATION ──
        two_col = QHBoxLayout()
        two_col.setSpacing(24)

        # Left: why flagged
        why_col = QVBoxLayout()
        why_col.setSpacing(4)
        why_hdr = QLabel(tr("WHY FLAGGED"))
        why_hdr.setStyleSheet("font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 9px; letter-spacing: 2px;")
        why_hdr.setObjectName("Muted")
        why_col.addWidget(why_hdr)
        why_text = QLabel(finding.get("why", "—"))
        why_text.setWordWrap(True)
        why_text.setStyleSheet("font-size: 13px; line-height: 1.4;")
        why_col.addWidget(why_text)
        two_col.addLayout(why_col, stretch=3)

        # Right: recommendation box
        rec_col = QVBoxLayout()
        rec_col.setSpacing(4)
        rec_hdr = QLabel(tr("RECOMMENDATION"))
        rec_hdr.setStyleSheet("font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 9px; letter-spacing: 2px;")
        rec_hdr.setObjectName("Muted")
        rec_col.addWidget(rec_hdr)

        risk_colors = {"Safe": "#7cc596", "Review": "#d8b46a", "Risk": "#d68a78", "Protected": "#d68a78"}
        rc = risk_colors.get(finding.get("risk", "Safe"), "#7cc596")
        rec_box = QFrame()
        rec_box.setStyleSheet(
            f"border: 1px solid {rc}50; background: transparent; padding: 6px 12px;"
        )
        rec_box_lay = QVBoxLayout(rec_box)
        rec_box_lay.setContentsMargins(10, 6, 10, 6)
        rec_box_lay.setSpacing(2)
        rec_val = QLabel(finding.get("recommendation", "—").upper())
        rec_val.setStyleSheet(
            f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 9px; letter-spacing: 1px; "
            f"color: {rc}; border: none; background: transparent;"
        )
        rec_box_lay.addWidget(rec_val)
        rec_col.addWidget(rec_box)

        rec_info = QLabel(tr("Frees {size}").format(size=finding.get("size", "—")))
        rec_info.setObjectName("Dim")
        rec_info.setStyleSheet("font-size: 11px;")
        rec_col.addWidget(rec_info)
        rec_col.addStretch()
        two_col.addLayout(rec_col, stretch=2)
        layout.addLayout(two_col)

        # ── Metadata row ──
        meta_row = QHBoxLayout()
        meta_row.setSpacing(28)

        if finding.get("is_entity"):
            meta_fields = [
                (tr("TYPE"), finding.get("entity_type_label", "—")),
                (tr("SIZE"), finding.get("size", "—")),
                (tr("FILES"), f'{finding.get("file_count", 0):,}'),
                (tr("FOLDERS"), f'{finding.get("folder_count", 0):,}'),
                (tr("RISK"), finding.get("risk", "—")),
                (tr("CONFIDENCE"), finding.get("confidence", "—")),
            ]
        else:
            meta_fields = [
                (tr("CATEGORY"), finding.get("category", "—")),
                (tr("SIZE"), finding.get("size", "—")),
                (tr("LAST ACCESS"), finding.get("last_access", "—")),
                (tr("MODIFIED"), finding.get("first_seen", "—")),
                (tr("RISK"), finding.get("risk", "—")),
            ]

        for key, value in meta_fields:
            pair = QVBoxLayout()
            pair.setSpacing(1)
            k = QLabel(key)
            k.setObjectName("Muted")
            k.setStyleSheet("font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; letter-spacing: 1px;")
            pair.addWidget(k)
            v = QLabel(str(value))
            v.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
            pair.addWidget(v)
            meta_row.addLayout(pair)
        meta_row.addStretch()
        layout.addLayout(meta_row)

        # ── Source rule ──
        rule_row = QVBoxLayout()
        rule_row.setSpacing(2)
        rule_hdr = QLabel(tr("SOURCE RULE"))
        rule_hdr.setObjectName("Muted")
        rule_hdr.setStyleSheet("font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; letter-spacing: 1px;")
        rule_row.addWidget(rule_hdr)
        rule_val = QLabel(finding.get("source_rule", "—"))
        rule_val.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
        rule_val.setWordWrap(True)
        rule_row.addWidget(rule_val)
        layout.addLayout(rule_row)

        # ── AI Explanation section ──
        ai_row = QVBoxLayout()
        ai_row.setSpacing(4)

        ai_hdr_row = QHBoxLayout()
        ai_hdr_row.setSpacing(8)
        ai_hdr = QLabel(tr("AI EXPLANATION"))
        ai_hdr.setObjectName("Muted")
        ai_hdr.setStyleSheet("font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; letter-spacing: 1px;")
        ai_hdr_row.addWidget(ai_hdr)

        ai_status = finding.get("ai_status", "none")
        status_text = {
            "none": tr("not queued"),
            "disabled": tr("disabled"),
            "pending": tr("queued"),
            "analyzing": tr("analyzing…"),
            "running": tr("analyzing…"),
            "ready": "",
            "done": "",
            "failed": tr("failed"),
            "error": tr("failed"),
            "cancelled": tr("cancelled"),
        }.get(ai_status, "")
        if status_text:
            ai_status_lbl = QLabel(f"· {status_text}")
            ai_status_lbl.setObjectName("Dim")
            ai_status_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
            ai_hdr_row.addWidget(ai_status_lbl)

        if ai_status in ("ready", "done") and finding.get("ai_model"):
            ai_lang = finding.get("ai_language", "")
            label_parts = [finding["ai_model"]]
            if ai_lang:
                label_parts.append(ai_lang)
            model_lbl = QLabel(f"· {' · '.join(label_parts)}")
            model_lbl.setObjectName("Dim")
            model_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
            ai_hdr_row.addWidget(model_lbl)

        ai_hdr_row.addStretch()
        ai_row.addLayout(ai_hdr_row)

        # AI explanation text inside a fixed-height scroll area
        self._ai_text_lbl = QLabel()
        self._ai_text_lbl.setWordWrap(True)
        self._ai_text_lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        if ai_status in ("ready", "done") and finding.get("ai_explanation"):
            self._ai_text_lbl.setText(finding["ai_explanation"])
            self._ai_text_lbl.setStyleSheet("font-size: 12px; line-height: 1.5;")
        elif ai_status in ("failed", "error"):
            err = finding.get("ai_error", "unknown error")
            self._ai_text_lbl.setText(tr("AI explanation failed: {error}").format(error=err))
            self._ai_text_lbl.setStyleSheet(
                "font-family: 'JetBrains Mono'; font-size: 11px; font-style: italic; color: #d68a78;"
            )
        elif ai_status in ("analyzing", "running"):
            self._ai_text_lbl.setText(tr("Analyzing…"))
            self._ai_text_lbl.setStyleSheet(
                "font-family: 'JetBrains Mono'; font-size: 11px; font-style: italic; color: #d8b46a;"
            )
        elif ai_status == "cancelled":
            self._ai_text_lbl.setText(tr("Analysis was cancelled."))
            self._ai_text_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px; font-style: italic;")
        elif ai_status == "disabled":
            self._ai_text_lbl.setText(tr("AI explanations are disabled."))
            self._ai_text_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px; font-style: italic;")
        else:
            self._ai_text_lbl.setText(tr("Queued for analysis…"))
            self._ai_text_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px; font-style: italic;")

        _ai_scroll = QScrollArea()
        _ai_scroll.setWidgetResizable(True)
        _ai_scroll.setFixedHeight(100)
        _ai_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        _ai_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        _ai_scroll.setStyleSheet("border: none; background: transparent;")
        _ai_scroll.setWidget(self._ai_text_lbl)
        ai_row.addWidget(_ai_scroll)
        layout.addLayout(ai_row)

        # ── Action buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_open = QPushButton(tr("Open in Explorer"))
        self._btn_open.setObjectName("Subtle")
        self._btn_open.setStyleSheet("padding: 4px 12px; font-size: 11px;")
        self._btn_open.setCursor(Qt.PointingHandCursor)
        btn_row.addWidget(self._btn_open)

        self._btn_copy = QPushButton(tr("Copy path"))
        self._btn_copy.setObjectName("Subtle")
        self._btn_copy.setStyleSheet("padding: 4px 12px; font-size: 11px;")
        self._btn_copy.setCursor(Qt.PointingHandCursor)
        btn_row.addWidget(self._btn_copy)

        self._btn_rerun_ai = QPushButton(tr("Re-run AI"))
        self._btn_rerun_ai.setObjectName("Ghost")
        self._btn_rerun_ai.setStyleSheet("padding: 4px 12px; font-size: 11px;")
        self._btn_rerun_ai.setCursor(Qt.PointingHandCursor)
        btn_row.addWidget(self._btn_rerun_ai)

        btn_row.addStretch()
        layout.addLayout(btn_row)
