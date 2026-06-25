"""Tests for the SmartEntity model — category mapping, risk, to_dict shape."""
from app.models.smart_entity import (
    SmartEntity,
    ENTITY_TYPES,
    _CATEGORY_BY_TYPE,
)
from app.screens.cleanup_dialog import _cleanup_targets_for_item
from app.screens.findings_dashboard import _duplicate_title


def _entity(entity_type: str, **kw) -> SmartEntity:
    base = dict(path="C:/x", name="x", entity_type=entity_type,
                file_count=1, size_bytes=1024)
    base.update(kw)
    return SmartEntity(**base)


def test_every_entity_type_has_a_category():
    missing = [t for t in ENTITY_TYPES if t not in _CATEGORY_BY_TYPE]
    assert not missing, f"entity types with no category mapping: {missing}"


def test_category_property_matches_constant():
    e = _entity("node_modules")
    assert e.category == "Dev Artifacts"
    assert e.category == _CATEGORY_BY_TYPE["node_modules"]


def test_unknown_type_falls_back_to_unknown_category():
    e = _entity("not_a_real_type")
    assert e.category == "Unknown"


def test_risk_defaults_from_entity_type():
    assert _entity("cache_folder").risk == "Safe"
    assert _entity("protected_system").risk == "Protected"
    assert _entity("application").risk == "Review"


def test_cloud_sync_never_stays_safe():
    e = _entity("cache_folder", cloud_sync_provider="OneDrive")
    assert e.risk == "Review", "cloud-synced entities must not be Safe"


def test_to_dict_has_finding_compatible_keys():
    d = _entity("photo_collection", file_count=3, size_bytes=8192).to_dict()
    for key in ("category", "path", "name", "risk", "size_bytes",
                "reclaimable_bytes", "entity_type", "is_entity",
                "why", "recommendation"):
        assert key in d, f"to_dict() missing key: {key}"


def test_to_dict_why_explains_type_reason_and_removal_impact():
    d = _entity(
        "cache_folder",
        risk_reason="Cache folder name",
        size_bytes=8192,
    ).to_dict()

    assert "What it is: Cache / Temp Folder." in d["why"]
    assert "Why found: Cache folder name." in d["why"]
    assert "If removed:" in d["why"]
    assert "recreate" in d["why"]


def test_duplicate_group_why_explains_identical_copies():
    d = _entity(
        "duplicate_group",
        size_bytes=3000,
        dup_reclaimable=2000,
        risk="Optional",
        risk_reason="",
        duplicate_locations=[
            {"path": "C:/keeper/report.pdf", "name": "report.pdf"},
            {"path": "D:/copies/report.pdf", "name": "report.pdf"},
        ],
        removable_duplicate_paths=["D:/copies/report.pdf"],
    ).to_dict()

    assert "Duplicate File Group" in d["why"]
    assert "identical file content" in d["why"]
    assert "extra copies" in d["why"]


def test_safe_entity_is_fully_reclaimable():
    d = _entity("cache_folder", size_bytes=5000).to_dict()
    assert d["reclaimable_bytes"] == 5000


def test_duplicate_group_displays_reclaimable_size():
    d = _entity(
        "duplicate_group",
        size_bytes=3000,
        dup_reclaimable=2000,
        risk="Optional",
        removable_duplicate_paths=["C:/dup/extra.bin"],
    ).to_dict()

    assert d["size"] == "2 KB"
    assert d["reclaimable_bytes"] == 2000
    assert d["removable_duplicate_paths"] == ["C:/dup/extra.bin"]


def test_duplicate_title_uses_representative_filename_and_copy_count():
    item = _entity(
        "duplicate_group",
        name="Duplicate Files",
        file_count=3,
        duplicate_locations=[
            {"path": "C:/keeper/report.pdf", "name": "report.pdf"},
            {"path": "D:/copies/report.pdf", "name": "report.pdf"},
            {"path": "E:/more/report.pdf", "name": "report.pdf"},
        ],
    ).to_dict()

    assert _duplicate_title(item) == "report.pdf · 3 copies"


def test_duplicate_cleanup_targets_only_explicit_extra_files():
    item = _entity(
        "duplicate_group",
        path="C:/keeper/file.bin",
        size_bytes=3000,
        dup_reclaimable=2000,
        risk="Optional",
        duplicate_locations=[
            {"path": "C:/keeper/file.bin", "size_bytes": 1000, "role": "keep candidate"},
            {"path": "D:/copies/file.bin", "size_bytes": 1000, "role": "extra copy candidate"},
            {"path": "E:/more/file.bin", "size_bytes": 1000, "role": "extra copy candidate"},
        ],
        removable_duplicate_paths=["D:/copies/file.bin", "E:/more/file.bin"],
    ).to_dict()

    targets = _cleanup_targets_for_item(item)

    assert [t["path"] for t in targets] == ["D:/copies/file.bin", "E:/more/file.bin"]
    assert "C:/keeper" not in [t["path"] for t in targets]
    assert "C:/keeper/file.bin" not in [t["path"] for t in targets]
    assert all(t["cleanup_source_type"] == "duplicate_group" for t in targets)


def test_legacy_duplicate_group_without_targets_is_manual_review_only():
    item = _entity(
        "duplicate_group",
        path="C:/keeper/file.bin",
        size_bytes=3000,
        dup_reclaimable=2000,
        risk="Optional",
    ).to_dict()

    assert _cleanup_targets_for_item(item) == []


def test_confidence_label_buckets():
    assert _entity("cache_folder", confidence_score=0.95).confidence_label == "Verified"
    assert _entity("cache_folder", confidence_score=0.70).confidence_label == "Strong"
    assert _entity("cache_folder", confidence_score=0.50).confidence_label == "Likely"
    assert _entity("cache_folder", confidence_score=0.10).confidence_label == "Uncertain"


def test_to_dict_exposes_confidence():
    d = _entity("application", confidence_score=0.9).to_dict()
    assert d["confidence_score"] == 0.9
    assert d["confidence_label"] == "Verified"


def test_refined_category_taxonomy():
    # installer family consolidated into one category
    assert _entity("installer").category == "Installers"
    assert _entity("installer_group").category == "Installers"
    assert _entity("installer_cache").category == "Installers"
    # media split by content type; media_collection stays the mixed bucket
    assert _entity("photo_collection").category == "Images"
    assert _entity("video_collection").category == "Videos"
    assert _entity("audio_collection").category == "Audio"
    assert _entity("creative_project").category == "Creative Projects"
    assert _entity("media_collection").category == "Media"
    # a dataset is research/ML data, not an archive
    assert _entity("dataset").category == "AI / ML"
