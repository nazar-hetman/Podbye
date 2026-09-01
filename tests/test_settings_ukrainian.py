"""Rendered Ukrainian semantics and fit for every Settings section."""
from __future__ import annotations

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QPushButton

from app.fonts import FONT_UI, load_fonts
from app.i18n import set_language
from app.screens.settings import SettingsScreen
from app.themes.theme_manager import build_qss
from app.widgets.controls import ElidedLabel


class _Store:
    def __init__(self):
        self.values = {
            "ui_language": "Ukrainian",
            "ai_tone": "Professional",
            "ai_length": "Detailed",
            "ai_explanation_language": "Ukrainian",
            "ai_endpoint_mode": "local",
            "ai_endpoint": "http://127.0.0.1:11434",
            "ai_timeout": 180,
            "ai_max_concurrent": 3,
            "ai_findings_enabled": False,
            "ai_startups_enabled": True,
            "ai_explain_risky_only": False,
            "perm_delete_enabled": False,
            "confirm_risky_cleanup": True,
            "scan_cross_volumes": False,
            "close_behavior": "ask",
            "theme": "forest",
            "ai_model": "",
        }
        self.config_path = "C:/Users/ExampleUser/AppData/Roaming/Podbye/config.json"

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set_and_save(self, key, value):
        self.values[key] = value


@pytest.fixture
def settings(qapp):
    previous_font = qapp.font()
    load_fonts()
    qapp.setFont(QFont(FONT_UI, 10))
    qapp.setStyleSheet(build_qss("forest"))
    set_language("Ukrainian")
    store = _Store()
    screen = SettingsScreen(settings_store=store)
    screen.resize(1100, 700)
    screen.show()
    for _ in range(8):
        qapp.processEvents()
    yield screen, store
    screen.deleteLater()
    qapp.processEvents()
    set_language("English")
    qapp.setFont(previous_font)


def _texts(widget):
    labels = [label.text() for label in widget.findChildren(QLabel)
              if label.isVisibleTo(widget) and label.text().strip()]
    buttons = [button.text() for button in widget.findChildren(QPushButton)
               if button.isVisibleTo(widget) and button.text().strip()]
    return labels + buttons


def _fit_faults(widget):
    faults = []
    for label in widget.findChildren(QLabel):
        if (not label.isVisibleTo(widget) or isinstance(label, ElidedLabel)
                or not label.text().strip()):
            continue
        if label.wordWrap():
            if label.heightForWidth(label.width()) > label.height() + 1:
                faults.append(label.text())
        elif label.sizeHint().width() > label.width() + 1:
            faults.append(label.text())
    for button in widget.findChildren(QPushButton):
        if button.isVisibleTo(widget) and button.sizeHint().width() > button.width() + 1:
            faults.append(button.text())
    return faults


def test_enum_values_are_localized_without_mutating_stored_values(settings):
    screen, store = settings

    assert screen._lang_combo.currentText() == "Українська"
    assert screen._lang_combo.currentData() == "Ukrainian"
    assert screen._tone_combo.currentText() == "Професійний"
    assert screen._tone_combo.currentData() == "Professional"
    assert screen._length_combo.currentText() == "Докладна"
    assert screen._length_combo.currentData() == "Detailed"
    assert screen._ai_lang_combo.currentText() == "Українська"
    assert screen._ai_lang_combo.currentData() == "Ukrainian"

    screen._tone_combo.setCurrentIndex(screen._tone_combo.findData("Technical"))
    assert store.values["ai_tone"] == "Technical"


def test_analysis_and_ignored_path_terms_match_the_feature_semantics(settings):
    screen, _store = settings
    assert screen._nav_btns["scan"].text() == "Аналіз"
    screen._switch_section("scan")
    text = screen._keep_empty_lbl.text()
    panel_title = next(label.text() for label in screen._keep_panel.findChildren(QLabel)
                       if label.text() == "ІГНОРОВАНІ ШЛЯХИ")
    assert panel_title == "ІГНОРОВАНІ ШЛЯХИ"
    assert screen._keep_empty_lbl.isVisible()
    assert "Припинити ігнорувати" not in text  # no paths are configured
    assert screen._cb_cross_volumes.text() == "Увімкнути"
    labels = [label.text() for label in screen._stack.currentWidget().findChildren(QLabel)]
    assert "Переходити на інші диски під час аналізу" in labels
    assert "Сканувати між дисками" not in labels
    assert "не пропонуватиметься для видалення" in text


def test_ai_settings_use_ai_and_keep_technical_values_raw(settings):
    screen, _store = settings
    screen._switch_section("ai")
    text = "\n".join(_texts(screen))
    assert "Час очікування AI" in text
    assert "Кількість одночасних AI-пояснень" in text
    assert "ШІ" not in text
    assert screen._endpoint_input.text() == "http://127.0.0.1:11434"


@pytest.mark.parametrize("section", ["general", "ai", "scan", "about"])
@pytest.mark.parametrize("width", [1100, 1600])
def test_ukrainian_settings_fit_every_section(settings, qapp, section, width):
    screen, _store = settings
    screen.resize(width, 900)
    screen._switch_section(section)
    for _ in range(8):
        qapp.processEvents()
    # The active viewport is the relevant layout at the minimum window size;
    # offscreen rows are checked when their tab is selected in this matrix.
    assert _fit_faults(screen._stack.currentWidget()) == []
