"""Startups screen — real Windows startup analysis with AI explanations."""
from __future__ import annotations

import os
import subprocess
import threading
import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QLineEdit, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtGui import QColor

from app.models.risk import RISK_ORDER
from app.models.startup_entry import StartupEntry
from app.widgets.controls import ask_ai_button_qss, ElidedLabel
from app.widgets.panels import Panel, apply_tactical_label
from app.widgets.pills import Badge
from app.themes.theme_manager import get_palette, theme_signaller
from app.i18n import tr


_RISK_VARIANT = {
    "Safe": "safe",
    "Optional": "optional",
    "Review": "review",
    "Protected": "protected",
}

_IMPACT_COLOR = {
    "Security component": "#d68a78",
    "Hardware utility": "#d68a78",
    "Background sync": "#7ab8d4",
    "Remote access service": "#7ab8d4",
    "Communication app": "#d8b46a",
    "Game launcher": "#d8b46a",
    "Creative helper": "#8eb4d9",
    "Update helper": "#8a9b8f",
    "Light utility": "#7cc596",
    "Startup item": "#8a9b8f",
}


def _separator() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    border = QColor(get_palette().get("border", "#213028"))
    border.setAlpha(58)
    line.setStyleSheet(
        f"background: rgba({border.red()}, {border.green()}, {border.blue()}, {border.alpha()}); border: none;"
    )
    return line


def _state_pill_style(enabled: bool) -> str:
    p = get_palette()
    fg = p.get("safe", "#7cc596") if enabled else p.get("text_dim", "#8a9b8f")
    bg = p.get("tint_bg", "#0f1914") if enabled else "transparent"
    border = p.get("border_alt", "#2b3d33") if enabled else p.get("border", "#213028")
    return (
        "font-family: 'JetBrains Mono'; font-size: 9px; letter-spacing: 1px; "
        f"padding: 2px 7px; border-radius: 2px; border: 1px solid {border}; "
        f"color: {fg}; background: {bg};"
    )


_HIGH_BOOT_IMPACT_ROLES = {
    "Background sync",
    "Remote access service",
    "Communication app",
    "Game launcher",
    "Creative helper",
    "Update helper",
}


def _startup_unknown_publisher(entry: StartupEntry) -> bool:
    publisher = (entry.publisher or "").strip().lower()
    return not publisher or publisher in {"unknown", "unknown publisher"}


def _startup_high_boot_impact(entry: StartupEntry) -> bool:
    impact = (entry.impact or "").strip()
    impact_l = impact.lower()
    return (
        "high" in impact_l
        or "heavy" in impact_l
        or impact in _HIGH_BOOT_IMPACT_ROLES
    )


def _rgba(hex_color: str, alpha: int) -> str:
    color = QColor(hex_color)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


# Gap between the badge and action columns, and the same gap the date spans.
_RAIL_GAP = 8


# A startup target older than this reads as a leftover rather than something
# in use. Three years clears anything still receiving updates, including the
# slow-moving utilities that ship a build a year.
_STALE_TARGET_YEARS = 3


def _target_is_stale(entry: StartupEntry) -> bool:
    if not entry.target_modified:
        return False
    return (time.time() - entry.target_modified) > _STALE_TARGET_YEARS * 365 * 86400


def _rail_text(entry: StartupEntry) -> str:
    """The date the startup target was last touched — the row's right column.

    Just the date. The role moved into the meta line beside the publisher and
    source, because a fixed-width column cannot hold "Remote access service ·
    updated 2026-08-04" without being wide enough to make every other row's
    column look empty. Empty when unknown rather than padded with a dash: a
    scheduled task whose executable has moved legitimately has no date, and a
    column of placeholder dashes reads as broken data.
    """
    return entry.target_modified_display


def _rail_style(entry: StartupEntry) -> str:
    p = get_palette()
    color = p.get("review", "#d8b46a") if _target_is_stale(entry) \
        else p.get("text_dim", "#8a9b8f")
    return f"font-family: 'JetBrains Mono'; font-size: 10px; color: {color};"


def _rail_tooltip(entry: StartupEntry) -> str:
    if _target_is_stale(entry):
        return tr("This program has not been updated since {date} — it may be "
                  "left over from software you no longer use.").format(
                      date=entry.target_modified_display)
    return entry.impact or ""


def _startup_recommendation(entry: StartupEntry) -> tuple[str, str, str, str]:
    """Return (status, recommendation, evidence, accent_color)."""
    p = get_palette()
    unknown = _startup_unknown_publisher(entry)
    high_impact = _startup_high_boot_impact(entry)
    accent_safe = p.get("safe", "#7cc596")
    accent_review = p.get("review", "#d8b46a")
    accent_risk = p.get("risk", "#d68a78")
    accent_info = p.get("accent", "#7ab8d4")

    if entry.risk == "Protected":
        return (
            tr("PROTECTED"),
            tr("Recommendation: keep this enabled unless you are deliberately changing a security, driver, or hardware workflow."),
            entry.risk_reason or tr("This startup entry is tied to protected system or device behavior."),
            accent_risk,
        )
    if unknown and high_impact:
        return (
            tr("NEEDS REVIEW"),
            tr("Recommendation: verify the publisher and consider disabling this to reduce startup load."),
            tr("Unknown publisher plus persistent startup behavior is worth checking before you leave it enabled."),
            accent_review,
        )
    if unknown:
        return (
            tr("NEEDS REVIEW"),
            tr("Recommendation: review the executable path and publisher before keeping this enabled at startup."),
            tr("Podbye could not verify the publisher for this entry."),
            accent_review,
        )
    if high_impact:
        return (
            tr("BOOT IMPACT"),
            tr("Recommendation: consider disabling this if you do not need it immediately after sign-in."),
            tr("This role can add background work during login: {impact}.").format(impact=entry.impact),
            accent_review,
        )
    if entry.risk == "Safe":
        return (
            tr("LOW CONCERN"),
            tr("Recommendation: safe to disable if automatic launch is just a convenience."),
            entry.risk_reason or tr("This appears to be a non-critical convenience startup entry."),
            accent_safe,
        )
    if entry.risk == "Optional":
        return (
            tr("OPTIONAL"),
            tr("Recommendation: keep enabled only if you use this immediately after Windows starts."),
            entry.recommendation or entry.risk_reason or tr("Manual launch is usually enough for this item."),
            accent_info,
        )
    return (
        tr("NEEDS REVIEW"),
        tr("Recommendation: inspect the path and purpose before changing this startup entry."),
        entry.risk_reason or tr("Podbye does not have enough confidence to mark this as safe."),
        accent_review,
    )


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child = item.layout()
        if widget is not None:
            # hide(), not setParent(None). deleteLater() only queues the
            # delete, and until it runs the widget still paints at its old
            # geometry over whatever replaced it — but unparenting to fix that
            # turns the widget into a top-level *window*, and these rows duly
            # appeared as blank 200x64 windows over the app when Analyze
            # rebuilt the list. Hiding stops the painting and keeps it a child.
            widget.hide()
            widget.deleteLater()
        elif child is not None:
            _clear_layout(child)


_STARTUP_PROMPT = """\
You are analyzing a Windows startup entry for Podbye, a system analysis tool.

Entry:
  Name: {name}
  Publisher: {publisher}
  Executable: {path}
  Startup source: {source}
  Current state: {status}
  Recommendation state: {risk} — {risk_reason}

Write 3–4 sentences of plain prose in {language}. Follow this structure:

Sentence 1 — Identity: Name the specific product and who makes it. \
Be concrete ("NVIDIA Container is a background service that coordinates GeForce utilities"), \
not vague ("This is a startup program by NVIDIA").

Sentence 2 — Function: Describe exactly what it does when running at Windows startup. \
Name the specific features it powers — overlays, sync, driver updates, hardware monitoring, \
auto-launch, etc. Never write "runs in the background" or "provides services" alone.

Sentence 3 — Disable impact: Explain what actually stops working if startup is disabled. \
Be explicit about which category applies: \
(a) Windows itself breaks — do not disable; \
(b) driver or hardware features stop (overlays, RGB, auto-updates); \
(c) cloud sync pauses until the app is opened manually; \
(d) a game launcher or vendor utility no longer auto-starts; \
(e) nothing notable changes for typical use.

Sentence 4 — Recommendation: Give a plain practical verdict. \
Do not repeat the recommendation label. State whether a typical user would notice it disabled \
and whether it is worth disabling. Example ending: \
"Safe to disable — the GPU keeps working normally, but the NVIDIA overlay and \
automatic driver update checks will stop."

Rules:
- Plain prose only. No lists, no bullets, no markdown.
- If purpose is unknown, begin sentence 1 with: \
"Purpose unknown — limited public information available."
- Never say "can disable" or "may affect system performance" without naming the specific feature.
- Do not be alarmist for vendor utilities that are purely optional. \
Clearly separate Windows-critical from vendor-convenient.
- If this is a security component (antivirus, VPN, firewall), state that disabling \
reduces active protection.
- Tone: practical and direct, written for a knowledgeable home user.\
"""


class StartupAIWorker(QThread):
    """Processes startup entries through local AI one by one."""

    entry_updated = Signal(object)
    queue_status = Signal(str)
    log_line = Signal(str)

    def __init__(self, entries: list, settings_store, parent=None):
        super().__init__(parent)
        self._entries = entries
        self._settings = settings_store
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        from app.services.ollama_client import generate
        from app.services.ai_explainer import _AI_GLOBAL_LOCK

        endpoint = self._settings.get("ai_endpoint", "http://127.0.0.1:11434")
        model = self._settings.get("ai_model", "")
        language = self._settings.get("ai_explanation_language", "English")
        timeout = int(self._settings.get("ai_timeout", 120))

        if not model:
            self.log_line.emit("[startups] no AI model configured — skip AI analysis")
            for entry in self._entries:
                entry.ai_status = "failed"
                entry.ai_error = "No model configured"
                self.entry_updated.emit(entry)
            return

        got_lock = _AI_GLOBAL_LOCK.acquire(blocking=False)
        if not got_lock:
            self.queue_status.emit("waiting")
            self.log_line.emit("[startups] AI queue: waiting for another AI job to finish…")
            while not self._cancel.is_set():
                got_lock = _AI_GLOBAL_LOCK.acquire(blocking=True, timeout=1.0)
                if got_lock:
                    break

        if not got_lock or self._cancel.is_set():
            if got_lock:
                _AI_GLOBAL_LOCK.release()
            self.queue_status.emit("")
            return

        self.queue_status.emit("running")
        self.log_line.emit(f"[startups] AI analysis: {len(self._entries)} entries · model: {model}")

        try:
            for entry in self._entries:
                if self._cancel.is_set():
                    break
                if entry.risk == "Protected":
                    entry.ai_status = "disabled"
                    entry.ai_explanation = entry.explanation_fallback or "Protected startup entry — no extra analysis needed."
                    self.entry_updated.emit(entry)
                    continue

                entry.ai_status = "analyzing"
                self.entry_updated.emit(entry)

                prompt = _STARTUP_PROMPT.format(
                    name=entry.name,
                    publisher=entry.publisher_display,
                    path=entry.path or "Unknown",
                    source=entry.source_label,
                    status=entry.status_label,
                    risk=entry.risk,
                    risk_reason=entry.risk_reason,
                    language=language,
                )

                ok, text = generate(endpoint, model, prompt, timeout, self._cancel)

                if self._cancel.is_set():
                    break

                entry.ai_status = "ready" if ok else "failed"
                if ok:
                    entry.ai_explanation = text.strip()
                else:
                    entry.ai_error = text

                self.log_line.emit(f"[startups] AI {'done' if ok else 'failed'}: {entry.name}")
                self.entry_updated.emit(entry)

        finally:
            _AI_GLOBAL_LOCK.release()
            self.queue_status.emit("")

        self.log_line.emit("[startups] AI analysis complete")


class StartupListRow(QFrame):
    clicked = Signal(str)
    toggle_requested = Signal(str, bool)

    # Measured against the app's own font: "PROTECTED" wants 83 and "Disable"
    # 42, so each column is the widest label it can ever hold plus a little
    # breathing room. Too narrow and a fixed column clips its own text, which
    # would be worse than the jitter it replaced.
    _BADGE_W = 92
    # Wide enough for the toggle as a button, in every shipped language:
    # "Disable" needs 62px, Ukrainian 74px and French 80px once the border and
    # padding are counted. At 54 the styled button could not shrink to fit, so
    # it overflowed its column and squeezed the name and meta labels beside it
    # into ellipses - the button clipped, and took the row's text with it.
    _ACTION_W = 84

    def __init__(self, entry: StartupEntry, parent=None):
        super().__init__(parent)
        self._entry = entry
        self._selected = False
        self._hovered = False
        self.setObjectName("StartupListRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        # Wide enough for the longest label each column can hold — "PROTECTED"
        # and "Disable" — so the columns never resize with the content.
        # Two stacked lines on each side, and the toggle button's stylesheet
        # padding makes it taller than its unpolished sizeHint suggests — at 52
        # the rail text was drawn through the button above it.
        self.setMinimumHeight(64)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 14, 8)
        layout.setSpacing(12)

        center = QVBoxLayout()
        center.setSpacing(3)

        # Same reasoning as the meta line below. Real entry names run to
        # "OneDrive Startup Task-S-1-5-21-3897710897-..." - one unbreakable
        # token far wider than any row - and an Ignored policy on a plain
        # QLabel let the layout cut it off mid-word with nothing to show for
        # it. The head of the name is the identifying part.
        self._name_lbl = ElidedLabel("")
        self._name_lbl.setStyleSheet("font-size: 14px; font-weight: 760;")
        self._name_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        center.addWidget(self._name_lbl)

        self._ai_lbl = QLabel()
        self._ai_lbl.setObjectName("Muted")
        self._ai_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 9px; color: {get_palette().get('text_dim', '#8a9b8f')};"
        )

        self._state_lbl = QLabel()
        self._state_lbl.setAlignment(Qt.AlignCenter)

        self._risk_badge = Badge("", "info")
        self._toggle_btn = QPushButton()
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.clicked.connect(self._on_toggle_clicked)

        # Elides rather than clips. "Microsoft Corporation - System startup
        # registry (64-bit)" is longer than any row is wide, and an Ignored
        # size policy on a plain QLabel lets the layout squeeze it without the
        # label doing anything about it: the text simply ran off the end. The
        # publisher matters more than the tail of the source label, so it
        # keeps the head and the full string stays on the tooltip.
        self._meta_lbl = ElidedLabel("")
        self._meta_lbl.setObjectName("Dim")
        self._meta_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; color: {get_palette().get('text_dim', '#8a9b8f')};"
        )
        self._meta_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        center.addWidget(self._meta_lbl)
        layout.addLayout(center, stretch=1)

        # Fixed columns. This cluster used to size itself per row, so its width
        # was max(badge + action, rail text) — and all three of those vary by
        # row ("OPTIONAL"/"PROTECTED", "Disable"/"Enable", "Creative helper" vs
        # "Remote access service"). Right-aligned against the row edge, that put
        # every row's badge at a different x: a ~112px swing down the list,
        # which is what made the column look scattered.
        right = QVBoxLayout()
        right.setSpacing(2)
        right.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self._risk_badge.setFixedWidth(self._BADGE_W)
        self._toggle_btn.setFixedWidth(self._ACTION_W)
        badges = QHBoxLayout()
        badges.setSpacing(_RAIL_GAP)
        # The toggle button already says Enable/Disable, so the separate
        # ON ENABLED / OFF DISABLED pill is redundant noise in the row — the
        # full state is still shown in the inspector. Keep action + risk only.
        self._state_lbl.setVisible(False)
        badges.addWidget(self._risk_badge, alignment=Qt.AlignVCenter)
        badges.addWidget(self._toggle_btn, alignment=Qt.AlignVCenter)
        right.addLayout(badges)
        # Second line of the rail, mirroring the Findings rows' size/last-active
        # column. A startup entry has no size, so it carries what it does have:
        # the role it plays at login, and how old the binary behind it is.
        #
        # Exactly one line, always. AI status shares it rather than adding a
        # third — the row is sized for two, and a third silently overlapped the
        # buttons above it.
        self._rail_lbl = QLabel()
        self._rail_lbl.setObjectName("Dim")
        self._rail_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # Exactly as wide as the two columns above it, so the date's right edge
        # lines up with the action's.
        self._rail_lbl.setFixedWidth(self._BADGE_W + _RAIL_GAP + self._ACTION_W)
        right.addWidget(self._rail_lbl)
        layout.addLayout(right)

        self.update_entry(entry)
        self._apply_style()

    @property
    def key(self) -> str:
        return self._entry.key

    def update_entry(self, entry: StartupEntry):
        self._entry = entry
        self._name_lbl.setText(entry.name)
        self._state_lbl.setText(
            f"{'ON' if entry.enabled else 'OFF'} {entry.status_label.upper()}"
        )
        self._state_lbl.setStyleSheet(_state_pill_style(entry.enabled))
        self._toggle_btn.setText(tr("Disable") if entry.enabled else tr("Enable"))
        self._risk_badge.set_badge(tr(entry.risk), _RISK_VARIANT.get(entry.risk, "info"))
        parts = [entry.publisher_display, entry.source_label, entry.impact]
        self._meta_lbl.setText("  ·  ".join(p for p in parts if p))
        # Only surface AI status in the row when it's actually doing something.
        # "AI disabled"/no-AI on every row is just noise — the inspector shows
        # per-entry AI state and offers the Ask AI action there. While it is
        # working it takes over the rail line rather than adding one.
        ai_text = {
            "pending": tr("Queued"),
            "analyzing": tr("Analyzing"),
            "failed": tr("Fallback"),
        }.get(entry.ai_status, "")
        self._rail_lbl.setText(ai_text or _rail_text(entry))
        self._rail_lbl.setStyleSheet(_rail_style(entry))
        self._rail_lbl.setToolTip(_rail_tooltip(entry))
        self._apply_style()

    def set_selected(self, selected: bool):
        self._selected = selected
        self._apply_style()

    def enterEvent(self, event):
        self._hovered = True
        self._apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._apply_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._entry.key)
        super().mousePressEvent(event)

    def _on_toggle_clicked(self):
        self.toggle_requested.emit(self._entry.key, not self._entry.enabled)

    def _apply_style(self):
        p = get_palette()
        primary = p.get("text", "#d6e2da") if self._entry.enabled else p.get("text_dim", "#8a9b8f")
        secondary = p.get("text_dim", "#8a9b8f") if self._entry.enabled else p.get("text_faint", "#57685e")
        # A disabled entry is a different state, not a slightly quieter row.
        # This was rgba(138, 155, 143, 9) — a fixed forest-ish tint at alpha 9
        # of 255, invisible on any theme and wrong on three of them. It now
        # takes a real recessed surface from the palette, so a disabled row
        # reads as switched off at a glance rather than on close inspection.
        idle_bg = "transparent" if self._entry.enabled else p.get("bg_deep", "#070c09")
        if self._selected:
            accent = p.get("accent", "#7cc596")
            bg = p.get("accent_soft", "#1b2e22")
            # The selected row is marked by its accent, not by a hairline in
            # the same border colour every other state uses. The left bar goes
            # to 4px and the surrounding border takes the accent at half
            # strength, so selection reads the same in all four themes rather
            # than depending on how far accent_soft happens to sit from the
            # list background in each one.
            border = _rgba(accent, 120)
            self.setStyleSheet(
                f"QFrame#StartupListRow {{ background: {bg}; "
                f"border-left: 4px solid {accent}; "
                f"border-top: 1px solid {border}; "
                f"border-bottom: 1px solid {border}; "
                f"border-right: 1px solid {border}; }}"
            )
            self._name_lbl.setStyleSheet(
                f"font-size: 14px; font-weight: 800; color: {primary};"
            )
        elif self._hovered:
            bg = p.get("panel_hover", "#1d2c25")
            self.setStyleSheet(
                f"QFrame#StartupListRow {{ background: {bg}; "
                f"border: 1px solid {p.get('border', '#213028')}; }}"
            )
            self._name_lbl.setStyleSheet(
                f"font-size: 14px; font-weight: 760; color: {primary};"
            )
        else:
            bg = idle_bg
            self.setStyleSheet(f"QFrame#StartupListRow {{ background: {bg}; border: none; }}")
            self._name_lbl.setStyleSheet(
                f"font-size: 14px; font-weight: 760; color: {primary};"
            )
        self._meta_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; color: {secondary};"
        )
        self._ai_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 9px; color: {secondary};"
        )
        self._toggle_btn.setStyleSheet(self._toggle_style(self._entry.enabled))

    @staticmethod
    def _toggle_style(enabled: bool) -> str:
        """A compact button, not a line of text.

        This was deliberately styled as bare text once, because twenty-five
        outlined buttons down the page outweighed the entries they belonged
        to. The answer to that is a smaller button, not the loss of the
        affordance: as text it read as a label, on the one control the screen
        exists to offer. So it keeps a border and a fill, at 10px with tight
        padding, quiet enough to repeat twenty-five times.
        """
        p = get_palette()
        fg = p.get("safe", "#7cc596") if enabled else p.get("text_dim", "#8a9b8f")
        border = _rgba(fg, 110)
        panel_alt = p.get("panel_alt", "#19231c")
        panel_hover = p.get("panel_hover", "#1d2c25")
        return (
            f"QPushButton {{ font-family: 'JetBrains Mono'; font-size: 10px; "
            f"padding: 3px 8px; border-radius: 2px; color: {fg}; "
            f"border: 1px solid {border}; background: {panel_alt}; }}"
            f"QPushButton:hover {{ border-color: {fg}; background: {panel_hover}; }}"
        )


class StartupInspectorPanel(QFrame):
    def __init__(self, parent=None, compact: bool = False, ask_ai_cb=None):
        super().__init__(parent)
        self._compact = compact
        self._ask_ai_cb = ask_ai_cb
        self._current_entry = None
        self.setObjectName("Panel")
        self.setStyleSheet("background: transparent; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(6)

        self._header_row = QWidget()
        hdr = QHBoxLayout(self._header_row)
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(8)

        self._title_lbl = QLabel(tr("STARTUP INSPECTION"))
        apply_tactical_label(self._title_lbl, font_size=8, letter_spacing=1)
        hdr.addWidget(self._title_lbl)

        self._selection_lbl = QLabel(tr("// inspection"))
        self._selection_lbl.setObjectName("Muted")
        self._selection_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px;")
        hdr.addWidget(self._selection_lbl)
        hdr.addStretch()
        layout.addWidget(self._header_row)

        self._name_lbl = QLabel(tr("Select a startup entry"))
        self._name_lbl.setStyleSheet("font-size: 13px; font-weight: 700;")
        # Long scheduled-task names (e.g. "OneDrive Startup Task-S-1-5-21-…")
        # must wrap, or they push the whole inspection panel past the window.
        self._name_lbl.setWordWrap(True)
        layout.addWidget(self._name_lbl)

        self._publisher_lbl = QLabel(tr("Choose an entry on the left to inspect impact and recommendation."))
        self._publisher_lbl.setObjectName("Dim")
        self._publisher_lbl.setWordWrap(True)
        self._publisher_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; color: {get_palette().get('text_dim', '#8a9b8f')};"
        )
        layout.addWidget(self._publisher_lbl)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(5)

        self._state_lbl = QLabel()
        self._state_lbl.setAlignment(Qt.AlignCenter)
        badge_row.addWidget(self._state_lbl)

        self._risk_badge = Badge("", "info")
        badge_row.addWidget(self._risk_badge)
        badge_row.addStretch()
        layout.addLayout(badge_row)

        self._recommendation_frame = QFrame()
        self._recommendation_frame.setObjectName("StartupRecommendationSection")
        rec_layout = QVBoxLayout(self._recommendation_frame)
        rec_layout.setContentsMargins(0, 4, 0, 2)
        rec_layout.setSpacing(6)

        rec_hdr = QHBoxLayout()
        rec_hdr.setSpacing(8)
        rec_title = QLabel(tr("AI RECOMMENDATIONS"))
        apply_tactical_label(rec_title, font_size=8, letter_spacing=2)
        rec_hdr.addWidget(rec_title)
        self._rec_status_lbl = QLabel(tr("WAITING"))
        self._rec_status_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
        rec_hdr.addWidget(self._rec_status_lbl)
        rec_hdr.addStretch()
        rec_layout.addLayout(rec_hdr)

        self._rec_text_lbl = QLabel(tr("Select a startup entry to see Podbye's recommendation."))
        self._rec_text_lbl.setWordWrap(True)
        self._rec_text_lbl.setStyleSheet("font-size: 12px; font-weight: 600;")
        rec_layout.addWidget(self._rec_text_lbl)

        self._rec_evidence_lbl = QLabel("")
        self._rec_evidence_lbl.setWordWrap(True)
        self._rec_evidence_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
        rec_layout.addWidget(self._rec_evidence_lbl)

        self._inspection_frame = QFrame()
        self._inspection_frame.setObjectName("StartupDetailSection")
        inspection_frame_layout = QVBoxLayout(self._inspection_frame)
        inspection_frame_layout.setContentsMargins(0, 0, 0, 0)
        inspection_frame_layout.setSpacing(6)
        meta_hdr = QLabel(tr("INSPECTION"))
        apply_tactical_label(meta_hdr, font_size=8, letter_spacing=2)
        meta_hdr.setVisible(False)

        self._inspection_host = QWidget()
        inspection_layout = QVBoxLayout(self._inspection_host)
        inspection_layout.setContentsMargins(0, 0, 0, 0)
        inspection_layout.setSpacing(4)
        self._source_lbl = self._make_info_value(inspection_layout, tr("Source"))
        self._impact_lbl = self._make_info_value(inspection_layout, tr("Impact"))
        # Elided, not wrapped: a launch path is one unbreakable word, and a
        # wrapping label reports it as the panel's minimum width — which clipped
        # every other row in the inspector at the sidebar edge.
        self._path_lbl = self._make_info_value(
            inspection_layout, tr("Launch path"), mono=True, elide=True)
        inspection_frame_layout.addWidget(self._inspection_host)
        layout.addWidget(self._inspection_frame)

        self._explanation_host = QFrame()
        self._explanation_host.setObjectName("StartupDetailReasoning")
        expl_layout = QVBoxLayout(self._explanation_host)
        expl_layout.setContentsMargins(10, 9, 10, 9)
        expl_layout.setSpacing(6)

        expl_hdr_row = QHBoxLayout()
        expl_hdr_row.setSpacing(8)
        expl_hdr = QLabel(tr("CONTEXTUAL REASONING"))
        apply_tactical_label(expl_hdr, font_size=8, letter_spacing=1)
        expl_hdr_row.addWidget(expl_hdr)
        expl_hdr_row.addStretch()
        # On-demand "Ask AI" — explain this one entry even when startup AI is
        # off. Shown only when there is no AI answer yet (set in set_entry()).
        self._ask_ai_btn = QPushButton(tr("Ask AI"))
        self._ask_ai_btn.setCursor(Qt.PointingHandCursor)
        self._ask_ai_btn.setStyleSheet(ask_ai_button_qss())
        self._ask_ai_btn.setVisible(False)
        self._ask_ai_btn.clicked.connect(self._on_ask_ai_clicked)
        expl_hdr_row.addWidget(self._ask_ai_btn)
        expl_layout.addLayout(expl_hdr_row)

        self._ai_status_lbl = QLabel()
        self._ai_status_lbl.setObjectName("Muted")
        self._ai_status_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px;")
        expl_layout.addWidget(self._ai_status_lbl)

        self._reason_lbl = self._make_info_value(expl_layout, tr("Importance"), wrap=True)
        self._explanation_lbl = self._make_info_value(expl_layout, tr("Reading"), wrap=True)
        layout.addWidget(self._recommendation_frame)
        layout.addWidget(self._explanation_host)

        # Footer actions, matching the Findings inspector. There used to be a
        # "QUICK ACTION" frame here holding a second Open Task Manager button —
        # but it was never added to a layout, so it could not be shown and
        # set_task_manager_handler wired a click nobody could make. The screen
        # header already carries that button; what was actually missing was any
        # way to act on the path this panel displays.
        self._action_frame = QFrame()
        self._action_frame.setObjectName("StartupDetailSection")
        action_layout = QVBoxLayout(self._action_frame)
        action_layout.setContentsMargins(0, 6, 0, 0)
        action_layout.setSpacing(5)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._open_btn = QPushButton(tr("Open in Explorer"))
        self._open_btn.setObjectName("Subtle")
        self._open_btn.setStyleSheet("font-size: 10px; padding: 3px 8px;")
        self._open_btn.setCursor(Qt.PointingHandCursor)
        self._open_btn.clicked.connect(self._on_open_location)
        btn_row.addWidget(self._open_btn)

        self._copy_btn = QPushButton(tr("Copy path"))
        self._copy_btn.setObjectName("Subtle")
        self._copy_btn.setStyleSheet("font-size: 10px; padding: 3px 8px;")
        self._copy_btn.setCursor(Qt.PointingHandCursor)
        self._copy_btn.clicked.connect(self._on_copy_path)
        btn_row.addWidget(self._copy_btn)
        btn_row.addStretch()
        action_layout.addLayout(btn_row)

        note = QLabel(tr("Podbye explains startup impact, but changes stay manual."))
        note.setObjectName("Muted")
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 9px;")
        action_layout.addWidget(note)

        self._action_frame.setVisible(False)
        layout.addWidget(self._action_frame)

        # Absorb leftover vertical space so the inspector content stays packed
        # at the top. Without this, the widgetResizable scroll area spreads the
        # slack between labels, producing the large gaps (name ↔ publisher etc).
        layout.addStretch(1)

        self._apply_section_styles()
        self.set_entry(None)

    def _make_info_value(self, layout, label: str, mono: bool = False,
                         wrap: bool = False, elide: bool = False) -> QLabel:
        row = QWidget()
        row_l = QHBoxLayout(row)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(8)

        key = QLabel(label.upper())
        key.setObjectName("Muted")
        # Wraps rather than clips. The compact column is 88px and French turns
        # "LAUNCH PATH" into "CHEMIN DE LANCEMENT", which needs 135px - a field
        # name cut in half is worse than a field name on two lines, and the
        # column cannot grow without taking the width from the value beside it.
        key.setWordWrap(True)
        key.setFixedWidth(88 if self._compact else 112)
        key.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 11px; font-weight: 600; "
            f"color: {get_palette().get('text_dim', '#8a9b8f')};"
        )
        row_l.addWidget(key, 0, Qt.AlignTop)

        value = ElidedLabel("—", mode=Qt.ElideMiddle) if elide else QLabel("—")
        value.setWordWrap(wrap)
        style = "font-size: 12px;"
        if mono:
            style += " font-family: 'JetBrains Mono';"
        style += f" color: {get_palette().get('text', '#d6e2da')};"
        value.setStyleSheet(style)
        row_l.addWidget(value, 1)
        layout.addWidget(row)
        return value

    def _entry_path(self) -> str:
        entry = self._current_entry
        if not entry:
            return ""
        return entry.path or entry.command or ""

    def _on_open_location(self):
        """Reveal the launch target in Explorer, selecting the file itself."""
        path = self._entry_path()
        if not path:
            return
        try:
            if os.path.exists(path):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                # A command line with arguments, or a target that has since been
                # uninstalled — fall back to the folder it was supposed to be in.
                parent = os.path.dirname(path)
                if os.path.isdir(parent):
                    subprocess.Popen(["explorer", os.path.normpath(parent)])
        except OSError:
            pass

    def _on_copy_path(self):
        path = self._entry_path()
        if not path:
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(path)
        self._copy_btn.setText(tr("Copied"))
        QTimer.singleShot(
            1200, self, lambda: self._copy_btn.setText(tr("Copy path")))

    def set_embedded_header_visible(self, visible: bool):
        self._header_row.setVisible(visible)

    def _apply_section_styles(self):
        p = get_palette()
        section_qss = (
            "QFrame#StartupDetailSection { background: transparent; border: none; }"
        )
        # Contextual reasoning is supporting material, so it reads as prose on
        # the panel. It used to be the only boxed section here while the
        # recommendation had no container at all, which put the frame around
        # the explanation and left the conclusion looking like a caption above
        # it. The box moved to the recommendation; this keeps a left rule so
        # the block still reads as one passage.
        reasoning_qss = (
            f"QFrame#StartupDetailReasoning {{ background: transparent; "
            f"border: none; border-left: 2px solid {p.get('border', '#213028')}; "
            f"border-radius: 0px; }}"
        )
        self._explanation_host.setStyleSheet(reasoning_qss)
        self._inspection_frame.setStyleSheet(section_qss)
        self._action_frame.setStyleSheet(section_qss)
        self._name_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 15px; font-weight: bold; "
            f"color: {p.get('text', '#d6e2da')};"
        )
        self._publisher_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 12px; "
            f"color: {p.get('text_dim', '#8a9b8f')};"
        )
        self._apply_recommendation_style(p.get("text_dim", "#8a9b8f"))

    def _apply_recommendation_style(self, accent: str):
        p = get_palette()
        border = _rgba(accent, 130)
        # The conclusion gets the container. Tinted in the accent the verdict
        # already carries, so the section's weight tracks its severity instead
        # of being uniform.
        self._recommendation_frame.setStyleSheet(
            f"QFrame#StartupRecommendationSection {{ background: {_rgba(accent, 20)}; "
            f"border: 1px solid {border}; border-radius: 2px; }}"
        )
        self._rec_status_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; "
            f"color: {accent}; padding: 1px 6px; border: 1px solid {border}; "
            "border-radius: 2px; background: transparent;"
        )
        self._rec_text_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 650; color: {p.get('text', '#d6e2da')};"
        )
        self._rec_evidence_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; "
            f"color: {p.get('text_dim', '#8a9b8f')};"
        )

    def _ask_ai_visible_for(self, entry: StartupEntry | None) -> bool:
        return (
            self._ask_ai_cb is not None
            and entry is not None
            and entry.ai_status not in ("ready", "done", "analyzing", "pending")
        )

    def _on_ask_ai_clicked(self):
        """Explain just the selected startup entry on demand."""
        if not (self._current_entry and self._ask_ai_cb):
            return
        reason = self._ask_ai_cb(self._current_entry)
        if reason:
            return  # couldn't start (e.g. no model) — handled by the callback
        self._ask_ai_btn.setVisible(False)
        self._ai_status_lbl.setText(tr("Analyzing startup behavior…"))

    def set_entry(self, entry: StartupEntry | None):
        self._current_entry = entry
        self._ask_ai_btn.setVisible(self._ask_ai_visible_for(entry))
        if entry is None:
            self._selection_lbl.setText(tr("// inspection"))
            self._name_lbl.setText(tr("Select a startup entry"))
            self._publisher_lbl.setText(tr("Choose an entry on the left to inspect impact and recommendation."))
            self._state_lbl.setText("")
            self._state_lbl.setStyleSheet("")
            self._risk_badge.setVisible(False)
            self._ai_status_lbl.setText("")
            self._explanation_lbl.setText(tr("Startup explanation will appear here once you select an entry."))
            self._rec_status_lbl.setText(tr("WAITING"))
            self._rec_text_lbl.setText(tr("Select a startup entry to see Podbye's recommendation."))
            self._rec_evidence_lbl.setText("")
            self._apply_recommendation_style(get_palette().get("text_dim", "#8a9b8f"))
            self._source_lbl.setText("—")
            self._impact_lbl.setText("—")
            self._path_lbl.setText("—")
            self._reason_lbl.setText("—")
            self._action_frame.setVisible(False)
            return

        self._selection_lbl.setText(tr("// selected"))
        # Actions operate on the launch path, so they only make sense once
        # there is one to act on.
        self._action_frame.setVisible(bool(entry.path or entry.command))
        self._name_lbl.setText(entry.name)
        self._publisher_lbl.setText(entry.publisher_display)
        self._state_lbl.setText(entry.status_label.upper())
        self._state_lbl.setStyleSheet(_state_pill_style(entry.enabled))
        self._risk_badge.setVisible(True)
        self._risk_badge.set_badge(tr(entry.risk), _RISK_VARIANT.get(entry.risk, "info"))
        rec_status, rec_text, rec_evidence, rec_accent = _startup_recommendation(entry)
        self._rec_status_lbl.setText(rec_status)
        self._rec_text_lbl.setText(rec_text)
        self._rec_evidence_lbl.setText(rec_evidence)
        self._apply_recommendation_style(rec_accent)
        self._ai_status_lbl.setText({
            "pending": tr("Queued for AI explanation"),
            "analyzing": tr("Analyzing startup behavior…"),
            "failed": tr("AI unavailable · using fallback explanation"),
            "disabled": tr("AI disabled for this entry"),
        }.get(entry.ai_status, ""))
        explanation = self._resolve_explanation(entry)
        self._explanation_lbl.setText(self._compact_text(explanation))
        # Shortening is a display choice; the whole answer stays reachable.
        self._explanation_lbl.setToolTip(explanation)
        self._source_lbl.setText(entry.source_label)
        self._impact_lbl.setText(entry.impact)
        self._path_lbl.setText(entry.path or entry.command or "—")
        self._reason_lbl.setText(entry.risk_reason or entry.recommendation or "—")

    def update_entry(self, entry: StartupEntry):
        self.set_entry(entry)

    @staticmethod
    def _resolve_explanation(entry: StartupEntry) -> str:
        if entry.ai_status == "ready" and entry.ai_explanation:
            return entry.ai_explanation
        if entry.ai_status == "disabled" and entry.ai_explanation:
            return entry.ai_explanation
        if entry.ai_status == "failed":
            return entry.explanation_fallback or entry.ai_error or tr("Startup explanation failed.")
        if entry.ai_status in ("pending", "analyzing"):
            return entry.explanation_fallback or tr("Startup analysis is in progress.")
        return entry.explanation_fallback or tr("Startup explanation not available yet.")

    @staticmethod
    def _compact_text(text: str, limit: int = 220) -> str:
        """Shorten an explanation for the panel, ending on a whole word.

        A hard slice cut mid-word — "Disabling startup removes background
        features until the app is opened manually. Disabl..." — which reads as
        corrupted text rather than as a summary. These explanations are written
        as three sentences, so preferring the last sentence end that fits
        usually yields a clean, complete thought; a word boundary is the
        fallback. The untruncated text stays available as the tooltip.
        """
        text = " ".join((text or "").split())
        if len(text) <= limit:
            return text
        window = text[:limit]
        end = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
        # Only if a sentence ends late enough to still say something.
        if end >= limit // 2:
            return window[:end + 1]
        cut = window.rfind(" ")
        return (window[:cut] if cut > 0 else window).rstrip(" ,;:") + "…"


class StartupRightSidebar(QFrame):
    """Persistent right-side inspector for selected startup metadata."""

    def __init__(self, parent=None, ask_ai_cb=None):
        super().__init__(parent)
        self._ask_ai_cb = ask_ai_cb
        self.setObjectName("StartupRightSidebar")
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        title = QLabel(tr("STARTUP INSPECTION"))
        apply_tactical_label(title, font_size=8, letter_spacing=1)
        hdr.addWidget(title)
        self._meta = QLabel(tr("// details"))
        self._meta.setObjectName("Muted")
        self._meta.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px;")
        hdr.addWidget(self._meta)
        hdr.addStretch()
        layout.addLayout(hdr)

        self._sep = QFrame()
        self._sep.setFixedHeight(1)
        layout.addWidget(self._sep)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet("border: none; background: transparent;")

        self.detail_widget = StartupInspectorPanel(compact=True, ask_ai_cb=ask_ai_cb)
        self.detail_widget.setStyleSheet("background: transparent; border: none;")
        self.detail_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.detail_widget.set_embedded_header_visible(False)
        self._scroll.setWidget(self.detail_widget)
        layout.addWidget(self._scroll, stretch=1)

        self.apply_style()
        self.clear()

    def apply_style(self):
        p = get_palette()
        detail_bg = p.get("panel_alt", "#18241e")
        border = p.get("border_alt", "#2b3d33")
        line = QColor(p.get("border", "#213028"))
        line.setAlpha(58)
        line_rgba = f"rgba({line.red()}, {line.green()}, {line.blue()}, {line.alpha()})"
        self.setStyleSheet(
            f"QFrame#StartupRightSidebar {{ background: {detail_bg}; "
            f"border: 1px solid {border}; border-radius: 2px; }}"
        )
        self._sep.setStyleSheet(f"background: {line_rgba}; border: none;")
        self.detail_widget.setStyleSheet(f"background: {detail_bg}; border: none;")
        self.detail_widget._apply_section_styles()

    def set_entry(self, entry: StartupEntry):
        self.detail_widget.set_entry(entry)
        self._meta.setText(tr("// selected"))

    def clear(self):
        self.detail_widget.set_entry(None)
        self._meta.setText(tr("// details"))


class StartupsScreen(QWidget):
    _BTN_IDLE = "padding: 4px 10px; font-size: 11px;"
    _BTN_GROUP_LABEL = "font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; letter-spacing: 1px;"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings_store = None
        self._entries: list[StartupEntry] = []
        self._filtered: list[StartupEntry] = []
        self._selected_key: str | None = None
        self._row_widgets: dict[str, StartupListRow] = {}
        self._detail_widget: StartupInspectorPanel | None = None
        self._ai_worker: StartupAIWorker | None = None
        self._ask_workers: list = []  # single-entry on-demand "Ask AI" workers
        self._btn_retry_ai: QPushButton | None = None
        # The set of risks currently shown. Multi-select, matching Findings —
        # this used to be a single string, so the identical-looking chip row
        # behaved as radio buttons here and as toggles there. All four present
        # means "no filter".
        self._risk_filter: set[str] = set(RISK_ORDER)
        self._status_filter = "All"
        self._search = ""
        self._risk_btns: dict[str, QPushButton] = {}
        self._status_btns: dict[str, QPushButton] = {}
        self._count_lbl: QLabel | None = None
        self._list_layout: QVBoxLayout | None = None
        self._empty_lbl: QLabel | None = None
        self._right_sidebar: StartupRightSidebar | None = None

        self._build_ui()
        theme_signaller().theme_changed.connect(self._rebuild_styles)

    def set_settings_store(self, store):
        self._settings_store = store

    def _rebuild_styles(self, theme_key: str = ""):
        self._apply_detail_widget_style()
        if self._detail_widget is not None:
            self._detail_widget._apply_section_styles()
        if self._entries:
            self._show_results()
        else:
            self._show_idle()

    def _apply_detail_widget_style(self):
        if self._detail_widget is None:
            return
        if self._right_sidebar is not None:
            self._right_sidebar.apply_style()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("border: none;")
        outer.addWidget(self._scroll)

        self._show_idle()

    def _make_header(self, parent_layout: QVBoxLayout, analyzing: bool = False):
        header = QHBoxLayout()
        header.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel(tr("STARTUPS"))
        apply_tactical_label(title, font_size=16, letter_spacing=4)
        title_col.addWidget(title)
        sub = QLabel(tr("Startup controls update Podbye state · Windows changes stay manual"))
        sub.setObjectName("Dim")
        sub.setStyleSheet("font-size: 12px;")
        title_col.addWidget(sub)
        header.addLayout(title_col, stretch=1)

        self._queue_lbl = QLabel()
        self._queue_lbl.setObjectName("Muted")
        # From the palette, not a literal: #d8b46a is a dark-theme amber
        # and sat almost invisibly on the light "paper" theme.
        self._queue_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 10px; "
            f"color: {get_palette().get('review', '#d8b46a')};")
        self._queue_lbl.setVisible(False)
        header.addWidget(self._queue_lbl)

        self._btn_analyze = QPushButton(tr("Re-analyze") if analyzing else tr("Analyze Startups"))
        self._btn_analyze.setObjectName("Subtle")
        self._btn_analyze.setCursor(Qt.PointingHandCursor)
        self._btn_analyze.clicked.connect(self._analyze)
        header.addWidget(self._btn_analyze)

        self._btn_retry_ai = QPushButton(tr("Retry failed"))
        self._btn_retry_ai.setObjectName("Subtle")
        self._btn_retry_ai.setCursor(Qt.PointingHandCursor)
        self._btn_retry_ai.setVisible(False)
        self._btn_retry_ai.clicked.connect(self._retry_failed_ai)
        header.addWidget(self._btn_retry_ai)

        btn_tm = QPushButton(tr("Open Task Manager ↗"))
        btn_tm.setObjectName("Subtle")
        btn_tm.setCursor(Qt.PointingHandCursor)
        btn_tm.clicked.connect(self._open_task_manager)
        header.addWidget(btn_tm)

        parent_layout.addLayout(header)

    def _show_idle(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 16, 22, 22)
        layout.setSpacing(20)

        self._make_header(layout, analyzing=False)

        idle_frame = Panel()
        idle_lay = idle_frame.with_layout(vertical=True, margins=(24, 32, 24, 32), spacing=12)
        idle_lbl = QLabel(tr("Click Analyze Startups to detect Windows startup programs."))
        idle_lbl.setAlignment(Qt.AlignCenter)
        idle_lbl.setObjectName("Muted")
        idle_lbl.setStyleSheet("font-size: 14px;")
        idle_lay.addWidget(idle_lbl)
        layout.addWidget(idle_frame)
        layout.addStretch()

        self._scroll.setWidget(content)

    def _show_results(self):
        self._row_widgets = {}

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 16, 22, 22)
        layout.setSpacing(12)

        self._make_header(layout, analyzing=True)
        self._build_summary(layout)
        self._build_body(layout)

        self._scroll.setWidget(content)
        self._reapply_filters(initial=True)

    def _build_summary(self, layout: QVBoxLayout):
        entries = self._entries
        total = len(entries)
        enabled = sum(1 for e in entries if e.enabled)
        needs_review = sum(1 for e in entries if e.risk in ("Review", "Protected"))
        optional_count = sum(1 for e in entries if e.risk == "Optional")

        p = get_palette()
        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)

        for hdr, val, unit, color in [
            (tr("TOTAL ENTRIES"), str(total), tr("detected"), ""),
            (tr("ENABLED"), str(enabled), f"of {total}", ""),
            (tr("NEEDS REVIEW"), str(needs_review), tr("review + protected"), p.get("review", "#d8b46a")),
            (tr("OPTIONAL"), str(optional_count), tr("manual launch is fine"), p.get("accent", "#7ab8d4")),
        ]:
            card = Panel()
            cl = card.with_layout(vertical=True, margins=(14, 10, 14, 10), spacing=2)

            h = QLabel(hdr)
            h.setObjectName("Muted")
            h.setStyleSheet("font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 9px; letter-spacing: 2px;")
            cl.addWidget(h)

            vrow = QHBoxLayout()
            vrow.setSpacing(4)
            vrow.setAlignment(Qt.AlignBaseline)
            num_style = "font-family: 'JetBrains Mono'; font-size: 28px; font-weight: bold;"
            if color:
                num_style += f" color: {color};"
            n = QLabel(val)
            n.setStyleSheet(num_style)
            vrow.addWidget(n)
            u = QLabel(unit)
            u.setObjectName("Dim")
            u.setStyleSheet("font-size: 11px;")
            vrow.addWidget(u)
            vrow.addStretch()
            cl.addLayout(vrow)
            cards_row.addWidget(card, stretch=1)

        layout.addLayout(cards_row)

    @staticmethod
    def _btn_active_style() -> str:
        p = get_palette()
        return (
            f"padding: 5px 12px; font-size: 10px; "
            f"background: {p.get('panel_alt', '#18241e')}; "
            f"border: 1px solid {p.get('border_alt', '#2b3d33')}; "
            f"color: {p.get('accent', '#7cc596')};"
        )

    @staticmethod
    def _btn_inactive_style() -> str:
        p = get_palette()
        return (
            f"padding: 5px 12px; font-size: 10px; "
            f"background: transparent; "
            f"border: 1px solid {p.get('border', '#213028')}; "
            f"color: {p.get('text_faint', '#57685e')};"
        )

    def _filter_btn(self, label: str, active: bool) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("Subtle")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(28)
        btn.setStyleSheet(self._btn_active_style() if active else self._btn_inactive_style())
        return btn

    def _build_body(self, layout: QVBoxLayout):
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)

        left_panel = Panel(alt=True)
        left_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_layout = left_panel.with_layout(vertical=True, margins=(0, 0, 0, 0), spacing=0)
        self._build_left_panel(left_layout)
        body_layout.addWidget(left_panel, stretch=7)

        self._right_sidebar = StartupRightSidebar(ask_ai_cb=self._on_ask_ai_startup)
        self._detail_widget = self._right_sidebar.detail_widget
        body_layout.addWidget(self._right_sidebar, stretch=3)
        self._apply_detail_widget_style()

        layout.addWidget(body, stretch=1)

    def _build_left_panel(self, layout: QVBoxLayout):
        hdr = QHBoxLayout()
        hdr.setContentsMargins(14, 12, 14, 10)
        hdr.setSpacing(8)

        lbl = QLabel(tr("STARTUP ENTRIES"))
        apply_tactical_label(lbl, font_size=9, letter_spacing=2)
        hdr.addWidget(lbl)

        self._count_lbl = QLabel()
        self._count_lbl.setObjectName("Muted")
        self._count_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px;")
        hdr.addWidget(self._count_lbl)
        hdr.addStretch()
        layout.addLayout(hdr)
        layout.addWidget(_separator())

        self._build_filters(layout)

        self._list_scroll = QScrollArea()
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list_scroll.setStyleSheet("border: none; background: transparent;")

        list_host = QWidget()
        self._list_layout = QVBoxLayout(list_host)
        self._list_layout.setContentsMargins(10, 8, 22, 8)
        self._list_layout.setSpacing(6)

        self._empty_lbl = QLabel(tr("No startup entries match the current filters."))
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        self._empty_lbl.setObjectName("Muted")
        self._empty_lbl.setStyleSheet("font-size: 13px; padding: 24px 0px;")
        self._list_layout.addWidget(self._empty_lbl)
        self._list_layout.addStretch()

        self._list_scroll.setWidget(list_host)
        layout.addWidget(self._list_scroll, stretch=1)

    def _build_filters(self, layout: QVBoxLayout):
        row = QVBoxLayout()
        row.setContentsMargins(14, 10, 14, 8)
        row.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.setAlignment(Qt.AlignVCenter)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(tr("Search startups…"))
        self._search_input.setFixedWidth(280)
        self._search_input.setFixedHeight(34)
        self._search_input.setText(self._search)
        self._search_input.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 11px; "
            f"padding: 0px 10px; border-radius: 2px; "
            f"border: 1px solid {get_palette().get('border_alt', '#2b3d33')}; "
            f"background: {get_palette().get('panel', '#141d18')};"
        )
        self._search_input.textChanged.connect(self._on_search_changed)
        top.addWidget(self._search_input)
        top.addSpacing(4)

        self._all_risks_btn = self._filter_btn(tr("All"), True)
        self._all_risks_btn.clicked.connect(self._on_all_risks_clicked)
        top.addWidget(self._all_risks_btn)

        self._risk_btns = {}
        for key in RISK_ORDER:
            btn = self._filter_btn(tr(key), key in self._risk_filter)
            btn.clicked.connect(lambda _=False, k=key: self._toggle_risk_filter(k))
            self._risk_btns[key] = btn
            top.addWidget(btn)

        top.addStretch()
        row.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        bottom.setAlignment(Qt.AlignVCenter)

        self._status_btns = {}
        status_lbl = QLabel(tr("STATE"))
        status_lbl.setObjectName("Muted")
        status_lbl.setStyleSheet(self._BTN_GROUP_LABEL)
        bottom.addWidget(status_lbl)
        for key, display in [("Enabled", tr("Enabled")), ("Disabled", tr("Disabled"))]:
            btn = self._filter_btn(display, self._status_filter == key)
            btn.clicked.connect(lambda _=False, k=key: self._set_status_filter(k))
            self._status_btns[key] = btn
            bottom.addWidget(btn)

        bottom.addStretch()
        row.addLayout(bottom)

        layout.addLayout(row)

    def _on_all_risks_clicked(self):
        """'All' means no filter — turn every risk chip back on."""
        self._risk_filter = set(RISK_ORDER)
        self._refresh_risk_chips()
        self._reapply_filters()

    def _toggle_risk_filter(self, key: str):
        """Add or remove one risk from the shown set.

        Turning the last one off would leave an empty list with no way back
        except All, so the final chip refuses to switch itself off.
        """
        if key in self._risk_filter:
            if len(self._risk_filter) == 1:
                return
            self._risk_filter.discard(key)
        else:
            self._risk_filter.add(key)
        self._refresh_risk_chips()
        self._reapply_filters()

    def _refresh_risk_chips(self):
        for key, btn in self._risk_btns.items():
            btn.setStyleSheet(self._btn_active_style() if key in self._risk_filter
                              else self._btn_inactive_style())
        showing_everything = len(self._risk_filter) == len(RISK_ORDER)
        self._all_risks_btn.setStyleSheet(
            self._btn_active_style() if showing_everything else self._btn_inactive_style())

    def _set_status_filter(self, label: str):
        self._status_filter = "All" if self._status_filter == label else label
        for lbl, btn in self._status_btns.items():
            btn.setStyleSheet(self._btn_active_style() if lbl == self._status_filter else self._btn_inactive_style())
        self._reapply_filters()

    def _on_search_changed(self, text: str):
        self._search = text
        self._reapply_filters()

    def _passes_filter(self, entry: StartupEntry) -> bool:
        if entry.risk not in self._risk_filter:
            return False
        if self._status_filter == "Enabled" and not entry.enabled:
            return False
        if self._status_filter == "Disabled" and entry.enabled:
            return False
        if self._search:
            haystack = f"{entry.name} {entry.publisher_display} {entry.path} {entry.source_label} {entry.command}".lower()
            if self._search.lower() not in haystack:
                return False
        return True

    def _reapply_filters(self, initial: bool = False):
        self._filtered = [e for e in self._entries if self._passes_filter(e)]
        if self._selected_key and not any(e.key == self._selected_key for e in self._filtered):
            self._selected_key = None
        self._rebuild_entry_list()

    def _rebuild_entry_list(self):
        if self._list_layout is None:
            return
        _clear_layout(self._list_layout)
        self._row_widgets = {}

        if self._count_lbl is not None:
            self._count_lbl.setText(tr("// {shown} of {total} shown",
                                       shown=len(self._filtered), total=len(self._entries)))

        if not self._filtered:
            empty = QLabel(tr("No startup entries match the current filters."))
            empty.setAlignment(Qt.AlignCenter)
            empty.setObjectName("Muted")
            empty.setStyleSheet("font-size: 13px; padding: 24px 0px;")
            self._list_layout.addWidget(empty)
            self._list_layout.addStretch()
            self._selected_key = None
            self._clear_detail_sidebar()
            return

        selected_visible = False
        for entry in self._filtered:
            row = StartupListRow(entry)
            row.clicked.connect(self._select_entry)
            row.toggle_requested.connect(self._set_entry_enabled)
            is_selected = entry.key == self._selected_key and bool(self._selected_key)
            row.set_selected(is_selected)
            if is_selected:
                selected_visible = True
            self._row_widgets[entry.key] = row
            self._list_layout.addWidget(row)
        self._list_layout.addStretch()

        if not selected_visible:
            self._selected_key = None
            self._clear_detail_sidebar()
            self._sync_row_selection()
        else:
            self._sync_row_selection()

    def _select_entry(self, key: str):
        entry = next((e for e in self._filtered if e.key == key), None)
        if entry is None:
            return
        if self._selected_key == key:
            self._selected_key = None
            self._sync_row_selection()
            self._clear_detail_sidebar()
            return
        self._selected_key = key
        self._sync_row_selection()
        self._show_detail_sidebar(entry)

    def _sync_row_selection(self):
        for row_key, row in self._row_widgets.items():
            row.set_selected(row_key == self._selected_key and bool(self._selected_key))

    def _set_entry_enabled(self, key: str, enabled: bool):
        entry = next((e for e in self._entries if e.key == key), None)
        if entry is None:
            return
        entry.enabled = enabled
        if self._selected_key == key and self._right_sidebar is not None:
            self._right_sidebar.set_entry(entry)
        self._show_results()

    def _show_detail_sidebar(self, entry: StartupEntry):
        if self._right_sidebar is None:
            return
        try:
            self._right_sidebar.set_entry(entry)
            if self._list_scroll is not None:
                row = self._row_widgets.get(entry.key)
                if row is not None:
                    self._list_scroll.ensureWidgetVisible(row, 0, 28)
        except Exception:
            self._clear_detail_sidebar()

    def _clear_detail_sidebar(self):
        if self._right_sidebar is None:
            return
        self._right_sidebar.clear()

    # ── Background work ───────────────────────────────────────────

    def busy_reason(self) -> str:
        if self._ai_worker is not None and self._ai_worker.isRunning():
            return tr("startup entries are being analyzed")
        return ""

    def stop_background_work(self, timeout_ms: int = 3000) -> bool:
        from app.services.workers import stop_worker
        return stop_worker(self._ai_worker, timeout_ms)

    # Re-reading the registry and the Startup folders costs about a third of a
    # second, and a screen can be shown several times in a row while a window
    # settles. Collapse those into one read.
    #
    # Class-level, not per-instance: the rate limit belongs to the machine's
    # startup list, not to a particular widget. The app only ever has one of
    # these screens, so this changes nothing there - but it stops a rebuilt or
    # second instance re-reading the registry a moment after the first did.
    _REFRESH_INTERVAL_S = 3.0
    _last_refresh = 0.0

    def showEvent(self, event):
        """Refresh the list whenever the page is opened.

        Startup entries change outside this app - an installer adds one, an
        uninstaller leaves one behind - so a list built on a previous visit
        describes a machine that has moved on. Opening the page is when the
        user expects it to be true.
        """
        super().showEvent(event)
        self._refresh_entries()

    def _refresh_entries(self):
        """Re-read the machine and merge into what is already on screen.

        Deliberately does not touch the model. An automatic refresh may not
        start work that costs money and minutes: Re-analyze is the action that
        re-runs the AI, and it stays the only one that does. A newly appeared
        entry is listed immediately and carries no verdict until asked for.
        """
        if self._ai_worker and self._ai_worker.isRunning():
            return                      # a full analysis is mid-flight; leave it alone
        if not self._entries:
            # Nothing to refresh yet. The first read of the machine stays the
            # explicit action it already was - the idle page offers "Analyze
            # Startups" for it - because walking the registry and the Startup
            # folders on a page the user has only glanced at is work nobody
            # asked for. This is the refresh, not the first analysis.
            return

        now = time.monotonic()
        if now - StartupsScreen._last_refresh < self._REFRESH_INTERVAL_S:
            return
        StartupsScreen._last_refresh = now

        from app.services.startup_detector import detect_startup_entries
        try:
            found = detect_startup_entries()
        except Exception:
            return                      # a refresh nobody asked for must not break the page

        # Carry the AI work across for entries that are still here. Matching is
        # by key, which is what the row widgets and the selection are keyed on.
        previous = {e.key: e for e in self._entries}
        for entry in found:
            old = previous.get(entry.key)
            if old is None:
                continue
            entry.ai_status = old.ai_status
            entry.ai_explanation = old.ai_explanation
            entry.recommendation = old.recommendation

        self._entries = found
        # Nothing is cleared first: the rows on screen are replaced by the
        # rebuilt list, so reopening does not blank a list mid-read.
        self._reapply_filters()
        # The selected entry may have been uninstalled between visits.
        if self._selected_key and self._selected_key not in {e.key for e in self._entries}:
            self._selected_key = None
            self._clear_detail_sidebar()

    def _analyze(self):
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.cancel()
            if not self._ai_worker.wait(500):
                self._ai_worker = None

        from app.services.startup_detector import detect_startup_entries
        self._entries = detect_startup_entries()
        self._filtered = [e for e in self._entries if self._passes_filter(e)]
        self._selected_key = None
        self._show_results()
        self._start_ai()

    def _start_ai(self, entries: list | None = None):
        """Run the model over *entries*, defaulting to the whole list.

        The worker analyses everything it is handed - there is no skip for an
        entry that already has a verdict - so the subset matters. Re-analyze
        passes nothing and gets the full run it promises; the refresh on open
        passes only what arrived since the last visit, or opening the page
        would silently do the expensive thing the button is for.
        """
        if not self._settings_store:
            return
        if not self._settings_store.get("ai_startups_enabled", True):
            for e in self._entries:
                e.ai_status = "disabled"
            self._reapply_filters()
            return

        targets = self._entries if entries is None else entries
        if not targets:
            return

        self._ai_worker = StartupAIWorker(targets, self._settings_store, parent=self)
        self._ai_worker.entry_updated.connect(self._on_ai_entry_updated)
        self._ai_worker.queue_status.connect(self._on_queue_status)
        self._ai_worker.start()

    def _on_ask_ai_startup(self, entry: StartupEntry) -> str:
        """On-demand AI for a single startup entry — runs even when startup AI
        is switched off. Returns "" when queued, or a reason code otherwise."""
        if not self._settings_store:
            return "unavailable"
        if not self._settings_store.get("ai_model", ""):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, tr("AI model needed"),
                tr("Select an AI model in Settings to use Ask AI."),
            )
            return "no-model"
        entry.ai_status = "analyzing"
        entry.ai_explanation = ""
        entry.ai_error = ""
        worker = StartupAIWorker([entry], self._settings_store, parent=self)
        worker.entry_updated.connect(self._on_ai_entry_updated)
        worker.queue_status.connect(self._on_queue_status)
        # Keep a reference so the QThread isn't collected mid-run, and drop it
        # (and its signals) once finished.
        self._ask_workers.append(worker)
        worker.finished.connect(lambda w=worker: self._ask_workers.remove(w)
                                if w in self._ask_workers else None)
        worker.start()
        return ""

    def _on_ai_entry_updated(self, entry: StartupEntry):
        row = self._row_widgets.get(entry.key)
        if row is not None:
            row.update_entry(entry)
        if self._selected_key == entry.key and self._right_sidebar is not None:
            self._show_detail_sidebar(entry)
        if self._btn_retry_ai is not None:
            ai_running = self._ai_worker is not None and self._ai_worker.isRunning()
            has_failed = any(e.ai_status == "failed" for e in self._entries)
            self._btn_retry_ai.setVisible(has_failed and not ai_running)

    def _on_queue_status(self, status: str):
        lbl = getattr(self, "_queue_lbl", None)
        if lbl is None:
            return
        if status == "waiting":
            # U+25D4, not U+25D0 — see _AI_SYMBOL in findings_table_model.
            lbl.setText(tr("◔ AI queued — waiting for another analysis to finish"))
            lbl.setVisible(True)
        else:
            lbl.setVisible(False)

    def _retry_failed_ai(self):
        failed = [e for e in self._entries if e.ai_status == "failed"]
        if not failed:
            return
        for entry in failed:
            entry.ai_status = "none"
            entry.ai_explanation = ""
            entry.ai_error = ""
        if self._btn_retry_ai is not None:
            self._btn_retry_ai.setVisible(False)
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.cancel()
            if not self._ai_worker.wait(500):
                self._ai_worker = None
        if not self._settings_store:
            return
        self._rebuild_entry_list()
        self._ai_worker = StartupAIWorker(failed, self._settings_store, parent=self)
        self._ai_worker.entry_updated.connect(self._on_ai_entry_updated)
        self._ai_worker.queue_status.connect(self._on_queue_status)
        self._ai_worker.start()

    def _open_task_manager(self):
        try:
            subprocess.Popen(["taskmgr.exe"])
        except OSError:
            pass
