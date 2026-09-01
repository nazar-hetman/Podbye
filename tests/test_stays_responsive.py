"""Stability and responsiveness at the seams a user actually hits.

Three real problems, each reachable by an ordinary action, each measured.

**A 33-second freeze opening a category.** Every row in the left pane is a real
widget in one QVBoxLayout, and each one's ``setVisible()`` posts a layout
request for the whole list — so the build cost grows faster than the row count.
Measured before the fix:

    100 rows  0.37 s        1,000 rows   3.6 s
    400 rows  2.02 s        3,000 rows  15.5 s
    800 rows  4.74 s        5,000 rows  33.5 s

``_ENTITY_CAP`` is 5,000, so the last line is not hypothetical: it is what
opening the biggest category on a real drive did. Suspending updates around the
loop was tried and measured *worse* (52.6 s) — Qt defers the layout requests
and the flush costs more than the incremental passes.

**A 2.8-second freeze opening the cleanup confirmation.** Expanding a folder
that holds a nested finding is a directory walk, and it ran in
``CleanupConfirmDialog.__init__`` — on the UI thread, from the click. Measured
at 2.8 s for C:/Windows/System32 (23,052 files), once per selected item.

**A crash closing the app during Ask AI on Startups.** The per-entry workers are
parented to the screen but were invisible to ``busy_reason`` and
``stop_background_work``, so a language switch — which rebuilds the shell and
deletes the outgoing widget tree — destroyed a running QThread. 0xC0000409, no
traceback, the crash ``app/services/workers.py`` exists to prevent.
"""
import os
import time

import pytest

import app.screens.findings_dashboard as fd
from app.themes.theme_manager import build_qss


def _entities(n):
    return [{"path": f"C:/x/app_{i:05d}", "name": f"Application {i:05d}",
             "entity_type": "application",
             "entity_type_label": "Installed application",
             "size_bytes": (i + 1) * 10 ** 6, "size": "10 MB",
             "file_count": 100 + i, "folder_count": 5,
             "risk": ("Safe", "Review", "Optional")[i % 3],
             "category": "Applications", "actionability": "recycle",
             "children_sample": [], "ai_status": "none"} for i in range(n)]


@pytest.fixture
def category(qapp):
    qapp.setStyleSheet(build_qss("forest"))
    made = []

    def build(count):
        view = fd.CategoryDetailView()
        view._app_index_cache = {}
        view.resize(1500, 900)
        view.show()
        view.set_category("Applications", _entities(count))
        for _ in range(4):
            qapp.processEvents()
        made.append(view)
        return view

    yield build
    for view in made:
        view.deleteLater()
    qapp.processEvents()


# ── the biggest category opens in a moment ────────────────────────

def test_the_list_does_not_render_a_widget_per_result(category):
    """The cap is the fix. Without it this is 5,000 widgets in one layout."""
    view = category(5000)
    painted = sum(1 for row in view._row_pool if not row.isHidden())

    assert painted <= fd.CategoryDetailView._ROW_RENDER_CAP
    assert painted > 0


def test_opening_the_biggest_category_is_not_a_freeze(qapp):
    """A wall-clock budget, deliberately loose enough to survive a slow CI box
    and still fail the 33 s it used to take."""
    qapp.setStyleSheet(build_qss("forest"))
    view = fd.CategoryDetailView()
    view._app_index_cache = {}
    view.resize(1500, 900)
    view.show()
    entities = _entities(5000)

    started = time.perf_counter()
    view.set_category("Applications", entities)
    for _ in range(4):
        qapp.processEvents()
    elapsed = time.perf_counter() - started

    try:
        assert elapsed < 10.0, f"{elapsed:.1f}s to open a 5,000-item category"
    finally:
        view.deleteLater()
        qapp.processEvents()


def test_the_count_says_it_is_showing_a_subset(category):
    """Hiding rows silently would be worse than the freeze."""
    view = category(5000)
    # full_text(), not text(): the caption is an ElidedLabel and text() is
    # what survived eliding.
    label = view._list_count_lbl.full_text()

    assert "250" in label and "5,000" in label, label
    assert "search or filter" in label
    assert view._list_count_lbl.width() > 0, "the caption rendered at zero width"


def test_a_small_category_is_unchanged(category):
    """The cap must be invisible below it — no new wording, no missing rows."""
    view = category(200)

    assert sum(1 for r in view._row_pool if not r.isHidden()) == 200
    assert view._list_count_lbl.full_text() == "// 200 visible"
    # It must also be on screen. Making the caption shrinkable so the section
    # title keeps its text is right; letting the row's stretch take all of it
    # is not, and that is what an Ignored size policy does.
    assert view._list_count_lbl.width() > 0
    assert view._list_count_lbl.text() == "// 200 visible", "elided with room to spare"


# ── and nothing is out of reach ───────────────────────────────────

def test_search_still_finds_what_is_not_painted(category):
    """The model keeps every row; only the painting is windowed."""
    view = category(5000)
    view._search.setText("Application 04321")
    view._apply_search_now()

    found = [r for r in view._row_pool if not r.isHidden()]
    assert len(found) == 1


def test_select_all_still_arms_everything_filtered(category):
    """_eligible_rows walks the proxy, not the painted rows. If it ever stops
    doing that, a bulk action would quietly act on a quarter of what the
    button offers."""
    view = category(5000)

    eligible = view._eligible_rows()

    assert len(eligible) > fd.CategoryDetailView._ROW_RENDER_CAP
    assert len(eligible) > 3000     # everything that is not Protected/Kept


def test_the_risk_filter_reaches_the_whole_set(category):
    view = category(5000)
    for risk, btn in view._risk_btns.items():
        btn.setChecked(risk == "Safe")
    view._apply_risk_filter()

    assert view._proxy.rowCount() > fd.CategoryDetailView._ROW_RENDER_CAP


# ── the confirmation dialog does not walk the disk to open ────────

def test_building_cleanup_targets_does_no_directory_walk(tmp_path, monkeypatch):
    """It ran in CleanupConfirmDialog.__init__, from the click that opens it.

    Asserted as "no walk happened" rather than a timing, because the cost is
    the tree's size and a tree big enough to time reliably is too slow to
    build in a test.
    """
    from app.screens import cleanup_dialog

    root = tmp_path / "Chrome"
    (root / "User Data" / "Cache").mkdir(parents=True)
    (root / "User Data" / "History").write_bytes(b"\0" * 1024)

    walked = []
    monkeypatch.setattr(os, "walk", lambda *a, **k: walked.append(a) or iter(()))
    monkeypatch.setattr(os, "listdir", lambda p: walked.append(p) or [])

    targets = cleanup_dialog._cleanup_targets_for_item({
        "path": str(root).replace("\\", "/"), "entity_type": "browser_profile",
        "size_bytes": 5_000_000, "contained_bytes": 2_000_000,
        "contained_paths": [str(root / "User Data" / "Cache").replace("\\", "/")]})

    assert walked == [], "the UI thread walked the filesystem"
    assert len(targets) == 1
    assert targets[0]["size_bytes"] == 5_000_000, "the total needed no walk"


def test_the_keep_out_list_reaches_the_worker(tmp_path):
    """Deferring the expansion is only safe if the worker actually gets it."""
    from app.screens import cleanup_dialog

    root = str(tmp_path / "Chrome").replace("\\", "/")
    cache = root + "/User Data/Cache"
    targets = cleanup_dialog._cleanup_targets_for_item({
        "path": root, "entity_type": "browser_profile", "size_bytes": 5,
        "contained_paths": [cache]})

    assert targets[0]["cleanup_exclude_paths"] == [cache]


def test_the_worker_expands_on_its_own_thread(tmp_path):
    from app.services.cleanup_engine import CleanupWorker

    root = tmp_path / "Chrome"
    (root / "keep").mkdir(parents=True)
    (root / "go.bin").write_bytes(b"\0")
    (root / "keep" / "x.bin").write_bytes(b"\0")
    root_s = str(root).replace("\\", "/")
    keep = str(root / "keep").replace("\\", "/")

    worker = CleanupWorker(paths=[root_s], exclude_by_path={root_s: [keep]})
    expanded = worker._expanded_paths()

    assert root_s + "/go.bin" in expanded
    assert keep not in expanded
    assert root_s not in expanded, "would have taken the nested finding"


def test_a_plain_folder_is_still_one_operation():
    """No exclusions must mean no expansion and no extra syscalls."""
    from app.services.cleanup_engine import CleanupWorker

    worker = CleanupWorker(paths=["C:/U/Game"])

    assert worker._expanded_paths() == ["C:/U/Game"]


# ── Startups: every thread it owns is known to the shell ──────────

def _startups(qapp, monkeypatch):
    import app.screens.startups as st

    monkeypatch.setattr("app.services.startup_detector.detect_startup_entries",
                        lambda: [])
    screen = st.StartupsScreen()
    screen.resize(1200, 800)
    return screen


class _FakeWorker:
    """Duck-typed like StartupAIWorker for the two methods under test."""

    def __init__(self, running=True):
        self._running = running
        self.cancelled = False

    def isRunning(self):
        return self._running

    def cancel(self):
        self.cancelled = True

    def requestInterruption(self):
        pass

    def wait(self, ms):
        self._running = False
        return True

    def setParent(self, parent):
        pass


def test_an_ask_ai_worker_makes_the_screen_busy(qapp, monkeypatch):
    """busy_reason gates the language switch, which deletes this widget tree.
    An unlisted worker meant the shell believed it was safe to do that."""
    screen = _startups(qapp, monkeypatch)
    try:
        assert screen.busy_reason() == ""

        screen._ask_workers.append(_FakeWorker())

        assert screen.busy_reason() != ""
    finally:
        screen._ask_workers.clear()
        screen.deleteLater()
        qapp.processEvents()


def test_teardown_stops_the_ask_ai_workers_too(qapp, monkeypatch):
    screen = _startups(qapp, monkeypatch)
    worker = _FakeWorker()
    screen._ask_workers.append(worker)
    try:
        screen.stop_background_work(50)

        assert worker.cancelled, "left running while the tree was torn down"
    finally:
        screen._ask_workers.clear()
        screen.deleteLater()
        qapp.processEvents()


def test_a_dead_worker_does_not_break_the_busy_check(qapp, monkeypatch):
    """isRunning() on a deleted C++ object raises RuntimeError, and this runs
    during teardown."""
    class _Dead:
        def isRunning(self):
            raise RuntimeError("wrapped C/C++ object has been deleted")

    screen = _startups(qapp, monkeypatch)
    screen._ask_workers.append(_Dead())
    try:
        assert screen.busy_reason() == ""
    finally:
        screen._ask_workers.clear()
        screen.deleteLater()
        qapp.processEvents()


# ── a probe that outlives its screen does not raise in a thread ───

def test_the_connection_probe_survives_its_screen_going_away():
    """_auto_test_connection guarded this; the manual Test button did not."""
    import inspect

    from app.screens.settings import SettingsScreen

    source = inspect.getsource(SettingsScreen._test_connection)

    assert "RuntimeError" in source, "unguarded emit from a daemon thread"
