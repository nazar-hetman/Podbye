"""Critical logic rules (data safety).

Rule 1 — App boundaries: a database/image/video/logo inside a recognised
application's install folder belongs to that application, never to a loose
media bucket.

Rule 2 — System protection: OS application packages and the servicing /
component store are never offered for cleanup, whatever an earlier
classification pass decided.
"""
import os

import pytest

from app.services import entity_detector as ed
from app.services.entity_detector import detect_entities
from app.models.finding import Finding

MB = 1024 * 1024
MEDIA_TYPES = {"media_collection", "photo_collection", "image_collection",
               "video_collection", "audio_collection"}


def _f(path, is_dir=False, size=0, ext="", parent=""):
    return Finding(path=path, name=os.path.basename(path), is_dir=is_dir,
                   size_bytes=size, extension=ext, modified=1, accessed=1,
                   parent=parent or os.path.dirname(path).replace("\\", "/"))


def _detect(findings, root="C:/"):
    return detect_entities(findings, root, log_fn=lambda _m: None)


def _norm(p):
    return p.replace("\\", "/").lower().rstrip("/")


# ── Rule 1 — app boundaries ──────────────────────────────────────


@pytest.fixture
def registered_app(monkeypatch):
    """An app registered in the uninstall registry, like a real install."""
    app_dir = "C:/Program Files/AcmeSuite"
    monkeypatch.setattr(ed, "_get_installed_programs", lambda *a, **k: {
        _norm(app_dir): {"name": "Acme Suite", "publisher": "Acme Ltd",
                         "version": "3.1", "install_date": "",
                         "uninstall_string": ""}
    })
    return app_dir


def _app_with_media(app_dir):
    """An install folder carrying exactly the asset kinds Rule 1 names."""
    f = [
        _f("C:/Program Files", is_dir=True, parent="C:/"),
        _f(app_dir, is_dir=True, parent="C:/Program Files"),
        _f(f"{app_dir}/acme.exe", size=40 * MB, ext=".exe", parent=app_dir),
    ]
    for sub, ext, n, sz in (("assets", ".png", 12, 3),      # logos / images
                            ("media", ".mp4", 4, 60),        # video
                            ("data", ".db", 3, 20)):         # database
        d = f"{app_dir}/{sub}"
        f.append(_f(d, is_dir=True, parent=app_dir))
        f += [_f(f"{d}/file{i}{ext}", size=sz * MB, ext=ext, parent=d)
              for i in range(n)]
    return f


def test_media_inside_an_app_is_not_a_standalone_media_entity(registered_app):
    entities = _detect(_app_with_media(registered_app))
    inside = [e for e in entities
              if _norm(e.path).startswith(_norm(registered_app))
              and _norm(e.path) != _norm(registered_app)]
    offenders = [e for e in inside if e.entity_type in MEDIA_TYPES]
    assert not offenders, (
        "app assets surfaced as standalone media: "
        f"{[(e.name, e.entity_type, e.path) for e in offenders]}")


def test_app_assets_are_not_offered_as_loose_files(registered_app):
    """No bucket may list files that live inside the application folder."""
    entities = _detect(_app_with_media(registered_app))
    app = _norm(registered_app)
    for e in entities:
        if _norm(e.path) == app:
            continue  # the app entity itself may own them
        for p in (e.removable_file_paths or []):
            assert not _norm(p).startswith(app + "/"), (
                f"{e.name!r} would recycle {p}, which belongs to the app")


def test_the_app_itself_is_still_detected(registered_app):
    """Guard against 'passing' by producing no entities at all."""
    entities = _detect(_app_with_media(registered_app))
    assert any(_norm(e.path) == _norm(registered_app) for e in entities)


# ── Rule 2 — system protection ───────────────────────────────────


def _system_tree():
    """OS app assets + component store, the shapes seen on a real machine."""
    f = [_f("C:/Windows", is_dir=True, parent="C:/")]
    for sub, ext, n in (("SystemApps/Microsoft.Windows.Cortana_cw5n/Assets", ".png", 8),
                        ("SystemApps/Microsoft.Windows.CloudExperienceHost/Cache", ".dat", 6),
                        ("WinSxS/Backup", ".dll", 5),
                        ("servicing/Packages", ".mum", 7),
                        ("assembly/GAC_MSIL", ".dll", 4)):
        parts = sub.split("/")
        cur = "C:/Windows"
        for seg in parts:
            cur = f"{cur}/{seg}"
            f.append(_f(cur, is_dir=True, parent=os.path.dirname(cur)))
        f += [_f(f"{cur}/item{i}{ext}", size=2 * MB, ext=ext, parent=cur)
              for i in range(n)]
    return f


def test_os_app_assets_are_never_cleanable():
    """Reproduced on a real machine before the fix: "Cache – Cortana.Ui" was
    Safe and six "Images – …" collections were Review."""
    entities = _detect(_system_tree())
    never = [e for e in entities
             if _norm(e.path).split("/")[2:3] and
             _norm(e.path).split("/")[2] in
             ("systemapps", "winsxs", "servicing", "assembly")]
    assert never, "scenario produced no entities in the protected subtrees"
    unprotected = [e for e in never if e.risk != "Protected"]
    assert not unprotected, (
        "OS system content offered for cleanup: "
        f"{[(e.name, e.risk, e.path) for e in unprotected]}")


@pytest.mark.parametrize("subtree", ["systemapps", "winsxs", "servicing", "assembly"])
def test_each_never_clean_subtree_is_covered(subtree):
    assert subtree in ed._NEVER_CLEAN_WINDOWS_SUBTREES


def test_protection_does_not_swallow_legitimately_cleanable_windows_dirs():
    """Guard against over-reach: Windows/Temp and Windows/Logs stay cleanable —
    over-protecting them would gut the product's core value."""
    f = [_f("C:/Windows", is_dir=True, parent="C:/")]
    for sub, ext in (("Temp", ".tmp"), ("Logs", ".log")):
        d = f"C:/Windows/{sub}"
        f.append(_f(d, is_dir=True, parent="C:/Windows"))
        f += [_f(f"{d}/f{i}{ext}", size=5 * MB, ext=ext, parent=d)
              for i in range(6)]
    entities = _detect(f)
    tempish = [e for e in entities
               if _norm(e.path).startswith(("c:/windows/temp", "c:/windows/logs"))]
    assert tempish, "no Temp/Logs entities produced"
    assert any(e.risk != "Protected" for e in tempish), (
        "Windows Temp/Logs were swept into Protected — cleanup value lost")


def _entity(path, risk="Safe"):
    from app.models.smart_entity import SmartEntity
    return SmartEntity(path=path, name=os.path.basename(path.rstrip("/")),
                       entity_type="cache_folder", size_bytes=MB,
                       file_count=1, folder_count=0, risk=risk)


def test_only_real_os_subtrees_are_protected():
    """The guard keys on <drive>/windows/<subtree>, so a folder that merely
    shares the name — a game's own SystemApps — is left alone."""
    os_asset = _entity("C:/Windows/SystemApps/Microsoft.Cortana/Assets")
    lookalike = _entity("C:/Games/Wanderer/SystemApps")
    deep_named = _entity("C:/Users/n/AppData/Local/App/WinSxS")

    ed._enforce_system_protection([os_asset, lookalike, deep_named])

    assert os_asset.risk == "Protected"
    assert lookalike.risk == "Safe", "non-OS path caught by the Windows rule"
    assert deep_named.risk == "Safe", "nested lookalike caught by the Windows rule"


def test_protection_states_a_reason():
    e = _entity("C:/Windows/servicing/Packages")
    ed._enforce_system_protection([e])
    assert e.risk == "Protected"
    assert e.risk_reason, "protected entity must explain why"
