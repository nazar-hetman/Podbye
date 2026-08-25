"""A folder of projects is not a project.

Measured on a real E:/ scan before this rule existed:

* ``E:/My Projects`` came back as one **Development Project** of 261.7 GB
  while holding four separate projects and five colmap databases. The cause
  is ``_WEAK_NAME_FOLDER_TYPES``, which matches the word "project" in a name
  — a folder named for the plural was classified as the singular.
* ``_src`` had the same shape from the other direction: a 2.4 GB
  ``mixed_folder`` whose four checkouts hold 30 GB between them.

Both rows invited a person to think about 261 GB as one decision. It never
was. The rule uses the same "lives directly inside" relation the inspector's
ITEMS list uses, so the label and the list cannot disagree.
"""
import os

import pytest

from app.models.finding import Finding
from app.services.entity_detector import detect_entities


def _f(path, size=0, is_dir=False):
    path = path.replace("/", os.sep)
    return Finding(path=path, name=os.path.basename(path), is_dir=is_dir,
                   size_bytes=size, extension=os.path.splitext(path)[1],
                   modified=0, accessed=0, parent=os.path.dirname(path))


def _project(root, name, size):
    """A folder anything would agree is one project: source, git, a marker."""
    base = f"{root}/{name}"
    return [
        _f(base, is_dir=True),
        _f(f"{base}/.git", is_dir=True),
        _f(f"{base}/.git/HEAD", 40),
        _f(f"{base}/src", is_dir=True),
        _f(f"{base}/src/main.py", size),
        _f(f"{base}/README.md", 900),
    ]


def _detect(findings, root):
    return detect_entities(findings, root.replace("/", os.sep),
                           log_fn=lambda _m: None)


def _by_name(entities, name):
    return next((e for e in entities if e.name.split(" ")[0] == name
                 or e.name == name), None)


# ── the case from the scan ────────────────────────────────────────

WORKSPACE = "E:/Projects"


@pytest.fixture
def workspace():
    """E:/Projects holding Alpha and Beta, plus scratch of its own.

    The loose files matter: a folder whose every byte already belongs to a
    project inside it is not emitted at all, and that is right — the projects
    are the findings. What needs a name is the container that has odds and
    ends of its own, which is what the real one looked like.
    """
    findings = [_f("E:/", is_dir=True), _f(WORKSPACE, is_dir=True),
                _f(f"{WORKSPACE}/scratch.py", 300_000),
                _f(f"{WORKSPACE}/notes.md", 200_000),
                _f(f"{WORKSPACE}/env.json", 100_000)]
    findings += _project(WORKSPACE, "Alpha", 30_000_000)
    findings += _project(WORKSPACE, "Beta", 20_000_000)
    return _detect(findings, "E:/")


def test_a_folder_of_projects_is_a_workspace(workspace):
    projects = _by_name(workspace, "Projects")
    assert projects is not None, "the container has to exist to be labelled"
    assert projects.entity_type == "dev_workspace"


def test_the_projects_inside_survive_as_their_own_findings(workspace):
    """A workspace does not swallow what it holds. Each project stays a row,
    because each is a decision someone might make on a different day."""
    names = {e.name for e in workspace}
    assert "Alpha" in names and "Beta" in names


def test_a_workspace_is_never_offered_for_deletion(workspace):
    projects = _by_name(workspace, "Projects")
    assert projects.actionability == "review_only"


def test_the_label_and_the_reason_agree(workspace):
    projects = _by_name(workspace, "Projects")
    assert projects.category == "Dev Artifacts"
    assert "several separate projects" in str(projects.reason)


# ── what it must not touch ────────────────────────────────────────

def test_one_project_inside_a_folder_is_not_a_workspace():
    """Two is a collection. One project in a folder is a project in a folder."""
    findings = [_f("E:/", is_dir=True), _f("E:/Holder", is_dir=True),
                _f("E:/Holder/scratch.py", 300_000),
                _f("E:/Holder/notes.md", 200_000)]
    findings += _project("E:/Holder", "Alpha", 30_000_000)
    entities = _detect(findings, "E:/")
    holder = _by_name(entities, "Holder")
    assert holder is None or holder.entity_type != "dev_workspace"


def test_a_project_that_vendors_source_stays_a_project():
    """irizi-odm-dev, from the real scan: 3.4 GB with two source checkouts
    inside it. It is a project that vendors dependencies, not a place where
    projects live, and its own markers are what say so."""
    root = "E:/dev/irizi-odm-dev"
    findings = [
        _f("E:/dev", is_dir=True), _f(root, is_dir=True),
        _f(f"{root}/requirements.txt", 4_000),  # its own project marker
        _f(f"{root}/README.md", 2_000),
        _f(f"{root}/opendm", is_dir=True),
        _f(f"{root}/opendm/config.py", 50_000_000),
    ]
    findings += _project(root, "ODM-source", 20_000_000)
    findings += _project(root, "pypopsift-source", 10_000_000)
    entities = _detect(findings, "E:/dev")
    project = _by_name(entities, "irizi-odm-dev")
    assert project is not None
    assert project.entity_type != "dev_workspace"


def test_a_package_cache_full_of_build_output_is_not_a_workspace():
    """.conda-pkgs holds ten entities on the real drive — eight of them build
    artifacts. Generated files are not projects, however many there are."""
    root = "E:/dev/.conda-pkgs"
    findings = [_f("E:/dev", is_dir=True), _f(root, is_dir=True)]
    for name in ("numpy-2.4.3", "cgal-5.6.1", "libopencv-4.13"):
        findings += [
            _f(f"{root}/{name}", is_dir=True),
            _f(f"{root}/{name}/Lib", is_dir=True),
            _f(f"{root}/{name}/Lib/site-packages", is_dir=True),
            _f(f"{root}/{name}/Lib/site-packages/mod.pyd", 30_000_000),
        ]
    entities = _detect(findings, "E:/dev")
    assert not any(e.entity_type == "dev_workspace" for e in entities)


def test_a_photo_folder_with_projects_below_it_is_still_photos():
    """Ivankiv060626-test is 52 GB of aerial imagery. Whatever sits inside it,
    the folder is not a development anything."""
    root = "E:/dev/survey"
    findings = [_f("E:/dev", is_dir=True), _f(root, is_dir=True)]
    findings += [_f(f"{root}/img{i:04d}.tif", 40_000_000) for i in range(60)]
    findings += _project(root, "Alpha", 1_000)
    findings += _project(root, "Beta", 1_000)
    entities = _detect(findings, "E:/dev")
    survey = _by_name(entities, "survey")
    if survey is not None:
        assert survey.entity_type != "dev_workspace"


def test_a_drive_root_is_never_a_workspace():
    """The rule that keeps every whole-drive misclassification out."""
    findings = [_f("E:/", is_dir=True)]
    findings += _project("E:", "Alpha", 30_000_000)
    findings += _project("E:", "Beta", 20_000_000)
    entities = _detect(findings, "E:/")
    for entity in entities:
        norm = entity.path.replace("\\", "/").rstrip("/").lower()
        assert not (norm in ("e:", "e:/")
                    and entity.entity_type == "dev_workspace")


# ── the inspector reads it the same way ───────────────────────────

def test_the_workspaces_items_are_the_projects_it_holds(workspace):
    """One relation, two consumers: the label says "several projects" and the
    ITEMS list has to show exactly those."""
    from app.models.entity_contents import child_entities
    everything = [e.to_dict() for e in workspace]
    projects = _by_name(workspace, "Projects").to_dict()
    names = {e["name"] for e in child_entities(projects, everything)}
    assert {"Alpha", "Beta"} <= names
