"""A finding's size must equal what recycling it actually removes.

Reported from a real screen: irizi-unex-client showing 3.89 GB with a CONTENTS
breakdown adding up to 5.58 GB. Measured, the folder holds 5.58 GB and nothing
was being preserved — ``contained_paths`` was empty — so recycling that row
took 5.58 GB while the row promised 3.89 GB. The gap was exactly ``.git``.

The cause is in the scanner, and it was a deliberate decision working as
designed in one direction and not the other. ``.git`` is *recorded* so the
detector can recognise a project, and *not descended into* because its object
store is thousands of files nobody triages. Not descending kept those files
out of the finding list — correct — and also kept their bytes out of the
parent's total, which was not. ``_build_subtree_stats`` reads a directory's
recorded children; ``.git`` has none, so it contributed zero.

Deletion never knew about any of this. ``expand_targets`` returns ``[root]``
when nothing is excluded, and ``SHFileOperationW`` takes the subtree whole,
``.git`` included.

It was systematic across the scan, and always in the dangerous direction —
more removed than promised:

    irizi-unex-client   row 3.89 GB   .git 1.69 GB   +43%
    maplibre-native-qt  row 0.89 GB   .git 0.45 GB   +50%
    llama.cpp           row 2.21 GB   .git 0.39 GB   +18%

The fix keeps the classification behaviour exactly as it was — one finding for
``.git``, no descent, no object files in the list — and measures the subtree so
its bytes reach the parent. Two fields on Finding carry it.

This file holds the invariant rather than the mechanism:

    **row size == deletion scope == what is on the disk.**
"""
import os

import pytest

from app.models.deletion_scope import excluded_paths, expand_targets, own_bytes
from app.models.finding import Finding
from app.services.entity_detector import detect_entities
from app.services.scanner import _VCS_DIRS

MB = 1024 ** 2


# ── a tree, and a scan of it that behaves like ScanWorker ─────────

def _write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


def _git_project(root, git_mb=6, src_mb=2, name="app"):
    """A project whose .git is a real share of it."""
    _write(os.path.join(root, "README.md"), 4096)
    _write(os.path.join(root, "package.json"), 2048)
    _write(os.path.join(root, "src", name + ".js"), src_mb * MB)
    # An object store: many files, which is why the scan does not walk it.
    for i in range(12):
        _write(os.path.join(root, ".git", "objects", "%02x" % i, "blob"),
               (git_mb * MB) // 12)
    _write(os.path.join(root, ".git", "HEAD"), 41)
    return root


def _scan(root, halt_after=None):
    """Walk the way ScanWorker does: record a VCS dir, never descend it,
    measure it. Mirrors app/services/scanner.py so this file tests the
    detector's half of the contract without starting a QThread."""
    out = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            f = Finding(path=entry.path, name=entry.name, is_dir=is_dir,
                        size_bytes=0 if is_dir else st.st_size,
                        extension="" if is_dir else os.path.splitext(entry.name)[1],
                        modified=st.st_mtime, accessed=st.st_atime,
                        parent=current)
            if is_dir and entry.name.lower() in _VCS_DIRS:
                f.undescended_bytes, f.undescended_files = _measure(entry.path)
                out.append(f)
                continue          # recorded, not descended
            out.append(f)
            if is_dir:
                stack.append(entry.path)
    return out


def _measure(path):
    total = files = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                else:
                    total += entry.stat(follow_symlinks=False).st_size
                    files += 1
            except OSError:
                pass
    return total, files


def _physical(root):
    total = files = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
                files += 1
            except OSError:
                pass
    return total, files


def _subtree(findings, root, path):
    """What _build_subtree_stats makes of *path* — the primitive every
    folder-backed entity's size comes from."""
    from app.services.entity_detector import _DetectionContext

    ctx = _DetectionContext(findings, root, lambda *_a: None, None, None)
    return ctx.subtree(path.replace("\\", "/").lower())


@pytest.fixture
def project(tmp_path):
    root = str(tmp_path / "work")
    return _git_project(os.path.join(root, "app")), root


# ── the invariant ─────────────────────────────────────────────────

def test_the_measured_subtree_equals_the_disk(project):
    """The primitive. Before the fix this was short by the whole .git."""
    path, root = project
    size, files, _folders, _m, _a = _subtree(_scan(root), root, path)
    disk_bytes, disk_files = _physical(path)

    assert size == disk_bytes
    assert files == disk_files


def test_the_git_bytes_are_the_difference(project):
    """Stated explicitly so a future change that drops them is legible."""
    path, root = project
    findings = _scan(root)
    with_git, _f, _fo, _m, _a = _subtree(findings, root, path)

    for f in findings:
        f.undescended_bytes = f.undescended_files = 0
    without, _f2, _fo2, _m2, _a2 = _subtree(findings, root, path)

    git_bytes, _ = _physical(os.path.join(path, ".git"))
    assert with_git - without == git_bytes
    assert git_bytes > 5 * MB


def test_the_row_equals_what_recycling_it_takes(project):
    """The whole point: no exclusions, so the button takes the folder whole."""
    path, root = project
    ents = detect_entities(_scan(root), root, log_fn=lambda *_a: None)
    row = next(e for e in ents
               if e.path.replace("\\", "/").lower() == path.replace("\\", "/").lower())
    disk_bytes, _ = _physical(path)

    assert excluded_paths(row.to_dict()) == []
    assert expand_targets(row.path, []) == [row.path]
    assert own_bytes(row.to_dict()) == disk_bytes


def test_the_file_count_includes_the_object_store(project):
    path, root = project
    ents = detect_entities(_scan(root), root, log_fn=lambda *_a: None)
    row = next(e for e in ents
               if e.path.replace("\\", "/").lower() == path.replace("\\", "/").lower())
    _bytes, disk_files = _physical(path)

    assert row.file_count == disk_files


# ── classification behaviour is unchanged ─────────────────────────

def test_the_object_store_is_still_not_walked(project):
    """The reason .git is skipped in the first place. Measuring it must not
    put thousands of object files back into the finding list."""
    path, root = project
    findings = _scan(root)

    inside_git = [f for f in findings
                  if "/.git/" in f.path.replace("\\", "/").lower()]
    assert inside_git == []


def test_the_git_finding_itself_still_exists(project):
    """It is what tells the detector the parent is a project."""
    path, root = project
    findings = _scan(root)

    git = [f for f in findings
           if f.path.replace("\\", "/").lower().endswith("/.git")]
    assert len(git) == 1
    assert git[0].is_dir
    assert git[0].undescended_files > 0


def test_the_parent_is_still_recognised_as_a_project(project):
    path, root = project
    ents = detect_entities(_scan(root), root, log_fn=lambda *_a: None)
    row = next(e for e in ents
               if e.path.replace("\\", "/").lower() == path.replace("\\", "/").lower())

    assert row.entity_type == "dev_project"


# ── and nothing else moved ────────────────────────────────────────

def test_a_project_without_a_repository_is_unchanged(tmp_path):
    """The fields default to zero, so a tree with nothing undescended must
    measure exactly as it always did."""
    root = str(tmp_path / "plain")
    path = os.path.join(root, "app")
    _write(os.path.join(path, "README.md"), 4096)
    _write(os.path.join(path, "src", "app.js"), 3 * MB)

    size, files, _fo, _m, _a = _subtree(_scan(root), root, path)
    disk_bytes, disk_files = _physical(path)

    assert (size, files) == (disk_bytes, disk_files)


def test_the_fields_default_to_zero():
    """A Finding built anywhere else — and every one restored from a session
    saved before this existed — contributes nothing extra."""
    f = Finding(path="C:/x", name="x", is_dir=True, size_bytes=0,
                extension="", modified=0, accessed=0, parent="C:/")

    assert f.undescended_bytes == 0
    assert f.undescended_files == 0


def test_nested_repositories_are_each_counted_once(tmp_path):
    """A workspace of repositories: every .git contributes to its own project
    and the total still equals the disk."""
    root = str(tmp_path / "many")
    for name in ("alpha", "beta", "gamma"):
        _git_project(os.path.join(root, name), git_mb=3, src_mb=1, name=name)

    findings = _scan(root)
    for name in ("alpha", "beta", "gamma"):
        path = os.path.join(root, name)
        size, files, _fo, _m, _a = _subtree(findings, root, path)
        disk_bytes, disk_files = _physical(path)
        assert (size, files) == (disk_bytes, disk_files), name

    whole, _f, _fo, _m, _a = _subtree(findings, root, root)
    assert whole == _physical(root)[0]


def test_the_measure_walk_is_the_scanners_own(project):
    """This file mirrors ScanWorker rather than driving a QThread, so the two
    must not drift apart. Asserted on the source: the real walk sets both
    fields on the VCS finding it records."""
    import inspect

    from app.services import scanner

    src = inspect.getsource(scanner.ScanWorker)
    assert "undescended_bytes" in src
    assert "undescended_files" in src
    assert "_measure_subtree" in src


def test_the_scanners_measure_stops_when_halted(tmp_path):
    """A cancelled scan must not sit inside a large object store."""
    from app.services.scanner import ScanWorker

    root = _git_project(str(tmp_path / "halted"), git_mb=6)
    worker = ScanWorker.__new__(ScanWorker)
    worker._halt = True

    assert worker._measure_subtree(os.path.join(root, ".git")) == (0, 0)


def test_an_unreadable_store_measures_what_it_can(tmp_path, monkeypatch):
    """An approximate total beats none — the alternative is the under-report
    this exists to fix."""
    from app.services.scanner import ScanWorker

    root = _git_project(str(tmp_path / "denied"), git_mb=6)
    worker = ScanWorker.__new__(ScanWorker)
    worker._halt = False
    monkeypatch.setattr(os, "scandir",
                        lambda p: (_ for _ in ()).throw(OSError("denied")))

    assert worker._measure_subtree(os.path.join(root, ".git")) == (0, 0)
