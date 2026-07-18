"""Loose-file buckets must report the folder the files actually live in.

Reported as dangerous, and reproduced on a real machine before the fix:
scanning the Desktop produced "Loose documents" (15 files) and "Loose media
files" (2 files) whose path was "C:/". The UI then presents Desktop files as
drive-root junk — and worse, one bucket merged files from unrelated folders, so
a single cleanup click would recycle files from all over the disk.
"""
import os

from app.services.entity_detector import detect_entities
from app.models.finding import Finding

MB = 1024 * 1024


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
    f += [_f(f"{root}/loose{i}.pdf", size=2 * MB, ext=".pdf", parent=root)
          for i in range(4)]
    f += [_f(f"{root}/img{i}.png", size=1 * MB, ext=".png", parent=root)
          for i in range(3)]
    return f


def test_loose_buckets_use_their_real_folder_not_the_scan_root():
    root = "C:/Work"
    entities = _detect(_folder_with_loose_files(root))

    loose = [e for e in entities if e.name.lower().startswith("loose")]
    assert loose, "no loose-file buckets produced — scenario no longer exercises pass 8"
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
        f"{[(e.name, e.path) for e in docs]}")


def test_files_genuinely_at_the_drive_root_may_use_it():
    """A real root file keeps the root as its path — that is not the bug."""
    findings = [
        _f("C:/", is_dir=True, parent=""),
        _f("C:/DumpStack.log", size=1 * MB, ext=".log", parent="C:/"),
    ]
    entities = _detect(findings)
    rooted = [e for e in entities
              if any(_norm(p) == "c:/dumpstack.log"
                     for p in (e.removable_file_paths or []))]
    if rooted:  # bucketing of a lone root file is allowed, but must stay at root
        assert _norm(rooted[0].path) in ("c:", "c:/")
