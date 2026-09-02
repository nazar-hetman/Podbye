"""One count of "findings inside", and no button for a folder of projects.

Reported from a real screen: Projects showing 255.4 GB, a FINDINGS INSIDE
section reading "6 items · 8.7 GB", and a line under the button reading
"13 finding(s) inside are listed separately and will be kept (12.5 GB)". Two
numbers for one idea, fifteen pixels apart, with a natural question between
them: what is the rest of the 255 GB, and what does the button take?

The 255.4 GB was never the folder. ``E:/Work/Projects`` measures 271.7 GB on
disk; the headline is already the owned size, with the nested findings taken
out. What went wrong was smaller and entirely ours:

* the section listed nested findings within ``MAX_ITEM_DEPTH``, while the
  scope line counted every finding that survives at any depth;
* the section could also list a nested *bucket*, which stands for files inside
  the folder and goes with it — promising to keep something that would not be
  kept;
* summing the rows understates what survives for a nested chain: A keeps B,
  and B's row shows B's own size, with B's own children already subtracted —
  but those survive too.

So the section is restricted to exactly the findings that survive, and takes
its total from the same subtraction the scope line reads.

The second half is the question the numbers were really asking. Projects is a
``dev_workspace`` — Podbye's own classifier reclassifies "a folder that holds
several separate projects rather than being one", the type is ``review_only``
because "deleting the whole thing is rarely what the user wants", and the
assessment on screen says "It is not one thing to delete". It was still given
a full-width primary button offering to recycle 255 GB of source code.
"""
import pytest

import app.screens.findings_dashboard as fd
from app.themes.theme_manager import build_qss

GB = 1024 ** 3
WORKSPACE = "E:/Work/Projects"


def _entity(path, name, **kw):
    base = {"path": path, "name": name, "entity_type": "dev_project",
            "entity_type_label": "Development Project", "size_bytes": GB,
            "size": "1.0 GB", "file_count": 100, "folder_count": 5,
            "risk": "Review", "category": "Dev Artifacts",
            "actionability": "review_only", "children_sample": [],
            "ai_status": "none"}
    base.update(kw)
    return base


@pytest.fixture
def view(qapp):
    qapp.setStyleSheet(build_qss("forest"))
    made = []

    def build(entities, select):
        v = fd.CategoryDetailView()
        v._app_index_cache = {}
        v.resize(1500, 950)
        v.show()
        v.set_category(entities[0].get("category", "Dev Artifacts"), entities)
        for _ in range(8):
            qapp.processEvents()
        v.select_by_path(select)
        for _ in range(10):
            qapp.processEvents()
        made.append(v)
        return v

    yield build
    for v in made:
        v.deleteLater()
    qapp.processEvents()


def _workspace_set(kept=13):
    paths = [f"{WORKSPACE}/p{i}" for i in range(kept)]
    parent = _entity(WORKSPACE, "Projects", entity_type="dev_workspace",
                     entity_type_label="Development Workspace",
                     size_bytes=int(255.4 * GB), size="255.4 GB",
                     file_count=25536, folder_count=1763,
                     contained_bytes=int(12.5 * GB), contained_paths=paths)
    return [parent] + [_entity(p, f"project-{i}") for i, p in enumerate(paths)]


# ── one number for findings inside ────────────────────────────────

def test_the_section_counts_every_finding_that_survives(view):
    """It counted the ones within three path levels; the line below counted
    all of them."""
    v = view(_workspace_set(kept=13), WORKSPACE)
    meta = v._detail_widget._contents_meta.text()

    assert "13" in meta, meta


def test_the_section_and_the_scope_line_agree(view):
    """Chrome-shaped: a folder that can be recycled, holding one finding."""
    chrome = "C:/U/Chrome"
    entities = [
        _entity(chrome, "Chrome Data", entity_type="browser_profile",
                entity_type_label="Browser Profile/Data", category="Browser Data",
                actionability="recycle", size_bytes=int(5.4 * GB), size="5.4 GB",
                contained_bytes=int(2.0 * GB), contained_paths=[chrome + "/Cache"]),
        _entity(chrome + "/Cache", "Chrome cache", entity_type="browser_cache",
                category="Browser Data", actionability="recycle",
                size_bytes=int(1.4 * GB), size="1.4 GB"),
    ]
    v = view(entities, chrome)
    panel = v._detail_widget

    assert "1 items" in panel._contents_meta.text()
    assert "2.0 GB" in panel._contents_meta.text()
    assert "1 finding(s)" in panel._scope_lbl.text()
    assert "2.0 GB" in panel._scope_lbl.text()


def test_the_total_is_what_survives_not_the_sum_of_the_rows(view):
    """A nested chain: the row shows B's own size, with B's own children
    already taken out — and those survive too. 1.4 GB of rows, 2.0 GB kept."""
    chrome = "C:/U/Chrome"
    entities = [
        _entity(chrome, "Chrome Data", entity_type="browser_profile",
                category="Browser Data", actionability="recycle",
                size_bytes=int(5.4 * GB), contained_bytes=int(2.0 * GB),
                contained_paths=[chrome + "/Cache"]),
        _entity(chrome + "/Cache", "Chrome cache", entity_type="browser_cache",
                category="Browser Data", size_bytes=int(1.4 * GB)),
    ]
    v = view(entities, chrome)

    assert "2.0 GB" in v._detail_widget._contents_meta.text()
    assert "1.4 GB" not in v._detail_widget._contents_meta.text()


def test_a_nested_bucket_is_not_listed_as_kept():
    """It stands for files inside the folder and goes with it, so it is not
    one of the survivors."""
    from app.models.entity_contents import items_summary

    parent = _entity("C:/U/Work", "Work", entity_type="mixed_folder",
                     contained_paths=["C:/U/Work/proj"])
    project = _entity("C:/U/Work/proj", "proj")
    bucket = _entity("C:/U/Work", "Loose files", entity_type="loose_files",
                     removable_file_paths=["C:/U/Work/a.tmp"])
    bucket["path"] = "C:/U/Work/loose"

    summary = items_summary(parent, [parent, project, bucket],
                            restrict_to=["C:/U/Work/proj"])

    assert [r.label for r in summary.rows] == ["proj"]


def test_an_entity_with_nothing_nested_is_unchanged(view):
    """The restriction must not empty the section for everything else."""
    plain = _entity("C:/U/Thing", "Thing", entity_type="mixed_folder",
                    actionability="recycle")
    inner = _entity("C:/U/Thing/inner", "inner")
    v = view([plain, inner], "C:/U/Thing")

    rows = v._detail_widget._contents
    assert rows is not None and rows.rows, "the section vanished"


# ── a folder of projects is not one thing to delete ───────────────

def test_a_workspace_is_not_offered_for_removal(view):
    v = view(_workspace_set(), WORKSPACE)

    assert not v._detail_widget._btn_recycle.isVisibleTo(v)


def test_it_says_why_instead_of_just_missing(view):
    """A missing action with no explanation reads as a bug."""
    v = view(_workspace_set(), WORKSPACE)
    note = v._detail_widget._workspace_lbl

    assert note.isVisibleTo(v)
    assert "separate projects" in note.text()
    assert "Open a project inside" in note.text()


def test_a_project_inside_is_still_actionable(view):
    """The route the note points at has to exist. Each project is its own
    finding with its own button."""
    entities = _workspace_set()
    v = view(entities, entities[1]["path"])

    assert not v._detail_widget._workspace_lbl.isVisibleTo(v)


def test_an_ordinary_folder_keeps_its_button(view):
    """Only the workspace type is suppressed — review_only covers browser
    profiles, document folders and much else that stays removable."""
    chrome = "C:/U/Chrome"
    entities = [_entity(chrome, "Chrome Data", entity_type="browser_profile",
                        category="Browser Data", actionability="recycle")]
    v = view(entities, chrome)

    assert v._detail_widget._btn_recycle.isVisibleTo(v)
    assert not v._detail_widget._workspace_lbl.isVisibleTo(v)


def test_the_model_and_the_screen_agree_about_workspaces():
    """The screen suppresses what the classifier already calls review_only,
    for the reason recorded beside that list."""
    from app.models.smart_entity import actionability_for_type

    assert actionability_for_type("dev_workspace", "Review") == "review_only"
    assert actionability_for_type("dev_project", "Review") == "review_only"


def test_the_workspace_note_is_translated(qapp):
    from app.i18n import set_language, tr

    key = ("This folder holds several separate projects, so there is nothing "
           "here to remove as one piece. Open a project inside and decide "
           "about it on its own.")
    try:
        for language in ("Ukrainian", "German", "Polish", "Spanish", "French"):
            set_language(language)
            assert tr(key) != key, f"{language} falls back to English"
    finally:
        set_language("English")
