# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Vigil — single-file windowed Windows build.

Build:  .venv\\Scripts\\python.exe -m PyInstaller --noconfirm vigil.spec
Output: dist\\Vigil.exe
"""
import os

block_cipher = None

# Data files loaded at runtime via __file__-relative paths. Each tuple is
# (source, destination-dir-inside-bundle); destinations mirror the source tree
# so Path(__file__).parent / ... resolves the same way when frozen.
datas = [
    ("app/services/classification_rules.json", "app/services"),
    ("app/assets/logo.svg", "app/assets"),
]


def _bundle_dir(src_dir: str, suffix: str):
    """Bundle every *suffix* file in *src_dir*.

    Globbed rather than listed file-by-file so a newly added locale/theme/font
    ships automatically — a hardcoded list silently drops new files from the
    build and they only fail on someone else's machine.
    """
    for name in sorted(os.listdir(src_dir)):
        if name.lower().endswith(suffix):
            datas.append((f"{src_dir}/{name}", src_dir))


_bundle_dir("app/locales", ".json")   # translations (uk.json, …)
_bundle_dir("app/themes", ".qss")     # themes (forest, amber, mono, paper, …)
_bundle_dir("app/fonts", ".ttf")      # shipped fonts

_icon = "app/assets/vigil.ico" if os.path.exists("app/assets/vigil.ico") else None

a = Analysis(
    ["app/main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=["win32com", "win32com.client"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PySide6.QtQuick",
              "PySide6.QtQml", "PySide6.Qt3D", "PySide6.QtWebEngineCore",
              "PySide6.QtWebEngineWidgets"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Vigil",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)
