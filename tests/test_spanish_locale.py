"""Spanish is a shipped locale with localized UI and preserved raw values."""
from app.i18n import (LANGUAGES, available_languages, get_language,
                      init_language, set_language, tr, tr_count)


def test_spanish_is_registered_and_offered_in_the_language_picker():
    assert LANGUAGES["Spanish"] == "es"
    assert "Spanish" in available_languages()


def test_spanish_restores_from_the_persisted_language_setting():
    class Store:
        def get(self, key, default=None):
            return "Spanish" if key == "ui_language" else default

    previous = get_language()
    try:
        init_language(Store())
        assert get_language() == "Spanish"
        assert tr("Start scan") == "Iniciar análisis"
        set_language("English")
        assert tr("Start scan") == "Start scan"
    finally:
        set_language(previous)


def test_spanish_uses_analysis_cleanup_and_findings_terms():
    previous = get_language()
    set_language("Spanish")
    try:
        assert tr("Start Analysis") == "Iniciar análisis"
        assert tr("FINDINGS") == "RESULTADOS"
        assert tr("Start scan") == "Iniciar análisis"
        assert tr("Images") == "Images"
    finally:
        set_language(previous)


def test_spanish_static_statuses_keep_cleanup_and_partial_analysis_distinct():
    """Static state copy must not fall back to English or blur product actions."""
    previous = get_language()
    set_language("Spanish")
    try:
        assert tr("You are keeping this") == "Estás ignorando este elemento"
        assert tr("Cleanup finished") == "Limpieza terminada"
        assert tr("Analysis was stopped — some files were never scanned and are missing here.") == (
            "El análisis se detuvo: algunos archivos nunca se analizaron y no aparecen aquí."
        )
        assert tr("Windows refused to start the uninstaller (access denied).") == (
            "Windows rechazó iniciar el desinstalador (acceso denegado)."
        )
    finally:
        set_language(previous)


def test_spanish_dynamic_cleanup_and_recommendation_templates_format_naturally():
    """Dynamic Podbye prose keeps its placeholders and outcome semantics."""
    previous = get_language()
    set_language("Spanish")
    try:
        partial = tr(
            "{cleaned} categories cleaned · {locked} files still in use — "
            "steps to finish are shown below",
            cleaned=3,
            locked=2,
        )
        assert partial == (
            "3 categorías limpiadas · 2 archivos siguen en uso; "
            "más abajo se muestran los pasos para terminar."
        )
        recommendation = tr(
            "Recommendation: consider disabling this if you do not need it "
            "immediately after sign-in."
        )
        assert recommendation.startswith("Recomendación:")
        assert "desactivarlo" in recommendation
        cloud = tr(
            "{n} item(s) are inside a cloud-sync folder. Deletion will "
            "propagate to your cloud account and all synced devices.",
            n=12,
        )
        assert "12 elementos" in cloud
        assert "dispositivos sincronizados" in cloud
    finally:
        set_language(previous)


def test_spanish_cleanup_counts_use_a_real_singular_form():
    previous = get_language()
    set_language("Spanish")
    try:
        key = "{n} item(s) · {size} will be sent to the Recycle Bin"
        assert tr_count(key, 1, n=1, size="2.2 GB") == (
            "Se enviará 1 elemento · 2.2 GB a la Papelera de reciclaje"
        )
        assert tr_count(key, 2, n=2, size="2.2 GB") == (
            "Se enviarán 2 elementos · 2.2 GB a la Papelera de reciclaje"
        )
    finally:
        set_language(previous)
