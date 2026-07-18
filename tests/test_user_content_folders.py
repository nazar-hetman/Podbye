"""Folders inside Documents/Videos/Pictures/Music are app data, not noise.

Observed on a real machine: Documents/Klei (58 MB of Don't Starve save data)
showed as "Klei · documents and code & config" typed unknown_folder — which
tells the user nothing and lands in the "Unknown" pile.
"""
import os

from app.services.entity_detector import (
    detect_entities, _enrich_user_content_subfolders,
)
from app.models.finding import Finding
from app.models.smart_entity import SmartEntity

MB = 1024 * 1024


def _f(path, is_dir=False, size=0, ext="", parent=""):
    return Finding(path=path, name=os.path.basename(path), is_dir=is_dir,
                   size_bytes=size, extension=ext, modified=1, accessed=1,
                   parent=parent or os.path.dirname(path).replace("\\", "/"))


def _e(path, etype="unknown_folder", name=None):
    return SmartEntity(path=path, name=name or os.path.basename(path),
                       entity_type=etype, size_bytes=MB, file_count=3,
                       folder_count=1)


# ── unit: the labelling rule ─────────────────────────────────────


def test_app_folder_in_documents_becomes_application_data():
    e = _e("C:/Users/n/Documents/Klei", name="Klei · documents and code & config")
    assert _enrich_user_content_subfolders([e]) == 1
    assert e.entity_type == "application_data"
    assert e.name == "Klei", "the noisy composite name should be replaced"
    assert "Documents" in e.risk_reason and "Klei" in e.risk_reason


def test_applies_to_each_user_content_root():
    roots = ["Documents", "Videos", "Pictures", "Music", "Saved Games"]
    ents = [_e(f"C:/Users/n/{r}/SomeApp") for r in roots]
    assert _enrich_user_content_subfolders(ents) == len(roots)
    assert all(e.entity_type == "application_data" for e in ents)


def test_does_not_touch_already_classified_entities():
    """A recognised type keeps its own, better classification."""
    for etype in ("game_saves", "photo_collection", "application", "venv"):
        e = _e("C:/Users/n/Documents/Thing", etype=etype)
        _enrich_user_content_subfolders([e])
        assert e.entity_type == etype


def test_only_direct_children_are_relabelled():
    """A folder deeper inside is not automatically 'app data in Documents'."""
    deep = _e("C:/Users/n/Documents/Klei/DoNotStarveTogether/backup")
    assert _enrich_user_content_subfolders([deep]) == 0
    assert deep.entity_type == "unknown_folder"


def test_unrelated_folders_are_untouched():
    other = _e("C:/Program Files/SomeApp/data")
    assert _enrich_user_content_subfolders([other]) == 0
    assert other.entity_type == "unknown_folder"


def test_it_does_not_claim_the_app_is_gone():
    """Install status needs alias handling + several evidence sources to be
    safe, so this rule must not imply the app is missing or deletable."""
    e = _e("C:/Users/n/Documents/Klei")
    _enrich_user_content_subfolders([e])
    text = f"{e.risk_reason} {e.summary}".lower()
    for claim in ("orphan", "safe to delete", "not installed", "no longer"):
        assert claim not in text, f"unsafe claim {claim!r} in: {text}"


# ── end to end ───────────────────────────────────────────────────


def test_pipeline_labels_documents_subfolder_as_app_data():
    docs = "C:/Users/n/Documents"
    f = [
        _f("C:/Users", is_dir=True, parent="C:/"),
        _f("C:/Users/n", is_dir=True, parent="C:/Users"),
        _f(docs, is_dir=True, parent="C:/Users/n"),
    ]
    # Several app folders — a real Documents folder holds many, which is what
    # lets it explode into per-app entities instead of one blob.
    for app in ("Klei", "Mission Planner", "grassdata", "My Games"):
        f.append(_f(f"{docs}/{app}", is_dir=True, parent=docs))
        for sub, ext, n in (("save", ".dat", 6), ("cfg", ".ini", 4),
                            ("logs", ".log", 5)):
            d = f"{docs}/{app}/{sub}"
            f.append(_f(d, is_dir=True, parent=f"{docs}/{app}"))
            f += [_f(f"{d}/x{i}{ext}", size=4 * MB, ext=ext, parent=d)
                  for i in range(n)]

    klei = [e for e in detect_entities(f, "C:/", log_fn=lambda _m: None)
            if e.path.replace("\\", "/").lower().endswith("/documents/klei")]
    assert klei, "no entity produced for Documents/Klei"
    assert klei[0].entity_type == "application_data"
    assert klei[0].category == "Application Data"
    assert klei[0].name == "Klei"
