# Third-party notices

Podbye bundles the components below. Each is used under its own license, which
applies to that component regardless of the license covering Podbye's own code
(see `LICENSE`). Full license texts are in `licenses/`.

---

## Qt for Python (PySide6) — GNU LGPL v3

| | |
|---|---|
| Version | 6.11.1 |
| Copyright | The Qt Company Ltd. and contributors |
| License | `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` — Podbye uses it under **LGPL-3.0** |
| Home | <https://www.qt.io/qt-for-python> |

Qt is the one dependency with obligations that shape how Podbye is *built and
shipped*, not just what a notice file says. Using it under the LGPL (rather
than buying a commercial Qt license) means:

1. **Say that Qt is used and is LGPL'd.** This file and the About screen do that.
2. **Ship the license text.** `licenses/` must contain the LGPL v3 and GPL v3
   texts in every distributed build.
3. **The user must be able to replace Qt with their own build.** This is
   LGPL v3 §4(d), and it is the requirement most desktop Python projects get
   wrong. A PyInstaller **one-file** executable packs the Qt DLLs inside the
   `.exe` and unpacks them to a temporary folder at run time — the recipient
   cannot swap in a modified Qt, so that layout does not satisfy §4(d).
   **Podbye therefore ships as a one-folder build**, with the Qt DLLs sitting
   next to the executable as ordinary replaceable files.
4. **No anti-relinking measures.** Don't sign, encrypt or checksum the Qt
   binaries in a way that stops them being replaced.

> Podbye's own noncommercial terms do **not** apply to Qt. Anyone who receives a
> Podbye build receives the LGPL rights to the Qt parts, and nothing in Podbye's
> license restricts them.

---

## psutil — BSD 3-Clause

| | |
|---|---|
| Version | 7.2.2 |
| Copyright | Jay Loden, Dave Daeschler, Giampaolo Rodolà |
| License | BSD 3-Clause — `licenses/psutil-BSD-3-Clause.txt` |
| Home | <https://github.com/giampaolo/psutil> |

Requires the copyright notice, the license text and the disclaimer to travel
with any distribution, source or binary. No other obligations.

---

## Bundled fonts — SIL Open Font License 1.1

All three are under the OFL — `licenses/SIL-Open-Font-License-1.1.txt`.

| Font | Copyright | Reserved Font Name |
|---|---|---|
| Inter | Copyright (c) 2016 The Inter Project Authors — <https://github.com/rsms/inter> | "Inter" |
| JetBrains Mono | Copyright (c) 2020 The JetBrains Mono Project Authors — <https://github.com/JetBrains/JetBrainsMono> | "JetBrains Mono" |
| Silkscreen | Copyright (c) 2001 Jason Kottke — <https://kottke.org/plus/type/silkscreen/> | "Silkscreen" |

The OFL explicitly permits bundling fonts with an application and redistributing
them, including in a commercial product. Two conditions matter here:

- The fonts may not be sold **on their own** (bundling inside Podbye is fine).
- If a font is *modified*, the modified version must not use the Reserved Font
  Name. Podbye ships them unmodified.

---

## Development-only dependencies

Not distributed in a build, listed for completeness: **pytest** (MIT),
**PyInstaller** (GPL v2 with a linking exception that explicitly permits
shipping proprietary or otherwise-licensed applications built with it).

---

## Checking this file is still true

`requirements.txt` is the source of truth for what ships. When a dependency is
added or upgraded, confirm its license here before releasing:

```
pip install pip-licenses
pip-licenses --with-urls --with-license-file --format=markdown
```
