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
