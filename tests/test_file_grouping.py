"""The inspector's per-file list groups by kind instead of listing everything flat.

Two halves: the pure bucketing rules, and the panel that draws them. The panel
half skips automatically if a Qt application cannot be created.
"""
import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.models.file_grouping import (   # noqa: E402
    OTHER_KIND, default_expanded, group_files, kind_of, stat_files,
)


# ── Bucketing rules ───────────────────────────────────────────────

def test_extension_decides_the_bucket_case_insensitively():
    assert kind_of("C:/x/photo.JPG") == "Images"
    assert kind_of(r"C:\x\clip.mp4") == "Videos"
    assert kind_of("C:/x/disk.VHDX") == "Disk images"


def test_a_file_with_no_extension_is_other():
    assert kind_of("C:/x/README") == OTHER_KIND
    # A leading dot is the whole name, not an extension.
    assert kind_of("C:/x/.gitignore") == OTHER_KIND
    assert kind_of("") == OTHER_KIND


def test_only_the_last_dot_is_the_extension():
    """'app.config.json' is config, not an unknown '.config.json' kind."""
    assert kind_of("C:/x/app.config.json") == "Code & config"


def test_buckets_are_ordered_by_bytes_not_by_count():
    """The reason the list exists is size, so size leads — three icons must
    not outrank one video because there are more of them."""
    paths = ["a/i1.png", "a/i2.png", "a/i3.png", "a/movie.mp4"]
    stats = {"a/i1.png": (800, 1.0), "a/i2.png": (800, 1.0),
             "a/i3.png": (800, 1.0), "a/movie.mp4": (2_000_000_000, 1.0)}
    kinds = [g.kind for g in group_files(paths, stats)]
    assert kinds == ["Videos", "Images"]


def test_biggest_file_leads_inside_a_bucket():
    paths = ["a/small.zip", "a/huge.zip", "a/mid.zip"]
    stats = {"a/small.zip": (10, 1.0), "a/huge.zip": (999, 1.0),
             "a/mid.zip": (500, 1.0)}
    assert group_files(paths, stats)[0].paths == [
        "a/huge.zip", "a/mid.zip", "a/small.zip"]


def test_unstattable_files_keep_their_collected_order():
    """Nothing exists on disk here, so every size is 0 and the sort must not
    shuffle the list the caller handed over."""
    paths = ["C:/old/a.zip", "C:/b.7z", "C:/c.rar"]
    assert group_files(paths, stat_files(paths))[0].paths == paths


def test_a_bucket_carries_its_own_totals():
    paths = ["a/1.log", "a/2.log"]
    stats = {"a/1.log": (100, 500.0), "a/2.log": (50, 900.0)}
    g = group_files(paths, stats)[0]
    assert (g.count, g.total_bytes) == (2, 150)
    assert (g.oldest_mtime, g.newest_mtime) == (500.0, 900.0)


def test_trivia_starts_collapsed_next_to_something_that_matters():
    """4 KB of icons beside a 2 GB video is one row, not five decisions."""
    paths = [f"a/icon{i}.png" for i in range(5)] + ["a/movie.mp4"]
    stats = {p: (800, 1.0) for p in paths}
    stats["a/movie.mp4"] = (2_000_000_000, 1.0)
    groups = group_files(paths, stats)
    assert default_expanded(groups) == {"Videos"}


def test_a_lone_bucket_is_always_open():
    """With nothing to compare against, collapsing only adds a click."""
    paths = [f"a/icon{i}.png" for i in range(40)]
    stats = {p: (800, 1.0) for p in paths}
    groups = group_files(paths, stats)
    assert default_expanded(groups) == {"Images"}


def test_a_short_bucket_opens_whatever_its_share():
    """Three rows are cheaper to scroll past than to hide behind a chevron."""
    paths = ["a/movie.mp4", "a/note.txt", "a/other.txt"]
    stats = {"a/movie.mp4": (2_000_000_000, 1.0),
             "a/note.txt": (5, 1.0), "a/other.txt": (5, 1.0)}
    groups = group_files(paths, stats)
    assert default_expanded(groups) == {"Videos", "Documents"}


def test_nothing_is_ever_all_closed():
    """Sizes unknown everywhere — open them rather than show shut rows with
    no figures to justify shutting them."""
    paths = [f"a/f{i}.png" for i in range(10)] + [f"a/f{i}.mp4" for i in range(10)]
    stats = {p: (0, 0.0) for p in paths}
    groups = group_files(paths, stats)
    assert default_expanded(groups) == {g.kind for g in groups}


def test_the_stat_pass_is_bounded_by_count(tmp_path):
    """It runs on the UI thread on every row click; 4,000 stats measured
    580 ms cold."""
    files = []
    for i in range(30):
        f = tmp_path / f"f{i}.bin"
        f.write_bytes(b"x" * (i + 1))
        files.append(str(f))
    stats = stat_files(files, limit=10)
    assert len(stats) == 10


def test_unreached_paths_are_absent_rather_than_zero(tmp_path):
    """Absent lets the caller fall back to a live getsize for the rows it
    actually draws; a recorded 0 would print a wrong size instead."""
    files = []
    for i in range(5):
        f = tmp_path / f"f{i}.bin"
        f.write_bytes(b"x" * 100)
        files.append(str(f))
    stats = stat_files(files, limit=2)
    assert files[4] not in stats


def test_a_slow_disk_stops_the_pass_even_under_the_count_cap(tmp_path):
    """A count cap cannot bound a network drive; elapsed time can."""
    files = []
    for i in range(200):
        f = tmp_path / f"f{i}.bin"
        f.write_bytes(b"x")
        files.append(str(f))
    stats = stat_files(files, limit=10_000, budget_s=0.0)
    assert len(stats) < len(files)


# ── The panel ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # pragma: no cover
        pytest.skip("PySide6 not available")
    return QApplication.instance() or QApplication([])


def _panel(qapp, recycle_into=None):
    from app.screens.findings_dashboard import _PreallocDetailPanel
    return _PreallocDetailPanel(
        open_cb=lambda p: None,
        copy_cb=lambda p: None,
        recycle_cb=(recycle_into.update if recycle_into is not None else None),
        ask_ai_file_cb=lambda p: None,
    )


def _populate(panel, paths, stats=None):
    """Drive the panel with sizes we control — the files do not exist."""
    from app.screens import findings_dashboard as fd
    ent = {
        "path": "C:/x", "name": "Mixed bucket", "risk": "Optional",
        "entity_type": "archive_group", "actionability": "recycle",
        "removable_file_paths": paths,
    }
    if stats is None:
        panel.populate(ent)
        return
    real = fd.stat_files
    fd.stat_files = lambda ps, limit=4000: {p: stats[p] for p in ps}
    try:
        panel.populate(ent)
    finally:
        fd.stat_files = real


def test_a_collapsed_bucket_shows_one_row_not_one_per_file(qapp):
    paths = [f"C:/x/icon{i}.png" for i in range(20)] + ["C:/x/movie.mp4"]
    stats = {p: (800, 1.0) for p in paths}
    stats["C:/x/movie.mp4"] = (2_000_000_000, 1.0)
    panel = _panel(qapp)
    _populate(panel, paths, stats)
    # The video is open; the 20 icons are one closed bucket row.
    assert [p for _cb, p in panel._file_checks] == ["C:/x/movie.mp4"]
    assert sorted(k for _cb, k in panel._group_checks) == ["Images", "Videos"]


def test_a_collapsed_bucket_still_counts_and_still_selects(qapp):
    """Grouping never hides: the closed bucket's files are in the total, and
    ticking its row selects every one of them."""
    paths = [f"C:/x/icon{i}.png" for i in range(20)] + ["C:/x/movie.mp4"]
    stats = {p: (800, 1.0) for p in paths}
    stats["C:/x/movie.mp4"] = (2_000_000_000, 1.0)
    panel = _panel(qapp)
    _populate(panel, paths, stats)
    assert len(panel._all_file_paths) == 21
    panel._on_group_toggle("Images", True)
    assert len(panel._selected_files) == 20
    panel._on_group_toggle("Images", False)
    assert panel._selected_files == set()


def test_expanding_a_bucket_reveals_its_files(qapp):
    paths = [f"C:/x/icon{i}.png" for i in range(20)] + ["C:/x/movie.mp4"]
    stats = {p: (800, 1.0) for p in paths}
    stats["C:/x/movie.mp4"] = (2_000_000_000, 1.0)
    panel = _panel(qapp)
    _populate(panel, paths, stats)
    panel._toggle_file_group("Images")
    assert len(panel._file_checks) == 21


def test_bucket_header_reflects_member_selection(qapp):
    paths = ["C:/x/a.zip", "C:/x/b.zip"]
    panel = _panel(qapp)
    _populate(panel, paths)
    header = dict((k, cb) for cb, k in panel._group_checks)["Archives"]
    assert header.isChecked() is False
    for cb, _p in panel._file_checks:
        cb.setChecked(True)
    assert header.isChecked() is True


def test_every_bucket_is_on_screen_however_long_the_first_one_is(qapp):
    """The bug this replaced global paging for: a bucket of 713 DLLs owned
    every early page, so the 33 images and 7 config files behind it did not
    appear until page fifteen."""
    paths = ([f"C:/x/f{i}.dll" for i in range(300)]
             + [f"C:/x/i{i}.png" for i in range(33)]
             + [f"C:/x/c{i}.json" for i in range(7)])
    stats = {p: (1_000_000, 1.0) for p in paths}
    panel = _panel(qapp)
    _populate(panel, paths, stats)
    kinds = [k for _cb, k in panel._group_checks]
    assert set(kinds) == {"Programs & libraries", "Images", "Code & config"}


def test_a_long_bucket_shows_a_slice_and_says_how_much_is_left(qapp):
    from PySide6.QtWidgets import QPushButton
    paths = [f"C:/x/f{i}.zip" for i in range(120)]
    stats = {p: (1_000_000, 1.0) for p in paths}
    panel = _panel(qapp)
    _populate(panel, paths, stats)
    assert len(panel._file_checks) == 50
    more = [b for b in panel._files_container.findChildren(QPushButton)
            if b.text().startswith("Show ")]
    assert more and "70" in more[0].text()


def test_extending_one_bucket_leaves_the_others_alone(qapp):
    paths = ([f"C:/x/f{i}.zip" for i in range(120)]
             + [f"C:/x/i{i}.png" for i in range(60)])
    stats = {p: (1_000_000, 1.0) for p in paths}
    panel = _panel(qapp)
    _populate(panel, paths, stats)
    images_before = panel._group_shown(panel._group_by_kind("Images"))
    panel._show_more_in_group("Archives")
    assert panel._group_shown(panel._group_by_kind("Archives")) == 100
    assert panel._group_shown(panel._group_by_kind("Images")) == images_before


def test_recycle_button_states_the_size_it_would_free(qapp):
    """40 files is a different decision at 12 KB than at 12 GB."""
    paths = ["C:/x/a.zip", "C:/x/b.zip"]
    stats = {p: (5 * 1024 * 1024, 1.0) for p in paths}
    panel = _panel(qapp)
    _populate(panel, paths, stats)
    panel._file_checks[0][0].setChecked(True)
    assert "5" in panel._btn_recycle_files.text()
    assert "MB" in panel._btn_recycle_files.text()


def test_per_file_ask_ai_is_quieter_than_the_bucket_one(qapp):
    """The chrome must not outweigh the file it refers to."""
    from PySide6.QtWidgets import QPushButton
    paths = ["C:/x/a.zip", "C:/x/b.zip"]
    panel = _panel(qapp)
    _populate(panel, paths)
    buttons = panel._files_container.findChildren(QPushButton)
    asks = [b for b in buttons if b.text() == "Ask AI"]
    assert len(asks) == 3, "one per file plus one on the bucket header"
    bordered = [b for b in asks if "border: 1px solid transparent" not in b.styleSheet()]
    assert len(bordered) == 1, "only the bucket header carries the loud button"


# ── Width, in every language ──────────────────────────────────────
# The inspector is ~365 px at a 1456-wide window and its horizontal scrollbar
# is off, so anything wider than the viewport is not scrolled to — it is cut.

INSPECTOR_W = 365


def _wide_bucket_panel(qapp, language):
    from app.i18n import set_language
    from app.screens import findings_dashboard as fd
    set_language(language)
    paths = ([f"C:/x/icon{i}.png" for i in range(23)]
             + ["C:/x/a_very_long_file_name_that_keeps_going_and_going.mp4"]
             + [f"C:/x/blob{i}.bin" for i in range(9)])
    sizes = {p: 812 for p in paths}
    sizes["C:/x/a_very_long_file_name_that_keeps_going_and_going.mp4"] = 2_000_000_000
    for i in range(9):
        sizes[f"C:/x/blob{i}.bin"] = 40_000_000
    real = fd.stat_files
    fd.stat_files = lambda ps, limit=1500, budget_s=0.15: {
        p: (sizes.get(p, 0), 1_710_000_000.0) for p in ps}
    try:
        panel = _panel(qapp)
        panel.populate({"path": "C:/x", "name": "Bucket", "risk": "Optional",
                        "entity_type": "cache_folder", "actionability": "recycle",
                        "removable_file_paths": paths})
    finally:
        fd.stat_files = real
    panel.resize(INSPECTOR_W, 900)
    panel.show()
    qapp.processEvents()
    return panel


@pytest.mark.parametrize("language", ["English", "Ukrainian", "French"])
def test_the_file_list_fits_the_inspector_in_every_language(qapp, language):
    """French "Demander à l'IA" beside "Programmes et bibliothèques" ran the
    row 33 px past the panel, where it was silently cut."""
    from app.i18n import set_language
    try:
        panel = _wide_bucket_panel(qapp, language)
        viewport = panel._files_scroll.viewport().width()
        content = panel._files_container.width()
        assert content <= viewport, (
            f"{language}: file list is {content - viewport}px wider than the panel")
    finally:
        set_language("English")


@pytest.mark.parametrize("language", ["English", "Ukrainian", "French"])
def test_a_bucket_never_loses_its_name_to_the_layout(qapp, language):
    """Two elidable labels side by side let the layout collapse one of them to
    nothing, and the one that vanished was the only part saying what the rows
    below actually are."""
    from app.i18n import set_language
    from app.widgets.controls import ElidedLabel
    try:
        panel = _wide_bucket_panel(qapp, language)
        rows = [r for r in panel._files_container.children()
                if type(r).__name__ == "_FileGroupRow"]
        assert rows, "no bucket headers were drawn"
        for row in rows:
            name = row.findChild(ElidedLabel)
            assert name is not None and name.width() > 20, (
                f"{language}: a bucket header rendered with no readable name")
    finally:
        set_language("English")


def test_a_theme_switch_repaints_the_file_rows(qapp):
    """Every row bakes in the live palette; nothing used to re-apply it, so
    the list wore the old theme until a different entity was clicked."""
    from app.themes.theme_manager import build_qss, get_palette
    from app.widgets.controls import ElidedLabel
    paths = ["C:/x/a.zip", "C:/x/b.zip"]
    panel = _panel(qapp)
    _populate(panel, paths)
    before = get_palette().get("accent")
    other = next((k for k in ("amber", "mono", "paper")
                  if get_palette(k).get("accent") != before), None)
    assert other, "no second theme with a different accent to switch to"
    try:
        build_qss(other)                 # this is what sets the active theme
        panel._repaint_file_rows()
        # deleteLater() only *posts* a DeferredDelete; without this the old
        # rows are still children and the assertion reads a discarded widget.
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qapp.processEvents()
        header = next(r for r in panel._files_container.children()
                      if type(r).__name__ == "_FileGroupRow" and not r.isHidden())
        assert get_palette().get("accent") in header.findChild(ElidedLabel).styleSheet()
    finally:
        build_qss("forest")


def test_a_style_change_repaints_after_the_polish_not_during_it(qapp):
    """setStyleSheet() re-polishes every live widget, and the StyleChange
    announcing it arrives while Qt is walking the tree. Rebuilding the rows
    there pulled children out from under that walk and faulted with an access
    violation — it segfaulted the whole test suite."""
    from PySide6.QtCore import QEvent
    panel = _panel(qapp)
    _populate(panel, ["C:/x/a.zip", "C:/x/b.zip"])
    rows_before = [cb for cb, _p in panel._file_checks]
    qapp.sendEvent(panel, QEvent(QEvent.StyleChange))
    assert [cb for cb, _p in panel._file_checks] == rows_before, (
        "rows were rebuilt inside the style-change handler")
    qapp.processEvents()
    assert [cb for cb, _p in panel._file_checks] != rows_before, (
        "the deferred repaint never ran")
