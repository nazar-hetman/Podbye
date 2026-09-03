"""A number that is not the whole answer must not be printed like one.

Two things the panel said about miniconda3, both true, both unhelpful.

    CONTENTS  2.3 GB · PARTIAL          under a row reading 24.3 GB

The folder really is 24.3 GB. The walk ran out of its budget at 2.3 and the
section printed that as its total, formatted exactly like a complete one — a
tenfold discrepancy with a four-letter chip to explain it. Said as coverage
against the row's own figure, the relationship is the message: *2.3 GB of
24.3 GB measured so far*. PARTIAL retires with it, having been the whole
explanation on its own.

    8 findings inside are listed separately and will be kept (27 KB).

27 KB of __pycache__ inside 24.3 GB. Also true. A preservation notice that
fires for a rounding error teaches the reader to skip the one that matters,
and the one that matters is Chrome keeping 2.0 GB of its 5.4. The sentence
now waits until the amount could change what someone does.

Neither of these could be fixed before the row's own size was trustworthy —
see test_the_row_counts_what_the_button_takes.py. Coverage is stated against
that figure, and materiality is measured as a share of it.
"""
import pytest

from app.models.deletion_scope import (
    contained_bytes, excluded_paths, keeps_something_inside, own_bytes,
)

GB = 1024 ** 3
MB = 1024 ** 2
KB = 1024


def _entity(size, kept_bytes=0, kept_paths=0, etype="dev_workspace"):
    """A workspace by default, because that is where the coverage line still
    lives. A finding with a destructive action gets the consequence view — an
    action header and a root row carrying the size — and its truncation is
    stated by the "Rest of this folder" row instead. Coverage in the header is
    for the case with no action and therefore no root: a workspace, a
    protected item, a kept one.
    """
    return {"path": "C:/thing", "name": "Thing", "entity_type": etype,
            "size_bytes": size, "contained_bytes": kept_bytes,
            "contained_paths": ["C:/thing/n%d" % i for i in range(kept_paths)],
            "file_count": 100}


# ── the preservation notice waits until it matters ────────────────

def test_the_miniconda_case_says_nothing():
    """27 KB inside 24.3 GB, across eight findings."""
    assert keeps_something_inside(_entity(int(24.3 * GB), 27 * KB, 8)) is False


def test_the_chrome_case_still_speaks():
    """2.0 GB of 5.4 GB genuinely stays behind, and the folder surviving the
    removal should be expected rather than alarming."""
    entity = _entity(int(5.4 * GB), int(2.0 * GB), 1, "browser_profile")

    assert keeps_something_inside(entity) is True


def test_nothing_nested_is_still_silent():
    assert keeps_something_inside(_entity(int(5.0 * GB))) is False


def test_a_small_finding_is_not_discussed_in_kilobytes():
    """The absolute floor. 0.5% of a 40 MB folder is 200 KB, which is not
    worth a line either."""
    assert keeps_something_inside(_entity(40 * MB, 300 * KB, 2)) is False


def test_a_material_share_of_a_small_finding_does_speak():
    assert keeps_something_inside(_entity(200 * MB, 60 * MB, 1)) is True


def test_the_threshold_is_relative_as_well_as_absolute():
    """20 MB clears the floor but is 0.08% of 24 GB — still noise there, and
    still worth saying inside a 1 GB folder."""
    assert keeps_something_inside(_entity(int(24.0 * GB), 20 * MB, 3)) is False
    assert keeps_something_inside(_entity(1 * GB, 20 * MB, 3)) is True


def test_paths_without_bytes_say_nothing():
    """A stored session can carry contained_paths with contained_bytes zero."""
    assert keeps_something_inside(_entity(int(5.0 * GB), 0, 4)) is False


def test_the_notice_and_its_numbers_come_from_one_source():
    """Whatever the line says is kept must be what the model says is kept."""
    entity = _entity(int(5.4 * GB), int(2.0 * GB), 3, "browser_profile")

    assert keeps_something_inside(entity)
    assert contained_bytes(entity) == int(2.0 * GB)
    assert len(excluded_paths(entity)) == 3
    assert own_bytes(entity) == int(5.4 * GB)


# ── a truncated walk states coverage, not a total ─────────────────

@pytest.fixture
def panel(qapp, _shared_panel):
    def build(entity, total_bytes, truncated):
        from app.models.entity_contents import ContentRow, Contents, MODE_CONTENTS

        p = _shared_panel([entity])
        p._current_entity = entity
        p._current_path = entity.get("path", "")
        p._contents = Contents(
            mode=MODE_CONTENTS,
            rows=[ContentRow(label="pkgs", size_bytes=total_bytes,
                             file_count=10, named=False, path="C:/thing/pkgs")],
            total_bytes=total_bytes, total_files=10, truncated=truncated)
        p._measuring_more = False
        p._render_contents()
        return p

    return build


def test_a_truncated_walk_states_what_it_covered(panel):
    """The reported line, and the figure it has to be measured against."""
    p = panel(_entity(int(24.3 * GB)), int(2.3 * GB), truncated=True)
    meta = p._contents_meta.text()

    assert "2.3 GB" in meta
    assert "24.3 GB" in meta
    assert "measured so far" in meta


def test_it_no_longer_prints_a_bare_partial_total(panel):
    """The exact failure: 2.3 GB formatted identically to a complete total."""
    p = panel(_entity(int(24.3 * GB)), int(2.3 * GB), truncated=True)

    assert p._contents_meta.text().strip() != "2.3 GB"
    assert "PARTIAL" not in p._contents_meta.text()


def test_a_complete_walk_is_unchanged(panel):
    """The ordinary case must not grow a sentence."""
    p = panel(_entity(int(5.0 * GB)), int(5.0 * GB), truncated=False)

    assert p._contents_meta.text() == "5.0 GB"


def test_the_tooltip_still_explains_the_shortfall(panel):
    p = panel(_entity(int(24.3 * GB)), int(2.3 * GB), truncated=True)

    assert "part of this folder was measured" in p._contents_meta.toolTip()


def test_a_complete_walk_carries_no_tooltip(panel):
    p = panel(_entity(int(5.0 * GB)), int(5.0 * GB), truncated=False)

    assert p._contents_meta.toolTip() == ""


def test_an_unknown_row_size_falls_back_to_the_marker(panel):
    """Coverage needs something to be measured against. With no row figure
    the old marker is still better than a bare number."""
    p = panel(_entity(0), int(2.3 * GB), truncated=True)
    meta = p._contents_meta.text()

    assert "PARTIAL" in meta
    assert "2.3 GB" in meta


# ── translation ───────────────────────────────────────────────────

@pytest.mark.parametrize("language", ["Ukrainian", "German", "Spanish",
                                      "Polish", "French"])
def test_the_new_line_is_translated(language):
    from app.i18n import set_language, tr

    key = "{measured} of {total} measured so far"
    try:
        set_language(language)
        assert tr(key) != key, f"{language} falls back to English"
        assert "{measured}" in tr(key) and "{total}" in tr(key)
    finally:
        set_language("English")
