"""Helpers to build synthetic Finding trees for classifier tests.

These let tests describe a directory layout in-memory without touching the
real filesystem, so detect_entities() can be exercised deterministically.
"""
from __future__ import annotations

import os
import time

from app.models.finding import Finding

_NOW = time.time()


def _norm(path: str) -> str:
    return path.replace("\\", "/").rstrip("/")


def mkdir(path: str, mtime: float | None = None) -> Finding:
    """Build a directory Finding at `path`."""
    p = _norm(path)
    name = p.rsplit("/", 1)[-1]
    parent = p.rsplit("/", 1)[0] if "/" in p else ""
    return Finding(
        path=p, name=name, is_dir=True, size_bytes=0, extension="",
        modified=mtime or _NOW, accessed=mtime or _NOW, parent=parent,
    )


def mkfile(path: str, size_bytes: int = 4096, mtime: float | None = None) -> Finding:
    """Build a file Finding at `path` (extension inferred from the name)."""
    p = _norm(path)
    name = p.rsplit("/", 1)[-1]
    parent = p.rsplit("/", 1)[0] if "/" in p else ""
    return Finding(
        path=p, name=name, is_dir=False, size_bytes=size_bytes,
        extension=os.path.splitext(name)[1].lower(),
        modified=mtime or _NOW, accessed=mtime or _NOW, parent=parent,
    )


def rich_tree(root: str = "T:/snap") -> list[Finding]:
    """A synthetic tree exercising most detection passes — used as a
    characterization fixture for the detect_entities() refactor."""
    f: list[Finding] = []
    # Monolith distribution (Phase 1 discovery)
    f += [mkdir(f"{root}/ffmpeg"), mkdir(f"{root}/ffmpeg/bin"),
          mkfile(f"{root}/ffmpeg/bin/ffmpeg.exe", 8_000_000),
          mkfile(f"{root}/ffmpeg/README.txt", 2_000)]
    # node_modules (Pass 1, always-standalone)
    f += [mkdir(f"{root}/node_modules"),
          mkfile(f"{root}/node_modules/index.js", 1_500),
          mkfile(f"{root}/node_modules/dep.js", 1_500)]
    # Python virtualenv (Pass 1)
    f += [mkdir(f"{root}/.venv"), mkdir(f"{root}/.venv/Scripts"),
          mkfile(f"{root}/.venv/Scripts/python.exe", 50_000),
          mkfile(f"{root}/.venv/pyvenv.cfg", 200)]
    # Photo collection (Pass 6 content classification)
    f += [mkdir(f"{root}/Vacation2024"),
          mkfile(f"{root}/Vacation2024/p1.jpg", 300_000),
          mkfile(f"{root}/Vacation2024/p2.jpg", 320_000),
          mkfile(f"{root}/Vacation2024/p3.png", 280_000)]
    # Document collection (Pass 6 content classification)
    f += [mkdir(f"{root}/Reports"),
          mkfile(f"{root}/Reports/q1.pdf", 90_000),
          mkfile(f"{root}/Reports/q2.docx", 70_000)]
    # Cache + logs (Pass 1 / Pass 4)
    f += [mkdir(f"{root}/cache"),
          mkfile(f"{root}/cache/a.tmp", 4_000),
          mkfile(f"{root}/cache/b.tmp", 4_000)]
    f += [mkdir(f"{root}/logs"), mkfile(f"{root}/logs/app.log", 12_000)]
    # Steam game (Pass 3b)
    f += [mkdir(f"{root}/Games"), mkdir(f"{root}/Games/steamapps"),
          mkdir(f"{root}/Games/steamapps/common"),
          mkdir(f"{root}/Games/steamapps/common/CoolGame"),
          mkfile(f"{root}/Games/steamapps/common/CoolGame/game.exe", 5_000_000),
          mkfile(f"{root}/Games/steamapps/common/CoolGame/data.pak", 9_000_000)]
    # Unclassifiable folder (Pass 7 sweep)
    f += [mkdir(f"{root}/oddments"),
          mkfile(f"{root}/oddments/thing.zzz", 1_000),
          mkfile(f"{root}/oddments/blob.qqq", 1_000)]
    return f
