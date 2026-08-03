"""A database attributed to the wrong app is worse than one attributed to none.

Reported as "databases and saves can be improved". Measured on the reporting
machine, the category held 28 entities and its labels were mostly invented:

    C:/…/Temp/claude/e--Forge/<guid>      -> "Likely database for CPUID CPU-Z 2.17"
    C:/…/AppData/Local/Ollama             -> "Likely database for Windblown"
    C:/…/AppData/Local/FastStone/FSIV     -> "Likely database for LocalSend"
    C:/…/NVIDIA Corporation/…/NvBackend   -> "Likely database for LocalSend"
    C:/…/AppData/Local/Microsoft/Edge/…   -> "Likely database for Microsoft Office"

The cause: a path segment counted as a match if it was merely a *substring* of
an installed app's name. The segment "local", from AppData\\Local, is a
substring of "LocalSend version 1.17.0", so every database under AppData\\Local
belonged to LocalSend. Which app won also varied between runs, because the
first match in registry order took it.

The category itself held two things of opposite value — irreplaceable game
saves and disposable app state — so it is now split into Saves and Databases.
"""
import pytest

from app.services import entity_detector as ed
from app.models.smart_entity import SmartEntity


def _registry(*names):
    """installed_apps as _get_installed_programs returns it."""
    return {f"c:/apps/{n.lower()}": {"name": n} for n in names}


REAL = _registry(
    "LocalSend version 1.17.0", "CPUID CPU-Z 2.17", "Windblown",
    "Microsoft Office Professional Plus 2019", "Ollama version 0.2.1",
    "PyCharm Community Edition 2024.1", "Zoom Workplace", "WinMerge x64",
)


# ── the reported false attributions ───────────────────────────────

@pytest.mark.parametrize("path,name", [
    ("C:/Users/u/AppData/Local/Temp/claude/e--Forge/84fa135e/cpu.db", "cpu.db"),
    ("C:/Users/u/AppData/Local/FastStone/FSIV/FSIV.db", "FSIV.db"),
    ("C:/Users/u/AppData/Local/ConnectedDevicesPlatform/2cc5/A.db", "A.db"),
    ("C:/Users/u/AppData/Local/Microsoft/Edge/User Data/Default/x.db", "x.db"),
    ("C:/Users/u/AppData/Local/Microsoft/Windows/Notifications/wpn.db", "wpn.db"),
])
def test_a_structural_path_segment_names_nobody(path, name):
    """"local", "data", "default", "windows" are structure, not identity."""
    assert ed._find_related_app(path, name, REAL) == ""


def test_the_username_is_not_evidence():
    apps = _registry("Nazar Studio Pro")
    assert ed._find_related_app(
        "C:/Users/Nazar/AppData/Local/Whatever/x.db", "x.db", apps) == ""


# ── attribution that is actually earned ───────────────────────────

@pytest.mark.parametrize("path,expected", [
    ("C:/Users/u/AppData/Local/Ollama/history.db", "Ollama version 0.2.1"),
    ("C:/Users/u/AppData/Roaming/Zoom/data/telemetrydata.db", "Zoom Workplace"),
    # A folder carrying the app name plus an edition/version suffix.
    ("C:/Users/u/AppData/Roaming/JetBrains/PyCharmCE2024.1/state.db",
     "PyCharm Community Edition 2024.1"),
])
def test_a_folder_named_after_the_app_does_attribute(path, expected):
    assert ed._find_related_app(path, "x.db", REAL) == f"Likely database for {expected}"


def test_matching_is_never_reversed():
    """The whole bug in one assertion: segment ⊂ app-name must not count."""
    apps = _registry("LocalSend version 1.17.0")
    assert ed._find_related_app("C:/x/local/a.db", "a.db", apps) == ""
    assert ed._find_related_app("C:/x/localsend/a.db", "a.db", apps) != ""


def test_the_answer_does_not_depend_on_registry_order():
    """Several products from one vendor must not rename the folder per run."""
    names = ["NVIDIA App", "NVIDIA CUDA Visual Studio Integration",
             "NVIDIA FrameView SDK", "NVIDIA PhysX"]
    path = "C:/Users/u/AppData/Local/NVIDIA Corporation/NvBackend/DAO/x.db"

    answers = {ed._find_related_app(path, "x.db", _registry(*order))
               for order in (names, list(reversed(names)), names[2:] + names[:2])}
    assert len(answers) == 1, f"registry order changed the answer: {answers}"


def test_an_empty_registry_is_not_an_error():
    assert ed._find_related_app("C:/x/y/a.db", "a.db", {}) == ""


def test_a_very_short_app_name_never_matches():
    """Two- and three-letter names would match half the disk."""
    assert ed._find_related_app("C:/Users/u/AppData/Roaming/QtProject/a.db",
                                "a.db", _registry("Qt", "R", "Go")) == ""


# ── the name head ─────────────────────────────────────────────────

@pytest.mark.parametrize("registry_name,head", [
    ("LocalSend version 1.17.0", "localsend"),
    ("PyCharm Community Edition 2024.1", "pycharm"),
    ("Zoom Workplace", "zoom"),
    ("(Ollama)", "ollama"),
    ("", ""),
])
def test_the_identifying_word_is_extracted(registry_name, head):
    assert ed._app_name_head(registry_name) == head


# ── the category split ────────────────────────────────────────────

def test_saves_and_databases_are_no_longer_the_same_bucket():
    """Irreplaceable and disposable must not share one chip."""
    save = SmartEntity(path="C:/x/RenPy/DAv0.1", name="DAv0.1",
                       entity_type="game_saves")
    db = SmartEntity(path="C:/x/Ollama", name="history.db", entity_type="database")

    assert save.category == "Saves"
    assert db.category == "Databases"
    assert save.category != db.category


@pytest.mark.parametrize("etype", ["game_saves", "database"])
def test_neither_may_be_bulk_deleted(etype):
    """Splitting the category must not have made either one recycle-able."""
    from app.models.smart_entity import actionability_for_type
    e = SmartEntity(path="C:/x", name="x", entity_type=etype)
    assert actionability_for_type(e.entity_type, e.risk) == "review_only"


@pytest.mark.parametrize("code", ["uk", "fr"])
def test_the_new_category_names_are_translated(code):
    import io, json, pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "locales"
    table = json.load(io.open(root / f"{code}.json", encoding="utf-8"))
    for name in ("Databases", "Saves"):
        assert table.get(name), f"{code}: category {name!r} is untranslated"


def test_the_split_categories_still_get_a_colour():
    from app.screens.findings_dashboard import _get_category_color
    fallback = _get_category_color("definitely not a category")
    for name in ("Databases", "Saves", "Downloads", "Desktop"):
        assert _get_category_color(name) != fallback, f"{name} fell through to Other"
