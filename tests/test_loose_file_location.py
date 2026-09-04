"""Loose-file buckets must report the folder the files actually live in.

Reported as dangerous, and reproduced on a real machine before the fix:
scanning the Desktop produced "Loose documents" (15 files) and "Loose media
files" (2 files) whose path was "C:/". The UI then presents Desktop files as
drive-root junk — and worse, one bucket merged files from unrelated folders, so
a single cleanup click would recycle files from all over the disk.
"""
import os

import pytest

from app.services import app_presence as ap
from app.services import entity_detector as ed
from app.services.entity_detector import (
    STANDALONE_LOOSE_FILE_BYTES,
    detect_entities,
)
from app.models.finding import Finding


@pytest.fixture(autouse=True)
def no_installed_apps(monkeypatch):
    """Judge these folders on their contents, not on what this machine has.

    Two independent lookups ask the machine about a folder name, and both had
    to be closed. Pass 2 matches a scanned directory against the installed
    programs registry by install path. The heterogeneous-root exploder then
    asks app_presence whether a drive-root folder *names* a present
    application and, if so, leaves it whole — and app_presence answers from
    the registry, the Program Files listing and the Start Menu, cached in a
    module global.

    A GitHub runner knows something called "Work", so C:/Work was left intact
    as one application entity holding all 23 files, the Containment Rule
    sealed it, and pass 8 never saw the loose files. The same test passed on
    every developer machine without that name. Stubbing only the registry was
    not enough: it is one of three strong sources, and the cache outlives the
    patch. What is under test is where a loose bucket puts itself, which must
    not depend on the software installed on the machine running it.
    """
    monkeypatch.setattr(ed, "_get_installed_programs", lambda *a, **k: {})
    monkeypatch.setattr(
        ap, "evidence",
        lambda force_refresh=False: {label: set() for label, _fn in ap._SOURCES})
    ap.reset_cache()
    yield
    ap.reset_cache()

MB = 1024 * 1024

# Small enough that pass 8 groups these files instead of giving each one its
# own finding. Derived from the threshold rather than written as a number, so
# raising the threshold cannot quietly move the fixture out of the pass it
# exists to test — which is exactly what happened when the threshold arrived.
BUCKETABLE = STANDALONE_LOOSE_FILE_BYTES // 8


def _f(path, is_dir=False, size=0, ext="", parent=""):
    return Finding(path=path, name=os.path.basename(path), is_dir=is_dir,
                   size_bytes=size, extension=ext, modified=1, accessed=1,
                   parent=parent or os.path.dirname(path).replace("\\", "/"))


def _detect(findings, root="C:/"):
    return detect_entities(findings, root, log_fn=lambda _m: None)


def _norm(p):
    return p.replace("\\", "/").rstrip("/").lower()


def _folder_with_loose_files(root: str):
    """A content-diverse folder holding loose documents and images.

    The subfolders make the root heterogeneous, which is what lets its loose
    files fall through to the loose-file bucketer — the same shape as the real
    Desktop that produced the bug.
    """
    f = [_f(root, is_dir=True, parent="C:/")]
    for sub, ext in (("code", ".py"), ("vids", ".mp4"),
                     ("docs", ".docx"), ("bin", ".exe")):
        f.append(_f(f"{root}/{sub}", is_dir=True, parent=root))
        f += [_f(f"{root}/{sub}/f{j}{ext}", size=5 * MB, ext=ext,
                 parent=f"{root}/{sub}") for j in range(4)]
    # Deliberately named nothing like "loose": the bucket has to be recognised
    # by what it holds, not by a word the fixture put in the filename.
    f += [_f(f"{root}/report{i}.pdf", size=BUCKETABLE, ext=".pdf", parent=root)
          for i in range(4)]
    f += [_f(f"{root}/photo{i}.png", size=BUCKETABLE, ext=".png", parent=root)
          for i in range(3)]
    return f


def _loose_buckets(entities, root):
    """The pass-8 buckets: several files that live directly in `root`.

    Identified by their contents. Selecting them by name (``startswith
    ("loose")``) matched the fixture's own ``loose0.pdf`` files, so the
    "did pass 8 run at all" guard passed while pass 8 was never reached.
    """
    found = []
    for e in entities:
        paths = e.removable_file_paths or []
        if len(paths) > 1 and all(
                _norm(os.path.dirname(p)) == _norm(root) for p in paths):
            found.append(e)
    return found


def _diagnostic(entities, findings, root):
    """Evidence for a failure that does not reproduce locally.

    This scenario passes on every local configuration tried — including a venv
    matching CI's exact PySide6/pytest/psutil versions, and the whole
    alphabetical prefix of the suite in one process — but fails on GitHub
    runners, where C:/Archive produces a bucket and C:/Work does not. The
    assertion therefore has to carry its own evidence out of CI: every entity
    the same call produced, and the entity each loose file ended up inside,
    including the case where it was absorbed rather than bucketed.
    """
    out = ["", "entities from this detect_entities call:"]
    for e in entities:
        out.append("  name=%-32r type=%-20s path=%-24r files=%s removable=%d"
                   % (e.name, getattr(e, "entity_type", "?"), e.path,
                      e.file_count, len(e.removable_file_paths or [])))

    own = [f.path for f in findings
           if not f.is_dir and _norm(os.path.dirname(f.path)) == _norm(root)]
    out += ["", "where each file directly in %s ended up:" % root]
    for p in own:
        leaf = os.path.basename(p)
        owners = [e for e in entities if p in (e.removable_file_paths or [])]
        if owners:
            for e in owners:
                out.append("  %-16s -> listed by %r (%s) at %r"
                           % (leaf, e.name, getattr(e, "entity_type", "?"), e.path))
            continue
        itself = [e for e in entities if _norm(e.path) == _norm(p)]
        if itself:
            out.append("  %-16s -> emitted AS ITSELF: %r (%s)"
                       % (leaf, itself[0].name,
                          getattr(itself[0], "entity_type", "?")))
            continue
        # Absorbed: no entity lists it, but one contains it by path.
        under = [e for e in entities
                 if _norm(p).startswith(_norm(e.path) + "/")]
        if under:
            out.append("  %-16s -> absorbed under %s"
                       % (leaf, ", ".join("%r (%s)"
                                          % (e.name, getattr(e, "entity_type", "?"))
                                          for e in under)))
        else:
            out.append("  %-16s -> NOT FOUND in any entity" % leaf)
    return "\n".join(out)


def test_loose_buckets_use_their_real_folder_not_the_scan_root():
    root = "C:/Work"
    findings = _folder_with_loose_files(root)
    entities = _detect(findings)

    loose = _loose_buckets(entities, root)
    assert loose, ("no loose-file buckets produced — scenario no longer "
                   "exercises pass 8" + _diagnostic(entities, findings, root))
    for e in loose:
        assert _norm(e.path) != "c:", (
            f"{e.name!r} claims the drive root; the files live in {root}")
        assert _norm(e.path).startswith(_norm(root)), (
            f"{e.name!r} path {e.path!r} is not the folder its files live in")


def test_every_bucket_only_contains_files_from_its_own_folder():
    """The safety invariant: cleaning a bucket must never reach outside it."""
    findings = _folder_with_loose_files("C:/Work") + \
        _folder_with_loose_files("C:/Archive")
    for e in _detect(findings):
        base = _norm(e.path)
        for p in (e.removable_file_paths or []):
            assert _norm(p).startswith(base), (
                f"{e.name!r} (at {e.path}) would recycle {p}, outside its folder")


def test_unrelated_folders_are_not_merged_into_one_bucket():
    findings = _folder_with_loose_files("C:/Work") + \
        _folder_with_loose_files("C:/Archive")
    entities = _detect(findings)

    docs = [e for e in entities
            if any(p.lower().endswith(".pdf")
                   for p in (e.removable_file_paths or []))]
    folders = {_norm(e.path) for e in docs}
    assert len(folders) >= 2, (
        "PDFs from two unrelated folders collapsed into one bucket: "
        f"{[(e.name, e.path) for e in docs]}"
        # On CI this reports only the C:/Archive bucket, so the question is
        # what became of C:/Work's files in the same call.
        + _diagnostic(entities, findings, "C:/Work"))


def test_files_genuinely_at_the_drive_root_may_use_it():
    """A real root file stays at the root — that is not the bug.

    A bucket of one is now the file rather than a bucket, so either answer is
    correct here; what must never happen is a path outside the folder the file
    is in.
    """
    findings = [
        _f("C:/", is_dir=True, parent=""),
        _f("C:/DumpStack.log", size=1 * MB, ext=".log", parent="C:/"),
    ]
    entities = _detect(findings)
    rooted = [e for e in entities
              if any(_norm(p) == "c:/dumpstack.log"
                     for p in (e.removable_file_paths or []))]
    if rooted:
        assert _norm(rooted[0].path) in ("c:", "c:/", "c:/dumpstack.log")


def test_a_bucket_of_one_file_is_that_file():
    """"Loose AI model files in C:" stood for exactly one 1 KB file.

    It named neither the file nor anything else actionable: the row said C:/,
    the inspector said C:/, and the Files tab does not open itself for a
    single file. Reported as "it does not show what is proposed to delete".
    """
    findings = [
        _f("C:/", is_dir=True, parent=""),
        _f("C:/AMTAG.BIN", size=1024, ext=".bin", parent="C:/"),
    ]
    entities = _detect(findings)
    subject = [e for e in entities
               if any(_norm(p) == "c:/amtag.bin"
                      for p in (e.removable_file_paths or []))]
    if subject:
        assert subject[0].name == "AMTAG.BIN"
        assert _norm(subject[0].path) == "c:/amtag.bin"


def test_a_kilobyte_of_bin_is_not_a_model():
    """.bin is a model extension the way .dat is a spreadsheet extension."""
    from app.services.entity_detector import _is_model_file
    tiny = _f("C:/AMTAG.BIN", size=1024, ext=".bin", parent="C:/")
    assert _is_model_file(tiny) is False


def test_python_files_under_conda_are_not_models():
    """Every file under miniconda3 is categorised "AI / ML" by path.

    Pass 8 trusted that category, so nine copies of setuptools' two-file
    tests/config/downloads fixture became nine "Loose AI model files" rows —
    ten of the nineteen rows in the AI/ML category, with the drive-root one.
    """
    from app.services.entity_detector import _is_model_file
    f = _f("C:/Users/n/miniconda3/Lib/site-packages/setuptools/tests/"
           "config/downloads/preload.py", size=2048, ext=".py")
    assert f.category == "AI / ML", "fixture no longer exercises the leak"
    assert _is_model_file(f) is False


def test_real_weights_are_still_models():
    from app.services.entity_detector import _is_model_file
    assert _is_model_file(
        _f("D:/models/llama-3.gguf", size=4 * 1024 * MB, ext=".gguf")) is True
    assert _is_model_file(
        _f("C:/Users/n/.ollama/models/blobs/sha256-abc", size=4096 * MB)) is True
    assert _is_model_file(
        _f("D:/checkpoints/weights.bin", size=800 * MB, ext=".bin")) is True
