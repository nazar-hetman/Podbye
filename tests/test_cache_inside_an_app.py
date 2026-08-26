"""An app's cache must be reachable without touching the app's own data.

Reported, about After Effects with the Mister Horse extensions installed:
Adobe keeps a great deal of cache, but the extension keeps its **login** in the
same app folder — "is it possible to clear only cache that really affects
nothing, so the login can be saved?"

Podbye's model already answers that when it *recognises* the cache: the cache
subfolder becomes its own Safe, recyclable entity and the folder holding the
sign-in stays Review with no whole-folder delete. It failed for Adobe on the
name alone. Pass 4's test was ``name == "cache"`` or ``name.endswith("cache")``,
so ``Media Cache Files`` matched nothing, no earlier pass claimed it, and
``_pass7_sweep`` then swallowed it into ``AppData/Local/Adobe`` as app data —
1.5 GB of rebuildable cache the user could not act on at all.

Deliberately NOT done here: detecting the credentials. A filename probe for
login/token/licence names over 280,000 files of real AppData returned 14,481
hits, nearly all of them DLLs and source files (``Microsoft.IdentityModel.
Tokens.dll``). Podbye offers what it positively recognises as regenerable and
leaves everything else alone; it never claims to have spotted your sign-in.
"""
from app.services.entity_detector import (
    detect_entities, _has_cache_word, _named_exactly_like_cache,
)
from app.models.smart_entity import actionability_for_type
from tests.treebuild import mkdir, mkfile

MB = 1024 * 1024
U = "C:/Users/n"


def _app_with_cache_and_login():
    """An extension that keeps a licence beside a large preview cache."""
    f = [mkdir(f"{U}/AppData"), mkdir(f"{U}/AppData/Roaming"),
         mkdir(f"{U}/AppData/Local")]
    app = f"{U}/AppData/Roaming/MisterHorse"
    f += [mkdir(app),
          mkfile(f"{app}/login.json", 2_000),
          mkfile(f"{app}/license.dat", 1_200)]
    f.append(mkdir(f"{app}/Cache"))
    f += [mkfile(f"{app}/Cache/preview{i}.mp4", 40 * MB) for i in range(40)]

    adobe = f"{U}/AppData/Local/Adobe/Common"
    f += [mkdir(f"{U}/AppData/Local/Adobe"), mkdir(adobe),
          mkdir(f"{adobe}/Media Cache Files")]
    f += [mkfile(f"{adobe}/Media Cache Files/x{i}.cfa", 25 * MB)
          for i in range(60)]
    return f


def _detect():
    return detect_entities(_app_with_cache_and_login(), "C:/",
                           log_fn=lambda _m: None)


def _by_leaf(entities, leaf):
    for e in entities:
        if e.path.replace("\\", "/").lower().endswith(leaf.lower()):
            return e
    return None


# ── the cache is offered ──────────────────────────────────────────

def test_a_cache_the_name_only_mentions_is_still_a_cache():
    """"Media Cache Files" \u2014 1.5 GB that used to be invisible."""
    media = _by_leaf(_detect(), "adobe/common/media cache files")
    assert media is not None, "the media cache was swallowed by its app folder"
    assert media.entity_type == "cache_folder"
    assert actionability_for_type(media.entity_type, media.risk) == "recycle"


def test_the_recognised_cache_is_the_target_not_the_app_folder():
    entities = _detect()
    cache = _by_leaf(entities, "misterhorse/cache")
    assert cache is not None and cache.entity_type == "cache_folder"
    assert actionability_for_type(cache.entity_type, cache.risk) == "recycle"


# ── the app's own data is not ─────────────────────────────────────

def test_the_folder_holding_the_login_is_never_offered_whole():
    """The parent may be listed; it must not be deletable in one click."""
    app = _by_leaf(_detect(), "appdata/roaming/misterhorse")
    if app is not None:
        assert actionability_for_type(app.entity_type, app.risk) != "recycle"


def test_the_login_is_not_inside_any_recyclable_target():
    login = f"{U}/AppData/Roaming/MisterHorse/login.json".lower()
    for e in _detect():
        if actionability_for_type(e.entity_type, e.risk) != "recycle":
            continue
        root = e.path.replace("\\", "/").lower().rstrip("/")
        assert not login.startswith(root + "/"), (
            f"{e.name!r} ({e.path}) would take the sign-in file with it")
        for path in (e.removable_file_paths or []):
            assert path.replace("\\", "/").lower() != login


# ── the widened name test, and what it must not take with it ──────

def test_names_that_mention_a_cache():
    for name in ("Media Cache Files", "Cache Storage", "Code Cache",
                 "cache2", "Caches", "GPUCache"):
        assert _has_cache_word(name) or _named_exactly_like_cache(name), name


def test_words_that_merely_contain_the_letters():
    for name in ("cachet", "apache", "Cachexia"):
        assert not _has_cache_word(name), name
        assert not _named_exactly_like_cache(name), name


def test_a_package_that_sounds_like_a_cache_is_not_one():
    """Six copies of the npm package "http-cache-semantics" on a real machine."""
    pkg = f"{U}/AppData/Roaming/npm/node_modules/http-cache-semantics"
    findings = [mkdir(f"{U}/AppData"), mkdir(f"{U}/AppData/Roaming"),
                mkdir(f"{U}/AppData/Roaming/npm"),
                mkdir(f"{U}/AppData/Roaming/npm/node_modules"), mkdir(pkg),
                mkfile(f"{pkg}/index.js", 30_000),
                mkfile(f"{pkg}/package.json", 1_000),
                mkfile(f"{pkg}/README.md", 4_000),
                mkfile(f"{pkg}/LICENSE", 1_000)]
    for e in detect_entities(findings, "C:/", log_fn=lambda _m: None):
        if e.path.replace("\\", "/").lower().endswith("http-cache-semantics"):
            assert e.entity_type != "cache_folder", (
                "an npm package was offered as a cache")


def test_only_the_widened_test_is_gated():
    """A folder that IS named "cache" is one wherever it sits.

    The package guard exists for names that merely *mention* a cache. It must
    not reach the plain name, which has recognised these folders all along.
    """
    from app.services.entity_detector import _inside_package_container
    inside = f"{U}/appdata/roaming/npm/node_modules/thing/cache".lower()
    assert _inside_package_container(inside) is True
    assert _named_exactly_like_cache("cache") is True


def test_an_app_cache_outside_a_package_is_recognised():
    app = f"{U}/AppData/Roaming/WidgetTool"
    findings = [mkdir(f"{U}/AppData"), mkdir(f"{U}/AppData/Roaming"),
                mkdir(app), mkdir(f"{app}/cache"),
                mkfile(f"{app}/settings.json", 2_000)]
    findings += [mkfile(f"{app}/cache/blob{i}.bin", 2 * MB) for i in range(8)]
    entities = detect_entities(findings, "C:/", log_fn=lambda _m: None)
    cache = _by_leaf(entities, "widgettool/cache")
    assert cache is not None and cache.entity_type == "cache_folder"
