"""Invariants the release must not lose.

Packaging mistakes are quiet: nothing fails at run time, the app works fine,
and the problem only surfaces as a licensing complaint or a user installing
something labelled with the wrong version. These are cheap to check and
expensive to notice any other way.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


# ── the LGPL obligation lives in the spec ─────────────────────────


def test_the_build_stays_one_folder():
    """Qt is used under the LGPL v3, whose section 4(d) requires that whoever
    receives the program can replace the Qt libraries. A one-file build packs
    the DLLs inside the .exe where nobody can reach them, so the spec must
    keep producing a folder — COLLECT present, binaries excluded from EXE.
    """
    spec = _read("vigil.spec")
    assert "COLLECT(" in spec, "one-file build would break LGPL v3 s4(d)"
    assert "exclude_binaries=True" in spec, (
        "binaries must live beside the exe, not inside it")


def test_the_build_ships_the_licence_texts():
    """The LGPL and the OFL both require their text to accompany every
    distribution, so the spec has to bundle them."""
    spec = _read("vigil.spec")
    assert '("LICENSE", ".")' in spec
    assert '("THIRD-PARTY-NOTICES.md", ".")' in spec
    assert '_bundle_dir("licenses"' in spec


@pytest.mark.parametrize("name", [
    "LGPL-3.0.txt",             # Qt / PySide6
    "GPL-3.0.txt",              # referenced by the LGPL
    "SIL-Open-Font-License-1.1.txt",   # Inter, JetBrains Mono, Silkscreen
    "psutil-BSD-3-Clause.txt",
])
def test_every_required_licence_text_is_present(name):
    path = ROOT / "licenses" / name
    assert path.exists(), f"licenses/{name} is missing"
    assert path.stat().st_size > 500, f"licenses/{name} looks truncated"


def test_the_project_licence_is_present_and_names_its_terms():
    licence = _read("LICENSE")
    assert "PolyForm Noncommercial License 1.0.0" in licence
    assert "Required Notice:" in licence, "the notice PolyForm asks you to carry"


# ── version drift ─────────────────────────────────────────────────


def test_the_installer_version_matches_the_app():
    """Two copies of a version number always drift. The installed program
    would then disagree with its own About screen."""
    app_version = re.search(r'__version__\s*=\s*"([^"]+)"',
                            _read("app/version.py")).group(1)
    iss_version = re.search(r'#define\s+AppVersion\s+"([^"]+)"',
                            _read("installer/Vigil.iss")).group(1)
    assert iss_version == app_version, (
        f"installer says {iss_version}, app says {app_version}")


# ── the installer must not undo the folder layout ─────────────────


def test_the_installer_deploys_the_whole_folder():
    iss = _read("installer/Vigil.iss")
    assert "recursesubdirs" in iss, (
        "the installer must deploy _internal\\ — including the replaceable Qt DLLs")
    assert "..\\dist\\Vigil" in iss.replace("/", "\\")


def test_the_installer_does_not_silently_delete_user_data():
    """Scan history lives in %APPDATA%\\Vigil. Removing it without asking would
    lose a reinstalling user's records."""
    iss = _read("installer/Vigil.iss")
    assert "userappdata" in iss
    assert "MsgBox" in iss, "user data must be removed only after asking"


# ── the release workflow ──────────────────────────────────────────


def test_the_release_workflow_tests_before_it_builds():
    """A release that fails its own tests is worse than no release.

    Compared by STEP order, not by where the words appear: "pyinstaller" also
    shows up in the dependency-install line, which made a naive string
    comparison claim the build came first.
    """
    workflow = _read(".github/workflows/release.yml")
    steps = re.findall(r"^\s*- (?:name: (.+)|uses: (.+))$", workflow, re.M)
    names = [(a or b).strip() for a, b in steps]
    assert "Run tests" in names, names
    assert "Build" in names, names
    assert names.index("Run tests") < names.index("Build")


def test_the_release_workflow_checks_licence_compliance():
    workflow = _read(".github/workflows/release.yml")
    assert "Qt6*.dll" in workflow, "the workflow must verify Qt ships unpacked"
    assert "SHA256" in workflow, "releases must carry checksums"
