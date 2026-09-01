"""Per-game save engines (Ren'Py, LÖVE, …) become clean game_saves entities.

Regression for the reported mess: %APPDATA%/RenPy/<Game>-<buildid> folders
showed as "Misc files in MyOfficeAdventures-1602343789" (mixed_folder) instead
of a named save entity with install status.
"""
import os

import pytest

from app.services import entity_detector as ed
from app.services.entity_detector import _clean_game_name
from app.models.finding import Finding

MB = 1024 * 1024


def _f(path, is_dir=False, size=0, ext="", parent=""):
    return Finding(path=path, name=os.path.basename(path), is_dir=is_dir,
                   size_bytes=size, extension=ext, modified=1, accessed=1,
                   parent=parent)


def _renpy_tree():
    base = "C:/Users/ExampleUser/AppData/Roaming/RenPy"
    f = [_f("C:/Users/ExampleUser/AppData/Roaming", is_dir=True, parent="C:/Users/ExampleUser/AppData"),
         _f(base, is_dir=True, parent="C:/Users/ExampleUser/AppData/Roaming")]
    for game in ("MyOfficeAdventures-1602343789", "GOGOPIZZABOY-1693749163"):
        g = f"{base}/{game}"
        f.append(_f(g, is_dir=True, parent=base))
        f.append(_f(f"{g}/sync", is_dir=True, parent=g))
        f += [_f(f"{g}/{i}.save", size=MB, ext=".save", parent=g) for i in range(3)]
        f.append(_f(f"{g}/persistent", size=MB, ext="", parent=g))
    return f


# ── name cleaning ────────────────────────────────────────────────


@pytest.mark.parametrize("raw, clean", [
    ("MyOfficeAdventures-1602343789", "MyOfficeAdventures"),
    ("GOGOPIZZABOY-1693749163", "GOGOPIZZABOY"),
    ("Game_1717618128", "Game"),
    ("PlainName", "PlainName"),
    ("Half-Life", "Half-Life"),          # short number-less hyphen kept
    ("Portal-2", "Portal-2"),            # 1 digit is not a build id
])
def test_clean_game_name(raw, clean):
    assert _clean_game_name(raw) == clean


# ── classification + enrichment ──────────────────────────────────


def test_renpy_folders_become_named_game_saves():
    entities = ed.detect_entities(_renpy_tree(), "C:/", log_fn=lambda _m: None)
    saves = {e.name: e for e in entities if e.entity_type == "game_saves"}
    assert "MyOfficeAdventures" in saves
    assert "GOGOPIZZABOY" in saves
    # the build-id suffix must be gone from the display name
    assert not any("160234" in e.name for e in entities)


def test_no_leftover_misc_blob_for_renpy_games():
    entities = ed.detect_entities(_renpy_tree(), "C:/", log_fn=lambda _m: None)
    blobs = [e for e in entities
             if e.entity_type == "mixed_folder" and "renpy" in e.path.lower()]
    assert blobs == []


def test_save_entity_reports_install_status():
    entities = ed.detect_entities(_renpy_tree(), "C:/", log_fn=lambda _m: None)
    e = next(e for e in entities
             if e.entity_type == "game_saves" and e.name == "MyOfficeAdventures")
    # These games aren't installed in this synthetic scan.
    assert "not installed" in e.risk_reason.lower()


def test_sync_subfolder_absorbed_into_game():
    """The per-game folder is claimed whole, so its 'sync' subfolder is not a
    separate entity."""
    entities = ed.detect_entities(_renpy_tree(), "C:/", log_fn=lambda _m: None)
    strays = [e for e in entities if e.name.lower() == "sync"]
    assert strays == []
