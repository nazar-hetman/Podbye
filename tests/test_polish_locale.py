"""Polish is a shipped locale, with localized UI and preserved raw values."""
from app.i18n import (LANGUAGES, available_languages, display_name,
                      get_language, init_language, set_language, tr)


def test_polish_is_registered_and_ships_a_locale_file():
    # "Polish" is the canonical name — the one stored in settings and passed
    # to set_language, English like every other key. "Polski" is what the
    # picker shows. See app/i18n.ENDONYMS.
    assert LANGUAGES["Polish"] == "pl"
    assert "Polish" in available_languages()
    assert display_name("Polish") == "Polski"


def test_polish_restores_from_a_setting_written_before_the_rename():
    """"Polski" shipped briefly as the canonical name, so it is in real
    settings files. Those users must keep the language they chose."""
    class Store:
        def get(self, key, default=None):
            return "Polski" if key == "ui_language" else default

    previous = get_language()
    try:
        init_language(Store())
        assert get_language() == "Polish"
        assert tr("Start scan") == "Rozpocznij analizę"
        set_language("English")
        assert tr("Start scan") == "Start scan"
    finally:
        set_language(previous)


def test_polish_preserves_product_taxonomy_and_technical_values():
    set_language("Polish")
    try:
        assert tr("Images") == "Images"
        assert tr("Cache & Temp") == "Cache & Temp"
        assert tr("All drives") == "Wszystkie dyski"
        assert tr("Polski") == "Polski"
    finally:
        set_language("English")


def test_polish_uses_product_meanings_for_analysis_and_exclusions():
    set_language("Polish")
    try:
        assert tr("Scan") == "Analiza"
        assert tr("Stopped (partial)") == "Zatrzymano (częściowo)"
        assert tr("Kept paths") == "Ignorowane ścieżki"
        assert tr("Keep") == "Ignoruj podczas czyszczenia"
    finally:
        set_language("English")


def test_polish_localizes_dynamic_reasoning_and_result_templates():
    set_language("Polish")
    try:
        duplicate = tr(
            "{count} identical copies were found ({where}). The newest copy is kept as "
            "the original; the rest are extra and can be moved to the Recycle Bin to "
            "reclaim {size}. Make sure none of the copies is still in active use first.",
            count=3, where="C:\\Apps", size="1.2 GB")
        assert "Znaleziono identyczne kopie: 3" in duplicate
        assert "C:\\Apps" in duplicate
        assert "1.2 GB" in duplicate

        cleanup = tr(
            "✓  {count} item(s) moved to Recycle Bin · {freed} freed",
            count=12, freed="800 MB")
        assert cleanup == "✓  Elementy przeniesione do Kosza: 12 · zwolniono: 800 MB"

        # stdout identifies the technical stream and deliberately remains English.
        assert tr("// stdout") == "// stdout"
    finally:
        set_language("English")
