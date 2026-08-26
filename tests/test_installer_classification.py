"""An .exe is not an installer, and a program directory is not a pile of them.

Reported from a full C:/ scan: "C:\\Program Files\\Microsoft OneDrive" and
"AppData\\Local\\Programs\\Ollama" appeared under Installers. Both are installed
software. The cause was classifying by extension — .exe counted as an installer,
and an installed program's folder is mostly .exe files.

Re-measured on the reporting machine over the real Program Files and
Local/Programs trees, the blast radius was larger than reported: **110 entities,
1.17 GB, every one of them offered for recycling**, including `python.exe`,
`java.exe`, `keytool.exe` and `OneDrive.exe` listed individually as installers.
After the fix that set is a single entity — RyzenMaster/bin/Setup.exe, which
really is a setup executable.

The real file listings from that machine are used verbatim below.
"""
import os

import pytest

from app.services import entity_detector as ed
from app.models.finding import Finding
from app.models.smart_entity import SmartEntity, actionability_for_type

MB = 1024 * 1024

# Real listing: 3 of 5 files are .exe — ratio 0.60, over the 0.4 threshold.
_ONEDRIVE_FILES = ["OneDrive.App.exe", "OneDrive.exe",
                   "OneDrive.VisualElementsManifest.xml",
                   "OneDriveStandaloneUpdater.exe", "Resources.pri"]

# Real listing: 3 of 6 files are .exe — ratio 0.50.
_OLLAMA_FILES = ["app.ico", "ollama app.exe", "ollama.exe",
                 "unins000.dat", "unins000.exe", "unins000.msg"]

# What a folder of genuine installers looks like.
_REAL_INSTALLERS = ["vlc-3.0.20-win64.exe", "python-3.11.5-amd64.exe",
                    "VSCodeUserSetup-x64.exe",
                    "obs-studio-30.0.2-full-installer.exe", "notes.txt"]


def _files(folder, names):
    return [Finding(path=f"{folder}/{n}", name=n, is_dir=False, size_bytes=8 * MB,
                    extension=os.path.splitext(n)[1].lower(),
                    modified=1, accessed=1, parent=folder)
            for n in names]


def _looks_installer(name):
    return ed._looks_like_installer_file(name, os.path.splitext(name)[1].lower())


# ── the predicate: does this file install software, or is it software? ──

@pytest.mark.parametrize("name", [
    "vlc-3.0.20-win64.exe",              # version + arch
    "python-3.11.5-amd64.exe",
    "node-v20.11.0-x64.exe",
    "VSCodeUserSetup-x64.exe",           # arch suffix
    "npp.8.6.Installer.exe",             # named
    "vc_redist.x64.exe",
    "7z2301-x64.exe",
    "gstreamer-1.0-x86_64-1.22.12.msi",  # package extension needs no name evidence
    "AnythingAtAll.msi",
])
def test_recognises_real_installers(name):
    assert _looks_installer(name)


@pytest.mark.parametrize("name", [
    "OneDrive.exe",
    "OneDrive.App.exe",
    "OneDriveStandaloneUpdater.exe",  # camelCase "Updater" is not installer intent
    "ollama.exe",
    "ollama app.exe",
    "Code.exe",
    "ffmpeg.exe",
    "python.exe",                    # was listed as an installer before the fix
    "java.exe",
    "keytool.exe",
])
def test_rejects_plain_program_executables(name):
    assert not _looks_installer(name)


# ── content classification ────────────────────────────────────────

@pytest.mark.parametrize("folder,names", [
    ("C:/Program Files/Microsoft OneDrive", _ONEDRIVE_FILES),
    ("C:/Users/u/AppData/Local/Programs/Ollama", _OLLAMA_FILES),
])
def test_a_program_directory_is_not_an_installer_collection(folder, names):
    assert ed._classify_by_content(_files(folder, names)) != "installer_group"


def test_a_real_installer_folder_still_classifies_as_installers():
    """The fix must not blind Podbye to genuine installer collections."""
    got = ed._classify_by_content(_files("C:/Stash/Installers", _REAL_INSTALLERS))
    assert got == "installer_group"


# ── the install-root guard ────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "c:/program files/microsoft onedrive",
    "c:/program files (x86)/some app",
    "c:/users/u/appdata/local/programs/ollama",
])
def test_install_root_children_are_recognised(path):
    assert ed._is_install_root_child(path)


@pytest.mark.parametrize("path", [
    "c:/program files",                                    # the root itself
    "c:/program files/amd/ryzenmaster",                    # a component, one deeper
    "c:/users/u/appdata/local/programs",                   # the per-user root itself
    "c:/mats/{guid}/filebackup/c/program files/thing",     # a backup copy
])
def test_non_install_root_children_are_not(path):
    assert not ed._is_install_root_child(path)


@pytest.mark.parametrize("etype", ["installer", "installer_group"])
def test_installer_in_an_install_root_is_corrected_to_application(etype):
    got, reason = ed._safety_correct_entity_type(
        "C:/Program Files/Microsoft OneDrive", etype)
    assert got == "application"
    assert "uninstaller" in reason


def test_a_genuine_installer_elsewhere_keeps_its_type():
    got, _ = ed._safety_correct_entity_type("C:/Stash/Installers", "installer_group")
    assert got == "installer_group"


# ── the rescue pass ───────────────────────────────────────────────

@pytest.mark.parametrize("path,etype", [
    ("C:/Program Files/Microsoft OneDrive", "installer_group"),
    ("C:/Users/u/AppData/Local/Programs/Ollama", "application_data"),
    ("C:/Program Files/gstreamer", "unknown_folder"),
])
def test_install_root_folders_are_rescued_as_applications(path, etype):
    ent = SmartEntity(path=path, name="whatever", entity_type=etype)
    assert ed._enrich_program_files_apps([ent]) == 1
    assert ent.entity_type == "application"
    assert ent.name == os.path.basename(path)
    assert ent.category == "Applications"


def test_the_rescue_never_talks_a_specific_classification_down():
    """A game or dev environment in Program Files keeps its answer."""
    ents = [SmartEntity(path="C:/Program Files/SomeGame", name="SomeGame",
                        entity_type="game"),
            SmartEntity(path="C:/Program Files/Python311", name="Python311",
                        entity_type="development_environment")]
    assert ed._enrich_program_files_apps(ents) == 0
    assert [e.entity_type for e in ents] == ["game", "development_environment"]


def test_installed_software_is_never_offered_for_recycling():
    """The consequence that made this a safety bug, asserted directly."""
    ent = SmartEntity(path="C:/Program Files/Microsoft OneDrive",
                      name="Microsoft OneDrive", entity_type="installer_group")
    ed._enrich_program_files_apps([ent])
    assert actionability_for_type(ent.entity_type, ent.risk) != "recycle"


# ── loose files ───────────────────────────────────────────────────

def test_a_lone_program_exe_is_not_bucketed_as_an_installer():
    """Pass 8 routed every loose .exe into the archive/installer bucket, which
    is how python.exe became "Installer (python)" with itself as the target."""
    assert not _looks_installer("python.exe")
    assert _looks_installer("python-3.11.5-amd64.exe")
