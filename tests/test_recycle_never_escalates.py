"""Choosing the Recycle Bin is not consent to permanent deletion.

``_recycle_one`` calls SHFileOperationW with ``FOF_NOCONFIRMATION``, and
Windows answers a request it cannot satisfy by destroying the file: an item
larger than the volume's Recycle Bin quota is deleted outright, and **the call
still reports success**. A volume with ``NukeOnDelete`` set does that to
everything on it.

Podbye used to notice afterwards — the path landed in ``not_recycled`` and the
log said "NOT recoverable ... removed permanently". By then the file was gone.
A user who picked "Move to Recycle Bin" had their data destroyed without ever
being asked, which is the one escalation a cleanup tool must not make.

The quota is per-volume and readable from the registry before anything is
touched, so it is now a refusal rather than a discovery: the item is left on
disk and reported. This is not hypothetical — on the reporting machine C:'s
bin caps at 9.8 GB, and Podbye routinely lists games and model folders larger
than that.

Permanent deletion stays available, unchanged, as the mode the user selects on
purpose.
"""
import os

import pytest

from app.services import cleanup_engine
from app.services.cleanup_engine import CleanupResult, CleanupWorker
from app.services.recycle_bin import RecyclePolicy

GB = 1024 ** 3


@pytest.fixture
def victim(tmp_path):
    target = tmp_path / "big.bin"
    target.write_bytes(b"\0" * 2048)
    return str(target).replace("\\", "/")


def _policy(monkeypatch, **kw):
    monkeypatch.setattr("app.services.recycle_bin.recycle_bin_policy",
                        lambda path: RecyclePolicy(**kw))


def _never_deletes(monkeypatch):
    """Fail loudly if anything actually removes a file."""
    def _boom(path):
        raise AssertionError(f"the item was destroyed: {path}")

    monkeypatch.setattr(cleanup_engine, "_recycle_one", _boom)
    monkeypatch.setattr(cleanup_engine, "_delete_one", _boom)


# ── the policy reader ─────────────────────────────────────────────

def test_an_item_over_the_quota_is_refused():
    policy = RecyclePolicy(nuke_on_delete=False, max_bytes=10 * GB)

    assert policy.refuses(11 * GB) == "too_large"
    assert policy.refuses(9 * GB) == ""


def test_a_volume_with_the_bin_switched_off_refuses_everything():
    policy = RecyclePolicy(nuke_on_delete=True, max_bytes=10 * GB)

    assert policy.refuses(1) == "bin_disabled"


def test_an_unreadable_policy_refuses_nothing():
    """A machine where the registry cannot be read must still be able to
    clean up. Unknown is not "unlimited", but it is not a reason to stop —
    the post-hoc verification still covers what it can."""
    policy = RecyclePolicy()

    assert policy.refuses(500 * GB) == ""
    assert not policy.known


def test_the_managed_capacity_sentinel_is_not_read_as_a_size():
    """MaxCapacity of -1 means "let Windows decide", not "minus one byte"."""
    import winreg

    from app.services import recycle_bin

    class _Key:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _query(key, name):
        return (-1 if name == "MaxCapacity" else 0), winreg.REG_DWORD

    original_open, original_q = winreg.OpenKey, winreg.QueryValueEx
    winreg.OpenKey = lambda *a, **k: _Key()
    winreg.QueryValueEx = _query
    recycle_bin._volume_guid_original = recycle_bin._volume_guid
    recycle_bin._volume_guid = lambda path: "{guid}"
    try:
        policy = recycle_bin.recycle_bin_policy("C:/x")
        assert policy.max_bytes is None
        assert policy.refuses(500 * GB) == ""
    finally:
        winreg.OpenKey, winreg.QueryValueEx = original_open, original_q
        recycle_bin._volume_guid = recycle_bin._volume_guid_original


# ── nothing is destroyed to satisfy a recycle request ─────────────

def test_an_oversized_item_is_left_on_disk(victim, monkeypatch):
    _policy(monkeypatch, nuke_on_delete=False, max_bytes=1024)   # 2 KB file
    _never_deletes(monkeypatch)

    result = cleanup_engine.move_to_recycle_bin([victim])

    assert os.path.exists(victim), "the file was removed anyway"
    assert result.skipped_not_recyclable == {victim: "too_large"}
    assert result.succeeded == []
    assert result.not_recycled == []


def test_a_disabled_bin_leaves_everything_on_disk(victim, monkeypatch):
    _policy(monkeypatch, nuke_on_delete=True, max_bytes=100 * GB)
    _never_deletes(monkeypatch)

    result = cleanup_engine.move_to_recycle_bin([victim])

    assert os.path.exists(victim)
    assert result.skipped_not_recyclable == {victim: "bin_disabled"}


def test_a_refusal_is_not_counted_as_removed(victim, monkeypatch):
    """It must not reach the freed total, or the summary would claim space
    that is still occupied."""
    _policy(monkeypatch, nuke_on_delete=False, max_bytes=1024)
    _never_deletes(monkeypatch)

    result = cleanup_engine.move_to_recycle_bin([victim])

    assert result.total_bytes_freed == 0
    assert victim not in result.failed
    assert victim not in result.in_use


def test_an_item_that_fits_is_recycled_normally(victim, monkeypatch):
    """The guard must not stop ordinary cleanup."""
    _policy(monkeypatch, nuke_on_delete=False, max_bytes=100 * GB)
    monkeypatch.setattr(cleanup_engine, "_recycle_one", lambda path: None)

    result = cleanup_engine.move_to_recycle_bin([victim])

    assert result.succeeded == [victim]
    assert result.skipped_not_recyclable == {}


# ── the same guard on the worker the UI actually uses ─────────────

def _run(worker):
    """Drive run() on this thread — it is a plain method."""
    captured = {}
    worker.finished.connect(lambda r: captured.setdefault("result", r))
    worker.run()
    return captured["result"]


def test_the_worker_refuses_an_oversized_item(victim, monkeypatch, qapp):
    _policy(monkeypatch, nuke_on_delete=False, max_bytes=1024)
    _never_deletes(monkeypatch)

    result = _run(CleanupWorker(paths=[victim], mode=CleanupWorker.MODE_RECYCLE))

    assert os.path.exists(victim)
    assert result.skipped_not_recyclable == {victim: "too_large"}


def test_the_worker_says_why_it_kept_the_file(victim, monkeypatch, qapp):
    _policy(monkeypatch, nuke_on_delete=False, max_bytes=1024)
    _never_deletes(monkeypatch)
    worker = CleanupWorker(paths=[victim], mode=CleanupWorker.MODE_RECYCLE)
    lines = []
    worker.log_line.connect(lines.append)

    _run(worker)

    said = "\n".join(lines)
    assert "kept on disk" in said.lower()
    assert "permanent" in said.lower()


# ── permanent mode is untouched, and still opt-in ─────────────────

def test_permanent_mode_still_deletes_when_the_user_chose_it(victim, monkeypatch, qapp):
    """The quota has nothing to say about a permanent delete — the user asked
    for exactly that, with the setting enabled."""
    _policy(monkeypatch, nuke_on_delete=True, max_bytes=1)
    removed = []
    monkeypatch.setattr(cleanup_engine, "_delete_one",
                        lambda path: removed.append(path) or None)

    result = _run(CleanupWorker(paths=[victim], mode=CleanupWorker.MODE_PERMANENT,
                                perm_delete_enabled=True))

    assert removed == [victim]
    assert result.succeeded == [victim]
    assert result.skipped_not_recyclable == {}


def test_permanent_mode_without_the_setting_still_refuses(victim, monkeypatch, qapp):
    _never_deletes(monkeypatch)

    result = _run(CleanupWorker(paths=[victim], mode=CleanupWorker.MODE_PERMANENT,
                                perm_delete_enabled=False))

    assert os.path.exists(victim)
    assert result.failed == [victim]


# ── and the result says what happened ─────────────────────────────

def test_the_result_panel_reports_a_refusal(qapp, victim):
    from PySide6.QtWidgets import QFrame, QVBoxLayout

    from app.screens.cleanup_dialog import CleanupConfirmDialog
    from app.themes.theme_manager import build_qss

    qapp.setStyleSheet(build_qss("forest"))
    result = CleanupResult()
    result.skipped_not_recyclable = {victim: "too_large"}

    dlg = CleanupConfirmDialog.__new__(CleanupConfirmDialog)
    dlg._issues = []
    dlg._issue_colors = {}
    host = QFrame()
    dlg._issues_body_layout = QVBoxLayout(host)
    dlg._issues_frame = QFrame()
    dlg._issues_toggle = None
    # The button belongs to the assembled dialog; what is under test is which
    # issues get collected, not how many the toggle claims.
    dlg._update_issues_button = lambda: None
    try:
        dlg._populate_issues(result)
    finally:
        host.deleteLater()
        qapp.processEvents()

    reasons = [reason for reason, _path, _detail in dlg._issues]
    details = " ".join(detail for _r, _p, detail in dlg._issues)
    assert "Kept on disk" in reasons, reasons
    assert "would have been permanent" in details
    assert victim in [path for _r, path, _d in dlg._issues]
