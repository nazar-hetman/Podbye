"""The model called a 40 GB folder empty, because we told it to.

Traced end to end rather than guessed at. Three facts, all measured on this
machine:

* ``os.stat`` on a directory returns the size of its own entry — 4096 bytes on
  NTFS — however much is inside it. That is what "Ask AI" on a contents row put
  in the prompt.
* the scanner records every directory as ``size_bytes = 0`` on purpose, because
  the entity detector aggregates sizes afterwards. That is what the bulk pass
  put in the prompt: ``Size: 0 B``.
* the Contents section has already measured the folder, off the UI thread, and
  the figures are on screen next to the button that asks.

So a small model was handed one hard number, and it was a zero. It said the
folder was empty, and it was right about the prompt and wrong about the disk.

What is pinned here: a size is never sent as a bare number the caller did not
measure, the measurement the section already has travels with the request, and
the prompt states the count as a constraint rather than leaving it to be
inferred.
"""
import os

import pytest

from app.services.prompt_builder import build_prompt, build_entity_prompt


FOLDER = dict(path="C:/Steam/steamapps/common", name="common", is_dir=True,
              category="Games", risk="Review", source_rule="",
              modified="2025-01-01", accessed="2025-01-01")


# ── a directory's own stat is not the size of what is in it ───────

def test_a_directory_reports_its_own_entry_size_not_its_contents(tmp_path):
    """The premise of the bug, checked rather than assumed."""
    big = tmp_path / "big"
    big.mkdir()
    (big / "payload.bin").write_bytes(b"x" * 500_000)

    assert os.stat(big).st_size < 500_000


# ── an unmeasured folder is unknown, never zero ───────────────────

def test_an_unmeasured_folder_is_never_quoted_a_size():
    p = build_prompt(size="0 B", size_bytes=0, **FOLDER)

    assert "Size: 0 B" not in p
    assert "not measured" in p


def test_an_unmeasured_folder_is_forbidden_from_being_called_empty():
    p = build_prompt(size="0 B", size_bytes=0, **FOLDER).lower()

    assert "never say it is empty" in p


def test_an_unmeasured_folder_is_told_not_to_invent_a_size():
    """The failure mode this replaces is a confident number, not a hedge."""
    p = build_prompt(size="0 B", size_bytes=0, **FOLDER).lower()

    assert "unknown" in p
    assert "guess" in p


# ── a measured folder states its contents as a fact ───────────────

def test_a_measured_folder_states_what_it_holds():
    p = build_prompt(size="148 GB", size_bytes=158_913_789_952,
                     file_count=40_349, folder_count=812, **FOLDER)

    assert "40,349 files" in p
    assert "812 folders" in p
    assert "148 GB" in p


def test_a_measured_folder_forbids_the_empty_claim_outright():
    p = build_prompt(size="148 GB", size_bytes=158_913_789_952,
                     file_count=40_349, **FOLDER)

    assert "is NOT empty" in p


def test_a_file_is_left_alone():
    """A file's size comes off its own stat entry and was never in doubt — the
    constraint would be noise in every prompt that does not need it."""
    p = build_prompt(**{**FOLDER, "is_dir": False}, size="4 MB",
                     size_bytes=4_000_000)

    assert "NOT empty" not in p
    assert "Size: 4 MB" in p


def test_a_zero_byte_file_still_reports_zero():
    """Zero really is zero for a file, and saying so is useful."""
    p = build_prompt(**{**FOLDER, "is_dir": False}, size="0 B", size_bytes=0)

    assert "Size: 0 B" in p


@pytest.mark.parametrize("length", ["compact", "standard", "detailed"])
def test_every_length_carries_the_constraint(length):
    """A user on compact answers is not opted out of the facts."""
    p = build_prompt(size="0 B", size_bytes=0, length=length, **FOLDER).lower()

    assert "never say it is empty" in p


def test_the_compact_prompt_stays_small_enough_for_a_small_model():
    p = build_prompt(size="0 B", size_bytes=0, length="compact", **FOLDER)

    assert len(p) < 1200, f"compact prompt is {len(p)} chars — trim it"


# ── entities carry their own measurement ──────────────────────────

def _entity_prompt(**kw):
    base = dict(path="C:/Program Files/MEmu", name="MEmu",
                entity_type="installed_application",
                entity_type_label="Installed application", size="40.2 GB",
                size_bytes=43_000_000_000, file_count=479, folder_count=57,
                category="Applications", risk="Review", children_sample=[])
    base.update(kw)
    return build_entity_prompt(**base)


def test_an_entity_states_its_contents_as_a_fact():
    p = _entity_prompt()

    assert "479 files" in p
    assert "is NOT empty" in p


def test_an_entity_with_nothing_measured_says_so():
    p = _entity_prompt(size="0 B", size_bytes=0, file_count=0, folder_count=0)

    assert "not measured" in p
    assert "never say it is empty" in p.lower()


# ── the measurement travels with the request ──────────────────────

def _panel(asked):
    from app.screens.findings_dashboard import _PreallocDetailPanel
    return _PreallocDetailPanel(
        open_cb=lambda p: None, copy_cb=lambda p: None,
        ask_ai_file_cb=lambda path, facts=None: asked.append((path, facts)))


def _measured_contents():
    from app.models.entity_contents import Contents, ContentRow, MODE_CONTENTS
    return Contents(mode=MODE_CONTENTS, total_bytes=10 ** 11, total_files=40_349,
                    rows=[ContentRow(label="Installed games", size_bytes=10 ** 11,
                                     file_count=40_349,
                                     path="C:/Steam/steamapps/common")])


def test_the_row_hands_over_what_the_section_measured(qapp):
    asked = []
    panel = _panel(asked)
    panel._contents = _measured_contents()

    panel._on_content_ask("C:/Steam/steamapps/common")

    assert asked == [("C:/Steam/steamapps/common",
                      {"size_bytes": 10 ** 11, "file_count": 40_349})]


def test_a_provisional_section_hands_over_nothing(qapp):
    """A row drawn before the walk finished has no measurement, and inventing
    one is the failure this exists to prevent."""
    asked = []
    panel = _panel(asked)
    contents = _measured_contents()
    contents.provisional = True
    panel._contents = contents

    panel._on_content_ask("C:/Steam/steamapps/common")

    assert asked == [("C:/Steam/steamapps/common", {})]


def test_a_folder_finding_is_built_with_the_measured_size(tmp_path):
    """Not with os.stat's 4 KB, which is the size of the directory entry."""
    from app.screens.findings_dashboard import _finding_for_path
    folder = tmp_path / "common"
    folder.mkdir()

    finding = _finding_for_path(str(folder), 10 ** 11)

    assert finding.size_bytes == 10 ** 11


def test_a_folder_finding_with_nothing_measured_is_zero_not_four_kilobytes(tmp_path):
    """Zero reaches the prompt as "not measured"; 4 KB reaches it as a fact."""
    from app.screens.findings_dashboard import _finding_for_path
    folder = tmp_path / "common"
    folder.mkdir()

    assert _finding_for_path(str(folder)).size_bytes == 0


# ── the explainer sends it, and survives "Ask again" ──────────────

class _Store:
    def __init__(self, **kw):
        self._d = {"ai_model": "llama", "ai_endpoint": "http://x",
                   "ai_tone": "neutral", "ai_length": "standard",
                   "ai_explanation_language": "English", "ai_timeout": 5}
        self._d.update(kw)

    def get(self, key, default=None):
        return self._d.get(key, default)


def _finding(**kw):
    from app.models.finding import Finding
    base = dict(path="C:/Steam/steamapps/common", name="common", is_dir=True,
                size_bytes=0, extension="", modified=0.0, accessed=0.0,
                parent="C:/Steam/steamapps")
    base.update(kw)
    return Finding(**base)


def _prompt_from_one_explain(monkeypatch, *, facts, force=False):
    """Run one _explain with the network stubbed, and return the prompt."""
    from app.services import ai_explainer as ax
    seen = {}

    def _generate(**kw):
        seen["prompt"] = kw["prompt"]
        return True, "An answer."

    monkeypatch.setattr(ax, "generate", _generate)
    monkeypatch.setattr(ax, "_save_cached", lambda *a, **k: None)
    monkeypatch.setattr(ax, "_load_cached", lambda *a, **k: None)

    explainer = ax.AIExplainer(_Store())
    # Queued, then run right here. Without this the queue starts a dispatcher
    # of its own, and that thread races this one for the same request context —
    # whichever gets there first takes it, and the other explains the item with
    # no measurement at all. One request, one worker, no race.
    explainer._running = True
    finding = _finding()
    explainer.explain_item(finding, force_refresh=force, facts=facts)
    explainer._explain(finding)
    return seen["prompt"]


def test_the_measurement_reaches_the_prompt(monkeypatch):
    prompt = _prompt_from_one_explain(
        monkeypatch, facts={"size_bytes": 10 ** 11, "file_count": 40_349})

    assert "40,349 files" in prompt
    assert "is NOT empty" in prompt


def test_without_a_measurement_the_prompt_says_unknown(monkeypatch):
    prompt = _prompt_from_one_explain(monkeypatch, facts={})

    assert "not measured" in prompt
    assert "Size: 0 B" not in prompt


def test_asking_again_about_a_file_does_not_raise(monkeypatch):
    """A Finding is a slots dataclass, so writing an undeclared flag onto one
    raised straight out of the "Ask again" click in the dialog."""
    prompt = _prompt_from_one_explain(monkeypatch, facts={}, force=True)

    assert prompt          # got as far as building it


def test_a_forced_re_ask_still_skips_the_cache(monkeypatch):
    from app.services import ai_explainer as ax
    monkeypatch.setattr(ax, "generate", lambda **kw: (True, "Fresh answer."))
    monkeypatch.setattr(ax, "_save_cached", lambda *a, **k: None)
    monkeypatch.setattr(ax, "_load_cached", lambda *a, **k: "Stale answer.")

    explainer = ax.AIExplainer(_Store())
    explainer._running = True          # see _prompt_from_one_explain
    explainer._run_mode = "restore"
    finding = _finding()
    explainer.explain_item(finding, force_refresh=True)
    explainer._explain(finding)

    assert finding.ai_explanation == "Fresh answer."


def test_the_context_does_not_outlive_one_request(monkeypatch):
    """Or a folder measured once would answer for every later pass over it."""
    from app.services import ai_explainer as ax
    monkeypatch.setattr(ax, "generate", lambda **kw: (True, "An answer."))
    monkeypatch.setattr(ax, "_save_cached", lambda *a, **k: None)
    monkeypatch.setattr(ax, "_load_cached", lambda *a, **k: None)

    explainer = ax.AIExplainer(_Store())
    explainer._running = True          # see _prompt_from_one_explain
    finding = _finding()
    explainer.explain_item(finding, facts={"size_bytes": 1, "file_count": 2})
    explainer._explain(finding)

    assert explainer._take_request_context(finding) == {}


def test_the_unknown_size_is_never_restated_as_a_quantity():
    """The display string by then reads "not measured", which is fine text and
    a nonsense quantity — the facts line must not repeat it as one."""
    p = build_prompt(size="0 B", **FOLDER)

    assert "holds not measured" not in p
    assert "were not counted" in p
