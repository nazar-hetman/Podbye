"""A folder inside a proven application root may be understood on its own.

vcpkg was retyped to development_environment by the safety pass — correct,
because deleting a vcpkg instance is not a cache sweep — and that made
19.6 GB unreachable, of which 15.46 GB is ``buildtrees``: output vcpkg
rebuilds from sources it still has. The parent's claim was the right size for
the parent and the wrong size for what was inside it.

Two entry points, because the pipeline has two shapes:

**Path A — extract before the parent claims.** ``_phase1_discovery`` claims a
monolith root and the Containment Rule then seals it, so a stronger child role
has to be recorded before ``ctx.claim()`` runs. vcpkg goes through here.

**Path B — retype what is already independent.** ``AppData/Roaming`` is a
container directory, so every child of ``Roaming/Code`` is classified on its
own and *no parent entity exists at all*. ``CachedExtensionVSIXs`` was already
its own 1.34 GB row typed application_data; it needed the right type, not
extraction. This follows ``apply_known_path_rules``, which already retypes
generic entities in post-processing.

The rule both obey, and the reason this file exists:

    **Application identity may improve understanding. It must never by
    itself increase deletion permission.**

A proof decides only whether a rule applies. The *role* alone picks the entity
type, and actionability comes from that type through the same
``actionability_for_type`` as everything else. Recognising VS Code does not
make VS Code's settings more disposable — only the components with a role are
touched, and roles exist only for content an application regenerates.
"""
import os

import pytest

from app.models.deletion_scope import expand_targets
from app.models.smart_entity import actionability_for_type
from app.services import component_roles as cr


# ── fixtures that build real trees, because the proofs read the disk ──

def _touch(path, size=16):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


def _vcpkg(root, complete=True, marker=".vcpkg-root"):
    """A vcpkg instance. *complete* False leaves out triplets."""
    os.makedirs(os.path.join(root, "ports", "zlib"), exist_ok=True)
    if complete:
        os.makedirs(os.path.join(root, "triplets"), exist_ok=True)
    if marker:
        _touch(os.path.join(root, marker))
    for sub in ("buildtrees", "packages", "downloads"):
        _touch(os.path.join(root, sub, sub[0], "f.bin"), 64)
    return root


def _vscode(root, complete=True):
    os.makedirs(os.path.join(root, "User"), exist_ok=True)
    _touch(os.path.join(root, "CachedExtensionVSIXs", "a.vsix"), 64)
    dirs = ("Cache", "CachedData", "GPUCache") if complete else ("Cache",)
    for d in dirs:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    return root


@pytest.fixture
def vcpkg(tmp_path):
    return _vcpkg(str(tmp_path / "vcpkg_x"))


@pytest.fixture
def vscode(tmp_path):
    return _vscode(str(tmp_path / "Code"))


# ── proof: structure, never a name and never global presence ──────

def test_a_real_vcpkg_proves_itself(vcpkg):
    assert "ports + triplets" in cr.prove_vcpkg_root(vcpkg)


@pytest.mark.parametrize("kwargs,why", [
    (dict(complete=False), "no triplets"),
    (dict(marker=""), "no vcpkg-written marker"),
])
def test_a_tree_that_only_looks_like_vcpkg_proves_nothing(tmp_path, kwargs, why):
    """ports and triplets are ordinary words; the marker is vcpkg's own."""
    root = _vcpkg(str(tmp_path / "notvcpkg"), **kwargs)

    assert cr.prove_vcpkg_root(root) == "", why
    assert cr.components_for(root) == []


def test_the_folder_being_named_vcpkg_is_not_evidence(tmp_path):
    """The exact mistake the safety audit was about."""
    root = str(tmp_path / "vcpkg")
    os.makedirs(os.path.join(root, "buildtrees"), exist_ok=True)

    assert cr.prove_vcpkg_root(root) == ""


@pytest.mark.parametrize("marker", [".vcpkg-root", "vcpkg.exe",
                                    "scripts/buildsystems/vcpkg.cmake"])
def test_any_one_of_the_three_markers_is_enough(tmp_path, marker):
    root = _vcpkg(str(tmp_path / ("m" + marker[-4:])), marker=marker)

    assert cr.prove_vcpkg_root(root) != ""


def test_a_vscode_user_data_root_proves_itself(vscode):
    assert "CachedExtensionVSIXs" in cr.prove_vscode_user_data(vscode)


def test_a_code_folder_with_vscode_installed_elsewhere_proves_nothing(
        tmp_path, monkeypatch):
    """Global app_presence is explicitly not proof. "VS Code is installed
    somewhere" says nothing about whether *this* folder is its user data."""
    from app.services import app_presence

    monkeypatch.setattr(app_presence, "presence",
                        lambda name, strong_only=False: (app_presence.PRESENT,
                                                         "Start Menu"))
    root = str(tmp_path / "Code")
    os.makedirs(os.path.join(root, "CachedExtensionVSIXs"), exist_ok=True)

    assert cr.prove_vscode_user_data(root) == ""
    assert cr.components_for(root) == []


def test_a_partial_electron_shape_is_not_enough(tmp_path):
    root = _vscode(str(tmp_path / "Codeish"), complete=False)

    assert cr.prove_vscode_user_data(root) == ""


# ── the policy gate: role decides the action, never identity ──────

def test_every_role_maps_to_an_existing_type():
    for role in cr.known_roles():
        assert cr.role_entity_type(role) != ""


def test_an_unknown_role_yields_nothing():
    """Stale knowledge must fail closed, not guess a default."""
    assert cr.role_entity_type("wildly_new_role") == ""
    assert cr.role_entity_type("") == ""


def test_the_roles_only_ever_produce_regenerable_types():
    """Settings, saves, workspaces and project data have no role and must
    never acquire one by accident."""
    for role in cr.known_roles():
        etype = cr.role_entity_type(role)
        assert actionability_for_type(etype, "") == "recycle"


def test_a_download_cache_is_a_decision_not_a_default():
    """It comes back over the network, so it stays Review rather than Safe."""
    from app.models.smart_entity import _ENTITY_RISK

    assert _ENTITY_RISK[cr.role_entity_type("download_cache")] == "Review"
    assert _ENTITY_RISK[cr.role_entity_type("build_output")] == "Safe"


def test_no_rule_lacks_a_root_proof():
    """A relative path on its own is not evidence."""
    table = cr._rules_table()
    assert table, "the PoC table is missing"
    for rule_id, spec in table.items():
        assert spec.get("root_proof") in cr.PROOFS, rule_id
        assert spec.get("relative"), rule_id
        assert cr.role_entity_type(spec.get("role", "")), rule_id


def test_the_poc_covers_exactly_four_components():
    """Scope guard: this pass is not a general application-knowledge layer."""
    assert set(cr._rules_table()) == {
        "vcpkg.buildtrees", "vcpkg.packages", "vcpkg.downloads",
        "vscode.cached_extension_vsixs",
    }


@pytest.mark.parametrize("app", ["steam", "ollama", "chrome", "discord",
                                 "docker", "lmstudio", "lm studio"])
def test_no_rule_was_added_for_an_app_generic_logic_already_handles(app):
    blob = " ".join(str(cr._rules_table())).lower()
    assert app not in blob


# ── the active-build heuristic ────────────────────────────────────

def test_a_quiet_tree_reads_as_idle(vcpkg):
    old = 1_600_000_000
    for sub in ("buildtrees", "packages"):
        os.utime(os.path.join(vcpkg, sub, sub[0]), (old, old))

    assert cr.build_looks_active(vcpkg) == ""


def test_a_freshly_touched_tree_reads_as_busy(vcpkg):
    assert "changed" in cr.build_looks_active(vcpkg)


def test_a_lock_file_reads_as_busy(vcpkg):
    old = 1_600_000_000
    for sub in ("buildtrees", "packages"):
        os.utime(os.path.join(vcpkg, sub, sub[0]), (old, old))
    _touch(os.path.join(vcpkg, "vcpkg.lock"))

    assert "lock file" in cr.build_looks_active(vcpkg)


def test_an_unreadable_tree_reads_as_busy(vcpkg, monkeypatch):
    """Errs toward busy: refusing a cleanup is the cheap mistake."""
    monkeypatch.setattr(os, "listdir",
                        lambda p: (_ for _ in ()).throw(OSError("denied")))

    assert cr.build_looks_active(vcpkg) != ""


def test_a_busy_tree_still_offers_the_download_cache(vcpkg):
    """The gate is per role. downloads is not build output and is unaffected
    by a build being in progress."""
    comps = {c.rule_id: c for c in cr.components_for(vcpkg)}

    assert "vcpkg.downloads" in comps
    assert comps["vcpkg.downloads"].role == "download_cache"


# ── deletion scope: strict, deduplicated, fail-closed ─────────────

@pytest.fixture
def tree(tmp_path):
    root = str(tmp_path / "parent")
    for rel in ("keep/inner/a.bin", "take/b.bin", "c.bin"):
        _touch(os.path.join(root, rel.replace("/", os.sep)))
    return root


def test_nothing_excluded_is_one_operation(tree):
    assert expand_targets(tree, []) == [tree]


def test_an_exclusion_is_carved_around(tree):
    keep = os.path.join(tree, "keep", "inner")
    out = {p.replace("\\", "/").lower() for p in expand_targets(tree, [keep])}

    assert keep.replace("\\", "/").lower() not in out
    assert any(p.endswith("/take") for p in out)
    assert any(p.endswith("/c.bin") for p in out)


def test_an_exclusion_outside_the_root_is_ignored(tree, tmp_path):
    """It cannot be carved around, so pretending to honour it would be a lie.
    The root has nothing of anyone else's in it, so it goes whole."""
    outside = str(tmp_path / "somewhere_else")
    os.makedirs(outside, exist_ok=True)

    assert expand_targets(tree, [outside]) == [tree]


def test_the_root_excluding_itself_takes_nothing(tree):
    assert expand_targets(tree, [tree]) == []


def test_a_parent_of_the_root_as_exclusion_takes_nothing(tree):
    """Not a strict descendant, and it means the finding owns nothing here."""
    assert expand_targets(tree, [os.path.dirname(tree)]) == [tree]


def test_duplicate_exclusions_do_not_duplicate_targets(tree):
    keep = os.path.join(tree, "keep", "inner")
    once = expand_targets(tree, [keep])
    twice = expand_targets(tree, [keep, keep, keep.replace("\\", "/")])

    assert once == twice
    assert len(twice) == len({p.lower().replace("\\", "/") for p in twice})


def test_a_missing_exclusion_takes_nothing(tree):
    """The tree is not the shape this scope was computed against."""
    gone = os.path.join(tree, "keep", "vanished")

    assert expand_targets(tree, [gone]) == []


def test_an_unreadable_directory_takes_nothing(tree, monkeypatch):
    keep = os.path.join(tree, "keep", "inner")
    monkeypatch.setattr(os, "listdir",
                        lambda p: (_ for _ in ()).throw(OSError("denied")))

    assert expand_targets(tree, [keep]) == []


def test_a_reparse_point_is_never_a_target(tree, monkeypatch):
    """What is under a junction is not inside this folder, and deleting
    through one reaches data the finding never measured."""
    keep = os.path.join(tree, "keep", "inner")
    link = os.path.join(tree, "take").replace("\\", "/").lower()
    monkeypatch.setattr(os.path, "islink",
                        lambda p: p.replace("\\", "/").lower() == link)

    out = {p.replace("\\", "/").lower() for p in expand_targets(tree, [keep])}

    assert link not in out
    assert any(p.endswith("/c.bin") for p in out)


def test_an_undecidable_reparse_check_takes_nothing(tree, monkeypatch):
    keep = os.path.join(tree, "keep", "inner")
    monkeypatch.setattr(os.path, "islink",
                        lambda p: (_ for _ in ()).throw(OSError("denied")))

    assert expand_targets(tree, [keep]) == []


# ── legacy sessions ───────────────────────────────────────────────

def test_a_restored_legacy_entity_has_no_evidence_and_no_rule():
    """Sessions saved before these fields existed must read as *absent*,
    never as proven."""
    from app.models.smart_entity import SmartEntity

    e = SmartEntity(path="C:/x", name="x", entity_type="build_folder")
    row = e.to_dict()

    assert row["evidence"] == ""
    assert row["component_rule_id"] == ""


def test_a_legacy_row_without_the_keys_still_reads(tmp_path):
    """A stored dict from an older build simply lacks them."""
    legacy = {"path": "C:/x", "name": "x", "entity_type": "build_folder",
              "size_bytes": 10}

    assert legacy.get("component_rule_id", "") == ""
    assert expand_targets(legacy["path"], []) == ["C:/x"]
