"""Resume/continuation tests — the scan frontier lets an interrupted scan
continue from where it stopped instead of re-walking the whole tree, and the
frontier survives the session snapshot round-trip.
"""
import os
import tempfile
import shutil

from app.services.scanner import ScanWorker
from app.state.scan_state import ScanState
from app.models.finding import Finding

# QApplication + offscreen platform come from the session fixture in conftest.py


def _make_tree() -> str:
    root = tempfile.mkdtemp(prefix="podbye_resume_test_")
    for d in ("a/b", "c/d", "e"):
        os.makedirs(os.path.join(root, d))
        with open(os.path.join(root, d, "f.txt"), "wb") as fh:
            fh.write(b"x" * 100)
    with open(os.path.join(root, "top.txt"), "wb") as fh:
        fh.write(b"y" * 50)
    return root


def _run(worker: ScanWorker):
    findings, frontiers = [], []
    worker.batch_ready.connect(lambda b: findings.extend(b))
    worker.frontier_update.connect(lambda fr: frontiers.append(list(fr)))
    worker.run()
    return findings, frontiers


def test_completed_scan_reports_empty_frontier():
    root = _make_tree()
    try:
        _, frontiers = _run(ScanWorker(root))
        assert frontiers, "expected at least a final frontier emit"
        assert frontiers[-1] == [], "a completed scan must not be resumable"
    finally:
        shutil.rmtree(root)


def test_resume_stack_skips_rewalk():
    root = _make_tree()
    try:
        # Pretend the first run recorded top.txt + the whole 'a' subtree and
        # stopped with 'c' and 'e' still pending.
        known = {
            p.replace("\\", "/").lower()
            for p in (
                os.path.join(root, "top.txt"),
                os.path.join(root, "a"),
                os.path.join(root, "a", "b"),
                os.path.join(root, "a", "b", "f.txt"),
            )
        }
        resume_stack = [os.path.join(root, "c"), os.path.join(root, "e")]
        findings, _ = _run(
            ScanWorker(root, skip_paths=known, resume_stack=resume_stack)
        )
        rel = {os.path.relpath(f.path, root) for f in findings}
        # Only the c/ and e/ subtrees are walked — the 'a' subtree is never
        # re-visited, and known paths are not re-recorded.
        assert rel == {"c\\d", os.path.join("c", "d", "f.txt"),
                       os.path.join("e", "f.txt")}
        assert not any(r.startswith("a") for r in rel)
    finally:
        shutil.rmtree(root)


def test_frontier_survives_snapshot_round_trip():
    ss = ScanState()
    ss.clear()
    ss.add_findings([
        Finding(path=r"C:\d\f.txt", name="f.txt", is_dir=False, size_bytes=10,
                extension=".txt", modified=0, accessed=0, parent=r"C:\d")
    ])
    ss.on_frontier_update([r"C:\d\sub1", r"C:\d\sub2"])

    snap = ss._build_snapshot(
        "running", lightweight=False,
        findings=list(ss._findings), entities=[], frontier=list(ss._scan_frontier),
    )
    assert snap["scan_frontier"] == [r"C:\d\sub1", r"C:\d\sub2"]

    restored = ScanState()
    restored.clear()
    restored.restore_from_session(snap)
    assert restored.resume_frontier == [r"C:\d\sub1", r"C:\d\sub2"]
    assert restored.total_count == 1


def test_fresh_scan_has_no_resume_frontier():
    ss = ScanState()
    ss.clear()
    assert ss.resume_frontier == []
