"""Browser Data must contain browsers, and nothing that merely says "chrome".

Reported from a full C:/ scan: a wall of near-identical rows like

    AppData/Roaming/Windsurf/CachedData/abcd9c86…/chrome
    AppData/Roaming/Code/CachedData/8a7abeba…/chrome

all shown as "Chrome Data" at Review. Every Electron app embeds Chromium, so
every one keeps a folder literally named "chrome" holding V8's compiled-code
cache, one copy per app build. Measured on the reporting machine: **17 such
rows** (10 Windsurf, 7 VS Code) claiming to hold passwords, cookies, history and
bookmarks. They hold compiled JavaScript.

Two more crept in the same way, by substring: "EdgeJourneys" and "EdgeEDrop"
(Copilot's own folders) and an extension's "chrome-extension_….leveldb".

After the fix, Browser Data on that machine is three rows — Chrome, Vivaldi,
Edge — and the Electron caches are two Safe rows in Cache & Temp worth 348 MB.
"""
import os

import pytest

from app.services import entity_detector as ed
from app.models.finding import Finding

MB = 1024 * 1024


def _f(path, is_dir=False, size=0, ext="", parent=""):
    return Finding(path=path, name=os.path.basename(path), is_dir=is_dir,
                   size_bytes=size, extension=ext, modified=1, accessed=1,
                   parent=parent)


def _child_dirs(*names):
    return [_f(n, is_dir=True) for n in names]


# ── naming: a browser must be named by a whole path segment ───────

@pytest.mark.parametrize("path,expected", [
    ("c:/users/u/appdata/local/google/chrome", "chrome"),
    ("c:/users/u/appdata/local/microsoft/edge", "edge"),
    ("c:/users/u/appdata/local/vivaldi", "vivaldi"),
    ("c:/users/u/appdata/roaming/mozilla/firefox/profiles/x.default", "firefox"),
    ("c:/users/u/appdata/local/google/chrome/user data/default", "chrome"),
])
def test_real_browsers_are_named(path, expected):
    assert ed._browser_from_path(path) == expected


@pytest.mark.parametrize("path", [
    # Copilot's own data folders — reported as 0.0 MB "Edge Data" rows.
    "c:/users/u/appdata/local/microsoft/copilot/user data/default/edgejourneys",
    "c:/users/u/appdata/local/microsoft/copilot/user data/default/edgeedrop",
    "c:/users/u/appdata/local/microsoft/copilot/user data/default/edgehubappusage",
    # One extension's IndexedDB store, reported as "Chrome Data".
    "c:/users/u/appdata/local/rmmz-game/user data/default/indexeddb/"
    "chrome-extension_njgcanhfjdabfmnlmpmdedalocpafnhl_0.indexeddb.leveldb",
    # A folder that just happens to be called this in a project.
    "e:/work/site/src/styles/chrome-ish",
])
def test_lookalikes_are_not_browsers(path):
    assert ed._browser_from_path(path) == ""


# ── evidence: a name is not a profile ─────────────────────────────

def test_an_electron_code_cache_is_not_profile_evidence():
    """The reported path, with the children it really has."""
    path = ("c:/users/u/appdata/roaming/windsurf/cacheddata/"
            "abcd9c8664da5af505557f3b327b5537400635f2/chrome")
    assert not ed._has_browser_profile_evidence(path, _child_dirs("js", "wasm"))


@pytest.mark.parametrize("path,children", [
    # Holds the container the browser creates.
    ("c:/users/u/appdata/local/google/chrome", ["User Data"]),
    ("c:/users/u/appdata/roaming/mozilla/firefox", ["Profiles"]),
    # Sits inside one.
    ("c:/users/u/appdata/local/google/chrome/user data/default", ["IndexedDB"]),
])
def test_profile_containers_count_as_evidence(path, children):
    assert ed._has_browser_profile_evidence(path, _child_dirs(*children))


@pytest.mark.parametrize("marker", [
    "Preferences", "Cookies", "History", "Login Data", "Bookmarks",
    "places.sqlite", "prefs.js", "logins.json",
])
def test_the_files_a_browser_writes_count_as_evidence(marker):
    assert ed._has_browser_profile_evidence(
        "c:/somewhere/chrome", _child_dirs(marker))


def test_a_bare_browser_name_is_not_evidence():
    assert not ed._has_browser_profile_evidence(
        "c:/somewhere/chrome", _child_dirs("js", "wasm", "assets"))


# ── detection, on the reported folder shape ───────────────────────

def _electron_tree(app_folder, n_builds, per_build_mb):
    """AppData/Roaming/<App>/CachedData/<40-hex>/chrome/{js,wasm} — the real shape."""
    base = f"C:/Users/u/AppData/Roaming/{app_folder}"
    cached = f"{base}/CachedData"
    out = [
        _f("C:/Users", is_dir=True, parent="C:/"),
        _f("C:/Users/u", is_dir=True, parent="C:/Users"),
        _f("C:/Users/u/AppData", is_dir=True, parent="C:/Users/u"),
        _f("C:/Users/u/AppData/Roaming", is_dir=True, parent="C:/Users/u/AppData"),
        _f(base, is_dir=True, parent="C:/Users/u/AppData/Roaming"),
        _f(cached, is_dir=True, parent=base),
    ]
    for i in range(n_builds):
        h = f"{i:040x}"
        build = f"{cached}/{h}"
        chrome = f"{build}/chrome"
        out += [_f(build, is_dir=True, parent=cached),
                _f(chrome, is_dir=True, parent=build)]
        for sub in ("js", "wasm"):
            d = f"{chrome}/{sub}"
            out.append(_f(d, is_dir=True, parent=chrome))
            out.append(_f(f"{d}/blob.bin", size=per_build_mb * MB // 2,
                          ext=".bin", parent=d))
    return out


def test_an_apps_builds_collapse_into_one_cache_row():
    """Ten build folders became ten "Chrome Data" rows; they are one cache."""
    entities = ed.detect_entities(_electron_tree("Windsurf", 10, 25),
                                  "C:/", log_fn=lambda _m: None)
    mine = [e for e in entities if "cacheddata" in e.path.replace("\\", "/").lower()]

    assert len(mine) == 1, f"still fragmented: {[e.path for e in mine]}"
    ent = mine[0]
    assert ent.category == "Cache & Temp"
    assert ent.entity_type == "cache_folder"
    assert ent.name == "Windsurf · code cache"
    assert "10" in ent.risk_reason, "does not say how many builds are kept"


def test_the_row_is_not_called_chrome_and_not_browser_data():
    entities = ed.detect_entities(_electron_tree("Windsurf", 10, 25),
                                  "C:/", log_fn=lambda _m: None)
    assert not [e for e in entities if e.category == "Browser Data"]
    assert not [e for e in entities if "Chrome" in e.name]


def test_vs_code_gets_its_product_name():
    """The folder is called "Code"; nobody calls the app that."""
    entities = ed.detect_entities(_electron_tree("Code", 7, 14),
                                  "C:/", log_fn=lambda _m: None)
    mine = [e for e in entities if "cacheddata" in e.path.replace("\\", "/").lower()]
    assert len(mine) == 1
    assert mine[0].name == "VS Code · code cache"


def test_the_cache_is_offered_for_cleanup():
    """It is genuinely regenerable — 348 MB of it on the reporting machine."""
    entities = ed.detect_entities(_electron_tree("Windsurf", 10, 25),
                                  "C:/", log_fn=lambda _m: None)
    ent = next(e for e in entities
               if "cacheddata" in e.path.replace("\\", "/").lower())
    assert ent.risk == "Safe"
    assert ent.actionability == "recycle"


def test_a_cacheddata_folder_without_build_hashes_is_left_alone():
    """The rule keys on content-addressed build folders, not the name."""
    base = "C:/Users/u/AppData/Roaming/SomeApp/CachedData"
    findings = [
        _f("C:/Users", is_dir=True, parent="C:/"),
        _f("C:/Users/u", is_dir=True, parent="C:/Users"),
        _f("C:/Users/u/AppData", is_dir=True, parent="C:/Users/u"),
        _f("C:/Users/u/AppData/Roaming", is_dir=True, parent="C:/Users/u/AppData"),
        _f("C:/Users/u/AppData/Roaming/SomeApp", is_dir=True,
           parent="C:/Users/u/AppData/Roaming"),
        _f(base, is_dir=True, parent="C:/Users/u/AppData/Roaming/SomeApp"),
        _f(f"{base}/notes", is_dir=True, parent=base),
        _f(f"{base}/notes/a.txt", size=2 * MB, ext=".txt", parent=f"{base}/notes"),
    ]
    entities = ed.detect_entities(findings, "C:/", log_fn=lambda _m: None)
    assert not [e for e in entities if e.name.endswith("· code cache")]


# ── the real thing still works ────────────────────────────────────

def test_a_real_chrome_profile_is_still_browser_data():
    """The fix must not stop Podbye seeing an actual browser profile."""
    base = "C:/Users/u/AppData/Local/Google/Chrome"
    ud = f"{base}/User Data/Default"
    findings = [
        _f("C:/Users", is_dir=True, parent="C:/"),
        _f("C:/Users/u", is_dir=True, parent="C:/Users"),
        _f("C:/Users/u/AppData", is_dir=True, parent="C:/Users/u"),
        _f("C:/Users/u/AppData/Local", is_dir=True, parent="C:/Users/u/AppData"),
        _f("C:/Users/u/AppData/Local/Google", is_dir=True,
           parent="C:/Users/u/AppData/Local"),
        _f(base, is_dir=True, parent="C:/Users/u/AppData/Local/Google"),
        _f(f"{base}/User Data", is_dir=True, parent=base),
        _f(ud, is_dir=True, parent=f"{base}/User Data"),
    ]
    for n in ["Preferences", "Cookies", "History", "Login Data", "Bookmarks"]:
        findings.append(_f(f"{ud}/{n}", size=8 * MB, parent=ud))

    entities = ed.detect_entities(findings, "C:/", log_fn=lambda _m: None)
    profiles = [e for e in entities if e.entity_type == "browser_profile"]

    assert profiles, "lost a real Chrome profile"
    assert profiles[0].name == "Chrome Data"
    assert profiles[0].category == "Browser Data"


def test_a_real_profile_is_one_row_not_one_per_subfolder():
    """Leaves used to win over their profile root; the root must claim first."""
    base = "C:/Users/u/AppData/Local/Google/Chrome"
    ud = f"{base}/User Data/Default"
    findings = [
        _f("C:/Users", is_dir=True, parent="C:/"),
        _f("C:/Users/u", is_dir=True, parent="C:/Users"),
        _f("C:/Users/u/AppData", is_dir=True, parent="C:/Users/u"),
        _f("C:/Users/u/AppData/Local", is_dir=True, parent="C:/Users/u/AppData"),
        _f("C:/Users/u/AppData/Local/Google", is_dir=True,
           parent="C:/Users/u/AppData/Local"),
        _f(base, is_dir=True, parent="C:/Users/u/AppData/Local/Google"),
        _f(f"{base}/User Data", is_dir=True, parent=base),
        _f(ud, is_dir=True, parent=f"{base}/User Data"),
        _f(f"{ud}/Preferences", size=8 * MB, parent=ud),
    ]
    for sub in ["IndexedDB", "Local Storage", "Sync Data"]:
        d = f"{ud}/{sub}"
        findings.append(_f(d, is_dir=True, parent=ud))
        findings.append(_f(f"{d}/data.bin", size=6 * MB, ext=".bin", parent=d))

    entities = ed.detect_entities(findings, "C:/", log_fn=lambda _m: None)
    profiles = [e for e in entities if e.entity_type == "browser_profile"]
    assert len(profiles) == 1, f"fragmented: {[e.path for e in profiles]}"
