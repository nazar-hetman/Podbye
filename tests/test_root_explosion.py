"""Tests for heterogeneous user-root explosion.

Covers the decision helper and the end-to-end behaviour: a diverse Documents
folder is broken into per-subfolder entities instead of one blob, while a
homogeneous folder stays a single entity.
"""
import os

from app.models.finding import Finding
from app.services.entity_detector import _root_is_heterogeneous, detect_entities


# ── _root_is_heterogeneous ────────────────────────────────────────

def test_two_distinct_types_is_heterogeneous():
    assert _root_is_heterogeneous(
        ["photo_collection", "document_folder", None], False, 3
    ) is True


def test_single_type_is_homogeneous():
    assert _root_is_heterogeneous(
        ["photo_collection", "photo_collection", None], False, 3
    ) is False


def test_multipurpose_name_with_enough_subdirs_explodes():
    # No clear content types, but a known dump folder with many subfolders.
    assert _root_is_heterogeneous([None, None, None, None], True, 4) is True


def test_multipurpose_name_with_few_subdirs_does_not_explode():
    assert _root_is_heterogeneous([None, None], True, 2) is False


def test_non_multipurpose_unclassified_does_not_explode():
    assert _root_is_heterogeneous([None, None, None, None], False, 4) is False


# ── end-to-end ────────────────────────────────────────────────────

def _mk(path, is_dir=False, size=0):
    return Finding(
        path=path, name=os.path.basename(path.rstrip("/")), is_dir=is_dir,
        size_bytes=size, extension=os.path.splitext(path)[1] if not is_dir else "",
        modified=1700000000, accessed=1700000000, parent=os.path.dirname(path.rstrip("/")),
    )


def test_diverse_documents_root_is_exploded():
    root = "D:/Personal"
    findings = [
        _mk(root, is_dir=True),
        _mk("D:/Personal/Documents", is_dir=True),
        # Photos subfolder (homogeneous images)
        _mk("D:/Personal/Documents/Vacation", is_dir=True),
        _mk("D:/Personal/Documents/Vacation/a.jpg", size=3_000_000),
        _mk("D:/Personal/Documents/Vacation/b.jpg", size=3_000_000),
        # Video subfolder
        _mk("D:/Personal/Documents/Clips", is_dir=True),
        _mk("D:/Personal/Documents/Clips/c.mp4", size=50_000_000),
        _mk("D:/Personal/Documents/Clips/d.mp4", size=50_000_000),
        # Docs subfolder
        _mk("D:/Personal/Documents/Reports", is_dir=True),
        _mk("D:/Personal/Documents/Reports/r1.pdf", size=1_000_000),
        _mk("D:/Personal/Documents/Reports/r2.pdf", size=1_000_000),
        # An archives subfolder
        _mk("D:/Personal/Documents/Old", is_dir=True),
        _mk("D:/Personal/Documents/Old/o.zip", size=20_000_000),
        _mk("D:/Personal/Documents/Old/p.zip", size=20_000_000),
    ]
    ents = detect_entities(findings, root, log_fn=lambda s: None)
    cats = {e.category for e in ents}
    names = {e.name for e in ents}
    # Documents should NOT survive as one blob; its children appear separately.
    assert "Images" in cats and "Videos" in cats
    assert not any(e.name == "Documents" and e.category == "Documents" for e in ents)
    # Per-subfolder entities exist (qualified names from _qualify_folder_name).
    assert any("Vacation" in n for n in names)
    assert any("Clips" in n for n in names)


def test_homogeneous_folder_not_exploded():
    root = "D:/Media"
    findings = [_mk(root, is_dir=True), _mk("D:/Media/Pics", is_dir=True)]
    # Four image-only subfolders → homogeneous → should NOT explode into noise.
    for i in range(4):
        sub = f"D:/Media/Pics/set{i}"
        findings.append(_mk(sub, is_dir=True))
        findings.append(_mk(f"{sub}/img{i}.jpg", size=2_000_000))
        findings.append(_mk(f"{sub}/img{i}b.jpg", size=2_000_000))
    ents = detect_entities(findings, root, log_fn=lambda s: None)
    # "Pics" stays ONE consolidated entity rather than exploding into 4 — a
    # homogeneous folder must not be broken into per-subfolder noise.
    pics_entities = [
        e for e in ents if "/pics" in e.path.replace("\\", "/").lower()
    ]
    assert len(pics_entities) == 1
