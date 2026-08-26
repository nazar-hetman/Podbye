"""History must not print a breakdown that contradicts its own headline.

Cleanup records written before per-file sizes were measured carry the bucket's
total on every one of its members. One real record holds nine identical 668 MB
items for 3.9 GB actually freed, so the card showed "CLEANED 3.9 GB" a few
pixels above a breakdown summing to 5.9 GB.
"""
import os

from app.screens.history import _cleanup_top_categories

# Captured at import time, before the autouse guard redirects it.
REAL_APPDATA = os.environ.get("APPDATA", "")


def _items(n, size, category="Cache & Temp"):
    return [{"path": f"C:/x/f{i}.bin", "name": f"f{i}.bin", "size": size,
             "risk": "Optional", "category": category} for i in range(n)]


def test_a_stamped_record_keeps_its_sizes():
    rows = _cleanup_top_categories(_items(3, 1000), sizes_trusted=True)
    assert rows[0][1] == 3
    assert rows[0][2] == 3000


def test_an_unstamped_record_reports_counts_not_sizes():
    """The counts in those records are still right; the sizes are not."""
    rows = _cleanup_top_categories(_items(9, 668 * 1024 ** 2), sizes_trusted=False)
    assert rows[0][1] == 9
    assert rows[0][2] == 0, "an untrusted size must not be added up"


def test_rows_still_order_sensibly_without_sizes():
    items = _items(2, 10, "Cache & Temp") + _items(5, 10, "Dev Artifacts")
    rows = _cleanup_top_categories(items, sizes_trusted=False)
    assert [r[1] for r in rows] == [5, 2], "should fall back to ordering by count"


def test_new_records_are_stamped(tmp_path, monkeypatch):
    """Without the stamp every future record would be treated as suspect."""
    import app.state.session_store as store
    from app.services.cleanup_engine import CleanupResult

    monkeypatch.setattr(store, "_sessions_dir", lambda: tmp_path)
    result = CleanupResult(succeeded=["C:/x/a.bin"], total_bytes_freed=10)
    assert store.save_cleanup_record("s", _items(1, 10), result, "recycle_bin")

    import glob, io, json
    written = glob.glob(str(tmp_path / "cleanup_*.json"))
    assert written, "no record was written"
    assert json.load(io.open(written[0], encoding="utf-8"))["item_sizes"] == "measured"


def test_the_suite_cannot_write_to_the_real_session_store():
    """The guard that exists because this test's first draft did exactly that.

    save_cleanup_record resolves the directory through the *private*
    _sessions_dir(); redirecting the public sessions_dir() looks right and does
    nothing, so the record landed in the running user's history.
    """
    from app.state import session_store
    resolved = os.path.normcase(str(session_store._sessions_dir()))
    assert REAL_APPDATA, "no APPDATA to compare against"
    real = os.path.normcase(os.path.join(REAL_APPDATA, "Podbye", "sessions"))
    assert resolved != real, "the suite is pointed at the user's live store"
