"""The language list is written in the languages it offers.

Two problems, one fix.

The picker listed ``English, Ukrainian, Spanish, German, French, Polski`` —
five English names and one endonym, because Polish had been registered under
its own name while the rest used English ones.

The deeper problem was what the other five did. Their labels came from
``tr()``, so the list was translated into whatever the UI was *currently* set
to. Switch to a language you cannot read and every option in the list is
written in that language — including the one you need to get back. A language
picker is the one list that must not be localized.

So the display name is now the endonym, fixed and never translated, while the
canonical name stays English: it is the value in the settings file, the
argument to ``set_language()``, and the key in ``LANGUAGES``. Separating them
is what lets the picker read correctly without changing what is stored.

"""
import pytest

from app.i18n import (ENDONYMS, LANGUAGES, available_languages,
                      display_name, get_language, set_language)


# ── the list reads in its own languages ───────────────────────────

def test_every_offered_language_is_shown_in_its_own_name():
    shown = [display_name(name) for name in available_languages()]

    assert shown == ["English", "Українська", "Español", "Deutsch",
                     "Français", "Polski"], shown


@pytest.mark.parametrize("ui_language", available_languages())
def test_the_list_does_not_change_with_the_interface_language(ui_language):
    """The point of endonyms. Translating the list means a user who lands in a
    language they cannot read has no labelled way out."""
    previous = get_language()
    try:
        set_language(ui_language)
        shown = [display_name(name) for name in available_languages()]
        assert shown == ["English", "Українська", "Español", "Deutsch",
                         "Français", "Polski"], f"changed under {ui_language}"
    finally:
        set_language(previous)


def test_every_language_has_an_endonym():
    """A new locale file must not appear in the picker under an English name."""
    missing = [name for name in available_languages()
               if name != "English" and name not in ENDONYMS]

    assert missing == [], f"no endonym for {missing}"


# ── the stored name is unchanged and English ──────────────────────

def test_the_canonical_names_are_all_english():
    assert set(LANGUAGES) == {"English", "Ukrainian", "Spanish", "German",
                              "French", "Polish"}


def test_an_endonym_is_never_also_a_stored_name():
    """The two vocabularies stay separate, so a display label can never be
    mistaken for -- or written into -- the settings file. English is the one
    language whose endonym is its canonical name, and it needs no entry."""
    for name, endonym in ENDONYMS.items():
        assert endonym not in LANGUAGES, (
            f"{endonym!r} is both what the picker shows and what gets stored")
        assert name in LANGUAGES


# ── and the picker itself behaves ─────────────────────────────────

@pytest.fixture
def settings(qapp, tmp_path, monkeypatch):
    from app.themes.theme_manager import build_qss

    monkeypatch.setenv("APPDATA", str(tmp_path))
    qapp.setStyleSheet(build_qss("forest"))
    made = []

    def build(stored):
        from app.config.settings_store import SettingsStore
        from app.screens.settings import SettingsScreen

        store = SettingsStore()
        store.set_and_save("ui_language", stored)
        screen = SettingsScreen(settings_store=store)
        screen.resize(1400, 900)
        screen.show()
        for _ in range(6):
            qapp.processEvents()
        made.append(screen)
        return screen

    yield build
    for screen in made:
        screen.deleteLater()
    qapp.processEvents()


def test_the_picker_shows_endonyms(settings):
    screen = settings("English")
    combo = screen._lang_combo
    shown = [combo.itemText(i) for i in range(combo.count())]

    assert shown == ["English", "Українська", "Español", "Deutsch",
                     "Français", "Polski"], shown


def test_the_picker_stores_the_canonical_name(settings):
    screen = settings("English")

    screen._lang_combo.setCurrentIndex(screen._lang_combo.findData("Polish"))
    screen._apply_language()

    assert screen._store.get("ui_language") == "Polish"


def test_the_picker_still_fits_its_fixed_width(settings):
    """The combo is setFixedWidth(168); "Українська" is the longest label."""
    screen = settings("English")
    combo = screen._lang_combo

    for i in range(combo.count()):
        needed = combo.fontMetrics().horizontalAdvance(combo.itemText(i))
        assert needed < combo.width() - 24, (
            f"{combo.itemText(i)!r} needs {needed}px of {combo.width()}px")


# ── the other language list is the opposite case ──────────────────

def test_the_ai_answer_language_list_is_translated(qapp):
    """Deliberately not endonyms.

    "Which language should the AI answer in" is a question about output,
    asked of someone reading the current UI — so those names belong in that
    UI's language, exactly as the UI-language picker's must not.

    It was half filled in: Polish had five of six names, Ukrainian and French
    two, Spanish and German none, so the list read as a mix of the user's
    language and English.
    """
    from app.i18n import explanation_languages, tr

    previous = get_language()
    try:
        for ui_language in available_languages():
            if ui_language == "English":
                continue
            set_language(ui_language)
            untranslated = [name for name in explanation_languages()
                            if tr(name) == name and name != ui_language]
            assert untranslated == [], (
                f"under {ui_language} these stayed English: {untranslated}")
    finally:
        set_language(previous)


def test_the_two_lists_disagree_on_purpose(qapp):
    """If they ever match, one of them has adopted the other's rule."""
    from app.i18n import explanation_languages, tr

    previous = get_language()
    try:
        set_language("Polish")
        ui = [display_name(n) for n in available_languages()]
        ai = [tr(n) for n in explanation_languages()]

        assert ui != ai
        assert "Українська" in ui and "Ukraiński" in ai
    finally:
        set_language(previous)


def test_every_offered_answer_language_has_a_native_instruction():
    """The generic fallback is the failure mode this guards."""
    from app.i18n import explanation_languages
    from app.services.prompt_builder import _language_instruction

    for language in explanation_languages():
        instruction = _language_instruction(language)
        assert not instruction.startswith("Return explanation in"), (
            f"{language} fell through to the generic English instruction")
