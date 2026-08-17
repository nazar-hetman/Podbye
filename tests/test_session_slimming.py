"""A session file stores inputs, not the sentences rendered from them.

Measured on a full C:/ scan (1158 entities, 2.2 MB): `why` alone was 198 KB,
with `recommendation`, `summary`, `entity_type_label` and `confidence_label`
behind it — every one of them rebuilt by to_dict() on the way to the screen,
and none of them ever read back off disk.

Two things follow, and both are pinned here:

* what is dropped must be reproducible from what is kept, so a reopened
  session is indistinguishable from the one that was saved;
* the prose that is dropped is user-facing text frozen in the scan-time
  language, so rebuilding it is also what makes a reopened session follow the
  language the user is reading now.
"""
import pytest

from app.state import session_store
from app.state.scan_state import ScanState
from app.models.finding import Finding
from app.models.smart_entity import SmartEntity


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    monkeypatch.setattr(session_store, "_sessions_dir", lambda: d)
    return d


def _entity(**overrides):
    """An entity with every optional field populated — nothing left defaulted.

    A round-trip test that only exercises the common fields is exactly how the
    dropped-on-restore bug survived: uninstall_string, origin and is_self were
    written on every scan and read back on none of them.
    """
    kwargs = dict(
        path="C:/Users/n/AppData/Local/Forge",
        name="Forge",
        entity_type="application_data",
        size_bytes=56_398_474_342,
        file_count=33_498,
        folder_count=483,
        risk="Review",
        risk_reason="Application support data path",
        confidence="heuristic",
        confidence_score=0.85,
        modified=1_786_477_058.5,
        accessed=1_786_477_050.25,
        children_sample=["runs", "terrain", "work"],
        app_version="2.1.0",
        app_publisher="Forge Labs",
        install_date="2026-01-04",
        uninstall_string='"C:/Program Files/Forge/uninstall.exe" /S',
        cloud_sync_provider="OneDrive",
        age_boost=0.2,
        origin="Downloads",
        is_self=False,
        removable_file_paths=["C:/Users/n/AppData/Local/Forge/old.log"],
    )
    kwargs.update(overrides)
    return SmartEntity(**kwargs)


def _finding(**overrides):
    kwargs = dict(
        path="C:/Windows/Temp/a.tmp",
        name="a.tmp",
        is_dir=False,
        size_bytes=4096,
        extension="tmp",
        modified=1_786_400_000.0,
        accessed=1_786_410_000.0,
        parent="C:/Windows/Temp",
    )
    kwargs.update(overrides)
    return Finding(**kwargs)


def _snapshot(entities=None, findings=None):
    return session_store.build_snapshot(
        session_id="s1", target="C:/", scan_mode="smart", status="completed",
        start_time=0.0, scanned_count=len(findings or []), total_size=0,
        category_totals={}, risk_totals={},
        findings_dicts=[f.to_dict() for f in (findings or [])],
        entities_dicts=[e.to_dict() for e in (entities or [])],
    )


def _reopen(snapshot):
    """Save, load and restore — the exact path History's Open findings takes."""
    session_store.save_session(snapshot)
    loaded = session_store.load_session()
    state = ScanState()
    state.restore_from_session(loaded)
    return state


# ── what must not be on disk ──────────────────────────────────────

@pytest.mark.parametrize("key", [
    "why", "recommendation", "entity_type_label", "confidence_label",
    "category", "size", "age", "source_rule", "actionability",
    "last_access", "first_seen", "is_dir", "is_entity", "reclaimable_bytes",
])
def test_derived_entity_fields_are_not_persisted(sessions_dir, key):
    stored = _snapshot(entities=[_entity()])["entities"][0]
    assert key in _entity().to_dict(), f"{key} is not a field to_dict produces"
    assert key not in stored


@pytest.mark.parametrize("key", ["why", "recommendation", "size", "age",
                                 "last_access", "first_seen"])
def test_derived_finding_fields_are_not_persisted(sessions_dir, key):
    stored = _snapshot(findings=[_finding()])["findings"][0]
    assert key not in stored


def test_findings_keep_their_classification(sessions_dir):
    """Re-deriving these means re-running categorize() over the whole scan."""
    stored = _snapshot(findings=[_finding()])["findings"][0]
    for key in ("category", "risk", "source_rule", "risk_reason"):
        assert stored[key], f"{key} must survive; recomputing it is real work"


def test_empty_fields_cost_nothing(sessions_dir):
    """A bare entity carries a dozen empty app-metadata keys at ~25 bytes each."""
    bare = SmartEntity(path="C:/x", name="x", entity_type="cache_folder")
    stored = _snapshot(entities=[bare])["entities"][0]
    assert "app_version" not in stored
    assert "uninstall_string" not in stored
    assert "duplicate_locations" not in stored


# ── what must survive the round trip ──────────────────────────────

def test_a_reopened_entity_is_indistinguishable_from_the_saved_one(sessions_dir):
    """The whole justification for dropping a field: to_dict rebuilds it."""
    original = _entity().to_dict()

    state = _reopen(_snapshot(entities=[_entity()]))
    restored = state._entities[0].to_dict()

    assert restored == original


def test_a_reopened_finding_is_indistinguishable_from_the_saved_one(sessions_dir):
    original = _finding().to_dict()

    state = _reopen(_snapshot(findings=[_finding()]))
    restored = state._findings[0].to_dict()

    assert restored == original


@pytest.mark.parametrize("key, expected", [
    # Written on every scan, read back on none — reopening a session from
    # History silently weakened it before this was fixed.
    ("uninstall_string", '"C:/Program Files/Forge/uninstall.exe" /S'),
    ("app_publisher", "Forge Labs"),
    ("app_version", "2.1.0"),
    ("install_date", "2026-01-04"),
    ("origin", "Downloads"),
    ("removable_file_paths", ["C:/Users/n/AppData/Local/Forge/old.log"]),
    ("confidence_score", 0.85),
    ("last_access", "2026-08-11"),
])
def test_a_reopened_entity_keeps_the_fields_restore_used_to_drop(
        sessions_dir, key, expected):
    state = _reopen(_snapshot(entities=[_entity()]))
    assert state._entities[0].to_dict()[key] == expected


def test_a_reopened_entity_still_knows_it_is_vigils_own_data(sessions_dir):
    """is_self is what keeps Vigil's own folder out of a cleanup selection."""
    state = _reopen(_snapshot(entities=[_entity(is_self=True)]))
    assert state._entities[0].is_self is True


def test_origin_still_decides_the_category_after_a_reopen(sessions_dir):
    """category is a property now, so dropping it only works if origin survives."""
    state = _reopen(_snapshot(entities=[_entity()]))
    assert state._entities[0].to_dict()["category"] == "Downloads"


def test_a_reopened_finding_keeps_its_sub_type_label(sessions_dir):
    """semantic_label is the 'What it is:' half of a finding's `why`."""
    f = _finding()
    f.semantic_label = "Browser cache"
    state = _reopen(_snapshot(findings=[f]))
    assert "What it is: Browser cache" in state._findings[0].to_dict()["why"]


# ── the totals History reads ──────────────────────────────────────

def test_the_session_reclaimable_total_survives_stripping(sessions_dir):
    """Per-entity reclaimable_bytes is dropped, so the sum is stored up front."""
    entities = [_entity(), _entity(path="C:/other", risk="Optional")]
    expected = sum(e.to_dict()["reclaimable_bytes"] for e in entities)

    snapshot = _snapshot(entities=entities)

    assert snapshot["total_reclaimable_bytes"] == expected
    assert expected > 0, "the fixture stopped exercising the sum"


def test_history_reports_the_reclaimable_total_it_was_given(sessions_dir):
    entities = [_entity(path="C:/other", risk="Optional")]
    snapshot = _snapshot(entities=entities)

    session_store.append_to_history(snapshot)
    record = session_store.load_history()[0]

    assert record["total_reclaimable_bytes"] == snapshot["total_reclaimable_bytes"]
    assert record["display_count"] == 1
