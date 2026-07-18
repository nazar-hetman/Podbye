"""Browser caches are separated from irreplaceable profile data.

A browser profile is mostly things you cannot get back — passwords, cookies,
history, bookmarks — so the whole tree sits at Review and its cache goes
unnoticed inside it. Measured on a real profile: 794 MB of a 6.35 GB Chrome
folder is pure cache, 613 MB of that under Service Worker alone.
"""
import os

from app.services.entity_detector import detect_entities
from app.models.finding import Finding

MB = 1024 * 1024
UD = "C:/Users/n/AppData/Local/Google/Chrome/User Data"


def _f(path, is_dir=False, size=0, ext="", parent=""):
    return Finding(path=path, name=os.path.basename(path), is_dir=is_dir,
                   size_bytes=size, extension=ext, modified=1, accessed=1,
                   parent=parent or os.path.dirname(path).replace("\\", "/"))


def _tree():
    f = [_f(UD, is_dir=True, parent="C:/Users/n/AppData/Local/Google/Chrome"),
         _f(f"{UD}/Default", is_dir=True, parent=UD)]
    d = f"{UD}/Default"
    # Irreplaceable profile data. Real Chrome stores these as extensionless
    # SQLite files — giving them ".db" made the whole profile read as a
    # database folder, which claimed the caches before they could be split out.
    for name in ("Login Data", "Cookies", "History", "Bookmarks",
                 "Preferences", "Web Data", "Favicons", "Top Sites"):
        f.append(_f(f"{d}/{name}", size=5 * MB, parent=d))
    # regenerable caches
    for sub, n, mb in (("Cache", 6, 15), ("Code Cache", 4, 8)):
        p = f"{d}/{sub}"
        f.append(_f(p, is_dir=True, parent=d))
        f += [_f(f"{p}/e{i}", size=mb * MB, parent=p) for i in range(n)]
    sw = f"{d}/Service Worker"
    f.append(_f(sw, is_dir=True, parent=d))
    for sub, n, mb in (("CacheStorage", 8, 40), ("ScriptCache", 3, 5)):
        p = f"{sw}/{sub}"
        f.append(_f(p, is_dir=True, parent=sw))
        f += [_f(f"{p}/e{i}", size=mb * MB, parent=p) for i in range(n)]
    # registrations — small, and NOT a cache
    db = f"{sw}/Database"
    f.append(_f(db, is_dir=True, parent=sw))
    f.append(_f(f"{db}/reg.db", size=1 * MB, ext=".db", parent=db))
    return f


def _norm(p):
    return p.replace("\\", "/").lower().rstrip("/")


def _entities():
    return detect_entities(_tree(), "C:/", log_fn=lambda _m: None)


def test_caches_are_offered_as_safe():
    caches = [e for e in _entities() if e.entity_type == "cache_folder"]
    assert caches, "no browser cache separated from the profile"
    assert all(e.risk == "Safe" for e in caches)


def test_service_worker_cachestorage_is_separated():
    """The single biggest one — 613 MB on the real profile."""
    hits = [e for e in _entities() if _norm(e.path).endswith("service worker/cachestorage")]
    assert hits and hits[0].risk == "Safe"


def test_service_worker_database_is_not_treated_as_cache():
    """Registrations live beside the caches; only the caches may be taken."""
    for e in _entities():
        if _norm(e.path).endswith("service worker/database"):
            assert e.entity_type != "cache_folder"
        for p in (e.removable_file_paths or []):
            if "service worker/database" in _norm(p):
                assert e.entity_type != "cache_folder", (
                    "registration database offered as disposable cache")


def test_profile_data_is_never_marked_safe():
    """Passwords, cookies and history must not become one-click deletable."""
    for e in _entities():
        if e.entity_type == "browser_profile":
            assert e.risk != "Safe", "browser profile offered as Safe"


def test_cache_names_say_where_they_came_from():
    """Several folders are literally called "Cache"; identical names made the
    disambiguator append the same word again ("Cache (Cache)")."""
    names = [e.name for e in _entities() if e.entity_type == "cache_folder"]
    assert len(names) == len(set(names)), f"duplicate cache names: {names}"
    assert any("/" in n for n in names), "names should carry their location"


def test_a_cache_folder_outside_a_browser_is_untouched():
    """A folder called "Cache" in a source tree is not browser data."""
    src = "C:/dev/project"
    findings = [
        _f(src, is_dir=True, parent="C:/dev"),
        _f(f"{src}/Cache", is_dir=True, parent=src),
    ] + [_f(f"{src}/Cache/f{i}.tmp", size=2 * MB, parent=f"{src}/Cache")
         for i in range(5)]
    for e in detect_entities(findings, "C:/", log_fn=lambda _m: None):
        if _norm(e.path).endswith("/dev/project/cache"):
            assert "cache ·" not in e.name.lower(), "non-browser folder labelled browser cache"


def test_bytes_are_not_double_counted():
    findings = _tree()
    ents = detect_entities(findings, "C:/", log_fn=lambda _m: None)
    file_bytes = sum(f.size_bytes for f in findings if not f.is_dir)
    assert sum(e.size_bytes for e in ents) <= file_bytes
