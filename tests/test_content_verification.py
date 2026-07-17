"""Tests for content/format verification and single-file entity targeting.

Covers the fixes for: trusting folder names over content, the user-profile
blob, installer entities pointing at their parent folder, and loose buckets
that could target the drive root.
"""
import os

from app.models.finding import Finding
from app.services.entity_detector import (
    detect_entities, _content_confirms_type, _qualify_folder_name,
)
from app.screens.cleanup_dialog import _cleanup_targets_for_item, _is_drive_root_path


# ── Folder names are not qualified with the account name ───────────

def test_profile_documents_not_qualified_with_username():
    assert _qualify_folder_name("documents", "c:/users/nazar/documents") == "Documents"


def test_meaningful_qualifier_is_still_used():
    # An app/owner qualifier is useful and must be kept.
    assert _qualify_folder_name(
        "cache", "c:/users/nazar/appdata/local/discord/cache"
    ) == "Cache – Discord"


def _mk(path, is_dir=False, size=0, ext=None):
    if ext is None:
        ext = "" if is_dir else os.path.splitext(path)[1]
    return Finding(
        path=path, name=os.path.basename(path.rstrip("/")), is_dir=is_dir,
        size_bytes=size, extension=ext, modified=1700000000, accessed=1700000000,
        parent=os.path.dirname(path.rstrip("/")),
    )


# ── _content_confirms_type ────────────────────────────────────────

def test_confirm_true_when_files_match():
    kids = [_mk("d/a.jpg", size=1), _mk("d/b.png", size=1)]
    assert _content_confirms_type(kids, "photo_collection") is True


def test_confirm_false_when_files_do_not_match():
    kids = [_mk("d/app.exe", size=1), _mk("d/lib.dll", size=1)]
    assert _content_confirms_type(kids, "photo_collection") is False


def test_confirm_false_for_empty_folder():
    assert _content_confirms_type([], "video_collection") is False


def test_confirm_passthrough_for_non_content_type():
    assert _content_confirms_type([], "cache_folder") is True


# ── End-to-end: name vs content ───────────────────────────────────

def test_videos_folder_without_videos_is_not_video_collection():
    root = "D:/T"
    findings = [
        _mk(root, is_dir=True),
        _mk("D:/T/Videos", is_dir=True),
        # No video files — just text. Name says "Videos" but content disagrees.
        _mk("D:/T/Videos/notes.txt", size=1000),
        _mk("D:/T/Videos/readme.md", size=1000),
    ]
    ents = detect_entities(findings, root, log_fn=lambda s: None)
    vids = [e for e in ents if "/videos" in e.path.replace("\\", "/").lower()]
    assert all(e.entity_type != "video_collection" for e in vids)


def test_videos_folder_with_videos_is_video_collection():
    root = "D:/T"
    findings = [
        _mk(root, is_dir=True),
        _mk("D:/T/Videos", is_dir=True),
        _mk("D:/T/Videos/a.mp4", size=50_000_000),
        _mk("D:/T/Videos/b.mkv", size=50_000_000),
    ]
    ents = detect_entities(findings, root, log_fn=lambda s: None)
    assert any(e.entity_type == "video_collection" for e in ents)


def test_name_with_photo_but_no_images_not_images():
    # "Windows Photo Viewer"-style folder: name has "photo", content is exes.
    root = "D:/T"
    findings = [
        _mk(root, is_dir=True),
        _mk("D:/T/Photo Viewer", is_dir=True),
        _mk("D:/T/Photo Viewer/viewer.exe", size=5_000_000),
        _mk("D:/T/Photo Viewer/core.dll", size=2_000_000),
    ]
    ents = detect_entities(findings, root, log_fn=lambda s: None)
    pv = [e for e in ents if "photo viewer" in e.path.lower()]
    assert all(e.category != "Images" for e in pv)


# ── Installer entity targets the file, not the folder ─────────────

def test_loose_installer_points_at_file_not_parent():
    root = "D:/Downloads"
    findings = [
        _mk(root, is_dir=True),
        _mk("D:/Downloads/Codex Installer.exe", size=40_000_000),
    ]
    ents = detect_entities(findings, root, log_fn=lambda s: None)
    inst = [e for e in ents if e.entity_type == "installer"]
    assert inst, "expected an installer entity"
    e = inst[0]
    assert e.path.replace("\\", "/").endswith("Codex Installer.exe")
    d = e.to_dict()
    targets = _cleanup_targets_for_item(d)
    assert len(targets) == 1
    assert targets[0]["path"].replace("\\", "/").endswith("Codex Installer.exe")


# ── Cleanup never targets a drive root ────────────────────────────

def test_drive_root_path_helper():
    assert _is_drive_root_path("C:/")
    assert _is_drive_root_path("C:\\")
    assert _is_drive_root_path("C:")
    assert not _is_drive_root_path("C:/Users")


def test_loose_bucket_expands_to_files_not_root():
    # A loose-archive bucket whose display path is the root must expand to the
    # individual files, never recycle the root itself.
    item = {
        "risk": "Optional",
        "entity_type": "archive_group",
        "actionability": "recycle",
        "path": "C:/",
        "name": "Loose archives",
        "removable_file_paths": ["C:/old/a.zip", "C:/b.7z"],
    }
    targets = _cleanup_targets_for_item(item)
    paths = {t["path"] for t in targets}
    assert paths == {"C:/old/a.zip", "C:/b.7z"}
    assert "C:/" not in paths


def test_root_path_without_files_is_refused():
    item = {"risk": "Optional", "entity_type": "archive_group",
            "actionability": "recycle", "path": "C:/", "name": "x"}
    assert _cleanup_targets_for_item(item) == []
