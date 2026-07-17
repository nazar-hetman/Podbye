"""Tests for UWP AppData/Local/Packages → named app-data mapping."""
from app.services.entity_detector import (
    _humanize_package_name,
    _split_camel,
    _is_appdata_packages_path,
)


# ── _split_camel ──────────────────────────────────────────────────

def test_split_camel_basic():
    assert _split_camel("WindowsCalculator") == "Windows Calculator"


def test_split_camel_single_word_unchanged():
    assert _split_camel("Photos") == "Photos"


def test_split_camel_letter_digit_boundary():
    assert _split_camel("Office365") == "Office 365"


# ── _humanize_package_name ────────────────────────────────────────

def test_known_package_maps_to_friendly_name():
    assert _humanize_package_name(
        "SpotifyAB.SpotifyMusic_zpdnekdrzrea0"
    ) == "Spotify"


def test_known_microsoft_package():
    assert _humanize_package_name(
        "Microsoft.WindowsCalculator_8wekyb3d8bbwe"
    ) == "Windows Calculator"


def test_unknown_package_is_parsed_from_last_segment():
    # Not in the curated map → parse "VendorName.CoolApp" → "Cool App".
    assert _humanize_package_name("VendorName.CoolApp_abcdef123456") == "Cool App"


def test_pure_id_package_falls_back_to_base():
    # No readable app name → keep the publisher-qualified base, not garbage.
    assert _humanize_package_name(
        "Microsoft.549981C3F5F10_8wekyb3d8bbwe"
    ) == "Microsoft.549981C3F5F10"


def test_no_underscore_name_handled():
    assert _humanize_package_name("SomeApp") == "Some App"


# ── _is_appdata_packages_path ─────────────────────────────────────

def test_packages_container_detected():
    assert _is_appdata_packages_path(
        "c:/users/nazar/appdata/local/packages"
    ) is True


def test_package_child_is_not_the_container():
    assert _is_appdata_packages_path(
        "c:/users/nazar/appdata/local/packages/spotifyab.spotifymusic_x"
    ) is False
