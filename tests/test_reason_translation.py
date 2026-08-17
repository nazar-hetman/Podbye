"""The evidence line under a finding must be in the reader's language.

"Installed application in Program Files — remove it through its own
uninstaller" is the sentence that tells a user *why* Vigil classified
something, and it stayed English in every translated build.

Translating it where the detector writes it would be the obvious fix and the
wrong one: a scan is saved to disk and reopened later, possibly after the user
changes language, and the stored sentence would be frozen in whichever
language happened to be active when the scan ran. So the template and its
values are stored, and the sentence is composed at display time.
"""
import pytest

from app.i18n import get_language, set_language
from app.models.reasons import (
    Reason, reason_args_of, reason_key_of, translate_reason,
)
from app.models.smart_entity import SmartEntity


@pytest.fixture(autouse=True)
def _restore_language():
    original = get_language()
    yield
    set_language(original)


# ── the value object ──────────────────────────────────────────────


def test_a_reason_is_a_string_everywhere_it_is_already_used():
    """risk_reason is embedded in AI prompts, serialised, and tested with
    .startswith() — a str subclass keeps all of that working."""
    reason = Reason("Known directory: {name}", name="node_modules")
    assert isinstance(reason, str)
    assert reason == "Known directory: node_modules"
    assert reason.startswith("Known directory")
    assert f"{reason}!" == "Known directory: node_modules!"


def test_a_reason_remembers_its_template():
    reason = Reason("Cache folder for {app}", app="JetBrains")
    assert reason_key_of(reason) == "Cache folder for {app}"
    assert reason_args_of(reason) == {"app": "JetBrains"}


def test_a_plain_string_has_no_template():
    assert reason_key_of("just text") == ""
    assert reason_args_of("just text") == {}


def test_a_template_with_no_arguments_is_its_own_text():
    assert Reason("Ungrouped folder") == "Ungrouped folder"


def test_a_broken_template_degrades_to_the_key_instead_of_raising():
    """A missing argument must never take down a scan."""
    assert Reason("Save data for {game}") == "Save data for {game}"


# ── it survives the round trip to disk ────────────────────────────


def test_the_template_reaches_the_saved_session():
    entity = SmartEntity(path="C:/x", name="x", entity_type="cache_folder")
    entity.risk_reason = Reason("Known directory: {name}", name="node_modules")
    data = entity.to_dict()
    assert data["risk_reason"] == "Known directory: node_modules"
    assert data["reason_key"] == "Known directory: {name}"
    assert data["reason_args"] == {"name": "node_modules"}


def test_a_reopened_session_is_rendered_in_the_language_chosen_now():
    """The whole point: scan in English, reopen in Ukrainian, read Ukrainian."""
    from app.state.scan_state import _restore_reason

    set_language("English")
    entity = SmartEntity(path="C:/x", name="x", entity_type="cache_folder")
    entity.risk_reason = Reason("Ungrouped folder")
    saved = entity.to_dict()

    set_language("Ukrainian")
    restored = _restore_reason(saved)
    assert reason_key_of(restored) == "Ungrouped folder"
    assert translate_reason(saved) != "Ungrouped folder", "still English"


def test_a_session_saved_before_templates_still_shows_its_english():
    """Old sessions carry only the rendered sentence. Showing that is honest;
    there is nothing left to translate from."""
    set_language("Ukrainian")
    legacy = {"risk_reason": "Some older explanation"}
    assert translate_reason(legacy) == "Some older explanation"


# ── display ───────────────────────────────────────────────────────


def test_the_reason_is_translated_with_its_values_substituted():
    set_language("Ukrainian")
    entity = {"risk_reason": "Known directory: node_modules",
              "reason_key": "Known directory: {name}",
              "reason_args": {"name": "node_modules"}}
    text = translate_reason(entity)
    assert "node_modules" in text, "the value must survive translation"
    assert text != entity["risk_reason"], "should not still be English"


def test_english_renders_the_key_itself():
    set_language("English")
    entity = {"risk_reason": "", "reason_key": "Cache folder for {app}",
              "reason_args": {"app": "JetBrains"}}
    assert translate_reason(entity) == "Cache folder for JetBrains"


def test_an_entity_with_no_reason_at_all_is_empty_not_an_error():
    assert translate_reason({}) == ""


# ── the detector actually produces them ───────────────────────────


def test_the_detector_labels_its_findings_with_templates():
    """A Reason that never reaches the detector translates nothing."""
    from app.services.entity_detector import detector_reason_templates

    templates = detector_reason_templates()
    assert len(templates) >= 20, f"only found {len(templates)}"
    assert "Ungrouped folder" in templates
    assert "Known directory: {name}" in templates
    # Every template must be a real format string or the args are dead weight.
    for template in templates:
        assert "{" not in template or "}" in template, template
