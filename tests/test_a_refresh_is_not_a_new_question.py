"""An AI answer arriving must not restart the measurement it interrupts.

Reported against the beta.5 build: miniconda3, after "Measuring more
details…" finished, still read

    Miniconda                            24.3 GB
       pkgs                               2.1 GB
       Other data                          54 MB
       Rest of this folder — not itemised 22.1 GB

91% unexplained, after the deeper walk that exists to explain it.

The walk was never the problem. Measured on that machine:

    raw scandir walk of miniconda3      2.1 s   24.27 GB   300,081 files
    walk_contents budget   700 ms  ->   2.88 GB (11.9%)  truncated
    walk_contents budget  6000 ms  ->  24.27 GB (100%)   complete, ~3 s
    walk_contents budget 60000 ms  ->  24.27 GB (100%)   complete, ~3 s

Six seconds is already ample and raising it changes nothing. What the panel
never got was three uninterrupted seconds:

    AIExplainer.finding_updated -> _on_ai_finding_updated -> update_entity
      -> _show_detail_sidebar -> populate() -> _populate_contents
         -> _stop_contents_walk()

Every model answer about the inspected row cancelled its walk, cleared
_deep_walk_for and started again from the fast budget. An AI status is not a
filesystem event; it cannot move a byte. So a refresh whose contents inputs
are unchanged now keeps the measurement and only repaints — a content row's
own Ask AI / View result button does depend on AI state, so the rows are
still rebound.

Two things made this look like a budget problem rather than a lifecycle one.
``walk_contents`` reported a cancelled walk as ``truncated``, indistinguish-
able from running out of time; and ``_deep_walk_for`` was cleared on every
re-populate, so the escalation re-armed and was cancelled again, forever.
"""
import pytest

from app.models.entity_contents import (
    MODE_CONTENTS, MODE_FILES, ContentRow, Contents, walk_contents,
)

GB = 1024 ** 3
MB = 1024 ** 2


def _entity(own, path="C:/thing", name="Thing", ai_status="none",
            explanation="", kept=(), files=None):
    e = {"path": path, "name": name, "entity_type": "dev_project",
         "size_bytes": own, "contained_bytes": 0,
         "contained_paths": list(kept), "file_count": 100,
         "ai_status": ai_status, "ai_explanation": explanation}
    if files:
        e["removable_file_paths"] = list(files)
    return e


def _walk(total, truncated=False, mode=MODE_CONTENTS, rows=None, cancelled=False):
    return Contents(mode=mode,
                    rows=rows if rows is not None
                    else [ContentRow(label="pkgs", size_bytes=total)],
                    total_bytes=total, total_files=10,
                    truncated=truncated, cancelled=cancelled)


@pytest.fixture
def panel(qapp, _shared_panel):
    def build(entity, world=None):
        p = _shared_panel(world or [entity])
        p._current_entity = entity
        p._current_path = entity.get("path", "")
        p._contents = None
        p._contents_inputs_seen = ()
        p._deep_walk_for = ""
        p._measuring_more = False
        p._contents_file_paths = []
        p._contents_exclude = []
        return p

    yield build
    _shared_panel([])._stop_contents_walk(200)
    qapp.processEvents()


@pytest.fixture
def walks(monkeypatch):
    """Record walks instead of running them, and note cancellations."""
    import app.screens.findings_dashboard as fd

    started, stopped = [], []

    def fake_start(self, path, file_paths=None, exclude=None, budget_ms=0):
        started.append({"path": path, "budget": budget_ms,
                        "exclude": list(exclude or [])})
        self._contents_worker = object()      # something is "in flight"

    def fake_stop(self, timeout_ms=1500):
        if self._contents_worker is not None:
            stopped.append(True)
        self._contents_worker = None

    monkeypatch.setattr(fd._PreallocDetailPanel, "_start_contents_walk", fake_start)
    monkeypatch.setattr(fd._PreallocDetailPanel, "_stop_contents_walk", fake_stop)
    return started, stopped


# ── cancelled is not truncated ────────────────────────────────────

def test_running_out_of_time_is_truncation(tmp_path):
    for i in range(200):
        d = tmp_path / ("d%03d" % i)
        d.mkdir()
        (d / "f.bin").write_bytes(b"x" * 4096)

    c = walk_contents(str(tmp_path), budget_ms=0)

    assert c.truncated is True
    assert c.cancelled is False


def test_being_asked_to_stop_is_cancellation(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.bin").write_bytes(b"x" * 4096)

    c = walk_contents(str(tmp_path), budget_ms=60000, should_stop=lambda: True)

    assert c.cancelled is True
    assert c.truncated is True, "a cancelled walk is also incomplete"


def test_a_complete_walk_is_neither(tmp_path):
    (tmp_path / "f.bin").write_bytes(b"x" * 4096)

    c = walk_contents(str(tmp_path), budget_ms=60000)

    assert (c.truncated, c.cancelled) == (False, False)


def test_a_cancelled_result_is_never_committed(panel, walks):
    """It measured nothing it can be held to, and committing it would also
    spend the one escalation this selection gets."""
    p = panel(_entity(int(24.3 * GB)))

    p._on_contents_measured("C:/thing", _walk(int(1.0 * GB), truncated=True,
                                              cancelled=True))

    assert p._contents is None
    assert walks[0] == [], "a cancelled walk armed the deeper one"


# ── a metadata refresh keeps the measurement ──────────────────────

def test_an_ai_update_does_not_restart_the_walk(panel, walks):
    """The reported case, at the method the AI path actually calls."""
    started, stopped = walks
    entity = _entity(int(24.3 * GB))
    p = panel(entity)
    p._populate_contents(entity)
    assert len(started) == 1

    answered = dict(entity, ai_status="ready", ai_explanation="A conda install.")
    p._current_entity = answered
    p._populate_contents(answered)

    assert len(started) == 1, "the AI answer started a second walk"
    assert stopped == [], "the AI answer cancelled the walk in flight"


def test_the_escalation_survives_a_refresh(panel, walks):
    """miniconda3 end to end: fast walk truncates, deep walk starts, an AI
    answer lands mid-flight, and the deep result still arrives and completes
    the picture with no residual row left behind."""
    started, _stopped = walks
    entity = _entity(int(24.3 * GB))
    p = panel(entity)
    p._populate_contents(entity)

    # the fast walk comes back short
    p._on_contents_measured("C:/thing", _walk(int(2.1 * GB), truncated=True))
    assert len(started) == 2 and started[1]["budget"] == p._DEEP_CONTENTS_BUDGET_MS
    assert p._measuring_more is True

    # an AI answer arrives while the deeper walk is running
    answered = dict(entity, ai_status="ready", ai_explanation="A conda install.")
    p._current_entity = answered
    p._populate_contents(answered)
    assert len(started) == 2, "the refresh restarted the measurement"
    assert p._deep_walk_for == "C:/thing", "the refresh re-armed the escalation"

    # the deeper walk lands
    p._on_contents_measured("C:/thing", _walk(
        int(24.3 * GB),
        rows=[ContentRow(label="envs", size_bytes=int(14.7 * GB)),
              ContentRow(label="pkgs", size_bytes=int(7.3 * GB)),
              ContentRow(label="Installed packages", size_bytes=int(2.3 * GB))]))

    labels = [w._name.full_text() for w in p._content_row_pool
              if w.isVisibleTo(p)]
    assert not any("not itemised" in l for l in labels), labels
    assert p._measuring_more is False
    assert p._contents.total_bytes == int(24.3 * GB)


def test_repeated_refreshes_never_restart_it(panel, walks):
    """A queue of answers, not one."""
    started, stopped = walks
    entity = _entity(int(24.3 * GB))
    p = panel(entity)
    p._populate_contents(entity)

    for i in range(10):
        p._populate_contents(dict(entity, ai_status="ready",
                                  ai_explanation="answer %d" % i))

    assert len(started) == 1
    assert stopped == []


def test_the_rows_are_still_rebound_on_a_refresh(panel, walks):
    """A content row's own Ask AI / View result button depends on AI state,
    so skipping the walk must not skip the repaint."""
    entity = _entity(int(5.0 * GB))
    p = panel(entity)
    p._populate_contents(entity)
    p._contents = _walk(int(5.0 * GB))
    p._render_contents()

    p._populate_contents(dict(entity, ai_status="ready"))

    assert [w._name.full_text() for w in p._content_row_pool
            if w.isVisibleTo(p)][0] == "Thing"


# ── but a real change still invalidates ───────────────────────────

def test_a_different_path_restarts_the_measurement(panel, walks):
    started, stopped = walks
    p = panel(_entity(int(5.0 * GB)))
    p._populate_contents(_entity(int(5.0 * GB)))

    other = _entity(int(9.0 * GB), path="C:/other", name="Other")
    p._current_path = "C:/other"
    p._populate_contents(other)

    assert len(started) == 2
    assert stopped == [True], "the previous walk was left running"
    assert started[1]["path"] == "C:/other"


def test_changed_exclusions_restart_the_measurement(panel, walks):
    """A nested finding appearing or going changes what the walk must skip,
    so the old answer is about a different question."""
    started, _stopped = walks
    entity = _entity(int(5.0 * GB))
    p = panel(entity)
    p._populate_contents(entity)

    with_nested = _entity(int(5.0 * GB), kept=["C:/thing/nested"])
    p._populate_contents(with_nested)

    assert len(started) == 2
    assert started[1]["exclude"] == ["C:/thing/nested"]


def test_a_changed_file_list_restarts_the_measurement(panel, walks):
    # Two files, not one: a single-file entity is MODE_NONE and starts no
    # walk at all, so it cannot show a restart either way.
    started, _stopped = walks
    two = _entity(int(1.0 * GB), files=["C:/thing/a.zip", "C:/thing/b.zip"])
    p = panel(two)
    p._populate_contents(two)
    assert len(started) == 1

    three = _entity(int(1.0 * GB), files=["C:/thing/a.zip", "C:/thing/b.zip",
                                          "C:/thing/c.zip"])
    p._populate_contents(three)

    assert len(started) == 2


def test_the_inputs_ignore_everything_that_cannot_move_a_byte(panel):
    """Stated directly, so the next field added to an entity is considered."""
    from app.models.entity_contents import mode_for

    p = panel(_entity(int(5.0 * GB)))
    base = _entity(int(5.0 * GB))
    noisy = dict(base, ai_status="ready", ai_explanation="text",
                 ai_error="", risk="Optional", risk_reason="changed",
                 size_bytes=int(9.9 * GB), summary="new")

    assert (p._contents_inputs(base, mode_for(base))
            == p._contents_inputs(noisy, mode_for(noisy)))


# ── a late result can never land on the wrong finding ─────────────

def test_a_late_result_from_the_previous_finding_is_dropped(panel, walks):
    """The user selected A, moved to B, and A's walk finishes afterwards."""
    a = _entity(int(24.3 * GB), path="C:/a", name="A")
    p = panel(a)
    p._populate_contents(a)

    b = _entity(int(1.0 * GB), path="C:/b", name="B")
    p._current_path = "C:/b"
    p._current_entity = b
    p._populate_contents(b)

    p._on_contents_measured("C:/a", _walk(int(24.3 * GB)))

    assert p._contents is None or p._contents.total_bytes != int(24.3 * GB)


def test_a_late_result_cannot_arm_the_deeper_walk_for_another_row(panel, walks):
    started, _stopped = walks
    a = _entity(int(24.3 * GB), path="C:/a", name="A")
    p = panel(a)
    p._populate_contents(a)
    b = _entity(int(1.0 * GB), path="C:/b", name="B")
    p._current_path = "C:/b"
    p._populate_contents(b)
    before = len(started)

    p._on_contents_measured("C:/a", _walk(int(2.0 * GB), truncated=True))

    assert len(started) == before
    assert p._deep_walk_for != "C:/a"


def test_the_budget_was_not_raised():
    """The measurement said 6 s already finishes miniconda3 in ~3 s, and
    60 s finishes no more of it. A larger number would have been a guess
    dressed as a fix."""
    from app.screens.findings_dashboard import _PreallocDetailPanel

    assert _PreallocDetailPanel._DEEP_CONTENTS_BUDGET_MS == 6000


# ── a breakdown about to be replaced is not shown at all ──────────
#
# Reported on beta.5: selecting miniconda3 showed pkgs 2.3 GB, Other data
# 54 MB and "Rest of this folder — not itemised" 21.9 GB, then four seconds
# later replaced all three with the real figures. Podbye appeared to change
# its mind about a folder it was offering to delete. The header and the root
# row were right the whole time; only the part still being measured was not.

def _measuring(panel, walks, own=int(24.3 * GB), kept=()):
    entity = _entity(own, kept=kept)
    world = [entity] + [_entity(int(2.0 * GB), path=p, name="nested")
                        for p in kept]
    p = panel(entity, world)
    p._populate_contents(entity)
    p._on_contents_measured("C:/thing", _walk(int(2.3 * GB), truncated=True))
    return p


def _labels(p):
    return [w._name.full_text() for w in p._content_row_pool
            if w.isVisibleTo(p)]


def test_the_partial_breakdown_is_not_rendered(panel, walks):
    p = _measuring(panel, walks)

    assert _labels(p) == ["Thing", "Measuring contents…"]


def test_the_authoritative_answer_stays_visible(panel, walks):
    """The header and the size were right all along and must not flicker."""
    p = _measuring(panel, walks)
    rows = [w for w in p._content_row_pool if w.isVisibleTo(p)]

    assert p._contents_title.text() == "WILL BE MOVED TO RECYCLE BIN"
    assert rows[0]._name.full_text() == "Thing"
    assert rows[0]._size.text() == "24.3 GB"


def test_the_placeholder_carries_no_size(panel, walks):
    """A zero would read as "this folder holds 0 B"."""
    p = _measuring(panel, walks)
    rows = [w for w in p._content_row_pool if w.isVisibleTo(p)]

    assert rows[1]._size.text() == ""


def test_the_placeholder_is_a_state_not_a_target(panel, walks):
    """Nothing to tick, nothing to drill into, nothing to ask AI about."""
    p = _measuring(panel, walks)
    rows = [w for w in p._content_row_pool if w.isVisibleTo(p)]

    assert not rows[1]._check.isVisibleTo(p)
    assert rows[1]._drillable is False


def test_the_header_does_not_repeat_the_placeholder(panel, walks):
    p = _measuring(panel, walks)

    assert p._contents_meta.text() == ""


def test_will_remain_waits_for_the_final_answer(panel, walks):
    """It comes from the same split as the residual row and is just as
    provisional while a better measurement is running."""
    p = _measuring(panel, walks, kept=["C:/thing/nested"])

    assert not p._remain_hdr_host.isVisibleTo(p)


def test_the_final_result_replaces_only_the_placeholder(panel, walks):
    p = _measuring(panel, walks)

    p._on_contents_measured("C:/thing", _walk(
        int(24.3 * GB),
        rows=[ContentRow(label="envs", size_bytes=int(14.7 * GB)),
              ContentRow(label="pkgs", size_bytes=int(7.3 * GB))]))
    rows = [w for w in p._content_row_pool if w.isVisibleTo(p)]

    assert p._contents_title.text() == "WILL BE MOVED TO RECYCLE BIN"
    assert [w._name.full_text() for w in rows] == ["Thing", "envs", "pkgs"]
    assert rows[0]._size.text() == "24.3 GB"


def test_a_fast_completing_finding_never_shows_it(panel, walks):
    """Most findings. No placeholder, no delay, no change from today."""
    entity = _entity(int(5.0 * GB))
    p = panel(entity)
    p._populate_contents(entity)

    p._on_contents_measured("C:/thing", _walk(
        int(5.0 * GB),
        rows=[ContentRow(label="src", size_bytes=int(3.0 * GB)),
              ContentRow(label="dist", size_bytes=int(2.0 * GB))]))

    assert p._measuring_more is False
    assert _labels(p) == ["Thing", "src", "dist"]


def test_a_file_list_is_never_replaced_by_a_placeholder(panel, walks):
    """measure_files never escalates, so it can never be mid-measurement."""
    entity = _entity(int(1.0 * GB), files=["C:/thing/a.zip", "C:/thing/b.zip"])
    p = panel(entity)
    p._populate_contents(entity)
    p._contents_file_paths = ["C:/thing/a.zip", "C:/thing/b.zip"]

    p._on_contents_measured("C:/thing", _walk(
        int(1.0 * GB), truncated=True, mode=MODE_FILES,
        rows=[ContentRow(label="a.zip", size_bytes=int(0.6 * GB)),
              ContentRow(label="b.zip", size_bytes=int(0.4 * GB))]))

    assert p._measuring_more is False
    assert "Measuring contents…" not in _labels(p)


@pytest.mark.parametrize("language", ["Ukrainian", "German", "Spanish",
                                      "Polish", "French"])
def test_the_placeholder_is_translated(language):
    from app.i18n import set_language, tr

    try:
        set_language(language)
        assert tr("Measuring contents…") != "Measuring contents…"
    finally:
        set_language("English")


# ── the residual is never the row the cap drops ───────────────────
#
# Seen on a slow run of the real miniconda3: the deeper walk ran out of
# budget too, six rows came back, the display cap showed five, and 4.85 GB
# of "Rest of this folder — not itemised" sat behind a "+1 more" button
# under a root reading 24.3 GB. The row that exists to make the branch add
# up was the first one dropped, because removal_split appends it last.

def _six_row_truncated(panel, walks):
    """A deep walk that also gave up: five measured rows plus the residual,
    one more than the panel shows."""
    entity = _entity(int(24.3 * GB))
    p = panel(entity)
    p._populate_contents(entity)
    p._on_contents_measured("C:/thing", _walk(int(2.2 * GB), truncated=True))
    p._on_contents_measured("C:/thing", _walk(
        int(19.45 * GB), truncated=True,
        rows=[ContentRow(label="Installed packages", size_bytes=int(1.3 * GB)),
              ContentRow(label="Package download cache", size_bytes=int(0.8 * GB)),
              ContentRow(label="envs", size_bytes=int(9.8 * GB)),
              ContentRow(label="pkgs", size_bytes=int(7.3 * GB)),
              ContentRow(label="Other data", size_bytes=int(0.25 * GB))]))
    return p


def test_the_deep_walk_may_still_truncate(panel, walks):
    """The precondition. C:/Windows does this every time."""
    p = _six_row_truncated(panel, walks)

    assert p._measuring_more is False
    assert p._contents.truncated is True


def test_the_residual_would_otherwise_fall_past_the_cap(panel, walks):
    """Stated so the test cannot quietly stop exercising the case."""
    from app.models.entity_contents import removal_split

    p = _six_row_truncated(panel, walks)
    split = removal_split(p._current_entity, p._contents, [p._current_entity])

    assert len(split.removed.rows) == p._CONTENT_ROWS_SHOWN + 1
    assert split.removed.rows[-1].residual is True


def test_the_residual_stays_visible(panel, walks):
    p = _six_row_truncated(panel, walks)

    assert any("not itemised" in l for l in _labels(p)), _labels(p)


def test_an_ordinary_row_gives_up_its_place(panel, walks):
    """The smallest measured row, not the accounting."""
    p = _six_row_truncated(panel, walks)
    labels = _labels(p)

    assert "Other data" not in labels
    assert "envs" in labels and "pkgs" in labels


def test_the_visible_and_hidden_counts_still_add_up(panel, walks):
    p = _six_row_truncated(panel, walks)
    visible = [w for w in p._content_row_pool if w.isVisibleTo(p)]

    # root + the capped rows, and exactly one row still hidden behind the button
    assert len(visible) == p._CONTENT_ROWS_SHOWN + 1
    assert p._btn_contents_more.text() == "+1 more"


def test_expanding_shows_everything_including_the_residual(panel, walks):
    p = _six_row_truncated(panel, walks)
    p._contents_expanded = True
    p._render_contents()
    labels = _labels(p)

    assert "Other data" in labels
    assert any("not itemised" in l for l in labels)
    assert p._btn_contents_more.text() == "Show less"


def test_a_complete_walk_is_capped_exactly_as_before(panel, walks):
    """No residual exists, so nothing is pinned and nothing changes."""
    entity = _entity(int(24.3 * GB))
    p = panel(entity)
    p._populate_contents(entity)
    p._on_contents_measured("C:/thing", _walk(
        int(24.3 * GB),
        rows=[ContentRow(label="a", size_bytes=int(8.0 * GB)),
              ContentRow(label="b", size_bytes=int(6.0 * GB)),
              ContentRow(label="c", size_bytes=int(5.0 * GB)),
              ContentRow(label="d", size_bytes=int(3.0 * GB)),
              ContentRow(label="e", size_bytes=int(2.0 * GB)),
              ContentRow(label="f", size_bytes=int(0.3 * GB))]))
    labels = _labels(p)

    assert labels == ["Thing", "a", "b", "c", "d", "e"]
    assert p._btn_contents_more.text() == "+1 more"


def test_a_truncated_walk_within_the_cap_is_untouched(panel, walks):
    """Nothing is hidden, so there is nothing to pin."""
    entity = _entity(int(24.3 * GB))
    p = panel(entity)
    p._populate_contents(entity)
    p._on_contents_measured("C:/thing", _walk(int(2.2 * GB), truncated=True))
    p._on_contents_measured("C:/thing", _walk(
        int(10.0 * GB), truncated=True,
        rows=[ContentRow(label="envs", size_bytes=int(6.0 * GB)),
              ContentRow(label="pkgs", size_bytes=int(4.0 * GB))]))
    labels = _labels(p)

    assert labels[:3] == ["Thing", "envs", "pkgs"]
    assert any("not itemised" in l for l in labels)
    assert not p._btn_contents_more.isVisibleTo(p)


# ── a sample is not a breakdown either ────────────────────────────
#
# Reported on the beta.5 build: selecting miniconda3 showed ".condarc,
# .nonadmin, api-ms-win-core-console-l1-1-0.dll…" where the breakdown goes,
# then "Measuring contents…", then the real figures. Three answers to one
# question. Those names are children_sample — up to eight, in scandir order,
# without sizes — and quick_summary is the only thing that sets provisional,
# so the flag already marked them as "not a measurement".

def _just_selected(panel, walks, own=int(24.3 * GB), kept=(), files=None):
    """The instant after selection: a walk has started, nothing has returned."""
    entity = _entity(own, kept=kept, files=files)
    entity["children_sample"] = [".condarc", ".nonadmin",
                                 "api-ms-win-core-console-l1-1-0.dll"]
    world = [entity] + [_entity(int(2.0 * GB), path=p, name="nested")
                        for p in kept]
    p = panel(entity, world)
    p._populate_contents(entity)
    return p


def test_sampled_names_are_never_shown_as_contents(panel, walks):
    p = _just_selected(panel, walks)

    for name in (".condarc", ".nonadmin", "api-ms-win-core-console-l1-1-0.dll"):
        assert name not in _labels(p), _labels(p)


def test_the_first_visible_state_is_the_root_and_the_placeholder(panel, walks):
    p = _just_selected(panel, walks)

    assert _labels(p) == ["Thing", "Measuring contents…"]


def test_the_total_is_authoritative_from_the_first_frame(panel, walks):
    """It never has to be corrected, so it must not wait."""
    p = _just_selected(panel, walks)
    rows = [w for w in p._content_row_pool if w.isVisibleTo(p)]

    assert p._contents_title.text() == "WILL BE MOVED TO RECYCLE BIN"
    assert rows[0]._size.text() == "24.3 GB"


def test_nothing_provisional_is_published_before_a_measurement(panel, walks):
    """No residual row, and no WILL REMAIN computed against a sample."""
    p = _just_selected(panel, walks, kept=["C:/thing/nested"])

    assert not any("not itemised" in l for l in _labels(p))
    assert not p._remain_hdr_host.isVisibleTo(p)


def test_the_measured_breakdown_replaces_the_placeholder(panel, walks):
    p = _just_selected(panel, walks)

    p._on_contents_measured("C:/thing", _walk(
        int(24.3 * GB),
        rows=[ContentRow(label="envs", size_bytes=int(14.7 * GB)),
              ContentRow(label="pkgs", size_bytes=int(7.3 * GB))]))

    assert _labels(p) == ["Thing", "envs", "pkgs"]
    assert p._measuring_more is False


def test_one_fast_complete_walk_is_the_only_change_of_state(panel, walks):
    """A folder that measures quickly goes placeholder -> answer, and never
    shows a sample in between."""
    p = _just_selected(panel, walks, own=int(5.0 * GB))
    assert _labels(p) == ["Thing", "Measuring contents…"]

    p._on_contents_measured("C:/thing", _walk(
        int(5.0 * GB),
        rows=[ContentRow(label="src", size_bytes=int(3.0 * GB)),
              ContentRow(label="dist", size_bytes=int(2.0 * GB))]))

    assert _labels(p) == ["Thing", "src", "dist"]


def test_a_file_list_still_shows_its_files_immediately(panel, walks):
    """There the provisional rows *are* the answer — the real files, awaiting
    only their sizes — so they must not be hidden behind a placeholder."""
    p = _just_selected(panel, walks,
                       files=["C:/thing/a.zip", "C:/thing/b.zip"])

    labels = _labels(p)
    assert "a.zip" in labels and "b.zip" in labels
    assert "Measuring contents…" not in labels


def test_a_truncated_final_result_still_pins_its_residual(panel, walks):
    """The d8ac871 guarantee, unchanged by any of this."""
    p = _just_selected(panel, walks)
    p._on_contents_measured("C:/thing", _walk(int(2.2 * GB), truncated=True))
    p._on_contents_measured("C:/thing", _walk(
        int(19.45 * GB), truncated=True,
        rows=[ContentRow(label="Installed packages", size_bytes=int(1.3 * GB)),
              ContentRow(label="Package download cache", size_bytes=int(0.8 * GB)),
              ContentRow(label="envs", size_bytes=int(9.8 * GB)),
              ContentRow(label="pkgs", size_bytes=int(7.3 * GB)),
              ContentRow(label="Other data", size_bytes=int(0.25 * GB))]))

    assert any("not itemised" in l for l in _labels(p)), _labels(p)
    assert p._btn_contents_more.text() == "+1 more"
