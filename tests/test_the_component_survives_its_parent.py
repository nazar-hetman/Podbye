"""End to end: an extracted component reaches the user, and narrows nothing else.

Companion to test_a_proven_component_owns_itself.py, which covers the proofs
and the deletion-scope arithmetic in isolation. This file runs the real
detector over real directories, because the two things most likely to undo
this work happen in the pipeline rather than in a helper:

* ``_phase1_discovery`` claims a monolith root and the Containment Rule seals
  it, so an extraction that runs a moment too late produces nothing;
* post-processing absorbs sub-folder entities into application parents, which
  would fold the component straight back into the claim it was taken out of.

The invariant underneath all of it: **extraction narrows ownership and does
nothing else.** The parent keeps its type, its risk and its action, and only
its size moves.
"""
import os

import pytest

from app.models.deletion_scope import expand_targets
from app.models.finding import Finding
from app.models.smart_entity import actionability_for_type
from app.services.entity_detector import detect_entities

MB = 1024 ** 2


def _touch(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


def _findings(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            p = os.path.join(dirpath, d)
            out.append(Finding(path=p, name=d, is_dir=True, size_bytes=0,
                               extension="", modified=0, accessed=0,
                               parent=dirpath))
        for f in filenames:
            p = os.path.join(dirpath, f)
            out.append(Finding(path=p, name=f, is_dir=False,
                               size_bytes=os.path.getsize(p),
                               extension=os.path.splitext(f)[1],
                               modified=0, accessed=0, parent=dirpath))
    return out


@pytest.fixture
def scan(tmp_path):
    """A scan root holding a real, quiet vcpkg instance."""
    root = str(tmp_path / "src")
    vcpkg = os.path.join(root, "vcpkg_demo")
    os.makedirs(os.path.join(vcpkg, "ports", "zlib"), exist_ok=True)
    os.makedirs(os.path.join(vcpkg, "triplets"), exist_ok=True)
    _touch(os.path.join(vcpkg, ".vcpkg-root"), 8)
    _touch(os.path.join(vcpkg, "buildtrees", "zlib", "out.obj"), 8 * MB)
    _touch(os.path.join(vcpkg, "packages", "zlib", "lib.a"), 4 * MB)
    _touch(os.path.join(vcpkg, "downloads", "zlib.tar.gz"), 2 * MB)
    _touch(os.path.join(vcpkg, "docs", "readme.md"), 1 * MB)
    old = 1_600_000_000
    for sub in ("buildtrees", "packages"):
        os.utime(os.path.join(vcpkg, sub, "zlib"), (old, old))

    def run():
        return detect_entities(_findings(root), root, log_fn=lambda *_a: None)

    return type("Scan", (), {"root": root, "vcpkg": vcpkg, "run": staticmethod(run)})


def _by_suffix(entities, suffix):
    for e in entities:
        if e.path.replace("\\", "/").lower().endswith(suffix):
            return e
    return None


# ── path A reaches the user ───────────────────────────────────────

def test_the_components_become_their_own_findings(scan):
    ents = scan.run()

    for rel, rule in (("buildtrees", "vcpkg.buildtrees"),
                      ("packages", "vcpkg.packages"),
                      ("downloads", "vcpkg.downloads")):
        e = _by_suffix(ents, "/" + rel)
        assert e is not None, f"{rel} was not extracted"
        assert e.component_rule_id == rule
        assert e.evidence == "structure"


def test_they_are_actually_actionable(scan):
    ents = scan.run()

    build = _by_suffix(ents, "/buildtrees")
    downloads = _by_suffix(ents, "/downloads")

    assert actionability_for_type(build.entity_type, build.risk) == "recycle"
    assert actionability_for_type(downloads.entity_type, downloads.risk) == "recycle"
    assert build.risk == "Safe"
    assert downloads.risk == "Review"      # comes back over the network


def test_the_download_cache_says_it_will_be_fetched_again(scan):
    e = _by_suffix(scan.run(), "/downloads")

    assert "fetched again over the network" in e.risk_reason


def test_the_build_output_names_its_proof(scan):
    e = _by_suffix(scan.run(), "/buildtrees")

    assert "ports + triplets" in e.risk_reason
    assert "no build activity seen" in e.risk_reason


# ── and the parent is left exactly as it was ──────────────────────

def test_the_parent_keeps_its_type_risk_and_action(scan):
    """Extraction may narrow ownership. It may never make the parent more
    deletable — that is the whole safety property."""
    parent = _by_suffix(scan.run(), "/vcpkg_demo")

    assert parent is not None
    assert parent.entity_type == "development_environment"
    assert parent.risk == "Review"
    assert actionability_for_type(parent.entity_type, parent.risk) == "review_only"


def test_only_the_parents_size_moved(scan):
    """14 MB of components out of a 15 MB tree leaves the parent with its own
    1 MB of docs."""
    ents = scan.run()
    parent = _by_suffix(ents, "/vcpkg_demo")
    kids = sum(_by_suffix(ents, "/" + r).size_bytes
               for r in ("buildtrees", "packages", "downloads"))

    assert kids == 14 * MB
    assert parent.size_bytes < 2 * MB


def test_an_active_build_leaves_the_output_inside_the_parent(scan):
    """Conservative by design: a tree that looks busy is not offered."""
    os.utime(os.path.join(scan.vcpkg, "buildtrees", "zlib"), None)
    ents = scan.run()

    assert _by_suffix(ents, "/buildtrees") is None
    assert _by_suffix(ents, "/packages") is None
    # The download cache is a different role and is unaffected.
    assert _by_suffix(ents, "/downloads") is not None


def test_an_unproven_tree_yields_no_components(tmp_path):
    """Same folder names, no vcpkg marker."""
    root = str(tmp_path / "src")
    fake = os.path.join(root, "vcpkg_fake")
    os.makedirs(os.path.join(fake, "ports"), exist_ok=True)
    _touch(os.path.join(fake, "buildtrees", "x", "f.bin"), 8 * MB)

    ents = detect_entities(_findings(root), root, log_fn=lambda *_a: None)

    assert all(not e.component_rule_id for e in ents)


# ── post-processing must not undo it ──────────────────────────────

def test_absorption_never_swallows_an_extracted_component(scan):
    """The parent is an absorber type in some configurations; a component
    carries a rule id precisely so it cannot be folded back in."""
    ents = scan.run()
    kept = [e for e in ents if e.component_rule_id]

    assert len(kept) == 3


def test_the_guard_is_the_rule_id_not_the_type():
    """Asserted directly, because the retain-list it sits beside is by type
    and a future edit could reasonably assume this one is too."""
    import inspect

    from app.services import entity_detector as ed

    src = inspect.getsource(ed._postprocess)
    assert "component_rule_id" in src


def test_one_folder_gets_one_folder_backed_row(scan):
    """Two rows for one folder means two rows offering the same bytes."""
    ents = scan.run()
    from app.models.deletion_scope import is_folder_backed

    seen = [e.path.replace("\\", "/").lower().rstrip("/")
            for e in ents if is_folder_backed(e.to_dict())]

    assert len(seen) == len(set(seen)), "a folder was listed twice"


def test_a_duplicate_root_keeps_the_one_that_knows_more(scan):
    """A proven component outranks a generic row for the same folder."""
    from app.services.entity_detector import _one_entity_per_root
    from app.models.smart_entity import SmartEntity

    generic = SmartEntity(path="C:/x/buildtrees", name="buildtrees",
                          entity_type="unknown_folder", size_bytes=10,
                          file_count=1)
    proven = SmartEntity(path="C:/x/buildtrees", name="vcpkg build trees",
                         entity_type="build_folder", size_bytes=10,
                         file_count=1)
    proven.component_rule_id = "vcpkg.buildtrees"

    out = _one_entity_per_root([generic, proven], lambda *_a: None)

    assert len(out) == 1
    assert out[0].component_rule_id == "vcpkg.buildtrees"


def test_file_backed_buckets_may_share_a_path(scan):
    """Pass-8 buckets carry their enclosing directory as their path and own
    only their own listed files. Deduplicating them would delete findings."""
    from app.services.entity_detector import _one_entity_per_root
    from app.models.smart_entity import SmartEntity

    a = SmartEntity(path="C:/x", name="Misc A", entity_type="mixed_folder",
                    size_bytes=5, file_count=1)
    a.removable_file_paths = ["C:/x/a.tmp"]
    b = SmartEntity(path="C:/x", name="Misc B", entity_type="archive_group",
                    size_bytes=5, file_count=1)
    b.removable_file_paths = ["C:/x/b.zip"]

    out = _one_entity_per_root([a, b], lambda *_a: None)

    assert len(out) == 2


def test_a_group_header_never_becomes_a_folder_target(scan):
    """A row that stands for a set of files must not turn into a folder
    delete: expand_targets is only reached for folder-backed rows, and a
    bucket's own files are what it owns."""
    from app.models.deletion_scope import file_paths_of, is_folder_backed
    from app.models.smart_entity import SmartEntity

    bucket = SmartEntity(path=scan.root, name="Misc files",
                         entity_type="mixed_folder", size_bytes=5, file_count=1)
    bucket.removable_file_paths = [os.path.join(scan.root, "loose.bin")]
    row = bucket.to_dict()

    assert not is_folder_backed(row)
    assert file_paths_of(row) == bucket.removable_file_paths


# ── cleanup scope, the three selections ───────────────────────────

def test_child_only_takes_only_the_child(scan):
    ents = scan.run()
    child = _by_suffix(ents, "/buildtrees")

    assert expand_targets(child.path, []) == [child.path]


def test_parent_only_leaves_every_component_behind(scan):
    ents = scan.run()
    parent = _by_suffix(ents, "/vcpkg_demo")
    row = parent.to_dict()

    targets = {p.replace("\\", "/").lower()
               for p in expand_targets(parent.path, row.get("contained_paths") or [])}

    for rel in ("buildtrees", "packages", "downloads"):
        kept = os.path.join(parent.path, rel).replace("\\", "/").lower()
        assert kept not in targets, f"{rel} would have gone with the parent"
    assert any(t.endswith("/docs") for t in targets), "the parent's own docs"


def test_both_selected_covers_everything_exactly_once(scan):
    """A plain sum, because ownership is disjoint — and it has to equal what
    is really on disk, not what the fixture meant to write. Measured rather
    than asserted against a constant: the first version of this forgot the
    8-byte .vcpkg-root marker and failed by exactly that."""
    ents = scan.run()
    parent = _by_suffix(ents, "/vcpkg_demo")
    kids = [_by_suffix(ents, "/" + r)
            for r in ("buildtrees", "packages", "downloads")]

    on_disk = 0
    for dirpath, _dirnames, filenames in os.walk(scan.vcpkg):
        for f in filenames:
            on_disk += os.path.getsize(os.path.join(dirpath, f))

    assert parent.size_bytes + sum(k.size_bytes for k in kids) == on_disk


def test_the_parent_records_what_it_gave_up(scan):
    ents = scan.run()
    row = _by_suffix(ents, "/vcpkg_demo").to_dict()
    contained = {p.replace("\\", "/").lower() for p in (row.get("contained_paths") or [])}

    for rel in ("buildtrees", "packages", "downloads"):
        assert any(c.endswith("/" + rel) for c in contained), rel
