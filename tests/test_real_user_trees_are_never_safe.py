"""Real user folders that Podbye called Safe on nothing but a name.

Every case below was produced by running the actual classifier while reviewing
Podbye for 1.0. Each one came back **Safe** - "generally safe to regenerate",
"safe to move to the Recycle Bin" - on the strength of a folder name, or of a
few letters somewhere in a path:

* ``D:\\Temp`` holding a tax return, a passport scan and a family video;
* ``ClientX\\out`` holding a finished 2 GB client video;
* ``Blog``, ``Product Catalog`` and ``Film Dialogues`` read as log folders,
  because "log" appears inside the word;
* every file under ``Documents\\Templates`` or ``Photos\\Contemporary Art``,
  because "temp" appears inside the word.

Safe is the one verdict that tells a person they need not look. It must be
earned by evidence: a known application cache location, a folder that is
structurally part of a software project, content that is actually cache or
log data. A name alone never licenses it for a person's own files.

The second half is the other side of the same rule: the cases that *do* have
that evidence stay Safe, so fixing the first half cannot be done by making
nothing Safe.
"""
import os

import pytest

from app.models.finding import Finding
from app.services.entity_detector import detect_entities

MB = 1024 * 1024
U = "C:/Users/ann"


def _f(path, size=0, is_dir=False):
    return Finding(path=path, name=os.path.basename(path), is_dir=is_dir,
                   size_bytes=size,
                   extension="" if is_dir else os.path.splitext(path)[1],
                   modified=0, accessed=0, parent=os.path.dirname(path))


def _tree(root, files):
    dirs = set()
    for path, _size in files:
        d = os.path.dirname(path)
        while d.startswith(root) and d != root:
            dirs.add(d)
            d = os.path.dirname(d)
    return ([_f(root, is_dir=True)]
            + [_f(d, is_dir=True) for d in sorted(dirs)]
            + [_f(p, s) for p, s in files])


def _entities(root, files):
    return detect_entities(_tree(root, files), root, log_fn=lambda _m: None)


def _covering(entities, path):
    """Every entity whose folder is *path* or contains it."""
    p = path.lower()
    return [e for e in entities
            if p == e.path.lower() or p.startswith(e.path.lower().rstrip("/") + "/")]


# ── folders: never Safe on a name ─────────────────────────────────
#
# (scan root, files, a user file that must not end up inside a Safe row)

USER_TREES = {
    "D:/Temp with a tax return, passport scan and family video": (
        "D:/", [("D:/Temp/tax_return_2024.pdf", 3 * MB),
                ("D:/Temp/passport_scan.jpg", 4 * MB),
                ("D:/Temp/family.mp4", 900 * MB)],
        "D:/Temp/passport_scan.jpg"),
    "a lowercase D:/tmp of signed scans": (
        "D:/", [("D:/tmp/contract_signed.pdf", 2 * MB),
                ("D:/tmp/id_card.jpg", 3 * MB)],
        "D:/tmp/id_card.jpg"),
    "a Temp folder seven levels deep holding a novel": (
        "D:/", [("D:/a/b/c/d/e/Temp/novel_draft.docx", 5 * MB)],
        "D:/a/b/c/d/e/Temp/novel_draft.docx"),
    "a client's finished video in 'out'": (
        "D:/Work", [("D:/Work/ClientX/out/ad_final.mp4", 2000 * MB),
                    ("D:/Work/ClientX/brief.pdf", 2 * MB)],
        "D:/Work/ClientX/out/ad_final.mp4"),
    "a thesis 'build' folder holding the final PDF": (
        "D:/Uni", [("D:/Uni/Thesis/build/thesis_final.pdf", 30 * MB),
                   ("D:/Uni/Thesis/build/figures.docx", 20 * MB),
                   ("D:/Uni/Thesis/main.tex", 1 * MB)],
        "D:/Uni/Thesis/build/thesis_final.pdf"),
    "a personal journal kept in 'logs'": (
        "D:/Personal", [("D:/Personal/logs/2024-journal.docx", 8 * MB),
                        ("D:/Personal/logs/2025-journal.docx", 9 * MB)],
        "D:/Personal/logs/2024-journal.docx"),
    "game saves kept in a folder called 'cache'": (
        "D:/Games", [("D:/Games/MyMods/cache/profile.sav", 20 * MB),
                     ("D:/Games/MyMods/cache/world.sav", 200 * MB)],
        "D:/Games/MyMods/cache/world.sav"),
    "a venv with no pyvenv.cfg": (
        "D:/Code", [("D:/Code/tool/venv/Lib/torch.dll", 900 * MB),
                    ("D:/Code/tool/run.py", 2000)],
        "D:/Code/tool/venv/Lib/torch.dll"),
    "a downloaded offline map in 'Garmin Map Cache'": (
        "D:/Data", [("D:/Data/Garmin Map Cache/europe.img", 3000 * MB)],
        "D:/Data/Garmin Map Cache/europe.img"),
    "photo exports in 'Cached exports'": (
        "D:/Stuff", [("D:/Stuff/Cached exports/album.zip", 900 * MB),
                     ("D:/Stuff/Cached exports/IMG_1.jpg", 8 * MB)],
        "D:/Stuff/Cached exports/album.zip"),
    "a Blog of posts in yearly folders": (
        "D:/Writing", [("D:/Writing/Blog/2024/post1.docx", 3 * MB),
                       ("D:/Writing/Blog/2025/post2.docx", 3 * MB)],
        "D:/Writing/Blog/2024/post1.docx"),
    "a Product Catalog of PDFs": (
        "D:/Work", [("D:/Work/Product Catalog/2025/catalog.pdf", 50 * MB),
                    ("D:/Work/Product Catalog/2024/catalog.pdf", 50 * MB)],
        "D:/Work/Product Catalog/2025/catalog.pdf"),
    "recorded Film Dialogues": (
        "D:/Media", [("D:/Media/Film Dialogues/ep1/take1.wav", 200 * MB),
                     ("D:/Media/Film Dialogues/ep2/take1.wav", 200 * MB)],
        "D:/Media/Film Dialogues/ep1/take1.wav"),
    "press photos in 'Release'": (
        "D:/Press", [("D:/Press/Release/2025/photo1.jpg", 10 * MB),
                     ("D:/Press/Release/2025/photo2.jpg", 10 * MB)],
        "D:/Press/Release/2025/photo1.jpg"),
    "3D renders in 'Output'": (
        "D:/3D", [("D:/3D/Output/scene1/frame001.png", 40 * MB),
                  ("D:/3D/Output/scene1/final.mp4", 900 * MB)],
        "D:/3D/Output/scene1/final.mp4"),
    "family tax papers in 'Public'": (
        "D:/Family", [("D:/Family/Public/taxes/2024.pdf", 5 * MB),
                      ("D:/Family/Public/taxes/2023.pdf", 5 * MB)],
        "D:/Family/Public/taxes/2024.pdf"),
    "a wedding video in Videos/.../out": (
        "D:/Videos", [("D:/Videos/Wedding/out/final_cut.mp4", 4000 * MB),
                      ("D:/Videos/Wedding/raw.mp4", 9000 * MB)],
        "D:/Videos/Wedding/out/final_cut.mp4"),
    "database dumps kept as backups": (
        "D:/Backups", [("D:/Backups/dumps/shop_2025-01.sql", 800 * MB),
                       ("D:/Backups/dumps/shop_2025-02.sql", 820 * MB)],
        "D:/Backups/dumps/shop_2025-01.sql"),
}


@pytest.mark.parametrize("case", sorted(USER_TREES))
def test_a_user_folder_is_never_safe_on_a_name(case):
    root, files, user_file = USER_TREES[case]
    entities = _entities(root, files)

    safe = [e for e in _covering(entities, user_file) if e.risk == "Safe"]

    assert not safe, (
        f"{case}: {user_file} sits inside a Safe row: "
        + "; ".join(f"{e.path} ({e.entity_type}: {e.risk_reason})" for e in safe))


# ── files: a word inside a path is not evidence ───────────────────

USER_FILES = [
    "C:/Users/ann/Documents/Templates/contract.docx",
    "D:/Photos/Contemporary Art/IMG_1.jpg",
    "D:/Work/Attempts/final_report.pdf",
    "D:/Music/Tempo Mixes/set1.flac",
    "C:/Users/ann/Desktop/Temperature study/data.xlsx",
    "D:/Backups/phone/cached_contacts.vcf",
    "D:/Knowledge/edge cases/cache-notes.docx",
    "D:/Temp/passport_scan.jpg",
    "D:/tmp/contract_signed.pdf",
]


@pytest.mark.parametrize("path", USER_FILES)
def test_a_user_file_is_never_safe_because_of_letters_in_its_path(path):
    finding = _f(path, 5 * MB)
    assert finding.risk != "Safe", (
        f"{path} -> {finding.category} / {finding.risk}: {finding.risk_reason}")


@pytest.mark.parametrize("path", [
    "D:/Uni/Thesis/build", "D:/Personal/logs", "D:/Temp", "D:/Work/ClientX/out",
])
def test_a_user_folder_row_in_all_files_mode_is_never_safe(path):
    """All-files mode lists folders as rows too; recycling one takes it whole."""
    finding = _f(path, is_dir=True)
    assert finding.risk != "Safe", (
        f"{path} -> {finding.category} / {finding.risk}: {finding.risk_reason}")


# ── the other side: evidence still earns Safe ─────────────────────

EVIDENCED = {
    "a Rust target next to Cargo.toml": (
        "D:/Code", [("D:/Code/app/Cargo.toml", 1000),
                    ("D:/Code/app/src/main.rs", 5000),
                    ("D:/Code/app/target/debug/app.exe", 40 * MB),
                    ("D:/Code/app/target/debug/deps/x.rlib", 200 * MB)],
        "D:/Code/app/target", "build_folder"),
    "a web build next to package.json, images and all": (
        "D:/Code", [("D:/Code/site/package.json", 1000),
                    ("D:/Code/site/src/app.js", 1000),
                    ("D:/Code/site/dist/assets/hero.jpg", 8 * MB),
                    ("D:/Code/site/dist/index.js", 6 * MB)],
        "D:/Code/site/dist", "build_folder"),
    "node_modules": (
        "D:/Code", [("D:/Code/web/package.json", 1000),
                    ("D:/Code/web/node_modules/react/index.js", 30 * MB),
                    ("D:/Code/web/node_modules/react/package.json", 1000)],
        "D:/Code/web/node_modules", "node_modules"),
    "a venv that has its pyvenv.cfg": (
        "D:/Code", [("D:/Code/tool/pyproject.toml", 100),
                    ("D:/Code/tool/.venv/pyvenv.cfg", 100),
                    ("D:/Code/tool/.venv/Lib/site-packages/torch/x.dll", 900 * MB)],
        "D:/Code/tool/.venv", "venv"),
    "__pycache__ inside a project": (
        "D:/Code", [("D:/Code/tool/pyproject.toml", 100),
                    ("D:/Code/tool/pkg/__pycache__/m.cpython-312.pyc", 10 * MB),
                    ("D:/Code/tool/pkg/m.py", 1000)],
        "D:/Code/tool/pkg/__pycache__", "cache_folder"),
    "Chrome's own cache": (
        "C:/", [(U + "/AppData/Local/Google/Chrome/User Data/Default/Cache/Cache_Data/f_000001",
                 300 * MB)],
        U + "/AppData/Local/Google/Chrome/User Data/Default/Cache", "cache_folder"),
    "the npm cache": (
        "C:/", [(U + "/AppData/Roaming/npm-cache/_cacache/content-v2/sha512/ab/cd", 300 * MB)],
        U + "/AppData/Roaming/npm-cache", "cache_folder"),
    "Adobe's media cache": (
        "C:/", [(U + "/AppData/Roaming/Adobe/Common/Media Cache Files/clip.cfa", 900 * MB)],
        U + "/AppData/Roaming/Adobe/Common/Media Cache Files", "cache_folder"),
    "the DirectX shader cache": (
        "C:/", [(U + "/AppData/Local/D3DSCache/abc/x.idx", 90 * MB)],
        U + "/AppData/Local/D3DSCache", "shader_cache"),
    "Steam's shader cache": (
        "D:/", [("D:/SteamLibrary/steamapps/shadercache/570/fozpipelinesv6/x.foz", 90 * MB),
                ("D:/SteamLibrary/steamapps/common/Dota 2/game.exe", 100 * MB)],
        "D:/SteamLibrary/steamapps/shadercache", "shader_cache"),
}


@pytest.mark.parametrize("case", sorted(EVIDENCED))
def test_evidence_still_earns_safe(case):
    root, files, path, etype = EVIDENCED[case]
    entities = _entities(root, files)
    match = [e for e in entities if e.path.lower() == path.lower()]

    assert match, f"{case}: no row for {path}: {[e.path for e in entities]}"
    assert match[0].risk == "Safe", (
        f"{case}: {match[0].entity_type} / {match[0].risk}: {match[0].risk_reason}")
    assert match[0].entity_type == etype


@pytest.mark.parametrize("path", [
    U + "/AppData/Local/Temp/abc.tmp",
    U + "/AppData/Local/Google/Chrome/User Data/Default/Cache/Cache_Data/f_000001",
    "D:/Code/tool/pkg/__pycache__/m.cpython-312.pyc",
    "D:/Server/app.log",
])
def test_evidenced_files_stay_safe(path):
    finding = _f(path, 1 * MB)
    assert finding.risk == "Safe", (
        f"{path} -> {finding.category} / {finding.risk}: {finding.risk_reason}")
