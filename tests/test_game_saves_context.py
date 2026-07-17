"""Tests for game-save owning-game resolution.

These guard the pure helpers that turn a bare "Saves" finding into an
actionable "<Game> Saves — still installed / may be uninstalled" entity.
"""
from app.services.entity_detector import (
    _extract_owning_game,
    _normalize_game_name,
    _game_is_installed,
    _pretty_game,
)


# ── _extract_owning_game ──────────────────────────────────────────

def test_my_games_container_yields_following_segment():
    assert _extract_owning_game(
        "c:/users/nazar/documents/my games/skyrim/saves"
    ) == "skyrim"


def test_saved_games_container_yields_following_segment():
    assert _extract_owning_game(
        "c:/users/nazar/saved games/cyberpunk 2077"
    ) == "cyberpunk 2077"


def test_leaf_save_marker_uses_nearest_ancestor():
    assert _extract_owning_game(
        "d:/steamlibrary/steamapps/common/stardew valley/saves"
    ) == "stardew valley"


def test_publisher_is_skipped_for_game_under_appdata():
    # Larian Studios is the publisher; Baldur's Gate 3 is the game.
    assert _extract_owning_game(
        "c:/users/n/appdata/local/larian studios/baldur's gate 3/playerprofiles"
    ) == "baldur's gate 3"


def test_multi_game_container_itself_returns_empty():
    # The container folder has no single owning game.
    assert _extract_owning_game("c:/users/nazar/saved games") == ""


def test_generic_only_path_returns_empty():
    assert _extract_owning_game("c:/users/nazar/documents/saves") == ""


# ── _normalize_game_name ──────────────────────────────────────────

def test_normalize_strips_edition_noise():
    assert _normalize_game_name(
        "The Witcher 3: Wild Hunt - GOTY Edition"
    ) == "witcher 3 wild hunt"


def test_normalize_handles_punctuation_and_case():
    assert _normalize_game_name("Baldur's Gate 3") == "baldur s gate 3"


# ── _game_is_installed ────────────────────────────────────────────

def test_exact_normalized_match_is_installed():
    known = {"stardew valley", "portal 2"}
    assert _game_is_installed("stardew valley", known) is True


def test_token_overlap_match_is_installed():
    known = {"witcher 3 wild hunt"}
    assert _game_is_installed(_normalize_game_name("Witcher 3"), known) is True


def test_unrelated_name_is_not_installed():
    known = {"stardew valley", "portal 2"}
    assert _game_is_installed("skyrim", known) is False


def test_short_name_does_not_substring_false_match():
    # "ark" must not match "darksiders" via substring.
    assert _game_is_installed("ark", {"darksiders"}) is False


def test_empty_inputs_are_safe():
    assert _game_is_installed("", {"skyrim"}) is False
    assert _game_is_installed("skyrim", set()) is False


# ── _pretty_game ──────────────────────────────────────────────────

def test_pretty_game_preserves_apostrophe_casing():
    assert _pretty_game("baldur's gate 3") == "Baldur's Gate 3"


def test_pretty_game_title_cases_words():
    assert _pretty_game("cyberpunk 2077") == "Cyberpunk 2077"
