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

# Panels built here are parentless, so nothing ever destroys them. Left alive
# they are re-polished by every later `app.setStyleSheet()` in the suite:
# these three files leaked 6,811 widgets between them, and test_theme_switching
# went from 13 s standalone to ~400 s in the full run because of it.
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
    """30 KB of icons beside a 2 GB video is one row, not forty decisions."""
    paths = [f"a/icon{i}.png" for i in range(40)] + ["a/movie.mp4"]
    stats = {p: (800, 1.0) for p in paths}
    stats["a/movie.mp4"] = (2_000_000_000, 1.0)
    groups = group_files(paths, stats)
    assert default_expanded(groups) == {"Videos"}


def test_a_short_list_opens_whole_however_it_splits():
    """Under SMALL_LIST nothing is buried, so folding part of it away only
    costs a click -- measured: most real file lists are this short."""
    paths = [f"a/icon{i}.png" for i in range(5)] + ["a/movie.mp4"]
    stats = {p: (800, 1.0) for p in paths}
    stats["a/movie.mp4"] = (2_000_000_000, 1.0)
    groups = group_files(paths, stats)
    assert default_expanded(groups) == {g.kind for g in groups}


def test_a_lone_bucket_is_always_open():
    """With nothing to compare against, collapsing only adds a click."""
    paths = [f"a/icon{i}.png" for i in range(60)]
    stats = {p: (800, 1.0) for p in paths}
    groups = group_files(paths, stats)
    assert default_expanded(groups) == {"Images"}


def test_a_short_bucket_opens_whatever_its_share():
    """Three rows are cheaper to scroll past than to hide behind a chevron."""
    paths = (["a/movie.mp4", "a/note.txt", "a/other.txt"]
             + [f"a/icon{i}.png" for i in range(40)])
    stats = {p: (800, 1.0) for p in paths}
    stats["a/movie.mp4"] = (2_000_000_000, 1.0)
    stats["a/note.txt"] = stats["a/other.txt"] = (5, 1.0)
    groups = group_files(paths, stats)
    assert {"Videos", "Documents"} <= default_expanded(groups)
    assert "Images" not in default_expanded(groups)


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


# ── Extensions that are not extensions, and buckets named after them ──

def test_a_version_tail_is_not_a_file_type():
    """"electron-v1.0.227-win32-x64" ends in ".227-win32-x64"; taking that as
    its type produced one bucket per build number."""
    from app.models.file_grouping import extension_of
    assert extension_of("C:/x/electron-v1.0.227-win32-x64") == ""
    assert kind_of("C:/x/electron-v1.0.227-win32-x64") == OTHER_KIND


def test_a_rotation_suffix_falls_back_to_the_real_extension():
    """"chrome_debug.log.1" is a log, and ".1" says nothing."""
    from app.models.file_grouping import extension_of
    assert extension_of("C:/x/chrome_debug.log.1") == ".log"
    assert kind_of("C:/x/chrome_debug.log.1") == "Logs & backups"
    assert extension_of("C:/x/1.2") == ""


def test_every_rolled_over_log_lands_in_logs():
    """26 x .log_backup1 was the second-largest 'Other files' contributor."""
    for name in ("a.log", "a.log2", "a.log_backup", "a.log_backup1", "a.logs"):
        assert kind_of("C:/x/" + name) == "Logs & backups", name


@pytest.mark.parametrize("name,kind", [
    ("shortcut.lnk", "Shortcuts"),
    ("site.url", "Shortcuts"),
    ("flight.kml", "Map & survey data"),
    ("cloud.laz", "Map & survey data"),
    ("registry.regtrans-ms", "Logs & backups"),
    ("system.evtx", "Logs & backups"),
    ("store.sqlite-wal", "Databases"),
    ("vpn.ovpn", "Code & config"),
    ("notes.drawio", "Documents"),
    ("symbols.pdb", "Programs & libraries"),
])
def test_extensions_that_used_to_fall_through(name, kind):
    assert kind_of("C:/x/" + name) == kind


def test_an_unknown_extension_seen_often_earns_its_own_row():
    """'Other files x 18' tells a reader nothing; '.pcm x 18' tells them
    something."""
    paths = [f"a/f{i}.pcm" for i in range(5)]
    groups = group_files(paths, {p: (10, 1.0) for p in paths})
    assert [g.kind for g in groups] == [".pcm"]
    assert groups[0].ext == ".pcm"


def test_a_one_off_unknown_extension_does_not_get_a_row():
    """A bucket per one-off extension is the noise the grouping removes."""
    paths = ["a/x.qqq", "a/y.zzz", "a/z.wow"]
    groups = group_files(paths, {p: (10, 1.0) for p in paths})
    assert [g.kind for g in groups] == [OTHER_KIND]
    assert groups[0].count == 3


def test_a_named_extension_bucket_renders_in_words():
    """An unknown extension seen often gets a row named after itself.

    The widget that drew it is gone; the naming rule that made ".pcm x 18"
    readable is not, because a findings row's subtitle still uses it.
    """
    groups = group_files([f"C:/x/take{i}.pcm" for i in range(18)])
    assert any(g.ext == ".pcm" for g in groups), [g.kind for g in groups]

