"""Every nav label fits the sidebar, in every language it ships.

Reported on Ukrainian: "Швидке Очищення — slightly can't fit the menu". It was
three languages, all cut on the same item, because the sidebar is a fixed 196px
and leaves the label 119px of it:

    English    Quick Cleanup         96px   fits
    Ukrainian  Швидке очищення      126px   Швидке очище…
    German     Schnellbereinigung   128px   Schnellbereinig…
    Polish     Szybkie czyszczenie  136px   Szybkie czyszc…

Two fixes were considered and rejected. Shrinking the type for the languages
that overflow means a per-language font size, which is a trap the next locale
falls into silently — and this is the most-read text in the app. Shortening the
shared "Quick Cleanup" string was rejected because "Швидке очищення" is the
right phrase in a sentence about what the cleanup did; only the sidebar has
119px.

So the sidebar asks for ``nav:<screen>`` first and falls back to the full name.
A language defines one only if it needs one.

The same audit found ``WORKSTATION`` and ``SYSTEM`` rendering in English in
Spanish, German and Polish. They reach tr() from ``NAV_ITEMS`` as variables, so
``_tr_keys()`` — which scans for literal ``tr("...")`` calls — never knew they
existed, and locale coverage reported 100% while three strings were untranslated
on screen. That scan now includes the table.
"""
import pytest
from PySide6.QtGui import QFont

from app.fonts import FONT_UI, load_fonts
from app.i18n import available_languages, set_language, tr
from app.themes.theme_manager import build_qss
from app.widgets.sidebar import Sidebar

# app/widgets/sidebar.py: setFixedWidth(196), and the row spends the rest on
# margins, the icon and the shortcut hint.
SIDEBAR_WIDTH = 196


@pytest.fixture
def dressed(qapp):
    previous = qapp.font()
    load_fonts()
    qapp.setFont(QFont(FONT_UI, 10))
    qapp.setStyleSheet(build_qss("forest"))
    yield qapp
    set_language("English")
    qapp.setFont(previous)


def _sidebar(dressed):
    bar = Sidebar()
    bar.resize(SIDEBAR_WIDTH, 900)
    bar.show()
    for _ in range(6):
        dressed.processEvents()
    return bar


@pytest.mark.parametrize("language", available_languages())
def test_no_nav_label_is_cut(dressed, language):
    set_language(language)
    bar = _sidebar(dressed)
    try:
        cut = [(b._name_lbl.full_text(),
                b._name_lbl.fontMetrics().horizontalAdvance(b._name_lbl.full_text()),
                b._name_lbl.width())
               for b in bar._buttons
               if b._name_lbl.text() != b._name_lbl.full_text()]
        assert cut == [], (
            f"{language}: " + ", ".join(f"{t!r} needs {n}px of {w}px"
                                        for t, n, w in cut))
    finally:
        bar.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("language", available_languages())
def test_every_nav_label_is_translated(dressed, language):
    """The fallback must not quietly hand back the English screen name.

    ``_nav_label`` returns tr(name) when a language defines no short form, and
    an earlier version of it returned *name* — which would have shown English
    labels to every language that fits.
    """
    set_language(language)
    bar = _sidebar(dressed)
    try:
        english = {name for _section, items in Sidebar.NAV_ITEMS.items()
                   for name, _icon, _shortcut in items}
        shown = {b._name_lbl.full_text() for b in bar._buttons}
        if language == "English":
            assert shown == english
        else:
            # Autostart is genuinely the German and Polish word, so an overlap
            # of one is expected; wholesale English is not.
            assert len(shown & english) <= 1, f"{language} shows {shown & english}"
    finally:
        bar.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("language", available_languages())
def test_the_section_headers_are_translated(dressed, language):
    """They reach tr() as variables, which is how they shipped in English."""
    set_language(language)
    untranslated = [s for s in Sidebar.NAV_ITEMS if tr(s) == s]
    if language in ("English", "German", "Polish"):
        # SYSTEM is the correct word in German and Polish; both carry an
        # explicit entry so the coverage scan can see them.
        assert "WORKSTATION" not in untranslated or language == "English"
    else:
        assert untranslated == [], f"{language}: {untranslated} still English"


# ── the short form is opt-in, and only where it is needed ─────────

def test_a_language_without_a_short_form_gets_the_full_name(dressed):
    from app.widgets.sidebar import _nav_label

    set_language("French")

    assert _nav_label("Quick Cleanup") == tr("Quick Cleanup") == "Nettoyage"


def test_a_language_with_one_gets_the_short_form(dressed):
    from app.widgets.sidebar import _nav_label

    set_language("Ukrainian")

    assert _nav_label("Quick Cleanup") == "Очищення"
    # ...and the full phrase is untouched for prose about what was cleaned.
    assert tr("Quick Cleanup") == "Швидке очищення"


def test_english_needs_no_short_forms(dressed):
    from app.widgets.sidebar import _nav_label

    set_language("English")

    for _section, items in Sidebar.NAV_ITEMS.items():
        for name, _icon, _shortcut in items:
            assert _nav_label(name) == name


# ── the coverage scan can now see table-driven keys ───────────────

def test_the_coverage_scan_includes_the_nav_table():
    """It reported 100% while WORKSTATION and SYSTEM were English on screen."""
    import sys

    sys.path.insert(0, "tests")
    from test_translation_coverage import _tr_keys

    keys = _tr_keys()
    assert "WORKSTATION" in keys
    assert "SYSTEM" in keys
    assert "Quick Cleanup" in keys
