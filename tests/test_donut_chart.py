"""The storage donut answers "what is taking the space", not "what exists".

A real C:/ scan produces 19 categories. Rendered one wedge each, the tail is a
fringe of sub-degree slivers — unreadable, unclickable, and indistinguishable
from one another. Worse, the 1.4° separator gap was subtracted from each wedge
without a floor, so a sliver narrower than the gap got a *negative* sweep and
Qt drew it counter-clockwise, back across its neighbours. That is the speckled
band at the top of the ring.

The category list beside the chart still lists every category; only the ring
pools its tail.
"""
import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def donut(qapp):
    from app.screens.findings_dashboard import DonutChartWidget
    w = DonutChartWidget()
    w.resize(260, 260)
    yield w
    w.deleteLater()
    qapp.processEvents()


def _cats(sizes):
    """(category, data) pairs, largest first, like the overview builds."""
    total = sum(sizes) or 1
    return [(f"Cat{i}", {"size_bytes": s, "percentage": 100.0 * s / total})
            for i, s in enumerate(sorted(sizes, reverse=True))]


# The real shape: four big categories, then a long tail of fractions.
REAL_SCAN = _cats([124_700, 98_700, 55_400, 46_500, 17_600, 12_100, 7_400,
                   4_900, 4_000, 2_100, 2_100, 2_000, 900, 800, 400, 300,
                   200, 100, 50])


# ── pooling the tail ──────────────────────────────────────────────

def test_a_long_tail_is_pooled_into_one_wedge(donut):
    donut.set_data(REAL_SCAN, sum(d["size_bytes"] for _, d in REAL_SCAN))

    assert len(donut._segments) == donut.MAX_SLICES + 1


def test_the_pooled_wedge_is_last_and_marked(donut):
    donut.set_data(REAL_SCAN, 1)
    last = donut._segments[-1]

    assert last["is_other"]
    assert last["pooled"] == len(REAL_SCAN) - donut.MAX_SLICES


def test_the_pooled_wedge_carries_the_whole_tail(donut):
    donut.set_data(REAL_SCAN, 1)
    tail = REAL_SCAN[donut.MAX_SLICES:]

    assert donut._segments[-1]["size_bytes"] == sum(d["size_bytes"] for _, d in tail)


def test_a_short_list_is_not_pooled(donut):
    short = _cats([50, 30, 20])
    donut.set_data(short, 100)

    assert len(donut._segments) == 3
    assert not any(s.get("is_other") for s in donut._segments)


def test_exactly_the_limit_is_not_pooled(donut):
    donut.set_data(_cats([10] * donut.MAX_SLICES), 80)

    assert len(donut._segments) == donut.MAX_SLICES
    assert not any(s.get("is_other") for s in donut._segments)


def test_the_centre_still_reports_every_category(donut):
    """Pooling must not make the chart under-report the scan."""
    donut.set_data(REAL_SCAN, 1)

    assert donut._category_count == len(REAL_SCAN)


# ── the wedges still add up ───────────────────────────────────────

def test_the_wedges_fill_the_circle(donut):
    donut.set_data(REAL_SCAN, 1)
    span = sum(s["angle_span"] for s in donut._segments)

    assert span == pytest.approx(360.0, abs=0.01)


def test_wedge_size_stays_proportional(donut):
    donut.set_data(REAL_SCAN, 1)
    total = sum(d["size_bytes"] for _, d in REAL_SCAN)

    for seg in donut._segments:
        assert seg["angle_span"] == pytest.approx(
            360.0 * seg["size_bytes"] / total, abs=0.01)


# ── the inverted-sliver bug ───────────────────────────────────────

@pytest.mark.parametrize("angle_span", [
    360.0, 90.0, 5.0, 1.5, 1.4, 1.39, 0.5, 0.1, 0.01, 0.0,
])
def test_a_wedge_is_never_swept_backwards(donut, angle_span):
    """Qt draws a negative sweep counter-clockwise, across its neighbours."""
    assert donut._drawn_span(angle_span) > 0


def test_the_gap_never_eats_more_than_the_wedge(donut):
    """Below the gap width the separator has to shrink with the slice."""
    assert donut._drawn_span(0.5) <= 0.5
    assert donut._drawn_span(10.0) == pytest.approx(10.0 - donut.GAP_DEG)


def test_a_wide_wedge_keeps_the_full_separator(donut):
    for span in (20.0, 90.0, 180.0):
        assert donut._drawn_span(span) == pytest.approx(span - donut.GAP_DEG)


def test_the_ring_paints_with_a_spray_of_slivers(donut, qapp):
    """The geometry that used to produce the speckled band."""
    from PySide6.QtGui import QPixmap
    from PySide6.QtCore import QSize

    donut.set_data(_cats([100_000] + [1] * 8), 100_008)
    donut.show()
    qapp.processEvents()

    pix = QPixmap(QSize(260, 260))
    donut.render(pix)          # raises if the paint path is broken
    assert not pix.isNull()


# ── the pooled wedge is not a category you can open ───────────────

def _click(donut, qapp, seg):
    """Press the real handler at the middle of a wedge."""
    import math
    from PySide6.QtCore import QPointF, QEvent, Qt
    from PySide6.QtGui import QMouseEvent

    donut.show()
    qapp.processEvents()
    cx, cy = donut.width() / 2, donut.height() / 2
    outer = (min(donut.width(), donut.height()) - 28) / 2
    radius = outer * 0.85                      # inside the ring, past the hole
    mid = math.radians(seg["angle_start"] + seg["angle_span"] / 2)
    pos = QPointF(cx + radius * math.sin(mid), cy - radius * math.cos(mid))

    donut.mousePressEvent(QMouseEvent(
        QEvent.MouseButtonPress, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


def test_clicking_the_pooled_wedge_opens_nothing(donut, qapp):
    donut.set_data(REAL_SCAN, 1)
    opened = []
    donut.sector_clicked.connect(opened.append)

    _click(donut, qapp, donut._segments[-1])

    assert opened == [], "the pooled wedge stands for several categories"


def test_clicking_a_real_wedge_opens_that_category(donut, qapp):
    """The guard must not have disabled the chart's whole purpose."""
    donut.set_data(REAL_SCAN, 1)
    opened = []
    donut.sector_clicked.connect(opened.append)

    biggest = donut._segments[0]
    _click(donut, qapp, biggest)

    assert opened == [biggest["cat"]]
