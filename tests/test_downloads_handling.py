"""Downloads is a collection of individual items — never one merged blob.

Before this, a single extracted download shattered into meaningless fragments:
one Qt build produced "Misc files in release", ".cache", "Misc files in
translations", "Misc files in QtQml" and more, none of which means anything on
its own or can be acted on sensibly.
"""
import os

from app.services.entity_detector import detect_entities
from app.models.finding import Finding

MB = 1024 * 1024
# A standalone Downloads folder. Deliberately not nested under a user profile:
# a profile with a single subfolder never reaches the explode threshold, so it
# would form a "User Profile" entity that also counts these files — an artifact
# of the miniature tree, not of Downloads handling. Real profiles have many
# subfolders and explode, which is what a real scan shows.
DL = "C:/Downloads"


def _f(path, is_dir=False, size=0, ext="", parent=""):
    return Finding(path=path, name=os.path.basename(path), is_dir=is_dir,
                   size_bytes=size, extension=ext, modified=1, accessed=1,
                   parent=parent or os.path.dirname(path).replace("\\", "/"))


def _detect(findings, root="C:/"):
    return detect_entities(findings, root, log_fn=lambda _m: None)


def _norm(p):
    return p.replace("\\", "/").lower().rstrip("/")


def _downloads_tree():
    """An extracted multi-part download beside loose files and installers."""
    f = [_f(DL, is_dir=True, parent="C:/")]
    # one extracted download with internally diverse subfolders
    pkg = f"{DL}/SomeApp_6_11_Release"
    f.append(_f(pkg, is_dir=True, parent=DL))
    for sub, ext, n in (("release", ".dll", 8), ("translations", ".qm", 6),
                        (".cache", ".bin", 10), ("QtQml", ".qml", 7)):
        d = f"{pkg}/{sub}"
        f.append(_f(d, is_dir=True, parent=pkg))
        f += [_f(f"{d}/f{i}{ext}", size=3 * MB, ext=ext, parent=d)
              for i in range(n)]
    # standalone downloads sitting loose in the folder
    f += [_f(f"{DL}/Setup{i}.exe", size=90 * MB, ext=".exe", parent=DL)
          for i in range(2)]
    f += [_f(f"{DL}/report{i}.pdf", size=4 * MB, ext=".pdf", parent=DL)
          for i in range(3)]
    return f, pkg


def test_an_extracted_download_stays_one_entity():
    findings, pkg = _downloads_tree()
    entities = _detect(findings)

    inside = [e for e in entities if _norm(e.path).startswith(_norm(pkg))]
    assert len(inside) == 1, (
        "one download shattered into fragments: "
        f"{[(e.name, e.path) for e in inside]}")
    assert _norm(inside[0].path) == _norm(pkg)
    assert inside[0].entity_type == "download_item"


def test_the_download_entity_covers_all_its_files():
    findings, pkg = _downloads_tree()
    entity = next(e for e in _detect(findings)
                  if _norm(e.path) == _norm(pkg))
    expected = sum(f.size_bytes for f in findings
                   if not f.is_dir and _norm(f.path).startswith(_norm(pkg)))
    assert entity.size_bytes == expected
    assert entity.file_count == 31  # 8 + 6 + 10 + 7


def test_downloads_itself_never_becomes_one_blob():
    """The whole folder must not collapse into a single "Downloads" entity."""
    findings, _ = _downloads_tree()
    entities = _detect(findings)
    assert not any(_norm(e.path) == _norm(DL) and e.file_count > 20
                   for e in entities), (
        "Downloads was parsed as one unified directory")
    assert len(entities) >= 3, "expected several standalone items"


def test_installers_in_downloads_stay_individual():
    findings, _ = _downloads_tree()
    entities = _detect(findings)
    installers = [e for e in entities if e.entity_type == "installer"]
    assert len(installers) == 2, (
        f"expected each .exe to be its own item, got {len(installers)}")
    for e in installers:
        assert e.file_count == 1


def test_loose_documents_are_bucketed_not_merged_into_the_download():
    findings, pkg = _downloads_tree()
    entities = _detect(findings)
    for e in entities:
        if _norm(e.path) == _norm(pkg):
            continue
        for p in (e.removable_file_paths or []):
            assert not _norm(p).startswith(_norm(pkg) + "/"), (
                f"{e.name!r} reaches inside the download: {p}")


def test_every_byte_is_accounted_for_exactly_once():
    findings, _ = _downloads_tree()
    entities = _detect(findings)
    file_bytes = sum(f.size_bytes for f in findings if not f.is_dir)
    assert sum(e.size_bytes for e in entities) <= file_bytes
