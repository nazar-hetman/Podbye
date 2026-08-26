"""Podbye must not offer to clean itself up.

%APPDATA%/Podbye holds config.json, the scan sessions and the logs. Left to the
generic passes those classify like anyone else's data — "logs" as a log folder,
"cache" as a cache folder, both Safe and both recycle-able — so Podbye could
propose deleting the session store holding the results on screen, mid-run.

Measured on the reporting machine, that folder is 6.9 GB. It is cleared through
History (one session at a time), never by recycling the folder underneath a
live process.
"""
import os

import pytest

from app.services import entity_detector as ed
from app.services import cleanup_engine, self_paths
from app.models.finding import Finding
from app.models.smart_entity import SmartEntity

MB = 1024 * 1024


@pytest.fixture
def fake_appdata(monkeypatch, tmp_path):
    """Point %APPDATA% somewhere synthetic so the real profile is untouched."""
    monkeypatch.setenv("APPDATA", r"C:\Users\u\AppData\Roaming")
    return "c:/users/u/appdata/roaming/podbye"


def _f(path, is_dir=False, size=0, ext="", parent=""):
    return Finding(path=path, name=os.path.basename(path), is_dir=is_dir,
                   size_bytes=size, extension=ext, modified=1, accessed=1,
                   parent=parent)


def _podbye_tree():
    """The real shape: config.json plus sessions / logs / cache subfolders."""
    base = "C:/Users/u/AppData/Roaming/Podbye"
    out = [
        _f("C:/Users", is_dir=True, parent="C:/"),
        _f("C:/Users/u", is_dir=True, parent="C:/Users"),
        _f("C:/Users/u/AppData", is_dir=True, parent="C:/Users/u"),
        _f("C:/Users/u/AppData/Roaming", is_dir=True, parent="C:/Users/u/AppData"),
        _f(base, is_dir=True, parent="C:/Users/u/AppData/Roaming"),
        _f(f"{base}/config.json", size=4096, ext=".json", parent=base),
    ]
    for sub, fname, ext, size in [
        ("sessions", "session_b7ff978b.json", ".json", 1643 * MB),
        ("sessions", "last_run.json", ".json", 200 * MB),
        ("logs", "podbye.log", ".log", 12 * MB),
        ("cache", "ai_cache.db", ".db", 30 * MB),
    ]:
        d = f"{base}/{sub}"
        out.append(_f(d, is_dir=True, parent=base))
        out.append(_f(f"{d}/{fname}", size=size, ext=ext, parent=d))
    return out


# ── where Podbye lives ─────────────────────────────────────────────

def test_data_dir_follows_appdata(fake_appdata):
    assert self_paths.data_dir() == fake_appdata


@pytest.mark.parametrize("path", [
    "C:/Users/u/AppData/Roaming/Podbye",
    "C:/Users/u/AppData/Roaming/Podbye/sessions/last_run.json",
    "C:/Users/u/AppData/Roaming/Podbye/logs",
    r"C:\Users\u\AppData\Roaming\Podbye\cache",   # backslashes too
])
def test_own_paths_are_recognised(fake_appdata, path):
    assert self_paths.is_self_path(path)


@pytest.mark.parametrize("path", [
    "C:/Users/u/AppData/Roaming/PodbyeSomethingElse",   # prefix, not a parent
    "C:/Users/u/AppData/Roaming/Code",
    "C:/Users/u/Downloads/podbye.zip",
    "",
])
def test_unrelated_paths_are_not(fake_appdata, path):
    assert not self_paths.is_self_path(path)


# ── the guard against protecting half the disk ────────────────────

@pytest.mark.parametrize("root", [
    "c:/users/u/downloads",     # a portable build run from Downloads
    "c:/users/u/desktop",
    "c:/program files",
    "c:",                       # a drive root
    "c:/users",
])
def test_a_shared_folder_is_never_adopted_as_a_self_root(root):
    """A portable exe run out of Downloads must not protect all of Downloads."""
    assert not self_paths._is_usable_root(root)


@pytest.mark.parametrize("root", [
    "c:/program files/podbye",
    "e:/my projects/podbye",
    "c:/users/u/appdata/roaming/podbye",
])
def test_a_dedicated_folder_is_adopted(root):
    assert self_paths._is_usable_root(root)


def test_a_portable_build_in_downloads_protects_nothing(monkeypatch):
    monkeypatch.setattr(self_paths, "install_dir", lambda: "c:/users/u/downloads")
    monkeypatch.setenv("APPDATA", r"C:\Users\u\AppData\Roaming")
    assert "c:/users/u/downloads" not in self_paths.self_roots()
    assert not self_paths.is_self_path("C:/Users/u/Downloads/holiday-photos.zip")


# ── detection ─────────────────────────────────────────────────────

def test_the_data_folder_becomes_one_protected_entity(fake_appdata):
    entities = ed.detect_entities(_podbye_tree(), "C:/", log_fn=lambda _m: None)
    mine = [e for e in entities
            if e.path.replace("\\", "/").lower().startswith(fake_appdata)]

    assert len(mine) == 1, f"fragmented into {[e.path for e in mine]}"
    ent = mine[0]
    assert ent.is_self
    assert ent.entity_type == "protected_system"
    assert ent.risk == "Protected"
    assert ent.actionability == "protected"


def test_the_logs_and_cache_inside_are_not_offered_separately(fake_appdata):
    """The specific failure: 'logs' as a Safe log folder, 'cache' as Safe cache."""
    entities = ed.detect_entities(_podbye_tree(), "C:/", log_fn=lambda _m: None)
    strays = [e for e in entities
              if e.path.replace("\\", "/").lower().startswith(fake_appdata + "/")]
    assert not strays, f"offered Podbye's own subfolders: {[e.path for e in strays]}"


def test_the_flag_survives_serialisation(fake_appdata):
    entities = ed.detect_entities(_podbye_tree(), "C:/", log_fn=lambda _m: None)
    ent = next(e for e in entities
               if e.path.replace("\\", "/").lower() == fake_appdata)
    assert ent.to_dict()["is_self"] is True


def test_ordinary_entities_are_not_flagged():
    assert SmartEntity(path="C:/x", name="x", entity_type="cache_folder") \
        .to_dict()["is_self"] is False


# ── the delete-time backstop ──────────────────────────────────────

@pytest.mark.parametrize("path", [
    "C:/Users/u/AppData/Roaming/Podbye",
    "C:/Users/u/AppData/Roaming/Podbye/sessions/session_b7ff978b.json",
])
def test_cleanup_refuses_podbyes_own_paths(fake_appdata, path):
    """A stale entity from an older session must not delete the running app."""
    assert cleanup_engine._is_protected_for_delete(path)


def test_cleanup_still_allows_ordinary_paths(fake_appdata):
    assert not cleanup_engine._is_protected_for_delete("C:/Users/u/Downloads/old.zip")


def test_recycling_a_self_path_is_skipped_not_attempted(fake_appdata, tmp_path):
    """End to end: the engine reports it skipped, and the file still exists."""
    victim = tmp_path / "keep_me.txt"
    victim.write_text("session data")
    import app.services.self_paths as sp
    # Treat the temp dir as Podbye's install root for this test.
    root = str(tmp_path).replace("\\", "/").lower()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sp, "self_roots", lambda: (root,))
    try:
        result = cleanup_engine.move_to_recycle_bin([str(victim)])
    finally:
        monkey.undo()

    assert result.skipped_protected == [str(victim)]
    assert not result.succeeded
    assert victim.exists(), "deleted a file belonging to Podbye"


# ── the message ───────────────────────────────────────────────────

def test_the_user_is_told_what_this_is(qapp):
    from app.screens.findings_dashboard import _finding_recommendation

    status, recommendation, evidence, _accent = _finding_recommendation(
        {"is_self": True, "risk": "Protected", "entity_type": "protected_system"})

    assert status
    assert "Podbye" in recommendation
    assert "finish" in recommendation.lower()
    assert "Podbye" in evidence


def test_a_normal_protected_item_keeps_the_plain_wording(qapp):
    from app.screens.findings_dashboard import _finding_recommendation

    _s, recommendation, _e, _a = _finding_recommendation(
        {"risk": "Protected", "entity_type": "protected_system"})
    assert "Podbye" not in recommendation
