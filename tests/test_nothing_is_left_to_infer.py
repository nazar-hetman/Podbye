"""Every byte the action touches is on one side of the split or the other.

    IN THIS FOLDER == WILL BE REMOVED + WILL REMAIN

Both totals come from the model — ``own_bytes`` and ``contained_bytes``,
which ``_enforce_disjoint_sizes`` already guarantees are disjoint, so a byte
cannot be counted twice. Neither is summed from the rows it is displayed
above: rows *explain* a number, they do not produce it. That distinction is
the whole design, because it is what lets a truncated walk still add up.

Measured on the real session, 56 of 1,392 findings keep anything material, so
the split is the exception and the flat CONTENTS section stays the rule.

The case that forced the residual row, from that scan:

    C:/Windows   own 46.39 GB   walk measured 10.90 GB   short by 35.49 GB

Three itemised rows under a 46.39 GB heading, and 35 GB the reader was left
to notice for themselves. ``removal_split`` now emits "Rest of this folder —
not itemised" for exactly that shortfall. It is a different thing from the
unnamed "Other" row ``_condense`` already produces: that one is measured and
merely small, this one was never measured at all.

The remain branch shows four rows at most — C:/Windows keeps 59 findings and
enumerating them buries the branch that answers "what am I about to lose".
The cap hides rows, never bytes.
"""
import pytest

from app.models.deletion_scope import (
    contained_bytes, keeps_something_inside, own_bytes,
)
from app.models.entity_contents import (
    MODE_CONTENTS, MODE_FILES, ContentRow, Contents, REMAIN_ROWS_SHOWN,
    removal_split,
)

GB = 1024 ** 3
MB = 1024 ** 2
KB = 1024


def _entity(own, kept=0, kept_paths=(), path="C:/thing"):
    return {"path": path, "name": "Thing", "entity_type": "browser_profile",
            "size_bytes": own, "contained_bytes": kept,
            "contained_paths": list(kept_paths), "file_count": 500}


def _nested(path, size, name=None):
    return {"path": path, "name": name or path.rsplit("/", 1)[-1],
            "entity_type": "cache_folder", "size_bytes": size,
            "file_count": 10, "contained_paths": [], "contained_bytes": 0}


def _walk(rows, total=None, truncated=False):
    return Contents(mode=MODE_CONTENTS,
                    rows=[ContentRow(label=l, size_bytes=s) for l, s in rows],
                    total_bytes=total if total is not None
                    else sum(s for _l, s in rows),
                    total_files=100, truncated=truncated)


# ── the invariant, in every shape ─────────────────────────────────

def test_a_complete_walk_balances():
    kept = ["C:/thing/nested"]
    entity = _entity(int(3.9 * GB), int(1.7 * GB), kept)
    contents = _walk([("Emulator", int(3.2 * GB)), ("dist", 516 * MB),
                      ("build", 173 * MB), ("", 54 * MB)],
                     total=int(3.9 * GB))

    split = removal_split(entity, contents, [entity, _nested(kept[0], int(1.7 * GB))])

    assert split.balances
    assert split.in_folder == int(3.9 * GB) + int(1.7 * GB)
    assert split.removed.total_bytes == own_bytes(entity)
    assert split.remain.total_bytes == contained_bytes(entity)


def test_a_truncated_walk_still_balances():
    """The C:/Windows case: 10.90 GB itemised beneath a 46.39 GB heading."""
    kept = ["C:/w/n%d" % i for i in range(3)]
    entity = _entity(int(46.39 * GB), int(2.19 * GB), kept, path="C:/w")
    contents = _walk([("WinSxS", int(10.65 * GB)), ("bundle", 245 * MB),
                      ("", 12 * MB)],
                     total=int(10.90 * GB), truncated=True)
    world = [entity] + [_nested(p, int(0.73 * GB)) for p in kept]

    split = removal_split(entity, contents, world)

    assert split.balances
    assert sum(r.size_bytes for r in split.removed.rows) == split.removed.total_bytes


def test_the_shortfall_becomes_a_row_that_says_what_it_is():
    kept = ["C:/w/n0"]
    entity = _entity(int(46.39 * GB), int(2.19 * GB), kept, path="C:/w")
    contents = _walk([("WinSxS", int(10.65 * GB))],
                     total=int(10.65 * GB), truncated=True)

    split = removal_split(entity, contents, [entity, _nested(kept[0], int(2.19 * GB))])
    residual = split.removed.rows[-1]

    assert "not itemised" in residual.label
    assert residual.size_bytes == int(46.39 * GB) - int(10.65 * GB)
    assert residual.named, "it is a concept, not a folder that happens to be there"


def test_a_complete_walk_grows_no_residual_row():
    """The measured 'Other' row already covers the tail; a second one would
    be an invented number."""
    kept = ["C:/thing/nested"]
    entity = _entity(int(3.9 * GB), int(1.7 * GB), kept)
    contents = _walk([("Emulator", int(3.5 * GB)), ("", int(0.4 * GB))],
                     total=int(3.9 * GB))

    split = removal_split(entity, contents, [entity, _nested(kept[0], int(1.7 * GB))])

    assert not any("not itemised" in r.label for r in split.removed.rows)
    assert len(split.removed.rows) == 2


def test_the_totals_are_never_summed_from_the_rows():
    """A walk that measured nothing at all must still report the real scope."""
    kept = ["C:/thing/nested"]
    entity = _entity(int(8.0 * GB), int(1.0 * GB), kept)
    contents = _walk([], total=0, truncated=True)

    split = removal_split(entity, contents, [entity, _nested(kept[0], int(1.0 * GB))])

    assert split.removed.total_bytes == int(8.0 * GB)
    assert split.balances


# ── the remain branch ─────────────────────────────────────────────

def test_the_remain_list_is_capped_but_its_total_is_not():
    """C:/Windows keeps 59 findings. The cap hides rows, never bytes."""
    kept = ["C:/w/n%d" % i for i in range(12)]
    entity = _entity(int(46.0 * GB), 12 * 100 * MB, kept, path="C:/w")
    world = [entity] + [_nested(p, 100 * MB) for p in kept]
    contents = _walk([("WinSxS", int(46.0 * GB))], total=int(46.0 * GB))

    split = removal_split(entity, contents, world)

    assert len(split.remain.rows) == REMAIN_ROWS_SHOWN
    assert split.remain.hidden == 12 - REMAIN_ROWS_SHOWN
    assert split.remain.total_bytes == 12 * 100 * MB
    assert split.balances


def test_a_short_remain_list_hides_nothing():
    kept = ["C:/thing/a", "C:/thing/b"]
    entity = _entity(int(5.0 * GB), 400 * MB, kept)
    world = [entity] + [_nested(p, 200 * MB) for p in kept]

    split = removal_split(entity, _walk([("x", int(5.0 * GB))]), world)

    assert split.remain.hidden == 0
    assert len(split.remain.rows) == 2


# ── when the split does not apply ─────────────────────────────────

def test_nothing_preserved_means_no_split(qapp):
    """96% of findings. They keep the flat section they have today."""
    entity = _entity(int(5.58 * GB))

    assert keeps_something_inside(entity) is False


def test_an_insignificant_amount_preserved_means_no_split(qapp):
    """miniconda3: 27 KB of __pycache__ inside 24.3 GB."""
    entity = _entity(int(24.3 * GB), 27 * KB, ["C:/m/n%d" % i for i in range(8)])

    assert keeps_something_inside(entity) is False


# ── and the panel follows the model ───────────────────────────────

@pytest.fixture
def panel(qapp, _shared_panel):
    """One panel, rebound per case.

    _PreallocDetailPanel pre-builds every widget it will ever need, and one
    per test across three files was enough to end a full run in an access
    violation inside the garbage collector — surfacing ~1500 tests later in
    a locale test, which is how long it took to find. Rebinding is what the
    panel does on every row click anyway.

    recycle_cb is what makes a destructive action exist at all: with no
    callback there is nothing to describe and no split, asserted below.
    """
    def build(entity, contents, world):
        p = _shared_panel(world)
        p._current_entity = entity
        p._current_path = entity.get("path", "")
        p._contents = contents
        p._measuring_more = False
        p._render_contents()
        return p

    return build


def _unused(qapp):
    return None


def _split_case():
    kept = ["C:/thing/nested"]
    entity = _entity(int(3.9 * GB), int(1.7 * GB), kept)
    world = [entity, _nested(kept[0], int(1.7 * GB), "Version history")]
    contents = _walk([("Emulator", int(3.2 * GB)), ("dist", 516 * MB),
                      ("build", 173 * MB), ("", 54 * MB)], total=int(3.9 * GB))
    return entity, contents, world


def test_the_panel_names_both_branches(panel):
    p = panel(*_split_case())

    assert p._contents_title.text() == "WILL BE MOVED TO RECYCLE BIN"
    assert p._remain_title.text() == "WILL REMAIN"
    assert p._remain_hdr_host.isVisibleTo(p)


def test_the_action_header_carries_no_size(panel):
    """The figure belongs beside the name of the thing it is the size of."""
    p = panel(*_split_case())

    assert p._contents_meta.text() == ""


def test_the_root_row_carries_the_authoritative_size(panel):
    p = panel(*_split_case())
    rows = [w for w in p._content_row_pool if w.isVisibleTo(p)]

    assert rows[0]._name.full_text() == "Thing"
    assert rows[0]._size.text() == "3.9 GB"


def test_the_remain_header_keeps_its_total(panel):
    """That branch has no single natural root, so its total stays in the
    header."""
    p = panel(*_split_case())

    assert p._remain_meta.text() == "1.7 GB"


def test_a_finding_with_nothing_preserved_still_names_the_action(panel):
    """Consequence, not contents — but with no second branch, because
    nothing is preserved."""
    entity = _entity(int(5.58 * GB))
    contents = _walk([("Emulator", int(3.17 * GB)), ("dist", int(2.41 * GB))],
                     total=int(5.58 * GB))

    p = panel(entity, contents, [entity])
    rows = [w for w in p._content_row_pool if w.isVisibleTo(p)]

    assert p._contents_title.text() == "WILL BE MOVED TO RECYCLE BIN"
    assert rows[0]._size.text() == "5.6 GB"
    assert not p._remain_hdr_host.isVisibleTo(p)
    assert not p._remain_rows_host.isVisibleTo(p)


def test_an_insignificant_preserved_amount_draws_no_second_branch(panel):
    """miniconda3: 27 KB of __pycache__ inside 24.3 GB."""
    entity = _entity(int(24.3 * GB), 27 * KB, ["C:/m/n%d" % i for i in range(8)])
    world = [entity] + [_nested("C:/m/n%d" % i, 3 * KB) for i in range(8)]
    contents = _walk([("pkgs", int(7.0 * GB))], total=int(7.0 * GB), truncated=True)

    p = panel(entity, contents, world)

    assert not p._remain_hdr_host.isVisibleTo(p)


def test_a_truncated_walk_without_a_split_still_accounts_for_itself(panel):
    """The regression this unification exists to prevent: a 24.3 GB root over
    7.0 GB of children, with 17 GB left for the reader to notice."""
    entity = _entity(int(24.3 * GB), 27 * KB, ["C:/m/n0"])
    world = [entity, _nested("C:/m/n0", 27 * KB)]
    contents = _walk([("pkgs", int(7.0 * GB))], total=int(7.0 * GB), truncated=True)

    p = panel(entity, contents, world)
    rows = [w for w in p._content_row_pool if w.isVisibleTo(p)]
    labels = [w._name.full_text() for w in rows]

    assert rows[0]._size.text() == "24.3 GB"
    assert any("not itemised" in l for l in labels), labels


def test_a_file_list_is_never_split(panel):
    """It already names exactly what goes."""
    kept = ["C:/thing/nested"]
    entity = _entity(int(3.9 * GB), int(1.7 * GB), kept)
    entity["removable_file_paths"] = ["C:/thing/a.zip"]
    contents = Contents(mode=MODE_FILES,
                        rows=[ContentRow(label="a.zip", size_bytes=int(3.9 * GB))],
                        total_bytes=int(3.9 * GB), total_files=1)

    p = panel(entity, contents, [entity, _nested(kept[0], int(1.7 * GB))])
    rows = [w for w in p._content_row_pool if w.isVisibleTo(p)]

    # The action is named, because the files really are being recycled.
    assert p._contents_title.text() == "WILL BE MOVED TO RECYCLE BIN"
    # But no root row: a bucket removes the files it lists and the folder
    # they are in stays, so a root would say the opposite of what happens.
    assert [w._name.full_text() for w in rows] == ["a.zip"]
    assert not p._remain_hdr_host.isVisibleTo(p)


def test_the_preserved_rows_are_not_armable(panel):
    """Each is a finding with its own row and its own button. Two ways to arm
    one thing is how a screen starts disagreeing with itself."""
    p = panel(*_split_case())

    shown = [w for w in p._remain_row_pool if w.isVisibleTo(p)]
    assert shown, "the remain branch drew no rows"
    for widget in shown:
        assert not widget._check.isVisibleTo(p)


def test_the_split_and_the_sentence_agree(panel):
    """Both ask keeps_something_inside(), so they cannot disagree about
    whether anything is preserved."""
    entity, contents, world = _split_case()
    p = panel(entity, contents, world)

    assert keeps_something_inside(entity)
    assert p._remain_hdr_host.isVisibleTo(p)


# ── translation ───────────────────────────────────────────────────

@pytest.mark.parametrize("language", ["Ukrainian", "German", "Spanish",
                                      "Polish", "French"])
def test_the_new_copy_is_translated(language):
    from app.i18n import set_language, tr

    # "WILL BE REMOVED" was retired: the header names the actual action, so
    # it is the button's own words or nothing.
    keys = ["WILL BE MOVED TO RECYCLE BIN", "WILL BE UNINSTALLED",
            "WILL REMAIN", "Rest of this folder — not itemised"]
    try:
        set_language(language)
        missing = [k for k in keys if tr(k) == k]
        assert missing == [], f"{language}: {missing}"
    finally:
        set_language("English")


# ── the wording and the button cannot diverge ─────────────────────

def _panel_for(entity, world, **cbs):
    from app.screens.findings_dashboard import _PreallocDetailPanel

    kwargs = {"recycle_cb": lambda *_a: None, "entities_cb": lambda: world}
    kwargs.update(cbs)
    p = _PreallocDetailPanel(lambda *_a: None, lambda *_a: None, **kwargs)
    p._current_entity = entity
    return p


NO_ACTION = [
    ("dev_workspace", {"entity_type": "dev_workspace"}),
    ("protected", {"actionability": "protected"}),
    ("kept", {"actionability": "kept"}),
    ("group", {"is_group": True}),
    ("no path", {"path": ""}),
]


@pytest.mark.parametrize("name,overrides", NO_ACTION)
def test_no_action_means_no_action_language(qapp, name, overrides):
    """A workspace's recycle button is suppressed on purpose, and for one
    release the contents section still announced WILL BE REMOVED over 255 GB
    of source code no button would have touched."""
    entity = _entity(int(255.4 * GB), int(8.6 * GB), ["E:/p/a"])
    entity.update(overrides)
    world = [entity, _nested("E:/p/a", int(8.6 * GB), "a")]
    p = _panel_for(entity, world)
    try:
        assert p._destructive_action(entity) == ""
        assert p._action_header(p._destructive_action(entity)) == ""
        assert p._removal_split(_walk([("x", 1)])) is None
    finally:
        p.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("name,overrides", NO_ACTION)
def test_the_header_never_promises_what_the_button_withholds(qapp, name, overrides):
    """Rendered, not reasoned about: whatever ends up in the title must not
    be action language when no destructive action exists."""
    entity = _entity(int(255.4 * GB), int(8.6 * GB), ["E:/p/a"])
    entity.update(overrides)
    world = [entity, _nested("E:/p/a", int(8.6 * GB), "a")]
    p = _panel_for(entity, world)
    p._contents = _walk([("Focus", int(253.6 * GB))], int(255.4 * GB))
    try:
        p._render_contents()
        title = p._contents_title.text()
        assert "WILL BE" not in title, title
        assert not p._remain_hdr_host.isVisibleTo(p)
    finally:
        p.deleteLater()
        qapp.processEvents()


def test_with_no_recycle_callback_there_is_no_action(qapp):
    """The panel cannot recycle without one, so it must not say it will."""
    entity = _entity(int(5.0 * GB))
    p = _panel_for(entity, [entity], recycle_cb=None)
    try:
        assert p._destructive_action(entity) == ""
    finally:
        p.deleteLater()
        qapp.processEvents()


def test_the_button_gate_and_the_wording_read_one_function():
    """Asserted on the source. They were computed separately for one release
    and immediately disagreed; an assert in populate() now ties the
    button's own gate to the function the wording reads."""
    import inspect

    from app.screens.findings_dashboard import _PreallocDetailPanel

    src = inspect.getsource(_PreallocDetailPanel.populate)
    assert "_destructive_action(entity)" in src
    assert "assert allow_recycle" in src


def test_every_header_word_belongs_to_a_real_action():
    """No third verb can appear without a matching action."""
    from app.screens.findings_dashboard import _PreallocDetailPanel

    entity = _entity(int(5.0 * GB))
    p = _PreallocDetailPanel(lambda *_a: None, lambda *_a: None,
                             recycle_cb=lambda *_a: None,
                             entities_cb=lambda: [entity])
    try:
        assert p._action_header("recycle") == "WILL BE MOVED TO RECYCLE BIN"
        assert p._action_header("uninstall") == "WILL BE UNINSTALLED"
        assert p._action_header("") == ""
        assert p._action_header("something-new") == ""
    finally:
        p.deleteLater()
