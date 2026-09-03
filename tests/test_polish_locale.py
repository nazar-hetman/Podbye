"""Polish is a shipped locale, with localized UI and preserved raw values."""
from app.i18n import (LANGUAGES, available_languages, display_name,
                      set_language, tr)


def test_polish_is_registered_and_ships_a_locale_file():
    # "Polish" is the canonical name — the one stored in settings and passed
    # to set_language, English like every other key. "Polski" is what the
    # picker shows. See app/i18n.ENDONYMS.
    assert LANGUAGES["Polish"] == "pl"
    assert "Polish" in available_languages()
    assert display_name("Polish") == "Polski"


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
