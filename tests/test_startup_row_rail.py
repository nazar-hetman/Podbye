"""Startup rows carry a right-hand column, like the Findings rows do.

A Findings row ends in size + last-active. A startup entry has neither, so its
rows just stopped, and the role it plays at login was buried mid-sentence in
the meta line next to the publisher and the source.

The rail now carries the two things a startup entry does have: that role, and
how old the binary behind it is — the only staleness signal available for one,
and the thing that distinguishes a program you use from a leftover.
"""
import os
import time

import app.screens.startups as st

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.models.startup_entry import StartupEntry

YEAR = 365 * 86400


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _entry(**over):
    kwargs = dict(
        name="Grammarly", command="C:/g/g.exe", path="C:/g/g.exe",
        publisher="Grammarly Inc.", source="run_hkcu",
        source_label="User startup registry", enabled=True,
        risk="Optional", risk_reason="convenience", impact="Creative helper",
        target_modified=time.time() - 30 * 86400,
    )
    kwargs.update(over)
    return StartupEntry(**kwargs)


def _row(qapp, entry):
    from app.screens.startups import StartupListRow
    return StartupListRow(entry)


# ── what each part of the row shows ───────────────────────────────

def test_the_row_has_no_column_under_its_buttons(qapp):
    """It held three things in turn — the target date, then only a stale date,
    then AI status — and each read as a caption for the button above it.

    A row now has two places and one rule: prose on the left, controls on the
    right, and a status is prose. Nothing is appended beneath a control.
    """
    row = _row(qapp, _entry())

    assert not hasattr(row, "_rail_lbl")


def test_a_stale_target_says_so_in_the_meta_line(qapp):
    """The signal survives the move — in words, where the row's prose lives."""
    old = time.time() - (st._STALE_TARGET_YEARS + 1) * 365 * 86400
    row = _row(qapp, _entry(target_modified=old))

    # full_text(), not text(): the meta line elides to whatever width the row
    # has, and an unshown row has almost none.
    assert "not updated since" in row._meta_lbl.full_text()
    assert row._entry.target_modified_display in row._meta_lbl.full_text()


def test_a_current_target_says_nothing_about_its_age(qapp):
    """Only the stale case is worth a word; everything else is just a date."""
    row = _row(qapp, _entry(target_modified=time.time() - 30 * 86400))

    assert "not updated" not in row._meta_lbl.full_text()


def test_the_role_sits_in_the_meta_line(qapp):
    """A fixed-width column cannot hold the role and the date together.

    "Remote access service · updated 2026-08-04" would force a column wide
    enough to leave every shorter row looking empty, so the role joins the
    publisher and source on the meta line and the column keeps only the date.
    """
    row = _row(qapp, _entry())
    meta = row._meta_lbl.text()

    assert "Creative helper" in meta
    assert "Grammarly Inc." in meta
    assert "User startup registry" in meta


def test_the_role_is_stated_once(qapp):
    row = _row(qapp, _entry())
    assert row._meta_lbl.full_text().count("Creative helper") == 1


# ── missing data is omitted, not padded ───────────────────────────

def test_an_unreadable_target_date_says_nothing_about_age(qapp):
    """Scheduled tasks whose executable has moved have no mtime to show."""
    row = _row(qapp, _entry(target_modified=0.0))

    assert "not updated" not in row._meta_lbl.full_text()
    assert "Creative helper" in row._meta_lbl.full_text(), "the role must survive"


def test_no_placeholder_dash_is_invented(qapp):
    row = _row(qapp, _entry(impact="", target_modified=0.0))

    assert "—" not in row._meta_lbl.full_text()


# ── the columns are fixed, which is the point ─────────────────────

def _rows_for_alignment(qapp):
    """Rows whose badge, action and date all differ in natural width."""
    return [
        _row(qapp, _entry(name="Grammarly", risk="Optional", enabled=True,
                          impact="Creative helper")),
        _row(qapp, _entry(name="Windows Defender", risk="Protected", enabled=True,
                          impact="Security component")),
        _row(qapp, _entry(name="OneDrive", risk="Review", enabled=False,
                          impact="Background sync", target_modified=0.0)),
        _row(qapp, _entry(name="Steam", risk="Safe", enabled=True,
                          impact="Game launcher")),
    ]


def test_the_badge_column_is_the_same_width_on_every_row(qapp):
    widths = {r._risk_badge.width() for r in _rows_for_alignment(qapp)}
    assert len(widths) == 1, f"badge column varies by row: {widths}"


def test_the_action_column_is_the_same_width_on_every_row(qapp):
    """Disable/Enable differ in length; the column must not."""
    widths = {r._toggle_btn.width() for r in _rows_for_alignment(qapp)}
    assert len(widths) == 1, f"action column varies by row: {widths}"


def test_the_controls_are_the_only_thing_in_their_column(qapp):
    """The badge and the action keep their fixed widths — that is what stops
    the column scattering — and nothing else is placed beneath them."""
    from app.screens.startups import StartupListRow
    row = _row(qapp, _entry())

    assert row._risk_badge.width() == StartupListRow._BADGE_W
    assert row._toggle_btn.width() == StartupListRow._ACTION_W


def test_the_columns_hold_their_longest_label(qapp):
    """A fixed width that clips its own text would be worse than jitter.

    Measured with the app's own fonts loaded, as main() does — a bare test
    process has different metrics and would set the bar in the wrong place.
    """
    from PySide6.QtGui import QFont
    from app.fonts import load_fonts, FONT_UI
    from app.widgets.pills import Badge
    from app.screens.startups import StartupListRow

    previous = qapp.font()
    load_fonts()
    qapp.setFont(QFont(FONT_UI, 10))
    try:
        widest_badge = max(Badge(t, "info").sizeHint().width()
                           for t in ("PROTECTED", "OPTIONAL", "REVIEW", "SAFE"))
        row = _row(qapp, _entry(risk="Protected", enabled=True))
        widest_action = max(
            row._toggle_btn.fontMetrics().horizontalAdvance(t)
            for t in ("Disable", "Enable"))
    finally:
        qapp.setFont(previous)

    assert StartupListRow._BADGE_W >= widest_badge, "the badge column clips"
    assert StartupListRow._ACTION_W >= widest_action, "the action column clips"


# ── the action is text, not a button box ──────────────────────────

def test_the_action_is_a_compact_button(qapp):
    """This used to assert the opposite, and the reasoning it recorded still
    stands: twenty-five outlined buttons outweighed the entries they belonged
    to. The answer was a smaller button, not the loss of the affordance - as
    bare text the one control this screen exists to offer read as a label. It
    keeps a border and a fill at 10px with tight padding.

    Width is pinned with it: a styled QPushButton cannot shrink below its own
    chrome, so at the old 54px column it overflowed and squeezed the name and
    meta labels beside it into nothing.
    """
    row = _row(qapp, _entry())
    qss = row._toggle_btn.styleSheet()

    assert "border: 1px solid" in qss
    assert "background: transparent" not in qss
    assert "font-size: 10px" in qss
    assert row._ACTION_W >= 80, "no room for the label in French"


# ── staleness ─────────────────────────────────────────────────────

def test_a_recent_target_is_not_flagged(qapp):
    from app.screens.startups import _target_is_stale
    assert not _target_is_stale(_entry(target_modified=time.time() - 30 * 86400))


def test_an_old_target_is_flagged(qapp):
    from app.screens.startups import _target_is_stale
    assert _target_is_stale(_entry(target_modified=time.time() - 5 * YEAR))


def test_an_unknown_date_is_not_called_stale(qapp):
    """Unreadable is not the same as old, and must not be coloured as a warning."""
    from app.screens.startups import _target_is_stale
    assert not _target_is_stale(_entry(target_modified=0.0))


def test_a_stale_row_explains_itself_on_hover(qapp):
    row = _row(qapp, _entry(target_modified=time.time() - 5 * YEAR))
    assert "no longer use" in row._meta_lbl.toolTip()


# ── the model side ────────────────────────────────────────────────

def test_the_date_formats_as_a_plain_day():
    entry = _entry(target_modified=time.mktime((2021, 12, 5, 0, 0, 0, 0, 0, -1)))
    assert entry.target_modified_display == "2021-12-05"


def test_no_date_reads_as_empty_not_epoch():
    assert _entry(target_modified=0.0).target_modified_display == ""


def test_a_nonsense_timestamp_does_not_raise():
    assert _entry(target_modified=1e18).target_modified_display == ""


def test_the_detector_reads_the_target_mtime(tmp_path):
    from app.services.startup_detector import _target_mtime
    exe = tmp_path / "thing.exe"
    exe.write_bytes(b"MZ")
    assert _target_mtime(str(exe)) == pytest.approx(exe.stat().st_mtime)


def test_a_missing_target_reads_as_unknown(tmp_path):
    from app.services.startup_detector import _target_mtime
    assert _target_mtime(str(tmp_path / "gone.exe")) == 0.0
    assert _target_mtime("") == 0.0
