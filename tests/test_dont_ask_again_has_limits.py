"""Turning confirmation off must not turn off the questions that matter.

The dialog offered "Don't ask again for review/uncertain items". Ticking it
stored confirm_risky_cleanup=False, and from then on *every* cleanup - Review
items, personal folders, items in a OneDrive folder - started without a
question. The cloud acknowledgment ("I understand this will delete files from
my cloud account") lived only in the confirm button's enabled state, and the
auto-confirm route never pressed the button.

The rule now: skipping the confirmation applies only to a selection made
entirely of Safe and Optional items outside any cloud-sync folder. Anything
that needs review, or that would delete from a cloud account, is always asked
about - and the cloud permission handed to the worker comes from the checkbox
itself, so a route that skips the button cannot carry it.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


SAFE = {"path": "C:/Dev/app/node_modules", "name": "node_modules",
        "risk": "Safe", "entity_type": "node_modules", "actionability": "recycle",
        "size_bytes": 2_000_000, "is_dir": True}
OPTIONAL = {"path": "C:/Data/old.zip", "name": "old.zip", "risk": "Optional",
            "entity_type": "archive_group", "actionability": "recycle",
            "size_bytes": 1_000, "is_dir": False}
REVIEW = {"path": "D:/Work/ClientX", "name": "ClientX", "risk": "Review",
          "entity_type": "unknown_folder", "actionability": "review_only",
          "size_bytes": 5_000_000, "is_dir": True}
CLOUD = {"path": "C:/Users/ann/OneDrive/Cache", "name": "Cache", "risk": "Optional",
         "entity_type": "cache_folder", "actionability": "recycle",
         "cloud_sync_provider": "OneDrive", "size_bytes": 1_000, "is_dir": True}


@pytest.fixture
def started(monkeypatch):
    calls = []
    from app.screens import cleanup_dialog
    monkeypatch.setattr(cleanup_dialog.CleanupConfirmDialog, "_on_confirm",
                        lambda self: calls.append(self))
    return calls


def _dialog(items, auto):
    """Build the dialog and let a scheduled auto-start run, if one was set."""
    from PySide6.QtWidgets import QApplication
    from app.screens.cleanup_dialog import CleanupConfirmDialog
    dlg = CleanupConfirmDialog(items=items, auto_confirm=auto)
    QApplication.processEvents()
    return dlg


# ── when confirmation is off ──────────────────────────────────────

def test_safe_and_optional_items_still_start_on_their_own(qapp, started):
    _dialog([SAFE, OPTIONAL], auto=True)
    assert len(started) == 1


@pytest.mark.parametrize("items", [[REVIEW], [SAFE, REVIEW]], ids=["review", "mixed"])
def test_a_review_item_is_always_asked_about(qapp, started, items):
    dlg = _dialog(items, auto=True)
    assert started == [], "a Review item was removed without a question"
    assert not dlg._btn_confirm.isHidden(), "there is nothing left to answer with"


@pytest.mark.parametrize("items", [[CLOUD], [SAFE, CLOUD]], ids=["cloud", "mixed"])
def test_a_cloud_item_is_always_asked_about(qapp, started, items):
    _dialog(items, auto=True)
    assert started == [], "a cloud-synced item was removed without a question"


# ── the permission the worker receives ────────────────────────────

@pytest.fixture
def worker_kwargs(monkeypatch):
    seen = {}
    from app.screens import cleanup_dialog

    class _FakeWorker:
        MODE_RECYCLE = "recycle_bin"
        MODE_PERMANENT = "permanent"

        def __init__(self, **kw):
            seen.update(kw)
            from types import SimpleNamespace
            sig = SimpleNamespace(connect=lambda *a, **k: None)
            self.progress = self.log_line = self.finished = sig

        def start(self):
            pass

        def isRunning(self):
            return False

    monkeypatch.setattr(cleanup_dialog, "CleanupWorker", _FakeWorker)
    return seen


def test_confirming_without_the_cloud_box_grants_no_cloud_permission(qapp, worker_kwargs):
    dlg = _dialog([CLOUD], auto=False)
    dlg._on_confirm()          # the route that does not go through the button
    assert worker_kwargs.get("allow_cloud") is False


def test_ticking_the_cloud_box_is_what_grants_it(qapp, worker_kwargs):
    dlg = _dialog([CLOUD], auto=False)
    dlg._cloud_cb.setChecked(True)
    dlg._on_confirm()
    assert worker_kwargs.get("allow_cloud") is True


# ── what the tick box offers ──────────────────────────────────────

def test_the_tick_box_is_offered_where_it_applies(qapp, started):
    dlg = _dialog([SAFE, OPTIONAL], auto=False)
    assert dlg._dont_ask_cb is not None
    assert "Safe" in dlg._dont_ask_cb.text()
    assert "review" not in dlg._dont_ask_cb.text().lower()


@pytest.mark.parametrize("items", [[REVIEW], [CLOUD]], ids=["review", "cloud"])
def test_the_tick_box_is_not_offered_where_it_would_not_apply(qapp, started, items):
    dlg = _dialog(items, auto=False)
    assert dlg._dont_ask_cb is None
