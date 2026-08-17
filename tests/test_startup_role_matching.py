"""A startup entry's role is decided by whole words, not by substrings.

Keywords used to be matched with a bare ``in``, against a haystack of the
entry name, its path, its publisher and its product name. Several of those
keywords are three or four letters, so anything whose name merely contained
one was mis-filed:

    'steam'  in 'msteams'   -> Teams filed as a Game launcher
    'box'    in 'xbox'      -> Xbox filed as Background sync
    'riot'   in 'patriot'   -> an RGB utility filed as a Game launcher
    'origin' in 'originals' -> anything under an originals/ folder likewise

The role is not cosmetic: it picks the risk tier, the boot-impact badge, the
recommendation sentence, and the description handed to the AI. Teams was being
described to the model as a game launcher.
"""
import pytest

from app.services.startup_detector import _infer_role, _classify_risk, _search_space


def role(name, path="", publisher="", product=""):
    return _infer_role(name, path, publisher, product)


# ── the collisions that were reported or found ────────────────────

def test_teams_is_not_a_game_launcher():
    """'msteams' contains 'steam' — the case in the screenshot."""
    assert role("Teams",
                r"C:/Program Files/WindowsApps/MSTeams_25076/ms-teams.exe",
                "Microsoft") == "Communication app"


def test_the_older_teams_path_is_also_a_communication_app():
    assert role("Teams",
                r"C:/Users/n/AppData/Local/Microsoft/Teams/current/Teams.exe",
                "Microsoft", "Microsoft Teams") == "Communication app"


def test_xbox_is_a_game_launcher_not_a_sync_client():
    """'xbox' contains 'box', and the sync branch ran first."""
    assert role("Xbox", r"C:/Program Files/WindowsApps/Xbox/XboxApp.exe",
                "Microsoft") == "Game launcher"


def test_a_patriot_utility_is_not_a_riot_launcher():
    assert role("Patriot Viper", r"C:/Program Files/Patriot/ViperRGB.exe",
                "Patriot") != "Game launcher"


def test_an_originals_folder_does_not_make_something_a_game_launcher():
    assert role("Backup", r"C:/Users/n/Documents/originals/backup.exe",
                "Acme") != "Game launcher"


# ── the matches that must keep working ────────────────────────────

@pytest.mark.parametrize("name, path, publisher, expected", [
    ("Steam", r"C:/Program Files (x86)/Steam/steam.exe", "Valve", "Game launcher"),
    ("EpicGamesLauncher", r"C:/Program Files/Epic Games/Launcher/EGL.exe",
     "Epic Games", "Game launcher"),
    ("OneDrive", r"C:/Program Files/Microsoft OneDrive/OneDrive.exe",
     "Microsoft", "Background sync"),
    ("Dropbox", r"C:/Program Files/Dropbox/Client/Dropbox.exe", "Dropbox",
     "Background sync"),
    ("Discord", r"C:/Users/n/AppData/Local/Discord/app.exe", "Discord Inc.",
     "Communication app"),
    ("Grammarly", r"C:/Users/n/AppData/Local/Grammarly/Desktop.exe",
     "Grammarly Inc.", "Creative helper"),
    ("Tailscale", r"C:/Program Files/Tailscale/tailscale-ipn.exe",
     "Tailscale Inc.", "Remote access service"),
])
def test_real_entries_still_get_their_role(name, path, publisher, expected):
    assert role(name, path, publisher) == expected


@pytest.mark.parametrize("name, expected", [
    # Registry values are routinely one run-on word; CamelCase splitting is
    # what keeps these matching after the switch to whole-word comparison.
    ("GoogleUpdate", "Update helper"),
    ("AdobeAAMUpdater", "Creative helper"),
    ("OneDriveSetup", "Background sync"),
])
def test_concatenated_registry_names_still_match(name, expected):
    assert role(name, rf"C:/Program Files/{name}.exe", "Acme") == expected


# ── the matcher itself ────────────────────────────────────────────

def test_a_word_is_matched_whole():
    space = _search_space("Patriot")
    assert " patriot " in space
    assert " riot " not in space


def test_camel_case_contributes_its_parts_and_the_whole():
    space = _search_space("MSTeams")
    assert " msteams " in space, "the run-on form must still be matchable"
    assert " teams " in space, "the sub-word must be matchable"
    assert " steam " not in space, "the mid-word collision must be gone"


def test_separators_split_words():
    space = _search_space(r"C:/Program Files/ms-teams.exe")
    assert " teams " in space
    assert " steam " not in space


def test_multi_word_keys_need_consecutive_words():
    assert " google drive " in _search_space("GoogleDrive")
    assert " google drive " in _search_space("Google Drive")
    assert " google drive " not in _search_space("Google Chrome Drive Letters")


# ── the role feeds the risk tier ──────────────────────────────────

def test_a_misfiled_role_no_longer_drags_the_risk_with_it():
    """Teams is Optional either way, but for the right reason now."""
    risk, _ = _classify_risk(
        "Teams", r"C:/Program Files/WindowsApps/MSTeams_25076/ms-teams.exe",
        "Microsoft", "")
    assert risk == "Optional"
