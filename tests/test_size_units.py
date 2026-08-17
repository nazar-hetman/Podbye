"""Sizes keep climbing units instead of piling up in GB.

The all-time counters on Home are cumulative across every scan ever run, so
they leave the range GB is readable in. A reporting machine showed
"13542.8 GB analyzed" — five significant digits of a unit nobody counts that
high in, in a mini-stat sized for four.

The unit ladder itself lives in one place; what this file also pins is that
the screens which style the number and the unit as separate labels get both
from that ladder, instead of re-deriving the unit with a local if-chain (which
is exactly how they stayed capped at GB).
"""
import pytest

from app.models.finding import _format_size, split_size

KB = 1024
MB = 1024 ** 2
GB = 1024 ** 3
TB = 1024 ** 4
PB = 1024 ** 5


@pytest.mark.parametrize("size_bytes, expected", [
    # The reported figure.
    (int(13542.8 * GB), "13.2 TB"),
    (TB, "1.0 TB"),
    (PB, "1.0 PB"),
    (2 * PB, "2.0 PB"),
    # Just under a unit, where the threshold and the rounded value disagree.
    (TB - 1, "1.0 TB"),
    (PB - 1, "1.0 PB"),
    # Still GB right up to the boundary — the ladder must not promote early.
    (1023 * GB, "1023.0 GB"),
    (999 * GB, "999.0 GB"),
])
def test_large_sizes_climb_past_gigabytes(size_bytes, expected):
    assert _format_size(size_bytes) == expected


@pytest.mark.parametrize("size_bytes, expected", [
    (0, "0 B"),
    (1023, "1023 B"),
    (KB, "1 KB"),
    (MB, "1 MB"),
    (GB, "1.0 GB"),
    (GB - 1, "1.0 GB"),
])
def test_the_units_below_a_terabyte_are_unchanged(size_bytes, expected):
    """The ladder was rewritten; everything already on screen must be untouched."""
    assert _format_size(size_bytes) == expected


def test_every_unit_is_reachable():
    """A ladder rung nothing can land on is a typo, not a unit."""
    seen = {_format_size(n).split(" ")[1]
            for n in (5, 5 * KB, 5 * MB, 5 * GB, 5 * TB, 5 * PB)}
    assert seen == {"B", "KB", "MB", "GB", "TB", "PB"}


def test_the_number_never_reaches_four_digits_before_the_top_unit():
    """The point of the change: a value that has outgrown its unit rolls over."""
    for exponent in range(0, 51):
        out = _format_size(2 ** exponent)
        number, unit = out.split(" ")
        if unit == "PB":
            continue  # nothing above it to roll into
        assert float(number) < 1024, f"{2 ** exponent} -> {out!r}"


# ── the two-label readouts ────────────────────────────────────────

@pytest.mark.parametrize("size_bytes", [
    0, 512, KB, 40 * MB, 3 * GB, int(13542.8 * GB), 7 * TB, PB,
])
def test_split_size_agrees_with_the_formatter(size_bytes):
    number, unit = split_size(size_bytes)
    assert f"{number} {unit}" == _format_size(size_bytes)


def test_split_size_reaches_terabytes():
    """Home's freed hero and Quick Cleanup's total used to hardcode GB."""
    assert split_size(7 * TB) == ("7.0", "TB")


def test_split_size_handles_zero():
    """Quick Cleanup renders this before anything is selected."""
    assert split_size(0) == ("0", "B")


def test_settings_reuses_the_shared_ladder():
    """Its private copy stopped at GB and missed the boundary-rounding fix."""
    from app.screens.settings import _human_size
    for n in (0, 1023, MB, GB - 1, 13542 * GB, PB):
        assert _human_size(n) == _format_size(n)
