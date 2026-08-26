"""A recycled item must be proven to have reached the bin.

Podbye's central promise is that cleanup is recoverable. The delete path asks
the shell for that with FOF_ALLOWUNDO, but it also passes FOF_NOCONFIRMATION,
which suppresses the prompt Windows shows when an item is too large for the
volume's Recycle Bin quota. In that case the shell deletes the item outright —
and SHFileOperationW still returns 0. Nothing distinguished that from a real
recycle, so the app reported "moved to Recycle Bin · fully recoverable" over a
permanent deletion.

Large items are therefore bracketed with a bin-size query. These tests drive
that logic with a stubbed bin, since a real one cannot be made to overflow on
demand inside a test.
"""
import os

import pytest

from app.services import cleanup_engine as ce


BIG = ce._VERIFY_RECYCLED_MIN_BYTES + 1
SMALL = 1024


@pytest.fixture
def bin_queries(monkeypatch):
    """Count how often the Recycle Bin is measured, and answer with a size."""
    state = {"bin": 5_000_000_000, "queries": 0}

    def _bin_size_for(path):
        state["queries"] += 1
        return state["bin"]

    monkeypatch.setattr(ce, "_bin_size_for", _bin_size_for)
    return state


def _make(tmp_path, name, size):
    p = tmp_path / name
    p.write_bytes(b"0" * min(size, 4096))
    # Report the intended size without writing gigabytes to a test machine.
    return str(p), size


def test_a_large_item_the_bin_absorbed_is_reported_as_recycled(tmp_path, bin_queries, monkeypatch):
    path, size = _make(tmp_path, "big.bin", BIG)
    monkeypatch.setattr(ce, "_get_size", lambda p: size)

    def _recycle_one(p):
        os.remove(p)
        bin_queries["bin"] += size          # the bin absorbed it
        return None
    monkeypatch.setattr(ce, "_recycle_one", _recycle_one)

    result = ce.move_to_recycle_bin([path])
    assert result.succeeded == [path]
    assert result.not_recycled == []


def test_a_large_item_the_bin_refused_is_flagged_as_permanent(tmp_path, bin_queries, monkeypatch):
    """The bug: gone from disk, absent from the bin, reported as success."""
    path, size = _make(tmp_path, "huge.bin", BIG)
    monkeypatch.setattr(ce, "_get_size", lambda p: size)

    def _recycle_one(p):
        os.remove(p)          # deleted outright — the bin never grows
        return None
    monkeypatch.setattr(ce, "_recycle_one", _recycle_one)

    result = ce.move_to_recycle_bin([path])
    assert result.succeeded == [path], "the removal itself did work"
    assert result.not_recycled == [path], (
        "an item that never reached the bin must not be described as recoverable"
    )


def test_small_items_are_not_queried(tmp_path, bin_queries, monkeypatch):
    """Verification must not cost a shell round trip per cached file."""
    paths = []
    for i in range(5):
        p, _ = _make(tmp_path, f"small{i}.dat", SMALL)
        paths.append(p)
    monkeypatch.setattr(ce, "_get_size", lambda p: SMALL)
    monkeypatch.setattr(ce, "_recycle_one", lambda p: (os.remove(p), None)[1])

    result = ce.move_to_recycle_bin(paths)
    assert len(result.succeeded) == 5
    assert result.not_recycled == []
    assert bin_queries["queries"] == 0, (
        "small items cannot exceed the bin quota — querying for each one is "
        "pure overhead on a cleanup of thousands of cache files"
    )


def test_an_unavailable_bin_api_is_not_read_as_permanent_deletion(tmp_path, monkeypatch):
    """(0, 0) means 'no answer' as well as 'empty' — never accuse on silence."""
    path, size = _make(tmp_path, "big.bin", BIG)
    monkeypatch.setattr(ce, "_get_size", lambda p: size)
    monkeypatch.setattr(ce, "_recycle_one", lambda p: (os.remove(p), None)[1])
    monkeypatch.setattr(ce, "_bin_size_for", lambda p: None)

    result = ce.move_to_recycle_bin([path])
    assert result.succeeded == [path]
    assert result.not_recycled == [], (
        "an unmeasurable bin must not be reported as a permanent deletion"
    )


def test_bin_size_for_returns_none_when_the_query_fails(monkeypatch):
    from app.services import recycle_bin

    monkeypatch.setattr(recycle_bin, "recycle_bin_status", lambda drive=None: (0, 0))
    assert ce._bin_size_for(r"C:\some\path") is None


def test_the_worker_flags_it_too(tmp_path, monkeypatch):
    """The dialog runs CleanupWorker, which has its own copy of the loop.

    The first fix landed only in move_to_recycle_bin, which the UI never calls.
    """
    path, size = _make(tmp_path, "huge.bin", BIG)
    monkeypatch.setattr(ce, "_get_size", lambda p: size)
    monkeypatch.setattr(ce, "_recycle_one", lambda p: (os.remove(p), None)[1])
    monkeypatch.setattr(ce, "_bin_size_for", lambda p: 1_000_000)

    worker = ce.CleanupWorker([path], mode=ce.CleanupWorker.MODE_RECYCLE)
    captured = []
    worker.finished.connect(captured.append)
    worker.run()                      # run() directly — no thread needed

    assert captured, "worker did not emit a result"
    assert captured[0].not_recycled == [path]


def test_permanent_delete_mode_is_untouched_by_verification(tmp_path, monkeypatch):
    """Nothing to verify when the user asked for a permanent delete."""
    path, size = _make(tmp_path, "big.bin", BIG)
    monkeypatch.setattr(ce, "_get_size", lambda p: size)

    called = []
    monkeypatch.setattr(ce, "_bin_size_for", lambda p: called.append(p) or 0)

    worker = ce.CleanupWorker([path], mode=ce.CleanupWorker.MODE_PERMANENT,
                              perm_delete_enabled=True)
    captured = []
    worker.finished.connect(captured.append)
    worker.run()

    assert captured[0].succeeded == [path]
    assert captured[0].not_recycled == []
    assert called == [], "the bin was queried for a permanent delete"


def test_the_summary_stops_promising_recoverability_when_something_was_not(qapp):
    """The warning and the summary must not contradict each other.

    The result panel prints a per-batch warning *and* a closing assessment.
    The assessment is built from counts alone, so it happily appended "anything
    that was cleaned remains recoverable in the Recycle Bin" directly under a
    line saying an item had been permanently deleted.
    """
    from app.services.cleanup_result_classifier import assess_cleanup_counts

    counts = dict(succeeded_count=2, in_use_count=0, failed_count=0,
                  skipped_count=0, category_label="Selected items")

    everything_recoverable = assess_cleanup_counts(**counts).explanation_text
    assert "remains recoverable" in everything_recoverable

    partly_permanent = assess_cleanup_counts(
        **counts, all_recoverable=False).explanation_text
    assert "remains recoverable" not in partly_permanent
    assert "permanently" in partly_permanent


def test_the_dialog_passes_the_flag_through(tmp_path, qapp, monkeypatch):
    """End-to-end: a not_recycled result must reach the assessment."""
    from app.screens.cleanup_dialog import CleanupConfirmDialog

    items = [dict(path="C:/x/models", name="models", entity_type="ai_models",
                  category="AI / ML", risk="Review", size_bytes=41_000_000_000,
                  size="38.2 GB", is_dir=True, reason="AI/ML model storage")]
    result = ce.CleanupResult()
    result.succeeded = ["C:/x/models", "C:/x/cache"]
    result.not_recycled = ["C:/x/models"]
    result.total_bytes_freed = 41_000_000_000

    dlg = CleanupConfirmDialog(items)
    dlg._present_as_progress()
    dlg._on_finished(result)
    text = dlg._result_lbl.text()

    assert "cannot be restored" in text, "the permanent-deletion warning is missing"
    assert "remains recoverable" not in text, (
        "the summary still promises full recoverability under the warning"
    )
    dlg.deleteLater()
