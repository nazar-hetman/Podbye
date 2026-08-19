"""A folder's stray loose files must not name the whole folder.

_classify_by_content reads a folder's DIRECT children; the entity it labels
then reports RECURSIVE totals. When the two disagree, the label describes a
sample that is nothing like the thing being measured.

Reported from an all-drives scan: the only loose file at D:\\ was RESULTS.md,
so document extensions were 100% of the direct files, the drive root was
labelled "Documents Folder", and the row displayed the drive's recursive
totals — 392,273 files and 645 GB of aerial imagery, under a blank name.
The same shape hit coordinate_recovery_outputs: 187,555 files, 0.0% documents
overall, labelled from a handful of loose files sitting at its top.

Single-drive scans hid it, because there the drive root is the scan root and
is dropped as the scan-root aggregate. It only surfaces with several roots.
"""
import pytest

from app.models.finding import Finding
from app.services.entity_detector import (
    _direct_files_describe_folder, detect_entities,
)

GB = 1024 ** 3


def _file(path, size):
    import os
    return Finding(path=path, name=os.path.basename(path), is_dir=False,
                   size_bytes=size, extension=os.path.splitext(path)[1].lower(),
                   modified=0, accessed=0, parent=os.path.dirname(path))


def _dir(path):
    import os
    return Finding(path=path, name=os.path.basename(path.rstrip("/")), is_dir=True,
                   size_bytes=0, extension="", modified=0, accessed=0,
                   parent=os.path.dirname(path.rstrip("/")))


# ── the predicate itself ─────────────────────────────────────────

def test_one_readme_does_not_speak_for_a_whole_drive():
    readme = _file("D:/RESULTS.md", 4096)
    photos = [_file(f"D:/Cetus/img{i}.jpg", 2 * GB) for i in range(20)]
    assert not _direct_files_describe_folder([readme], [readme] + photos)


def test_loose_files_that_are_most_of_the_folder_do_speak():
    docs = [_file(f"D:/Reports/r{i}.pdf", 1 * GB) for i in range(9)]
    stray = _file("D:/Reports/sub/thumb.jpg", 1024)
    assert _direct_files_describe_folder(docs, docs + [stray])


def test_a_flat_folder_always_speaks_for_itself():
    docs = [_file(f"D:/Reports/r{i}.pdf", 1 * GB) for i in range(3)]
    assert _direct_files_describe_folder(docs, docs)


def test_zero_byte_descendants_do_not_divide_by_zero():
    empty = [_file("D:/x/a.txt", 0)]
    assert _direct_files_describe_folder(empty, empty)


def test_no_direct_files_never_speaks():
    assert not _direct_files_describe_folder([], [_file("D:/x/sub/a.jpg", GB)])


# ── end to end ───────────────────────────────────────────────────

def _drive_with_a_stray_readme():
    """A drive of imagery with one markdown file loose at its root."""
    findings = [_file("D:/RESULTS.md", 8192)]
    for folder in ("Survey", "Flights", "Ortho"):
        findings.append(_dir(f"D:/{folder}"))
        findings += [_file(f"D:/{folder}/img{i}.jpg", 3 * GB) for i in range(12)]
    return findings


def test_a_drive_root_is_not_a_documents_folder():
    # Scanned as one of several roots, so the drive root is not the scan root.
    entities = detect_entities(_drive_with_a_stray_readme(), "")
    offenders = [
        e for e in entities
        if e.entity_type == "document_folder" and e.size_bytes > 10 * GB
    ]
    assert not offenders, (
        "a drive of imagery was labelled a documents folder from one stray "
        f".md: {[(e.name, e.size_bytes // GB) for e in offenders]}"
    )


def test_a_genuine_document_folder_still_classifies():
    """The guard must not cost real document folders their label."""
    findings = [_dir("E:/Reports")]
    findings += [_file(f"E:/Reports/report{i}.pdf", 40 * 1024 * 1024)
                 for i in range(30)]
    entities = detect_entities(findings, "E:/Reports")
    kinds = {e.entity_type for e in entities}
    assert "document_folder" in kinds, (
        f"a folder of 30 PDFs no longer reads as documents: {kinds}"
    )
