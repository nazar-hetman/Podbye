"""FindingsTableModel — row indexing and header caching."""
import pytest

from app import i18n
from app.models import findings_table_model as ftm
from app.models.findings_table_model import FindingsTableModel


def _e(path, **kw):
    d = {"path": path, "name": path.rsplit("/", 1)[-1], "size_bytes": 1,
         "risk": "Review", "ai_status": "none"}
    d.update(kw)
    return d


@pytest.fixture
def model():
    m = FindingsTableModel()
    m.set_entities([_e(f"C:/e{i}") for i in range(20)])
    return m


# ── update_entity_by_path ────────────────────────────────────────


def test_update_entity_by_path_finds_correct_row(model):
    row = model.update_entity_by_path(_e("C:/e7", ai_status="ready"))
    assert row == 7
    assert model.get_entity(7)["ai_status"] == "ready"


def test_update_entity_by_path_unknown_returns_minus_one(model):
    assert model.update_entity_by_path(_e("C:/nope")) == -1


def test_update_entity_by_path_missing_path_returns_minus_one(model):
    assert model.update_entity_by_path({"name": "x"}) == -1


def test_index_tracks_set_entities(model):
    model.set_entities([_e("C:/only")])
    assert model.update_entity_by_path(_e("C:/only", ai_status="ready")) == 0
    # Rows from the previous set must no longer resolve.
    assert model.update_entity_by_path(_e("C:/e7")) == -1


def test_index_tracks_remove_cleaned(model):
    """Rows shift after a removal — the index must follow, not go stale."""
    model.remove_cleaned(["C:/e0", "C:/e1"])
    # e2 was row 2, is now row 0.
    row = model.update_entity_by_path(_e("C:/e2", ai_status="ready"))
    assert row == 0
    assert model.get_entity(0)["path"] == "C:/e2"
    assert model.get_entity(0)["ai_status"] == "ready"
    assert model.update_entity_by_path(_e("C:/e0")) == -1


def test_update_emits_datachanged_for_its_row(model, qtbot=None):
    seen = []
    model.dataChanged.connect(lambda tl, br, roles: seen.append((tl.row(), br.row())))
    model.update_entity_by_path(_e("C:/e3", ai_status="ready"))
    assert seen == [(3, 3)]


# ── header cache ─────────────────────────────────────────────────


def test_headers_cached_between_calls():
    ftm._headers_cache = None
    first = ftm._headers()
    assert ftm._headers() is first, "headers rebuilt despite unchanged language"


def test_headers_rebuild_on_language_switch():
    original = i18n.get_language()
    try:
        i18n.set_language("English")
        ftm._headers_cache = None
        english = ftm._headers()
        i18n.set_language("Ukrainian")
        ukrainian = ftm._headers()
        assert ukrainian is not english, "stale headers after language switch"
        # NAME must actually translate, proving the cache re-resolved.
        assert ukrainian[ftm.COL_NAME] != english[ftm.COL_NAME]
    finally:
        i18n.set_language(original)
        ftm._headers_cache = None


def test_column_count_matches_headers(model):
    assert model.columnCount() == len(ftm._headers()) == 8
