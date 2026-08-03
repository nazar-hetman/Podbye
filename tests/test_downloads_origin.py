"""One folder, one place to look at it.

Reported from a full C:/ scan: "odd that we have downloads section - and also
showing files from downloads in other section - should be something ones".

Both halves were true. _pass_downloads claims each subfolder of Downloads as a
download_item (category Downloads), and the loose files left behind then fall
through the type bucketer: archives to Archives, .exe/.msi to Installers,
everything else to Unknown. Measured on the reporting machine's real Downloads
folder, its 11 entities were spread across **six** categories, and no view
showed the user their Downloads.

The mix-up is one axis answering two questions. Archives / Installers / Images
answer "what is it"; Downloads answers "where is it". Location now wins where a
location was meant — and only for grouping: entity_type still drives risk,
actionability and the detail panel.
"""
import os

import pytest

from app.services import entity_detector as ed
from app.models.finding import Finding
from app.models.smart_entity import SmartEntity, actionability_for_type

MB = 1024 * 1024
DL = "C:/Users/u/Downloads"


def _f(path, is_dir=False, size=0, ext="", parent=""):
    return Finding(path=path, name=os.path.basename(path), is_dir=is_dir,
                   size_bytes=size, extension=ext, modified=1, accessed=1,
                   parent=parent)


# ── which folders count as the user's Downloads ───────────────────

@pytest.mark.parametrize("path,expected", [
    ("c:/users/u/downloads", "c:/users/u/downloads"),
    ("c:/users/u/downloads/thing.zip", "c:/users/u/downloads"),
    ("c:/users/u/downloads/sub/deep/file.iso", "c:/users/u/downloads"),
    ("c:/downloads/x.iso", "c:/downloads"),          # a Downloads at a drive root
    ("d:/download/x.iso", "d:/download"),            # singular spelling
])
def test_the_users_downloads_is_recognised(path, expected):
    assert ed._download_root_of(path) == expected
    assert ed._origin_root_of(path) == (expected, "Downloads")


@pytest.mark.parametrize("path", [
    # An app's own staging folder is not the user's Downloads. Seven of these
    # exist on the reporting machine; none may pull its contents into the view.
    "c:/users/u/appdata/roaming/someapp/downloads/cache.bin",
    "c:/users/u/appdata/local/programs/tool/download/tmp",
    "d:/games/steam/downloads/depot",
    "c:/program files/vendor/downloads/pkg.dat",
    # Nothing to do with downloads at all.
    "c:/users/u/documents/report.pdf",
])
def test_an_apps_internal_download_folder_is_not(path):
    assert ed._download_root_of(path) == ""
    assert ed._origin_root_of(path) == ("", "")


# ── the category override ─────────────────────────────────────────

@pytest.mark.parametrize("etype", [
    "archive_group", "installer", "document_folder", "mixed_folder",
    "media_collection", "download_item", "unknown_folder",
])
def test_anything_in_downloads_is_filed_under_downloads(etype):
    e = SmartEntity(path=f"{DL}/thing", name="thing", entity_type=etype,
                    origin="Downloads")
    assert e.category == "Downloads"


@pytest.mark.parametrize("etype,expected", [
    ("protected_system", "System"),      # protection must stay visible
    ("duplicate_group", "Duplicates"),   # spans several locations at once
])
def test_protection_and_duplicates_keep_their_category(etype, expected):
    e = SmartEntity(path=f"{DL}/thing", name="thing", entity_type=etype,
                    origin="Downloads")
    assert e.category == expected


def test_without_an_origin_the_type_still_decides():
    e = SmartEntity(path="C:/elsewhere/x", name="x", entity_type="archive_group")
    assert e.category == "Archives"


def test_the_origin_survives_serialisation():
    e = SmartEntity(path=f"{DL}/x", name="x", entity_type="installer",
                    origin="Downloads")
    d = e.to_dict()
    assert d["origin"] == "Downloads"
    assert d["category"] == "Downloads"


# ── risk and actions must not move with the category ──────────────

def test_regrouping_does_not_change_what_an_item_is():
    """An installer in Downloads is still an installer: same risk, same action."""
    here = SmartEntity(path=f"{DL}/setup-1.2.3-x64.exe", name="Installer (setup)",
                       entity_type="installer", origin="Downloads")
    away = SmartEntity(path="C:/Stash/setup-1.2.3-x64.exe", name="Installer (setup)",
                       entity_type="installer")

    assert here.category == "Downloads" and away.category == "Installers"
    assert here.risk == away.risk
    assert here.entity_type == away.entity_type
    assert (actionability_for_type(here.entity_type, here.risk)
            == actionability_for_type(away.entity_type, away.risk))


# ── end to end, on the reported folder shape ──────────────────────

def _downloads_tree():
    """The real mix: an extracted folder, loose archives, installers, misc."""
    out = [
        _f("C:/Users", is_dir=True, parent="C:/"),
        _f("C:/Users/u", is_dir=True, parent="C:/Users"),
        _f(DL, is_dir=True, parent="C:/Users/u"),
        # an extracted download, claimed whole as one item
        _f(f"{DL}/Mission Planner 1", is_dir=True, parent=DL),
        _f(f"{DL}/Mission Planner 1/app.exe", size=40 * MB, ext=".exe",
           parent=f"{DL}/Mission Planner 1"),
        # loose files of every kind that used to scatter
        _f(f"{DL}/big-archive.zip", size=830 * MB, ext=".zip", parent=DL),
        _f(f"{DL}/another.7z", size=120 * MB, ext=".7z", parent=DL),
        _f(f"{DL}/VSCodeUserSetup-x64.exe", size=95 * MB, ext=".exe", parent=DL),
        _f(f"{DL}/vlc-3.0.20-win64.exe", size=40 * MB, ext=".exe", parent=DL),
        _f(f"{DL}/manual.pdf", size=250 * MB, ext=".pdf", parent=DL),
        _f(f"{DL}/notes.md", size=1 * MB, ext=".md", parent=DL),
        _f(f"{DL}/clip.mp4", size=20 * MB, ext=".mp4", parent=DL),
    ]
    return out


def test_the_whole_folder_lands_in_one_category():
    entities = ed.detect_entities(_downloads_tree(), "C:/", log_fn=lambda _m: None)
    mine = [e for e in entities
            if e.path.replace("\\", "/").lower().startswith(DL.lower())]

    assert mine, "the Downloads folder produced no entities at all"
    categories = {e.category for e in mine}
    assert categories == {"Downloads"}, f"still scattered across {sorted(categories)}"


def test_nothing_from_downloads_shows_up_under_archives_or_installers():
    entities = ed.detect_entities(_downloads_tree(), "C:/", log_fn=lambda _m: None)
    strays = [(e.category, e.path) for e in entities
              if e.category in ("Archives", "Installers", "Unknown", "Documents")
              and "downloads" in e.path.replace("\\", "/").lower()]
    assert not strays, f"leaked out of Downloads: {strays}"


def test_the_types_underneath_are_still_varied():
    """Grouping them together must not have flattened what they are."""
    entities = ed.detect_entities(_downloads_tree(), "C:/", log_fn=lambda _m: None)
    mine = [e for e in entities
            if e.path.replace("\\", "/").lower().startswith(DL.lower())]
    assert len({e.entity_type for e in mine}) > 1, \
        f"every entity became the same type: {[e.entity_type for e in mine]}"


def test_an_apps_download_folder_is_untouched_end_to_end():
    base = "C:/Users/u/AppData/Roaming/SomeApp/Downloads"
    findings = [
        _f("C:/Users", is_dir=True, parent="C:/"),
        _f("C:/Users/u", is_dir=True, parent="C:/Users"),
        _f("C:/Users/u/AppData", is_dir=True, parent="C:/Users/u"),
        _f("C:/Users/u/AppData/Roaming", is_dir=True, parent="C:/Users/u/AppData"),
        _f("C:/Users/u/AppData/Roaming/SomeApp", is_dir=True,
           parent="C:/Users/u/AppData/Roaming"),
        _f(base, is_dir=True, parent="C:/Users/u/AppData/Roaming/SomeApp"),
        _f(f"{base}/pkg.zip", size=200 * MB, ext=".zip", parent=base),
        _f(f"{base}/pkg2.zip", size=200 * MB, ext=".zip", parent=base),
    ]
    entities = ed.detect_entities(findings, "C:/", log_fn=lambda _m: None)
    assert not [e for e in entities if e.category == "Downloads"]


# ── Desktop, the same problem in the same shape ───────────────────
#
# Measured on the reporting machine: 2,298 findings on the Desktop produced 9
# entities spread over four categories, with 0.41 GB of it filed as "Unknown" —
# on a folder whose contents the user can literally see on screen.

DESK = "C:/Users/u/Desktop"


@pytest.mark.parametrize("path,expected", [
    ("c:/users/u/desktop", "c:/users/u/desktop"),
    ("c:/users/u/desktop/report.docx", "c:/users/u/desktop"),
    ("c:/users/public/desktop/shared.lnk", "c:/users/public/desktop"),
])
def test_the_desktop_is_recognised(path, expected):
    assert ed._origin_root_of(path) == (expected, "Desktop")


@pytest.mark.parametrize("path", [
    "c:/users/u/appdata/roaming/someapp/desktop/layout.ini",
    "d:/projects/ui/desktop/main.qml",
])
def test_an_apps_internal_desktop_folder_is_not(path):
    assert ed._origin_root_of(path) == ("", "")


def _desktop_tree():
    """A Desktop holding the usual junk drawer, in a realistic profile.

    The profile needs several diverse subfolders or the heterogeneous-root
    exploder never fires: a home with one or two children stays a single "User
    Profile" blob that swallows the Desktop, and no Desktop entity exists to
    categorise at all.
    """
    out = [_f("C:/Users", is_dir=True, parent="C:/"),
           _f("C:/Users/u", is_dir=True, parent="C:/Users")]
    folders = {
        "Desktop": [("report.pdf", ".pdf", 30), ("photo.jpg", ".jpg", 20),
                    ("notes.md", ".md", 2), ("tool.exe", ".exe", 90),
                    ("archive.zip", ".zip", 400)],
        "Documents": [("thesis.docx", ".docx", 5)],
        "Pictures": [("holiday.jpg", ".jpg", 10)],
        "Videos": [("clip.mp4", ".mp4", 50)],
        "Music": [("song.mp3", ".mp3", 5)],
    }
    for sub, files in folders.items():
        d = f"C:/Users/u/{sub}"
        out.append(_f(d, is_dir=True, parent="C:/Users/u"))
        for name, ext, mb in files:
            out.append(_f(f"{d}/{name}", size=mb * MB, ext=ext, parent=d))
    return out


def test_the_desktop_lands_in_one_category():
    entities = ed.detect_entities(_desktop_tree(), "C:/", log_fn=lambda _m: None)
    mine = [e for e in entities
            if e.path.replace("\\", "/").lower().startswith(DESK.lower())]

    assert mine, "the Desktop produced no entities at all"
    assert {e.category for e in mine} == {"Desktop"}


def test_downloads_and_desktop_do_not_bleed_into_each_other():
    seen = {"Downloads": 0, "Desktop": 0}
    dl_only = [f for f in _downloads_tree() if f.path not in ("C:/Users", "C:/Users/u")]
    entities = ed.detect_entities(_desktop_tree() + dl_only, "C:/",
                                  log_fn=lambda _m: None)
    for e in entities:
        norm = e.path.replace("\\", "/").lower()
        if norm.startswith(DL.lower()):
            assert e.category == "Downloads", f"{e.path} -> {e.category}"
            seen["Downloads"] += 1
        elif norm.startswith(DESK.lower()):
            assert e.category == "Desktop", f"{e.path} -> {e.category}"
            seen["Desktop"] += 1
    assert all(seen.values()), f"nothing to check on one side: {seen}"


@pytest.mark.parametrize("code", ["uk", "fr"])
def test_the_new_category_name_is_translated(code):
    """Category names reach tr() as a variable, so no static scan catches them."""
    import io, json, pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "locales"
    table = json.load(io.open(root / f"{code}.json", encoding="utf-8"))
    for name in ("Downloads", "Desktop"):
        assert table.get(name), f"{code}: category {name!r} is untranslated"
