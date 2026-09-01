"""Settings screen — compact workstation configuration with instant apply."""

from __future__ import annotations

import os
import threading
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QLineEdit, QFrame, QRadioButton,
    QScrollArea, QSlider, QStackedWidget,
)
from PySide6.QtCore import Qt, Signal, QObject, QTimer

from app.widgets.panels import Panel, apply_tactical_label
from app.widgets.controls import (ElidedLabel, TacticalCheckBox,
                                  TacticalComboBox, style_container)
from app.themes.theme_manager import THEME_NAMES, THEME_KEYS, get_palette, theme_signaller
from app.i18n import (available_languages, canonical_language, display_name,
                      explanation_languages, tr)
from app.services.ollama_client import LOCAL_ENDPOINT


# ─── Helpers ───────────────────────────────────────────────

def _contains_widget_type(widget: QWidget, widget_types: tuple[type, ...]) -> bool:
    if isinstance(widget, widget_types):
        return True
    return any(isinstance(child, widget_types) for child in widget.findChildren(QWidget))


# The label and its description share one column. 208px was narrow enough to
# wrap "Local finds a model server already running on this machine — Ollama, LM
# Studio or llama.cpp — on its usual port" onto five lines while the page had
# 1919px to work with and used a third of it. 320px is still a column — the
# fields stay where they are and nothing grows to fill the window — but the
# prose reads in half as many lines, and it fits the app's 1100px minimum
# window with room to spare.
_LABEL_COL_WIDTH = 320

# Helper text under a field is the same kind of prose and was in the same
# state, wrapped to whatever width the control above it happened to have.
_HELPER_MAX_WIDTH = 460


def _setting_row(label_text: str, desc: str, widget: QWidget) -> QVBoxLayout:
    """A setting row: label col + widget col, with optional description."""
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
    lbl.setFixedWidth(_LABEL_COL_WIDTH)
    label_col.addWidget(lbl)
    if desc:
        d = QLabel(desc)
        d.setObjectName("Dim")
        d.setStyleSheet("font-size: 11px;")
        d.setWordWrap(True)
        d.setFixedWidth(_LABEL_COL_WIDTH)
        # QHBoxLayout otherwise uses QLabel's one-line size hint here and can
        # reserve fewer lines than a translated description needs.
        d.setMinimumHeight(d.heightForWidth(_LABEL_COL_WIDTH))
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


# The AI page's form grid. The label column lives in _setting_row; these are
# the two columns to its right, so a value and its action land in the same
# place in every row of a panel rather than wherever the text happened to end.
_VALUE_COL_WIDTH = 280      # matches the endpoint input; a minimum, not a cap
_ACTION_COL_WIDTH = 100
# Scan's value blocks are prose rather than fields, so they get their own,
# wider column — one width for both, so the page has a single value edge.
_SCAN_VALUE_WIDTH = 360
# Wide enough for a real %APPDATA% path on one line; see _storage_row.
_PATH_VALUE_WIDTH = 560
# A floor, not a guarantee: a Qt style sheet's min-height beats setFixedHeight,
# so the theme has the last word on the rendered height. What this buys is that
# both buttons reach it the same way, instead of one carrying an inline
# min-height that overrode #Ghost's padding while the other did not.
# A floor, never a ceiling. setFixedHeight() here set the maximum too, and the
# theme's own padding (7px top and bottom, plus the font) puts a styled button's
# minimum at 44 — so Qt raised the minimum above the maximum and the widget
# painted 44px in a slot the layout had reserved 32 for. The bottom 6px of Test
# and Refresh were clipped, which is what "buttons partially visible" was.
_ACTION_HEIGHT = 32


def _divider() -> QFrame:
    """Dashed-style divider between setting rows."""
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet("background: transparent; border-top: 1px dashed palette(mid);")
    return f


def _human_size(num_bytes: int) -> str:
    """Byte count in the largest unit that keeps it readable.

    Delegates rather than repeating the ladder: this copy silently stopped at
    GB and missed the boundary-rounding fix the shared formatter carries.
    """
    from app.models.finding import _format_size
    return _format_size(num_bytes)


def _dir_size(path: str) -> tuple[int, int]:
    """(total bytes, file count) under *path*, or (0, 0) if it does not exist.

    os.walk rather than a recursive glob: this runs against the session store,
    which has reached several gigabytes, and must not build a list of every
    entry before it can report anything.
    """
    total = 0
    count = 0
    if not path or not os.path.isdir(path):
        return 0, 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
                count += 1
            except OSError:
                pass
    return total, count


class _StorageResult(QObject):
    """Carries measured storage sizes from the background thread to the UI."""
    result = Signal(dict)


class _ConnectionResult(QObject):
    """Carries a probe result from the background thread to the UI.

    (status, backend, models, runtime_path) rather than a formatted message:
    the wording belongs in the UI so it can be translated and re-translated on
    a language switch, and so the status can drive more than just a label.
    """
    result = Signal(str, str, list, str, str)


# ─── Settings Screen ──────────────────────────────────────

_SECTIONS = [
    ("general",   "General"),
    ("ai",        "AI"),
    ("scan",      "Scan"),
    ("about",     "About"),
]

_SECTION_SUBS = {
    "general":   "appearance · language · window",
    "ai":        "local model · explanation · performance",
    "scan":      "safeguards · cleanup method",
    "about":     "build · storage · diagnostics",
}


def _add_localized_enum_items(combo: TacticalComboBox, values: list[str]) -> None:
    """Show translated enum labels while retaining canonical stored values."""
    for value in values:
        combo.addItem(tr(value), value)


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
        self._storage_result = _StorageResult(self)
        self._storage_result.result.connect(self._on_storage_sizes)
        self._storage_targets: dict[str, str] = {}
        self._storage_size_lbls: dict[str, QLabel] = {}
        self._slider_timers: list[QTimer] = []
        self._lang_dirty = False
        self._build_ui()
        self._load_from_store()
        theme_signaller().theme_changed.connect(self._refresh_local_styles)

    def set_settings_store(self, store):
        self._store = store
        self._load_from_store()
        # About caches the config path when it builds its rows, so a store that
        # arrives afterwards left that row reading "unavailable" with a dead
        # Open folder button. main.py passes the store to __init__ and never
        # hits this, but the setter is public and the failure is silent.
        targets = getattr(self, "_storage_targets", None)
        if targets is not None and store is not None:
            targets["config"] = str(getattr(store, "config_path", "") or "")
            lbl = getattr(self, "_config_path_lbl", None)
            if lbl is not None:
                lbl.setText(targets["config"] or tr("unavailable"))

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
        # Endpoint mode + address. Radios are set with signals blocked so
        # restoring the saved state can't fire the toggle handler and overwrite
        # the very values being loaded.
        is_local = self._store.get("ai_endpoint_mode", "local") != "server"
        for rb in (self._rb_ep_local, self._rb_ep_server):
            rb.blockSignals(True)
        self._rb_ep_local.setChecked(is_local)
        self._rb_ep_server.setChecked(not is_local)
        for rb in (self._rb_ep_local, self._rb_ep_server):
            rb.blockSignals(False)
        self._endpoint_input.setText(self._store.get("ai_endpoint"))
        self._apply_endpoint_mode_state(is_local)
        # Signals blocked: restoring a saved value must not look like a user
        # edit and trigger the debounced write-back.
        for slider, key in ((self._timeout_slider, "ai_timeout"),
                            (self._concurrent_slider, "ai_max_concurrent")):
            slider.blockSignals(True)
            slider.setValue(self._store.get(key))
            slider.blockSignals(False)
        self._timeout_val.setText(f"{self._store.get('ai_timeout')} s")
        self._concurrent_val.setText(str(self._store.get("ai_max_concurrent")))

        # Tone
        tone = self._store.get("ai_tone")
        for i in range(self._tone_combo.count()):
            if self._tone_combo.itemData(i) == tone:
                self._tone_combo.setCurrentIndex(i)
                break

        # Length
        length = self._store.get("ai_length")
        for i in range(self._length_combo.count()):
            if self._length_combo.itemData(i) == length:
                self._length_combo.setCurrentIndex(i)
                break

        # AI explanation language
        # Migrated like ui_language: this setting can also hold a name from
        # an older build, and an unmatched value would leave the picker on
        # English while the prompt still asked for the old one.
        ai_lang = canonical_language(
            self._store.get("ai_explanation_language", "English"))
        for i in range(self._ai_lang_combo.count()):
            if self._ai_lang_combo.itemData(i) == ai_lang:
                self._ai_lang_combo.setCurrentIndex(i)
                break

        # Toggles
        self._cb_findings.setChecked(self._store.get("ai_findings_enabled", False))
        self._cb_startups.setChecked(self._store.get("ai_startups_enabled", True))
        self._cb_risky_only.setChecked(self._store.get("ai_explain_risky_only"))

        # Cleanup safety. Recycle Bin is the only method; the store also pins
        # this on load, and the Scan tab states it rather than offering a choice.
        if self._store.get("perm_delete_enabled", False):
            self._store.set_and_save("perm_delete_enabled", False)
        self._cb_confirm_risky.setChecked(self._store.get("confirm_risky_cleanup", True))
        self._cb_cross_volumes.setChecked(self._store.get("scan_cross_volumes", False))

        # Close-while-busy behavior
        self.reload_close_behavior()

        # UI language
        # Migrated, so a settings file written when "Polski" was the stored
        # name still selects Polish instead of falling through to English.
        ui_lang = canonical_language(self._store.get("ui_language", "English"))
        for i in range(self._lang_combo.count()):
            if self._lang_combo.itemData(i) == ui_lang:
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
                # None, not 0: no server has been asked yet.
                self._model_combo.addItem(saved_model, None)
                self._model_combo.setCurrentIndex(0)
            finally:
                self._model_combo.blockSignals(False)
            self._conn_status_lbl.setText(tr("saved · not verified"))
            self._conn_status_lbl.setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 11px; color: {get_palette().get('review', '#d8b46a')};"
            )
        else:
            self._show_no_models()

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
        """Subdued guidance: smaller than a description, not fainter than one.

        This used text_faint, which is the colour a disabled control wears, and
        measured 3.3-3.9 against panel_alt on every theme where 4.5 is the bar
        for text this size. Guidance that is styled like something switched off
        is guidance nobody reads. Size still marks it as secondary.
        """
        p = get_palette()
        return f"font-size: 10px; color: {p.get('text_dim', '#8a9b8f')};"

    def _on_endpoint_mode_changed(self, _checked: bool = False):
        """Switch between the built-in local endpoint and a custom server one.

        The custom address lives in its own setting (``ai_server_endpoint``), so
        flipping to Local and back restores exactly what the user typed instead
        of discarding it. Only ``ai_endpoint`` — the address AI calls actually
        use — is repointed.
        """
        local = self._rb_ep_local.isChecked()
        self._save_value("ai_endpoint_mode", "local" if local else "server")
        if local:
            self._endpoint_input.setText(LOCAL_ENDPOINT)
            self._save_value("ai_endpoint", LOCAL_ENDPOINT)
        else:
            remembered = self._store.get("ai_server_endpoint", "") if self._store else ""
            self._endpoint_input.setText(remembered)
            self._save_value("ai_endpoint", remembered)
        self._apply_endpoint_mode_state(local)

    def _apply_endpoint_mode_state(self, local: bool):
        """Local mode shows the fixed address read-only; Server mode is editable."""
        self._endpoint_input.setEnabled(not local)

    def _on_endpoint_edited(self):
        """Persist a hand-typed endpoint (Server mode only edits are possible)."""
        text = self._endpoint_input.text().strip()
        self._save_value("ai_endpoint", text)
        if self._rb_ep_server.isChecked():
            # Remember it separately so a Local round-trip doesn't lose it.
            self._save_value("ai_server_endpoint", text)

    def _model_level_label(self, size_bytes) -> str:
        """Size tier for the active model, or an honest blank when unknown.

        Three distinct states, and they used to collapse into one word, "small":
        None  — no server contacted yet, so nothing is known;
        0     — the server answered but reports no size (LM Studio, llama.cpp
                list models by id alone, so a 70B model was labelled small);
        n > 0 — a real figure from Ollama.
        """
        from app.services.ollama_client import format_model_size
        if size_bytes is None:
            return ""
        if size_bytes <= 0:
            return tr("local model • size not reported by this server")
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
        # None is meaningful here — "not asked yet" — so it must not be
        # coerced to 0, which means "the server told us it has no size".
        size_bytes = self._model_combo.itemData(idx, Qt.UserRole)
        self._model_meta_lbl.setText(self._model_level_label(size_bytes))

    def _save_value(self, key: str, value):
        if self._store:
            self._store.set_and_save(key, value)
            self.settings_saved.emit()

    def _persist_slider(self, key: str, slider: QSlider):
        """Save *slider* under *key* on any change, debounced.

        sliderReleased alone only fires after a mouse drag of the handle. A value
        nudged with the arrow keys, the mouse wheel, or a click on the groove
        moved the label and was then silently lost on the next launch. The
        debounce keeps a drag from rewriting config.json on every pixel.
        """
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(400)
        timer.timeout.connect(lambda: self._save_value(key, slider.value()))
        slider.valueChanged.connect(lambda _: timer.start())
        self._slider_timers.append(timer)

    def _save_model(self):
        model_text = self._model_combo.currentText()
        model_name = model_text.split(" · ")[0] if " · " in model_text else model_text
        self._save_value("ai_model", model_name)

    def _on_language_changed(self):
        if not self._store:
            return
        self._lang_dirty = (self._lang_combo.currentData()
                            != canonical_language(self._store.get("ui_language", "English")))
        self._btn_apply_lang.setEnabled(self._lang_dirty)

    def _apply_language(self):
        if not self._store or not self._lang_dirty:
            return
        self._store.set_and_save("ui_language", self._lang_combo.currentData())
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

        # Measure on open rather than at build time: sizes go stale as scans run,
        # and walking a multi-gigabyte session store at startup would delay every
        # launch for a number only this tab shows.
        if sec_id == "about":
            self._refresh_storage_sizes()

    # ─── Section Builders ──────────────────────────────────

    def _build_general(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        style_container(scroll, "border: none;")

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(22, 12, 22, 22)
        lay.setSpacing(16)

        # Appearance panel
        app_panel = Panel(alt=True)
        app_lay = app_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        app_lay.addLayout(_panel_title(tr("Appearance"), tr("theme")))
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
        # Endonyms, not tr(): see app/i18n.ENDONYMS. Every other enum on this
        # screen is a Podbye concept and belongs in the user's language; a
        # language name belongs in its own.
        for value in available_languages():
            self._lang_combo.addItem(display_name(value), value)
        self._lang_combo.setFixedWidth(168)
        self._lang_combo.currentTextChanged.connect(lambda _: self._on_language_changed())
        self._apply_combo_style(self._lang_combo)
        lang_row.addWidget(self._lang_combo, alignment=Qt.AlignVCenter)
        self._btn_apply_lang = QPushButton(tr("Apply"))
        self._btn_apply_lang.setCursor(Qt.PointingHandCursor)
        self._btn_apply_lang.setEnabled(False)
        self._btn_apply_lang.setMinimumHeight(30)
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

        # Window panel — how the app behaves, not how a scan behaves. This used
        # to sit under Scan, where "when closing while busy" read as a scan
        # safeguard rather than a window preference.
        win_panel = Panel(alt=True)
        win_lay = win_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        win_lay.addLayout(_panel_title(tr("Window"), tr("closing")))
        self._register_styled_panel(win_panel)

        self._close_behavior_combo = TacticalComboBox()
        self._close_behavior_combo.addItem(tr("Ask me each time"), "ask")
        self._close_behavior_combo.addItem(tr("Keep running in background"), "background")
        self._close_behavior_combo.addItem(tr("Quit and stop the work"), "quit")
        self._close_behavior_combo.setFixedWidth(220)
        self._apply_combo_style(self._close_behavior_combo)
        self._close_behavior_combo.currentIndexChanged.connect(
            lambda _: self._save_value(
                "close_behavior", self._close_behavior_combo.currentData()))
        win_lay.addLayout(_setting_row(
            tr("When closing while busy"),
            # The label above already states the condition, so the description
            # only has to list the choices. Restating it cost this row eight
            # wrapped lines in a 208px column beside a single 42px dropdown.
            tr("Podbye can ask, keep working in the system tray, or stop and quit."),
            self._close_behavior_combo,
        ))

        lay.addWidget(win_panel)
        lay.addStretch()

        scroll.setWidget(content)
        return scroll

    def _build_ai(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        style_container(scroll, "border: none;")

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(22, 12, 22, 22)
        lay.setSpacing(16)

        # Local Model Server panel
        server_panel = Panel(alt=True)
        srv_lay = server_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        srv_lay.addLayout(_panel_title(tr("Local Model Server"), tr("endpoint")))
        self._register_styled_panel(server_panel)

        # Connection mode — Local (built-in address) vs Server (custom LAN host)
        mode_w = QWidget()
        mode_h = QHBoxLayout(mode_w)
        mode_h.setContentsMargins(0, 0, 0, 0)
        mode_h.setSpacing(16)
        self._rb_ep_local = QRadioButton(tr("Local"))
        self._rb_ep_server = QRadioButton(tr("Server"))
        for rb in (self._rb_ep_local, self._rb_ep_server):
            rb.setCursor(Qt.PointingHandCursor)
            self._style_radio(rb)
            mode_h.addWidget(rb)
        mode_h.addStretch()
        self._rb_ep_local.setChecked(True)
        self._rb_ep_local.toggled.connect(self._on_endpoint_mode_changed)
        srv_lay.addLayout(_setting_row(
            tr("Connection mode"),
            tr("Local finds a model server already running on this machine — "
               "Ollama, LM Studio or llama.cpp — on its usual port. Server points "
               "Podbye at another machine on your network (LAN addresses only)."),
            mode_w,
        ))

        # Endpoint
        self._endpoint_input = QLineEdit(LOCAL_ENDPOINT)
        self._endpoint_input.setStyleSheet(self._input_qss())
        self._styled_inputs.append(self._endpoint_input)
        self._endpoint_input.setFixedWidth(280)
        self._endpoint_input.setPlaceholderText("http://192.168.1.50:11434")
        self._endpoint_input.editingFinished.connect(self._on_endpoint_edited)
        ep_w = QWidget()
        ep_h = QHBoxLayout(ep_w)
        ep_h.setContentsMargins(0, 0, 0, 0)
        ep_h.setSpacing(8)
        ep_h.addWidget(self._endpoint_input)
        self._btn_test = QPushButton(tr("Test"))
        self._btn_test.setObjectName("Ghost")
        self._btn_test.setCursor(Qt.PointingHandCursor)
        # Height set as a widget property, not a stylesheet: an inline
        # stylesheet here would take precedence over #Ghost's fill and border
        # and leave the button invisible on a panel_alt panel. Test rendered
        # 12px tall beside a 32px Refresh — two Ghost buttons, one panel.
        self._btn_test.setFixedWidth(_ACTION_COL_WIDTH)
        self._btn_test.setMinimumHeight(_ACTION_HEIGHT)
        self._btn_test.clicked.connect(self._test_connection)
        ep_h.addWidget(self._btn_test)
        srv_lay.addLayout(_setting_row(tr("Endpoint"), tr("Ollama-compatible HTTP server. Podbye never reaches the public network."), ep_w))

        # Connection status: the state on one line, and what to do about it on
        # the next. A single line had to serve every outcome, so it ended up
        # saying nothing useful about any of them.
        conn_w = QWidget()
        conn_v = QVBoxLayout(conn_w)
        conn_v.setContentsMargins(0, 0, 0, 0)
        conn_v.setSpacing(4)

        conn_top = QHBoxLayout()
        conn_top.setContentsMargins(0, 0, 0, 0)
        conn_top.setSpacing(8)
        self._conn_status_lbl = QLabel("")
        # Minimum, never fixed. The status line runs to "offline · no Ollama,
        # LM Studio or llama.cpp server found" — 672px of text. A fixed column
        # would align the button by truncating the sentence that explains why
        # the button is there. Short states still reserve the column, so the
        # action lands on the same axis in every case that fits.
        self._conn_status_lbl.setMinimumWidth(_VALUE_COL_WIDTH)
        self._conn_status_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        conn_top.addWidget(self._conn_status_lbl)

        self._btn_start_ollama = QPushButton(tr("Start Ollama"))
        self._btn_start_ollama.setObjectName("Ghost")
        self._btn_start_ollama.setCursor(Qt.PointingHandCursor)
        self._btn_start_ollama.setMinimumHeight(_ACTION_HEIGHT)
        self._btn_start_ollama.setVisible(False)
        self._btn_start_ollama.clicked.connect(self._start_ollama)
        conn_top.addWidget(self._btn_start_ollama)
        conn_top.addStretch()
        conn_v.addLayout(conn_top)

        self._conn_hint_lbl = QLabel("")
        self._conn_hint_lbl.setObjectName("Dim")
        self._conn_hint_lbl.setStyleSheet("font-size: 11px;")
        self._conn_hint_lbl.setWordWrap(True)
        self._conn_hint_lbl.setMaximumWidth(360)
        self._conn_hint_lbl.setVisible(False)
        conn_v.addWidget(self._conn_hint_lbl)

        srv_lay.addLayout(_setting_row(tr("Connection"), tr("Last contacted server."), conn_w))

        # Library / refresh
        lib_w = QWidget()
        lib_h = QHBoxLayout(lib_w)
        lib_h.setContentsMargins(0, 0, 0, 0)
        lib_h.setSpacing(8)
        self._library_count_lbl = QLabel(tr("0 models available"))
        self._library_count_lbl.setMinimumWidth(_VALUE_COL_WIDTH)
        self._library_count_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        lib_h.addWidget(self._library_count_lbl)
        self._btn_refresh_models = QPushButton(tr("Refresh"))
        self._btn_refresh_models.setObjectName("Ghost")
        self._btn_refresh_models.setCursor(Qt.PointingHandCursor)
        self._btn_refresh_models.setFixedWidth(_ACTION_COL_WIDTH)
        self._btn_refresh_models.setMinimumHeight(_ACTION_HEIGHT)
        self._btn_refresh_models.clicked.connect(self._test_connection)
        lib_h.addWidget(self._btn_refresh_models)
        lib_h.addStretch()
        srv_lay.addLayout(_setting_row(tr("Library"), tr("Local model catalog read from the server."), lib_w))

        # Local-only disclosure
        disc = QLabel(
            tr("Podbye refuses to connect to non-loopback or non-LAN endpoints. "
               "There is no cloud fallback, no API key field, no analytics.")
        )
        disc.setObjectName("Dim")
        disc.setStyleSheet("font-size: 11px; padding: 2px 0px;")
        disc.setWordWrap(True)
        disc.setMaximumWidth(_HELPER_MAX_WIDTH)
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
        model_hint1.setMaximumWidth(_HELPER_MAX_WIDTH)
        model_v.addWidget(model_hint1)
        model_hint2 = QLabel(tr("A model trained before an app existed may not identify it correctly."))
        model_hint2.setObjectName("Dim")
        model_hint2.setStyleSheet(self._helper_style())
        model_hint2.setWordWrap(True)
        model_hint2.setMaximumWidth(_HELPER_MAX_WIDTH)
        model_v.addWidget(model_hint2)
        mod_lay.addLayout(_setting_row(tr("Active model"), tr("Choose the local model used for explanations."), model_w))
        self._show_no_models()

        lay.addWidget(model_panel)

        # Explanation panel
        expl_panel = Panel(alt=True)
        expl_lay = expl_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        expl_lay.addLayout(_panel_title(tr("Explanation"), tr("style & length")))
        self._register_styled_panel(expl_panel)

        self._tone_combo = TacticalComboBox()
        _add_localized_enum_items(self._tone_combo,
                                  ["Neutral", "Friendly", "Professional", "Technical"])
        self._tone_combo.setCurrentIndex(0)
        self._tone_combo.setFixedWidth(146)
        self._apply_combo_style(self._tone_combo)
        self._tone_combo.currentIndexChanged.connect(
            lambda _: self._save_value("ai_tone", self._tone_combo.currentData()))
        expl_lay.addLayout(_setting_row(tr("Explanation style"), tr("Affects terminology and tone, not personality."), self._tone_combo))

        self._length_combo = TacticalComboBox()
        _add_localized_enum_items(self._length_combo, ["Compact", "Standard", "Detailed"])
        self._length_combo.setCurrentIndex(1)
        self._length_combo.setFixedWidth(140)
        self._apply_combo_style(self._length_combo)
        self._length_combo.currentIndexChanged.connect(
            lambda _: self._save_value("ai_length", self._length_combo.currentData()))
        expl_lay.addLayout(_setting_row(tr("Explanation length"), tr("Controls how much the model writes per finding."), self._length_combo))

        # No separator here. Style, length and language are one group — all
        # three describe how the explanation is written — so a rule between
        # length and language split a subgroup rather than marking one, and
        # left the scope toggles below attached to language instead. The panel
        # already separates its rows with a consistent 10px rhythm.

        # AI explanation language
        ai_lang_w = QWidget()
        ai_lang_lay = QVBoxLayout(ai_lang_w)
        ai_lang_lay.setContentsMargins(0, 0, 0, 0)
        ai_lang_lay.setSpacing(4)
        self._ai_lang_combo = TacticalComboBox()
        _add_localized_enum_items(self._ai_lang_combo, explanation_languages())
        self._ai_lang_combo.setCurrentIndex(0)
        self._ai_lang_combo.setFixedWidth(168)
        self._apply_combo_style(self._ai_lang_combo)
        self._ai_lang_combo.currentIndexChanged.connect(
            lambda _: self._save_value("ai_explanation_language", self._ai_lang_combo.currentData()))
        ai_lang_lay.addWidget(self._ai_lang_combo)
        ai_lang_hint1 = QLabel(tr("Make sure your local AI model supports the selected language well."))
        ai_lang_hint1.setObjectName("Dim")
        ai_lang_hint1.setStyleSheet(self._helper_style())
        ai_lang_hint1.setWordWrap(True)
        ai_lang_hint1.setMaximumWidth(_HELPER_MAX_WIDTH)
        ai_lang_lay.addWidget(ai_lang_hint1)
        ai_lang_hint2 = QLabel(tr("Smaller local models may produce lower quality explanations in some languages."))
        ai_lang_hint2.setObjectName("Dim")
        ai_lang_hint2.setStyleSheet(self._helper_style())
        ai_lang_hint2.setWordWrap(True)
        ai_lang_hint2.setMaximumWidth(_HELPER_MAX_WIDTH)
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
        self._cb_findings = TacticalCheckBox(tr("Explain all findings automatically"))
        self._style_checkbox(self._cb_findings)
        self._cb_findings.toggled.connect(lambda checked: self._save_value("ai_findings_enabled", checked))
        ai_toggle_l.addWidget(self._cb_findings)
        self._cb_startups = TacticalCheckBox(tr("Startups"))
        self._style_checkbox(self._cb_startups)
        self._cb_startups.toggled.connect(lambda checked: self._save_value("ai_startups_enabled", checked))
        ai_toggle_l.addWidget(self._cb_startups)
        expl_lay.addLayout(_setting_row(tr("AI explanations"), tr("Per-item \"Ask AI\" always works. Automatic explanation of every "
               "finding is off by default — it is slow on a local model; turn it "
               "on for long background runs."), ai_toggle_w))

        self._cb_risky_only = TacticalCheckBox(tr("Enable"))
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
        self._timeout_val = QLabel(tr("{value} s", value=180))
        self._timeout_val.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
        self._timeout_val.setFixedWidth(64)
        self._timeout_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._timeout_slider.valueChanged.connect(lambda v: self._timeout_val.setText(f"{v} s"))
        self._persist_slider("ai_timeout", self._timeout_slider)
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
        self._persist_slider("ai_max_concurrent", self._concurrent_slider)
        conc_h.addWidget(self._concurrent_slider)
        conc_h.addWidget(self._concurrent_val)
        conc_hint = QLabel(tr("1 = quieter · 3 = recommended · 5+ = aggressive"))
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
        style_container(scroll, "border: none;")

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(22, 12, 22, 22)
        lay.setSpacing(16)

        # Safeguards
        safe_panel = Panel(alt=True)
        s_lay = safe_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        s_lay.addLayout(_panel_title(tr("Safeguards"), tr("safe cleanup behavior")))
        self._register_styled_panel(safe_panel)

        self._cb_confirm_risky = TacticalCheckBox(tr("Enable"))
        self._style_checkbox(self._cb_confirm_risky)
        self._cb_confirm_risky.setChecked(True)
        self._cb_confirm_risky.toggled.connect(lambda checked: self._save_value("confirm_risky_cleanup", checked))
        s_lay.addLayout(_setting_row(
            tr("Confirm risky cleanup"),
            tr("Ask before removing items that may still matter to you or an app."),
            self._cb_confirm_risky,
        ))

        self._cb_cross_volumes = TacticalCheckBox(tr("Enable"))
        self._style_checkbox(self._cb_cross_volumes)
        self._cb_cross_volumes.toggled.connect(
            lambda checked: self._save_value("scan_cross_volumes", checked))
        s_lay.addLayout(_setting_row(
            tr("Scan across drives"),
            tr("Follow into other drives and volumes (mounted disks, junctions). "
               "Off keeps each scan on the drive you picked."),
            self._cb_cross_volumes,
        ))

        lay.addWidget(safe_panel)

        # ── Items you keep ───────────────────────────────────────
        # A Keep mark stops something being selected or deleted, in this and
        # every later scan. That is exactly the kind of setting a user needs
        # to be able to find again and take back, so it is listed rather than
        # living only on the row it was made from.
        self._keep_panel = Panel(alt=True)
        keep_lay = self._keep_panel.with_layout(
            vertical=True, margins=(14, 12, 14, 12), spacing=10)
        keep_lay.addLayout(_panel_title(tr("Items you keep"), tr("never deleted")))
        self._register_styled_panel(self._keep_panel)

        self._keep_list_box = QWidget()
        self._keep_list_box.setMinimumWidth(_SCAN_VALUE_WIDTH)
        self._keep_list_layout = QVBoxLayout(self._keep_list_box)
        self._keep_list_layout.setContentsMargins(0, 0, 0, 0)
        self._keep_list_layout.setSpacing(4)

        # The row and the empty state are alternatives, not a row that happens
        # to be empty. Held in a container so one can replace the other: the
        # "nothing yet" line used to sit in the value column, dim, beside an
        # equally dim description — two columns of muted text that read as a
        # form with a blank field rather than as a section with nothing in it.
        self._keep_row_host = QWidget()
        keep_row_lay = QVBoxLayout(self._keep_row_host)
        keep_row_lay.setContentsMargins(0, 0, 0, 0)
        keep_row_lay.setSpacing(0)
        keep_row_lay.addLayout(_setting_row(
            tr("Kept paths"),
            tr("Marked with Keep in Findings. Nothing inside these is ever "
               "selected by a bulk action, and cleanup refuses them outright."),
            self._keep_list_box,
        ))
        keep_lay.addWidget(self._keep_row_host)

        # Spans the panel rather than sitting in a column, and carries the
        # safety rule itself: hiding the row would otherwise hide the sentence
        # explaining what a Keep mark actually does.
        self._keep_empty_lbl = QLabel(
            tr("Nothing kept yet. Use Keep on any finding — kept paths are never "
               "selected by a bulk action, and cleanup refuses them outright.")
        )
        self._keep_empty_lbl.setObjectName("Dim")
        self._keep_empty_lbl.setStyleSheet("font-size: 11px;")
        self._keep_empty_lbl.setWordWrap(True)
        keep_lay.addWidget(self._keep_empty_lbl)

        lay.addWidget(self._keep_panel)
        self._refresh_kept_paths()

        # File Handling
        fh_panel = Panel(alt=True)
        fh_lay = fh_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        fh_lay.addLayout(_panel_title(tr("File Handling"), tr("cleanup method")))
        self._register_styled_panel(fh_panel)

        # Cleanup is Recycle Bin-first. The setting states the default rather
        # than advertising a deletion mode that the UI does not offer.
        method_w = QWidget()
        method_v = QVBoxLayout(method_w)
        method_v.setContentsMargins(0, 0, 0, 0)
        method_v.setSpacing(4)

        self._method_value_lbl = QLabel(tr("Recycle Bin"))
        self._method_value_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 11px;")
        method_v.addWidget(self._method_value_lbl)

        method_note = QLabel(tr("Cleanup uses the Recycle Bin by default. "
                                "Emptying it is irreversible."))
        method_note.setObjectName("Dim")
        method_note.setStyleSheet("font-size: 11px;")
        method_note.setWordWrap(True)
        method_note.setMaximumWidth(_HELPER_MAX_WIDTH)
        # Fixed, not a maximum. A word-wrapping QLabel reports a narrow size
        # hint, and the container sizes to it — the note wrapped at 165px and
        # stood eight lines tall instead of three. Wrapping never clips, so
        # pinning the width is safe here.
        method_note.setFixedWidth(_SCAN_VALUE_WIDTH)
        method_v.addWidget(method_note)

        fh_lay.addLayout(_setting_row(tr("Cleanup method"), "", method_w))

        lay.addWidget(fh_panel)
        lay.addStretch()

        scroll.setWidget(content)
        return scroll

    def _refresh_kept_paths(self):
        """Redraw the kept-path list from the store."""
        from app.services import keep_list

        layout = self._keep_list_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        paths = keep_list.kept_paths()
        # Swap the whole row for the section-state line, rather than leaving a
        # labelled row with an empty value column.
        self._keep_row_host.setVisible(bool(paths))
        self._keep_empty_lbl.setVisible(not paths)
        if not paths:
            return

        for path in paths:
            row = QWidget()
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(8)
            lbl = ElidedLabel(path, mode=Qt.ElideMiddle)
            lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
            lbl.setToolTip(path)
            # An ElidedLabel carries the Ignored size policy so it never forces
            # a panel wider. Next to a button that does state a width, the
            # layout gave the button everything and collapsed the path to
            # nothing: the row showed "Stop keeping" and no path at all.
            lbl.setMinimumWidth(240)
            row_l.addWidget(lbl, stretch=1)
            btn = QPushButton(tr("Stop keeping"))
            btn.setObjectName("Subtle")
            btn.setStyleSheet("font-size: 10px; padding: 2px 8px;")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked=False, target=path: self._stop_keeping(target))
            row_l.addWidget(btn)
            layout.addWidget(row)

    def _stop_keeping(self, path: str):
        from app.services import keep_list
        keep_list.unkeep(path)
        self._refresh_kept_paths()

    def _build_about(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        style_container(scroll, "border: none;")

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(22, 12, 22, 22)
        lay.setSpacing(16)

        # Build panel
        build_panel = Panel(alt=True)
        b_lay = build_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        b_lay.addLayout(_panel_title(tr("Build"), tr("product")) )
        self._register_styled_panel(build_panel)

        from app.version import __version__, BUILD, REPO_URL, RELEASES_URL
        for k, v in [
            (tr("Version"), __version__),
            (tr("Build"), BUILD),
        ]:
            val_lbl = QLabel(v)
            val_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
            b_lay.addLayout(_setting_row(k, "", val_lbl))

        b_lay.addWidget(_divider())

        # Source and releases. Both are links handed to the system browser —
        # Podbye itself never requests either. That is the whole reason there is
        # no automatic update check: a program that promises it does not talk to
        # the internet cannot quietly announce its version, IP and launch time
        # on every start. The button says what it does so nobody assumes
        # otherwise.
        links = QWidget()
        links_row = QHBoxLayout(links)
        links_row.setContentsMargins(0, 0, 0, 0)
        links_row.setSpacing(8)
        for label, url in ((tr("Check for updates"), RELEASES_URL),
                           (tr("View source"), REPO_URL)):
            btn = QPushButton(label)
            btn.setObjectName("Ghost")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._utility_btn_qss())
            btn.setToolTip(url)
            btn.clicked.connect(lambda _=False, u=url: self._open_external(u))
            links_row.addWidget(btn)
        links_row.addStretch()
        b_lay.addLayout(_setting_row(
            tr("Repository"),
            tr("Opens in your browser. Podbye never contacts the internet "
               "itself, so there is nothing to check from in here."),
            links))

        lay.addWidget(build_panel)

        # Storage panel — every row is a directory that really exists, read from
        # the module that owns it. The previous version listed four hardcoded
        # strings, two of which ("reports", "cache\\hashes.db") no code has ever
        # created, and omitted the session store — the only one big enough to
        # matter. Each row opens in Explorer, because a path a user cannot find
        # is not much better than no path at all.
        store_panel = Panel(alt=True)
        p_lay = store_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        p_lay.addLayout(_panel_title(tr("Storage"), tr("where Podbye keeps its data")))
        self._register_styled_panel(store_panel)

        from app.state.session_store import sessions_dir
        from app.services.ai_explainer import cache_dir

        config_path = self._store.config_path if self._store else ""
        self._storage_targets = {
            "config": str(config_path),
            "sessions": str(sessions_dir()),
            "ai_cache": str(cache_dir()),
        }
        self._storage_size_lbls: dict[str, QLabel] = {}

        p_lay.addLayout(self._storage_row(
            "config", tr("Settings"),
            tr("Your preferences. Deleting it resets Podbye to defaults."),
        ))
        p_lay.addWidget(_divider())
        p_lay.addLayout(self._storage_row(
            "sessions", tr("Scan sessions"),
            tr("Saved scan results, so a run can be reopened later."),
            show_size=True,
        ))
        p_lay.addWidget(_divider())
        p_lay.addLayout(self._storage_row(
            "ai_cache", tr("AI explanation cache"),
            tr("Answers already generated, reused instead of asking the model "
               "again. Safe to clear — it only costs a re-run."),
            show_size=True, clearable=True,
        ))

        lay.addWidget(store_panel)

        # Diagnostics
        diag_panel = Panel(alt=True)
        dg_lay = diag_panel.with_layout(vertical=True, margins=(14, 12, 14, 12), spacing=10)
        # Not diagnostics: there is nothing here that reports on a fault. The
        # panel holds one maintenance action and the product metadata under it,
        # so it says so. "Open logs folder" was the only diagnostic it ever had
        # and it was removed when it turned out Podbye logs to the console.
        dg_lay.addLayout(_panel_title(tr("Maintenance"), tr("reset & product info")))
        self._register_styled_panel(diag_panel)

        btn_row = QWidget()
        br_lay = QHBoxLayout(btn_row)
        br_lay.setContentsMargins(0, 0, 0, 0)
        br_lay.setSpacing(8)

        # "Open logs folder" used to live here. Podbye configures logging to the
        # console only, so the button created an empty directory and opened it —
        # a support action that could never produce anything to send.
        # Quiet at rest, dangerous on contact. #Danger paints a filled red
        # button, which made the loudest thing on this page an action nobody
        # comes here to perform — and Analyze's Stop button shares that name,
        # so the weight belongs there rather than being taken away from it.
        self._btn_reset = QPushButton(tr("Reset all settings"))
        self._btn_reset.setObjectName("DangerQuiet")
        self._btn_reset.setCursor(Qt.PointingHandCursor)
        self._restyle_reset_button()
        self._btn_reset.clicked.connect(self._reset_all_settings)
        br_lay.addWidget(self._btn_reset)
        # The quiet-danger style has its own vertical padding.  Reserve the
        # button's post-style minimum here rather than an English-sized row;
        # otherwise a longer localized caption can make the button paint below
        # this small maintenance row at the minimum window width.
        btn_row.setMinimumHeight(self._btn_reset.minimumSizeHint().height())

        br_lay.addStretch()
        dg_lay.addWidget(btn_row)

        disc = QLabel(
            tr("Local-first storage analysis and cleanup assistant.\n"
               "No cloud processing. No background telemetry. Decisions stay on your machine.")
        )
        disc.setObjectName("Dim")
        disc.setStyleSheet(f"{self._helper_style()} line-height: 1.4;")
        disc.setWordWrap(True)
        disc.setMaximumWidth(_HELPER_MAX_WIDTH)
        # QSS line-height is not included consistently in QLabel's size hint.
        # Reserve the measured wrapped height so localized two-line product copy
        # cannot lose its last pixels in the compact About panel.
        disc.setMinimumHeight(disc.heightForWidth(_HELPER_MAX_WIDTH))
        dg_lay.addWidget(disc)

        credit = QLabel(tr("Built with Qt for Python (PySide6), used under the LGPL v3."))
        credit.setObjectName("Dim")
        credit.setStyleSheet(self._helper_style())
        credit.setWordWrap(True)
        dg_lay.addWidget(credit)

        lay.addWidget(diag_panel)
        lay.addStretch()

        scroll.setWidget(content)
        return scroll

    # ─── About: storage rows ───────────────────────────────

    def _storage_row(self, key: str, label: str, desc: str,
                     show_size: bool = False, clearable: bool = False) -> QVBoxLayout:
        """One storage location: real path, optional size, and a way to open it."""
        path = self._storage_targets.get(key, "")

        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        path_lbl = QLabel(path or tr("unavailable"))
        if key == "config":
            self._config_path_lbl = path_lbl
        path_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        path_lbl.setWordWrap(True)
        # 380px broke "C:\Users\<name>\AppData\Roaming\Podbye\sessions"
        # across two lines, splitting one value at whatever character happened
        # to land on the boundary. A real path needs ~552px, so the cap is set
        # above that: one line wherever the window allows, wrapping only when
        # the window is genuinely too narrow to hold it.
        # A minimum, not a maximum. A word-wrapping QLabel reports a narrow
        # size hint and the container sizes to it, so a cap alone left the
        # label at 484px and the path still broke over two lines. The app's
        # window minimum is 1100px, which leaves ~609px for this column, so
        # 560 fits at every size the window can actually be.
        path_lbl.setMinimumWidth(_PATH_VALUE_WIDTH)
        path_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(path_lbl)

        if show_size:
            size_lbl = QLabel(tr("measuring…"))
            size_lbl.setObjectName("Muted")
            size_lbl.setStyleSheet(self._helper_style())
            col.addWidget(size_lbl)
            self._storage_size_lbls[key] = size_lbl

        btns = QHBoxLayout()
        btns.setContentsMargins(0, 0, 0, 0)
        btns.setSpacing(8)
        btn_open = QPushButton(tr("Open folder"))
        btn_open.setObjectName("Ghost")
        btn_open.setCursor(Qt.PointingHandCursor)
        btn_open.setStyleSheet(self._utility_btn_qss())
        btn_open.clicked.connect(lambda _=False, p=path: self._reveal_path(p))
        btn_open.setEnabled(bool(path))
        btns.addWidget(btn_open)

        if clearable:
            btn_clear = QPushButton(tr("Clear cache"))
            btn_clear.setObjectName("Ghost")
            btn_clear.setCursor(Qt.PointingHandCursor)
            btn_clear.setStyleSheet(self._utility_btn_qss())
            btn_clear.clicked.connect(self._clear_ai_cache)
            btns.addWidget(btn_clear)

        btns.addStretch()
        col.addLayout(btns)

        return _setting_row(label, desc, holder)

    def _open_external(self, url: str):
        """Hand *url* to the system browser.

        QDesktopServices, not urllib: this must not become a request Podbye
        makes. Nothing here opens a socket, which is also why
        test_offline_guarantee still passes with these buttons on screen.
        """
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))

    def _reveal_path(self, path: str):
        """Show *path* in the system file manager.

        A file is revealed with its parent open and the file selected; a folder
        is opened directly. A path that does not exist yet (no scan run, no AI
        answer cached) opens the nearest parent that does, rather than failing
        silently on a button the user just pressed.
        """
        import subprocess
        import sys
        from pathlib import Path

        if not path:
            return
        target = Path(path)
        try:
            if sys.platform == "win32" and target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
                return
            # Nearest ancestor that exists: the sessions folder is absent until
            # the first scan, and the AI cache until the first explanation.
            folder = target if target.is_dir() else target.parent
            while not folder.is_dir() and folder != folder.parent:
                folder = folder.parent
            opener = {"win32": "explorer", "darwin": "open"}.get(sys.platform, "xdg-open")
            subprocess.Popen([opener, str(folder)])
        except OSError:
            pass

    def _clear_ai_cache(self):
        from app.services.ai_explainer import clear_cache
        removed = clear_cache()
        lbl = self._storage_size_lbls.get("ai_cache")
        if lbl:
            lbl.setText(tr("cleared · {n} file(s) removed", n=removed))
        QTimer.singleShot(600, self, self._refresh_storage_sizes)

    def _refresh_storage_sizes(self):
        """Measure the session store and AI cache off the UI thread.

        The session store has reached multiple gigabytes in practice, so walking
        it must never be done on the UI thread.
        """
        targets = {k: self._storage_targets.get(k, "")
                   for k in self._storage_size_lbls}
        if not targets:
            return

        def _worker():
            sizes: dict[str, tuple[int, int]] = {}
            for key, path in targets.items():
                sizes[key] = _dir_size(path)
            try:
                self._storage_result.result.emit(sizes)
            except RuntimeError:
                pass  # screen torn down while measuring

        threading.Thread(target=_worker, daemon=True).start()

    def _on_storage_sizes(self, sizes: dict):
        for key, (total, count) in sizes.items():
            lbl = self._storage_size_lbls.get(key)
            if not lbl:
                continue
            if count == 0:
                lbl.setText(tr("empty"))
            else:
                lbl.setText(tr("{size} · {n} file(s)",
                               size=_human_size(total), n=count))

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
        self._conn_status_lbl.setText(tr("testing…"))
        self._conn_status_lbl.setStyleSheet(f"font-family: 'JetBrains Mono'; font-size: 11px; color: {get_palette().get('review', '#d8b46a')};")

        endpoint = self._endpoint_input.text().strip()
        # Local means "a server on this machine", not "Ollama's port". Only
        # Local scans the other well-known ports; a Server address the user
        # typed is used exactly as given.
        discover = self._rb_ep_local.isChecked()

        def _worker():
            from app.services.ollama_client import probe
            r = probe(endpoint, discover=discover)
            try:
                self._conn_result.result.emit(
                    r["status"], r["backend"], r["models"], r["runtime_path"],
                    r["endpoint"])
            except RuntimeError:
                # The screen was torn down while the probe was in flight — a
                # language switch rebuilds the shell, and a probe with
                # discover=True walks several ports with timeouts. Its sibling
                # _auto_test_connection already guarded this; the manual Test
                # button did not, and raised in a daemon thread instead.
                pass

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _connection_message(self, status: str, backend: str, count: int) -> tuple:
        """(text, colour-key, hint) for a probe status.

        Every branch says what is true and what to do next. The old code had one
        failure message for every cause — "offline · no Ollama, LM Studio or
        llama.cpp server found" — which on a machine that has Ollama installed
        and merely stopped reads as "you don't have one".
        """
        from app.services import ollama_client as oc

        if status == oc.STATUS_ONLINE:
            label = oc.BACKEND_LABELS.get(backend, backend)
            return (tr("connected · {backend} · {n} model(s)", backend=label, n=count),
                    "safe", "")
        if status == oc.STATUS_NO_MODELS:
            label = oc.BACKEND_LABELS.get(backend, backend)
            # The next step differs per runtime: Ollama downloads models from a
            # terminal, LM Studio loads one that is already on disk from its own
            # window. Telling an LM Studio user to run "ollama pull" is noise.
            hint = (tr("LM Studio is running but no model is loaded. "
                       "Load one in its Developer tab, then press Test.")
                    if backend == oc.BACKEND_OPENAI else
                    tr("The server is running but has no models yet. "
                       "Pull one, for example:  ollama pull llama3.2:3b"))
            return (tr("connected · {backend} · no models installed", backend=label),
                    "review", hint)
        if status == oc.STATUS_NOT_RUNNING:
            return (tr("Ollama is installed but not running"), "review",
                    tr("Start it and Podbye will connect on its own — "
                       "nothing here needs to be filled in."))
        if status == oc.STATUS_NOT_INSTALLED:
            return (tr("no local AI runtime on this machine"), "risk",
                    tr("Install Ollama or LM Studio, then press Test. "
                       "Podbye only ever talks to your own machine or LAN."))
        if status == oc.STATUS_UNREACHABLE:
            return (tr("no answer from that address"), "risk",
                    tr("Check the machine is on, the runtime is running, and "
                       "that it listens on the network rather than only on "
                       "its own loopback."))
        if status == oc.STATUS_REFUSED:
            return (tr("refused · not a local address"), "risk",
                    tr("Podbye only connects to this machine or your LAN, "
                       "never to a cloud API."))
        return (tr("unknown state"), "risk", "")

    def _on_connection_result(self, status: str, backend: str, models: list,
                              runtime_path: str, endpoint: str = ""):
        from app.services import ollama_client as oc

        # Discovery may have found the runtime on a different port than the one
        # configured. Adopt it, or AI calls would keep going to the dead address.
        if endpoint and endpoint != self._endpoint_input.text().strip():
            self._endpoint_input.setText(endpoint)
            self._save_value("ai_endpoint", endpoint)

        self._btn_test.setEnabled(True)
        self._btn_refresh_models.setEnabled(True)

        text, colour_key, hint = self._connection_message(
            status, backend, len(models))
        palette = get_palette()
        self._conn_status_lbl.setText(text)
        self._conn_status_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 11px; "
            f"color: {palette.get(colour_key, '#d68a78')};"
        )
        self._conn_hint_lbl.setText(hint)
        self._conn_hint_lbl.setVisible(bool(hint))

        # Offer to start a runtime that is installed but stopped — the one case
        # where the user's next action is a single click rather than a decision.
        self._ollama_exe = runtime_path
        self._btn_start_ollama.setVisible(status == oc.STATUS_NOT_RUNNING
                                          and bool(runtime_path))

        if models:
            # Block signals so repopulating does not fire _save_model and
            # overwrite the stored model with a transient selection.
            self._model_combo.blockSignals(True)
            try:
                self._model_combo.clear()
                for m in models:
                    self._model_combo.addItem(m["name"], m.get("size", 0))
                saved_model = self._store.get("ai_model", "") if self._store else ""
                matched = False
                for i in range(self._model_combo.count()):
                    if saved_model == self._model_combo.itemText(i):
                        self._model_combo.setCurrentIndex(i)
                        matched = True
                        break
            finally:
                self._model_combo.blockSignals(False)
            self._model_combo.setEnabled(True)

            # The saved model is not on the server. Falling through here left
            # the dropdown showing the first installed model while ai_model kept
            # pointing at the missing one, so Settings looked healthy and every
            # explanation failed with "model not found". Adopt what is actually
            # there, persist it, and say which one changed.
            if not matched and self._store:
                adopted = self._model_combo.itemText(0)
                self._save_value("ai_model", adopted)
                if saved_model:
                    hint = tr("\"{old}\" is no longer on this server — switched to "
                              "\"{new}\".", old=saved_model, new=adopted)
                    self._conn_hint_lbl.setText(hint)
                    self._conn_hint_lbl.setVisible(True)
        else:
            self._show_no_models()

        self._update_library_summary()
        self._update_model_meta()

    def _start_ollama(self):
        """Launch the installed runtime, then re-test once it has come up."""
        import subprocess
        exe = getattr(self, "_ollama_exe", "")
        if not exe:
            return
        self._btn_start_ollama.setEnabled(False)
        self._conn_status_lbl.setText(tr("starting Ollama…"))
        try:
            # "ollama serve" is the daemon; detached so closing Podbye does not
            # take the model server down with it.
            subprocess.Popen(
                [exe, "serve"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            self._btn_start_ollama.setEnabled(True)
            self._conn_status_lbl.setText(tr("could not start Ollama"))
            return
        # It needs a moment to bind the port; re-test without blocking the UI.
        QTimer.singleShot(1500, self, self._retest_after_start)

    def _retest_after_start(self):
        self._btn_start_ollama.setEnabled(True)
        self._test_connection()

    def _auto_test_connection(self):
        """Silently test the saved endpoint in background on startup."""
        endpoint = self._endpoint_input.text().strip()
        discover = self._rb_ep_local.isChecked()
        if not endpoint and not discover:
            return
        endpoint = endpoint or LOCAL_ENDPOINT

        def _worker():
            from app.services.ollama_client import probe
            r = probe(endpoint, discover=discover)
            try:
                self._conn_result.result.emit(
                    r["status"], r["backend"], r["models"], r["runtime_path"],
                    r["endpoint"])
            except RuntimeError:
                pass  # Qt object already destroyed during shutdown

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _reset_all_settings(self):
        """Every preference at once, with no undo — so it asks first.

        It did not. One click cleared the endpoint, the model, the theme, the
        language and the safeguard toggles, with nothing between the pointer
        and the loss. Same shape as the other irreversible prompt in the app
        (main.py's close-while-busy question): Yes/No, defaulting to No.
        """
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self,
            tr("Reset all settings?"),
            tr("Every preference goes back to its default — endpoint, model, "
               "theme, language and the cleanup safeguards.\n\n"
               "Your scan history and kept paths are not touched."),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

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

    def _restyle_reset_button(self):
        """Reset reads as an ordinary secondary button at rest, and turns red
        on hover and on keyboard focus.

        The danger is real, so it is shown at the moment the user is about to
        act rather than for the whole time the page is open. Focus is included
        deliberately: someone tabbing to this button gets the same warning a
        pointer does.
        """
        if not hasattr(self, "_btn_reset"):
            return
        p = get_palette()
        risk = p.get("risk", "#c67a69")
        risk_soft = p.get("risk_soft", "#2a1d1a")
        rest_border = p.get("border_alt", "#2b3d33")
        rest_text = p.get("text_dim", "#8a9b8f")
        danger = f"border-color: {risk}; color: {risk};"
        self._btn_reset.setStyleSheet(
            f"QPushButton#DangerQuiet {{ background: transparent; "
            f"border: 1px solid {rest_border}; color: {rest_text}; "
            f"padding: 7px 14px; border-radius: 2px; font-size: 11px; }}"
            f"QPushButton#DangerQuiet:hover {{ background: {risk_soft}; {danger} }}"
            f"QPushButton#DangerQuiet:focus {{ {danger} }}"
        )

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
        self._restyle_reset_button()
        # Re-apply the Apply button style with the current palette too.
        if hasattr(self, "_btn_apply_lang"):
            self._restyle_apply_button()

    def _show_no_models(self):
        """Empty the model list, keeping only a model the user really chose.

        Podbye used to fill this with a placeholder catalogue — "llama3.2:3b",
        "qwen2.5:7b", "mistral", "gemma2:2b" — whenever the server was offline.
        They looked exactly like real entries, so picking one was the obvious
        move, and every explanation then failed because that model had never
        been pulled. A list is now either the models you actually have, or
        empty with the reason spelled out beside it.
        """
        saved_model = self._store.get("ai_model", "") if self._store else ""
        # Block signals: repopulating must not fire _save_model and overwrite
        # the stored model.
        self._model_combo.blockSignals(True)
        try:
            self._model_combo.clear()
            if saved_model:
                # Keep it visible so the setting is not silently lost while the
                # server is down; it is a real past choice, not a suggestion.
                # None: unreachable server means unknown size, not "no size".
                self._model_combo.addItem(saved_model, None)
                self._model_combo.setCurrentIndex(0)
        finally:
            self._model_combo.blockSignals(False)
        self._model_combo.setEnabled(self._model_combo.count() > 0)
        self._update_library_summary()
        self._update_model_meta()
