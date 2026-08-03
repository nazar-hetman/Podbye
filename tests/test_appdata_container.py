"""AppData must never be shown as one deletable entity.

Reported: a full C:/ scan listed "AppData (Nazar) — 58 GB" at the top of
Application Data. There is no action a user can take on that row: it is not a
thing they installed, deleting it would destroy every app's settings, and its
size double-counted the hundreds of per-app entities detected inside it.

AppData and its fixed subdivisions are structure, not content. They are claimed
so no pass can turn them into a blob, but never emitted, so the per-application
folders inside them become the entities the user actually sees.
"""
import os

import pytest

from app.models.finding import Finding
from app.services.entity_detector import _is_appdata_container_dir, detect_entities


def _norm(path) -> str:
    """Normalised comparison form for a path or an entity."""
    raw = getattr(path, "path", path)
    return raw.replace("\\", "/").lower().rstrip("/")


# ── _is_appdata_container_dir ─────────────────────────────────────

@pytest.mark.parametrize("path", [
    "c:/users/nazar/appdata",
    "c:/users/nazar/appdata/local",
    "c:/users/nazar/appdata/locallow",
    "c:/users/nazar/appdata/roaming",
    "c:/users/nazar/appdata/local/programs",
    "c:/users/nazar/appdata/",            # trailing separator
])
def test_container_paths_are_structural(path):
    assert _is_appdata_container_dir(path) is True


@pytest.mark.parametrize("path", [
    "c:/users/nazar/appdata/local/discord",       # a real per-app folder
    "c:/users/nazar/appdata/roaming/code",
    "c:/users/nazar/appdata/local/programs/ollama",
    "c:/users/nazar",                             # the profile itself
    "c:/users",
    "c:/program files/someapp/appdata",           # app's own nested AppData
    "d:/backup/users/nazar/appdata",              # not a profile root
])
def test_other_paths_are_not_structural(path):
    assert _is_appdata_container_dir(path) is False


# ── end-to-end ────────────────────────────────────────────────────

def _mk(path, is_dir=False, size=0):
    norm = path.replace("\\", "/").rstrip("/")
    return Finding(
        path=path, name=os.path.basename(norm), is_dir=is_dir, size_bytes=size,
        extension="" if is_dir else os.path.splitext(norm)[1],
        modified=1700000000, accessed=1700000000,
        parent=os.path.dirname(norm),
    )


HOME = "C:/Users/Nazar"
APPDATA = f"{HOME}/AppData"
# One sizeable folder per application, the way a real profile looks.
APPS = (("Local", "Discord"), ("Local", "Spotify"),
        ("Roaming", "Code"), ("Roaming", "npm"), ("LocalLow", "NVIDIA"))


def _profile_findings():
    """A profile shaped like the real one: AppData plus diverse sibling folders.

    The siblings matter. With AppData as the only child, the profile itself is
    grouped into one blob and AppData never gets the chance to become the 60 GB
    row that was reported — the bug would hide behind an unrealistic fixture.
    """
    findings = [
        _mk("C:/", is_dir=True),
        _mk("C:/Users", is_dir=True),
        _mk(HOME, is_dir=True),
        _mk(APPDATA, is_dir=True),
        _mk(f"{APPDATA}/Local", is_dir=True),
        _mk(f"{APPDATA}/Roaming", is_dir=True),
        _mk(f"{APPDATA}/LocalLow", is_dir=True),
    ]
    for sub, app in APPS:
        findings.append(_mk(f"{APPDATA}/{sub}/{app}", is_dir=True))
        for i in range(3):
            findings.append(
                _mk(f"{APPDATA}/{sub}/{app}/data{i}.bin", size=200_000_000))
    # Diverse profile siblings so the home dir explodes, as it does in a scan.
    for folder, name, size in (("Documents", "report.pdf", 4_000_000),
                               ("Pictures", "holiday.jpg", 6_000_000),
                               ("Videos", "clip.mp4", 90_000_000),
                               ("Music", "track.mp3", 8_000_000)):
        findings.append(_mk(f"{HOME}/{folder}", is_dir=True))
        findings.append(_mk(f"{HOME}/{folder}/{name}", size=size))
    return findings


def test_appdata_root_never_becomes_an_entity():
    ents = detect_entities(_profile_findings(), "C:/", log_fn=lambda s: None)
    containers = {_norm(APPDATA), _norm(f"{APPDATA}/Local"),
                  _norm(f"{APPDATA}/Roaming"), _norm(f"{APPDATA}/LocalLow")}
    blobs = [e for e in ents if _norm(e) in containers]
    assert not blobs, (
        "AppData was shown as a single entity: "
        + ", ".join(f"{e.name} ({e.size_bytes} B)" for e in blobs)
    )


def test_per_application_folders_are_shown_instead():
    ents = detect_entities(_profile_findings(), "C:/", log_fn=lambda s: None)
    paths = {_norm(e) for e in ents}
    for sub, app in APPS[:4]:
        assert _norm(f"{APPDATA}/{sub}/{app}") in paths, f"{app} was swallowed"


def test_nothing_inside_appdata_is_lost():
    """Splitting the container must not drop bytes on the floor."""
    findings = _profile_findings()
    ents = detect_entities(findings, "C:/", log_fn=lambda s: None)
    assert sum(e.size_bytes for e in ents) >= sum(f.size_bytes for f in findings)


def test_programs_container_is_split_into_installed_apps():
    """AppData/Local/Programs is the profile's Program Files — same rule."""
    programs = f"{APPDATA}/Local/Programs"
    findings = _profile_findings() + [_mk(programs, is_dir=True)]
    for app in ("Ollama", "Microsoft VS Code", "LM Studio"):
        findings.append(_mk(f"{programs}/{app}", is_dir=True))
        findings.append(_mk(f"{programs}/{app}/app.exe", size=500_000_000))
    ents = detect_entities(findings, "C:/", log_fn=lambda s: None)
    paths = {_norm(e) for e in ents}
    assert _norm(programs) not in paths, "Programs was shown as one blob"
    assert _norm(f"{programs}/Ollama") in paths
