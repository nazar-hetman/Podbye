"""Tests for the canonical risk colour / variant / reclaimable helpers and
for cross-mode category + reclaimable consistency.
"""
from app.models.risk import (
    risk_variant, reclaimable_bytes,
    RISK_SAFE, RISK_OPTIONAL, RISK_REVIEW, RISK_PROTECTED,
)
from app.models.finding import categorize, Finding


# ── risk_variant ──────────────────────────────────────────────────

def test_variant_is_distinct_per_level():
    variants = {risk_variant(r) for r in
                (RISK_SAFE, RISK_OPTIONAL, RISK_REVIEW, RISK_PROTECTED)}
    assert variants == {"safe", "optional", "review", "protected"}


def test_legacy_risk_folds_to_review_variant():
    assert risk_variant("Risk") == "review"


# ── reclaimable_bytes (shared formula) ────────────────────────────

def test_safe_is_fully_reclaimable():
    assert reclaimable_bytes(RISK_SAFE, 1000) == 1000


def test_review_without_age_is_zero():
    assert reclaimable_bytes(RISK_REVIEW, 1000) == 0


def test_review_with_age_boost_is_partial():
    assert reclaimable_bytes(RISK_REVIEW, 1000, age_boost=0.4) == 400


def test_optional_is_zero_by_formula():
    # Optional needs a human decision; only duplicates (handled by caller) differ.
    assert reclaimable_bytes(RISK_OPTIONAL, 1000) == 0


def test_protected_is_never_reclaimable():
    assert reclaimable_bytes(RISK_PROTECTED, 1000, age_boost=0.4) == 0


# ── All-files Media split (mode parity) ───────────────────────────

def test_image_extension_categorizes_as_images():
    cat, *_ = categorize("C:/u/p/photo.jpg", "photo.jpg", ".jpg", False, 1000)
    assert cat == "Images"


def test_video_extension_categorizes_as_videos():
    cat, *_ = categorize("C:/u/p/clip.mp4", "clip.mp4", ".mp4", False, 1000)
    assert cat == "Videos"


def test_audio_extension_categorizes_as_audio():
    cat, *_ = categorize("C:/u/p/song.mp3", "song.mp3", ".mp3", False, 1000)
    assert cat == "Audio"


def test_split_media_categories_are_review_risk():
    f = Finding(path="C:/u/p/photo.jpg", name="photo.jpg", is_dir=False,
                size_bytes=1000, extension=".jpg", modified=0, accessed=0, parent="C:/u/p")
    assert f.category == "Images"
    assert f.risk == "Review"


def test_finding_to_dict_uses_shared_reclaimable():
    # A Safe cache file → fully reclaimable via the shared formula.
    f = Finding(path="C:/u/AppData/Local/Temp/x.tmp", name="x.tmp", is_dir=False,
                size_bytes=2048, extension=".tmp", modified=0, accessed=0,
                parent="C:/u/AppData/Local/Temp")
    d = f.to_dict()
    if f.risk == "Safe":
        assert d["reclaimable_bytes"] == 2048
    else:
        assert d["reclaimable_bytes"] == 0
