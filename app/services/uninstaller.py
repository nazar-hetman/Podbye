"""Run an application's own uninstaller, and be honest about the outcome.

Deep Uninstall reported "uninstaller launched" every time it managed to spawn
a process, which is not the same thing as an uninstaller running. Three
separate reasons it silently did nothing, all measured on one real machine
with 533 registered programs:

1. **Elevation.** 506 of those 533 are HKLM entries whose uninstaller writes
   to Program Files and HKLM, so it needs admin. ``subprocess.Popen`` cannot
   elevate — ``CreateProcess`` fails with ERROR_ELEVATION_REQUIRED — and with
   ``shell=True`` the failure goes to a console nobody sees. ``ShellExecuteW``
   is the only Windows API that can raise a UAC prompt.

2. **Quoting.** A registry command may be an unquoted path containing spaces:
   ``C:\\Program Files\\CutePDF Writer\\uninst.exe /S``. Splitting on the first
   space yields ``C:\\Program``, which exists nowhere. Four entries on that
   machine were shaped like this.

3. **Stale entries.** 19 of 475 uninstall commands pointed at an executable
   that no longer exists — Steam had moved, so the uninstallers for Dota 2,
   Counter-Strike 2 and Deep Rock Galactic all pointed into thin air. Nothing
   can be launched, and the user should be told that rather than shown a
   success toast.
"""
from __future__ import annotations

import os

# ShellExecuteW returns a value <= 32 to signal failure; these are the ones
# worth naming. 1223 is ERROR_CANCELLED, which is the user declining UAC —
# not a failure of ours, and it must not be reported as one.
SE_FILE_NOT_FOUND = 2
SE_PATH_NOT_FOUND = 3
SE_ACCESS_DENIED = 5
ERROR_CANCELLED = 1223

# Outcomes, so callers can react without parsing prose.
LAUNCHED = "launched"
CANCELLED = "cancelled"
MISSING = "missing"
NO_COMMAND = "no-command"
FAILED = "failed"

_EXE_SUFFIXES = (".exe", ".msi", ".bat", ".cmd", ".com")


def split_uninstall_command(command: str) -> tuple[str, str]:
    """Split a registry uninstall string into (executable, arguments).

    Handles the quoted form, and the unquoted-path-with-spaces form that
    naive splitting mangles.
    """
    cmd = (command or "").strip()
    if not cmd:
        return "", ""

    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 0:
            return cmd[1:end], cmd[end + 1:].strip()
        return cmd.strip('"'), ""

    lowered = cmd.lower()
    for suffix in _EXE_SUFFIXES:
        start = 0
        while True:
            idx = lowered.find(suffix, start)
            if idx == -1:
                break
            end = idx + len(suffix)
            # Only a real boundary counts, so a folder called "setup.exe.bak"
            # does not split the string in the middle.
            if end == len(cmd) or cmd[end].isspace():
                return cmd[:end], cmd[end:].strip()
            start = end

    head, _, tail = cmd.partition(" ")
    return head, tail.strip()


def _is_msi(executable: str) -> bool:
    return os.path.basename(executable).lower().startswith("msiexec")


def uninstaller_is_runnable(command: str) -> bool:
    """True when the command names something that actually exists.

    A registry entry outlives the program it describes: uninstall commands for
    three Steam games pointed at a steam.exe that had moved drives. Offering
    Deep Uninstall for those promises something Vigil cannot deliver.
    """
    executable, _args = split_uninstall_command(command)
    if not executable:
        return False
    if _is_msi(executable):
        return True                     # always present on Windows
    return os.path.exists(os.path.expandvars(executable))


def describe_failure(code: int) -> str:
    """Human-readable reason for a ShellExecuteW return code."""
    from app.i18n import tr
    if code == ERROR_CANCELLED:
        return tr("You cancelled the Windows permission prompt.")
    if code in (SE_FILE_NOT_FOUND, SE_PATH_NOT_FOUND):
        return tr("Windows has an uninstaller registered for this program, but "
                  "the file it points to no longer exists. The entry is left "
                  "over from a program that was already removed or moved.")
    if code == SE_ACCESS_DENIED:
        return tr("Windows refused to start the uninstaller (access denied).")
    return tr("Windows could not start the uninstaller (error {code}).",
              code=code)


def launch_uninstaller(command: str, _executor=None) -> tuple[str, str]:
    """Run *command* elevated. Returns ``(outcome, message)``.

    ``_executor`` is injectable so the decision logic can be tested without
    actually uninstalling anything.
    """
    from app.i18n import tr

    executable, args = split_uninstall_command(command)
    if not executable:
        return NO_COMMAND, tr("No uninstaller command is registered.")

    if not uninstaller_is_runnable(command):
        return MISSING, describe_failure(SE_FILE_NOT_FOUND)

    executor = _executor or _shell_execute
    try:
        code = executor(executable, args)
    except Exception as exc:            # pragma: no cover - platform dependent
        return FAILED, tr("Could not start the uninstaller: {error}",
                          error=str(exc))

    # ERROR_CANCELLED must be tested before the success range: 1223 is greater
    # than 32, so checking "started" first reported a declined UAC prompt as a
    # successful uninstall.
    if code == ERROR_CANCELLED:
        return CANCELLED, describe_failure(code)
    if code > 32:
        return LAUNCHED, tr("Uninstaller started.")
    return FAILED, describe_failure(code)


def _shell_execute(executable: str, args: str) -> int:     # pragma: no cover
    """ShellExecuteEx with the "runas" verb — the only way to raise UAC.

    Popen/CreateProcess cannot elevate: it fails with ERROR_ELEVATION_REQUIRED
    and, under shell=True, that error is written to a console that does not
    exist. That is why Deep Uninstall appeared to do nothing.

    ShellExecuteEx rather than plain ShellExecute because the plain form
    reports a declined UAC prompt as SE_ERR_ACCESSDENIED, indistinguishable
    from Windows genuinely refusing. The Ex form sets ERROR_CANCELLED, so the
    user can be told "you cancelled" instead of "access denied".

    Returns >32 when started, otherwise a Win32/SE_ERR_* code.
    """
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_FLAG_NO_UI = 0x00000400
    SW_SHOWNORMAL = 1

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = args or None
    info.lpDirectory = os.path.dirname(executable) or None
    info.nShow = SW_SHOWNORMAL

    if ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        if info.hProcess:
            ctypes.windll.kernel32.CloseHandle(info.hProcess)
        return 33                       # any value above the success threshold
    return int(ctypes.get_last_error() or ctypes.windll.kernel32.GetLastError())
