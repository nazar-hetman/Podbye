"""Entity accounting invariants.

Two silent bugs used to live here, pushing in opposite directions so the total
looked plausible while every individual number was wrong:

1. entity size/count were summed from `gather()` — a list capped at 1000 items —
   so any folder above the cap under-reported.
2. a folder-rooted parent measured its whole subtree while a *retained*
   sub-entity inside it (node_modules, venv, …) was also counted, double-charging
   those bytes.

These tests pin both down.
"""
import os

from app.models.finding import Finding
from app.services.entity_detector import detect_entities

MB = 1024 * 1024
_SAMPLE_CAP = 1000  # _DetectionContext.sample() default limit


def _f(path, is_dir=False, size=0, ext="", parent=""):
    return Finding(path=path, name=os.path.basename(path), is_dir=is_dir,
                   size_bytes=size, extension=ext, modified=1, accessed=1,
                   parent=parent)


def _detect(findings, root="C:/T"):
    return detect_entities(findings, root, log_fn=lambda _m: None)


def _total_bytes(findings):
    return sum(f.size_bytes for f in findings if not f.is_dir)


def test_size_is_exact_above_the_sample_cap():
    """A folder with more descendants than sample()'s cap must still report its
    true size and file count — not the first `cap` items the walk happened to
    touch."""
    n = _SAMPLE_CAP + 500
    findings = [_f("C:/T", is_dir=True, parent="C:/"),
                _f("C:/T/BigApp", is_dir=True, parent="C:/T")]
    findings += [_f(f"C:/T/BigApp/f{i}.dat", size=MB, ext=".dat",
                    parent="C:/T/BigApp") for i in range(n)]

    entities = _detect(findings)
    assert sum(e.size_bytes for e in entities) == n * MB
    big = next(e for e in entities if e.name == "BigApp")
    assert big.size_bytes == n * MB
    assert big.file_count == n


def test_retained_subentity_is_not_double_counted():
    """node_modules is deliberately retained inside an app, so its bytes must be
    charged to it and removed from the parent — never counted twice."""
    findings = [
        _f("C:/T", is_dir=True, parent="C:/"),
        _f("C:/T/Blender", is_dir=True, parent="C:/T"),
        _f("C:/T/Blender/blender.exe", size=2 * MB, ext=".exe", parent="C:/T/Blender"),
        _f("C:/T/Blender/node_modules", is_dir=True, parent="C:/T/Blender"),
    ]
    findings += [_f(f"C:/T/Blender/node_modules/x{i}.js", size=MB, ext=".js",
                    parent="C:/T/Blender/node_modules") for i in range(50)]

    entities = _detect(findings)
    assert sum(e.size_bytes for e in entities) == _total_bytes(findings) == 52 * MB

    by_type = {e.entity_type: e for e in entities}
    assert by_type["node_modules"].size_bytes == 50 * MB
    # Parent keeps only its own bytes.
    assert by_type["portable_app"].size_bytes == 2 * MB


def test_nested_entities_sum_back_to_the_parent_subtree():
    """A ⊃ B ⊃ C: each byte charged exactly once, so the parts sum to the whole."""
    findings = [
        _f("C:/T", is_dir=True, parent="C:/"),
        _f("C:/T/App", is_dir=True, parent="C:/T"),
        _f("C:/T/App/blender.exe", size=1 * MB, ext=".exe", parent="C:/T/App"),
        _f("C:/T/App/node_modules", is_dir=True, parent="C:/T/App"),
        _f("C:/T/App/node_modules/a.js", size=4 * MB, ext=".js",
           parent="C:/T/App/node_modules"),
        _f("C:/T/App/node_modules/.venv", is_dir=True, parent="C:/T/App/node_modules"),
        _f("C:/T/App/node_modules/.venv/p.pyc", size=8 * MB, ext=".pyc",
           parent="C:/T/App/node_modules/.venv"),
    ]
    entities = _detect(findings)
    assert sum(e.size_bytes for e in entities) == _total_bytes(findings) == 13 * MB
    assert all(e.size_bytes >= 0 for e in entities)
    assert all(e.folder_count >= 0 for e in entities)


def test_entities_never_exceed_the_bytes_actually_scanned():
    """The headline invariant: the storage map can never claim more bytes than
    the scan found. (It may claim fewer — low-value entities are suppressed.)"""
    findings = [_f("C:/T", is_dir=True, parent="C:/")]
    for folder, count, size in (("Games", 1200, 3 * MB),
                                ("Photos", 40, 5 * MB),
                                ("Cache", 900, 1 * MB)):
        findings.append(_f(f"C:/T/{folder}", is_dir=True, parent="C:/T"))
        ext = ".jpg" if folder == "Photos" else ".dat"
        findings += [_f(f"C:/T/{folder}/f{i}{ext}", size=size, ext=ext,
                        parent=f"C:/T/{folder}") for i in range(count)]

    entities = _detect(findings)
    assert sum(e.size_bytes for e in entities) <= _total_bytes(findings)


def test_sample_is_capped_but_sizes_are_not():
    """sample() stays bounded (it feeds previews/classification), while the
    aggregate it no longer feeds is exact."""
    from app.services.entity_detector import _DetectionContext

    n = _SAMPLE_CAP + 200
    findings = [_f("C:/T", is_dir=True, parent="C:/"),
                _f("C:/T/D", is_dir=True, parent="C:/T")]
    findings += [_f(f"C:/T/D/f{i}.bin", size=MB, ext=".bin", parent="C:/T/D")
                 for i in range(n)]

    ctx = _DetectionContext(findings, "C:/T", log_fn=lambda _m: None,
                            progress_fn=lambda *a, **k: None,
                            entity_fn=lambda _e: None)
    assert len(ctx.sample("c:/t/d")) == _SAMPLE_CAP      # bounded
    size, files, _folders, _m, _a = ctx.subtree("c:/t/d")
    assert size == n * MB and files == n                 # exact


def test_same_named_entities_are_disambiguated():
    from app.models.smart_entity import SmartEntity
    from app.services.entity_detector import _disambiguate_names

    def _e(path, name):
        return SmartEntity(path=path, name=name, entity_type="application",
                           file_count=1, size_bytes=1024)

    ents = [_e("C:/Qt/6.5.0", "Qt"), _e("C:/Qt/5.15.2", "Qt"), _e("C:/ffmpeg", "FFmpeg")]
    _disambiguate_names(ents)
    names = {e.path: e.name for e in ents}
    assert names["C:/Qt/6.5.0"] == "Qt (6.5.0)"
    assert names["C:/Qt/5.15.2"] == "Qt (5.15.2)"
    assert names["C:/ffmpeg"] == "FFmpeg"   # unique name left alone
