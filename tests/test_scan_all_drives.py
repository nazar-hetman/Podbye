"""Multi-root scanning and per-drive entity partitioning ("Scan all drives")."""
import os
import tempfile
import pathlib

import pytest

from app.services.scanner import ScanWorker
from app.state.scan_state import ScanState
from app.models.finding import Finding


@pytest.fixture
def two_roots(tmp_path):
    a = tmp_path / "rootA"
    b = tmp_path / "rootB"
    for root, tag in ((a, "A"), (b, "B")):
        for i in range(3):
            d = root / f"d{i}"
            d.mkdir(parents=True)
            for j in range(4):
                (d / f"f{j}.log").write_text(tag)
    return str(a), str(b)


# ── scanner ──────────────────────────────────────────────────────


def test_single_target_still_seeds_one_root(tmp_path):
    w = ScanWorker(str(tmp_path))
    assert w._roots == [str(tmp_path)]


def test_multi_root_walks_every_root(two_roots):
    a, b = two_roots
    out = []
    w = ScanWorker("All drives", roots=[a, b])
    w.batch_ready.connect(out.extend)
    w.run()
    files = [f for f in out if not f.is_dir]
    an = a.replace("\\", "/").lower()
    bn = b.replace("\\", "/").lower()
    from_a = [f for f in files if f.path.replace("\\", "/").lower().startswith(an)]
    from_b = [f for f in files if f.path.replace("\\", "/").lower().startswith(bn)]
    assert len(from_a) == 12
    assert len(from_b) == 12


def test_multi_root_records_each_root_dir(two_roots):
    a, b = two_roots
    out = []
    w = ScanWorker("All drives", roots=[a, b])
    w.batch_ready.connect(out.extend)
    w.run()
    root_dirs = {f.path for f in out if f.is_dir and f.path in (a, b)}
    assert root_dirs == {a, b}


def test_root_devs_allows_all_seeded_roots(two_roots):
    """Both roots' volumes must be in the allowed set, or one drive gets pruned
    as a 'different volume'. Same volume here, but the set must hold every root."""
    a, b = two_roots
    w = ScanWorker("All drives", roots=[a, b])
    w._root_devs = {os.stat(a).st_dev, os.stat(b).st_dev}
    assert w._crosses_volume(a) is False
    assert w._crosses_volume(b) is False


# ── ScanState per-drive partition ────────────────────────────────


def _f(path):
    return Finding(path=path, name=os.path.basename(path), is_dir=False,
                   size_bytes=10, extension=".log", modified=1, accessed=1,
                   parent=os.path.dirname(path))


def test_findings_under_partitions_by_root():
    st = ScanState()
    st.add_findings([_f("C:/apps/a.log"), _f("C:/apps/b.log"),
                     _f("D:/games/c.log")])
    under_c = st._findings_under("C:/")
    under_d = st._findings_under("D:/")
    assert {f.path for f in under_c} == {"C:/apps/a.log", "C:/apps/b.log"}
    assert {f.path for f in under_d} == {"D:/games/c.log"}


def test_set_scan_roots_filters_empty():
    st = ScanState()
    st.set_scan_roots(["C:/", "", None, "D:/"])
    assert st.scan_roots == ["C:/", "D:/"]


def test_clear_resets_scan_roots():
    st = ScanState()
    st.set_scan_roots(["C:/", "D:/"])
    st.clear()
    assert st.scan_roots == []
