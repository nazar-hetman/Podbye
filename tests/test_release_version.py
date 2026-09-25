"""tools/release_version.py: the one reader of the application version.

The release workflow trusts this script to say which version it is building
and to stop a tag that names a different one. Both are cheap to get subtly
wrong - a regex that matches the wrong line, a comparison that forgives the
missing "v" - and expensive to notice after an installer has shipped.
"""
import importlib.util
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "release_version.py"

_spec = importlib.util.spec_from_file_location("release_version", SCRIPT)
release_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_version)


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, cwd=ROOT)


def test_it_reads_the_version_the_app_reports():
    from app.version import __version__
    assert release_version.read_version() == __version__


def test_the_app_version_is_well_formed():
    assert release_version.VERSION_RE.match(release_version.read_version())


def test_it_prints_the_version():
    from app.version import __version__
    proc = _run()
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == __version__


def test_the_matching_tag_passes():
    version = release_version.read_version()
    proc = _run("--tag", f"v{version}")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == version


@pytest.mark.parametrize("tag", [
    "{v}",              # the missing "v"
    "v{v}.1",           # a longer version that starts the same
    "v{v}-hotfix",
    "V{v}",
    "v0.0.0",
])
def test_a_mismatched_tag_fails_and_says_why(tag):
    version = release_version.read_version()
    proc = _run("--tag", tag.format(v=version))
    assert proc.returncode == 1
    assert proc.stdout == "", "nothing may be printed as a version on failure"
    assert "does not match the application version" in proc.stderr
    assert f"v{version}" in proc.stderr


def test_it_reads_only_the_real_assignment(tmp_path):
    fake = tmp_path / "version.py"
    fake.write_text('# __version__ = "9.9.9"\n'
                    'OTHER = "1.1.1"\n'
                    '__version__ = "2.3.4-rc.1"\n', encoding="utf-8")
    assert release_version.read_version(fake) == "2.3.4-rc.1"


def test_a_file_without_a_version_is_an_error(tmp_path):
    fake = tmp_path / "version.py"
    fake.write_text('VERSION = "1.0.0"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        release_version.read_version(fake)


@pytest.mark.parametrize("good", ["1.0.0", "1.0.0-rc.1", "1.0.0-beta.5", "10.20.30"])
def test_accepted_version_shapes(good):
    assert release_version.VERSION_RE.match(good)


@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "1.0.0 beta", "1.0.0-", "1.0.0-rc..1", ""])
def test_rejected_version_shapes(bad):
    assert not release_version.VERSION_RE.match(bad)
