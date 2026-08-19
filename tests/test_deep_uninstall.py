"""Deep Uninstall must either work or say why it didn't.

It used to report "uninstaller launched" whenever it managed to spawn a
process, which is not the same thing. Measured on one machine with 533
registered programs: 506 need elevation that Popen cannot request, 4 have
unquoted paths with spaces that naive splitting mangles, and 19 point at an
executable that no longer exists.
"""
import os

import pytest

from app.services.uninstaller import (
    CANCELLED, ERROR_CANCELLED, FAILED, LAUNCHED, MISSING, NO_COMMAND,
    SE_ACCESS_DENIED, SE_FILE_NOT_FOUND,
    launch_uninstaller, split_uninstall_command, uninstaller_is_runnable,
)


# ── command-line splitting ────────────────────────────────────────


@pytest.mark.parametrize("command,exe,args", [
    # The ordinary quoted form.
    (r'"C:\Program Files\Git\unins001.exe" /SILENT',
     r"C:\Program Files\Git\unins001.exe", "/SILENT"),
    # Unquoted with no spaces.
    (r"MsiExec.exe /I{0F1B2C3D}", "MsiExec.exe", "/I{0F1B2C3D}"),
    # Unquoted WITH spaces — this is the one that produced "C:\Program" and
    # made four uninstallers on a real machine look like they did not exist.
    (r"C:\Program Files\CutePDF Writer\uninst.exe /S",
     r"C:\Program Files\CutePDF Writer\uninst.exe", "/S"),
    # No arguments at all.
    (r'"D:\Steam\uninstall.exe"', r"D:\Steam\uninstall.exe", ""),
    (r"C:\tools\setup.exe", r"C:\tools\setup.exe", ""),
    # Nothing to split.
    ("", "", ""),
    ("   ", "", ""),
])
def test_split_handles_every_shape_the_registry_uses(command, exe, args):
    assert split_uninstall_command(command) == (exe, args)


def test_split_does_not_break_on_an_exe_inside_a_folder_name():
    """A boundary is end-of-string or whitespace, not any occurrence."""
    cmd = r"C:\tools\setup.exe.bak\real.exe /q"
    assert split_uninstall_command(cmd) == (r"C:\tools\setup.exe.bak\real.exe", "/q")


# ── existence ─────────────────────────────────────────────────────


def test_a_missing_executable_is_not_runnable(tmp_path):
    gone = tmp_path / "nope" / "uninstall.exe"
    assert not uninstaller_is_runnable(f'"{gone}" /S')


def test_a_present_executable_is_runnable(tmp_path):
    real = tmp_path / "unins000.exe"
    real.write_text("")
    assert uninstaller_is_runnable(f'"{real}" /SILENT')


def test_msiexec_is_always_runnable():
    """It ships with Windows, so there is no file to check for."""
    assert uninstaller_is_runnable("MsiExec.exe /X{ABC-123}")


def test_an_empty_command_is_not_runnable():
    assert not uninstaller_is_runnable("")


# ── outcomes ──────────────────────────────────────────────────────


def test_no_command_is_reported_as_such():
    outcome, _msg = launch_uninstaller("")
    assert outcome == NO_COMMAND


def test_a_stale_registry_entry_is_named_not_silently_ignored(tmp_path):
    """Steam moved drives, so three games' uninstallers pointed at nothing.
    The user must be told the entry is a leftover."""
    gone = tmp_path / "gone" / "steam.exe"
    outcome, message = launch_uninstaller(f'"{gone}" uninstall')
    assert outcome == MISSING
    assert "no longer exists" in message


def test_a_started_uninstaller_reports_launched(tmp_path):
    real = tmp_path / "unins000.exe"
    real.write_text("")
    outcome, _msg = launch_uninstaller(f'"{real}" /S', _executor=lambda e, a: 42)
    assert outcome == LAUNCHED


def test_declining_the_uac_prompt_is_not_a_failure(tmp_path):
    """The user said no. That is an answer, not an error to warn about."""
    real = tmp_path / "unins000.exe"
    real.write_text("")
    outcome, message = launch_uninstaller(
        f'"{real}" /S', _executor=lambda e, a: ERROR_CANCELLED)
    assert outcome == CANCELLED
    assert "cancelled" in message.lower()


def test_access_denied_is_reported_as_a_failure(tmp_path):
    real = tmp_path / "unins000.exe"
    real.write_text("")
    outcome, message = launch_uninstaller(
        f'"{real}" /S', _executor=lambda e, a: SE_ACCESS_DENIED)
    assert outcome == FAILED
    assert "denied" in message.lower()


def test_the_executable_and_args_reach_the_executor(tmp_path):
    """Elevation is per-process: the exe must go to ShellExecute as the file
    and the switches as parameters, or the arguments are lost."""
    real = tmp_path / "unins000.exe"
    real.write_text("")
    seen = {}

    def executor(exe, args):
        seen["exe"], seen["args"] = exe, args
        return 42

    launch_uninstaller(f'"{real}" /allusers /S', _executor=executor)
    assert seen["exe"] == str(real)
    assert seen["args"] == "/allusers /S"


def test_an_executor_that_raises_is_reported_not_swallowed(tmp_path):
    real = tmp_path / "unins000.exe"
    real.write_text("")

    def boom(exe, args):
        raise OSError("no shell32 here")

    outcome, message = launch_uninstaller(f'"{real}"', _executor=boom)
    assert outcome == FAILED
    assert "no shell32 here" in message


# ── The button must not exist when it cannot work ────────────────

def _detail_widget(qapp):
    from app.fonts import load_fonts
    from app.themes.theme_manager import build_qss
    from app.screens.findings_dashboard import _PreallocDetailPanel
    load_fonts()
    qapp.setStyleSheet(build_qss("forest"))
    return _PreallocDetailPanel(
        open_cb=lambda *_: None, copy_cb=lambda *_: None,
        recycle_cb=lambda *_: None, uninstall_cb=lambda *_: None,
    )


def _app_entity(uninstall_string):
    return {
        "path": r"C:\Program Files\WSL",
        "name": "WSL",
        "entity_type": "application",
        "category": "Applications",
        "risk": "Review",
        "size_bytes": 457_000_000,
        "size": "436 MB",
        "file_count": 120,
        "uninstall_string": uninstall_string,
    }


def test_no_uninstall_button_when_windows_registers_no_uninstaller(qapp):
    """WSL sits in Program Files with no uninstall command.

    A visible-but-disabled button was reported as confusing: the explanation
    lived in a tooltip, so all the user saw was a control for something the
    app cannot do.
    """
    w = _detail_widget(qapp)
    w.populate(_app_entity(""))
    assert not w._btn_uninstall.isVisibleTo(w), (
        "Deep Uninstall is offered for an application with no uninstaller"
    )
    w.deleteLater()


def test_recycle_stays_available_as_the_fallback(qapp):
    """Hiding the dead button must not leave the user with no action at all."""
    w = _detail_widget(qapp)
    w.populate(_app_entity(""))
    assert w._btn_recycle.isVisibleTo(w), (
        "no uninstaller and no recycle — the entity has no available action"
    )
    w.deleteLater()


def test_the_button_appears_when_the_uninstaller_is_real(qapp):
    import sys
    w = _detail_widget(qapp)
    # sys.executable certainly exists, so uninstaller_is_runnable() passes.
    w.populate(_app_entity(f'"{sys.executable}" /uninstall'))
    assert w._btn_uninstall.isVisibleTo(w)
    assert w._btn_uninstall.isEnabled()
    w.deleteLater()
