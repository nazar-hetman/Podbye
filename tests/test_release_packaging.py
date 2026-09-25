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
    spec = _read("podbye.spec")
    assert "COLLECT(" in spec, "one-file build would break LGPL v3 s4(d)"
    assert "exclude_binaries=True" in spec, (
        "binaries must live beside the exe, not inside it")


def test_the_build_ships_the_licence_texts():
    """The LGPL and the OFL both require their text to accompany every
    distribution, so the spec has to bundle them."""
    spec = _read("podbye.spec")
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


def test_the_installer_keeps_no_version_of_its_own():
    """Two copies of a version number always drift - the installer's did, and
    the installed program would then disagree with its own About screen.
    app/version.py is the only copy; the installer is handed it at compile
    time and must refuse to compile without it rather than fall back to a
    default that could be stale.
    """
    iss = _read("installer/Podbye.iss")
    assert not re.search(r'#define\s+AppVersion\b', iss), (
        "Podbye.iss must not define AppVersion; pass /DAppVersion=... instead")
    guard = re.search(r'#ifndef\s+AppVersion\s*\n\s*#error\b.*\n\s*#endif', iss)
    assert guard, "Podbye.iss must #error when AppVersion is not passed in"
    assert "{#AppVersion}" in iss


def test_the_release_workflow_passes_the_app_version_to_the_installer():
    workflow = _read(".github/workflows/release.yml")
    compile_line = next(line for line in workflow.splitlines()
                        if "installer\\Podbye.iss" in line and "$iscc" in line)
    assert "/DAppVersion=$env:APP_VERSION" in compile_line, compile_line
    assert "APP_VERSION: ${{ needs.version.outputs.version }}" in workflow


# ── the installer must not undo the folder layout ─────────────────


def test_the_installer_deploys_the_whole_folder():
    """The installer must ship the folder the spec builds, whole.

    The source is the spec's COLLECT folder in PyInstaller's dist\\ output,
    referenced by wildcard and recursed, so _internal\\ and the replaceable Qt
    DLLs inside it arrive as ordinary files. Comparing against the spec also
    catches the case a literal cannot: the installer packaging a folder no
    build produces.
    """
    iss = _read("installer/Podbye.iss").replace("/", "\\")
    spec = _read("podbye.spec")

    collect = spec[spec.index("COLLECT("):]
    built = re.search(r'name="([^"]+)"', collect).group(1)
    source_dir = re.search(r'#define\s+SourceDir\s+"([^"]+)"', iss).group(1)

    assert source_dir.split("\\")[-1] == built, (
        f"installer packages {source_dir!r}, but the spec builds {built!r}")
    # And the folder must be where the build actually puts it. PyInstaller
    # writes to dist\ unless told otherwise; pointing the installer at a copy
    # kept aside by hand (dist-beta5\) made the release workflow compile
    # against a folder no build creates.
    assert source_dir == f"..\\dist\\{built}", (
        f"installer packages {source_dir!r}, but the build writes dist\\{built}")
    assert "--distpath" not in _read(".github/workflows/release.yml"), (
        "a custom --distpath would move the build away from what the installer packages")
    assert re.search(r'Source:\s*"\{#SourceDir\}\\\*"', iss), (
        "the whole folder must be the source, not a hand-picked file list")
    assert "recursesubdirs" in iss, (
        "the installer must deploy _internal\\ — including the replaceable Qt DLLs")
    assert "createallsubdirs" in iss


def test_the_installer_does_not_silently_delete_user_data():
    """Scan history lives in %APPDATA%\\Podbye. Removing it without asking would
    lose a reinstalling user's records."""
    iss = _read("installer/Podbye.iss")
    assert "userappdata" in iss
    assert "MsgBox" in iss, "user data must be removed only after asking"


# ── the release workflow ──────────────────────────────────────────


def _jobs(workflow: str) -> dict[str, str]:
    """Top-level job id -> the text of that job, from a workflow file.

    Jobs are the two-space-indented keys under ``jobs:``. Deliberately a text
    split rather than a YAML parser, so the suite needs no extra dependency.
    """
    body = workflow[workflow.index("\njobs:\n") + len("\njobs:\n"):]
    parts = re.split(r"^  ([A-Za-z0-9_-]+):\s*$", body, flags=re.M)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def test_the_release_workflow_tests_before_it_builds():
    """A release that fails its own tests is worse than no release.

    Enforced by job dependency, not by step order: the build job cannot start
    until the test job has passed.
    """
    jobs = _jobs(_read(".github/workflows/release.yml"))
    assert {"version", "test", "build"} <= set(jobs), sorted(jobs)
    needs = re.search(r"^    needs:\s*(.+)$", jobs["build"], re.M).group(1)
    assert "test" in needs and "version" in needs, needs
    assert "pyinstaller --noconfirm podbye.spec" in jobs["build"]


def test_the_release_is_tested_exactly_the_way_ci_is():
    """The release used to run the whole suite as one "pytest -q" process -
    the configuration tests.yml had to split because a Windows runner
    exhausts GDI handles and dies part-way with no test name. The release now
    calls tests.yml itself, so the two strategies cannot drift apart again.
    """
    release = _read(".github/workflows/release.yml")
    jobs = _jobs(release)
    assert re.search(r"^    uses:\s*\./\.github/workflows/tests\.yml\s*$",
                     jobs["test"], re.M), jobs["test"]
    assert "pytest" not in jobs["build"], (
        "tests belong in tests.yml; the build job must not run its own suite")

    tests = _read(".github/workflows/tests.yml")
    on_block = tests[tests.index("\non:\n"):tests.index("\njobs:\n")]
    assert re.search(r"^  workflow_call:", on_block, re.M), (
        "tests.yml must be callable for release.yml to reuse it")
    assert 'pytest -q -m "${{ matrix.selection }}"' in tests
    assert "tools/check_test_split.py" in tests


def test_the_release_workflow_rejects_a_tag_that_is_not_the_app_version():
    jobs = _jobs(_read(".github/workflows/release.yml"))
    assert 'python tools/release_version.py --tag "$REF_NAME"' in jobs["version"]
    assert "REF_NAME: ${{ github.ref_name }}" in jobs["version"]


def test_the_release_workflow_publishes_exactly_the_expected_artifacts():
    workflow = _read(".github/workflows/release.yml")
    assert '"Podbye-$env:VERSION-windows-x64.zip"' in workflow
    assert '"installer\\Output\\PodbyeSetup-$env:APP_VERSION.exe"' in workflow
    assert "prerelease: ${{ contains(needs.version.outputs.version, '-') }}" in workflow


def test_the_release_workflow_checks_licence_compliance():
    workflow = _read(".github/workflows/release.yml")
    assert "Qt6*.dll" in workflow, "the workflow must verify Qt ships unpacked"
    assert "SHA256" in workflow, "releases must carry checksums"
