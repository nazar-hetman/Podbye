"""Locale changes must redraw Podbye vocabulary, not historical/raw evidence.

These checks deliberately render the widgets that read persisted values.  A
translation table being present is not sufficient: sessions can outlive the
locale in which they were created.
"""
from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QLabel

from app.i18n import available_languages, get_language, set_language


_NAV = ("Home", "Quick Cleanup", "Analyze", "Findings", "Startups", "History", "Settings")
_NAV_TEXT = {
    "English": _NAV,
    "Ukrainian": ("Головна", "Очищення", "Аналіз", "Знахідки", "Автозапуск", "Історія", "Налаштування"),
    "Spanish": ("Inicio", "Limpieza rápida", "Analizar", "Resultados", "Inicio automático", "Historial", "Configuración"),
    "German": ("Startseite", "Bereinigung", "Analyse", "Ergebnisse", "Autostart", "Verlauf", "Einstellungen"),
    "French": ("Accueil", "Nettoyage", "Analyser", "Résultats", "Démarrage", "Historique", "Paramètres"),
    "Polish": ("Strona główna", "Czyszczenie", "Analiza", "Wyniki", "Autostart", "Historia", "Ustawienia"),
}


@pytest.fixture(autouse=True)
def _restore_language():
    old = get_language()
    yield
    set_language(old)


@pytest.mark.parametrize("language", available_languages())
def test_rendered_navigation_uses_the_active_ui_language(qapp, language):
    """The Sidebar is the persistent shell, so it must never retain English."""
    from app.widgets.sidebar import Sidebar

    set_language(language)
    sidebar = Sidebar()
    sidebar.show()
    qapp.processEvents()
    try:
        # Nothing elides here any more: a language whose full name does not
        # fit 119px defines a shorter "nav:<screen>" form instead, which is
        # why Ukrainian, German and Polish read shorter than their prose
        # translations. The rendered value is always the locale's, never the
        # English route key.
        assert tuple(button._name_lbl.full_text() for button in sidebar._buttons) == _NAV_TEXT[language]
    finally:
        sidebar.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("language, scope, mode, outcome", [
    ("Ukrainian", "Усі диски", "Адаптивний аналіз", "Зупинено (частково)"),
    ("German", "Alle Laufwerke", "Adaptive Analyse", "Gestoppt (teilweise)"),
    ("Spanish", "Todas las unidades", "Análisis adaptativo", "Detenido (parcial)"),
])
def test_a_historical_session_relocalizes_canonical_values(qapp, language, scope, mode, outcome):
    """An English-created stopped session reads as the current UI language.

    ``Images`` deliberately remains English: it is a PODBYE taxonomy ID, not
    translated historical prose.
    """
    from app.screens.history import SessionDetail

    record = {
        "session_id": "created-under-english",
        "target": "All drives", "scan_mode": "smart", "status": "stopped",
        "start_time": 100.0, "saved_at": 140.0, "display_count": 7,
        "scanned_count": 7, "total_size": 10_000,
        "risk_totals": {"Review": 2},
        "category_totals": {"Images": {"count": 7, "size_bytes": 10_000}},
    }
    set_language(language)
    panel = SessionDetail(record, lambda *_: None, lambda *_: None, lambda *_: None)
    panel.show()
    qapp.processEvents()
    try:
        rendered = "\n".join(label.text() for label in panel.findChildren(QLabel))
        assert scope in rendered
        assert mode in rendered
        assert outcome in rendered
        assert "Images" in rendered
        assert "All drives" not in rendered
    finally:
        panel.deleteLater()
        qapp.processEvents()


def test_a_real_history_path_is_not_relocalized():
    from app.screens.history import _target_label

    set_language("Spanish")
    assert _target_label("D:/Projects/Podbye") == "D:/Projects/Podbye"


def test_a_legacy_rule_recommendation_does_not_freeze_findings_in_english():
    """Only template-backed reasons are safe to carry across locale changes."""
    from app.screens.findings_dashboard import _finding_recommendation

    set_language("German")
    _state, _advice, evidence, _accent = _finding_recommendation({
        "risk": "Optional", "category": "Cache & Temp", "size": "1 GB",
        "actionability": "recycle",
        # This is exactly the generated display prose old snapshots contain.
        "recommendation": "Optional cleanup — remove if you no longer need it",
        "risk_reason": "", "reason_key": "", "reason_args": {},
    })

    assert evidence == "Dies ist wahrscheinlich entfernbar, kann aber weiterhin nützlich sein."
    assert "Optional cleanup" not in evidence


def test_legacy_ai_prose_is_preserved_but_marked_as_language_unknown(qapp):
    """Old sessions lacked ai_language; guessing from the new UI would lie."""
    from app.screens.findings_dashboard import RightSidebar

    set_language("German")
    answer = "This answer was generated before its language was recorded."
    sidebar = RightSidebar(open_cb=lambda *_: None, copy_cb=lambda *_: None,
                           ask_ai_cb=lambda *_args, **_kwargs: "")
    sidebar.resize(500, 700)
    sidebar.show()
    sidebar.populate({
        "path": "C:/old-record", "name": "old-record", "risk": "Review",
        "entity_type": "cache_folder", "category": "Cache & Temp",
        "size": "1 GB", "size_bytes": 1_000_000_000,
        "file_count": 1, "folder_count": 0,
        "ai_status": "ready", "ai_explanation": answer,
        # Intentionally no ai_language: this is the legacy shape.
    })
    qapp.processEvents()
    try:
        panel = sidebar.detail_widget
        assert panel._ai_state_badge.full_text() == "KI-Antwort · Originalsprache unbekannt"
        assert panel._ai_state_badge.toolTip() == "KI-Antwort · Originalsprache unbekannt"
        assert panel._ai_text.toPlainText() == answer
        assert panel._ai_ask_btn.isVisible()
        assert panel._ai_ask_btn.text() == "Erneut fragen"
    finally:
        sidebar.deleteLater()
        qapp.processEvents()


def test_explanation_cache_never_crosses_model_or_answer_language(tmp_path, monkeypatch):
    """Changing output language/model must not revive incompatible cached prose."""
    import app.services.ai_explainer as ai
    from app.models.finding import Finding

    monkeypatch.setattr(ai, "_cache_dir", lambda: tmp_path)
    finding = Finding("C:/cache.bin", "cache.bin", False, 4, ".bin", 1, 1, "C:/")
    ai._save_cached(finding, "model-a", "neutral", "standard", "English", "English answer")

    assert ai._load_cached(finding, "model-a", "neutral", "standard", "English") == "English answer"
    assert ai._load_cached(finding, "model-a", "neutral", "standard", "German") is None
    assert ai._load_cached(finding, "model-b", "neutral", "standard", "English") is None

    cache_file = next(tmp_path.glob("*.json"))
    assert json.loads(cache_file.read_text(encoding="utf-8"))["language"] == "English"
