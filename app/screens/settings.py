"""Settings screen — compact workstation configuration with instant apply."""

from __future__ import annotations

import os
import threading
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QLineEdit, QFrame, QRadioButton,
    QScrollArea, QSlider, QStackedWidget,
)
from PySide6.QtCore import Qt, Signal, QObject

from app.widgets.panels import Panel, apply_tactical_label
from app.widgets.controls import TacticalComboBox
from app.themes.theme_manager import THEME_NAMES, THEME_KEYS, get_palette, theme_signaller
from app.i18n import tr


# ─── Helpers ───────────────────────────────────────────────

def _contains_widget_type(widget: QWidget, widget_types: tuple[type, ...]) -> bool:
    if isinstance(widget, widget_types):
        return True
    return any(isinstance(child, widget_types) for child in widget.findChildren(QWidget))


def _setting_row(label_text: str, desc: str, widget: QWidget) -> QVBoxLayout:
    """A setting row: label col (220px) + widget col, with optional description."""
    p = get_palette()
    outer = QVBoxLayout()
    outer.setSpacing(0)
    outer.setContentsMargins(0, 0, 0, 0)

    row = QHBoxLayout()
    row.setSpacing(14)

    # Label column
    label_col = QVBoxLayout()
    label_col.setSpacing(4)
    lbl = QLabel(label_text)
    lbl.setStyleSheet("font-size: 12px;")
    lbl.setFixedWidth(208)
    label_col.addWidget(lbl)
    if desc:
        d = QLabel(desc)
        d.setObjectName("Dim")
        d.setStyleSheet("font-size: 11px;")
        d.setWordWrap(True)
        d.setFixedWidth(208)
        label_col.addWidget(d)
    row.addLayout(label_col)

    has_embedded_field = _contains_widget_type(widget, (QLineEdit, QComboBox, QSlider))
    has_choice_group = _contains_widget_type(widget, (QCheckBox, QRadioButton))
    is_interactive = has_embedded_field or has_choice_group
    text_only = isinstance(widget, QLabel) and not has_embedded_field and not has_choice_group

    # The previous "SettingsFieldWell" QFrame painted a darker rectangle
    # around every dropdown / checkbox / radio group / helper hint, which
    # read as an out-of-place dark green box on every settings page. Drop
    # the wrapper entirely — fields sit directly in the row column on the
    # panel background, matching the cleaner look of Home / Quick Cleanup.
    if text_only and not is_interactive:
        widget.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 11px; color: {p.get('text_dim', '#8a9b8f')};"
        )
    row.addWidget(widget, 0, Qt.AlignLeft | Qt.AlignTop)
    row.addStretch()

    outer.addLayout(row)
    return outer


def _panel_title(title: str, subtitle: str = "") -> QHBoxLayout:
    """Panel header matching design: pixel title + mono subtitle."""
    row = QHBoxLayout()
    row.setSpacing(10)
    t = QLabel(title)
    apply_tactical_label(t, font_size=11, letter_spacing=2)
    row.addWidget(t)
    if subtitle:
        s = QLabel(f"// {subtitle}")
        s.setObjectName("Muted")
        s.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
        row.addWidget(s)
    row.addStretch()
    return row


def _divider() -> QFrame:
    """Dashed-style divider between setting rows."""
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet("background: transparent; border-top: 1px dashed palette(mid);")
    return f


class _ConnectionResult(QObject):
    """Helper to emit connection test result from background thread."""
    result = Signal(bool, str, list)


# ─── Settings Screen ──────────────────────────────────────

_SECTIONS = [
    ("general",   "General"),
    ("ai",        "AI"),
    ("scan",      "Scan"),
    ("about",     "About"),
]

_SECTION_SUBS = {
    "general":   "appearance, language",
    "ai":        "local model · explanation · performance",
    "scan":      "safeguards · cleanup method",
    "about":     "build · paths · diagnostics",
}


class SettingsScreen(QWidget):

    settings_saved = Signal()

    def __init__(self, theme_callback=None, settings_store=None, parent=None):
        super().__init__(parent)
        self._theme_callback = theme_callback
        self._store = settings_store
        self._styled_panels: list[QFrame] = []
        self._styled_inputs: list[QLineEdit] = []
        self._styled_combos: list[TacticalComboBox] = []
        self._styled_checkboxes: list[QCheckBox] = []
        self._styled_radios: list[QRadioButton] = []
        self._conn_result = _ConnectionResult(self)
        self._conn_result.result.connect(self._on_connection_result)
        self._lang_dirty = False
        self._build_ui()
        self._load_from_store()
        theme_signaller().theme_changed.connect(self._refresh_local_styles)

    def set_settings_store(self, store):
        self._store = store
        self._load_from_store()

    # ─── Persistence ────────────────────────────────────────

    def reload_close_behavior(self):
        """Sync the close-while-busy dropdown with the stored value.

        Called on load and when the screen is shown, so a change made through
        the close dialog's 'Don't ask again' is reflected here too.
        """
        close_behavior = self._store.get("close_behavior", "ask") if self._store else "ask"
        idx = self._close_behavior_combo.findData(close_behavior)
        if idx >= 0:
            self._close_behavior_combo.blockSignals(True)
            self._close_behavior_combo.setCurrentIndex(idx)
            self._close_behavior_combo.blockSignals(False)

    def _load_from_store(self):
        """Load persisted settings into UI widgets."""
        if not self._store:
            return
        self._endpoint_input.setText(self._store.get("ai_endpoint"))
        self._timeout_slider.setValue(self._store.get("ai_timeout"))
        self._timeout_val.setText(f"{self._store.get('ai_timeout')} s")
        self._concurrent_slider.setValue(self._store.get("ai_max_concurrent"))
        self._concurrent_val.setText(str(self._store.get("ai_max_concurrent")))

        # Tone
        tone = self._store.get("ai_tone")
        for i in range(self._tone_combo.count()):
            if self._tone_combo.itemText(i) == tone:
                self._tone_combo.setCurrentIndex(i)
                break

        # Length
        length = self._store.get("ai_length")
        for i in range(self._length_combo.count()):
            if self._length_combo.itemText(i) == length:
                self._length_combo.setCurrentIndex(i)
                break

        # AI explanation language
        ai_lang = self._store.get("ai_explanation_language", "English")
        for i in range(self._ai_lang_combo.count()):
            if self._ai_lang_combo.itemText(i) == ai_lang:
                self._ai_lang_combo.setCurrentIndex(i)
                break

        # Toggles
        self._cb_findings.setChecked(self._store.get("ai_findings_enabled", True))
        self._cb_startups.setChecked(self._store.get("ai_startups_enabled", True))
        self._cb_cleanup_hints.setChecked(self._store.get("ai_cleanup_hints_enabled", False))
        self._cb_risky_only.setChecked(self._store.get("ai_explain_risky_only"))

        # Cleanup safety
        if self._store.get("perm_delete_enabled", False):
            self._store.set_and_save("perm_delete_enabled", False)
        self._rb_recycle.setChecked(True)
        self._rb_permanent.setChecked(False)
        self._rb_permanent.setEnabled(False)
        self._cb_confirm_risky.setChecked(self._store.get("confirm_risky_cleanup", True))
        self._cb_cross_volumes.setChecked(self._store.get("scan_cross_volumes", False))

        # Close-while-busy behavior
        self.reload_close_behavior()

        # UI language
        ui_lang = self._store.get("ui_language", "English")
        for i in range(self._lang_combo.count()):
            if self._lang_combo.itemText(i) == ui_lang:
                self._lang_combo.setCurrentIndex(i)
                break
        self._lang_dirty = False
        self._btn_apply_lang.setEnabled(False)

        # Theme chip — restore visual selection
        saved_theme = self._store.get("theme", "forest")
        for i, key in enumerate(THEME_KEYS):
            self._theme_btns[i].setChecked(key == saved_theme)

        # Saved model — show immediately without waiting for connection
        saved_model = self._store.get("ai_model", "")
        if saved_model:
            # Block signals: programmatic repopulation must not fire
            # currentTextChanged → _save_model and clobber the stored model.
            self._model_combo.blockSignals(True)
            try:
                self._model_combo.clear()
                self._model_combo.addItem(saved_model, 0)
                self._model_combo.setCurrentIndex(0)
            finally:
                self._model_combo.blockSignals(False)
            self._conn_status_lbl.setText("saved · not verified")
            self._conn_status_lbl.setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 11px; color: {get_palette().get('review', '#d8b46a')};"
            )
        else:
            self._populate_fallback_models()

        # Auto-test connection in background to refresh model list
        self._auto_test_connection()
        self._update_model_meta()
        self._update_library_summary()

    def _input_qss(self) -> str:
        p = get_palette()
        return (
            f"QLineEdit {{ background: {p.get('panel', '#141d18')}; "
            f"color: {p.get('text', '#d6e2da')}; border: 1px solid {p.get('border', '#25332b')}; "
            "padding: 4px 10px; min-height: 26px; border-radius: 2px; "
            "font-family: 'JetBrains Mono'; font-size: 11px; } "
            f"QLineEdit:hover {{ border-color: {p.get('border_hover', '#3a5648')}; background: {p.get('panel_hover', '#1d2c25')}; }} "
            f"QLineEdit:focus {{ border-color: {p.get('accent', '#7cc596')}; }}"
        )

    def _apply_combo_style(self, combo: TacticalComboBox):
        # Use the palette as-is so the combo's panel_alt matches the section
        # panel's panel_alt — no darker rectangle around the dropdown that
        # would read as an out-of-place "dark green box" on the AI page.
        combo.apply_reference_style(get_palette(), compact=True)
        combo.setSizeAdjustPolicy(QComboBox.AdjustToContentsOnFirstShow)
        if combo not in self._styled_combos:
            self._styled_combos.append(combo)

    def _utility_btn_qss(self) -> str:
        return "padding: 3px 10px; min-height: 26px; font-size: 11px;"

    def _toggle_indicator_qss(self, radio: bool = False) -> str:
        p = get_palette()
        size = 14 if radio else 15
        radius = 7 if radio else 2
        checked_bg = p.get("accent", "#7cc596")
        border = p.get("border_alt", "#2b3d33")
        hover = p.get("border_hover", "#3a5648")
        bg = p.get("panel", "#141d18")
        base = "QRadioButton" if radio else "QCheckBox"
        return (
            f"{base} {{ spacing: 8px; font-size: 11px; color: {p.get('text', '#d6e2da')}; }}"
            f"{base}::indicator {{ width: {size}px; height: {size}px; border-radius: {radius}px; "
            f"border: 1px solid {border}; background: {bg}; }}"
            f"{base}::indicator:hover {{ border-color: {hover}; }}"
            f"{base}::indicator:checked {{ border: 1px solid {checked_bg}; background: {checked_bg}; }}"
            f"{base}::indicator:unchecked {{ image: none; }}"
            f"{base}::indicator:checked {{ image: none; }}"
            f"{base}::indicator:checked:disabled {{ background: {checked_bg}; }}"
            f"{base}::indicator:disabled {{ border-color: {p.get('border', '#213028')}; }}"
        )

    def _style_checkbox(self, checkbox: QCheckBox):
        checkbox.setStyleSheet(self._toggle_indicator_qss(radio=False))
        if checkbox not in self._styled_checkboxes:
            self._styled_checkboxes.append(checkbox)

    def _style_radio(self, radio: QRadioButton):
        radio.setStyleSheet(self._toggle_indicator_qss(radio=True))
        if radio not in self._styled_radios:
            self._styled_radios.append(radio)

    def _helper_style(self) -> str:
        p = get_palette()
        return f"font-size: 10px; color: {p.get('text_faint', '#57685e')};"

    def _warning_helper_style(self) -> str:
        p = get_palette()
        return f"font-size: 10px; color: {p.get('review', '#d8b46a')};"

    def _about_button_qss(self, warning: bool = False) -> str:
        p = get_palette()
        border = p.get("review", "#d8b46a") if warning else p.get("border_hover", "#3a5648")
        hover_bg = p.get("review_soft", "#2c2516") if warning else p.get("panel_hover", "#1d2c25")
        hover_border = p.get("review", "#d8b46a") if warning else p.get("accent", "#7cc596")
        text = p.get("review", "#d8b46a") if warning else p.get("text", "#d6e2da")
        return (
            f"QPushButton {{ padding: 4px 11px; min-height: 26px; font-size: 11px; "
            f"border: 1px solid {border}; color: {text}; background: transparent; border-radius: 2px; }}"
            f"QPushButton:hover {{ background: {hover_bg}; border-color: {hover_border}; }}"
        )

    def _model_level_label(self, size_bytes: int) -> str:
        from app.services.ollama_client import format_model_size
        if size_bytes <= 0:
            return tr("small • local model")
        gb = size_bytes / (1024 ** 3)
        tier = tr("small") if gb < 4 else tr("medium") if gb < 10 else tr("large")
        return f"{tier} • {tr('local')} • ~{format_model_size(size_bytes)}"

    def _update_library_summary(self):
        count = self._model_combo.count()
        noun = tr("model") if count == 1 else tr("models")
        self._library_count_lbl.setText(f"{count} {noun} {tr('available')}")

    def _update_model_meta(self):
        idx = self._model_combo.currentIndex()
        if idx < 0:
            self._model_meta_lbl.setText("")
            return
        size_bytes = int(self._model_combo.itemData(idx, Qt.UserRole) or 0)
        self._model_meta_lbl.setText(self._model_level_label(size_bytes))

    def _save_value(self, key: str, value):
        if self._store:
            self._store.set_and_save(key, value)
            self.settings_saved.emit()

    def _save_model(self):
        model_text = self._model_combo.currentText()
        model_name = model_text.split(" · ")[0] if " · " in model_text else model_text
        self._save_value("ai_model", model_name)

    def _on_language_changed(self):
        if not self._store:
            return
        self._lang_dirty = self._lang_combo.currentText() != self._store.get("ui_language", "English")
        self._btn_apply_lang.setEnabled(self._lang_dirty)

    def _apply_language(self):
        if not self._store or not self._lang_dirty:
            return
        self._store.set_and_save("ui_language", self._lang_combo.currentText())
        self._lang_dirty = False
        self._btn_apply_lang.setEnabled(False)
        self.settings_saved.emit()

    # ─── UI Build ───────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top header row
        top_row = QHBoxLayout()
        top_row.setContentsMargins(22, 14, 22, 10)
        top_row.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        self._page_title = QLabel(tr("SETTINGS"))
        apply_tactical_label(self._page_title, font_size=16, letter_spacing=4)
        title_col.addWidget(self._page_title)

        self._section_title = QLabel(tr("GENERAL"))
        apply_tactical_label(self._section_title, font_size=9, letter_spacing=2)
        title_col.addWidget(self._section_title)

        top_row.addLayout(title_col)

        self._section_sub = QLabel(f"// {tr(_SECTION_SUBS['general'])}")
        self._section_sub.setObjectName("Muted")
        self._section_sub.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        top_row.addWidget(self._section_sub)
        top_row.addStretch()
        outer.addLayout(top_row)

        # Compact section tabs
        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(22, 0, 22, 10)
        nav_row.setSpacing(6)
        self._nav_btns: dict[str, QPushButton] = {}
        for sec_id, sec_label in _SECTIONS:
            btn = QPushButton(tr(sec_label))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setObjectName("Subtle")
            btn.setStyleSheet("padding: 4px 10px; font-size: 11px;")
            btn.clicked.connect(lambda checked=False, s=sec_id: self._switch_section(s))
            self._nav_btns[sec_id] = btn
            nav_row.addWidget(btn)
        nav_row.addStretch()
        outer.addLayout(nav_row)

        # Stacked sections
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_general())
        self._stack.addWidget(self._build_ai())
        self._stack.addWidget(self._build_scan())
        self._stack.addWidget(self._build_about())
        outer.addWidget(self._stack, stretch=1)

        # Default to General
        self._switch_section("general")

    def _switch_section(self, sec_id: str):
        """Switch active section in nav rail and stacked widget."""
        for sid, btn in self._nav_btns.items():
            active = sid == sec_id
            btn.setChecked(active)
            if active:
                btn.setStyleSheet(
                    f"padding: 4px 10px; font-size: 11px; "
                    f"background: {get_palette().get('accent_soft', '#1b2e22')}; "
                    f"border: 1px solid {get_palette().get('accent', '#7cc596')}; "
                    f"color: {get_palette().get('accent', '#7cc596')};"
                )
            else:
                btn.setStyleSheet("padding: 4px 10px; font-size: 11px;")

        idx = [s[0] for s in _SECTIONS].index(sec_id)
        self._stack.setCurrentIndex(idx)
        label = dict(_SECTIONS).get(sec_id, sec_id)
        self._section_title.setText(tr(label).upper())
        self._section_sub.setText(f"// {tr(_SECTION_SUBS.get(sec_id, ''))}")

    # ─── Section Builders ──────────────────────────────────

    def _build_general(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("border: none;")

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(22, 12, 22, 22)
        lay.setSpacing(16)

        # Appearance panel
        app_panel = Panel(alt=True)
        app_lay = app_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        app_lay.addLayout(_panel_title(tr("Appearance"), tr("theme & density")))
        self._register_styled_panel(app_panel)

        # Theme chips
        theme_w = QWidget()
        theme_h = QHBoxLayout(theme_w)
        theme_h.setContentsMargins(0, 0, 0, 0)
        theme_h.setSpacing(6)
        self._theme_btns = {}
        for i, name in enumerate(THEME_NAMES):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setObjectName("Subtle")
            btn.setStyleSheet("padding: 4px 9px; font-size: 11px;")
            if i == 0:
                btn.setChecked(True)
            btn.clicked.connect(lambda checked=False, idx=i: self._on_theme_chip(idx))
            self._theme_btns[i] = btn
            theme_h.addWidget(btn)
        theme_h.addStretch()
        app_lay.addLayout(_setting_row(tr("Theme"), tr("Color palette for the entire workstation."), theme_w))

        lay.addWidget(app_panel)

        # Language panel
        lang_panel = Panel(alt=True)
        lang_lay = lang_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        lang_lay.addLayout(_panel_title(tr("Language & Locale"), tr("strings")))
        self._register_styled_panel(lang_panel)

        lang_w = QWidget()
        lang_row = QHBoxLayout(lang_w)
        lang_row.setContentsMargins(0, 0, 0, 0)
        lang_row.setSpacing(8)
        self._lang_combo = TacticalComboBox()
        self._lang_combo.addItems(["English", "Ukrainian"])
        self._lang_combo.setFixedWidth(168)
        self._lang_combo.currentTextChanged.connect(lambda _: self._on_language_changed())
        self._apply_combo_style(self._lang_combo)
        lang_row.addWidget(self._lang_combo, alignment=Qt.AlignVCenter)
        self._btn_apply_lang = QPushButton(tr("Apply"))
        self._btn_apply_lang.setCursor(Qt.PointingHandCursor)
        self._btn_apply_lang.setEnabled(False)
        self._btn_apply_lang.setFixedHeight(30)
        self._btn_apply_lang.setMinimumWidth(80)
        self._restyle_apply_button()
        self._btn_apply_lang.clicked.connect(self._apply_language)
        lang_row.addWidget(self._btn_apply_lang, alignment=Qt.AlignVCenter)
        lang_row.addStretch()
        lang_lay.addLayout(_setting_row(
            tr("Interface language"),
            tr("Affects UI labels and navigation strings only. AI explanation language is set separately in AI settings."),
            lang_w,
        ))

        lay.addWidget(lang_panel)
        lay.addStretch()

        scroll.setWidget(content)
        return scroll

    def _build_ai(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("border: none;")

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(22, 12, 22, 22)
        lay.setSpacing(16)

        # Local Model Server panel
        server_panel = Panel(alt=True)
        srv_lay = server_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        srv_lay.addLayout(_panel_title(tr("Local Model Server"), tr("endpoint")))
        self._register_styled_panel(server_panel)

        # Endpoint
        self._endpoint_input = QLineEdit("http://127.0.0.1:11434")
        self._endpoint_input.setStyleSheet(self._input_qss())
        self._styled_inputs.append(self._endpoint_input)
        self._endpoint_input.setFixedWidth(280)
        self._endpoint_input.editingFinished.connect(
            lambda: self._save_value("ai_endpoint", self._endpoint_input.text().strip())
        )
        ep_w = QWidget()
        ep_h = QHBoxLayout(ep_w)
        ep_h.setContentsMargins(0, 0, 0, 0)
        ep_h.setSpacing(8)
        ep_h.addWidget(self._endpoint_input)
        self._btn_test = QPushButton(tr("Test"))
        self._btn_test.setObjectName("Ghost")
        self._btn_test.setCursor(Qt.PointingHandCursor)
        self._btn_test.setStyleSheet(self._utility_btn_qss())
        self._btn_test.clicked.connect(self._test_connection)
        ep_h.addWidget(self._btn_test)
        srv_lay.addLayout(_setting_row(tr("Endpoint"), tr("Ollama-compatible HTTP server. Vigil never reaches the public network."), ep_w))

        # Connection status
        self._conn_status_lbl = QLabel("")
        self._conn_status_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        srv_lay.addLayout(_setting_row(tr("Connection"), tr("Last contacted server."), self._conn_status_lbl))

        # Library / refresh
        lib_w = QWidget()
        lib_h = QHBoxLayout(lib_w)
        lib_h.setContentsMargins(0, 0, 0, 0)
        lib_h.setSpacing(8)
        self._library_count_lbl = QLabel(tr("0 models available"))
        self._library_count_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        lib_h.addWidget(self._library_count_lbl)
        lib_h.addStretch()
        self._btn_refresh_models = QPushButton(tr("Refresh"))
        self._btn_refresh_models.setObjectName("Ghost")
        self._btn_refresh_models.setCursor(Qt.PointingHandCursor)
        self._btn_refresh_models.setFixedWidth(88)
        self._btn_refresh_models.setStyleSheet(self._utility_btn_qss())
        self._btn_refresh_models.clicked.connect(self._test_connection)
        lib_h.addWidget(self._btn_refresh_models)
        srv_lay.addLayout(_setting_row(tr("Library"), tr("Local model catalog read from the server."), lib_w))

        # Local-only disclosure
        disc = QLabel(
            tr("Vigil refuses to connect to non-loopback or non-LAN endpoints. "
               "There is no cloud fallback, no API key field, no analytics.")
        )
        disc.setObjectName("Dim")
        disc.setStyleSheet("font-size: 11px; padding: 2px 0px;")
        disc.setWordWrap(True)
        srv_lay.addWidget(disc)

        lay.addWidget(server_panel)

        # Model Selection panel
        model_panel = Panel(alt=True)
        mod_lay = model_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        mod_lay.addLayout(_panel_title(tr("Model Selection"), tr("active model")))
        self._register_styled_panel(model_panel)

        self._model_combo = TacticalComboBox()
        self._model_combo.setFixedWidth(252)
        self._apply_combo_style(self._model_combo)
        self._model_combo.currentTextChanged.connect(lambda _: self._save_model())
        self._model_combo.currentIndexChanged.connect(lambda _: self._update_model_meta())
        model_w = QWidget()
        model_v = QVBoxLayout(model_w)
        model_v.setContentsMargins(0, 0, 0, 0)
        model_v.setSpacing(4)
        model_v.addWidget(self._model_combo)
        self._model_meta_lbl = QLabel("")
        self._model_meta_lbl.setObjectName("Muted")
        self._model_meta_lbl.setStyleSheet(self._helper_style())
        model_v.addWidget(self._model_meta_lbl)
        # Freshness guidance — explanation quality is bounded by the model's
        # knowledge cutoff. A model trained before an app existed can't identify
        # it and falls back to restating the folder name.
        model_hint1 = QLabel(tr("Newer, larger models give better explanations and recognise more recent apps."))
        model_hint1.setObjectName("Dim")
        model_hint1.setStyleSheet(self._helper_style())
        model_hint1.setWordWrap(True)
        model_v.addWidget(model_hint1)
        model_hint2 = QLabel(tr("A model trained before an app existed may not identify it correctly."))
        model_hint2.setObjectName("Dim")
        model_hint2.setStyleSheet(self._helper_style())
        model_hint2.setWordWrap(True)
        model_v.addWidget(model_hint2)
        mod_lay.addLayout(_setting_row(tr("Active model"), tr("Choose the local model used for explanations."), model_w))
        self._populate_fallback_models()

        lay.addWidget(model_panel)

        # Explanation panel
        expl_panel = Panel(alt=True)
        expl_lay = expl_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        expl_lay.addLayout(_panel_title(tr("Explanation"), tr("style & length")))
        self._register_styled_panel(expl_panel)

        self._tone_combo = TacticalComboBox()
        self._tone_combo.addItems(["Neutral", "Friendly", "Professional", "Technical"])
        self._tone_combo.setCurrentIndex(0)
        self._tone_combo.setFixedWidth(146)
        self._apply_combo_style(self._tone_combo)
        self._tone_combo.currentTextChanged.connect(lambda text: self._save_value("ai_tone", text))
        expl_lay.addLayout(_setting_row(tr("Explanation style"), tr("Affects terminology and tone, not personality."), self._tone_combo))

        self._length_combo = TacticalComboBox()
        self._length_combo.addItems(["Compact", "Standard", "Detailed"])
        self._length_combo.setCurrentIndex(1)
        self._length_combo.setFixedWidth(140)
        self._apply_combo_style(self._length_combo)
        self._length_combo.currentTextChanged.connect(lambda text: self._save_value("ai_length", text))
        expl_lay.addLayout(_setting_row(tr("Explanation length"), tr("Controls how much the model writes per finding."), self._length_combo))

        expl_lay.addWidget(_divider())

        # AI explanation language
        ai_lang_w = QWidget()
        ai_lang_lay = QVBoxLayout(ai_lang_w)
        ai_lang_lay.setContentsMargins(0, 0, 0, 0)
        ai_lang_lay.setSpacing(4)
        self._ai_lang_combo = TacticalComboBox()
        self._ai_lang_combo.addItems(["English", "Ukrainian"])
        self._ai_lang_combo.setCurrentIndex(0)
        self._ai_lang_combo.setFixedWidth(168)
        self._apply_combo_style(self._ai_lang_combo)
        self._ai_lang_combo.currentTextChanged.connect(lambda text: self._save_value("ai_explanation_language", text))
        ai_lang_lay.addWidget(self._ai_lang_combo)
        ai_lang_hint1 = QLabel(tr("Make sure your local AI model supports the selected language well."))
        ai_lang_hint1.setObjectName("Dim")
        ai_lang_hint1.setStyleSheet(self._helper_style())
        ai_lang_hint1.setWordWrap(True)
        ai_lang_lay.addWidget(ai_lang_hint1)
        ai_lang_hint2 = QLabel(tr("Smaller local models may produce lower quality explanations in some languages."))
        ai_lang_hint2.setObjectName("Dim")
        ai_lang_hint2.setStyleSheet(self._helper_style())
        ai_lang_hint2.setWordWrap(True)
        ai_lang_lay.addWidget(ai_lang_hint2)
        expl_lay.addLayout(_setting_row(
            tr("AI explanation language"),
            tr("Independent from interface language. The AI will answer in this language."),
            ai_lang_w,
        ))

        ai_toggle_w = QWidget()
        ai_toggle_l = QVBoxLayout(ai_toggle_w)
        ai_toggle_l.setContentsMargins(0, 0, 0, 0)
        ai_toggle_l.setSpacing(6)
        self._cb_findings = QCheckBox(tr("Findings"))
        self._style_checkbox(self._cb_findings)
        self._cb_findings.toggled.connect(lambda checked: self._save_value("ai_findings_enabled", checked))
        ai_toggle_l.addWidget(self._cb_findings)
        self._cb_startups = QCheckBox(tr("Startups"))
        self._style_checkbox(self._cb_startups)
        self._cb_startups.toggled.connect(lambda checked: self._save_value("ai_startups_enabled", checked))
        ai_toggle_l.addWidget(self._cb_startups)
        self._cb_cleanup_hints = QCheckBox(tr("Cleanup hints"))
        self._style_checkbox(self._cb_cleanup_hints)
        self._cb_cleanup_hints.toggled.connect(lambda checked: self._save_value("ai_cleanup_hints_enabled", checked))
        ai_toggle_l.addWidget(self._cb_cleanup_hints)
        expl_lay.addLayout(_setting_row(tr("AI explanations"), tr("Choose where local AI guidance appears."), ai_toggle_w))

        self._cb_risky_only = QCheckBox(tr("Enable"))
        self._style_checkbox(self._cb_risky_only)
        self._cb_risky_only.toggled.connect(lambda checked: self._save_value("ai_explain_risky_only", checked))
        expl_lay.addLayout(_setting_row(tr("Explain only risky findings"), tr("Skip the model for findings flagged as safe."), self._cb_risky_only))

        lay.addWidget(expl_panel)

        # Performance panel
        perf_panel = Panel(alt=True)
        perf_lay = perf_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        perf_lay.addLayout(_panel_title(tr("Performance"), tr("runtime")))
        self._register_styled_panel(perf_panel)

        # Emergency timeout
        timeout_w = QWidget()
        timeout_h = QHBoxLayout(timeout_w)
        timeout_h.setContentsMargins(0, 0, 0, 0)
        timeout_h.setSpacing(10)
        self._timeout_slider = QSlider(Qt.Horizontal)
        self._timeout_slider.setRange(60, 300)
        self._timeout_slider.setSingleStep(60)
        self._timeout_slider.setPageStep(60)
        self._timeout_slider.setValue(180)
        self._timeout_slider.setFixedWidth(200)
        self._timeout_slider.setFixedHeight(20)
        self._timeout_val = QLabel("180 s")
        self._timeout_val.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
        self._timeout_val.setFixedWidth(64)
        self._timeout_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._timeout_slider.valueChanged.connect(lambda v: self._timeout_val.setText(f"{v} s"))
        self._timeout_slider.sliderReleased.connect(lambda: self._save_value("ai_timeout", self._timeout_slider.value()))
        timeout_h.addWidget(self._timeout_slider)
        timeout_h.addWidget(self._timeout_val)
        timeout_hint = QLabel(tr("Lower = quicker recovery if a model stalls"))
        timeout_hint.setObjectName("Muted")
        timeout_hint.setStyleSheet(self._helper_style())
        timeout_h.addWidget(timeout_hint)
        perf_lay.addLayout(_setting_row(
            tr("Emergency AI timeout"),
            tr("Only used if the local model stops responding. Normal explanations wait until the model finishes."),
            timeout_w,
        ))

        # Concurrency
        conc_w = QWidget()
        conc_h = QHBoxLayout(conc_w)
        conc_h.setContentsMargins(0, 0, 0, 0)
        conc_h.setSpacing(10)
        self._concurrent_slider = QSlider(Qt.Horizontal)
        self._concurrent_slider.setRange(1, 8)
        self._concurrent_slider.setValue(3)
        self._concurrent_slider.setFixedWidth(200)
        self._concurrent_slider.setFixedHeight(20)
        self._concurrent_val = QLabel("3")
        self._concurrent_val.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
        self._concurrent_val.setFixedWidth(64)
        self._concurrent_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._concurrent_slider.valueChanged.connect(lambda v: self._concurrent_val.setText(str(v)))
        self._concurrent_slider.sliderReleased.connect(lambda: self._save_value("ai_max_concurrent", self._concurrent_slider.value()))
        conc_h.addWidget(self._concurrent_slider)
        conc_h.addWidget(self._concurrent_val)
        conc_hint = QLabel(tr("1 = quieter · 2 = recommended · 4+ = aggressive"))
        conc_hint.setObjectName("Muted")
        conc_hint.setStyleSheet(self._helper_style())
        conc_h.addWidget(conc_hint)
        perf_lay.addLayout(_setting_row(
            tr("Max simultaneous AI explanations"),
            tr("Higher values may use significantly more CPU/RAM/VRAM depending on your "
               "local model and hardware. Recommended: 3. Use 1 for weaker machines."),
            conc_w,
        ))

        lay.addWidget(perf_panel)
        lay.addStretch()

        scroll.setWidget(content)
        return scroll

    def _build_scan(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("border: none;")

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(22, 12, 22, 22)
        lay.setSpacing(16)

        # Safeguards
        safe_panel = Panel(alt=True)
        s_lay = safe_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        s_lay.addLayout(_panel_title(tr("Safeguards"), tr("safe cleanup behavior")))
        self._register_styled_panel(safe_panel)

        self._cb_confirm_risky = QCheckBox(tr("Enable"))
        self._style_checkbox(self._cb_confirm_risky)
        self._cb_confirm_risky.setChecked(True)
        self._cb_confirm_risky.toggled.connect(lambda checked: self._save_value("confirm_risky_cleanup", checked))
        s_lay.addLayout(_setting_row(
            tr("Confirm risky cleanup"),
            tr("Ask before removing items that may still matter to you or an app."),
            self._cb_confirm_risky,
        ))

        self._cb_cross_volumes = QCheckBox(tr("Enable"))
        self._style_checkbox(self._cb_cross_volumes)
        self._cb_cross_volumes.toggled.connect(
            lambda checked: self._save_value("scan_cross_volumes", checked))
        s_lay.addLayout(_setting_row(
            tr("Scan across drives"),
            tr("Follow into other drives and volumes (mounted disks, junctions). "
               "Off keeps each scan on the drive you picked."),
            self._cb_cross_volumes,
        ))

        # Close behavior — what the window's close button does while a scan,
        # cleanup or AI job is still running.
        self._close_behavior_combo = TacticalComboBox()
        self._close_behavior_combo.addItem(tr("Ask me each time"), "ask")
        self._close_behavior_combo.addItem(tr("Keep running in background"), "background")
        self._close_behavior_combo.addItem(tr("Quit and stop the work"), "quit")
        self._close_behavior_combo.setFixedWidth(220)
        self._apply_combo_style(self._close_behavior_combo)
        self._close_behavior_combo.currentIndexChanged.connect(
            lambda _: self._save_value(
                "close_behavior", self._close_behavior_combo.currentData()))
        s_lay.addLayout(_setting_row(
            tr("When closing while busy"),
            tr("If a task is still running when you close the window, Vigil can "
               "ask, keep working in the system tray, or stop and quit."),
            self._close_behavior_combo,
        ))

        lay.addWidget(safe_panel)

        # File Handling
        fh_panel = Panel(alt=True)
        fh_lay = fh_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        fh_lay.addLayout(_panel_title(tr("File Handling"), tr("cleanup method")))
        self._register_styled_panel(fh_panel)

        method_w = QWidget()
        method_l = QVBoxLayout(method_w)
        method_l.setContentsMargins(0, 0, 0, 0)
        method_l.setSpacing(10)

        recycle_row = QWidget()
        recycle_l = QVBoxLayout(recycle_row)
        recycle_l.setContentsMargins(0, 0, 0, 0)
        recycle_l.setSpacing(2)
        self._rb_recycle = QRadioButton(tr("Move files to Recycle Bin"))
        self._style_radio(self._rb_recycle)
        self._rb_recycle.toggled.connect(lambda checked: checked and self._save_value("perm_delete_enabled", False))
        recycle_l.addWidget(self._rb_recycle)
        recycle_hint = QLabel(tr("Recommended. Files can be restored later."))
        recycle_hint.setObjectName("Muted")
        recycle_hint.setStyleSheet(self._helper_style())
        recycle_hint.setWordWrap(True)
        recycle_l.addWidget(recycle_hint)
        method_l.addWidget(recycle_row)

        permanent_row = QWidget()
        permanent_l = QVBoxLayout(permanent_row)
        permanent_l.setContentsMargins(0, 0, 0, 0)
        permanent_l.setSpacing(3)
        self._rb_permanent = QRadioButton(tr("Permanently delete files"))
        self._style_radio(self._rb_permanent)
        self._rb_permanent.setEnabled(False)
        permanent_l.addWidget(self._rb_permanent)
        warn_lbl = QLabel(
            tr("Not available yet. Cleanup currently uses the Recycle Bin only.")
        )
        warn_lbl.setObjectName("Muted")
        warn_lbl.setStyleSheet(self._warning_helper_style())
        warn_lbl.setWordWrap(True)
        permanent_l.addWidget(warn_lbl)
        method_l.addWidget(permanent_row)

        fh_lay.addLayout(_setting_row(
            tr("Cleanup method"),
            tr("Choose how removed files should be handled."),
            method_w,
        ))

        lay.addWidget(fh_panel)
        lay.addStretch()

        scroll.setWidget(content)
        return scroll

    def _build_about(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("border: none;")

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(22, 12, 22, 22)
        lay.setSpacing(16)

        # Build panel
        build_panel = Panel(alt=True)
        b_lay = build_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        b_lay.addLayout(_panel_title(tr("Build"), tr("product")) )
        self._register_styled_panel(build_panel)

        for k, v in [
            (tr("Version"), "1.0.0-beta.1"),
            (tr("Build"), "2026.06"),
        ]:
            val_lbl = QLabel(v)
            val_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
            b_lay.addLayout(_setting_row(k, "", val_lbl))

        lay.addWidget(build_panel)

        # Paths panel
        paths_panel = Panel(alt=True)
        p_lay = paths_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        p_lay.addLayout(_panel_title(tr("Paths"), tr("where Vigil lives on this machine")))
        self._register_styled_panel(paths_panel)

        for k, v in [
            (tr("Configuration"), "%APPDATA%\\Vigil\\config.json"),
            (tr("Logs"), "%LOCALAPPDATA%\\Vigil\\logs\\"),
            (tr("Reports"), "%LOCALAPPDATA%\\Vigil\\reports\\"),
            (tr("Scan cache"), "%LOCALAPPDATA%\\Vigil\\cache\\hashes.db"),
        ]:
            val_lbl = QLabel(v)
            val_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
            p_lay.addLayout(_setting_row(k, "", val_lbl))

        lay.addWidget(paths_panel)

        # Diagnostics
        diag_panel = Panel(alt=True)
        dg_lay = diag_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        dg_lay.addLayout(_panel_title(tr("Diagnostics"), tr("support")))
        self._register_styled_panel(diag_panel)

        btn_row = QWidget()
        br_lay = QHBoxLayout(btn_row)
        br_lay.setContentsMargins(0, 0, 0, 0)
        br_lay.setSpacing(8)

        # Use the theme's objectName button classes (#Ghost / #Danger) rather
        # than an inline stylesheet. Inline styles are baked once at build time
        # and were never refreshed on a theme switch, so in the paper theme the
        # "Open logs folder" text stayed light-on-light and vanished. The
        # objectName classes live in the app QSS and follow the theme.
        btn_logs = QPushButton(tr("Open logs folder"))
        btn_logs.setObjectName("Ghost")
        btn_logs.setCursor(Qt.PointingHandCursor)
        btn_logs.clicked.connect(self._open_logs_folder)
        br_lay.addWidget(btn_logs)

        btn_reset = QPushButton(tr("Reset all settings"))
        btn_reset.setObjectName("Danger")
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.clicked.connect(self._reset_all_settings)
        br_lay.addWidget(btn_reset)

        br_lay.addStretch()
        dg_lay.addWidget(btn_row)

        disc = QLabel(
            tr("Local-first storage analysis and cleanup assistant.\n"
               "No cloud processing. No background telemetry. Decisions stay on your machine.")
        )
        disc.setObjectName("Dim")
        disc.setStyleSheet(f"{self._helper_style()} line-height: 1.4;")
        disc.setWordWrap(True)
        dg_lay.addWidget(disc)

        lay.addWidget(diag_panel)
        lay.addStretch()

        scroll.setWidget(content)
        return scroll

    # ─── Actions ────────────────────────────────────────────

    def _on_theme_chip(self, index: int):
        for i, btn in self._theme_btns.items():
            btn.setChecked(i == index)
        if self._theme_callback:
            self._theme_callback(THEME_KEYS[index])
        if self._store:
            self._store.set_and_save("theme", THEME_KEYS[index] if index < len(THEME_KEYS) else "forest")

    def _test_connection(self):
        """Test Ollama connection in background thread."""
        self._btn_test.setEnabled(False)
        self._btn_refresh_models.setEnabled(False)
        self._conn_status_lbl.setText("testing…")
        self._conn_status_lbl.setStyleSheet(f"font-family: 'JetBrains Mono'; font-size: 11px; color: {get_palette().get('review', '#d8b46a')};")

        endpoint = self._endpoint_input.text().strip()

        def _worker():
            from app.services.ollama_client import test_connection, list_models
            ok, msg = test_connection(endpoint)
            models = list_models(endpoint) if ok else []
            self._conn_result.result.emit(ok, msg, models)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _on_connection_result(self, ok: bool, msg: str, models: list):
        self._btn_test.setEnabled(True)
        self._btn_refresh_models.setEnabled(True)
        if ok:
            self._conn_status_lbl.setText(msg)
            self._conn_status_lbl.setStyleSheet(f"font-family: 'JetBrains Mono'; font-size: 11px; color: {get_palette().get('safe', '#7cc596')};")
            # Populate model dropdown with real models. Block signals so the
            # repopulation does not fire _save_model and overwrite the stored
            # model with a transient selection (the cause of "model switching").
            self._model_combo.blockSignals(True)
            try:
                self._model_combo.clear()
                for m in models:
                    self._model_combo.addItem(m['name'], m.get("size", 0))
                # Select stored model if available
                if self._store:
                    saved_model = self._store.get("ai_model", "")
                    for i in range(self._model_combo.count()):
                        if saved_model == self._model_combo.itemText(i):
                            self._model_combo.setCurrentIndex(i)
                            break
            finally:
                self._model_combo.blockSignals(False)
        else:
            self._conn_status_lbl.setText(msg)
            self._conn_status_lbl.setStyleSheet(f"font-family: 'JetBrains Mono'; font-size: 11px; color: {get_palette().get('risk', '#d68a78')};")
            self._populate_fallback_models()
        self._update_library_summary()
        self._update_model_meta()

    def _auto_test_connection(self):
        """Silently test the saved endpoint in background on startup."""
        endpoint = self._endpoint_input.text().strip()
        if not endpoint:
            return

        def _worker():
            from app.services.ollama_client import test_connection, list_models
            ok, msg = test_connection(endpoint)
            models = list_models(endpoint) if ok else []
            try:
                self._conn_result.result.emit(ok, msg, models)
            except RuntimeError:
                pass  # Qt object already destroyed during shutdown

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _open_logs_folder(self):
        import os, subprocess
        local = os.environ.get("LOCALAPPDATA", "")
        logs_dir = os.path.join(local, "Vigil", "logs") if local else ""
        if logs_dir:
            os.makedirs(logs_dir, exist_ok=True)
            subprocess.Popen(["explorer", logs_dir])

    def _reset_all_settings(self):
        if self._store:
            self._store.reset()
            self._load_from_store()
            # Re-apply default theme
            default_theme = self._store.get("theme", "forest")
            if self._theme_callback:
                self._theme_callback(default_theme)

    def _settings_panel_qss(self) -> str:
        p = get_palette()
        return (
            f"QFrame#PanelAlt {{ background: {p.get('panel_alt', '#19231c')}; "
            f"border: 1px solid {p.get('border_alt', '#32443a')}; border-radius: 2px; }}"
        )

    def _register_styled_panel(self, panel: QFrame):
        panel.setStyleSheet(self._settings_panel_qss())
        self._styled_panels.append(panel)

    def _restyle_apply_button(self):
        """Re-apply the language Apply button style with the current palette.

        Extracted so theme switches can refresh the inline accent colours.
        """
        if not hasattr(self, "_btn_apply_lang"):
            return
        p = get_palette()
        self._btn_apply_lang.setStyleSheet(
            "QPushButton { "
            f"background: transparent; "
            f"border: 1px solid {p.get('accent', '#7cc596')}; "
            f"color: {p.get('accent', '#7cc596')}; "
            "padding: 4px 14px; border-radius: 2px; font-size: 11px; font-weight: 500; "
            "}"
            f"QPushButton:hover {{ background: {p.get('accent_soft', '#1b2e22')}; }}"
            "QPushButton:disabled { "
            f"border-color: {p.get('border', '#213028')}; "
            f"color: {p.get('text_faint', '#57685e')}; "
            "}"
        )

    def _refresh_local_styles(self, _theme_key: str = ""):
        for panel in self._styled_panels:
            panel.setStyleSheet(self._settings_panel_qss())
        for line_edit in self._styled_inputs:
            line_edit.setStyleSheet(self._input_qss())
        for combo in self._styled_combos:
            self._apply_combo_style(combo)
        # Checkboxes and radios bake the accent/border into their stylesheet at
        # build time, so they must be re-styled or they keep the old palette.
        for checkbox in self._styled_checkboxes:
            checkbox.setStyleSheet(self._toggle_indicator_qss(radio=False))
        for radio in self._styled_radios:
            radio.setStyleSheet(self._toggle_indicator_qss(radio=True))
        # The section tabs (General / AI / Scan / About) cache their inline
        # palette colours when made active in _switch_section, so without a
        # refresh the previously-active tab keeps the old theme's accent.
        active = next(
            (sid for sid, btn in getattr(self, "_nav_btns", {}).items() if btn.isChecked()),
            None,
        )
        if active:
            self._switch_section(active)
        # Re-apply the Apply button style with the current palette too.
        if hasattr(self, "_btn_apply_lang"):
            self._restyle_apply_button()

    def _populate_fallback_models(self):
        """Fill model combo with fallback options, including saved model."""
        from app.services.ollama_client import fallback_models
        saved_model = self._store.get("ai_model", "") if self._store else ""
        # Block signals: filling the fallback list must not fire _save_model and
        # overwrite the stored model with a fallback entry.
        self._model_combo.blockSignals(True)
        try:
            self._model_combo.clear()
            added = set()
            if saved_model:
                self._model_combo.addItem(saved_model, 0)
                added.add(saved_model)
            for name in fallback_models():
                if name not in added:
                    self._model_combo.addItem(name, 0)
                    added.add(name)
            # Re-select saved model
            if saved_model:
                for i in range(self._model_combo.count()):
                    if self._model_combo.itemText(i) == saved_model:
                        self._model_combo.setCurrentIndex(i)
                        break
        finally:
            self._model_combo.blockSignals(False)
        self._update_library_summary()
        self._update_model_meta()
