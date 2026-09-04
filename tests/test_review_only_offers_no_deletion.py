"""A review-only finding must not carry a whole-folder delete button.

Reported against the installed build. C:/Users/<user>/AppData/Local/wsl is
typed application_data, which actionability_for_type calls "review_only" — the
taxonomy saying this is something to look at, not something to offer a
one-click removal for. The inspector offered "Move to Recycle Bin" anyway,
because the gate excluded only "protected" and "kept".

Clicking it did no harm: the folder is a 15.4 GB WSL disk image, the Recycle
Bin on that drive holds 9.79 GB, and cleanup_engine refused it rather than let
Windows delete it permanently. But the button should never have been there —
the promise was one Podbye could not keep, and the taxonomy already said so.

Reviewing stays available. This is only about the destructive action.
"""
import pytest

from app.models.smart_entity import actionability_for_type


REVIEW_ONLY_ENTITY = {
    "path": "C:/Users/tester/AppData/Local/wsl",
    "name": "wsl (Local)",
    "entity_type": "application_data",
    "risk": "Review",
    "size_bytes": 16_542_372_183,
    "file_count": 2,
    "folder_count": 1,
    "children_sample": ["{0ba1792e-ee3b-44e8-a8b2-d9fa3ac1a7f1}",
                        "ext4.vhdx", "shortcut.ico"],
}


def test_the_taxonomy_calls_this_review_only():
    """The premise. If this changes, the rest of the file is about nothing."""
    assert actionability_for_type("application_data", "Review") == "review_only"


def _panel(qapp):
    from app.screens.findings_dashboard import _PreallocDetailPanel
    # A recycle callback must be present, or the gate short-circuits on
    # "nothing to call" and the test would pass without exercising the rule.
    return _PreallocDetailPanel(
        open_cb=lambda _p: None,
        copy_cb=lambda _p: None,
        recycle_cb=lambda _e: None,
    )


def test_a_review_only_finding_gets_no_destructive_action(qapp):
    panel = _panel(qapp)
    try:
        assert panel._destructive_action(REVIEW_ONLY_ENTITY) == ""
    finally:
        panel.deleteLater()
        qapp.processEvents()


def test_a_recyclable_finding_still_gets_its_button(qapp):
    """The gate must not have been closed on everything."""
    panel = _panel(qapp)
    try:
        recyclable = dict(REVIEW_ONLY_ENTITY,
                          entity_type="cache_folder", risk="Safe",
                          path="C:/Users/tester/AppData/Local/Temp/somecache")
        assert actionability_for_type("cache_folder", "Safe") == "recycle"
        assert panel._destructive_action(recyclable) == "recycle"
    finally:
        panel.deleteLater()
        qapp.processEvents()


def test_the_header_does_not_promise_a_removal(qapp):
    """The wording above the contents comes from the same decision, so a
    review-only finding must not announce WILL BE MOVED TO RECYCLE BIN."""
    panel = _panel(qapp)
    try:
        action = panel._destructive_action(REVIEW_ONLY_ENTITY)
        assert "RECYCLE" not in panel._action_header(action).upper()
    finally:
        panel.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("etype", ["application_data", "user_profile",
                                   "vm_storage", "development_environment"])
def test_no_review_only_type_offers_a_whole_folder_delete(qapp, etype):
    """Every type the taxonomy marks review_only, not just the reported one."""
    if actionability_for_type(etype, "Review") != "review_only":
        pytest.skip(f"{etype} is not review_only")
    panel = _panel(qapp)
    try:
        entity = dict(REVIEW_ONLY_ENTITY, entity_type=etype,
                      path=f"C:/Users/tester/AppData/Local/{etype}")
        assert panel._destructive_action(entity) == ""
    finally:
        panel.deleteLater()
        qapp.processEvents()
