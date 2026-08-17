"""Sorting and quantity semantics.

Every case here is a defect that shipped: the sort control looked right, the
column rendered right, and the ordering was silently wrong or inconsistent.
"""
import time

import pytest

from app.models.finding import Finding, _format_size
from app.models.findings_table_model import FindingsFilterProxy, FindingsTableModel
from app.models.risk import (
    RISK_ORDER, RISK_SAFE, RISK_OPTIONAL, RISK_REVIEW, RISK_PROTECTED,
    risk_sort_index,
)

DAY = 86400


def _f(name, mtime, size=10, risk=""):
    return Finding(path=f"C:/x/{name}", name=name, is_dir=False, size_bytes=size,
                   extension=".log", modified=mtime, accessed=mtime, parent="C:/x",
                   risk=risk)


# ── "Oldest first" ───────────────────────────────────────────────


def test_to_dict_emits_raw_modified():
    """The findings table sorts on `modified`; to_dict must actually emit it.

    It previously emitted only the formatted `age`/`first_seen` strings, so the
    sort read a missing key, every row tied, and "Oldest first" did nothing.
    """
    now = time.time()
    d = _f("a.log", now - 5 * 365 * DAY).to_dict()
    assert "modified" in d
    assert d["modified"] == pytest.approx(now - 5 * 365 * DAY)


def test_oldest_sort_actually_orders_findings():
    now = time.time()
    dicts = [
        _f("recent.log", now - 2 * DAY).to_dict(),
        _f("ancient.log", now - 9 * 365 * DAY).to_dict(),
        _f("mid.log", now - 200 * DAY).to_dict(),
    ]
    INF = float("inf")

    def key(d):
        return d.get("modified", INF) if d.get("modified") else INF

    keys = [key(d) for d in dicts]
    assert len(set(keys)) == 3, "sort keys collapsed -> 'Oldest first' is a no-op"

    order = [d["name"] for d in sorted(dicts, key=key)]
    assert order == ["ancient.log", "mid.log", "recent.log"]


def test_oldest_sort_through_the_real_proxy(qapp_ready=None):
    """End-to-end through FindingsFilterProxy, not just the key function."""
    now = time.time()
    model = FindingsTableModel()
    model.set_entities([
        _f("recent.log", now - 2 * DAY).to_dict(),
        _f("ancient.log", now - 9 * 365 * DAY).to_dict(),
        _f("mid.log", now - 200 * DAY).to_dict(),
    ])
    from PySide6.QtCore import Qt
    proxy = FindingsFilterProxy()
    proxy.setSourceModel(model)
    proxy.set_sort_key("oldest")
    proxy.sort(0, Qt.AscendingOrder)

    names = [
        proxy.data(proxy.index(r, 0), Qt.UserRole)["name"]
        for r in range(proxy.rowCount())
    ]
    assert names == ["ancient.log", "mid.log", "recent.log"]


# ── canonical risk ordering ──────────────────────────────────────


def test_risk_sort_index_is_safe_first():
    assert risk_sort_index(RISK_SAFE) == 0
    assert risk_sort_index(RISK_PROTECTED) == len(RISK_ORDER) - 1
    ranks = [risk_sort_index(r) for r in
             (RISK_SAFE, RISK_OPTIONAL, RISK_REVIEW, RISK_PROTECTED)]
    assert ranks == sorted(ranks), "canonical order is not Safe -> Protected"


def test_risk_sort_index_normalizes_legacy_and_unknown():
    assert risk_sort_index("Risk") == risk_sort_index(RISK_REVIEW)   # legacy alias
    assert risk_sort_index(None) == risk_sort_index(RISK_REVIEW)
    assert risk_sort_index("nonsense") == risk_sort_index(RISK_REVIEW)


def test_findings_table_and_startups_agree_on_status_order():
    """The two screens each had a private, mutually contradictory ordering."""
    from app.models import findings_table_model as ftm
    assert not hasattr(ftm, "_RISK_ORDER"), "table model resurrected a private order"

    import inspect
    from app.services import startup_detector
    src = inspect.getsource(startup_detector)
    assert '_RISK_ORDER = {"Review"' not in src, "startups resurrected a private order"


def test_status_sort_puts_actionable_first_through_proxy():
    from PySide6.QtCore import Qt
    model = FindingsTableModel()
    model.set_entities([
        _f("p.log", 1, risk=RISK_PROTECTED).to_dict(),
        _f("s.log", 1, risk=RISK_SAFE).to_dict(),
        _f("r.log", 1, risk=RISK_REVIEW).to_dict(),
        _f("o.log", 1, risk=RISK_OPTIONAL).to_dict(),
    ])
    proxy = FindingsFilterProxy()
    proxy.setSourceModel(model)
    proxy.set_sort_key("risk")
    proxy.sort(0, Qt.AscendingOrder)
    risks = [
        proxy.data(proxy.index(r, 0), Qt.UserRole)["risk"]
        for r in range(proxy.rowCount())
    ]
    assert risks == [RISK_SAFE, RISK_OPTIONAL, RISK_REVIEW, RISK_PROTECTED]


# ── size formatting boundaries ───────────────────────────────────


@pytest.mark.parametrize("size_bytes, expected", [
    (1024 ** 3 - 1, "1.0 GB"),   # was "1024 MB"
    (1024 ** 2 - 1, "1 MB"),     # was "1024 KB" (MB renders with .0f)
    (1024 ** 3, "1.0 GB"),
    (1024 ** 2, "1 MB"),
    (1024, "1 KB"),
    (1023, "1023 B"),
    (0, "0 B"),
])
def test_format_size_never_renders_a_full_next_unit(size_bytes, expected):
    assert _format_size(size_bytes) == expected


def test_format_size_never_emits_1024_of_any_unit():
    """Sweep the boundaries: no output should ever read '1024 <unit>'."""
    for scale in (1024, 1024 ** 2, 1024 ** 3, 1024 ** 4, 1024 ** 5):
        for delta in range(-3, 4):
            out = _format_size(scale + delta)
            assert not out.startswith("1024 "), f"{scale + delta} -> {out!r}"


# ── resume keeps mtime ───────────────────────────────────────────


def test_restore_from_session_preserves_modified():
    """A resumed session must keep real mtimes.

    modified was hardcoded to 0.0 on restore, which made every finding look ~55
    years old, broke age sorting for the whole session, and changed cache_key —
    silently invalidating the AI cache on every resume.
    """
    from app.state.scan_state import ScanState
    now = time.time()
    original = _f("keep.log", now - 100 * DAY)
    st = ScanState()
    st.restore_from_session({"findings": [original.to_dict()], "entities": []})

    assert len(st.findings) == 1
    restored = st.findings[0]
    assert restored.modified == pytest.approx(original.modified)
    assert restored.cache_key == original.cache_key, "AI cache key changed on resume"


# ── every advertised sort must actually sort ─────────────────────
# The trap this guards: a sort key reaches the dropdown but lessThan() has no
# branch for it, so it falls through to the default (size descending) and is
# indistinguishable from "Largest first". A screen-local copy of the key list
# had drifted from the proxy's in exactly this way, listing a "Safe cleanup"
# and a "Last accessed" that were never implemented.

# For each key: the field it claims to order by, and whether the winning row
# is the one with the LOWER value of that field.
_SORT_FIELD = {
    "largest":      ("size_bytes", False),
    "smallest":     ("size_bytes", True),
    "reclaimable":  ("reclaimable_bytes", False),
    "last_access":  ("accessed", True),
    "oldest":       ("modified", True),
}


@pytest.mark.parametrize("key", [k for k, _ in FindingsFilterProxy.SORT_KEYS])
def test_every_offered_sort_key_has_a_real_branch(key):
    """Two rows differing ONLY in this sort's field must come out in its order.

    Size is held equal, so a key that silently falls through to the size
    default cannot pass by accident.
    """
    from PySide6.QtCore import Qt

    base = dict(size_bytes=100, reclaimable_bytes=10, risk=RISK_REVIEW,
                accessed=5000.0, modified=5000.0, ai_status="none")

    if key == "risk":
        lo = dict(base, name="lo", risk=RISK_SAFE)
        hi = dict(base, name="hi", risk=RISK_PROTECTED)
        expected_first = "lo"          # Safe is the most actionable
    elif key == "safe_cleanup":
        lo = dict(base, name="lo", risk=RISK_SAFE)
        hi = dict(base, name="hi", risk=RISK_PROTECTED)
        expected_first = "lo"          # Protected cannot be cleaned at all
    elif key == "ai_analyzed":
        lo = dict(base, name="lo", ai_status="ready")
        hi = dict(base, name="hi", ai_status="none")
        expected_first = "lo"
    else:
        field, lower_wins = _SORT_FIELD[key]
        lo = dict(base, name="lo", **{field: 1.0})
        hi = dict(base, name="hi", **{field: 9000.0})
        expected_first = "lo" if lower_wins else "hi"

    model = FindingsTableModel()
    model.set_entities([hi, lo])       # deliberately the wrong way round
    proxy = FindingsFilterProxy()
    proxy.setSourceModel(model)
    proxy.set_sort_key(key)
    proxy.sort(0, Qt.AscendingOrder)

    order = [proxy.data(proxy.index(r, 0), Qt.UserRole)["name"]
             for r in range(proxy.rowCount())]
    assert order[0] == expected_first, (
        f"sort {key!r} did not order by its own criterion (got {order}) — "
        f"it is probably falling through to the size default")


def test_the_screen_offers_exactly_the_keys_the_proxy_implements():
    """One list, not two. The duplicate in findings_dashboard drifted."""
    from app.screens.findings_dashboard import SORT_OPTIONS
    assert list(SORT_OPTIONS) == list(FindingsFilterProxy.SORT_KEYS)
