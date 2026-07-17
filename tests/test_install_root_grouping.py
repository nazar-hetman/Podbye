"""A registered install root stays one entity instead of fragmenting.

Regression for the reported C:/Qt case: C:/Qt has one registry entry ("The Qt
Company Ltd") covering the whole tree, but its diverse children (6.11.1, Tools,
Docs, Examples) made the heterogeneous-root exploder claim the root as a
pass-through, so each child fragmented into its own "Qt (…)" application.
"""
import os

import pytest

from app.services import entity_detector as ed
from app.models.finding import Finding

MB = 1024 * 1024


def _f(path, is_dir=False, size=0, ext="", parent=""):
    return Finding(path=path, name=os.path.basename(path), is_dir=is_dir,
                   size_bytes=size, extension=ext, modified=1, accessed=1,
                   parent=parent)


@pytest.fixture
def fake_qt_registry(monkeypatch):
    """C:/Qt registered as one installed app, like the real Qt installer."""
    reg = {
        "c:/qt": {
            "name": "Qt",
            "publisher": "The Qt Company Ltd",
            "version": "6.11.1",
            "install_date": "",
            "uninstall_string": "C:/Qt/MaintenanceTool.exe",
        }
    }
    monkeypatch.setattr(ed, "_get_installed_programs", lambda *a, **k: reg)
    return reg


def _qt_tree():
    """C:/Qt with content-diverse children — the shape that used to explode."""
    f = [_f("C:/Qt", is_dir=True, parent="C:/")]
    # a versioned build dir (binaries)
    f += [_f("C:/Qt/6.11.1", is_dir=True, parent="C:/Qt")]
    f += [_f(f"C:/Qt/6.11.1/bin/lib{i}.dll", size=50 * MB, ext=".dll",
             parent="C:/Qt/6.11.1/bin") for i in range(4)]
    f += [_f("C:/Qt/6.11.1/bin", is_dir=True, parent="C:/Qt/6.11.1")]
    # tools
    f += [_f("C:/Qt/Tools", is_dir=True, parent="C:/Qt")]
    f += [_f(f"C:/Qt/Tools/t{i}.exe", size=30 * MB, ext=".exe",
             parent="C:/Qt/Tools") for i in range(3)]
    # docs (different content type -> heterogeneity)
    f += [_f("C:/Qt/Docs", is_dir=True, parent="C:/Qt")]
    f += [_f(f"C:/Qt/Docs/d{i}.html", size=1 * MB, ext=".html",
             parent="C:/Qt/Docs") for i in range(5)]
    # examples (source)
    f += [_f("C:/Qt/Examples", is_dir=True, parent="C:/Qt")]
    f += [_f(f"C:/Qt/Examples/e{i}.cpp", size=1 * MB, ext=".cpp",
             parent="C:/Qt/Examples") for i in range(5)]
    return f


def test_registered_install_root_stays_single_entity(fake_qt_registry):
    findings = _qt_tree()
    entities = ed.detect_entities(findings, "C:/", log_fn=lambda _m: None)

    qt_roots = [e for e in entities
                if e.path.replace("\\", "/").lower() == "c:/qt"]
    assert len(qt_roots) == 1, f"C:/Qt fragmented into {len(entities)} entities"
    assert qt_roots[0].entity_type == "application"
    assert qt_roots[0].name == "Qt"

    # Nothing under C:/Qt should be a separate top-level entity.
    strays = [e for e in entities
              if e.path.replace("\\", "/").lower().startswith("c:/qt/")]
    assert not strays, f"stray sub-entities under C:/Qt: {[e.path for e in strays]}"


def test_install_root_size_is_not_double_counted(fake_qt_registry):
    findings = _qt_tree()
    entities = ed.detect_entities(findings, "C:/", log_fn=lambda _m: None)
    file_bytes = sum(f.size_bytes for f in findings if not f.is_dir)
    entity_bytes = sum(e.size_bytes for e in entities)
    assert entity_bytes <= file_bytes
    qt = next(e for e in entities if e.path.replace("\\", "/").lower() == "c:/qt")
    assert qt.size_bytes == file_bytes  # the whole tree, once
