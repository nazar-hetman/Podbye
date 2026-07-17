"""Tests for two categorization-accuracy fixes:

1. AppData/Local/Microsoft is no longer blanket-classified as System/Protected
   — only the genuine credential/crypto stores stay protected.
2. App/UI asset folders (icons, sprites, web graphics) are kept out of the
   user-facing Images category and shown as application data instead.
"""
import os

from app.models.finding import Finding
from app.services.entity_detector import (
    detect_entities,
    _is_protected_path,
    _looks_like_app_assets,
)


def _mk(path, is_dir=False, size=0, ext=None):
    if ext is None:
        ext = "" if is_dir else os.path.splitext(path)[1]
    return Finding(
        path=path, name=os.path.basename(path.rstrip("/")), is_dir=is_dir,
        size_bytes=size, extension=ext, modified=1700000000, accessed=1700000000,
        parent=os.path.dirname(path.rstrip("/")),
    )


# ── #1  AppData\Local\Microsoft is not blanket "System" ───────────

def test_appdata_microsoft_root_not_protected():
    assert _is_protected_path("c:/users/nazar/appdata/local/microsoft") is False


def test_appdata_microsoft_subtree_not_protected():
    # Edge cache lives here — regenerable, not system-critical.
    assert _is_protected_path(
        "c:/users/nazar/appdata/local/microsoft/edge/user data/default/cache"
    ) is False


def test_credential_store_still_protected():
    assert _is_protected_path(
        "c:/users/nazar/appdata/roaming/microsoft/protect/s-1-5-21"
    ) is True
    assert _is_protected_path(
        "c:/users/nazar/appdata/roaming/microsoft/crypto/rsa"
    ) is True
    assert _is_protected_path(
        "c:/users/nazar/appdata/roaming/microsoft/credentials"
    ) is True
    assert _is_protected_path(
        "c:/users/nazar/appdata/local/microsoft/vault"
    ) is True


def test_windows_root_still_protected():
    assert _is_protected_path("c:/windows/system32") is True


def test_appdata_microsoft_cache_classifies_as_cache_not_system():
    root = "c:/users/nazar/appdata/local/microsoft"
    findings = [
        _mk(root, is_dir=True),
        _mk(f"{root}/edge", is_dir=True),
        _mk(f"{root}/edge/cache", is_dir=True),
        _mk(f"{root}/edge/cache/a.tmp", size=4000),
        _mk(f"{root}/edge/cache/b.tmp", size=4000),
    ]
    ents = detect_entities(findings, root, log_fn=lambda s: None)
    assert ents, "expected at least one entity"
    assert all(e.entity_type != "protected_system" for e in ents)
    assert all(e.category != "System" for e in ents)


# ── #2  App/UI asset folders are not user media ───────────────────

def test_asset_helper_flags_web_graphics():
    files = [_mk("p/logo.svg", size=2000), _mk("p/icon.png", size=3000)]
    assert _looks_like_app_assets("c:/proj/assets", "assets", files) is True


def test_asset_helper_respects_real_photos():
    # A real photo present → never treated as assets, even when named "assets".
    files = [_mk("p/IMG_001.jpg", size=2_500_000), _mk("p/icon.png", size=3000)]
    assert _looks_like_app_assets("c:/proj/assets", "assets", files) is False


def test_asset_helper_ignores_plain_named_folder():
    # Not an asset name and not inside an app tree → leave it as user media.
    files = [_mk("p/a.png", size=3000), _mk("p/b.png", size=3000)]
    assert _looks_like_app_assets("c:/users/nazar/screens", "screens", files) is False


def test_asset_helper_detects_internal_ancestor():
    files = [_mk("p/sprite.png", size=3000)]
    assert _looks_like_app_assets(
        "c:/proj/myapp/resources/images", "images", files
    ) is True


def test_app_assets_folder_not_in_images_category():
    # An "assets" folder that surfaces on its own (here, directly under the
    # scan root) must not be filed under Images.
    root = "c:/proj/myapp"
    findings = [
        _mk(root, is_dir=True),
        _mk(f"{root}/assets", is_dir=True),
        _mk(f"{root}/assets/logo.svg", size=2000),
        _mk(f"{root}/assets/icon.png", size=3000),
        _mk(f"{root}/assets/sprite.gif", size=2500),
    ]
    ents = detect_entities(findings, root, log_fn=lambda s: None)
    assets = [e for e in ents if e.path.replace("\\", "/").lower().endswith("/assets")]
    assert assets, "expected the assets folder to be surfaced"
    assert all(e.category != "Images" for e in assets)
    assert all(e.entity_type != "photo_collection" for e in assets)


def test_real_photo_collection_still_images():
    root = "c:/users/nazar/pictures"
    findings = [
        _mk(root, is_dir=True),
        _mk(f"{root}/Vacation2024", is_dir=True),
        _mk(f"{root}/Vacation2024/p1.jpg", size=2_000_000),
        _mk(f"{root}/Vacation2024/p2.jpg", size=2_200_000),
        _mk(f"{root}/Vacation2024/p3.jpg", size=1_900_000),
    ]
    ents = detect_entities(findings, root, log_fn=lambda s: None)
    assert any(e.entity_type == "photo_collection" for e in ents)
