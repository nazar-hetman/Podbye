"""Install detection for app support folders.

The rule this module exists to enforce: we can prove an application is PRESENT,
but we can never prove it is absent. Measured on a real profile, matching a
folder name against the uninstall registry alone mislabels most apps —
``.vscode`` (2.9 GB) and ``.lmstudio`` (2.65 GB) are not in it although both are
installed. A wrong "orphaned" verdict invites deleting live data.
"""
import pytest

from app.services import app_presence as ap
from app.services.app_presence import presence, describe, PRESENT, UNKNOWN, GENERIC


@pytest.fixture
def sources(monkeypatch):
    """Deterministic evidence — the real machine is not consulted."""
    ev = {"registry": set(), "installed folder": set(),
          "Start Menu": set(), "PATH": set(), "running process": set()}
    monkeypatch.setattr(ap, "evidence", lambda force_refresh=False: ev)
    return ev


# ── the sources each carry apps the others miss ──────────────────


@pytest.mark.parametrize("source", [
    "registry", "installed folder", "Start Menu", "PATH", "running process",
])
def test_any_single_source_is_enough(sources, source):
    sources[source] = {ap._norm("Blender")}
    state, via = presence(".blender")
    assert state == PRESENT
    assert via == source


def test_start_menu_rescues_vscode(sources):
    """The exact case that broke: absent from the registry, present on disk."""
    sources["registry"] = {ap._norm("Some Unrelated App")}
    sources["Start Menu"] = {ap._norm("Visual Studio Code")}
    assert presence(".vscode")[0] == PRESENT


def test_alias_maps_folder_name_to_product_name(sources):
    sources["installed folder"] = {ap._norm("LM Studio")}
    assert presence(".lmstudio")[0] == PRESENT


def test_running_process_counts_as_evidence(sources):
    sources["running process"] = {ap._norm("claude")}
    assert presence(".claude")[0] == PRESENT


# ── never claim absence ──────────────────────────────────────────


def test_no_evidence_is_unknown_not_absent(sources):
    state, via = presence(".somethingnobodyhas")
    assert state == UNKNOWN
    assert via == ""


def test_presence_never_returns_an_absent_state(sources):
    """Guards the module's central promise."""
    for name in (".aws", ".azure", ".gemini", ".madeupthing", "x"):
        assert presence(name)[0] in (PRESENT, UNKNOWN, GENERIC)


def test_description_of_unknown_does_not_invite_deletion(sources):
    text = describe(".gemini").lower()
    for claim in ("safe to delete", "orphan", "not installed", "no longer installed"):
        assert claim not in text, f"unsafe claim {claim!r} in: {text}"
    assert "could not confirm" in text


def test_description_of_present_says_keep(sources):
    sources["registry"] = {ap._norm("Ollama")}
    assert "keep" in describe(".ollama").lower()


# ── generic folders are not applications ─────────────────────────


@pytest.mark.parametrize("name", [".cache", ".config", ".local", ".tmp", ".logs"])
def test_convention_folders_are_generic(sources, name):
    assert presence(name)[0] == GENERIC


def test_generic_description_does_not_name_an_app(sources):
    assert "shared support folder" in describe(".cache").lower()


# ── matching precision ───────────────────────────────────────────


def test_short_fragments_do_not_match(sources):
    sources["PATH"] = {ap._norm("gozilla")}
    assert presence(".go")[0] == UNKNOWN, "2-letter name matched half the machine"


def test_prefix_matching_not_substring(sources):
    """'cache' must not match 'shadercache' — that is how false positives crept
    in and made the signal meaningless."""
    sources["installed folder"] = {ap._norm("ShaderCache")}
    assert presence(".somecache")[0] == UNKNOWN


def test_version_suffixes_still_match(sources):
    sources["Start Menu"] = {ap._norm("Visual Studio Code (User)")}
    assert presence(".vscode")[0] == PRESENT


def test_leading_dot_is_optional(sources):
    sources["registry"] = {ap._norm("Docker Desktop")}
    assert presence("docker")[0] == presence(".docker")[0] == PRESENT


def test_empty_name_is_unknown(sources):
    assert presence("")[0] == UNKNOWN
    assert presence(".")[0] == UNKNOWN


# ── wiring into the detector ─────────────────────────────────────


def _entity(path, etype="unknown_folder"):
    import os as _os
    from app.models.smart_entity import SmartEntity
    return SmartEntity(path=path, name=_os.path.basename(path), entity_type=etype,
                       size_bytes=1024, file_count=2, folder_count=1)


def test_generic_dotfolder_becomes_named_app_data(sources):
    from app.services.entity_detector import _enrich_support_folders
    sources["registry"] = {ap._norm("Ollama")}
    e = _entity("C:/Users/n/.ollama")
    assert _enrich_support_folders([e]) == 1
    assert e.entity_type == "application_data"
    assert e.name == "ollama (app data)"
    assert "keep this data" in e.risk_reason


def test_a_better_classification_is_not_downgraded(sources):
    """.ollama detected as ai_models knows more than 'application data' —
    keep the type, just add what we can prove about the owner."""
    from app.services.entity_detector import _enrich_support_folders
    sources["registry"] = {ap._norm("Ollama")}
    e = _entity("C:/Users/n/.ollama", etype="ai_models")
    _enrich_support_folders([e])
    assert e.entity_type == "ai_models", "specific type was downgraded"
    assert "installed" in e.risk_reason


def test_unconfirmed_owner_never_invites_deletion(sources):
    from app.services.entity_detector import _enrich_support_folders
    e = _entity("C:/Users/n/.somethingunknown")
    _enrich_support_folders([e])
    text = f"{e.risk_reason} {e.summary}".lower()
    for claim in ("safe to delete", "orphan", "not installed"):
        assert claim not in text, f"unsafe claim {claim!r}: {text}"


def test_only_profile_level_dotfolders_are_touched(sources):
    """A .git or .cache nested deep in a project is not profile app data."""
    from app.services.entity_detector import _enrich_support_folders
    deep = _entity("C:/Users/n/projects/thing/.cache")
    assert _enrich_support_folders([deep]) == 0
    assert deep.entity_type == "unknown_folder"


# ── matcher direction + evidence strength ────────────────────────


def test_a_shorter_tool_name_does_not_match_a_longer_folder(sources):
    """The folder "Archive" matched the tool "arch" — a short pool entry
    swallowing a longer folder name. That direction is now rejected."""
    sources["PATH"] = {ap._norm("arch")}
    assert presence(".archive")[0] == UNKNOWN


def test_an_extending_match_is_only_trusted_from_a_strong_source(sources):
    """"work" matching "workfolders" has exactly the same shape as
    "visualstudiocode" matching "visualstudiocodeuser", so it cannot be ruled
    out structurally. Evidence strength is what separates them: from PATH it is
    a coincidence, from an installed product it is real."""
    sources["PATH"] = {ap._norm("workfolders")}
    assert presence(".work")[0] == PRESENT, "permissive mode may still match"
    assert presence(".work", strong_only=True)[0] == UNKNOWN, (
        "a PATH coincidence must not drive a grouping decision")


def test_a_pool_entry_extending_the_candidate_still_matches(sources):
    """The direction we DO want: version/edition suffixes."""
    sources["Start Menu"] = {ap._norm("Visual Studio Code (User)")}
    assert presence(".vscode")[0] == PRESENT


def test_strong_only_ignores_path_and_processes(sources):
    """PATH and running processes are full of generic short names, so a
    grouping decision must not rest on them alone."""
    sources["PATH"] = {ap._norm("games")}
    assert presence(".games")[0] == PRESENT
    assert presence(".games", strong_only=True)[0] == UNKNOWN


def test_strong_only_still_accepts_installed_products(sources):
    for src in ap.STRONG_SOURCES:
        sources.clear()
        sources.update({k: set() for k in
                        ("registry", "installed folder", "Start Menu",
                         "PATH", "running process")})
        sources[src] = {ap._norm("PyCharm Community Edition")}
        assert presence("PyCharm Community Edition", strong_only=True)[0] == PRESENT


# ── review follow-up: cache invalidation after an uninstall ──────


def test_reset_cache_actually_clears_the_evidence(monkeypatch):
    calls = {"n": 0}

    def counting_source():
        calls["n"] += 1
        return {ap._norm("Thing")}

    monkeypatch.setattr(ap, "_SOURCES", (("registry", counting_source),))
    ap.reset_cache()
    ap.evidence()
    ap.evidence()
    assert calls["n"] == 1, "evidence should be cached"
    ap.reset_cache()
    ap.evidence()
    assert calls["n"] == 2, "reset_cache did not clear the snapshot"
    ap.reset_cache()


def test_deep_uninstall_clears_the_presence_cache():
    """Without this, a re-scan after uninstalling still reports the app as
    installed and tells the user to KEEP the leftovers they just removed."""
    import inspect
    from app.screens.findings_dashboard import CategoryDetailView
    src = inspect.getsource(CategoryDetailView._handle_deep_uninstall)
    assert "invalidate_installed_programs_cache" in src
    assert "reset_cache" in src, (
        "registry cache is refreshed but app_presence evidence is not")
