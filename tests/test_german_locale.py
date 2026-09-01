"""German is a shipped locale with localized UI and preserved raw values."""
from app.i18n import (LANGUAGES, available_languages, get_language,
                      init_language, set_language, tr)


def test_german_is_registered_and_offered_in_the_language_picker():
    assert LANGUAGES["German"] == "de"
    assert "German" in available_languages()


def test_german_restores_from_the_persisted_language_setting():
    class Store:
        def get(self, key, default=None):
            return "German" if key == "ui_language" else default

    previous = get_language()
    try:
        init_language(Store())
        assert get_language() == "German"
        assert tr("Start scan") == "Analyse starten"
        set_language("English")
        assert tr("Start scan") == "Start scan"
    finally:
        set_language(previous)


def test_german_home_cleanup_and_analysis_use_product_terms():
    previous = get_language()
    set_language("German")
    try:
        assert tr("Start Analysis") == "Analyse starten"
        assert tr("FINDINGS") == "ERGEBNISSE"
        assert tr("Start scan") == "Analyse starten"
        assert tr("Stopped") == "Gestoppt"
        assert tr("Images") == "Images"
    finally:
        set_language(previous)


def test_german_formats_dynamic_reasons_and_cleanup_results():
    previous = get_language()
    set_language("German")
    try:
        duplicate = tr(
            "{count} identical copies were found ({where}). The newest copy is kept as "
            "the original; the rest are extra and can be moved to the Recycle Bin to "
            "reclaim {size}. Make sure none of the copies is still in active use first.",
            count=3, where="C:\\Apps", size="1.2 GB")
        assert "3 identische Kopien" in duplicate
        assert "C:\\Apps" in duplicate
        assert "1.2 GB" in duplicate

        cleanup = tr("✓  {count} item(s) moved to Recycle Bin · {freed} freed",
                     count=12, freed="800 MB")
        assert cleanup == "✓  12 Elemente in den Papierkorb verschoben · 800 MB freigegeben"
    finally:
        set_language(previous)
