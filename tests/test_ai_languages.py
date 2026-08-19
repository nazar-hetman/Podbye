"""Every language the AI picker offers must have a real instruction behind it.

The picker is deliberately not gated on locale files — a user running a
multilingual model can ask for German even though de.json does not exist. That
freedom only works if the prompt actually asks for German properly.

Spanish, German and French were offered for months while falling through to a
generic fallback phrased in English ("Return explanation in German only"). It
is not broken, but it is the weaker form: a small local model follows an
instruction written *in* the target language far more reliably than one that
merely names it.
"""
import pytest

from app.i18n import explanation_languages
from app.services.prompt_builder import _LANGUAGE_INSTRUCTIONS, _language_instruction


@pytest.mark.parametrize("language", explanation_languages())
def test_every_offered_language_has_its_own_instruction(language):
    assert language.lower() in _LANGUAGE_INSTRUCTIONS, (
        f"{language} is offered in the AI language picker but has no entry in "
        f"_LANGUAGE_INSTRUCTIONS, so it falls back to a generic English "
        f"instruction that small models follow less reliably"
    )


@pytest.mark.parametrize("language", explanation_languages())
def test_the_instruction_is_written_in_the_target_language(language):
    """The generic fallback is recognisable by its English phrasing."""
    instruction = _language_instruction(language)
    assert not instruction.startswith("Return explanation in"), (
        f"{language} still uses the generic English fallback: {instruction!r}"
    )
    assert instruction.strip(), f"{language} has an empty instruction"


def test_an_unknown_language_still_produces_a_usable_instruction():
    """The fallback must stay — a user can type a language we never listed."""
    instruction = _language_instruction("Klingon")
    assert "Klingon" in instruction


def test_language_lookup_is_case_and_whitespace_insensitive():
    assert _language_instruction("  GERMAN  ") == _LANGUAGE_INSTRUCTIONS["german"]


def test_none_falls_back_to_english():
    assert _language_instruction(None) == _LANGUAGE_INSTRUCTIONS["english"]
