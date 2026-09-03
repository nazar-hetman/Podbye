"""The selected finding gets one longer measurement. Nothing else changes.

The fast walk is budgeted for the list — a category of 250 rows has to open
promptly, so ``DEFAULT_BUDGET_MS`` is 700. That budget is wrong for the one
folder a person is actually reading. Measured on the reporting machine:

    C:/Users/<u>/miniconda3    700 ms →   2.56 GB, truncated
                              6000 ms →  24.27 GB, complete, in 3.6 s

So the panel's "Rest of this folder — not itemised" row went from 92% of the
finding to nothing at all, for one folder, off the UI thread.

The rules this file holds, because every one of them is a way to get it
wrong:

* the list's budget is untouched — raising it there slows every row to help
  one;
* the second walk happens once per selection, never in a loop;
* it is a *folder* thing: measure_files() answers about a fixed list and has
  nothing more to find;
* a selection change or a teardown cancels it, and a late result for a row
  the user has left is discarded;
* the deeper budget is bounded, so truncation stays a legitimate final answer
  — C:/Windows does not finish at any budget worth waiting for, and keeps its
  residual row.
"""
import pytest

from app.models.entity_contents import (
    DEFAULT_BUDGET_MS, MODE_CONTENTS, MODE_FILES, ContentRow, Contents,
)

GB = 1024 ** 3
MB = 1024 ** 2


def _entity(own, path="C:/thing", name="Thing", files=None):
    e = {"path": path, "name": name, "entity_type": "dev_project",
         "size_bytes": own, "contained_bytes": 0, "contained_paths": [],
         "file_count": 100}
    if files:
        e["removable_file_paths"] = list(files)
    return e


def _walk(total, truncated, mode=MODE_CONTENTS):
    return Contents(mode=mode,
                    rows=[ContentRow(label="pkgs", size_bytes=total)],
                    total_bytes=total, total_files=10, truncated=truncated)


@pytest.fixture
def panel(qapp, _shared_panel):
    """One panel for the module — see the fixture in conftest for why."""
    def build(entity):
        p = _shared_panel([entity])
        p._current_entity = entity
        p._current_path = entity.get("path", "")
        p._contents = None
        p._deep_walk_for = ""
        p._measuring_more = False
        p._contents_file_paths = []
        p._contents_exclude = []
        return p

    yield build
    # Whatever a test left running must not outlive it, even though the panel
    # itself is reused.
    for widget in (_shared_panel([]),):
        widget._stop_contents_walk(200)
    qapp.processEvents()


@pytest.fixture
def started(monkeypatch):
    """Record walks instead of running them."""
    import app.screens.findings_dashboard as fd

    calls = []

    def fake(self, path, file_paths=None, exclude=None, budget_ms=0):
        calls.append({"path": path, "files": list(file_paths or []),
                      "exclude": list(exclude or []), "budget": budget_ms})

    monkeypatch.setattr(fd._PreallocDetailPanel, "_start_contents_walk", fake)
    return calls


# ── the budgets ───────────────────────────────────────────────────

def test_the_list_budget_is_untouched():
    """Raising it here would slow every row of a 250-row category."""
    assert DEFAULT_BUDGET_MS == 700


def test_the_inspector_budget_is_larger_but_bounded():
    from app.screens.findings_dashboard import _PreallocDetailPanel

    deep = _PreallocDetailPanel._DEEP_CONTENTS_BUDGET_MS
    assert deep > DEFAULT_BUDGET_MS * 4
    assert deep <= 15000, "a budget nobody would wait out is not a budget"


def test_a_worker_without_a_budget_keeps_the_default(monkeypatch):
    """0 means "whatever entity_contents decides", not "no limit"."""
    import app.models.entity_contents as ec
    from app.screens.findings_dashboard import ContentsWalkWorker

    seen = {}
    monkeypatch.setattr(ec, "walk_contents",
                        lambda path, **kw: seen.update(kw) or _walk(1, False))
    ContentsWalkWorker("C:/x", budget_ms=0).run()

    assert "budget_ms" not in seen


def test_a_worker_with_a_budget_passes_it_through(monkeypatch):
    import app.models.entity_contents as ec
    from app.screens.findings_dashboard import ContentsWalkWorker

    seen = {}
    monkeypatch.setattr(ec, "walk_contents",
                        lambda path, **kw: seen.update(kw) or _walk(1, False))
    ContentsWalkWorker("C:/x", budget_ms=6000).run()

    assert seen["budget_ms"] == 6000


# ── when the second look happens ──────────────────────────────────

def test_a_truncated_result_earns_one_longer_look(panel, started):
    p = panel(_entity(int(24.3 * GB)))

    p._on_contents_measured("C:/thing", _walk(int(2.5 * GB), truncated=True))

    assert len(started) == 1
    assert started[0]["budget"] == p._DEEP_CONTENTS_BUDGET_MS
    assert started[0]["path"] == "C:/thing"


def test_a_complete_result_starts_nothing(panel, started):
    p = panel(_entity(int(5.0 * GB)))

    p._on_contents_measured("C:/thing", _walk(int(5.0 * GB), truncated=False))

    assert started == []


def test_it_never_loops(panel, started):
    """The deeper walk can come back truncated too — C:/Windows does. One
    escalation per selection, or the panel measures forever."""
    p = panel(_entity(int(46.0 * GB)))

    p._on_contents_measured("C:/thing", _walk(int(2.0 * GB), truncated=True))
    p._on_contents_measured("C:/thing", _walk(int(12.0 * GB), truncated=True))
    p._on_contents_measured("C:/thing", _walk(int(12.0 * GB), truncated=True))

    assert len(started) == 1


def test_a_file_list_never_escalates(panel, started):
    """measure_files answers about a fixed list; a longer look finds nothing
    it did not already have."""
    entity = _entity(int(5.0 * GB), files=["C:/thing/a.zip"])
    p = panel(entity)
    p._contents_file_paths = ["C:/thing/a.zip"]

    p._on_contents_measured("C:/thing",
                            _walk(int(2.0 * GB), True, mode=MODE_FILES))

    assert started == []


def test_the_deeper_walk_keeps_the_same_exclusions(panel, started):
    """Deletion scope must not move because the measurement got longer."""
    p = panel(_entity(int(24.3 * GB)))
    p._contents_exclude = ["C:/thing/nested"]

    p._on_contents_measured("C:/thing", _walk(int(2.5 * GB), truncated=True))

    assert started[0]["exclude"] == ["C:/thing/nested"]


# ── lifecycle ─────────────────────────────────────────────────────

def test_a_late_result_for_an_abandoned_row_is_discarded(panel, started):
    p = panel(_entity(int(24.3 * GB)))
    p._current_path = "C:/somewhere-else"

    p._on_contents_measured("C:/thing", _walk(int(2.5 * GB), truncated=True))

    assert started == []
    assert p._contents is None, "it rendered a row the user had left"


def test_rapid_selection_changes_leave_one_walk_in_flight(panel, qapp):
    """Clicking through rows faster than the biggest can be measured is the
    normal case, not the edge one."""
    from app.screens.findings_dashboard import _LIVE_CONTENT_WALKS

    p = panel(_entity(int(1.0 * GB)))
    before = len(_LIVE_CONTENT_WALKS)
    for i in range(8):
        p._stop_contents_walk(50)
        p._start_contents_walk("C:/thing/%d" % i)
    p._stop_contents_walk(200)
    qapp.processEvents()

    assert p._contents_worker is None
    assert len(_LIVE_CONTENT_WALKS) <= before + 8


def test_each_selection_starts_from_a_clean_slate(panel, started, qapp):
    """A row that escalated must not stop the next one from escalating."""
    p = panel(_entity(int(24.3 * GB)))
    p._on_contents_measured("C:/thing", _walk(int(2.5 * GB), truncated=True))
    assert len(started) == 1

    other = _entity(int(30.0 * GB), path="C:/other", name="Other")
    p._current_entity = other
    p._current_path = "C:/other"
    p._populate_contents(other)
    p._on_contents_measured("C:/other", _walk(int(2.0 * GB), truncated=True))

    assert p._deep_walk_for == "C:/other"
    assert any(c["path"] == "C:/other" and c["budget"] for c in started)


def test_teardown_cancels_whatever_is_running(panel, qapp):
    p = panel(_entity(int(24.3 * GB)))
    p._start_contents_walk("C:/thing", budget_ms=6000)
    worker = p._contents_worker
    assert worker is not None

    p._stop_contents_walk(200)
    qapp.processEvents()

    assert p._contents_worker is None
    assert worker._stop is True


def test_the_worker_is_never_parented_to_the_panel(panel):
    """Qt destroys a child QThread with its parent, and destroying a running
    one calls std::terminate."""
    p = panel(_entity(int(1.0 * GB)))
    p._start_contents_walk("C:/thing")
    try:
        assert p._contents_worker.parent() is None
    finally:
        p._stop_contents_walk(200)


# ── what the user sees meanwhile ──────────────────────────────────

def test_it_says_it_is_still_looking(panel, started):
    p = panel(_entity(int(24.3 * GB)))
    p._contents = _walk(int(2.5 * GB), truncated=True)

    p._on_contents_measured("C:/thing", _walk(int(2.5 * GB), truncated=True))

    assert "Measuring more" in p._contents_meta.text()


def test_the_notice_promises_a_look_not_an_outcome():
    """For something the size of C:/Windows the residual will still be there
    when the deeper walk gives up."""
    from app.i18n import tr

    text = tr("Measuring more details…")
    for overclaim in ("all", "everything", "complete", "full"):
        assert overclaim not in text.lower()


def test_the_residual_row_survives_the_wait(panel, started):
    """It is removed by a better measurement, never by the promise of one."""
    p = panel(_entity(int(24.3 * GB)))

    p._on_contents_measured("C:/thing", _walk(int(2.5 * GB), truncated=True))
    labels = [w._name.full_text() for w in p._content_row_pool
              if w.isVisibleTo(p)]

    assert any("not itemised" in l for l in labels), labels


def test_a_complete_second_result_clears_the_notice(panel, started):
    p = panel(_entity(int(24.3 * GB)))
    p._on_contents_measured("C:/thing", _walk(int(2.5 * GB), truncated=True))

    p._on_contents_measured("C:/thing", _walk(int(24.3 * GB), truncated=False))
    labels = [w._name.full_text() for w in p._content_row_pool
              if w.isVisibleTo(p)]

    assert p._measuring_more is False
    assert "Measuring more" not in p._contents_meta.text()
    assert not any("not itemised" in l for l in labels)


@pytest.mark.parametrize("language", ["Ukrainian", "German", "Spanish",
                                      "Polish", "French"])
def test_the_notice_is_translated(language):
    from app.i18n import set_language, tr

    key = "Measuring more details…"
    try:
        set_language(language)
        assert tr(key) != key, f"{language} falls back to English"
    finally:
        set_language("English")
