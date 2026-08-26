# Building Podbye

```
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
- Run the tests: `.venv\Scripts\python.exe -m pytest -q` (expect 1012 passing).
- pywin32 is optional. Without it, .lnk targets are resolved by parsing
  MS-SHLLINK directly instead of via win32com; both paths are exercised.

## Beta distribution

Zip `dist\Podbye\` and hand over the archive. There is no installer yet, and the
executable is unsigned — Windows SmartScreen will warn on first run, and
testers need "More info -> Run anyway". Code signing is the fix when you are
ready to buy a certificate.
