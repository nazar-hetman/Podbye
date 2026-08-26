"""Keep: the user's own standing instruction about their own files.

Asked for: *"I need irizi focus — I'm adding it to protected state. If I click
select all, it won't be selected."*

Protected already existed, but it is Podbye's judgement about system locations
and it is recomputed by every scan. Keep is the other thing, and the difference
drives the design: it is stored in config rather than the session, it covers a
subtree rather than a path, and it is enforced in the cleanup engine as well as
the UI — a session reopened from History carries entity dicts built before the
mark was made, so the only layer that can be trusted is the one doing the
deleting.
"""
import pytest

from app.services import keep_list
from app.services.cleanup_engine import (
    CleanupResult, ProtectedPathError, move_to_recycle_bin, permanent_delete,
)


@pytest.fixture(autouse=True)
def fresh_store(tmp_path, monkeypatch):
    """A keep list backed by a throwaway config.json."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    keep_list.reset_for_tests()
    yield
    keep_list.reset_for_tests()


# ── what may be kept ──────────────────────────────────────────────

def test_keeping_a_project_folder(tmp_path):
    assert keep_list.keep(r"E:\Irizi Focus") is True
    assert keep_list.is_kept(r"E:\Irizi Focus") is True


def test_it_covers_everything_underneath():
    """People keep a project, not a file list."""
    keep_list.keep(r"E:\Irizi Focus")
    assert keep_list.is_kept(r"E:\Irizi Focus\build\out.exe") is True
    assert keep_list.is_kept(r"E:\Irizi Focus\.venv") is True


def test_a_sibling_is_not_kept():
    keep_list.keep(r"E:\Irizi Focus")
    assert keep_list.is_kept(r"E:\Irizi Focus Old") is False
    assert keep_list.is_kept(r"E:\Other") is False


@pytest.mark.parametrize("path", ["C:/", "C:", r"C:\Users\n\Downloads",
                                  r"C:\Users\n\Desktop", ""])
def test_too_broad_to_keep(path):
    """Keeping Downloads would quietly take most of the disk out of reach."""
    assert keep_list.can_keep(path) is False
    assert keep_list.keep(path) is False


def test_marking_a_parent_supersedes_what_is_inside_it():
    keep_list.keep(r"E:\Work\Irizi Focus")
    keep_list.keep(r"E:\Work")
    assert keep_list.kept_paths() == (r"E:\Work",)


def test_a_path_is_listed_back_the_way_it_was_given():
    """Settings shows this list; a mangled path reads as Podbye's mistake."""
    keep_list.keep(r"E:\Irizi Focus")
    assert keep_list.kept_paths() == (r"E:\Irizi Focus",)


def test_matching_ignores_case_and_separator():
    keep_list.keep(r"E:\Irizi Focus")
    assert keep_list.is_kept("e:/irizi focus/build") is True
    assert keep_list.unkeep("e:/IRIZI focus") is True


def test_the_mark_can_be_taken_back():
    keep_list.keep(r"E:\Irizi Focus")
    assert keep_list.unkeep(r"E:\Irizi Focus") is True
    assert keep_list.is_kept(r"E:\Irizi Focus") is False


def test_unkeeping_something_covered_but_not_marked():
    """The mark is on the root; a child cannot release itself."""
    keep_list.keep(r"E:\Irizi Focus")
    assert keep_list.unkeep(r"E:\Irizi Focus\build") is False
    assert keep_list.is_kept(r"E:\Irizi Focus\build") is True


def test_it_can_name_the_folder_the_mark_is_on():
    """"Kept" on a row the user never marked needs to explain itself."""
    keep_list.keep(r"E:\Irizi Focus")
    root = keep_list.kept_root_for(r"E:\Irizi Focus\build\out.exe")
    assert keep_list.display_name(root) == "Irizi Focus"


# ── it survives the thing that made it necessary ──────────────────

def test_it_outlives_the_session(tmp_path, monkeypatch):
    """A rescan rebuilds every entity; the mark is not one of them."""
    keep_list.keep(r"E:\Irizi Focus")
    keep_list.reset_for_tests()          # as if the app had restarted
    assert keep_list.is_kept(r"E:\Irizi Focus\anything") is True


# ── the engine refuses it, whatever the UI thought ────────────────

def test_recycling_skips_a_kept_path(tmp_path):
    target = tmp_path / "Irizi Focus"
    target.mkdir()
    (target / "data.bin").write_bytes(b"x" * 32)
    keep_list.keep(str(target))

    result = move_to_recycle_bin([str(target)])

    assert result.skipped_kept == [str(target)]
    assert result.succeeded == []
    assert target.exists(), "a kept folder was removed"


def test_the_skip_is_reported_apart_from_protected(tmp_path):
    """One is Podbye's refusal, the other is the user's."""
    assert CleanupResult().skipped_kept == []
    assert CleanupResult().skipped_protected == []


def test_a_file_inside_a_kept_folder_is_skipped_too(tmp_path):
    root = tmp_path / "Irizi Focus"
    (root / "sub").mkdir(parents=True)
    victim = root / "sub" / "notes.txt"
    victim.write_text("keep me")
    keep_list.keep(str(root))

    result = move_to_recycle_bin([str(victim)])

    assert result.skipped_kept == [str(victim)]
    assert victim.exists()


def test_permanent_delete_refuses_rather_than_skipping(tmp_path):
    """Nothing about that call is reversible, so it stops the batch."""
    target = tmp_path / "Irizi Focus"
    target.mkdir()
    keep_list.keep(str(target))

    with pytest.raises(ProtectedPathError):
        permanent_delete([str(target)], perm_delete_enabled=True)
    assert target.exists()


def test_an_unkept_path_is_still_deletable(tmp_path):
    """The guard must not become a blanket refusal."""
    keep_list.keep(str(tmp_path / "Irizi Focus"))
    other = tmp_path / "Scratch"
    other.mkdir()
    result = move_to_recycle_bin([str(other)])
    assert result.skipped_kept == []
