"""A row that stands for a list of files should say so, and open on the list.

Asked for: "when we click on 'Downloads archives' the list will show up beneath
the section — in that way user has information per unit and per group".

The per-file view already existed: the detail panel has a paginated Files tab
with a checkbox per file and its own recycle button. Nothing pointed at it, so
"Loose archives in Downloads" read as one indivisible thing. Rather than grow
the findings list into a tree — which would mean teaching sorting, filtering,
selection counts and the arm/checkbox logic about parent and child rows — the
row now says the list exists and selecting it lands there.

The distinction that decides both: an entity backed by a *list* of files
(loose buckets, archive and installer groups) versus one backed by a *folder*
(an app, a game, a photo library). Only the first is a group.
"""
import pytest

from app.screens.findings_dashboard import (
    _entity_file_group_size, _entity_contains_text,
)


def _bucket(n=6, **extra):
    """A loose bucket: its meaning is the file list it was built from."""
    e = {
        "name": "Loose archives in Downloads",
        "path": "C:/Users/u/Downloads",
        "entity_type": "archive_group",
        "entity_type_label": "Archive Files",
        "file_count": n, "folder_count": 0,
        "removable_file_paths": [f"C:/Users/u/Downloads/a{i}.zip" for i in range(n)],
    }
    e.update(extra)
    return e


def _folder(**extra):
    """A folder-backed entity: its meaning is the folder, not a list."""
    e = {
        "name": "Mission Planner 1",
        "path": "C:/Users/u/Downloads/Mission Planner 1",
        "entity_type": "download_item",
        "entity_type_label": "Downloaded Item",
        "file_count": 812, "folder_count": 40,
        "removable_file_paths": [],
    }
    e.update(extra)
    return e


# ── which entities are groups ─────────────────────────────────────

def test_a_loose_bucket_is_a_group():
    assert _entity_file_group_size(_bucket(6)) == 6


def test_a_folder_backed_entity_is_not():
    """812 files inside, but none of them individually actionable."""
    assert _entity_file_group_size(_folder()) == 0


def test_a_single_file_entity_is_not_a_group():
    """One installer is not a list to pick from."""
    single = _bucket(1, name="Installer (VSCodeUserSetup)", entity_type="installer")
    assert _entity_file_group_size(single) == 1


@pytest.mark.parametrize("value", [None, [], ["", None]])
def test_missing_or_empty_lists_are_handled(value):
    assert _entity_file_group_size(_folder(removable_file_paths=value)) == 0


# ── what the row says ─────────────────────────────────────────────

def test_a_group_row_advertises_the_file_list():
    text = _entity_contains_text(_bucket(6))
    assert "choose individual files" in text
    assert "6 files" in text, "dropped the existing content summary"
    assert "Archive Files" in text


def test_a_folder_row_is_left_alone():
    text = _entity_contains_text(_folder())
    assert "choose individual files" not in text
    assert "812 files" in text and "40 folders" in text


def test_a_single_file_row_makes_no_such_offer():
    single = _bucket(1, name="Installer (setup)", entity_type="installer")
    assert "choose individual files" not in _entity_contains_text(single)


def test_duplicates_keep_their_own_subtitle():
    dup = _bucket(4, entity_type="duplicate_group")
    assert "choose individual files" not in _entity_contains_text(dup)


@pytest.mark.parametrize("code", ["uk", "fr"])
def test_the_cue_is_translated(code):
    import io, json, pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "locales"
    table = json.load(io.open(root / f"{code}.json", encoding="utf-8"))
    assert table.get("choose individual files"), f"{code}: cue is untranslated"


# ── where selecting one lands ─────────────────────────────────────

class _FakeTabs:
    """Just enough QTabWidget for _populate_files_section."""

    def __init__(self, current=0):
        self._current = current
        self.enabled = {}
        self.visible = {}
        self.texts = {}

    def setTabEnabled(self, i, v): self.enabled[i] = v
    def setTabVisible(self, i, v): self.visible[i] = v
    def setTabText(self, i, t): self.texts[i] = t
    def currentIndex(self): return self._current
    def setCurrentIndex(self, i): self._current = i


def _panel_with(entity, starting_tab=0):
    """Drive _populate_files_section without building any Qt widgets.

    A plain class borrowing the two methods under test: _PreallocDetailPanel is
    a QWidget, and a QWidget cannot be allocated without running its __init__.
    """
    from app.screens.findings_dashboard import _PreallocDetailPanel as P

    class _Panel:
        _FILES_PER_PAGE = P._FILES_PER_PAGE
        _collect_entity_files = P._collect_entity_files
        _populate_files_section = P._populate_files_section

        def __init__(self):
            self._tabs = _FakeTabs(starting_tab)
            self._all_file_paths = []
            self._selected_files = set()
            self._file_checks = []
            self._file_groups = []
            self._file_stats = {}
            self._files_expanded = set()
            self._group_limit = {}

        def _render_files_page(self):
            pass

    panel = _Panel()
    panel._populate_files_section(entity)
    return panel


def test_selecting_a_group_lands_on_the_file_list():
    panel = _panel_with(_bucket(6))
    assert panel._tabs.currentIndex() == 1, "group row did not open on its files"
    assert panel._tabs.enabled[1] is True
    assert "6" in panel._tabs.texts[1]


def test_selecting_a_folder_entity_stays_on_information():
    panel = _panel_with(_folder(), starting_tab=0)
    assert panel._tabs.currentIndex() == 0


def test_moving_from_a_group_to_a_folder_returns_to_information():
    """The Files tab must not stay selected for an entity that has no list."""
    panel = _panel_with(_folder(), starting_tab=1)
    assert panel._tabs.currentIndex() == 0


def test_an_entity_with_no_files_disables_the_tab():
    panel = _panel_with(_bucket(1), starting_tab=1)
    assert panel._tabs.enabled[1] is False
    assert panel._tabs.currentIndex() == 0
