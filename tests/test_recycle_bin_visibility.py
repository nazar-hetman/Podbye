"""Cleaning does not free disk space until the bin is emptied — say so.

Podbye sends cleanup to the Recycle Bin by default. The normal cleanup path is a *move* to the Recycle Bin,
so nothing it does is irreversible. But a move on the same volume is a rename,
and not one byte comes back until the bin is emptied. Nobody was told that. On
one real machine the bin held 16.7 GB across 795 items while the user kept
running Quick Cleanup and wondering why "a couple of GB can't be deleted".

Emptying stays the user's own explicit decision. The number being visible is
not optional — without it the app's central claim, "you got space back", is
not true.
"""
import pytest

from app.services.recycle_bin import empty_recycle_bin, recycle_bin_status


def test_status_returns_two_non_negative_numbers():
    size_bytes, items = recycle_bin_status()
    assert isinstance(size_bytes, int) and isinstance(items, int)
    assert size_bytes >= 0 and items >= 0


def test_a_failed_query_reports_empty_not_a_windfall():
    """A number Podbye could not read must never be shown as space to reclaim."""
    import app.services.recycle_bin as rb

    class _Boom:
        def __getattr__(self, name):
            raise OSError("no shell32 here")

    original = rb.ctypes.windll
    rb.ctypes.windll = _Boom()
    try:
        assert rb.recycle_bin_status() == (0, 0)
    finally:
        rb.ctypes.windll = original


def test_emptying_reports_a_failure_rather_than_claiming_success():
    import app.services.recycle_bin as rb

    class _Shell:
        def SHEmptyRecycleBinW(self, *_a):
            return 5            # some Win32 error
    class _Windll:
        shell32 = _Shell()

    original = rb.ctypes.windll
    rb.ctypes.windll = _Windll()
    try:
        ok, message = empty_recycle_bin()
        assert ok is False
        assert "5" in message
    finally:
        rb.ctypes.windll = original


def test_a_successful_empty_is_reported_as_success():
    import app.services.recycle_bin as rb

    class _Shell:
        def SHEmptyRecycleBinW(self, *_a):
            return 0
    class _Windll:
        shell32 = _Shell()

    original = rb.ctypes.windll
    rb.ctypes.windll = _Windll()
    try:
        ok, _message = empty_recycle_bin()
        assert ok is True
    finally:
        rb.ctypes.windll = original


# ── the screen shows it ───────────────────────────────────────────


@pytest.fixture
def quick(qapp):
    from app.config.settings_store import SettingsStore
    from app.screens.quick_cleanup import QuickCleanupScreen
    screen = QuickCleanupScreen()
    screen.set_settings_store(SettingsStore())
    yield screen
    screen.stop_background_work(3000)
    screen.close()
    screen.deleteLater()
    qapp.processEvents()


def test_quick_cleanup_shows_what_is_waiting_in_the_bin(quick, qapp):
    quick.refresh_recycle_bin()
    qapp.processEvents()
    assert quick._bin_lbl.text().strip(), "the bin figure must not be blank"


def test_the_empty_button_is_disabled_when_there_is_nothing_to_empty(quick, qapp, monkeypatch):
    monkeypatch.setattr("app.services.recycle_bin.recycle_bin_status",
                        lambda drive=None: (0, 0))
    quick.refresh_recycle_bin()
    assert not quick._btn_empty_bin.isEnabled()
    assert quick._bin_note.isHidden(), "no nudge when there is nothing to reclaim"


def test_the_warning_appears_only_when_the_bin_holds_something(quick, qapp, monkeypatch):
    monkeypatch.setattr("app.services.recycle_bin.recycle_bin_status",
                        lambda drive=None: (16 * 1024 ** 3, 795))
    quick.refresh_recycle_bin()
    qapp.processEvents()
    assert quick._btn_empty_bin.isEnabled()
    assert "16" in quick._bin_lbl.text()
    assert "795" in quick._bin_lbl.text()
