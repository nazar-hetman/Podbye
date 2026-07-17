"""Tests for drive/volume awareness (app.services.drives) and the scanner's
cross-volume safeguard. These exercise the stdlib fallbacks and the st_dev
isolation check without needing a second physical volume.
"""
import os

from app.services import drives
from app.services.scanner import ScanWorker

_HERE = os.path.dirname(os.path.abspath(__file__))


# ── drives module ─────────────────────────────────────────────────

def test_disk_usage_returns_positive_totals():
    total, used, free = drives.disk_usage(_HERE)
    assert total > 0
    assert 0 <= free <= total
    assert used <= total


def test_disk_usage_bad_path_is_zeroed():
    assert drives.disk_usage("Z:/no/such/path/exists/here") == (0, 0, 0)


def test_same_volume_self_is_true():
    assert drives.same_volume(_HERE, _HERE) is True


def test_same_volume_two_paths_same_drive():
    parent = os.path.dirname(_HERE)
    assert drives.same_volume(_HERE, parent) is True


def test_same_volume_fails_open_on_error():
    # A stat error must not be reported as "different volume".
    assert drives.same_volume("Z:/nope", "Z:/also-nope") is True


def test_summarize_has_capacity_and_kind():
    info = drives.summarize(_HERE)
    assert info is not None
    assert info.total > 0
    assert info.kind in {"Fixed", "Removable", "Network", "Optical", "Unknown"}
    assert 0.0 <= info.percent_used <= 100.0


def test_drive_kind_returns_known_label():
    assert drives.drive_kind(_HERE) in {
        "Fixed", "Removable", "Network", "Optical", "Unknown"
    }


def test_list_drives_is_a_list():
    out = drives.list_drives()
    assert isinstance(out, list)
    # With psutil present we expect at least one drive; without it, empty.
    if drives.HAVE_PSUTIL:
        assert out and all(d.total > 0 for d in out)


# ── scanner cross-volume safeguard ────────────────────────────────

def test_worker_defaults_to_no_cross_volume():
    w = ScanWorker(_HERE)
    assert w._cross_volumes is False
    assert w._root_dev is None  # set only when run() starts


def test_crosses_volume_false_for_same_volume():
    w = ScanWorker(_HERE)
    w._root_dev = os.stat(_HERE).st_dev
    assert w._crosses_volume(_HERE) is False


def test_crosses_volume_fails_open_on_stat_error():
    w = ScanWorker(_HERE)
    w._root_dev = os.stat(_HERE).st_dev
    assert w._crosses_volume("Z:/definitely/not/here") is False
