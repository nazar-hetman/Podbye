"""Typing two letters should not return two hundred rows.

Reported: "when I'm selecting there D it shows a lot of things not related to
D - to see Discord I need put Disc there".

The filter built one haystack per entity - name, path, category, every
duplicate location and every sampled child name, concatenated - and did a plain
substring test on it. Measured against the 706-entity C:/ export in the repo:

    query   matched
    d       700 of 706     nearly every path contains a d: AppData,
    di      204            ProgramData, Windows, Downloads
    dis      75
    disc     10            <- the first query that actually narrows

So the field did nothing until the fourth character. Now the query is answered
from the narrowest place that answers it at all - words in a name, then
anywhere in a name, then everything else - and "di" returns the nine rows whose
names begin a word with it.

Nothing became unreachable: a query that only exists in a path still finds its
rows through the last tier.
"""
import pytest
from PySide6.QtCore import Qt

from app.models.findings_table_model import (
    COL_NAME, FindingsFilterProxy, FindingsTableModel,
)


def _entity(name, path, category="Dev Artifacts", children=()):
    return {"name": name, "path": path, "category": category,
            "children_sample": list(children), "size_bytes": 1000,
            "risk": "Safe", "entity_type": "", "file_count": 1,
            "folder_count": 0}


WORLD = [
    _entity("npm Packages - discord_spellcheck",
            "C:/Users/n/AppData/Local/Discord/app-1.0/modules/node_modules"),
    _entity("npm Packages - discord_utils",
            "C:/Users/n/AppData/Local/Discord/app-1.0/modules/node_modules"),
    _entity("NVIDIA Corporation", "C:/ProgramData/NVIDIA Corporation"),
    _entity("OBS Studio", "C:/Program Files/obs-studio"),
    _entity("Packages - VisualStudio", "C:/ProgramData/Package Cache"),
    _entity("GPUCache", "C:/Users/n/AppData/Roaming/Slack/Cache"),
    _entity("Docker", "C:/Program Files/Docker"),
]


@pytest.fixture
def proxy(qapp):
    model = FindingsTableModel()
    model.set_entities(WORLD)
    p = FindingsFilterProxy()
    p.setSourceModel(model)
    return p


def _names(proxy):
    return sorted((proxy.data(proxy.index(row, COL_NAME), Qt.UserRole) or {})
                  .get("name", "") for row in range(proxy.rowCount()))


# -- the reported case ---------------------------------------------

def test_two_letters_find_the_app_they_start(proxy):
    proxy.set_search("di")

    assert _names(proxy) == ["npm Packages - discord_spellcheck",
                             "npm Packages - discord_utils"]


def test_letters_inside_other_words_do_not_drag_them_in(proxy):
    """NVI-di-A and Stu-di-o are why "di" used to return everything."""
    proxy.set_search("di")

    assert "NVIDIA Corporation" not in _names(proxy)
    assert "OBS Studio" not in _names(proxy)


def test_a_path_full_of_ds_is_not_a_match_for_d(proxy):
    """AppData, ProgramData, Roaming\\...\\Cache - every path has a d in it."""
    proxy.set_search("d")

    assert "GPUCache" not in _names(proxy)
    assert "Docker" in _names(proxy)


# -- and nothing became unreachable --------------------------------

def test_a_fragment_from_the_middle_of_a_name_still_matches(proxy):
    """No word begins with "isc", so the search widens to whole names."""
    proxy.set_search("isc")

    assert _names(proxy) == ["npm Packages - discord_spellcheck",
                             "npm Packages - discord_utils"]


def test_a_query_that_only_exists_in_a_path_still_works(proxy):
    """Nothing is named "roaming"; the search widens to paths for it."""
    proxy.set_search("roaming")

    assert _names(proxy) == ["GPUCache"]


def test_a_query_that_matches_nothing_matches_nothing(proxy):
    proxy.set_search("zzzz")

    assert _names(proxy) == []


def test_clearing_the_search_restores_every_row(proxy):
    proxy.set_search("di")
    proxy.set_search("")

    assert len(_names(proxy)) == len(WORLD)


# -- the joined-fields bug the rewrite also closed ------------------

def test_a_match_cannot_span_two_fields(qapp):
    """Concatenated raw, a name ending in "npm" followed by a path starting
    "c:/" matched "npmc:" - a string that exists nowhere."""
    model = FindingsTableModel()
    model.set_entities([_entity("npm", "C:/Users/n/tools")])
    proxy = FindingsFilterProxy()
    proxy.setSourceModel(model)

    proxy.set_search("npmc:")

    assert proxy.rowCount() == 0


def test_the_risk_filter_still_applies_alongside_the_search(qapp):
    model = FindingsTableModel()
    model.set_entities([
        {**_entity("Docker", "C:/Program Files/Docker"), "risk": "Safe"},
        {**_entity("Docker Desktop", "C:/Program Files/Docker"),
         "risk": "Review"},
    ])
    proxy = FindingsFilterProxy()
    proxy.setSourceModel(model)

    proxy.set_search("docker")
    proxy.set_risk_filter({"Review"})

    assert _names(proxy) == ["Docker Desktop"]
