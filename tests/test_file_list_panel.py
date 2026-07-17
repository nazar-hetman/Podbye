"""Tests for the inspector's expandable per-file list (grouped/loose entities).

Skips automatically if a Qt application cannot be created in the environment.
"""
import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # pragma: no cover
        pytest.skip("PySide6 not available")
    app = QApplication.instance() or QApplication([])
    return app


def _panel(qapp, recycle_into=None):
    from app.screens.findings_dashboard import _PreallocDetailPanel
    return _PreallocDetailPanel(
        open_cb=lambda p: None,
        copy_cb=lambda p: None,
        recycle_cb=(recycle_into.update if recycle_into is not None else None),
    )


def test_loose_group_lists_its_files(qapp):
    panel = _panel(qapp)
    ent = {
        "path": "C:/", "name": "Loose archives", "risk": "Optional",
        "entity_type": "archive_group", "actionability": "recycle", "size": "1 GB",
        "removable_file_paths": ["C:/old/a.zip", "C:/b.7z", "C:/c.rar"],
    }
    panel.populate(ent)
    listed = [p for _cb, p in panel._file_checks]
    assert listed == ["C:/old/a.zip", "C:/b.7z", "C:/c.rar"]


def test_recycle_selected_files_targets_only_checked(qapp):
    captured: dict = {}
    panel = _panel(qapp, recycle_into=captured)
    ent = {
        "path": "C:/", "name": "Loose archives", "risk": "Optional",
        "entity_type": "archive_group", "actionability": "recycle",
        "removable_file_paths": ["C:/a.zip", "C:/b.7z", "C:/c.rar"],
    }
    panel.populate(ent)
    panel._file_checks[0][0].setChecked(True)
    panel._file_checks[2][0].setChecked(True)
    panel._on_recycle_files()
    assert captured.get("removable_file_paths") == ["C:/a.zip", "C:/c.rar"]


def test_application_entity_has_no_file_list(qapp):
    panel = _panel(qapp)
    ent = {
        "path": "C:/Program Files/App", "name": "App", "risk": "Review",
        "entity_type": "application", "category": "Applications",
        "removable_file_paths": [],
    }
    panel.populate(ent)
    assert panel._file_checks == []


def test_single_file_group_does_not_expand(qapp):
    panel = _panel(qapp)
    ent = {
        "path": "C:/Downloads/setup.exe", "name": "Installer (Setup)",
        "risk": "Optional", "entity_type": "installer", "actionability": "recycle",
        "removable_file_paths": ["C:/Downloads/setup.exe"],
    }
    panel.populate(ent)
    # One file adds no insight — the Files tab stays disabled.
    assert panel._file_checks == []
    assert panel._tabs.isTabEnabled(1) is False


def test_pagination_and_cross_page_selection(qapp):
    panel = _panel(qapp)
    paths = [f"C:/Photos/img_{i:03d}.jpg" for i in range(150)]
    ent = {
        "path": "C:/Photos", "name": "Photos", "risk": "Review",
        "entity_type": "photo_collection", "actionability": "review_only",
        "removable_file_paths": paths,
    }
    panel.populate(ent)
    assert panel._tabs.isTabEnabled(1) is True
    assert panel._file_page_count() == 3
    assert len(panel._file_checks) == 50          # one page at a time

    # Select 20 on page 1, then 10 on page 2 → counter persists at 30.
    for cb, _p in panel._file_checks[:20]:
        cb.setChecked(True)
    panel._files_change_page(1)
    for cb, _p in panel._file_checks[:10]:
        cb.setChecked(True)
    assert len(panel._selected_files) == 30

    # Returning to page 1 restores the earlier checkboxes.
    panel._files_change_page(-1)
    assert sum(1 for cb, _ in panel._file_checks if cb.isChecked()) == 20


def test_select_page_then_recycle_all_selected(qapp):
    captured: dict = {}
    panel = _panel(qapp, recycle_into=captured)
    paths = [f"C:/x/f{i}.zip" for i in range(120)]
    ent = {
        "path": "C:/", "name": "Loose archives", "risk": "Optional",
        "entity_type": "archive_group", "actionability": "recycle",
        "removable_file_paths": paths,
    }
    panel.populate(ent)
    panel._files_select_page()                    # selects the 50 on page 1
    assert len(panel._selected_files) == 50
    panel._on_recycle_files()
    assert len(captured["removable_file_paths"]) == 50
