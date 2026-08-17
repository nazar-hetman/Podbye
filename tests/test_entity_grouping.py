"""One app should be one row.

A real All-drives scan produced 1,241 rows. Discord alone was 23 of them —
`shared_proto_db`, `Session Storage`, `Network`, `WidevineCdm`, `Crashpad`,
four separate `node_modules` — none of which is a decision a person can make.
`AppData/Local/Packages` produced ~120 rows and `Program Files/dotnet` 43.

Grouping folds those under the app that owns them. It never hides anything:
the members stay in the group and the caller still renders them.
"""
import pytest

from app.models.entity_grouping import (
    build_app_index, group_entities, group_label, merge_key, owner_key,
)


def _e(path, size=0, files=0, name=""):
    return {"path": path, "size_bytes": size, "file_count": files,
            "name": name or path.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]}


# ── owner resolution ──────────────────────────────────────────────


@pytest.mark.parametrize("path,expected", [
    (r"C:\Users\n\AppData\Roaming\discord\Network",
     "C:/Users/n/AppData/Roaming/discord"),
    (r"C:\Users\n\AppData\Roaming\discord",
     "C:/Users/n/AppData/Roaming/discord"),
    (r"C:\Program Files\dotnet\sdk\8.0.100",
     "C:/Program Files/dotnet"),
    # A nested container is one level deeper: the Store package IS the app,
    # so every package must not collapse into a single "Packages" bucket.
    (r"C:\Users\n\AppData\Local\Packages\Microsoft.Paint_8we\LocalCache",
     "C:/Users/n/AppData/Local/Packages/Microsoft.Paint_8we"),
    (r"C:\Users\n\AppData\Local\Programs\Cursor\resources",
     "C:/Users/n/AppData/Local/Programs/Cursor"),
    # Nothing to attach to.
    (r"D:\photos\2024\summer", ""),
])
def test_owner_is_the_app_folder(path, expected):
    assert owner_key(path) == expected


def test_registry_beats_path_shape_and_wins_longest_match():
    """The Uninstall table knows the real boundary; a suite must not swallow
    the specific product installed underneath it."""
    index = {
        "d:/games/launcher": "Some Launcher",
        "d:/games/launcher/titles/stardew": "Stardew Valley",
    }
    assert owner_key(r"D:\Games\Launcher\titles\stardew\saves", index) \
        == "D:/Games/Launcher/titles/stardew"
    assert owner_key(r"D:\Games\Launcher\config", index) == "D:/Games/Launcher"


def test_an_over_broad_install_location_is_ignored():
    """Installers routinely record a container instead of their own folder.
    Honouring `C:\\Program Files` would put every program in one group."""
    index = build_app_index({
        "c:/program files": {"name": "Sloppy Installer"},
        "c:/": {"name": "Worse Installer"},
        "c:/program files/git": {"name": "Git"},
    })
    assert "c:/program files" not in index
    assert "c:/" not in index
    assert index["c:/program files/git"] == "Git"


# ── grouping ──────────────────────────────────────────────────────


def test_one_app_becomes_one_group():
    rows = [
        _e(r"C:\Users\n\AppData\Roaming\discord", 200),
        _e(r"C:\Users\n\AppData\Roaming\discord\Network", 100),
        _e(r"C:\Users\n\AppData\Roaming\discord\shared_proto_db", 50),
        _e(r"D:\photos", 900),
    ]
    groups = group_entities(rows, app_index={})
    assert len(groups) == 2
    discord = groups[0]
    assert discord["root"] is not None, "the folder itself should head its group"
    assert len(discord["members"]) == 2
    assert discord["size_bytes"] == 350


def test_local_and_roaming_are_the_same_app():
    """An app owns both AppData folders; two "Discord" rows is one too many."""
    rows = [
        _e(r"C:\Users\n\AppData\Roaming\discord\Network", 10),
        _e(r"C:\Users\n\AppData\Local\Discord\app-1.0", 20),
    ]
    groups = group_entities(rows, app_index={})
    assert len(groups) == 1
    assert groups[0]["size_bytes"] == 30


def test_packages_and_programs_do_not_merge_by_bare_name():
    """Only a *direct* child of a per-user container merges on its name.
    "Packages" is not an app, so its children must stay separate."""
    a = merge_key("C:/Users/n/AppData/Local/Packages/App.One_x")
    b = merge_key("C:/Users/n/AppData/Local/Packages/App.Two_y")
    assert a != b


def test_nothing_is_dropped():
    """Grouping reorganises; it must never lose a row. The user asked for
    small items to stay visible so they can be tidied, not hidden."""
    rows = [_e(f"C:/Users/n/AppData/Roaming/app/{i}", i) for i in range(20)]
    rows += [_e(r"D:\loose\thing", 5)]
    groups = group_entities(rows, app_index={})
    seen = sum(len(g["members"]) + (1 if g["root"] else 0) for g in groups)
    assert seen == len(rows)


def test_unowned_entities_stay_one_row_each():
    """A row with no owner must not be merged with other unowned rows."""
    rows = [_e(r"D:\a", 1), _e(r"E:\b", 2), _e(r"F:\c", 3)]
    groups = group_entities(rows, app_index={})
    assert len(groups) == 3


def test_group_order_follows_the_incoming_sort():
    """The caller sorts, then groups — so the sort must survive grouping."""
    rows = [
        _e(r"D:\big", 900),
        _e(r"C:\Users\n\AppData\Roaming\app\x", 50),
        _e(r"C:\Users\n\AppData\Roaming\app\y", 40),
        _e(r"D:\small", 1),
    ]
    groups = group_entities(rows, app_index={})
    assert [g["size_bytes"] for g in groups] == [900, 90, 1]


# ── naming ────────────────────────────────────────────────────────


def test_registry_display_name_is_preferred():
    """"Visual Studio Code", not the folder name "Code"."""
    index = {"c:/users/n/appdata/local/programs/code": "Visual Studio Code"}
    rows = [
        _e(r"C:\Users\n\AppData\Local\Programs\Code", 10),
        _e(r"C:\Users\n\AppData\Local\Programs\Code\resources", 20),
    ]
    groups = group_entities(rows, app_index=index)
    assert group_label(groups[0]) == "Visual Studio Code"


def test_label_falls_back_to_the_folder_name():
    rows = [
        _e(r"C:\Users\n\AppData\Roaming\obs-studio\a", 10),
        _e(r"C:\Users\n\AppData\Roaming\obs-studio\b", 20),
    ]
    groups = group_entities(rows, app_index={})
    assert group_label(groups[0]) == "obs-studio"
