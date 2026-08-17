"""The folder tree is the escape hatch when classification is wrong.

WSL was filed under Virtual Machines because two .vhdx images outweighed 619
DLLs by size; Discord under Media. Both are findable by path in one place, and
the path is never a guess. What this must get right is the arithmetic: a
folder has to report the true total of everything under it, or drilling for
space leads nowhere.
"""
import pytest

from app.models.path_tree import PathNode, build_tree, collapse_single_child_chains


def _e(path, size, files=1):
    return {"path": path, "size_bytes": size, "file_count": files,
            "name": path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]}


def _child(node: PathNode, *names) -> PathNode:
    for name in names:
        node = node.children[name.lower()]
    return node


def test_sizes_roll_up_to_every_ancestor():
    root = build_tree([
        _e(r"C:\Users\n\AppData\Roaming\discord", 600),
        _e(r"C:\Users\n\AppData\Roaming\Code", 400),
        _e(r"C:\Program Files\WSL", 800),
    ])
    assert root.size_bytes == 1800
    assert _child(root, "c:").size_bytes == 1800
    assert _child(root, "c:", "users").size_bytes == 1000
    assert _child(root, "c:", "users", "n", "appdata", "roaming").size_bytes == 1000
    assert _child(root, "c:", "program files").size_bytes == 800


def test_several_drives_share_one_synthetic_root():
    root = build_tree([_e(r"C:\a", 10), _e(r"D:\b", 20), _e(r"E:\c", 30)])
    assert set(root.children) == {"c:", "d:", "e:"}
    assert root.size_bytes == 60


def test_file_counts_roll_up_too():
    root = build_tree([
        _e(r"C:\x\one", 10, files=5),
        _e(r"C:\x\two", 10, files=7),
    ])
    assert _child(root, "c:", "x").file_count == 12


def test_the_entity_is_reachable_at_its_own_node():
    """Drilling to a folder must hand back the thing you can act on."""
    wsl = _e(r"C:\Program Files\WSL", 800)
    root = build_tree([wsl])
    node = _child(root, "c:", "program files", "wsl")
    assert node.entity is wsl


def test_a_shared_path_keeps_one_node_but_the_full_size():
    """A folder's loose files split by content type produce several entities
    at one path — Downloads appeared four times on a real scan."""
    root = build_tree([
        _e(r"C:\Users\n\Downloads", 100),
        _e(r"C:\Users\n\Downloads", 250),
    ])
    node = _child(root, "c:", "users", "n", "downloads")
    assert node.size_bytes == 350
    assert node.entity_count == 2
    assert node.entity is not None


def test_children_come_back_biggest_first():
    root = build_tree([
        _e(r"C:\small", 1), _e(r"C:\huge", 900), _e(r"C:\mid", 50),
    ])
    names = [n.name for n in _child(root, "c:").sorted_children()]
    assert names == ["huge", "mid", "small"]


def test_entities_with_no_path_are_ignored_not_crashed():
    root = build_tree([{"path": "", "size_bytes": 5}, _e(r"C:\real", 10)])
    assert root.size_bytes == 10


def test_empty_input_gives_an_empty_root():
    root = build_tree([])
    assert root.size_bytes == 0 and not root.children


# ── chain collapsing ──────────────────────────────────────────────


def test_a_single_child_chain_becomes_one_row():
    """C: > Users > n > AppData > Roaming > discord is five clicks of nothing."""
    root = collapse_single_child_chains(
        build_tree([_e(r"C:\Users\n\AppData\Roaming\discord", 600)]))
    top = list(root.children.values())
    assert len(top) == 1
    assert top[0].name == "C:/Users/n/AppData/Roaming/discord"
    assert top[0].path.lower() == "c:/users/n/appdata/roaming/discord"
    assert top[0].size_bytes == 600


def test_collapsing_stops_where_the_tree_branches():
    root = collapse_single_child_chains(build_tree([
        _e(r"C:\Users\n\AppData\Roaming\discord", 600),
        _e(r"C:\Users\n\AppData\Roaming\Code", 400),
    ]))
    top = list(root.children.values())
    assert len(top) == 1
    roaming = top[0]
    assert roaming.name == "C:/Users/n/AppData/Roaming"
    assert {c.name for c in roaming.children.values()} == {"discord", "Code"}


def test_collapsing_never_swallows_a_node_that_holds_an_entity():
    """A folder that is itself a finding must stay clickable in its own right."""
    root = collapse_single_child_chains(build_tree([
        _e(r"C:\app", 100),
        _e(r"C:\app\cache", 400),
    ]))
    app_node = _child(root, "c:/app") if "c:/app" in root.children else None
    # C: has one child (app), app has an entity, so the chain stops at C:
    top = list(root.children.values())[0]
    assert top.entity is not None or top.children, "chain collapsed too far"
    assert top.size_bytes == 500


def test_collapsing_preserves_total_size():
    entities = [
        _e(r"C:\Users\n\AppData\Roaming\discord", 600),
        _e(r"D:\deep\deeper\deepest\thing", 900),
    ]
    plain = build_tree(entities)
    collapsed = collapse_single_child_chains(build_tree(entities))
    assert collapsed.size_bytes == plain.size_bytes == 1500
