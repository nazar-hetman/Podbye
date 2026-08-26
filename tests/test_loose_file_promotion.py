"""A loose file becomes its own finding when it deserves its own decision.

Five ZIPs in Downloads are usually five decisions — one obsolete installer,
one project export, one backup, one still needed. "Loose archives in
Downloads · 830 MB" is an artificial deletion unit that matches nobody's
intent.

But the opposite rule is worse. Measured on a real all-drives session: 37
type-grouped buckets held 288 files, **72% of them under 100 KB with a median
of 16 KB**. Exploding every bucket would have produced ~200 findings for
things nobody adjudicates — 18 config files under `discord` totalling 0.2 MB.

So: promote, do not explode. Size is the first cheap proxy for "independently
significant"; the product rule it stands in for is *group what shares a
lifecycle, promote what does not*. On the reporting machine's real Downloads
folder this leaves three buckets holding 16 files worth 1 MB between them,
and promotes the 412 MB archive that was buried in one of them.
"""
import pytest

from app.services.entity_detector import (
    STANDALONE_LOOSE_FILE_BYTES, detect_entities, deserves_own_finding,
)
from tests.treebuild import mkdir, mkfile

MB = 1024 * 1024
ROOT = "C:/Users/n/Downloads"


def _tree(*files):
    out = [mkdir(ROOT)]
    out += [mkfile(f"{ROOT}/{name}", size) for name, size in files]
    return out


def _detect(findings, root="C:/Users/n"):
    return detect_entities(findings, root, log_fn=lambda _m: None)


def _named(entities, name):
    return next((e for e in entities if e.name == name), None)


def _buckets(entities):
    return [e for e in entities if len(e.removable_file_paths or []) >= 2]


# ── the threshold is a policy knob, not the model ─────────────────

def test_the_threshold_is_one_place():
    assert STANDALONE_LOOSE_FILE_BYTES == MB


def test_the_decision_goes_through_one_predicate():
    assert deserves_own_finding(mkfile("C:/d/big.zip", MB)) is True
    assert deserves_own_finding(mkfile("C:/d/small.zip", MB - 1)) is False


# ── promotion ─────────────────────────────────────────────────────

def test_a_large_archive_gets_its_own_finding():
    entities = _detect(_tree(("Cetus Photo brave1_logs.zip", 412 * MB),
                             ("irizi-odm-dev.zip", 352 * MB)))
    assert _named(entities, "Cetus Photo brave1_logs.zip") is not None
    assert _named(entities, "irizi-odm-dev.zip") is not None
    assert not _buckets(entities), "large archives were still bucketed"


def test_a_promoted_finding_targets_only_that_file():
    entities = _detect(_tree(("big.zip", 40 * MB), ("other.zip", 40 * MB)))
    one = _named(entities, "big.zip")
    assert one.removable_file_paths == [f"{ROOT}/big.zip"]
    assert one.path == f"{ROOT}/big.zip"


def test_promotion_applies_to_every_kind_of_loose_file():
    entities = _detect(_tree(("holiday.mp4", 30 * MB), ("report.pdf", 4 * MB),
                             ("notes.qqq", 9 * MB), ("run.log", 8 * MB)))
    for name in ("holiday.mp4", "report.pdf", "notes.qqq", "run.log"):
        assert _named(entities, name) is not None, name


def test_a_promoted_file_keeps_the_risk_of_its_kind():
    """Promotion changes the unit of decision, never how risky it is."""
    entities = _detect(_tree(("holiday.mp4", 30 * MB)))
    assert _named(entities, "holiday.mp4").entity_type == "media_collection"


# ── and the small stay together ───────────────────────────────────

def test_tiny_files_stay_bucketed():
    """18 config files worth 0.2 MB are not 18 deletion decisions."""
    entities = _detect(_tree(*[(f"conf{i}.qqq", 8 * 1024) for i in range(18)]))
    buckets = _buckets(entities)
    assert len(buckets) == 1
    assert len(buckets[0].removable_file_paths) == 18


def test_a_bucket_keeps_only_what_was_not_promoted():
    entities = _detect(_tree(("big.zip", 40 * MB),
                             ("a.zip", 900), ("b.zip", 900), ("c.zip", 900)))
    assert _named(entities, "big.zip") is not None
    buckets = _buckets(entities)
    assert len(buckets) == 1
    assert len(buckets[0].removable_file_paths) == 3


def test_bucketed_files_are_still_reachable_one_by_one():
    """Grouping never hides: the bucket still carries every path."""
    entities = _detect(_tree(*[(f"n{i}.qqq", 4 * 1024) for i in range(6)]))
    bucket = _buckets(entities)[0]
    assert len(bucket.removable_file_paths) == 6


def test_a_leftover_of_one_becomes_that_file():
    """The existing rule still holds after the big ones are taken out."""
    entities = _detect(_tree(("big.zip", 40 * MB), ("tiny.zip", 500)))
    assert _named(entities, "tiny.zip") is not None
    assert not _buckets(entities)


# ── things that were already individual stay that way ─────────────

def test_an_installer_keeps_its_product_name():
    """Installers were promoted long before size was a rule."""
    entities = _detect(_tree(("VSCodeUserSetup-x64-1.85.exe", 92 * MB)))
    names = [e.name for e in entities]
    assert any(n.startswith("Installer (") for n in names), names


def test_a_small_installer_is_still_promoted():
    """The installer rule is about what the file is, not how big it is."""
    entities = _detect(_tree(("tool-setup.exe", 30 * 1024)))
    assert any(e.name.startswith("Installer (") for e in entities)


# ── per-directory scoping survives ────────────────────────────────

def test_buckets_are_still_scoped_to_their_own_folder():
    findings = [mkdir("C:/Users/n"), mkdir(ROOT), mkdir("C:/Users/n/Desktop")]
    findings += [mkfile(f"{ROOT}/a{i}.qqq", 900) for i in range(3)]
    findings += [mkfile(f"C:/Users/n/Desktop/b{i}.qqq", 900) for i in range(3)]

    for bucket in _buckets(_detect(findings)):
        roots = {p.rsplit("/", 1)[0] for p in bucket.removable_file_paths}
        assert len(roots) == 1, f"{bucket.name} spans {roots}"


# ── ownership beats size ──────────────────────────────────

def test_a_managed_store_is_not_split_into_files():
    """Three 3 GB blobs under .ollama are one model store, not three choices.

    Caught by an existing test rather than by review: the size rule alone
    promoted each blob, offering a decision nobody can act on — you remove an
    Ollama model with `ollama rm`, not by deleting one hash out of its store.
    """
    from app.services.entity_detector import deserves_own_finding
    blob = mkfile("C:/Users/n/.ollama/models/blobs/sha256-aa", 3000 * MB)
    assert deserves_own_finding(blob) is False


def test_a_package_directory_owns_its_files_too():
    from app.services.entity_detector import deserves_own_finding
    assert deserves_own_finding(
        mkfile("C:/app/node_modules/thing/bundle.js", 4 * MB)) is False


def test_a_stray_model_file_is_still_its_own_decision():
    """A .gguf someone downloaded is not part of anybody's store."""
    from app.services.entity_detector import deserves_own_finding
    assert deserves_own_finding(mkfile(f"{ROOT}/llama-3.gguf", 4000 * MB)) is True
