"""A repository is a project however deep it is kept.

Reported against a real E:/ scan: "Projects" showed 259.6 GiB and one item,
while the folder underneath it held seven separate repositories. Only the one
that happened to carry requirements.txt was recognised.

Two things were in the way, and the second only became visible once the first
was fixed:

* the scanner listed ".git" in _SKIP_DIRS, which skips *before* recording, so
  no .git finding was ever produced. The detector's VCS rule waits for one, so
  it had never fired at all - zero of 1,327 entities in a real all-drives scan
  cited it;
* the detector's Guard 4 suppresses generic directory entities below
  _MAX_GENERIC_DIR_DEPTH, and every one of those repositories sat one level
  past it.

A .git directory is not a generic folder name. It is the definitive statement
that its parent is a project, and how far down it sits is an accident of where
someone keeps their code.
"""
import os

import pytest

from app.models.entity_contents import child_entities
from app.models.smart_entity import _CATEGORY_BY_TYPE
from app.services.entity_detector import (
    _MAX_GENERIC_DIR_DEPTH, _WORKSPACE_MIN_PROJECTS, _retype_workspaces,
    detect_entities,
)
from app.services.scanner import ScanWorker


def _repo(root, *parts, marker=None):
    """A directory that is a git checkout, with a little content."""
    path = os.path.join(root, *parts)
    os.makedirs(os.path.join(path, ".git", "objects"), exist_ok=True)
    with open(os.path.join(path, ".git", "objects", "pack"), "wb") as fh:
        fh.write(b"x" * 4096)
    with open(os.path.join(path, "main.py"), "wb") as fh:
        fh.write(b"y" * 2048)
    if marker:
        with open(os.path.join(path, marker), "wb") as fh:
            fh.write(b"z" * 128)
    return path


def _scan(root):
    """Run the real scanner synchronously - run() executes in this thread, so
    the signal delivery is direct and no event loop is needed."""
    findings = []
    worker = ScanWorker(str(root))
    worker.batch_ready.connect(findings.extend)
    worker.run()
    return findings


def _detect(root, findings):
    return [e.to_dict() for e in
            detect_entities(findings, str(root), log_fn=lambda m: None)]


def _by_path(entities):
    return {e.get("path", "").replace("\\", "/").lower(): e for e in entities}


# -- the scanner has to hand the marker over ----------------------

def test_a_vcs_directory_is_recorded(tmp_path):
    """It used to be skipped before recording, so the rule below could never
    fire on it."""
    _repo(tmp_path, "solo")
    names = [f.name.lower() for f in _scan(tmp_path)]
    assert ".git" in names


def test_a_vcs_directory_is_still_not_descended_into(tmp_path):
    """One finding that names the parent a project, without the object store
    behind it - that is thousands of files nobody triages."""
    _repo(tmp_path, "solo")
    paths = [f.path.replace("\\", "/").lower() for f in _scan(tmp_path)]
    inside = [p for p in paths if "/.git/" in p]
    assert not inside, f"descended into the object store: {inside[:3]}"


def test_the_recycle_bin_is_still_skipped_outright(tmp_path):
    """_SKIP_DIRS keeps its other members: those are skipped *and* unrecorded."""
    os.makedirs(tmp_path / "$RECYCLE.BIN" / "S-1-5-21", exist_ok=True)
    (tmp_path / "$RECYCLE.BIN" / "S-1-5-21" / "x.bin").write_bytes(b"x" * 16)
    paths = [f.path.replace("\\", "/").lower() for f in _scan(tmp_path)]
    assert not [p for p in paths if "$recycle.bin" in p]


# -- depth is not a reason to disbelieve a repository -------------

def test_a_repository_below_the_generic_depth_guard_is_a_project(tmp_path):
    """The exact shape that was reported: the checkout sits deeper than
    _MAX_GENERIC_DIR_DEPTH allows a generic directory entity to be."""
    deep = ["lvl%d" % i for i in range(_MAX_GENERIC_DIR_DEPTH + 1)]
    path = _repo(tmp_path, *deep, "buried-repo")
    rel_depth = (path.replace("\\", "/").lower().count("/")
                 - str(tmp_path).replace("\\", "/").lower().rstrip("/").count("/"))
    assert rel_depth > _MAX_GENERIC_DIR_DEPTH, "the fixture is not deep enough"

    found = _by_path(_detect(tmp_path, _scan(tmp_path)))
    entity = found.get(path.replace("\\", "/").lower())
    assert entity is not None, "the buried repository produced no entity"
    assert entity.get("entity_type") == "dev_project"


def test_a_repository_needs_no_marker_file(tmp_path):
    """Six of the seven reported repositories had no package.json,
    requirements.txt or equivalent - only .git."""
    path = _repo(tmp_path, "workspace", "no-marker-repo")
    found = _by_path(_detect(tmp_path, _scan(tmp_path)))
    assert found.get(path.replace("\\", "/").lower(), {}).get("entity_type") == "dev_project"


def test_the_vcs_directory_does_not_become_an_entity_of_its_own(tmp_path):
    """The entity is the parent project; a bare .git row would be noise."""
    _repo(tmp_path, "workspace", "repo-a")
    entities = _detect(tmp_path, _scan(tmp_path))
    stray = [e.get("path") for e in entities
             if e.get("path", "").replace("\\", "/").lower().endswith(("/.git", "/.hg", "/.svn"))]
    assert not stray, f"a bare VCS directory reached the findings list: {stray}"


# -- the label and the drill-down have to agree -------------------
#
# Tested directly on _retype_workspaces rather than through a scan: getting a
# temporary tree to land on a workspace *candidate* type depends on what else
# happens to be in the folder, and a fixture that has to guess at that tests
# the classifier's mood rather than the invariant.


class _Ent:
    """The attributes _retype_workspaces reads and writes."""

    def __init__(self, path, entity_type):
        self.path = path
        self.entity_type = entity_type
        self.reason = ""
        self.risk = "Review"
        self.removable_file_paths = []

    def as_dict(self, category="Dev Artifacts"):
        return {"path": self.path, "entity_type": self.entity_type,
                "category": category, "size_bytes": 1024, "file_count": 1}


class _Ctx:
    """Enough context for the pass: a root, and what sits directly in a dir."""

    def __init__(self, root_norm, direct=None):
        self.root_norm = root_norm
        self._direct = direct or {}

    def gather_direct(self, norm):
        return self._direct.get(norm, [])


def _retype(entities, root_norm="e:", direct=None):
    _retype_workspaces(_Ctx(root_norm, direct), entities, lambda m: None)


def test_a_folder_of_repositories_is_labelled_a_workspace():
    holder = _Ent("e:/code", "mixed_folder")
    repos = [_Ent(f"e:/code/repo-{n}", "dev_project") for n in "abc"]
    _retype([holder] + repos)
    assert holder.entity_type == "dev_workspace"


def test_the_workspace_label_and_its_items_agree():
    """_retype_workspaces says it uses "the same 'lives directly inside'
    relation the inspector's ITEMS list uses, so the label and the list can
    never disagree". Before this fix they did: on a real scan the label was
    justified by four projects while ITEMS showed one, because six of the
    seven repositories were never entities at all.

    Scoped to repositories sitting within MAX_ITEM_DEPTH of the folder, which
    is the shape this fix produces. Deliberately not asserted in general:
    MAX_ITEM_DEPTH and the inspector's category scoping are unchanged, so a
    workspace whose projects are buried deeper, or split across categories,
    can still show fewer items than its label counted.
    """
    holder = _Ent("e:/code", "mixed_folder")
    repos = [_Ent(f"e:/code/repo-{n}", "dev_project") for n in "abc"]
    _retype([holder] + repos)
    assert holder.entity_type == "dev_workspace", "not labelled a workspace"

    everything = [e.as_dict() for e in [holder] + repos]
    items = child_entities(holder.as_dict(), everything)
    projects = [i for i in items if i.get("entity_type") == "dev_project"]
    assert len(projects) >= _WORKSPACE_MIN_PROJECTS, (
        f"labelled a workspace on {len(repos)} projects but ITEMS shows "
        f"{len(projects)}: {[i.get('path') for i in items]}")


def test_every_repository_is_reachable_from_the_workspace():
    """The reported symptom: six of seven siblings were absent from the
    drill-down while their bytes sat inside its total."""
    holder = _Ent("e:/code", "mixed_folder")
    names = ["repo-a", "repo-b", "repo-c", "repo-d"]
    repos = [_Ent("e:/code/" + n, "dev_project") for n in names]
    _retype([holder] + repos)
    items = child_entities(holder.as_dict(),
                           [e.as_dict() for e in [holder] + repos])
    shown = {i["path"].rsplit("/", 1)[-1] for i in items}
    assert set(names) <= shown, f"missing from ITEMS: {set(names) - shown}"


def test_a_project_of_its_own_is_not_relabelled():
    """"client-odm-dev vendors two source checkouts and would otherwise be
    called a workspace, when it is a project that happens to contain them."
    Its own marker files are what say so."""
    class _F:
        def __init__(self, name):
            self.name = name
            self.is_dir = False

    holder = _Ent("e:/code", "dev_project")
    repos = [_Ent(f"e:/code/vendor-{n}", "dev_project") for n in "ab"]
    _retype([holder] + repos, direct={"e:/code": [_F("package.json")]})
    assert holder.entity_type == "dev_project"


def test_a_single_repository_does_not_make_its_parent_a_workspace():
    """"Two is a collection. One project inside a folder is a project in a
    folder." - _WORKSPACE_MIN_PROJECTS."""
    holder = _Ent("e:/code", "mixed_folder")
    _retype([holder, _Ent("e:/code/only-repo", "dev_project")])
    assert holder.entity_type == "mixed_folder"


def test_the_scan_root_is_never_a_workspace():
    holder = _Ent("e:", "mixed_folder")
    repos = [_Ent(f"e:/repo-{n}", "dev_project") for n in "abc"]
    _retype([holder] + repos, root_norm="e:")
    assert holder.entity_type == "mixed_folder"
