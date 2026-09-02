"""Ask AI on a file has to work on a session the user reopened.

It looked the path up in the scan's findings list and gave up when it was
missing — which is *every* file on a restored session, because a large scan
deliberately does not persist its 1.8M raw findings. Measured on a real
1,258-entity session: 79 of 79 buckets answered "This file is not part of the
current scan results". The Files tab also lists files from a live folder
listing, which were never findings at all.
"""
import os

import pytest
from PySide6.QtCore import QObject, Signal

from app.screens.findings_dashboard import _finding_for_path


def test_a_file_on_disk_yields_something_to_ask_about(tmp_path):
    f = tmp_path / "AuraProcess.ini"
    f.write_bytes(b"x" * 4096)
    finding = _finding_for_path(str(f))
    assert finding is not None
    assert finding.name == "AuraProcess.ini"
    assert finding.size_bytes == 4096
    assert finding.extension == ".ini"
    assert finding.is_dir is False
    assert finding.modified > 0


def test_the_prompt_fields_are_all_filled_in(tmp_path):
    """A bare Finding would ask the model about a nameless, sizeless thing."""
    f = tmp_path / "notes.txt"
    f.write_bytes(b"hello")
    finding = _finding_for_path(str(f))
    assert finding.size, "no human-readable size"
    assert finding.age, "no age"
    assert finding.category, "no category — the prompt says what kind of thing it is"
    assert finding.parent == str(tmp_path)


def test_a_folder_is_reported_as_one(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    assert _finding_for_path(str(sub)).is_dir is True


@pytest.mark.parametrize("path", ["", None, "C:/definitely/not/here.bin"])
def test_nothing_on_disk_yields_nothing(path):
    assert _finding_for_path(path) is None


def test_ask_ai_on_a_restored_session_still_opens(qapp, tmp_path, monkeypatch):
    """The regression itself: no findings in the model, file on disk."""
    from app.state.scan_state import ScanState
    from app.screens.findings_dashboard import CategoryDetailView
    import app.widgets.ask_ai_dialog as dialog_mod

    target = tmp_path / "leftover.log"
    target.write_bytes(b"x" * 128)

    asked = {}

    class _StubDialog(QObject):
        # A QObject with the real signal: the view connects
        # ai_settings_requested through to its own before exec(), so a plain
        # object stops standing in for the dialog the moment that exists.
        ai_settings_requested = Signal()

        def __init__(self, item, explainer, parent=None, facts=None):
            super().__init__()
            asked["item"] = item
            asked["facts"] = facts

        def exec(self):
            return 0

    monkeypatch.setattr(dialog_mod, "AskAIDialog", _StubDialog)

    class _Explainer:
        _session_id = ""

    state = ScanState()          # nothing restored: no findings at all
    state._ai_explainer = _Explainer()
    monkeypatch.setattr(type(state), "ai_explainer",
                        property(lambda self: self._ai_explainer), raising=False)

    view = CategoryDetailView()
    view.set_scan_state(state)
    try:
        view._on_ask_ai_file(str(target))
        assert "item" in asked, "the dialog never opened — the old guard tripped"
        assert asked["item"].path == str(target)
    finally:
        view.close()
        view.deleteLater()
        qapp.processEvents()
