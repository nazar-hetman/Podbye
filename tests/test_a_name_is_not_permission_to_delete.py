"""Podbye may not offer to delete more than it actually identified.

From a real scan of this machine — 1,288 entities, 87.1 GB marked Safe:

    27.37 GB  dev_artifacts  risk=Safe  action=recycle  E:/Work/TestApps
    reason: "Development/test folder name"
    contained_bytes = 0    contained_paths = 0

``TestApps`` contains the substring "test". That was the entire case. Nothing
inside was identified — zero nested findings — and with no exclusions
``expand_targets`` returns ``[root]``, so the button recycled the whole tree:
a 26 GB Qt SDK install with its own MaintenanceTool, a git working copy with
uncommitted work possible, and 0.38 GB of genuine build output. 1.4% of what
was offered was actually regenerable, and that single row was 31% of every
byte the scan called Safe.

Three more of the same family were live on the same scan:

    5.31 GB  C:/Program Files (x86)/Microsoft SDKs   "Known monolith distribution"
    2.03 GB  C:/Program Files (x86)/Windows Kits     "Known monolith distribution"
    3.94 GB  C:/Users/<u>/.vscode                    "…installed… — keep this data"

The first two came from a table mapping installed toolchains to
``dev_artifacts`` — a type whose own definition is "produced by development
tooling, safe to regenerate". Windows Kits does not regenerate; deleting it
breaks the C++ toolchain. The third said "keep this data" in its reason while
offering Move to Recycle Bin, because the pass that writes that sentence never
touched the risk beside it.

And ``r-`` was a two-character prefix pattern claiming every folder whose name
began with it, each one typed Safe and contained — so nothing inside could
ever correct it.

The rule these tests hold: **a name may suggest what a folder is, but only its
contents may license deleting it.**
"""
import pytest

from app.models.smart_entity import ENTITY_TYPES, _ENTITY_RISK, actionability_for_type
from app.services import entity_detector as ed


def _destructive(etype):
    """True when this type would be offered as a Safe whole-folder delete."""
    risk = _ENTITY_RISK.get(etype, "Review")
    return risk == "Safe" and actionability_for_type(etype, risk) == "recycle"


# ── item 1: an installed toolchain is not a build artifact ────────

TOOLCHAINS = [
    "windows kits", "microsoft sdks", "python3", "python 3", "android sdk",
    "androidsdk", "android-sdk", "javasoft", "msys64", "msys2", "cygwin",
    "texlive", "miktex", "vcpkg",
]


@pytest.mark.parametrize("name", TOOLCHAINS)
def test_a_toolchain_is_never_a_safe_recycle(name):
    """Each of these is installed software. None regenerates."""
    etype = ed._monolith_type(name)

    assert not _destructive(etype), (
        f"{name!r} -> {etype} is still offered as a Safe whole-folder delete")


@pytest.mark.parametrize("name", ["windows kits", "microsoft sdks", "python3"])
def test_the_reported_program_files_cases(name):
    etype = ed._monolith_type(name)
    risk = _ENTITY_RISK.get(etype)

    assert etype == "development_environment"
    assert risk == "Review"
    assert actionability_for_type(etype, risk) == "review_only"


def test_no_monolith_pattern_maps_to_a_disposable_type():
    """The general rule, so the next toolchain added cannot reintroduce this."""
    offenders = [f"{pat} -> {etype}"
                 for pat, etype in ed._MONOLITH_ENTITY_TYPES.items()
                 if _destructive(etype)]

    assert offenders == [], offenders


def test_the_monolith_default_is_not_disposable_either():
    """An unlisted pattern falls through to this."""
    assert not _destructive(ed._monolith_type("something-unlisted-entirely"))


def test_dev_artifacts_still_means_regenerable():
    """The premise of the retyping. If this ever stops being true the table
    above has to be revisited, not quietly relied on."""
    import app.models.smart_entity as se

    assert "dev_artifacts" in se._DEV_GENERATED_TYPES
    assert _destructive("dev_artifacts")


# ── item 2: matching is specific and boundary-aware ───────────────

@pytest.mark.parametrize("name", [
    "r-", "r-projects", "r-and-d-2024", "r-survey-kyiv", "r-drafts",
])
def test_r_dash_claims_nothing(name):
    """Two characters, matched by prefix, claiming a whole tree as Safe."""
    assert not ed._matches_monolith(name)


@pytest.mark.parametrize("name", [
    "vcpkgtools", "pythonic", "gimpy", "dockerfiles", "redistributables",
])
def test_a_word_that_merely_begins_the_same_way_is_not_a_match(name):
    assert not ed._matches_monolith(name)


@pytest.mark.parametrize("name", [
    "vcpkg", "vcpkg_colmap", "python 3.12", "android-sdk-r24",
    "qgis 3.40.11", "windows kits", "msys64",
])
def test_the_real_ones_still_match(name):
    """The guard must not disarm the feature it protects."""
    assert ed._matches_monolith(name)


def test_every_shipped_pattern_is_specific_enough():
    """A short pattern is how "r-" happened. This is the floor."""
    too_short = [p for p in ed._KNOWN_MONOLITH_PATTERNS
                 if len(p) < ed._MIN_MONOLITH_PATTERN]

    assert too_short == [], f"patterns below the minimum length: {too_short}"


def test_a_short_pattern_cannot_match_even_if_one_is_added():
    """Enforced in the matcher, not only in the data."""
    assert not ed._monolith_pattern_hit("r-projects", "r-")
    assert not ed._matches_monolith("r-projects", extra=("r-",))


# ── item 3: a name may label, only contents may license deletion ──

def test_testapps_is_no_longer_safe():
    """The reported row, at the function that produced it."""
    etype, reason = ed._weak_name_folder_type("testapps")

    assert not _destructive(etype)
    assert "contents were not read" in reason


@pytest.mark.parametrize("name", [
    "testapps", "my-test-fixtures", "mock-data", "stub-server", "snapshots",
])
def test_no_weak_name_awards_a_disposable_type(name):
    etype, _reason = ed._weak_name_folder_type(name)
    if etype:
        assert not _destructive(etype), f"{name!r} -> {etype}"


@pytest.mark.parametrize("name", [
    "chrome-cache-backup", "buildlogs", "temp-survey-2024", "my-crash-notes",
])
def test_no_keyword_name_awards_a_disposable_type(name):
    etype = ed._keyword_folder_type(name)
    if etype:
        assert not _destructive(etype), f"{name!r} -> {etype}"


def test_the_downgrade_is_to_something_honest():
    """Unknown is what Podbye actually knows here, and it is Review with no
    whole-folder delete."""
    etype = ed._name_only_type("dev_artifacts")

    assert etype == "unknown_folder"
    assert _ENTITY_RISK[etype] == "Review"
    assert actionability_for_type(etype, "Review") == "review_only"


@pytest.mark.parametrize("etype", ["dev_project", "document_folder",
                                   "application_data", "backup_group"])
def test_types_a_name_may_still_award_are_untouched(etype):
    """The gate is about destructive Safe types, not about labelling."""
    assert ed._name_only_type(etype) == etype


@pytest.mark.parametrize("etype", ["node_modules", "venv", "cache_folder",
                                   "build_folder"])
def test_exact_known_directories_keep_their_classification(etype):
    """The regression this fix must not cause. "node_modules" means one thing;
    it is not a guess from a substring, and it stays Safe."""
    assert _destructive(etype), f"{etype} lost its Safe classification"


def test_the_forbidden_set_is_exactly_the_disposable_types():
    """Kept in step by construction rather than by memory."""
    missed = [t for t in ENTITY_TYPES
              if _destructive(t) and t not in ed._NAME_ONLY_FORBIDDEN_TYPES]

    assert missed == [], f"a name could still award: {missed}"


# ── item 4: the reason and the button must agree ──────────────────

class _Ent:
    """Enough of a SmartEntity for the enrichment pass."""

    def __init__(self, path, entity_type, risk):
        self.path = path
        self.entity_type = entity_type
        self.risk = risk
        self.risk_reason = ""
        self.name = ""
        self.summary = ""


def _home():
    import os
    return os.path.expanduser("~").replace("\\", "/")


def test_a_row_never_says_keep_this_data_beside_a_delete_button(monkeypatch):
    """The .vscode case: 3.9 GB of extensions, risk Safe, action recycle, and
    a reason reading "vscode appears to be installed … — keep this data"."""
    from app.services import app_presence

    monkeypatch.setattr(app_presence, "presence",
                        lambda name, strong_only=False: (app_presence.PRESENT, "Start Menu"))

    ent = _Ent(f"{_home()}/.vscode", "dev_artifacts", "Safe")
    ed._enrich_support_folders([ent])

    assert "keep this data" in ent.risk_reason
    assert ent.risk == "Review"
    assert actionability_for_type(ent.entity_type, ent.risk) != "recycle"


def test_an_unconfirmed_owner_is_left_alone(monkeypatch):
    """Only a positive "it is installed" carries the demotion. UNKNOWN says
    check before removing, which is not the same claim."""
    from app.services import app_presence

    monkeypatch.setattr(app_presence, "presence",
                        lambda name, strong_only=False: (app_presence.UNKNOWN, ""))

    ent = _Ent(f"{_home()}/.somethingelse", "cache_folder", "Safe")
    ed._enrich_support_folders([ent])

    assert ent.risk == "Safe", "demoted a folder whose owner was never confirmed"


def test_the_demotion_does_not_invent_risk_where_there_was_none(monkeypatch):
    """A Review row stays Review rather than being escalated."""
    from app.services import app_presence

    monkeypatch.setattr(app_presence, "presence",
                        lambda name, strong_only=False: (app_presence.PRESENT, "registry"))

    ent = _Ent(f"{_home()}/.jetbrains", "application_data", "Review")
    ed._enrich_support_folders([ent])

    assert ent.risk == "Review"
