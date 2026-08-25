"""What Vigil thinks it is, and how Vigil says to remove it, are two things.

They used to be one "Recommendation: …" sentence, which quietly turned a
classification guess into a confident removal instruction. An application
detected from an executable marker is not the same claim as knowing the safe
way to remove it, and a reader cannot tell the two apart when they arrive in
one line.

Both halves already existed in the data — ``confidence_label`` grades the
detection (Verified / Strong / Likely / Uncertain) and ``actionability`` names
the method — so this splits the presentation rather than inventing a second
confidence system.
"""
import pytest

from PySide6.QtCore import QCoreApplication, QEvent

from app.screens.findings_dashboard import (
    CategoryDetailView, _detected_as_text, _detection_confidence_text,
    _removal_method_text,
)


def _entity(**over):
    base = {"path": "E:/Steam", "name": "Steam", "size": "160.1 GB",
            "size_bytes": 171 * 1024 ** 3, "risk": "Optional",
            "entity_type": "portable_app", "entity_type_label":
            "Portable Application", "category": "Applications",
            "file_count": 40_349, "folder_count": 4_344, "ai_status": "none",
            "confidence_label": "Strong", "actionability": "recycle"}
    base.update(over)
    return base


@pytest.fixture
def view(qapp):
    v = CategoryDetailView()
    v._app_index_cache = {}
    v.resize(1400, 800)
    yield v
    v.stop_background_work()
    v.close()
    v.setParent(None)
    v.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


# ── the two dimensions ────────────────────────────────────────────

def test_detection_says_what_it_is():
    assert _detected_as_text(_entity()) == "Portable Application"


def test_removal_says_what_to_do():
    assert "Recycle Bin" in _removal_method_text(_entity())


def test_an_installed_application_is_uninstalled_not_recycled():
    assert "uninstaller" in _removal_method_text(
        _entity(actionability="uninstall"))


def test_a_mixed_folder_is_reviewed_rather_than_removed():
    assert "Review" in _removal_method_text(_entity(actionability="review_only"))


def test_vigil_says_plainly_when_it_will_not_remove_something():
    assert "not remove" in _removal_method_text(
        _entity(actionability="protected", risk="Protected"))


# ── and the uncertainty survives to the screen ────────────────────

@pytest.mark.parametrize("label", ["Verified", "Strong", "Likely", "Uncertain"])
def test_the_existing_grading_reaches_the_user(label):
    graded = _detection_confidence_text(_entity(confidence_label=label))
    assert graded == label.upper()


def test_an_ungraded_entity_claims_no_confidence():
    assert _detection_confidence_text(_entity(confidence_label="")) == ""


def test_a_guess_is_not_dressed_up_as_a_method():
    """Uncertain detection, ordinary method: the two must not blend."""
    entity = _entity(confidence_label="Uncertain", actionability="review_only")
    assert _detection_confidence_text(entity) == "UNCERTAIN"
    assert "Review" in _removal_method_text(entity)


def test_an_uncertain_detection_does_not_instruct_confidently():
    """"UNCERTAIN" above "Move to the Recycle Bin" is a mixed message.

    On a real scan a content-classified folder is graded Uncertain — pass 7
    sets confidence 0.4 — and that folder was 90 GB of model weights. The
    button stays; the sentence stops asserting what the classifier does not
    know.
    """
    entity = _entity(confidence_label="Uncertain", actionability="recycle")
    assert "Recycle Bin" not in _removal_method_text(entity)
    assert "before removing" in _removal_method_text(entity)


def test_a_confident_detection_still_gives_a_plain_instruction():
    entity = _entity(confidence_label="Verified", actionability="recycle")
    assert "Recycle Bin" in _removal_method_text(entity)


# ── a promoted loose file describes itself as a file ──────────────

def test_a_promoted_file_says_what_kind_of_file_it_is():
    """It came out of a "Documents Folder" bucket; it is a document."""
    entity = _entity(path=r"C:\Users\n\Downloads\report.pdf",
                     name="report.pdf", entity_type="document_folder",
                     entity_type_label="Documents Folder",
                     removable_file_paths=[r"C:\Users\n\Downloads\report.pdf"])
    assert _detected_as_text(entity) == "Documents"


# ── both are on screen, apart ─────────────────────────────────────

def test_the_inspector_shows_them_as_separate_rows(view):
    view.set_category("Applications", [_entity()])
    view._show_detail_sidebar(_entity())
    panel = view._right_sidebar.detail_widget

    assert panel._detected_lbl.text() == "Portable Application"
    assert panel._rec_status_lbl.text() == "STRONG"
    assert "Recycle Bin" in panel._rec_text_lbl.text()
