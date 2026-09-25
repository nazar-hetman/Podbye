# Building Podbye

```
.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.venv\Scripts\python.exe -m PyInstaller --noconfirm podbye.spec
```

Output: `dist\Podbye\` — **ship the whole folder**, not just the .exe.

## Why one folder and not one file

This is a licensing constraint, not a packaging preference. Podbye links Qt
under the LGPL v3, and section 4(d) requires that whoever receives the program
can replace the LGPL'd library with their own build. A one-file executable
packs the Qt DLLs inside the .exe and unpacks them to a temp directory at run
time, so nobody can swap them — that layout does not satisfy the LGPL.

`dist\Podbye\_internal\PySide6\*.dll` are ordinary files a recipient can
replace. Keep it that way: don't re-enable one-file, and don't sign, compress
or checksum the Qt DLLs in a way that stops them being replaced.

## Before shipping a build

- `LICENSE`, `THIRD-PARTY-NOTICES.md` and `licenses\*.txt` must be present in
  `dist\Podbye\_internal\`. The spec bundles them; verify after any spec edit.
- Run the tests the way CI does, as two processes:
  `.venv\Scripts\python.exe -m pytest -q -m qt` and
  `.venv\Scripts\python.exe -m pytest -q -m "not qt"`. Do not rely on a
  historical test count; the expected suite changes as the product evolves.
- pywin32 is optional. Without it, .lnk targets are resolved by parsing
  MS-SHLLINK directly instead of via win32com; both paths are exercised.

## Beta distribution

Zip `dist\Podbye\` for the portable release. The repository also includes an
Inno Setup installer source at `installer\Podbye.iss`; build and test it for a
standard installation release. It packages `dist\Podbye\` and takes its
version from `app\version.py`, passed in at compile time:

```
iscc "/DAppVersion=$(.venv\Scripts\python.exe tools\release_version.py)" installer\Podbye.iss
```

## Versions and release tags

`app\version.py` is the only place the version is written. To release, bump
`__version__` there, then push a tag that is exactly `v` followed by that
version (for example `v1.0.0-rc.1`). The Release workflow checks this first
and stops with an explanation if the two differ; it then runs the same test
jobs as `tests.yml`, builds, packages the ZIP and the installer, and creates a
draft release (marked pre-release when the version has a `-` suffix). The executable is unsigned, so Windows
SmartScreen may warn on first run. Code signing is the long-term fix.
