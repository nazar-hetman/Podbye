"""Tests for human-readable naming of opaque/mixed entities."""
import os

from app.models.finding import Finding
from app.services.entity_detector import (
    _ext_group, _content_descriptor, _looks_cryptic, _descriptive_folder_name,
    detect_entities,
)


def _mk(path, is_dir=False, size=0):
    return Finding(
        path=path, name=os.path.basename(path.rstrip("/")), is_dir=is_dir,
        size_bytes=size, extension="" if is_dir else os.path.splitext(path)[1],
        modified=1700000000, accessed=1700000000, parent=os.path.dirname(path.rstrip("/")),
    )


# ── _ext_group ────────────────────────────────────────────────────

def test_ext_group_known():
    assert _ext_group(".jpg") == "images"
    assert _ext_group(".mp4") == "videos"
    assert _ext_group(".py") == "code & config"
    assert _ext_group(".zip") == "archives"


def test_ext_group_unknown():
    assert _ext_group(".xyzzy") == ""


# ── _content_descriptor ───────────────────────────────────────────

def test_descriptor_mostly_one_type():
    kids = [_mk(f"d/{i}.jpg") for i in range(8)] + [_mk("d/a.txt")]
    assert _content_descriptor(kids) == "mostly images"


def test_descriptor_two_types():
    kids = [_mk("d/a.jpg"), _mk("d/b.png"), _mk("d/c.zip"), _mk("d/d.7z")]
    desc = _content_descriptor(kids)
    assert "images" in desc and "archives" in desc


def test_descriptor_empty_for_no_recognised():
    assert _content_descriptor([_mk("d/a.xyzzy")]) == ""
    assert _content_descriptor([]) == ""


# ── _looks_cryptic ────────────────────────────────────────────────

def test_guid_is_cryptic():
    assert _looks_cryptic("{3F2504E0-4F89-41D3-9A0C-0305E82C3301}")


def test_hash_is_cryptic():
    assert _looks_cryptic("a1b2c3d4e5f60718a9b0")


def test_normal_name_not_cryptic():
    assert not _looks_cryptic("Documents")
    assert not _looks_cryptic("My Project")


# ── _descriptive_folder_name ──────────────────────────────────────

def test_cryptic_folder_gets_generic_label_plus_content():
    kids = [_mk("d/a.dll"), _mk("d/b.log"), _mk("d/c.log")]
    name = _descriptive_folder_name(
        "{3F2504E0-4F89-41D3-9A0C-0305E82C3301}", "c:/data/{guid}", kids
    )
    assert name.startswith("Unrecognized folder")


def test_normal_folder_keeps_name_with_hint():
    kids = [_mk("d/a.log"), _mk("d/b.log")]
    name = _descriptive_folder_name("weirdstuff", "c:/weirdstuff", kids)
    assert "logs" in name


# ── End-to-end ────────────────────────────────────────────────────

def test_cryptic_folder_entity_gets_unrecognized_name():
    # A GUID-named folder of config files: not a content collection, so it
    # stays unknown — and should read as "Unrecognized folder · mostly …".
    root = "D:/T"
    guid = "{3F2504E0-4F89-41D3-9A0C-0305E82C3301}"
    findings = [
        _mk(root, is_dir=True),
        _mk(f"D:/T/{guid}", is_dir=True),
        _mk(f"D:/T/{guid}/a.ini", size=80_000),
        _mk(f"D:/T/{guid}/b.ini", size=80_000),
        _mk(f"D:/T/{guid}/c.json", size=80_000),
    ]
    ents = detect_entities(findings, root, log_fn=lambda s: None)
    match = [e for e in ents if guid.lower() in e.path.replace("\\", "/").lower()]
    assert match, "expected an entity for the GUID folder"
    e = match[0]
    if e.entity_type in ("unknown_folder", "mixed_folder"):
        assert e.name.startswith("Unrecognized folder")
        assert "code & config" in e.name
