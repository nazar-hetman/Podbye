"""Build a folder hierarchy from scan entities.

Every other view in Findings answers "what is this?" — a category, a risk, an
app. That is the useful question right up until the classifier is wrong, and
then there is no way back: WSL was filed under Virtual Machines because two
.vhdx images outweighed 619 DLLs, and Discord under Media. A user who knows
perfectly well where the folder lives had nowhere to go.

This is the escape hatch. It answers "where is this?", which is never a guess:
the path is ground truth. Sizes roll up, so a folder always reports the total
of everything beneath it and the big consumers surface by drilling, not by
trusting a label.

The tree is built from *entities* rather than raw findings on purpose. A full
C:/ scan holds ~1.8M findings and a node per file would be unusable as well as
enormous; entities are the things a user can actually act on, and every
scanned byte already belongs to exactly one of them.
"""
from __future__ import annotations


class PathNode:
    """One folder in the tree, or a leaf standing for an entity."""

    __slots__ = ("name", "path", "children", "entity",
                 "size_bytes", "file_count", "entity_count")

    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.children: dict[str, PathNode] = {}
        self.entity: dict | None = None      # set on a node an entity sits at
        self.size_bytes = 0
        self.file_count = 0
        self.entity_count = 0

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def sorted_children(self) -> list["PathNode"]:
        """Biggest first — the point of drilling is to follow the space."""
        return sorted(self.children.values(),
                      key=lambda n: (-n.size_bytes, n.name.lower()))

    def __repr__(self) -> str:      # pragma: no cover - debugging aid
        return f"<PathNode {self.path!r} {self.size_bytes}B {len(self.children)} kids>"


def _split(path: str) -> list[str]:
    norm = (path or "").replace("\\", "/").rstrip("/")
    return [seg for seg in norm.split("/") if seg]


def build_tree(entities: list[dict]) -> PathNode:
    """Fold *entities* into a folder tree with sizes rolled up.

    The returned root is synthetic: real scans span several drives, so there
    is no single filesystem root to hang them from.
    """
    root = PathNode("", "")

    for entity in entities or []:
        segments = _split(entity.get("path", ""))
        if not segments:
            continue
        size = int(entity.get("size_bytes", 0) or 0)
        files = int(entity.get("file_count", 0) or 0)

        node = root
        node.size_bytes += size
        node.file_count += files
        node.entity_count += 1

        walked: list[str] = []
        for segment in segments:
            walked.append(segment)
            child = node.children.get(segment.lower())
            if child is None:
                child = PathNode(segment, "/".join(walked))
                node.children[segment.lower()] = child
            child.size_bytes += size
            child.file_count += files
            child.entity_count += 1
            node = child

        # Several entities can share a path (a folder's loose files split by
        # content type). The first wins the node; the rest still contribute
        # their size, and the node reports the true entity_count.
        if node.entity is None:
            node.entity = entity

    return root


def collapse_single_child_chains(node: PathNode, _depth: int = 0) -> PathNode:
    """Fold ``C: → Users → Nazar → AppData → Roaming`` into one row.

    A chain of folders with one child each carries no information and costs
    five clicks to walk. Explorer-style breadcrumbs do the same thing. The
    node keeps the full path, so acting on it is unambiguous.
    """
    for key, child in list(node.children.items()):
        collapsed = child
        while len(collapsed.children) == 1 and collapsed.entity is None:
            only = next(iter(collapsed.children.values()))
            merged = PathNode(f"{collapsed.name}/{only.name}", only.path)
            merged.children = only.children
            merged.entity = only.entity
            merged.size_bytes = only.size_bytes
            merged.file_count = only.file_count
            merged.entity_count = only.entity_count
            collapsed = merged
        node.children[key] = collapsed
        collapse_single_child_chains(collapsed, _depth + 1)
    return node
